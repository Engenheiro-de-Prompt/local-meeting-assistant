import pathlib,tempfile,unittest
from unittest.mock import patch
import live_meeting_app as app
from test_processing import make_manager,write_chunk

class CadenceRegressionTests(unittest.TestCase):
    def test_reserve_scales_to_32_gib_host_and_critical_cpu_skips_stages(self):
        from resource_guard import ResourceGuard
        now=[0.0]
        guard=ResourceGuard(clock=lambda:now[0])
        healthy=dict(cpu_percent=20,ram_total_mib=32768,ram_available_mib=10000,memory_pressure='normal',disk_free_mib=10000)
        guard.update(healthy)
        guard.update({**healthy,'ram_available_mib':4000})
        now[0]=11
        guard.update({**healthy,'ram_available_mib':4000})
        self.assertGreaterEqual(guard.snapshot()['level'],1)
        guard.update({**healthy,'ram_available_mib':2000})
        self.assertEqual(guard.snapshot()['level'],4)
        other=ResourceGuard()
        other.update({**healthy,'cpu_percent':96})
        self.assertEqual(other.snapshot()['level'],4)

    def test_whisper_cadence_checkpoint_is_start_not_finish(self):
        with tempfile.TemporaryDirectory() as td:
            m,chunks=make_manager(pathlib.Path(td),'cadence')
            m.stop_requested=True
            write_chunk(chunks/'chunk_000000.wav')
            observed=[]
            def transcribe(*args):
                observed.append(m.session.get('last_transcription_monotonic'))
                m._shutdown_requested.set()
                return [],'pt'
            with patch.object(m,'_load_whisper',return_value={'snapshot':'fake'}),patch.object(m,'_transcribe_chunk',side_effect=transcribe):
                m._processing_loop(m.session)
            self.assertEqual(len(observed),1)
            self.assertIsNotNone(observed[0], 'Record inference start before work so its runtime is included in cadence')

if __name__=='__main__':unittest.main()
