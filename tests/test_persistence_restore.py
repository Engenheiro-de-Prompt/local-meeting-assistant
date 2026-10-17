import json
import pathlib
import tempfile
import unittest

import sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import live_meeting_app as app


class RestoreCompletedSessionTests(unittest.TestCase):
    def test_completed_session_is_restored_after_server_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp); folder=root/'Reuniao_2026-01-01_10-00-00'; folder.mkdir()
            audio=root/'Reuniao_2026-01-01_10-00-00.wav'; audio.write_bytes(b'RIFF-test')
            transcript=root/'Reuniao_2026-01-01_10-00-00.txt'; transcript.write_text('test transcript',encoding='utf-8')
            metadata={
                'session_id':folder.name,'state':'completed','started_at':'2026-01-01T10:00:00-03:00','elapsed_s':60,
                'audio_file':str(audio),'transcript_file':str(transcript),'capture_mode':'both',
                'whisper_model':'small','whisper_device':'cuda','llm_model':'gemma3:latest','ollama_port':11434,
                'analysis_interval_s':180,'segments':[{'start':0.0,'end':1.0,'text':'test transcript'}],
                'analysis_summary':'local test summary','analyses':{'questions':{'text':'One question'}},
                'context_compaction':{'summary':'Cumulative meeting ledger','covered_chars':48000},
            }
            (folder/'session.json').write_text(json.dumps(metadata),encoding='utf-8')
            manager=app.MeetingManager(root)
            status=manager.status()
            self.assertEqual(status['state'],'completed')
            self.assertEqual(status['session_id'],folder.name)
            self.assertEqual(status['transcript'],'[00:00:00 --> 00:00:01] test transcript')
            self.assertEqual(status['analyses']['questions']['text'],'One question')
            self.assertEqual(manager.session['context_compaction']['covered_chars'],48000)
            self.assertTrue(pathlib.Path(status['audio_file']).is_file())

    def test_restore_rejects_paths_outside_recordings_directory(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root=pathlib.Path(tmp); folder=root/'Reuniao_bad'; folder.mkdir()
            audio=pathlib.Path(outside)/'audio.wav'; audio.write_bytes(b'RIFF')
            transcript=pathlib.Path(outside)/'transcript.txt'; transcript.write_text('secret',encoding='utf-8')
            metadata={'session_id':folder.name,'state':'completed','audio_file':str(audio),'transcript_file':str(transcript),'segments':[],'analyses':{}}
            (folder/'session.json').write_text(json.dumps(metadata),encoding='utf-8')
            manager=app.MeetingManager(root)
            self.assertEqual(manager.status()['state'],'idle')


if __name__=='__main__':
    unittest.main()
