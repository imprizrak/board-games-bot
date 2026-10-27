"""Offline tests for Telegram event-announcement polls (no live API writes)."""

import ast
from collections import Counter
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from unittest import TestCase, main


APP_PATH = Path(__file__).with_name("app.py")
FUNC_NAMES = {
    "_event_game_poll_options",
    "_event_game_poll_rows",
    "telegram_keyboard_payload",
    "_toggle_group_event_game_vote",
}


class FakeResponse:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self.rows


class FakeRequests:
    RequestException = OSError

    def __init__(self, catalog):
        self.catalog = catalog
        self.picks = []
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        if url == "games":
            return FakeResponse(self.catalog)
        if url == "picks":
            params = kwargs["params"]
            matches = self.picks
            for column in ("event_id", "game_id", "telegram_user_id"):
                if column in params:
                    value = int(params[column].split(".")[1])
                    matches = [p for p in matches if int(p[column]) == value]
            return FakeResponse(matches)
        raise AssertionError(f"Unexpected URL: {url}")

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        self.picks.append(kwargs["json"])
        return FakeResponse([])

    def delete(self, url, **kwargs):
        self.calls.append(("delete", url, kwargs))
        params = kwargs["params"]
        self.picks = [p for p in self.picks if any(
            str(p[column]) != params[column].split(".")[1]
            for column in ("event_id", "game_id", "telegram_user_id")
        )]
        return FakeResponse([])


def load_poll_functions(requests):
    """Extract functions so importing app.py cannot start a real Telegram bot."""
    import logging
    import time

    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    methods = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNC_NAMES]
    code = compile(ast.Module(body=methods, type_ignores=[]), str(APP_PATH), "exec")
    namespace = {
        "time": time, "logging": logging, "Counter": Counter, "Lock": Lock,
        "requests": requests, "REST": "games", "EVENT_GAME_PICKS_REST": "picks",
        "HEADERS": {}, "_EVENT_GAME_OPTIONS_CACHE": {}, "_EVENT_GAME_OPTIONS_LOCK": Lock(),
        "get_event_snapshot": lambda _id: {"id": 42, "status": "scheduled", "rsvps": []},
        "_event_games_membership": lambda _id, _user: True,
        "_event_mini_app_link": lambda _id, payment=False: "https://t.me/test_bot",
    }
    exec(code, namespace)
    return namespace


class EventGamePollTests(TestCase):
    def setUp(self):
        self.requests = FakeRequests([
            {"id": i, "name": f"Гра {i:02d}"} for i in range(22, 0, -1)
        ])
        self.app = load_poll_functions(self.requests)
        self.event = {"id": 42, "status": "scheduled", "rsvps": [], "game_names": []}

    def test_all_library_games_even_when_event_has_no_selected_games(self):
        options = self.app["_event_game_poll_options"](self.event)
        self.assertEqual(len(options), 22)
        self.assertEqual([name for _id, name in options], sorted(
            [f"Гра {i:02d}" for i in range(1, 23)]))
        # The catalog should be reused across different events for five minutes.
        self.app["_event_game_poll_options"]({"id": 43})
        self.assertEqual(len([c for c in self.requests.calls if c[1] == "games"]), 1)

    def test_one_full_name_button_per_row_and_live_counts(self):
        self.requests.picks = [
            {"event_id": 42, "game_id": 1, "telegram_user_id": 1},
            {"event_id": 42, "game_id": 1, "telegram_user_id": 2},
        ]
        rows = self.app["_event_game_poll_rows"](self.event)
        self.assertEqual(len(rows), 22)
        self.assertTrue(all(len(row) == 1 for row in rows))
        callbacks = [button["callback_data"] for row in rows for button in row]
        self.assertEqual(len(set(callbacks)), 22)
        self.assertIn("· 2", rows[0][0]["text"])

    def test_long_game_name_is_not_shortened(self):
        long_name = "Вижити: втеча з таємничого острова"
        self.requests.catalog.append({"id": 23, "name": long_name})
        rows = self.app["_event_game_poll_rows"](self.event)
        self.assertIn(f"🎲 {long_name} · 0", [row[0]["text"] for row in rows])

    def test_telegram_announcement_includes_catalog_poll(self):
        keyboard = self.app["telegram_keyboard_payload"](42)["inline_keyboard"]
        self.assertEqual(keyboard[0][0]["callback_data"], "ev_go:42")
        game_buttons = [b for row in keyboard for b in row if b.get("callback_data", "").startswith("ev_game:")]
        self.assertEqual(len(game_buttons), 22)

    def test_toggle_votes_and_rsvp_gate(self):
        user = SimpleNamespace(id=501, username="player", first_name="Оля", last_name="")
        toggle = self.app["_toggle_group_event_game_vote"]
        selected, name = toggle(42, 1, user)
        self.assertEqual((selected, name), (True, "Гра 01"))
        self.assertEqual(len(self.requests.picks), 1)
        selected, _name = toggle(42, 1, user)
        self.assertFalse(selected)
        self.assertEqual(self.requests.picks, [])
        self.app["_event_games_membership"] = lambda _id, _user: False
        with self.assertRaisesRegex(ValueError, "rsvp_required"):
            toggle(42, 2, user)


if __name__ == "__main__":
    main()
