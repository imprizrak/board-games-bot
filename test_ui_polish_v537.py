"""Static regression checks for the v5.3.7 accessibility/performance pass."""

import re
from pathlib import Path
from unittest import TestCase, main


ROOT = Path(__file__).parent
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")
POLISH = HTML.split('<style id="styloteka-ui-polish">', 1)[1].split("</style>", 1)[0]


def luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))


def contrast(foreground, background):
    a, b = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (a + .05) / (b + .05)


class UIPolishTests(TestCase):
    def test_key_theme_colors_meet_wcag_aa_for_normal_text(self):
        palette = (
            ('#e7f5ff', '#020817'),  # Main text / background
            ('#a7bdd6', '#0b192d'),  # Muted text / card
            ('#a9c4dd', '#17314d'),  # Tab caption / glass fallback
            ('#f2fbff', '#315c82'),  # Active tab / frosted selected state
            ('#e7f7ff', '#1b3d5f'),  # Glass button caption / fallback
        )
        for foreground, background in palette:
            with self.subTest(foreground=foreground, background=background):
                self.assertGreaterEqual(contrast(foreground, background), 4.5)

    def test_all_five_nav_tabs_point_to_views(self):
        nav = HTML.split('<div class="bottom-nav">', 1)[1].split('</div>', 1)[0]
        targets = re.findall(r'<button class="nav-btn[^"]*" data-view="([^"]+)"', nav)
        self.assertEqual(len(targets), 5)
        self.assertEqual(len(set(targets)), 5)
        for target in targets:
            self.assertIn(f'id="{target}"', HTML)

    def test_uniform_controls_and_performance_guards(self):
        self.assertIn('--ui-control-h: 46px;', POLISH)
        self.assertIn('min-height: var(--ui-control-h) !important;', POLISH)
        self.assertIn('grid-template-columns: repeat(5, minmax(0, 1fr));', POLISH)
        self.assertIn('input:not([type="range"]), select, textarea { font-size: 16px !important; }', POLISH)
        self.assertIn('backdrop-filter: none !important;', POLISH)
        self.assertIn('background-attachment: scroll !important;', POLISH)
        self.assertIn('@media (max-width: 359px)', POLISH)

    def test_startup_network_dedup_and_fast_cache_first_paint(self):
        self.assertIn('function showCachedGamesWhileRefreshing()', HTML)
        self.assertIn('if (!force) showCachedGamesWhileRefreshing();', HTML)
        self.assertIn("if (document.getElementById('view-profile')?.classList.contains('active')) loadMyProfile();", HTML)
        self.assertIn('window.requestIdleCallback(() => checkMyAchievements()', HTML)
        self.assertIn('private, max-age=0, must-revalidate', APP)
        self.assertIn('conditional=True', APP)

    def test_modal_close_buttons_have_accessible_names(self):
        for tag in re.findall(r'<button[^>]*class="close-btn"[^>]*>', HTML):
            self.assertIn('aria-label=', tag)


if __name__ == "__main__":
    main()
