"""Regression checks for compact, translucent six-tab navigation."""

from pathlib import Path
import re
import unittest


HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
GLASS = re.search(
    r'<style id="styloteka-liquid-glass">(.*?)</style>', HTML, re.S
).group(1)
POLISH = re.search(
    r'<style id="styloteka-ui-polish">(.*?)</style>', HTML, re.S
).group(1)


class NavDropGlassTests(unittest.TestCase):
    def test_compact_navigation_keeps_touch_targets(self):
        self.assertIn("--glass-nav-height: 64px", GLASS)
        self.assertIn("height: 54px !important", GLASS)
        self.assertIn("min-height: 54px !important", GLASS)
        self.assertIn("grid-template-columns: repeat(6, minmax(0, 1fr))", POLISH)
        self.assertEqual(len(re.findall(r'class="nav-btn(?: active)?" data-view=', HTML)), 6)

    def test_clear_layer_uses_tints_not_opaque_background(self):
        self.assertIn("--glass-nav-clear:", GLASS)
        self.assertIn("rgba(219, 242, 255, .17)", GLASS)
        self.assertIn("rgba(5, 25, 51, .29)", GLASS)
        self.assertIn("background: var(--glass-nav-clear) !important", GLASS)
        self.assertIn("background: var(--glass-nav-selected) !important", GLASS)
        self.assertIn(".bottom-nav::after", GLASS)

    def test_blur_and_accessibility_fallbacks(self):
        self.assertIn("backdrop-filter: blur(16px) saturate(140%) !important", POLISH)
        self.assertIn("@media (prefers-reduced-transparency: reduce)", GLASS)
        self.assertIn("font-size: clamp(9px, 2.42vw, 10px) !important", GLASS)
        self.assertNotRegex(HTML, r'aria-label="Закрити"[^>]+aria-label="Закрити"')


if __name__ == "__main__":
    unittest.main()
