"""Optional Chrome integration, always isolated from real recordings and models."""
import json
import os
import pathlib
import subprocess
import tempfile
import threading
import unittest
import wave
from unittest.mock import patch
import live_meeting_app as app

ROOT = pathlib.Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('MEETING_PLAYWRIGHT_MODULE'), 'Set MEETING_PLAYWRIGHT_MODULE to run Chrome integration')
class ManualNotesBrowserRoundtrip(unittest.TestCase):
    def test_actual_ui_http_disk_and_manager_restore(self):
        with tempfile.TemporaryDirectory(prefix='meeting-browser-roundtrip-') as tmp:
            root = pathlib.Path(tmp)
            folder = root / 'synthetic-persistence'; folder.mkdir()
            audio = root / 'synthetic-persistence.wav'
            with wave.open(str(audio), 'wb') as output:
                output.setparams((1, 2, 8000, 0, 'NONE', 'not compressed')); output.writeframes(b'\x00\x00' * 800)
            manager = app.MeetingManager(root, ffmpeg='/usr/bin/ffmpeg')
            manager.session = {
                'session_id':'synthetic-persistence', 'state':'completed', 'folder':str(folder),
                'audio_file':str(audio), 'whisper_model':'synthetic',
                'session_json':str(folder/'session.json'), 'transcript_json':str(folder/'transcript.json'),
                'transcript_file':str(root/'synthetic-persistence.txt'),
                'started_at':'synthetic', 'elapsed_s':0, 'segments':[], 'next_chunk':0,
                'cards':app.default_boxes(), 'analyses':{}, 'llm_model':'synthetic-model',
                'ollama_port':11435, 'analysis_interval_s':180,
                'manual_notes':{'text':'','items':[]}, 'manual_notes_revision':0,
                'final_analysis_state':'not_run',
            }
            manager._persist()
            original_transcript = (root/'synthetic-persistence.txt').read_bytes()
            server = app.LoopbackServer(('127.0.0.1',0), app.make_handler(manager), manager)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with (patch.object(manager,'models',return_value={'11435':['synthetic-model'],'11434':[]}),
                  patch.object(manager,'devices',return_value={'microphones':[],'monitors':[]}),
                  patch.object(app,'_ollama_call',side_effect=AssertionError('Manual content must never invoke LLM')),
                  patch.object(app,'_ollama_json_call',side_effect=AssertionError('Manual content must never invoke LLM'))):
                thread.start()
                try:
                    env = {**os.environ, 'MEETING_TEST_URL':f'http://127.0.0.1:{server.server_port}'}
                    result = subprocess.run(['node','tests/dashboard-notes-roundtrip.cjs'],cwd=ROOT,env=env,text=True,capture_output=True,timeout=60)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    saved = json.loads((folder/'session.json').read_text())
                    self.assertEqual(saved['manual_notes']['text'],'Versão A confirmada; B continua local.')
                    self.assertTrue(saved['manual_notes']['items'][0]['checked'])
                    self.assertGreaterEqual(saved['manual_notes_revision'],1)
                    self.assertEqual((root/'synthetic-persistence.txt').read_bytes(),original_transcript)
                    restored = app.MeetingManager(root,ffmpeg='/usr/bin/ffmpeg').status()
                    self.assertEqual(restored['manual_notes'],saved['manual_notes'])
                    print(result.stdout.strip())
                finally:
                    server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
