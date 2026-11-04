import os,pathlib,subprocess,sys,tempfile,time,unittest
from unittest.mock import Mock,patch
import live_meeting_app as app
from test_processing import make_manager

class CaptureLifecycleTests(unittest.TestCase):
    def test_new_capture_rejected_before_devices_if_prior_audio_worker_hung(self):
        with tempfile.TemporaryDirectory() as td:
            m,_=make_manager(pathlib.Path(td),'prior-capture')
            assert m.session is not None
            m.session['state']='completed'
            m._audio_safety_thread=Mock(is_alive=Mock(return_value=True))
            with patch.object(m,'devices',side_effect=RuntimeError('device discovery unexpectedly reached')) as devices:
                with self.assertRaisesRegex(RuntimeError,'supervisor de áudio'):
                    m.start({})
            devices.assert_not_called()

    def test_stale_session_teardown_cannot_stop_current_audio_monitor(self):
        with tempfile.TemporaryDirectory() as td:
            m,_=make_manager(pathlib.Path(td),'new-capture')
            stop=__import__('threading').Event()
            m._audio_safety_stop=stop
            m._audio_safety_thread=Mock(is_alive=Mock(return_value=False))
            m._stop_resource_monitor({'session_id':'old-capture'})
            self.assertFalse(stop.is_set(),'Old worker must not tear down a new capture safety monitor')

    def _start_fixture(self, root):
        manager = app.MeetingManager(root, ffmpeg='mock-ffmpeg')
        manager.devices = Mock(return_value={'microphones': [{'id': 'default'}],
                                             'monitors': [{'id': '@DEFAULT_MONITOR@'}]})
        request = {'transcription_mode': 'off', 'live_analysis_enabled': False,
                   'final_analysis_enabled': False}
        return manager, request

    def test_invalid_audio_safety_prerequisite_never_launches_capture(self):
        with tempfile.TemporaryDirectory() as td:
            manager, request = self._start_fixture(pathlib.Path(td))
            with patch.object(app, 'AudioSafety', side_effect=ValueError('unsafe audio path')), \
                 patch.object(app.subprocess, 'Popen') as popen:
                with self.assertRaisesRegex(ValueError, 'unsafe audio path'):
                    manager.start(request)
            popen.assert_not_called()
            self.assertIsNone(manager.recorder)

    def test_symlink_recordings_path_rejected_before_capture(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            target = root / 'target'
            target.mkdir()
            linked = root / 'linked'
            linked.symlink_to(target, target_is_directory=True)
            manager, request = self._start_fixture(linked)
            with patch.object(app.subprocess, 'Popen') as popen:
                with self.assertRaisesRegex(ValueError, 'Symlinks'):
                    manager.start(request)
            popen.assert_not_called()

    def test_partial_supervisor_thread_start_failure_stops_all_started_threads(self):
        for name in ('MeetingResourceSampler', 'MeetingAudioSafety', 'LiveMeetingTranscriber'):
            with self.subTest(thread=name), tempfile.TemporaryDirectory() as td:
                manager, request = self._start_fixture(pathlib.Path(td))
                proc = Mock(pid=None)
                proc.poll.side_effect = lambda: 0 if proc.terminate.called else None
                original_start = app.threading.Thread.start
                def start(thread):
                    if thread.name == name:
                        raise RuntimeError('thread startup failed')
                    return original_start(thread)
                with patch.object(app.subprocess, 'Popen', return_value=proc), \
                     patch.object(app.time, 'sleep'), \
                     patch.object(manager, '_drain_stderr'), \
                     patch.object(manager, '_record_resource_sample'), \
                     patch.object(app.threading.Thread, 'start', start):
                    with self.assertRaisesRegex(RuntimeError, 'thread startup failed'):
                        manager.start(request)
                proc.terminate.assert_called_once()
                proc.wait.assert_called()
                self.assertIsNone(manager._audio_safety_thread)
                self.assertIsNone(manager._resource_thread)
                self.assertFalse(manager.worker_thread and manager.worker_thread.is_alive())

    def test_supervisor_initialization_failure_unwinds_capture_and_monitors(self):
        for failing_stage in ('_start_resource_monitor', '_start_audio_safety'):
            with self.subTest(stage=failing_stage), tempfile.TemporaryDirectory() as td:
                manager, request = self._start_fixture(pathlib.Path(td))
                proc = Mock(pid=None)
                proc.poll.side_effect = lambda: 0 if proc.terminate.called else None
                with patch.object(app.subprocess, 'Popen', return_value=proc), \
                     patch.object(app.time, 'sleep'), \
                     patch.object(manager, '_drain_stderr'), \
                     patch.object(manager, '_start_resource_monitor'), \
                     patch.object(manager, '_start_audio_safety'), \
                     patch.object(manager, '_stop_resource_monitor') as stop_monitor, \
                     patch.object(manager, '_stop_audio_safety') as stop_audio:
                    getattr(manager, failing_stage).side_effect = RuntimeError('supervisor failed')
                    with self.assertRaisesRegex(RuntimeError, 'supervisor failed'):
                        manager.start(request)
                proc.terminate.assert_called_once()
                proc.wait.assert_called()
                stop_monitor.assert_called_once_with(manager.session)
                stop_audio.assert_called_once()
                self.assertTrue(manager.stop_requested)
                self.assertEqual(manager.session['state'], 'error')
                self.assertFalse(manager._recorder_alive())
                self.assertIsNone(manager.worker_thread)

    @unittest.skipUnless(sys.platform=='darwin','macOS idle-sleep assertion')
    def test_idle_sleep_helper_scoped_to_capture_and_released_after_stop(self):
        with tempfile.TemporaryDirectory() as td:
            m,_=make_manager(pathlib.Path(td),'sleep-guard')
            proc=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'])
            m.recorder=proc
            try:
                m._start_audio_safety(m.session)
                helper=getattr(m,'_capture_wake',None)
                self.assertIsNotNone(helper,'Capture must own an idle-sleep assertion, not rely on unrelated caffeinate')
                assert helper is not None
                self.assertIsNone(helper.poll())
                self.assertEqual(helper.args,['/usr/bin/caffeinate','-i','-w',str(proc.pid)])
                deadline=time.monotonic()+2
                asserted=False
                while time.monotonic()<deadline:
                    text=subprocess.check_output(['pmset','-g','assertions'],text=True)
                    if f'pid {helper.pid}(caffeinate)' in text:
                        asserted=True;break
                    time.sleep(.05)
                self.assertTrue(asserted,'Actual macOS assertion must be registered')
                proc.terminate();proc.wait(timeout=2)
                m._stop_audio_safety()
                self.assertIsNotNone(helper.poll())
            finally:
                if proc.poll() is None:proc.kill();proc.wait(timeout=2)
                m._stop_audio_safety()

if __name__=='__main__':unittest.main()
