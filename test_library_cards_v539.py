"""Static regression checks for image-first library cards."""

from pathlib import Path
import re
import unittest


HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
CARDS = re.search(
    r'<style id="styloteka-library-game-cards">(.*?)</style>', HTML, re.S
).group(1)
RENDER = re.search(
    r'function renderGamesGrid\(\) \{(.*?)\n\}\n\ndocument\.getElementById\("librarySearch"\)',
    HTML,
    re.S,
).group(1)


class LibraryCardTests(unittest.TestCase):
    def test_reference_has_two_distinct_card_surfaces(self):
        self.assertIn("#gamesGrid .library-card-cover", CARDS)
        self.assertIn("#gamesGrid .library-card-info", CARDS)
        self.assertIn("border: 2px solid #355c82 !important", CARDS)
        self.assertIn("background: #0a1b30", CARDS)
        self.assertIn("background: #10263f", CARDS)
        self.assertIn("border-radius: 22px !important", CARDS)

    def test_boxes_are_not_cropped_and_titles_wrap(self):
        self.assertIn("object-fit: contain", CARDS)
        self.assertIn("overflow-wrap: anywhere", CARDS)
        self.assertIn("-webkit-line-clamp: unset", CARDS)
        self.assertIn('class="library-card-cover"', RENDER)
        self.assertIn('class="library-card-info"', RENDER)
        self.assertIn('class="card-title"', RENDER)
        self.assertIn('class="card-tags"', RENDER)

    def test_existing_library_actions_remain(self):
        self.assertIn("libraryVisibleGames()", RENDER)
        self.assertIn("openViewModal(game)", RENDER)
        self.assertIn("/favorite", RENDER)
        self.assertIn('loading="lazy"', RENDER)
        self.assertIn("this.nextElementSibling.hidden=false", RENDER)
        self.assertIn("card.tabIndex = 0", RENDER)
        self.assertIn("e.key === 'Enter'", RENDER)
        self.assertNotIn(".home-game-card", CARDS)


if __name__ == "__main__":
    unittest.main()
