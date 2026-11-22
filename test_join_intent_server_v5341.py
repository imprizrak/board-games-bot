"""Isolated authenticated join intent and receipt-queue timeline tests."""
import ast
import unittest
import logging
from pathlib import Path
from unittest.mock import Mock

SOURCE=Path(__file__).with_name('app.py').read_text('utf-8')
TREE=ast.parse(SOURCE)

def get_fn(name,env):
    matching=[n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(matching)==1,(name,matching)
    fn=matching[0]
    fn.decorator_list=[]
    ns={**env}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'app.py','exec'),ns)
    return ns[name]

class JoinIntentTests(unittest.TestCase):
    def setUp(self):
        self.published={'id':21,'price_kop':10000,'payment_method':'receipt','status':'scheduled'}
        self.sent=[]
        self.mock_requests=Mock()
        self.mock_requests.RequestException=OSError
        self.mock_requests.post.side_effect=self.post
        self.mock_requests.get.side_effect=self.get
        self.lookup=[]
    def post(self,url,headers=None,params=None,json=None,timeout=None):
        self.sent.append({'url':url,'headers':headers,'params':params,'json':json})
        return Mock(status_code=201,raise_for_status=lambda:None)
    def get(self,url,headers=None,params=None,timeout=None):
        self.lookup.append(params)
        return Mock(raise_for_status=lambda:None,json=lambda:[
            {'event_id':21,'telegram_user_id':123,'clicked_at':'2026-11-22T12:35:00Z'}])
    def server(self,user={'id':123}):
        env={'_request_telegram_user':lambda:user,
             'get_event_snapshot':lambda _:self.published,
             'jsonify':lambda x:x,'requests':self.mock_requests,
             'EVENT_JOIN_INTENTS_REST':'https://mock/rest/v1/event_join_intents',
             'HEADERS':{'apikey':'server-only'},'logging':logging}
        return get_fn('record_event_join_intent',env)
    def test_verified_user_server_time_and_first_click_conflict(self):
        result=self.server()(21)
        self.assertEqual(result,({'status':'recorded'},200))
        self.assertEqual(self.sent[0]['json'],{'event_id':21,'telegram_user_id':123})
        self.assertNotIn('clicked_at',self.sent[0]['json'])
        self.assertEqual(self.sent[0]['params'],{'on_conflict':'event_id,telegram_user_id'})
        self.assertIn('ignore-duplicates',self.sent[0]['headers']['Prefer'])
    def test_no_auth_rejected(self):
        self.assertEqual(self.server(user=None)(21),({'error':'telegram_auth_required'},401))
        self.assertEqual(self.sent,[])
    def test_free_or_automated_payment_rejected(self):
        for method,price in [('free',0),('monobank',10000)]:
            self.published={**self.published,'payment_method':method,'price_kop':price}
            self.assertEqual(self.server()(21),({'error':'not_receipt_event'},409))
        self.assertEqual(self.sent,[])
    def test_closed_event_rejected(self):
        self.published={**self.published,'status':'completed'}
        self.assertEqual(self.server()(21),({'error':'event_closed'},409))
        self.assertEqual(self.sent,[])
    def test_database_failure_does_not_fabricate_time(self):
        self.mock_requests.post.side_effect=OSError('network')
        self.assertEqual(self.server()(21),({'error':'join_intent_unavailable'},503))
    def test_admin_list_join_annotation_by_both_ids(self):
        env={'requests':self.mock_requests,'EVENT_JOIN_INTENTS_REST':'https://mock/rest',
             'HEADERS':{'apikey':'server-only'},'logging':logging}
        fn=get_fn('_attach_event_join_clicks',env)
        rows=[{'id':2,'event_id':21,'telegram_user_id':123,'created_at':'2026-11-22T12:42:00Z'},
              {'id':3,'event_id':21,'telegram_user_id':124,'created_at':'2026-11-22T12:43:00Z'}]
        result=fn(rows)
        self.assertEqual(result[0]['joined_at'],'2026-11-22T12:35:00Z')
        self.assertIsNone(result[1]['joined_at'])
        self.assertEqual(self.lookup[0]['event_id'],'in.(21)')
        self.assertEqual(self.lookup[0]['telegram_user_id'],'in.(123,124)')
    def test_admin_timeline_outage_preserves_moderation(self):
        self.mock_requests.get.side_effect=OSError('outage')
        fn=get_fn('_attach_event_join_clicks',{'requests':self.mock_requests,
            'EVENT_JOIN_INTENTS_REST':'https://mock/rest','HEADERS':{},'logging':logging})
        rows=fn([{'event_id':21,'telegram_user_id':123}])
        self.assertEqual(rows,[{'event_id':21,'telegram_user_id':123,'joined_at':None}])

if __name__=='__main__':unittest.main()
