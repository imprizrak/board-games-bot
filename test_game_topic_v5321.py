"""Tests for group game announcements routing exclusively to the chosen forum topic."""
import ast
import asyncio
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace

APP = Path(__file__).with_name('app.py')


def isolate(*names, **deps):
    tree = ast.parse(APP.read_text(encoding='utf-8'))
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            nodes.append(node)
    if len(nodes) != len(names):
        raise RuntimeError(f'missing functions: {names}')
    env = {'logging': logging, 'HEADERS': {}, 'TELEGRAM_GROUPS_REST': '/groups', 'Message': object}
    env.update(deps)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP), 'exec'), env)
    return env


class Response:
    def __init__(self, rows, ok=True):
        self.rows = rows
        self.ok = ok
    def json(self):
        return self.rows
    def raise_for_status(self):
        if not self.ok:
            raise ValueError('HTTP error')


class GameTopicTests(unittest.TestCase):
    def test_fetches_separate_event_and_game_topics(self):
        queries = []
        def get(url, *, headers, params, timeout):
            queries.append(params)
            return Response([{'chat_id': -100123, 'event_thread_id': 2, 'game_thread_id': 5},
                             {'chat_id': -100124, 'event_thread_id': None, 'game_thread_id': None}])
        env = isolate('get_active_group_targets_sync', requests=SimpleNamespace(get=get))
        result = env['get_active_group_targets_sync']()
        self.assertEqual(queries[0]['select'], 'chat_id,event_thread_id,game_thread_id')
        self.assertEqual(result[0], {'chat_id': -100123, 'event_thread_id': 2, 'game_thread_id': 5})
        self.assertIsNone(result[1]['game_thread_id'])

    def notifications(self, targets, cover_url=None, fail_photo=False):
        sent = []
        def post(method, payload):
            sent.append((method, payload))
            return ((False, 'photo unavailable') if fail_photo and method == 'sendPhoto' else (True, {'message_id': 1}))
        env = isolate('notify_game_groups_sync',
            get_active_group_targets_sync=lambda: targets,
            _game_group_announcement=lambda name, description: 'Тест: ' + name,
            game_group_keyboard_payload=lambda game_id: {'inline_keyboard': [[{'text': 'Відкрити', 'url': 'https://example.org'}]]},
            telegram_api_post=post)
        env['notify_game_groups_sync']('Каркассон', 'правила', cover_url, 77)
        return sent

    def test_send_photo_and_button_to_games_topic(self):
        sent = self.notifications([{'chat_id': -100123, 'game_thread_id': 5, 'event_thread_id': 9}], 'https://example.org/cover.jpg')
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], 'sendPhoto')
        self.assertEqual(sent[0][1]['message_thread_id'], 5)
        self.assertEqual(sent[0][1]['reply_markup']['inline_keyboard'][0][0]['text'], 'Відкрити')

    def test_photo_fallback_stays_in_same_topic(self):
        sent = self.notifications([{'chat_id': -100123, 'game_thread_id': 5, 'event_thread_id': 9}], 'https://example.org/broken.jpg', True)
        self.assertEqual([x[0] for x in sent], ['sendPhoto', 'sendMessage'])
        self.assertEqual([x[1]['message_thread_id'] for x in sent], [5, 5])

    def test_text_announcement_goes_to_games_topic(self):
        sent = self.notifications([{'chat_id': -100123, 'game_thread_id': 123}])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], 'sendMessage')
        self.assertEqual(sent[0][1]['message_thread_id'], 123)

    def test_unconfigured_group_gets_no_games_announcement(self):
        sent = self.notifications([{'chat_id': -100123, 'event_thread_id': 9, 'game_thread_id': None}])
        self.assertEqual(sent, [])

    def test_multiple_groups_route_each_to_own_topic_and_no_duplicate(self):
        sent = self.notifications([{'chat_id': -100123, 'game_thread_id': 5},
                                   {'chat_id': -100124, 'game_thread_id': 17},
                                   {'chat_id': -100123, 'game_thread_id': 5}])
        self.assertEqual({(x[1]['chat_id'],x[1]['message_thread_id']) for x in sent}, {(-100123,5),(-100124,17)})
        self.assertEqual(len(sent),2)

    def test_approval_and_direct_creation_still_publish_to_group(self):
        src = APP.read_text(encoding='utf-8')
        self.assertGreaterEqual(src.count('notify_game_groups_async('), 3)

    def async_context(self, is_admin=True, is_topic=True):
        answers=[]; patches=[]; group=-100123
        class Message:
            def __init__(self):
                self.chat=SimpleNamespace(type='supergroup',id=group)
                self.from_user=SimpleNamespace(id=44)
                self.is_topic_message=is_topic
                self.message_thread_id=77 if is_topic else None
            async def answer(self,text):
                answers.append(text)
        async def get_member(chat_id,user_id):
            return SimpleNamespace(status='administrator' if is_admin else 'member')
        def patch(url,*,headers,params,json,timeout):
            patches.append(json)
            return Response([{'chat_id':group}])
        env = isolate('cmd_setgamestopic',
            bot=SimpleNamespace(get_chat_member=get_member),
            ChatMemberStatus=SimpleNamespace(ADMINISTRATOR='administrator',CREATOR='creator'),
            requests=SimpleNamespace(patch=patch))
        asyncio.run(env['cmd_setgamestopic'](Message()))
        return answers,patches

    def test_admin_can_bind_topic_from_within_topic(self):
        answers,patches=self.async_context()
        self.assertEqual(patches, [{'game_thread_id': 77}])
        self.assertIn('підключено',answers[-1])

    def test_non_admin_cannot_bind_topic(self):
        answers,patches=self.async_context(is_admin=False)
        self.assertFalse(patches)
        self.assertIn('адміністратор',answers[-1])

    def test_command_rejects_general_chat(self):
        answers,patches=self.async_context(is_topic=False)
        self.assertFalse(patches)
        self.assertIn('Настілки',answers[-1])

    def test_test_command_posts_to_saved_topic_not_user_topic(self):
        sent=[]; answers=[]
        class Message:
            chat=SimpleNamespace(type='supergroup',id=-100123)
            from_user=SimpleNamespace(id=44)
            async def answer(self,text): answers.append(text)
        async def get_member(chat_id,user_id): return SimpleNamespace(status='administrator')
        env=isolate('cmd_testgamestopic',
            bot=SimpleNamespace(get_chat_member=get_member),
            ChatMemberStatus=SimpleNamespace(ADMINISTRATOR='administrator',CREATOR='creator'),
            get_active_group_targets_sync=lambda:[{'chat_id':-100123,'event_thread_id':91,'game_thread_id':77}],
            telegram_api_post=lambda method,payload: (sent.append((method,payload)) or True,{}))
        asyncio.run(env['cmd_testgamestopic'](Message()))
        self.assertEqual(sent[0][1]['message_thread_id'], 77)
        self.assertNotEqual(sent[0][1]['message_thread_id'], 91)


if __name__=='__main__':
    unittest.main()
