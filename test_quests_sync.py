"""Offline regression tests; does not contact Quests or modify Supabase."""
import ast
import hashlib
import logging
from pathlib import Path
from threading import Lock
from datetime import datetime, timezone
import time
import uuid
from urllib.parse import urlparse, parse_qs


class FakeResponse:
    is_redirect = False

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeRequests:
    RequestException = RuntimeError

    def __init__(self):
        self.count = 0
        self.payload = {}

    def get(self, url, **kwargs):
        self.count += 1
        assert url.startswith('https://share.quests.inc/api/quests/')
        assert kwargs['allow_redirects'] is False
        return FakeResponse(self.payload)


def run():
    requests = FakeRequests()
    tree = ast.parse(Path(__file__).with_name('app.py').read_text(encoding='utf-8'))
    names = {'_validated_quests_event_url', '_quests_public_id', '_quests_public_snapshot'}
    module = ast.Module(body=[node for node in tree.body
                              if isinstance(node, ast.FunctionDef) and node.name in names],
                        type_ignores=[])
    environment = dict(requests=requests, urlparse=urlparse, parse_qs=parse_qs,
                       uuid=uuid, hashlib=hashlib, logging=logging, time=time,
                       datetime=datetime, timezone=timezone, Lock=Lock,
                       _QUESTS_CACHE={}, _QUESTS_CACHE_LOCK=Lock(), _QUESTS_CACHE_SECONDS=75)
    exec(compile(module, 'app.py', 'exec'), environment)
    qid = '6815f5af-9c50-4b69-adf8-7b263c8e173a'
    link = f'https://share.quests.inc/event.html?id={qid}'
    assert environment['_quests_public_id'](link) == qid
    assert environment['_quests_public_id']('https://example.org/event.html?id='+qid) is None
    assert environment['_quests_public_id']('https://share.quests.inc/other?id='+qid) is None
    assert environment['_quests_public_id']('https://share.quests.inc/event.html?id=bad') is None

    people = [dict(firstname=f'Гість{i}', last_name='Тест',
                   share_url=f'https://quests.inc/user/{i}', username=f'guest{i}') for i in range(3)]
    requests.payload = {'is_success': True, 'data': [{'participators_count': 3,
                                                       'participators_15': people}]}
    first = environment['_quests_public_snapshot'](link)
    assert first['count'] == 3 and first['complete'] and len(first['participants']) == 3
    assert len(set(p['key'] for p in first['participants'])) == 3
    assert all(len(p['key']) == 64 for p in first['participants'])
    assert requests.count == 1
    assert environment['_quests_public_snapshot'](link)['count'] == 3
    assert requests.count == 1  # TTL cache avoids repeated calls

    requests.payload['data'][0]['participators_count'] = 20
    partial = environment['_quests_public_snapshot'](link, force=True)
    assert partial['count'] == 20 and not partial['complete']
    assert len(partial['participants']) == 3
    assert requests.count == 2
    print('Quests sync tests passed')


if __name__ == '__main__':
    run()
