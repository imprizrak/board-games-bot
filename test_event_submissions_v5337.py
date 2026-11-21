"""Backend event proposals tests, isolated from live API and Telegram."""
import ast
import re, json, logging
import unittest
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse, quote
from zoneinfo import ZoneInfo
from pathlib import Path
class DummyRequest:
    def __init__(self,path='/',method='GET',data=None,scope=None):
        self._data=data or {}
        self.form={}
        self.files={}
        self.args={'scope':scope} if scope else {}
        self.is_json=True
    def get_json(self,silent=False):return self._data
request=DummyRequest()
jsonify=lambda value:value

APP = Path(__file__).with_name('app.py')

def isolated(funcs, injected=None):
    tree=ast.parse(APP.read_text(encoding='utf8'))
    nodes=[]
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in funcs:
            node.decorator_list=[]
            nodes.append(node)
    assert len(nodes)==len(funcs), (funcs,[n.name for n in nodes])
    env={'request':request,'jsonify':jsonify,'json':json,'re':re,'logging':logging,
         'Decimal':Decimal,'InvalidOperation':InvalidOperation,'datetime':datetime,
         'timezone':timezone,'ZoneInfo':ZoneInfo,'urlparse':urlparse,'quote':quote,
         'Thread':lambda **kwargs:SimpleNamespace(start=lambda:None),
         'EVENT_TIMEZONE':'Europe/Kyiv','API_TOKEN':'token',
         'HEADERS':{},'EVENT_SUBMISSIONS_REST':'submissions',
         'SUPABASE_URL':'https://example.supabase.co',
         'requests':SimpleNamespace(RequestException=OSError)}
    env.update(injected or {})
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(APP),'exec'),env)
    return env

class MockResponse:
    def __init__(self,data,status=200):self.data=data;self.status_code=status;self.ok=status<400;self.text='{}'
    def json(self):return self.data
    def raise_for_status(self):
        if not self.ok:raise OSError('bad HTTP')

class EventProposalTests(unittest.TestCase):
    def setUp(self):
        self.future='2026-12-25'
        self.form={'title':'Чаювання у клубі','event_date':self.future,'event_time':'18:30',
                   'description':'Знайомство і чай','max_participants':'10',
                   'location_text':'Секретна адреса','game_names_json':'[]',
                   'price_mode':'free','price_uah':'0','payment_method':'free'}

    def validators(self):
        return isolated(['_proposal_validated_fields','_event_price_kop','_event_payment_config','_monobank_jar_link'],
           {'_normalize_event_location':lambda value:{'location_text':value,'location_map_url':'',
             'location_lat':None,'location_lng':None}})

    def test_valid_member_fields_restrict_reward(self):
        env=self.validators()
        data={**self.form,'attendance_reward_stils':'500'}
        result=env['_proposal_validated_fields'](data,admin=False)
        self.assertEqual(result['attendance_reward_stils'],50)
        self.assertEqual(result['location_text'],'Секретна адреса')
        self.assertEqual(result['title'],'Чаювання у клубі')
        self.assertEqual(result['payment_method'],'free')

    def test_member_cannot_select_automatic_merchant_checkout(self):
        env=self.validators()
        data={**self.form,'price_mode':'paid','price_uah':'150',
              'payment_method':'monobank'}
        with self.assertRaisesRegex(ValueError,'receipt_only_for_members'):
            env['_proposal_validated_fields'](data,admin=False)

    def test_valid_receipt_requires_details_or_valid_jar(self):
        env=self.validators()
        data={**self.form,'price_mode':'paid','price_uah':'150',
            'payment_method':'receipt','payment_link':'https://send.monobank.ua/jar/Abcd1234'}
        result=env['_proposal_validated_fields'](data,admin=False)
        self.assertEqual(result['price_kop'],15000)
        self.assertEqual(result['payment_link'],data['payment_link'])
        data['payment_link']='https://evil.com/jar/Abcd1234'
        with self.assertRaisesRegex(ValueError,'invalid_monobank_jar_link'):
            env['_proposal_validated_fields'](data,admin=False)

    def test_bad_title_and_time_rejected(self):
        env=self.validators()
        for updates in ({'title':'x'},{'event_time':'99:99'},{'max_participants':'1000'}):
            with self.subTest(updates=updates),self.assertRaises(ValueError):
                env['_proposal_validated_fields']({**self.form,**updates},admin=False)

    def test_no_telegram_auth_no_database_reads(self):
        api=SimpleNamespace(get=Mock())
        env=isolated(['propose_event'],{'_request_telegram_user':lambda:None,'requests':api})
        env['request']=DummyRequest(data=self.form)
        response,status=env['propose_event']()
        self.assertEqual(status,401)
        self.assertEqual(response['error'],'telegram_auth_required')
        api.get.assert_not_called()

    def test_authenticated_member_submission_does_not_create_public_event(self):
        api=SimpleNamespace(get=Mock(return_value=MockResponse([])),
            post=Mock(return_value=MockResponse([{'id':18,'status':'pending'}])))
        env=self.validators()
        env=isolated(['propose_event'],{
            'requests':api,'_request_telegram_user':lambda:{'id':345,'username':'member'},
            '_proposal_authorized_member':lambda u:True,
            '_telegram_user_is_group_admin':lambda uid:False,
            '_proposal_validated_fields':env['_proposal_validated_fields'],
            '_proposal_cover_file':lambda:None,
            '_payment_identity':lambda u:(345,'member','Учасник'),
            '_proposal_notify_admins':lambda item:None})
        env['request']=DummyRequest(data=self.form)
        response,status=env['propose_event']()
        self.assertEqual(status,201)
        self.assertEqual(response['status'],'pending')
        self.assertEqual(api.post.call_args.args[0],'submissions')
        self.assertEqual(api.post.call_args.kwargs['json']['telegram_user_id'],345)
        self.assertEqual(api.post.call_args.kwargs['json']['status'],'pending')

    def test_member_cannot_view_admin_queue(self):
        env=isolated(['list_event_submissions'],{
            '_request_telegram_user':lambda:{'id':10},'_telegram_user_is_group_admin':lambda uid:False})
        env['request']=DummyRequest(scope='pending')
        response,status=env['list_event_submissions']()
        self.assertEqual(status,403)

    def test_review_requires_group_admin(self):
        env=isolated(['review_event_submission'],{
            '_admin_required_response':lambda:(jsonify({'error':'group_admin_required'}),403)})
        env['request']=DummyRequest(data={'decision':'approved'})
        response,status=env['review_event_submission'](8)
        self.assertEqual(status,403)

    def test_proposals_must_not_appear_in_public_event_endpoint(self):
        source=APP.read_text('utf8')
        block=source.split('def get_events():',1)[1].split("@app.route('/api/events/<int:event_id>/quests/participants'",1)[0]
        self.assertNotIn('EVENT_SUBMISSIONS_REST',block)
        self.assertIn('status',block)
        approval=source.split('def review_event_submission(',1)[1].split('@app.route("/api/events", methods=["POST"])',1)[0]
        self.assertIn('styloteka_approve_event_submission',approval)
        self.assertIn('_admin_required_response()',approval)
        self.assertNotIn('INSERT INTO public.events',source)

if __name__=='__main__':unittest.main(verbosity=2)
