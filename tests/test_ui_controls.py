import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class UiControlTests(unittest.TestCase):
    def test_start_is_not_gated_by_a_consent_confirmation_checkbox(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('id="consent"', html)
        self.assertNotIn("$('consent').checked", html)
        self.assertIn("$('start').disabled=busy", html)


if __name__ == "__main__":
    unittest.main()
