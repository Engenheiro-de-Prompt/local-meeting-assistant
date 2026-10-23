import http.client
import pathlib
import tempfile
import threading
import unittest
import live_meeting_app as app


class DashboardAssetsTests(unittest.TestCase):
    def test_exact_local_assets_are_served_without_arbitrary_file_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = app.MeetingManager(pathlib.Path(tmp))
            server = app.LoopbackServer(('127.0.0.1', 0), app.make_handler(manager), manager)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for path, mime in [('/manual-notes.js', 'javascript'), ('/dashboard-layout.js', 'javascript'), ('/dashboard-layout.css', 'text/css')]:
                    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                    conn.request('GET', path)
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200, path)
                    self.assertIn(mime, response.getheader('Content-Type') or '')
                    self.assertIn("connect-src 'self'", response.getheader('Content-Security-Policy') or '')
                    self.assertTrue(response.read())
                    conn.close()
                for path in ['/../live_meeting_app.py', '/web/../live_meeting_app.py', '/live_meeting_app.py']:
                    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                    conn.request('GET', path)
                    response = conn.getresponse()
                    self.assertEqual(response.status, 404)
                    response.read(); conn.close()
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
