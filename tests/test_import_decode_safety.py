import pathlib,sys,tempfile,unittest,time
from unittest.mock import patch
from import_decode import decode_supervised

HEALTHY={'disk_free_mib':9000,'ram_available_mib':9000,'memory_pressure':'normal','cpu_percent':10}
class ImportSafetyTests(unittest.TestCase):
    def test_preflight_low_disk_does_not_launch(self):
        with tempfile.TemporaryDirectory() as d, patch('import_decode.subprocess.Popen') as launch:
            with self.assertRaisesRegex(RuntimeError,'disco'):
                decode_supervised(['fake'],pathlib.Path(d),sample=lambda:{**HEALTHY,'disk_free_mib':100})
            launch.assert_not_called()
    def test_running_decode_canceled_on_low_disk(self):
        with tempfile.TemporaryDirectory() as d:
            count=[0];children=[]
            def sample():
                count[0]+=1
                return {**HEALTHY,'disk_free_mib':100 if count[0]>1 else 9000}
            start=time.monotonic()
            with self.assertRaisesRegex(RuntimeError,'disco'):
                decode_supervised([sys.executable,'-c','import time;time.sleep(30)'],pathlib.Path(d),sample=sample,process_changed=children.append,poll_seconds=.02)
            self.assertLess(time.monotonic()-start,3)
            self.assertIsNotNone(children[0].poll())
            self.assertIsNone(children[-1])
    def test_output_budget_stops_child_not_silent_truncation(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d)
            (root/'chunk_000000.wav').write_bytes(b'x'*1024)
            with self.assertRaisesRegex(RuntimeError,'limite'):
                decode_supervised([sys.executable,'-c','import time;time.sleep(30)'],root,sample=lambda:HEALTHY,max_output_bytes=100,poll_seconds=.02)
    def test_success_actual_process_exit_and_shutdown(self):
        with tempfile.TemporaryDirectory() as d:
            result=decode_supervised([sys.executable,'-c','pass'],pathlib.Path(d),sample=lambda:HEALTHY,poll_seconds=.02)
            self.assertEqual(result.returncode,0)
            with self.assertRaisesRegex(RuntimeError,'cancelada'):
                decode_supervised(['never-run'],pathlib.Path(d),sample=lambda:HEALTHY,cancelled=lambda:True)
if __name__=='__main__': unittest.main()
