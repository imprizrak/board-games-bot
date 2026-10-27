"""Offline checks for the global Midnight Frost Mini App palette."""

from pathlib import Path
from unittest import TestCase, main


ROOT = Path(__file__).parent
HTML = (ROOT / "index.html").read_text(encoding="utf-8")


class MidnightFrostTests(TestCase):
    def test_night_palette_is_last_theme_override(self):
        self.assertIn('<meta name="theme-color" content="#020817">', HTML)
        self.assertIn('<style id="styloteka-midnight-frost">', HTML)
        theme = HTML.split('<style id="styloteka-midnight-frost">', 1)[1].split("</style>", 1)[0]
        self.assertIn('--bg: #020817;', theme)
        self.assertIn('--accent: #b7e6ff;', theme)
        self.assertIn('background: var(--bg-texture), var(--bg) !important;', theme)
        self.assertIn('background: #030d20 !important;', theme)
        self.assertIn('.bottom-nav button.active', theme)
        self.assertNotIn('rgba(212,175,55', theme)

    def test_futuristic_font_and_telegram_chrome(self):
        self.assertIn('family=Exo+2:', HTML)
        self.assertIn('family=Unbounded:', HTML)
        self.assertIn("font-family: 'Exo 2'", HTML)
        self.assertIn("font-family: 'Unbounded'", HTML)
        self.assertIn("tg[action]('#020817')", HTML)

    def test_no_legacy_yellow_text_accents_in_controls(self):
        self.assertIn('color: #bce8ff !important;', HTML)
        self.assertNotIn('color:#ffdf9b', HTML)
        self.assertNotIn('color:#ffe4aa', HTML)
        self.assertNotIn('color:#f0d57f', HTML)


if __name__ == "__main__":
    main()
