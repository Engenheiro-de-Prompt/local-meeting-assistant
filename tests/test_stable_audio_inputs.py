import pathlib,tempfile,unittest
from unittest.mock import patch
import live_meeting_app as app

class StableAVFoundationInputsTests(unittest.TestCase):
    def test_avfoundation_outputs_mono_without_async_clock_rewriting(self):
        command=app.build_ffmpeg_command('/ffmpeg','pc_only','','0',1,1,'chunk_%06d.wav',45,backend='avfoundation')
        self.assertIn('-ac',command)
        self.assertEqual(command[command.index('-ac')+1],'1')
        self.assertNotIn('aresample=async=1',' '.join(command))

    def test_capture_uses_discovered_names_not_process_local_numeric_indices(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=app.MeetingManager(pathlib.Path(tmp),ffmpeg='/local/ffmpeg')
            devices={'capture_backend':'avfoundation','microphones':[{'id':'2','name':'MacBook Pro Microphone'}],'monitors':[{'id':'1','name':'BlackHole 2ch'}]}
            with patch.object(manager,'devices',return_value=devices),patch.object(app.subprocess,'Popen') as popen,patch.object(app.threading.Thread,'start'),patch.object(app.time,'sleep'),patch.object(manager,'_start_resource_monitor'):
                popen.return_value.poll.return_value=None
                manager.start({'capture_mode':'both','mic_device':'2','pc_device':'1','transcription_mode':'off'})
                command=popen.call_args.args[0]
                self.assertEqual([command[i+1] for i,value in enumerate(command) if value=='-i'],[':MacBook Pro Microphone',':BlackHole 2ch'])

if __name__=='__main__':unittest.main()
