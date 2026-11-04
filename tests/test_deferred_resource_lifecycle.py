"""Stopped recovery keeps real guard telemetry, without a microphone or models."""
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import live_meeting_app as app
from resource_guard import ResourceGuard
from test_processing import make_manager, write_chunk


class DeferredResourceLifecycleTests(unittest.TestCase):
    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail('Timed out waiting for resource monitor')
            time.sleep(.005)

    def fixture(self, root, session_id='deferred'):
        folder = root / session_id
        folder.mkdir()
        manager, chunks = make_manager(folder, folder.name)
        assert manager.session is not None
        manager.recordings_dir = root
        manager.stop_requested = True
        manager.session.update(transcription_mode='live', live_analysis_enabled=False,
                               final_analysis_enabled=False)
        write_chunk(chunks / 'chunk_000000.wav')
        manager._persist()
        return manager

    def test_pressure_recovers_after_worker_exit_and_resume_reuses_one_monitor(self):
        with tempfile.TemporaryDirectory() as td:
            manager = self.fixture(pathlib.Path(td))
            now = [0.0]
            sample = dict(cpu_percent=99, ram_available_mib=9000,
                          memory_pressure='normal', disk_free_mib=9000, swap_out_mib=0)
            guard_factory = lambda: ResourceGuard(clock=lambda: now[0])
            with (patch.object(app, 'ResourceGuard', side_effect=guard_factory),
                  patch.object(app.ResourceSampler, 'collect', side_effect=lambda *_a, **_kw: dict(sample)),
                  patch.object(app, 'RESOURCE_SAMPLE_INTERVAL_SECONDS', .005),
                  patch.object(manager, '_finalize_audio'),
                  patch.object(manager, '_load_whisper', return_value=object()) as load,
                  patch.object(manager, '_transcribe_chunk', return_value=(
                      [{'start': 0, 'end': 1, 'text': 'Recovered'}], 'pt')),
                  patch.object(app, '_ollama_call') as llm):
                try:
                    assert manager.session is not None
                    manager._start_resource_monitor(manager.session)
                    monitor, stop, guard = manager._resource_thread, manager._resource_stop, manager.resource_guard
                    assert manager.session is not None and monitor is not None and stop is not None
                    manager._processing_loop(manager.session)
                    self.assertTrue(manager.session['resource_deferred'])
                    load.assert_not_called()
                    self.assertTrue(monitor.is_alive(), 'Deferred processing must retain telemetry')
                    self.assertFalse(stop.is_set())
                    # Exceed the 15-second stale threshold, then exercise real cooldowns.
                    now[0] = 20
                    self.wait_for(lambda: guard.sampled_at == 20)
                    self.assertFalse(manager.status()['resource_guard']['telemetry_stale'])
                    sample['cpu_percent'] = 1
                    for instant in (21, 82, 143):
                        now[0] = instant
                        self.wait_for(lambda: guard.sampled_at == instant)
                    self.assertEqual(guard.level, 2)
                    self.assertTrue(manager.status()['resource_guard']['transcription_allowed'])
                    # Starting twice and resuming a deserialized checkpoint must not reset
                    # pressure/cooldown policy or orphan the existing sampler.
                    manager._start_resource_monitor(manager.session)
                    self.assertIs(manager._resource_thread, monitor)
                    manager.resume_pending('deferred')
                    assert manager.worker_thread is not None and manager.session is not None
                    manager.worker_thread.join(3)
                    self.assertFalse(manager.worker_thread.is_alive())
                    self.assertEqual(manager.session['next_chunk'], 1)
                    self.assertEqual(manager.session['state'], 'completed')
                    self.assertIs(manager.resource_guard, guard)
                    self.assertFalse(monitor.is_alive(), 'Completion must stop telemetry')
                    llm.assert_not_called()
                finally:
                    manager.shutdown()
                self.assertFalse(monitor.is_alive())

    def test_manual_resume_and_shutdown_preserve_latched_llm_safety(self):
        with tempfile.TemporaryDirectory() as td:
            manager = self.fixture(pathlib.Path(td))
            now = [0.0]
            healthy = dict(cpu_percent=1, ram_available_mib=9000,
                           memory_pressure='normal', disk_free_mib=9000, swap_out_mib=0)
            with (patch.object(app, 'ResourceGuard', side_effect=lambda: ResourceGuard(clock=lambda: now[0])),
                  patch.object(app.ResourceSampler, 'collect', return_value=healthy),
                  patch.object(app, 'RESOURCE_SAMPLE_INTERVAL_SECONDS', .005),
                  patch.object(manager, '_finalize_audio'),
                  patch.object(manager, '_load_whisper') as load):
                try:
                    assert manager.session is not None
                    manager._start_resource_monitor(manager.session)
                    monitor, guard = manager._resource_thread, manager.resource_guard
                    assert manager.session is not None and monitor is not None
                    manager.set_resource_guard({'recording_only': True})
                    manager._processing_loop(manager.session)
                    now[0] = 20
                    self.wait_for(lambda: guard.sampled_at == 20)
                    manager.set_resource_guard({'recording_only': False})
                    self.assertTrue(manager.status()['resource_guard']['transcription_allowed'])
                    manager._llm_release_state = 'server_release_unverified'
                    guard.fail('llm_server_release_unverified')
                    manager.resume_pending('deferred')
                    assert manager.worker_thread is not None and manager.session is not None
                    manager.worker_thread.join(3)
                    self.assertFalse(manager.worker_thread.is_alive())
                    self.assertIs(manager.resource_guard, guard)
                    self.assertIs(manager._resource_thread, monitor)
                    self.assertEqual(manager.status()['resource_guard']['reason'], 'llm_server_release_unverified')
                    self.assertFalse(manager.status()['resource_guard']['transcription_allowed'])
                    load.assert_not_called()
                finally:
                    manager.shutdown()
                self.assertFalse(monitor.is_alive(), 'Shutdown must stop a deferred session monitor')

    def test_different_checkpoint_stops_old_monitor_without_clearing_llm_latch(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            manager = self.fixture(root)
            self.fixture(root, 'other')
            healthy = dict(cpu_percent=1, ram_available_mib=9000,
                           memory_pressure='normal', disk_free_mib=9000, swap_out_mib=0)
            with (patch.object(app.ResourceSampler, 'collect', return_value=healthy),
                  patch.object(app, 'RESOURCE_SAMPLE_INTERVAL_SECONDS', .005),
                  patch.object(manager, '_finalize_audio'),
                  patch.object(manager, '_load_whisper') as load):
                try:
                    assert manager.session is not None
                    manager._start_resource_monitor(manager.session)
                    monitor, guard = manager._resource_thread, manager.resource_guard
                    assert monitor is not None
                    manager._llm_release_state = 'server_release_unverified'
                    guard.fail('llm_server_release_unverified')
                    manager._processing_loop(manager.session)
                    manager.resume_pending('other')
                    assert manager.worker_thread is not None and manager.session is not None
                    manager.worker_thread.join(3)
                    self.assertFalse(manager.worker_thread.is_alive())
                    self.assertFalse(monitor.is_alive())
                    replacement = manager._resource_thread
                    assert replacement is not None
                    self.assertIsNot(replacement, monitor)
                    self.assertIsNot(manager.resource_guard, guard)
                    self.assertEqual(manager.session['session_id'], 'other')
                    self.assertEqual(manager.status()['resource_guard']['reason'], 'llm_server_release_unverified')
                    load.assert_not_called()
                finally:
                    manager.shutdown()
                self.assertFalse(replacement.is_alive())

    def test_stopping_sampler_retains_ownership_until_it_exits(self):
        with tempfile.TemporaryDirectory() as td:
            manager = self.fixture(pathlib.Path(td))
            entered, release = threading.Event(), threading.Event()
            healthy = dict(cpu_percent=1, ram_available_mib=9000,
                           memory_pressure='normal', disk_free_mib=9000, swap_out_mib=0)

            def collect(*_args, **_kwargs):
                if threading.current_thread() is manager._resource_thread:
                    entered.set()
                    release.wait(3)
                return dict(healthy)

            with (patch.object(app.ResourceSampler, 'collect', side_effect=collect),
                  patch.object(app, 'RESOURCE_SAMPLE_INTERVAL_SECONDS', .005)):
                try:
                    assert manager.session is not None
                    manager._start_resource_monitor(manager.session)
                    monitor, stop, guard = manager._resource_thread, manager._resource_stop, manager.resource_guard
                    assert monitor is not None and stop is not None
                    self.assertTrue(entered.wait(3))
                    guard.fail('llm_server_release_unverified')
                    # Simulate the bounded join expiring while a probe is in flight.
                    with patch.object(monitor, 'join'):
                        manager._stop_resource_monitor(manager.session)
                    self.assertIs(manager._resource_thread, monitor)
                    self.assertIs(manager._resource_stop, stop)
                    self.assertTrue(stop.is_set())
                    with self.assertRaisesRegex(RuntimeError, 'still stopping'):
                        manager._start_resource_monitor(manager.session)
                    self.assertIs(manager._resource_thread, monitor)
                    self.assertIs(manager.resource_guard, guard)
                    release.set()
                    monitor.join(3)
                    self.assertFalse(monitor.is_alive())
                    manager._start_resource_monitor(manager.session)
                    replacement = manager._resource_thread
                    assert replacement is not None
                    self.assertIsNot(replacement, monitor)
                    self.assertIs(manager.resource_guard, guard)
                    self.assertFalse(guard.allowed('llm'))
                finally:
                    release.set()
                    manager.shutdown()
                self.assertFalse(replacement.is_alive())


if __name__ == '__main__':
    unittest.main()
