"""Offline checks for the new home-page wordmark (no web or bot calls)."""

from pathlib import Path
from unittest import TestCase, main


ROOT = Path(__file__).parent


class HomeBrandingTests(TestCase):
    def test_home_header_uses_image_and_keeps_name(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('<header class="home-brand-title" aria-label="Про100 Настолки">', html)
        self.assertIn('class="home-brand-logo" src="/pro100-logo.png"', html)
        self.assertIn('<span class="home-brand-word">Настолки</span>', html)
        self.assertIn('.home-brand-content{', html)

    def test_transparent_logo_is_bundled_and_served(self):
        logo = (ROOT / "pro100-logo.png").read_bytes()
        self.assertEqual(logo[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(logo[25], 6, "Logo PNG should have RGBA channels")
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('@app.route("/pro100-logo.png")', app)
        self.assertIn('send_from_directory(".", "pro100-logo.png"', app)


if __name__ == "__main__":
    main()
