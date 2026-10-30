"""Removing the mini-game section must preserve the board-game library."""

from pathlib import Path
import re
import unittest


HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
NAV = HTML.split('<div class="bottom-nav">', 1)[1].split('</div>', 1)[0]


class GamesTabRemovalTests(unittest.TestCase):
    def test_five_main_tabs_remain_in_the_expected_order(self):
        self.assertEqual(
            re.findall(r'<button class="nav-btn(?: active)?" data-view="([^"]+)"', NAV),
            ["view-home", "view-library", "view-events", "view-community", "view-profile"],
        )
        self.assertIn("grid-template-columns:repeat(5,minmax(0,1fr))", HTML)
        self.assertIn("grid-template-columns: repeat(5, minmax(0, 1fr))", HTML)

    def test_removed_mini_games_have_no_views_or_launch_paths(self):
        for view in ("view-games", "view-dungeon", "view-coins", "view-treasure"):
            with self.subTest(view=view):
                self.assertNotIn(f'id="{view}"', HTML)
                self.assertNotIn(f"openMiniGame('{view}')", HTML)
        self.assertNotIn("function openMiniGame(", HTML)
        self.assertNotIn("function startCoinCatcherGame(", HTML)
        self.assertNotIn("function startTreasureGame(", HTML)
        self.assertNotIn("function startDungeonRun(", HTML)

    def test_board_game_library_and_saved_profile_features_remain(self):
        self.assertIn('id="view-library"', HTML)
        self.assertIn('id="gamesGrid"', HTML)
        self.assertIn('id="submitGameBtn"', HTML)
        self.assertIn("function loadGames(", HTML)
        self.assertIn("function resetProfileAppearance(", HTML)
        self.assertIn("let dungeonProfileCache = null;", HTML)


if __name__ == "__main__":
    unittest.main()
