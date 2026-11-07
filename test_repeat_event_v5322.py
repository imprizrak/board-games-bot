"""Regression checks for repeating a completed event as a fresh event."""
import ast
import json
import logging
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
HTML = (ROOT / 'index.html').read_text('utf-8')
APP = (ROOT / 'app.py').read_text('utf-8')

class Response:
    def __init__(self, rows):
        self.rows = rows
        self.content = json.dumps(rows).encode('utf8')
        self.status_code = 201
        self.text = ''
    def json(self):
        return self.rows
    def raise_for_status(self):
        pass


def build_handler(source, *, requests_log, posts_log, data):
    func = next(n for n in ast.parse(APP).body if isinstance(n, ast.FunctionDef) and n.name == 'add_event')
    func.decorator_list = []
    def get(url, **kwargs):
        requests_log.append((url, kwargs))
        record = {'id':14, 'status': source, 'cover_url':'https://example.org/event-cover.jpg'} if source else None
        return Response([record] if record else [])
    def post(url, **kwargs):
        posts_log.append((url, kwargs))
        return Response([{'id':90}])
    env = dict(
        request=SimpleNamespace(content_type='application/json',get_json=lambda **_:data),
        jsonify=lambda value:SimpleNamespace(get_json=lambda:value), requests=SimpleNamespace(get=get, post=post,
            RequestException=Exception), _admin_required_response=lambda: None,
        json=json, logging=logging, datetime=datetime, ZoneInfo=ZoneInfo,
        EVENT_TIMEZONE='Europe/Berlin', EVENTS_REST='https://example.org/events',
        HEADERS={}, upload_file=lambda file,group:'https://example.org/uploaded.jpg',
        _normalize_event_location=lambda value:{'location_text':value or '',
            'location_map_url':None, 'location_lat':None, 'location_lng':None},
        _event_price_kop=lambda data:0, _validated_quests_event_url=lambda v:'',
        _quests_count_from_input=lambda n:0,
        _event_payment_config=lambda data, price:('free','',''),
        _public_event_description=lambda payload:'',
        notify_subscribers_async=lambda *a,**k:None,
        notify_groups_async=lambda *a,**k:None,
        log_activity=lambda *a,**k:None
    )
    exec(compile(ast.Module(body=[func], type_ignores=[]),str(ROOT/'app.py'),'exec'),env)
    return env['add_event']

class RepeatEventTests(unittest.TestCase):
    def test_history_button_only_for_admins_and_history(self):
        self.assertIn("historyMode && isAdmin ? '<button type=\"button\" class=\"ev-repeat-btn\"",HTML)
        self.assertIn('openRepeatEventModal(ev)',HTML)

    def test_frontend_opens_new_event_not_patch(self):
        self.assertIn('repeatSourceEventId = sourceId;',HTML)
        self.assertIn('editingEventId = null;',HTML)
        self.assertIn("resetEventDatePickerField();\n  // A previous Quests event",HTML)
        self.assertIn("document.getElementById('eventQuestsUrl').value = '';",HTML)
        self.assertIn("formData.append('repeat_source_event_id', String(repeatSourceEventId));",HTML)
        self.assertIn("loadEvents(isEditing ? eventsMode : 'upcoming');",HTML)

    def test_server_creates_fresh_event_with_cover_but_no_legacy_records(self):
        gets=[]; posts=[]
        payload={'repeat_source_event_id':'14','title':'Новий ігровий вечір',
            'event_date':'2099-11-22','event_time':'18:30',
            'location_text':'New location', 'game_names_json':'["Селестія"]',
            'max_participants':5}
        handler=build_handler('completed',requests_log=gets,posts_log=posts,data=payload)
        result=handler()
        self.assertEqual(result.get_json()['event_id'],90)
        self.assertEqual(len(gets),1)
        body=posts[0][1]['json']
        self.assertEqual(body['status'],'scheduled')
        self.assertIsNone(body['completed_at'])
        self.assertEqual(body['event_date'],'2099-11-22')
        self.assertEqual(body['event_time'],'18:30')
        self.assertEqual(body['cover_url'],'https://example.org/event-cover.jpg')
        self.assertEqual(body['game_names'],['Селестія'])
        self.assertEqual(body['quests_checked_in_count'],0)
        self.assertEqual(body['quests_count'],0)
        for old in ('rsvps','receipt_path','paid','source_event_id','attendance','id'):
            self.assertNotIn(old,body)

    def test_source_must_be_completed(self):
        gets=[];posts=[]
        handler=build_handler('scheduled',requests_log=gets,posts_log=posts,data={
            'repeat_source_event_id':'14','event_date':'2099-11-22','title':'Тест'})
        response,code=handler()
        self.assertEqual(code,409)
        self.assertEqual(response.get_json()['error'],'repeat_source_not_completed')
        self.assertEqual(posts,[])

    def test_rejects_past_repeat_date(self):
        gets=[];posts=[]
        handler=build_handler('completed',requests_log=gets,posts_log=posts,data={
            'repeat_source_event_id':'14','event_date':'2020-01-01','title':'Тест'})
        response,code=handler()
        self.assertEqual(code,400)
        self.assertEqual(response.get_json()['error'],'repeat_date_in_past')
        self.assertEqual(posts,[])

    def test_cover_removal_does_not_clone_old_poster(self):
        gets=[];posts=[]
        handler=build_handler('completed',requests_log=gets,posts_log=posts,data={
            'repeat_source_event_id':'14','event_date':'2099-11-22','title':'Тест',
            'remove_cover':'1'})
        result=handler()
        self.assertEqual(result.get_json()['status'],'ok')
        self.assertIsNone(posts[0][1]['json']['cover_url'])

    def test_plain_event_creation_has_no_source_dependency(self):
        gets=[];posts=[]
        handler=build_handler('completed',requests_log=gets,posts_log=posts,data={
            'event_date':'2099-11-22','title':'Тест'})
        result=handler()
        self.assertEqual(result.get_json()['status'],'ok')
        self.assertEqual(gets,[])
        self.assertIsNone(posts[0][1]['json']['cover_url'])

if __name__=='__main__':unittest.main()
