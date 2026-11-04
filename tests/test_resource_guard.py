import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


class ResourceGuardTests(unittest.TestCase):
    def test_invalid_numeric_telemetry_is_unknown_not_healthy(self):
        from resource_guard import ResourceGuard
        for value in (float('nan'), float('inf'), -1, True):
            with self.subTest(value=value):
                guard = ResourceGuard()
                guard.update(dict(cpu_percent=value, ram_available_mib=9000,
                                  memory_pressure='normal', disk_free_mib=9000))
                self.assertFalse(guard.allowed('llm'))
                self.assertIsNone(guard.snapshot()['latest']['cpu_percent'])

    def test_sampler_tracks_current_cpu_swap_window_and_owned_children(self):
        import resource_guard as rg
        now = [0.0]
        sampler = rg.ResourceSampler(clock=lambda: now[0])
        def probe(command):
            if command[0].endswith('ps'):
                return f'123 0:0{int(now[0] / 2.5)}.00 1024\n'
            if command[-1] == 'hw.memsize':
                return str(8 * 1024 ** 3)
            if command[-1] == 'kern.memorystatus_vm_pressure_level':
                return '2'
            if command[-1] == 'vm.swapusage':
                return 'total = 1024.00M used = 20.00M free = 1004.00M'
            return f'Mach Virtual Memory Statistics: (page size of 4096 bytes)\nPages free: 1048576.\nSwapouts: {int(now[0])}.\n'
        with patch.object(rg.sys, 'platform', 'darwin'), patch.object(rg, '_probe', side_effect=probe), patch.object(sampler, '_cpu_ticks', side_effect=[(100, 80), (200, 150)]):
            first = sampler.collect('.', controlled_pids=[123])
            now[0] = 5
            second = sampler.collect('.', controlled_pids=[123])
        self.assertIsNone(first['cpu_percent'])
        self.assertEqual(second['cpu_percent'], 30)
        self.assertEqual(second['memory_pressure'], 'warning')
        self.assertEqual(second['swap_out_mib'], .02)
        self.assertEqual(second['controlled_cpu_percent'], 40)
        self.assertEqual(second['controlled_rss_mib'], 1)

    def test_pressure_is_sustained_emergency_immediate_and_recovery_slow(self):
        from resource_guard import ResourceGuard
        now = [0.0]
        guard = ResourceGuard(clock=lambda: now[0])
        healthy = dict(cpu_percent=20, ram_available_mib=4096, memory_pressure='normal',
                       swap_out_mib=0, disk_free_mib=4000)
        guard.update(healthy)
        guard.update(dict(healthy, cpu_percent=88))
        self.assertEqual(guard.snapshot()['level'], 0)
        now[0] = 11
        guard.update(dict(healthy, cpu_percent=88))
        self.assertEqual(guard.snapshot()['level'], 1)
        now[0] = 22
        guard.update(dict(healthy, cpu_percent=88))
        self.assertEqual(guard.snapshot()['level'], 2)
        guard.update(dict(healthy, memory_pressure='critical'))
        self.assertEqual(guard.snapshot()['level'], 4)
        now[0] = 23
        guard.update(healthy)
        now[0] = 82
        guard.update(healthy)
        self.assertEqual(guard.snapshot()['level'], 4)
        now[0] = 83
        guard.update(healthy)
        self.assertEqual(guard.snapshot()['level'], 3)
        now[0] = 143
        guard.update(healthy)
        self.assertEqual(guard.snapshot()['level'], 2)

    def test_unknown_stale_and_manual_pause_fail_closed(self):
        from resource_guard import ResourceGuard
        now = [0.0]
        guard = ResourceGuard(clock=lambda: now[0])
        guard.update({})
        self.assertFalse(guard.allowed('whisper'))
        self.assertFalse(guard.allowed('llm'))
        guard.set_recording_only(True)
        self.assertTrue(guard.snapshot()['recording_only'])
        guard.set_recording_only(False)
        self.assertFalse(guard.allowed('whisper'))
        guard.update(dict(cpu_percent=1, ram_available_mib=9000, memory_pressure='normal', disk_free_mib=9000))
        now[0] = 16
        self.assertFalse(guard.allowed('llm'))
        self.assertIn('stale', guard.snapshot()['reason'])

    def test_effective_intervals_do_not_mutate_requested_values(self):
        from resource_guard import ResourceGuard
        guard = ResourceGuard()
        guard.set_recording_only(True)
        state = guard.snapshot(analysis_interval_s=120, transcription_interval_s=33)
        self.assertEqual(state['requested_analysis_interval_s'], 120)
        self.assertEqual(state['requested_transcription_interval_s'], 33)
        self.assertIsNone(state['effective_transcription_interval_s'])
        self.assertFalse(state['llm_allowed'])


if __name__ == '__main__':
    unittest.main()
