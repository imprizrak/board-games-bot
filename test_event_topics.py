"""Offline checks for routing event announcements into a Telegram forum topic."""

import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main

APP_PATH = Path(__file__).with_name("app.py")
FUNCTIONS = {
    "get_active_group_targets_sync",
    "get_active_group_ids_sync",
    "_send_group_event_text_sync",
    "notify_groups_sync",
    "notify_event_reminder_groups_sync",
    "cmd_seteventtopic",
}


class FakeResponse:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self.rows


class FakeRequests:
    def __init__(self):
        self.rows = [{"chat_id": -1004328173945, "event_thread_id": 123}]
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", kwargs))
        return FakeResponse(self.rows)

    def patch(self, url, **kwargs):
        self.calls.append(("patch", kwargs))
        for row in self.rows:
            if kwargs["params"]["chat_id"] == f"eq.{row['chat_id']}":
                row.update(kwargs["json"])
                return FakeResponse([row])
        return FakeResponse([])


def isolated_app(fake_requests, telegram_api_post):
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in FUNCTIONS:
            node.decorator_list = []
            selected.append(node)
    mod = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    namespace = {
        "requests": fake_requests,
        "logging": logging,
        "TELEGRAM_GROUPS_REST": "groups",
        "HEADERS": {},
        "telegram_api_post": telegram_api_post,
        "_prepare_group_event_post": lambda text, summary, photo: (text[:40], [text[40:]] if len(text) > 40 else []),
        "_remember_event_group_post": lambda *args: None,
        "get_event_snapshot": lambda event_id: {"id": event_id, "cover_url": "https://example.org/photo.jpg"},
        "format_group_event": lambda event: "An event announcement longer than forty characters",
        "format_group_event_reminder": lambda event: "A reminder announcement longer than forty characters",
        "telegram_keyboard_payload": lambda event_id, url: {"inline_keyboard": [[{"text": "Я йду"}]]},
        "_event_google_maps_url": lambda event: "",
        "_format_group_event_header": lambda event: "Event",
        "bot": SimpleNamespace(),
        "Message": SimpleNamespace,
        "ChatMemberStatus": SimpleNamespace(ADMINISTRATOR="administrator", CREATOR="creator"),
    }
    exec(compile(mod, str(APP_PATH), "exec"), namespace)
    return namespace


class TopicRoutingTests(TestCase):
    def setUp(self):
        self.requests = FakeRequests()
        self.posts = []

        def post(method, payload):
            self.posts.append((method, payload))
            return True, {"message_id": 200 + len(self.posts)}

        self.ns = isolated_app(self.requests, post)

    def test_groups_keep_configured_forum_thread_id(self):
        self.assertEqual(self.ns["get_active_group_targets_sync"](), [
            {"chat_id": -1004328173945, "event_thread_id": 123}
        ])
        self.assertEqual(self.ns["get_active_group_ids_sync"](), [-1004328173945])

    def test_photo_and_followup_stay_in_same_topic(self):
        ok = self.ns["_send_group_event_text_sync"](
            -1004328173945, "An event announcement longer than forty characters",
            {"inline_keyboard": []}, photo_url="https://example.org/photo.jpg",
            event_id=5, message_thread_id=123,
        )
        self.assertTrue(ok)
        self.assertEqual([name for name, _ in self.posts], ["sendPhoto", "sendMessage"])
        self.assertTrue(all(payload["message_thread_id"] == 123 for _, payload in self.posts))
        self.assertEqual(self.posts[1][1]["reply_parameters"]["message_id"], 201)

    def test_without_topic_uses_original_group_behavior(self):
        self.ns["_send_group_event_text_sync"](-1004328173945, "Short", {}, message_thread_id=None)
        self.assertNotIn("message_thread_id", self.posts[0][1])

    def test_new_event_and_reminder_use_topic(self):
        self.ns["notify_groups_sync"](7)
        self.ns["notify_event_reminder_groups_sync"]({"id": 7})
        self.assertTrue(self.posts)
        self.assertTrue(all(payload.get("message_thread_id") == 123 for _, payload in self.posts))

    def test_admin_can_bind_topic_by_command(self):
        replies = []

        async def member(chat_id, user_id):
            return SimpleNamespace(status="administrator")

        async def answer(message):
            replies.append(message)

        self.ns["bot"].get_chat_member = member
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-1004328173945, type="supergroup"),
            from_user=SimpleNamespace(id=111),
            is_topic_message=True, message_thread_id=456,
            answer=answer,
        )
        asyncio.run(self.ns["cmd_seteventtopic"](message))
        self.assertEqual(self.requests.rows[0]["event_thread_id"], 456)
        self.assertTrue(any("Готово" in r for r in replies))


if __name__ == "__main__":
    main()
