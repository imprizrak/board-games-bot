"""Offline checks for the new home-page wordmark (no web or bot calls)."""

from pathlib import Path
from unittest import TestCase, main


ROOT = Path(__file__).parent


class HomeBrandingTests(TestCase):
    def test_home_header_has_only_centered_smaller_logo(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('<header class="home-brand-title" aria-label="STYLOTEKA">', html)
        self.assertIn('class="home-brand-logo" src="/styloteka-logo.png"', html)
        self.assertNotIn('class="home-brand-word"', html)
        self.assertIn('.home-brand-content{', html)
        self.assertIn('justify-content:center;', html)
        self.assertIn('height:clamp(46px,15vw,66px)', html)

    def test_transparent_logo_is_bundled_and_served(self):
        logo = (ROOT / "styloteka-logo.png").read_bytes()
        self.assertEqual(logo[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(logo[25], 6, "Logo PNG should have RGBA channels")
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('@app.route("/styloteka-logo.png")', app)
        self.assertIn('send_from_directory(".", "styloteka-logo.png"', app)

    def test_small_webp_logo_has_png_fallback(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        webp = (ROOT / "styloteka-logo.webp").read_bytes()
        png = (ROOT / "styloteka-logo.png").read_bytes()
        self.assertTrue(webp.startswith(b"RIFF") and webp[8:12] == b"WEBP")
        self.assertLess(len(webp), len(png) // 4)
        self.assertIn('<source srcset="/styloteka-logo.webp" type="image/webp">', html)
        self.assertIn('src="/styloteka-logo.png"', html)
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('@app.route("/styloteka-logo.webp")', app)


if __name__ == "__main__":
    main()
