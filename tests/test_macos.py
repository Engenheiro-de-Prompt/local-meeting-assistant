"""macOS capture tests: enumerate only; never open a real audio input."""
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import live_meeting_app as app

LISTING = """[AVFoundation indev @ 0x123] AVFoundation video devices:
[AVFoundation indev @ 0x123] [0] FaceTime HD Camera
[AVFoundation indev @ 0x123] [1] Capture screen 0
[AVFoundation indev @ 0x123] AVFoundation audio devices:
[AVFoundation indev @ 0x123] [0] MacBook Pro Microphone
[AVFoundation indev @ 0x123] [1] Microsoft Teams Audio
[AVFoundation indev @ 0x123] [2] BlackHole 2ch
[AVFoundation indev @ 0x123] [12] Loopback Audio
[in#0 @ 0x123] Error opening input: Input/output error
"""


class MacOSCaptureTests(unittest.TestCase):
    def test_device_discovery_lists_audio_only_and_classifies_loopback_without_recording(self):
        with tempfile.TemporaryDirectory() as td:
            manager = app.MeetingManager(pathlib.Path(td), ffmpeg='/local/ffmpeg')
            result = subprocess.CompletedProcess([], 1, '', LISTING)
            with patch.object(app.sys, 'platform', 'darwin'), patch.object(app.subprocess, 'run', return_value=result) as run:
                devices = manager.devices()
            self.assertEqual([d['id'] for d in devices['microphones']], ['0', '1'])
            self.assertEqual([d['id'] for d in devices['monitors']], ['2', '12'])
            self.assertEqual(devices['capture_backend'], 'avfoundation')
            self.assertIn('BlackHole', devices['monitors'][0]['desc'])
            args, kwargs = run.call_args
            self.assertEqual(args[0], ['/local/ffmpeg', '-hide_banner', '-f', 'avfoundation', '-list_devices', 'true', '-i', ''])
            self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
            self.assertLessEqual(kwargs['timeout'], 10)

    def test_avfoundation_commands_capture_each_mode_as_audio_only_with_segmented_wav(self):
        for mode, expected in [('mic_only', [':0']), ('pc_only', [':12']), ('both', [':0', ':12'])]:
            with self.subTest(mode=mode):
                cmd = app.build_ffmpeg_command('/local/ffmpeg', mode, '0', '12', 1.2, 0.8,
                                               '/out/chunk_%06d.wav', 45, backend='avfoundation')
                self.assertEqual([cmd[i + 1] for i, token in enumerate(cmd) if token == '-i'], expected)
                self.assertEqual(cmd.count('avfoundation'), len(expected))
                self.assertNotIn('pulse', cmd)
                self.assertIn('pcm_s16le', cmd)
                self.assertIn('16000', cmd)
                self.assertIn('segment', cmd)
                self.assertEqual('amix=inputs=2' in ' '.join(cmd), mode == 'both')
                self.assertIn('volume=1.20' if mode == 'mic_only' else 'volume=0.80', ' '.join(cmd))

    def test_avfoundation_rejects_non_numeric_or_noncanonical_selected_ids(self):
        for invalid in ['default', '@DEFAULT_MONITOR@', '-1', ':0', '0:1', '01', '1.0', ' 1', '１', '', None, 1]:
            with self.subTest(device=invalid), self.assertRaises(ValueError):
                app.build_ffmpeg_command('ffmpeg', 'mic_only', invalid, '12', 1, 1, 'chunk_%06d.wav', 45,
                                         backend='avfoundation')
        # An unused device must not be required for single-input capture.
        cmd = app.build_ffmpeg_command('ffmpeg', 'mic_only', '0', '', 1, 1, 'chunk_%06d.wav', 45,
                                      backend='avfoundation')
        self.assertIn(':0', cmd)
        cmd = app.build_ffmpeg_command('ffmpeg', 'pc_only', '', '12', 1, 1, 'chunk_%06d.wav', 45,
                                      backend='avfoundation')
        self.assertIn(':12', cmd)
        with self.assertRaises(ValueError):
            app.build_ffmpeg_command('ffmpeg', 'both', '0', '12', 1, 1, 'chunk_%06d.wav', 45,
                                     backend='arbitrary')

    def test_session_uses_discovered_backend_and_allows_only_selected_input(self):
        # Popen is intercepted at the boundary: no real audio is ever captured.
        for mode, mics, monitors, expected in [
            ('mic_only', [{'id': '0'}], [], [':0']),
            ('pc_only', [], [{'id': '12'}], [':12']),
            ('both', [{'id': '0'}], [{'id': '12'}], [':0', ':12']),
        ]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                manager = app.MeetingManager(pathlib.Path(td), ffmpeg='/local/ffmpeg')
                devices = {'microphones': mics, 'monitors': monitors, 'capture_backend': 'avfoundation'}
                with patch.object(manager, 'devices', return_value=devices), \
                     patch.object(app.subprocess, 'Popen') as popen, \
                     patch.object(app.threading.Thread, 'start'), patch.object(app.time, 'sleep'), \
                     patch.object(manager, '_start_resource_monitor'):
                    popen.return_value.poll.return_value = None
                    manager.start({'capture_mode': mode, 'mic_device': '0' if mics else '',
                                   'pc_device': '12' if monitors else '', 'transcription_mode': 'off'})
                    cmd = popen.call_args.args[0]
                    self.assertEqual([cmd[i + 1] for i, token in enumerate(cmd) if token == '-i'], expected)
                    self.assertIn('avfoundation', cmd)

    def test_system_capture_without_loopback_fails_before_any_process_or_files(self):
        for mode in ['both', 'pc_only']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                root = pathlib.Path(td) / 'recordings'
                manager = app.MeetingManager(root, ffmpeg='/local/ffmpeg')
                devices = {'microphones': [{'id': '0'}], 'monitors': [], 'capture_backend': 'avfoundation'}
                with patch.object(manager, 'devices', return_value=devices), patch.object(app.subprocess, 'Popen') as popen:
                    with self.assertRaisesRegex(RuntimeError, 'BlackHole.*Loopback'):
                        manager.start({'capture_mode': mode, 'mic_device': '0', 'pc_device': '',
                                       'transcription_mode': 'off'})
                    popen.assert_not_called()
                    self.assertFalse(root.exists())

    def test_macos_memory_metrics_use_reported_page_size_without_nvidia_probe(self):
        vm = ('Mach Virtual Memory Statistics: (page size of 16384 bytes)\n'
              'Pages free: 100.\nPages inactive: 200.\nPages speculative: 20.\n'
              'Pages active: 500.\nPages occupied by compressor: 30.\n')
        def run(command, **_kwargs):
            if command == ['/usr/sbin/sysctl', '-n', 'hw.memsize']:
                return subprocess.CompletedProcess(command, 0, '33554432\n', '')
            if command == ['/usr/bin/vm_stat']:
                return subprocess.CompletedProcess(command, 0, vm, '')
            self.fail(f'Unexpected macOS metric command: {command}')
        with patch.object(app.sys, 'platform', 'darwin'), patch.object(app.subprocess, 'run', side_effect=run):
            sample = app.collect_resource_sample()
        self.assertEqual(sample['ram_total_mib'], 32)
        self.assertEqual(sample['ram_available_mib'], 5)
        self.assertNotIn('gpu_memory_total_mib', sample)

    def test_open_local_uses_platform_opener_on_only_resolved_safe_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            transcript = root / 'transcript.txt'
            transcript.write_text('fixture')
            manager = app.MeetingManager(root)
            manager.session = {'transcript_file': str(transcript)}
            for platform, command in [('darwin', 'open'), ('linux', 'xdg-open')]:
                with self.subTest(platform=platform), patch.object(app.sys, 'platform', platform), \
                     patch.object(app.shutil, 'which', return_value='/usr/bin/' + command) as which, \
                     patch.object(app.subprocess, 'Popen') as popen:
                    manager.open_local('transcript')
                    which.assert_called_once_with(command)
                    self.assertEqual(popen.call_args.args[0], ['/usr/bin/' + command, str(transcript.resolve())])
                    manager.session['transcript_file'] = '/etc/passwd'
                    with self.assertRaises(RuntimeError):
                        manager.open_local('transcript')
                    self.assertEqual(popen.call_count, 1)
                    manager.session['transcript_file'] = str(transcript)

    def test_whisper_cpu_is_macos_default_for_live_and_import_but_linux_keeps_cuda(self):
        with tempfile.TemporaryDirectory() as td:
            manager = app.MeetingManager(pathlib.Path(td), ffmpeg='/local/ffmpeg')
            for platform, expected in [('darwin', 'cpu'), ('linux', 'cuda')]:
                with self.subTest(platform=platform), patch.object(app.sys, 'platform', platform):
                    self.assertEqual(manager._file_settings({'transcription_mode': 'off'})['whisper_device'], expected)
                    devices = {'microphones': [{'id': '0'}], 'monitors': [], 'capture_backend': 'avfoundation'}
                    manager.session = None
                    # Each platform case is a separate fake capture, not a live orphan.
                    manager.recorder = None
                    with patch.object(manager, 'devices', return_value=devices), \
                         patch.object(app.subprocess, 'Popen') as popen, patch.object(app.threading.Thread, 'start'), \
                         patch.object(app.time, 'sleep'), patch.object(manager, '_start_resource_monitor'):
                        popen.return_value.poll.return_value = None
                        manager.start({'capture_mode': 'mic_only', 'mic_device': '0', 'pc_device': '',
                                       'transcription_mode': 'off'})
                        assert manager.session is not None
                        self.assertEqual(manager.session['whisper_device'], expected)

    def test_dashboard_uses_macos_cpu_mic_defaults_and_explains_absent_loopback(self):
        html = (pathlib.Path(app.APP_ROOT) / 'web/index.html').read_text()
        load_devices = next(line for line in html.splitlines() if line.startswith('async function loadDevices()'))
        script = r"""
const assert = require('assert');
const controls = {mic: {}, pc: {}, device: {value: 'cuda', options: [{value:'cuda'}, {value:'cpu'}]}, mode: {value:'both'}};
const $ = id => controls[id];
let savedPrefs = {}, writes = 0;
function persistPrefs(){ writes++; }
function optionSelect(control, items){ control.items = items; }
let devices = {microphones:[{id:'0',desc:'Mic'}],monitors:[],capture_backend:'avfoundation'};
async function api(){ return devices; }
""" + load_devices + r"""
(async()=>{
  await loadDevices();
  assert.equal(controls.device.value, 'cpu');
  assert.equal(controls.mode.value, 'mic_only');
  assert(controls.pc.items[0].desc.includes('BlackHole'));
  assert.equal(controls.pc.items[0].id, '');
  assert.equal(savedPrefs.captureBackend, 'avfoundation');
  controls.mode.value = 'both'; await loadDevices(); assert.equal(controls.mode.value, 'both');
  devices = {microphones:[{id:'default'}],monitors:[{id:'monitor'}]};
  controls.device.value = 'cuda'; await loadDevices(); assert.equal(controls.device.value, 'cuda');
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_device_placeholder_keeps_empty_id_in_real_option_renderer(self):
        html = (pathlib.Path(app.APP_ROOT) / 'web/index.html').read_text()
        renderer = next(line for line in html.splitlines() if line.startswith('function optionSelect('))
        script = "const document={createElement:()=>({})};const options=[];" + renderer + ";optionSelect({append:o=>options.push(o)},[{id:'',desc:'BlackHole ausente'}],'');require('assert').strictEqual(options[0].value,'');"
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_macos_startup_failure_mentions_microphone_permission_and_not_pulse(self):
        with tempfile.TemporaryDirectory() as td:
            manager = app.MeetingManager(pathlib.Path(td), ffmpeg='/local/ffmpeg')
            devices = {'microphones': [{'id': '0'}], 'monitors': [], 'capture_backend': 'avfoundation'}
            with patch.object(manager, 'devices', return_value=devices), \
                 patch.object(app.subprocess, 'Popen') as popen, patch.object(app.threading.Thread, 'start'), \
                 patch.object(app.time, 'sleep'):
                popen.return_value.poll.return_value = 1
                popen.return_value.stderr.read.return_value = b'Permission denied'
                with self.assertRaisesRegex(RuntimeError, 'AVFoundation.*Microfone') as caught:
                    manager.start({'capture_mode': 'mic_only', 'mic_device': '0', 'pc_device': '',
                                   'transcription_mode': 'off'})
                self.assertNotIn('PulseAudio', str(caught.exception))
                assert manager.session is not None
                self.assertEqual(manager.session['state'], 'error')

    def test_no_frames_reports_actionable_macos_error_through_processing_loop(self):
        for empty_wav in [False, True]:
            with self.subTest(empty_wav=empty_wav), tempfile.TemporaryDirectory() as td:
                manager = app.MeetingManager(pathlib.Path(td), ffmpeg='/local/ffmpeg')
                chunks = pathlib.Path(td) / '.chunks'
                chunks.mkdir()
                if empty_wav:
                    with app.wave.open(str(chunks / 'chunk_000000.wav'), 'wb') as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(16000)
                session = {'state': 'processing', 'chunks_dir': str(chunks), 'next_chunk': 0,
                           'capture_backend': 'avfoundation', 'transcription_mode': 'off',
                           'audio_file': str(pathlib.Path(td) / 'output.wav')}
                manager.session = session
                manager.stop_requested = True
                with patch.object(manager, '_persist'), patch.object(manager, '_stop_resource_monitor'), \
                     patch.object(app.subprocess, 'run') as run:
                    manager._processing_loop(session)
                self.assertEqual(session['state'], 'error')
                self.assertIn('Nenhum frame de áudio', session['error'])
                self.assertIn('AVFoundation', session['error'])
                self.assertIn('Microfone', session['error'])
                self.assertIn('Privacidade e Segurança', session['error'])
                self.assertIn('Terminal', session['error'])
                self.assertNotIn('RuntimeError', session['error'])
                run.assert_not_called()
                self.assertFalse(pathlib.Path(session['audio_file']).exists())

    def test_stopped_empty_wav_does_not_hang_or_load_whisper_in_transcription_modes(self):
        from unittest.mock import Mock
        for mode in ['live', 'after_meeting']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                manager = app.MeetingManager(pathlib.Path(td), ffmpeg='/local/ffmpeg')
                with app.wave.open(str(pathlib.Path(td) / 'chunk_000000.wav'), 'wb') as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(16000)
                session = {'state': 'processing', 'chunks_dir': td, 'next_chunk': 0,
                           'capture_backend': 'avfoundation', 'transcription_mode': mode,
                           'live_analysis_enabled': False, 'audio_file': str(pathlib.Path(td) / 'out.wav')}
                manager.session = session
                manager.stop_requested = True
                manager.recorder = Mock()
                manager.recorder.poll.return_value = 0
                with patch.object(manager, '_persist'), patch.object(manager, '_stop_resource_monitor'), \
                     patch.object(manager, '_load_whisper') as load, \
                     patch.object(app.time, 'sleep', side_effect=AssertionError('stopped empty WAV must not wait')):
                    manager._processing_loop(session)
                self.assertIn('Nenhum frame de áudio', session['error'])
                self.assertEqual(session['state'], 'error')
                load.assert_not_called()

    def test_delayed_permission_denial_survives_trailing_stderr_and_is_actionable(self):
        import io
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as td:
            manager = app.MeetingManager(pathlib.Path(td), ffmpeg='/local/ffmpeg')
            proc = Mock()
            proc.poll.return_value = 1
            proc.stderr = io.BytesIO(b'AVFoundation: Permission denied SECRET_RAW_PATH\nExiting normally\n')
            manager._drain_stderr(proc)
            manager._drain_stop_error(proc)
            self.assertIn('Permission denied', manager.last_ffmpeg_error)
            session = {'state': 'recording', 'chunks_dir': td, 'next_chunk': 0,
                       'capture_backend': 'avfoundation', 'transcription_mode': 'off'}
            manager.session = session
            manager.recorder = proc
            with patch.object(manager, '_persist'), patch.object(manager, '_stop_resource_monitor'):
                manager._processing_loop(session)
            self.assertEqual(session['state'], 'error')
            self.assertIn('Microfone negado', session['error'])
            self.assertIn('Privacidade e Segurança', session['error'])
            self.assertNotIn('SECRET_RAW_PATH', session['error'])
            self.assertNotIn('PulseAudio', session['error'])

    def test_macos_session_rejects_unavailable_and_malformed_ids_before_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td) / 'recordings'
            manager = app.MeetingManager(root, ffmpeg='/local/ffmpeg')
            devices = {'microphones': [{'id': '0'}], 'monitors': [{'id': '12'}], 'capture_backend': 'avfoundation'}
            with patch.object(manager, 'devices', return_value=devices), patch.object(app.subprocess, 'Popen') as popen:
                for invalid in ['99', '01', '-1', ':0', '0;touch', 0, None, [], {}]:
                    with self.subTest(device=invalid), self.assertRaises(ValueError):
                        manager.start({'capture_mode': 'both', 'mic_device': invalid, 'pc_device': '12',
                                       'transcription_mode': 'off'})
                popen.assert_not_called()
                self.assertFalse(root.exists())


if __name__ == '__main__':
    unittest.main()
