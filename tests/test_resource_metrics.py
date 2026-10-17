import pathlib
import sys
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import live_meeting_app as app


class ResourceSampleTests(unittest.TestCase):
    def test_resource_summary_tracks_worst_ram_gpu_and_load_samples(self):
        first=app.merge_resource_sample({}, {
            'ram_total_mib':32768,'ram_available_mib':24000,'gpu_memory_total_mib':12288,
            'gpu_memory_used_mib':3000,'gpu_utilization_percent':30,'system_load_per_cpu':0.2,
        })
        second=app.merge_resource_sample(first, {
            'ram_total_mib':32768,'ram_available_mib':19000,'gpu_memory_total_mib':12288,
            'gpu_memory_used_mib':5200,'gpu_utilization_percent':72,'system_load_per_cpu':0.6,
        })
        self.assertEqual(second['sample_count'],2)
        self.assertEqual(second['ram_available_min_mib'],19000)
        self.assertEqual(second['gpu_memory_used_peak_mib'],5200)
        self.assertEqual(second['gpu_memory_peak_percent'],42.3)
        self.assertEqual(second['gpu_utilization_peak_percent'],72)
        self.assertEqual(second['system_load_per_cpu_peak'],0.6)

    def test_live_snapshot_contains_only_resource_metrics(self):
        snapshot=app.collect_resource_sample()
        self.assertGreater(snapshot.get('ram_total_mib',0),0)
        self.assertGreater(snapshot.get('ram_available_mib',0),0)
        self.assertNotIn('transcript',snapshot)
        self.assertNotIn('model_output',snapshot)


if __name__=='__main__':
    unittest.main()
