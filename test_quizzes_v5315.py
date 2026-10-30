"""Offline regression tests for quiz security, UI, and schema wiring."""

from pathlib import Path
import ast
import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock


ROOT = Path(__file__).parent
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")
SQL = (ROOT / "QUIZZES_SETUP.sql").read_text(encoding="utf-8")


class QuizFeatureTests(unittest.TestCase):
    @staticmethod
    def load_route(name, **globals_overrides):
        """Load only a route function; dependencies aren't installed in CI."""
        source_tree = ast.parse(APP)
        route_node = next(x for x in source_tree.body if isinstance(x, ast.FunctionDef) and x.name == name)
        route_node.decorator_list = []
        namespace = {"jsonify": lambda payload: payload, **globals_overrides}
        exec(compile(ast.Module(body=[route_node], type_ignores=[]), 'app.py', 'exec'), namespace)
        return namespace[name]

    def test_app_python_is_valid_and_has_required_endpoints(self):
        ast.parse(APP)
        for endpoint in (
            '/api/quizzes/catalog', '/api/quizzes/next',
            '/api/quizzes/answer', '/api/quizzes/questions',
        ):
            with self.subTest(endpoint=endpoint):
                self.assertIn(f'@app.route("{endpoint}"', APP)

    def test_no_correct_answers_leak_on_public_quiz_reads(self):
        start = APP.index('def quizzes_catalog():')
        end = APP.index('@app.route("/api/quizzes/answer"', start)
        quiz_read_routes = APP[start:end]
        self.assertNotIn('"correct_index"', quiz_read_routes)
        self.assertIn('category,question,options', quiz_read_routes)
        self.assertIn('telegram_auth_required', quiz_read_routes)

    def test_awards_are_atomic_and_only_once_per_question(self):
        self.assertIn('primary key (telegram_user_id, question_id)', SQL)
        self.assertIn('pg_advisory_xact_lock(p_user_id)', SQL)
        self.assertIn('p_answer_index = q.correct_index', SQL)
        self.assertIn('on conflict do nothing', SQL)
        self.assertIn('reward_stils > 0', SQL)
        self.assertIn('total_today >= 5', SQL)
        self.assertIn('gold = coalesce(public.profiles.gold, 0) + 2', SQL)
        self.assertIn('security invoker', SQL.lower())

    def test_schema_excludes_anon_and_authenticated_clients(self):
        self.assertIn('enable row level security', SQL)
        self.assertIn('from public, anon, authenticated', SQL)
        self.assertIn('grant execute on function public.styloteka_answer_quiz', SQL)
        self.assertIn('to service_role', SQL)

    def test_only_library_games_are_published_with_questions(self):
        self.assertIn('from public.games g join bank b on', SQL)
        self.assertIn('"active": "eq.true"', APP)
        self.assertIn('if game["total"]', APP)
        self.assertIn('if int(q["id"]) not in done', APP)

    def test_six_tabs_and_quiz_gameplay_are_accessible(self):
        nav = HTML.split('<div class="bottom-nav">', 1)[1].split('</div>', 1)[0]
        buttons = re.findall(r'<button class="nav-btn(?: active)?" data-view="([^"]+)"', nav)
        self.assertEqual(buttons, [
            'view-home', 'view-library', 'view-quizzes',
            'view-events', 'view-community', 'view-profile',
        ])
        self.assertIn('id="quizGameGrid"', HTML)
        self.assertIn('function quizAnswer(answerIndex)', HTML)
        self.assertIn('function quizSaveQuestion(event)', HTML)
        self.assertIn('grid-template-columns: repeat(6, minmax(0, 1fr)) !important;', HTML)

    def test_legacy_minigames_stay_removed_and_profiles_preserved(self):
        self.assertNotIn('id="view-coins"', HTML)
        self.assertNotIn('id="view-treasure"', HTML)
        self.assertNotIn('id="view-dungeon"', HTML)
        self.assertIn('id="view-library"', HTML)
        self.assertIn('id="view-profile"', HTML)
        self.assertIn('id="profileShopModal"', HTML)

    def test_answer_endpoint_checks_telegram_identity_before_rpc(self):
        rpc = Mock()
        endpoint = self.load_route(
            'quizzes_answer', _request_telegram_user=lambda: None,
            requests=SimpleNamespace(post=rpc), request=SimpleNamespace(get_json=lambda **_: {}),
        )
        body, code = endpoint()
        self.assertEqual(code, 401)
        self.assertEqual(body['error'], 'telegram_auth_required')
        rpc.assert_not_called()

    def test_answer_endpoint_uses_verified_telegram_id_not_body_user_id(self):
        rpc = Mock(return_value=SimpleNamespace(
            json=lambda: {"status": "answered", "reward": 2, "correct": True},
            raise_for_status=lambda: None,
        ))
        endpoint = self.load_route(
            'quizzes_answer', _request_telegram_user=lambda: {"id": 123456},
            requests=SimpleNamespace(post=rpc),
            request=SimpleNamespace(get_json=lambda **_: {"question_id": 7, "answer_index": 2, "user_id": 999999}),
            QUIZ_ANSWER_RPC='rpc', HEADERS={}, logging=SimpleNamespace(exception=lambda *_: None),
        )
        body, code = endpoint()
        self.assertEqual(code, 200)
        self.assertEqual(body['reward'], 2)
        self.assertEqual(rpc.call_args.kwargs['json']['p_user_id'], 123456)

    def test_invalid_answer_cannot_reach_reward_rpc(self):
        rpc = Mock()
        endpoint = self.load_route(
            'quizzes_answer', _request_telegram_user=lambda: {"id": 1},
            requests=SimpleNamespace(post=rpc),
            request=SimpleNamespace(get_json=lambda **_: {"question_id": 1, "answer_index": 4}),
        )
        body, code = endpoint()
        self.assertEqual((body['error'], code), ('invalid_answer', 400))
        rpc.assert_not_called()

    def test_already_answered_returns_conflict_not_reward(self):
        rpc = Mock(return_value=SimpleNamespace(
            json=lambda: {'status':'already_answered'}, raise_for_status=lambda: None,
        ))
        endpoint = self.load_route(
            'quizzes_answer', _request_telegram_user=lambda: {"id": 1},
            requests=SimpleNamespace(post=rpc),
            request=SimpleNamespace(get_json=lambda **_: {"question_id": 1, "answer_index": 0}),
            QUIZ_ANSWER_RPC='rpc', HEADERS={}, logging=SimpleNamespace(exception=lambda *_: None),
        )
        body, code = endpoint()
        self.assertEqual((body['status'], code), ('already_answered', 409))

    def test_question_creation_is_restricted_to_group_admins(self):
        rpc = Mock()
        endpoint = self.load_route(
            'quizzes_add_question', _request_telegram_user=lambda: {'id': 42},
            _telegram_user_is_group_admin=lambda _: False,
            requests=SimpleNamespace(post=rpc),
        )
        body, code = endpoint()
        self.assertEqual((body['error'], code), ('admin_only', 403))
        rpc.assert_not_called()

    def test_next_route_only_returns_not_yet_attempted_question(self):
        def read_rows(url, params):
            if url == 'questions':
                return [
                    {'id':1,'game_id':12,'question':'First','options':['A','B','C','D']},
                    {'id':2,'game_id':12,'question':'Second','options':['A','B','C','D']},
                ]
            return [{'question_id':1}]

        endpoint = self.load_route(
            'quizzes_next', _request_telegram_user=lambda: {'id': 42},
            _quiz_supabase_rows=read_rows,
            QUIZ_QUESTIONS_REST='questions', QUIZ_ATTEMPTS_REST='attempts',
            request=SimpleNamespace(args={'game_id':'12'}),
        )
        response = endpoint()
        self.assertEqual(response['question']['id'], 2)
        self.assertEqual(response['remaining'], 1)
        self.assertEqual(response['total'], 2)
        self.assertNotIn('correct_index', response['question'])


if __name__ == '__main__':
    unittest.main()
