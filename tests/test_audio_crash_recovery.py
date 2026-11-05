"""Real process-crash tests; synthetic audio files only, no devices or playback."""
import hashlib
import pathlib
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
import wave
from audio_safety import salvage_pcm_wav


def actual_pcm(path):
    raw=path.read_bytes()
    assert raw[:4]==b'RIFF' and raw[8:12]==b'WAVE'
    offset=12
    while offset+8<=len(raw):
        kind,length=struct.unpack_from('<4sI',raw,offset)
        offset+=8
        if kind==b'data':
            data=raw[offset:offset+min(length,len(raw)-offset)]
            return data[:len(data)//2*2]
        offset+=length+(length%2)
    raise AssertionError('Source contains no data chunk')


@unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required for actual crash test')
class AbruptAudioRecoveryTests(unittest.TestCase):
    def test_recording_only_keeps_real_pcm_advancing_with_independent_journal(self):
        from test_processing import make_manager
        import signal
        with tempfile.TemporaryDirectory(prefix='audio-fallback-') as td:
            manager,chunks=make_manager(pathlib.Path(td),'synthetic-audio-fallback')
            session=manager.session
            assert session is not None
            session['state']='recording'
            cmd=[shutil.which('ffmpeg'),'-nostdin','-hide_banner','-loglevel','error',
                 '-re','-f','lavfi','-i','anullsrc=channel_layout=mono:sample_rate=16000',
                 '-t','10','-ac','1','-ar','16000','-c:a','pcm_s16le','-f','segment',
                 '-segment_time','1','-reset_timestamps','1','-segment_format','wav',
                 '-segment_format_options','flush_packets=1',str(chunks/'chunk_%06d.wav')]
            with tempfile.TemporaryFile() as errors:
                proc=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=errors)
                manager.recorder=proc
                try:
                    manager._start_audio_safety(manager.session)
                    manager.set_resource_guard({'recording_only':True})
                    deadline=time.monotonic()+6
                    snapshot={}
                    while time.monotonic()<deadline:
                        snapshot=manager.status()['resource_guard']['audio_safety']
                        if snapshot.get('sealed_chunks',0)>0 and snapshot.get('duration_s',0)>1:break
                        time.sleep(.05)
                    self.assertIsNone(proc.poll(),'Fallback must not terminate recording')
                    self.assertEqual(snapshot.get('state'),'healthy','Silence with real frames is valid audio')
                    self.assertGreater(snapshot['duration_s'],1)
                    self.assertGreater(snapshot['sealed_chunks'],0)
                    self.assertTrue((chunks/'audio-journal.jsonl').is_file())
                    self.assertEqual(session['next_chunk'],0)
                    self.assertFalse(manager.status()['resource_guard']['transcription_allowed'])
                finally:
                    manager.stop_requested=True
                    if proc.poll() is None:
                        proc.send_signal(signal.SIGINT)
                        try:proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=2)
                    manager._stop_audio_safety()

    def test_sigkill_preserves_closed_segment_and_recovers_actual_open_pcm(self):
        with tempfile.TemporaryDirectory(prefix='audio-crash-') as td:
            root=pathlib.Path(td)
            cmd=[shutil.which('ffmpeg'),'-nostdin','-hide_banner','-loglevel','error',
                 '-re','-f','lavfi','-i','sine=frequency=440:sample_rate=16000',
                 '-t','10','-ac','1','-ar','16000','-c:a','pcm_s16le',
                 '-f','segment','-segment_time','1','-reset_timestamps','1',
                 '-segment_format','wav','-segment_format_options','flush_packets=1',
                 str(root/'chunk_%06d.wav')]
            with tempfile.TemporaryFile() as errors:
                proc=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=errors)
                closed=root/'chunk_000000.wav'
                active=root/'chunk_000001.wav'
                try:
                    deadline=time.monotonic()+8
                    while time.monotonic()<deadline:
                        if proc.poll() is not None:break
                        if active.exists() and active.stat().st_size>4000:break
                        time.sleep(.03)
                    self.assertIsNone(proc.poll(),'FFmpeg exited before injected crash')
                    self.assertTrue(active.exists(),'Second chunk should be open before kill')
                    self.assertGreater(active.stat().st_size,4000)
                    closed_hash=hashlib.sha256(closed.read_bytes()).hexdigest()
                    proc.kill()
                    self.assertNotEqual(proc.wait(timeout=2),0)
                    original=active.read_bytes()
                    pcm=actual_pcm(active)
                    self.assertTrue(pcm)
                    target=root/'recovered.wav'
                    report=salvage_pcm_wav(active,target)
                    with wave.open(str(target),'rb') as recovered:
                        self.assertEqual((recovered.getnchannels(),recovered.getframerate(),recovered.getsampwidth()),(1,16000,2))
                        self.assertEqual(recovered.getnframes(),len(pcm)//2)
                        self.assertEqual(recovered.readframes(recovered.getnframes()),pcm)
                    self.assertEqual(active.read_bytes(),original,'Never repair original in place')
                    self.assertEqual(hashlib.sha256(closed.read_bytes()).hexdigest(),closed_hash)
                    with wave.open(str(closed),'rb') as wav:
                        self.assertGreater(wav.getnframes(),0)
                    self.assertIsInstance(report,dict)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=2)

if __name__=='__main__':unittest.main()
