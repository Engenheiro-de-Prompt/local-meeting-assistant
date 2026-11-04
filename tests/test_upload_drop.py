"""Headless Chrome against static assets and intercepted synthetic uploads only."""
import os
import pathlib
import subprocess
import unittest


@unittest.skipUnless(os.environ.get('MEETING_PLAYWRIGHT_MODULE'), 'Set MEETING_PLAYWRIGHT_MODULE to run Chrome integration')
class UploadDropBrowserTest(unittest.TestCase):
    def test_drop_and_picker_use_same_guarded_upload_flow(self):
        result = subprocess.run(
            ['node', 'tests/dashboard-upload-drop.cjs'],
            cwd=pathlib.Path(__file__).resolve().parents[1],
            env=os.environ, text=True, capture_output=True, timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())
