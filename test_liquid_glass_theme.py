"""Offline checks for the iOS 27-inspired Liquid Glass control layer."""

from pathlib import Path
from unittest import TestCase, main


HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
GLASS = HTML.split('<style id="styloteka-liquid-glass">', 1)[1].split("</style>", 1)[0]


class LiquidGlassThemeTests(TestCase):
    def test_floating_navigation_and_touch_states(self):
        self.assertIn('bottom: var(--glass-bottom-gap) !important;', GLASS)
        self.assertIn('border-radius: 26px !important;', GLASS)
        self.assertIn('.bottom-nav button.active', GLASS)
        self.assertIn('backdrop-filter: blur(28px)', GLASS)
        self.assertIn('.bottom-nav button:active', GLASS)
        self.assertIn('grid-template-columns:repeat(6,minmax(0,1fr))', HTML)

    def test_controls_are_glass_but_game_cards_stay_clear(self):
        self.assertIn('.btn:not([style*="background"])', GLASS)
        self.assertIn('.home-tool-btn', GLASS)
        self.assertIn('.shop-chip', GLASS)
        self.assertNotIn('.home-game-card', GLASS)
        self.assertNotIn('.card-title', GLASS)

    def test_accessibility_and_fallback(self):
        self.assertIn('@supports not', GLASS)
        self.assertIn('@media (prefers-reduced-transparency: reduce)', GLASS)
        self.assertIn('@media (prefers-reduced-motion: reduce)', GLASS)


if __name__ == "__main__":
    main()
