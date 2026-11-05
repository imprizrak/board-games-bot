"""Regression checks: no address/game leaks and private attendee-only DM delivery."""
import ast
import logging
from pathlib import Path
from unittest import TestCase
from types import SimpleNamespace
APP = Path(__file__).with_name('app.py')


def isolate(names, dependencies=None):
    tree = ast.parse(APP.read_text(encoding='utf-8'))
    funcs=[]
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list=[]
            funcs.append(node)
    scope={'logging':logging, 'requests':SimpleNamespace(RequestException=OSError),
           'PRIVATE_EVENT_FIELDS':('location_text','location_map_url','location_lat','location_lng',
                                  'game_name','game_names','member_game_picks','quests_event_url'),
           'API_TOKEN':'testtoken', 'HEADERS':{},'RSVPS_REST':'rsvps'}
    scope.update(dependencies or {})
    exec(compile(ast.Module(body=funcs,type_ignores=[]),str(APP),'exec'),scope)
    return scope

class EventPrivateAccessTests(TestCase):
    def setUp(self):
        self.event={'id':21,'title':'Тест','event_date':'2026-12-15','event_time':'18:30',
                    'location_text':'Секретна адреса','location_map_url':'https://example.org/private',
                    'game_names':['Секретна гра'],'game_name':'Секретна гра',
                    'member_game_picks':[{'game_name':'Секретна гра'}],
                    'quests_event_url':'https://example.org/private-quests',
                    'payment_details':'12345678', 'rsvps':[{'telegram_user_id':11,'status':'going'}]}
    def test_public_redaction_removes_all_private_fields(self):
        scope=isolate(['_public_event_description','_event_redacted'])
        redacted=scope['_event_redacted'](self.event)
        self.assertNotIn('Секретна адреса',str(redacted))
        self.assertNotIn('Секретна гра',str(redacted))
        self.assertNotIn('12345678',str(redacted))
        self.assertEqual(redacted['title'],'Тест')
    def test_only_exact_telegram_user_id_and_going_status(self):
        scope=isolate(['_event_confirmed_for_user'])
        verify=scope['_event_confirmed_for_user']
        self.assertTrue(verify(21,{'id':11},self.event['rsvps']))
        self.assertFalse(verify(21,{'id':12},self.event['rsvps']))
        self.assertFalse(verify(21,{'id':11},[{'telegram_user_id':11,'status':'waitlist'}]))
        self.assertFalse(verify(21,None,self.event['rsvps']))
    def test_public_group_header_hides_location_and_games(self):
        scope=isolate(['_format_group_event_header'],{'_event_source_counts':lambda e:(0,0)})
        text=scope['_format_group_event_header'](self.event)
        self.assertNotIn('Секретна адреса',text)
        self.assertNotIn('Секретна гра',text)
        self.assertIn('особистому',text)
    def test_private_message_sends_only_after_confirmation(self):
        sent=[]
        scope=isolate(['_send_private_event_details','_private_event_text'],{
            'get_event_snapshot':lambda eid:self.event,
            '_event_confirmed_for_user':lambda eid,user,rows:int(user['id'])==11,
            '_event_mini_app_link':lambda eid:'https://t.me/test_bot?startapp=event_21',
            'telegram_api_post':lambda method,payload:(sent.append((method,payload)) or True,{'message_id':1})})
        self.assertFalse(scope['_send_private_event_details'](21,12))
        self.assertEqual(sent,[])
        self.assertTrue(scope['_send_private_event_details'](21,11))
        self.assertEqual(sent[0][1]['chat_id'],11)
        self.assertIn('Секретна адреса',sent[0][1]['text'])
        self.assertIn('Секретна гра',sent[0][1]['text'])
    def test_group_keyboards_do_not_embed_map_or_game_poll(self):
        scope=isolate(['telegram_keyboard_payload'],{
            'get_event_snapshot':lambda eid:self.event,
            '_event_mini_app_link':lambda eid,payment=False:'https://t.me/test_bot',
            '_event_game_poll_rows':lambda ev:[{'text':'SECRET GAME'}]})
        keyboard=scope['telegram_keyboard_payload'](21,'https://example.org/private')['inline_keyboard']
        self.assertNotIn('SECRET GAME',str(keyboard))
        self.assertNotIn('https://example.org/private',str(keyboard))
    def test_member_games_get_requires_auth_before_querying_votes(self):
        # Structural check makes accidental public regression visible.
        source=APP.read_text(encoding='utf-8')
        body=source.split('def member_event_games_get(event_id):',1)[1].split('def member_event_games_add(',1)[0]
        self.assertIn('telegram_auth_required',body)
        self.assertIn('confirmed_attendee_required',body)
        self.assertLess(body.index('confirmed_attendee_required'),body.index('_get_event_game_picks(event_id)'))
    def test_frontend_uses_authenticated_fetch_and_receipt_moderation(self):
        html=APP.with_name('index.html').read_text()
        self.assertIn('openAdminEventReceipts',html)
        self.assertIn('openEventReceiptModal',html)
        self.assertIn('openPrivateEventDetails',html)
        self.assertIn('eventPaymentDetails',html)
        self.assertIn("headers:telegramAuthHeaders(),cache:'no-store'",html)

