"""The add-game submit button must never sit behind the floating tab bar."""

from pathlib import Path
import re
import unittest


HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
GLASS = HTML.split('<style id="styloteka-liquid-glass">', 1)[1].split("</style>", 1)[0]
POLISH = HTML.split('<style id="ui-geometry-polish">', 1)[1].split("</style>", 1)[0]


class ModalNavLayeringTests(unittest.TestCase):
    def test_modal_layer_is_above_floating_navigation(self):
        modal_rules = re.search(r"\.modal\s*\{([^{}]*)\}", HTML, re.S).group(1)
        modal_rules = re.sub(r"/\*.*?\*/", "", modal_rules, flags=re.S)
        modal_layer = int(re.search(r"z-index:\s*(\d+)", modal_rules).group(1))
        nav_rules = re.search(r"\.bottom-nav\s*\{([^{}]*)\}", GLASS, re.S).group(1)
        nav_layer = int(re.search(r"z-index:\s*(\d+)", nav_rules).group(1))
        self.assertGreater(modal_layer, nav_layer)

    def test_add_game_submit_remains_in_scrollable_sheet(self):
        add_game = HTML.split('<div class="modal" id="addModal">', 1)[1].split(
            '<!-- Модерація запропонованих ігор -->', 1
        )[0]
        self.assertIn('id="submitGameBtn"', add_game)
        self.assertIn('onclick="submitGame()"', add_game)
        self.assertIn("overscroll-behavior:contain", POLISH)
        self.assertIn("scroll-padding-bottom:", POLISH)
        self.assertIn("var(--tg-safe-bottom,0px)", POLISH)


if __name__ == "__main__":
    unittest.main()
