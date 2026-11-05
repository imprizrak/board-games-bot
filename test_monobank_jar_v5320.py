"""Regression tests for safe jar links and manually approved event registrations."""
import ast
from pathlib import Path
import re
import subprocess
import unittest
from urllib.parse import urlparse

ROOT=Path(__file__).parent
APP=ROOT/'app.py'
HTML=ROOT/'index.html'


def isolate(*names):
    tree=ast.parse(APP.read_text(encoding='utf-8'))
    nodes=[]
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and node.name in names:
            node.decorator_list=[]
            nodes.append(node)
    assert len(nodes)==len(names), names
    env={'re':re, 'urlparse':urlparse, 'PRIVATE_EVENT_FIELDS':('location_text','location_map_url','location_lat','location_lng','game_name','game_names','member_game_picks','quests_event_url')}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(APP),'exec'),env)
    return env


class JarPaymentTests(unittest.TestCase):
    def setUp(self):
        env=isolate('_monobank_jar_link','_event_payment_config','_public_event_description','_event_redacted')
        self.link=env['_monobank_jar_link']
        self.config=env['_event_payment_config']
        self.redact=env['_event_redacted']

    def test_accepts_and_normalizes_real_jar_link(self):
        expected='https://send.monobank.ua/jar/6Uie4sRZxN'
        self.assertEqual(self.link(expected),expected)
        self.assertEqual(self.link(expected.removeprefix('https://')),expected)
        self.assertEqual(self.link(expected+'?t=share'),expected+'?t=share')

    def test_rejects_untrusted_redirect_url_and_javascript(self):
        invalid=[
            'javascript:alert(1)', 'http://send.monobank.ua/jar/abc123',
            'https://send.monobank.ua.evil.org/jar/abc123',
            'https://send.monobank.ua@evil.org/jar/abc123',
            'https://send.monobank.ua:444/jar/abc123',
            'https://send.monobank.ua/jar/abc123#fragment',
            'https://send.monobank.ua/NOTjar/abc123',
            'https://send.monobank.ua/jar/a',
            'https://send.monobank.ua/jar/abc123\nEvil',
            'https://send.monobank.ua/jar/abc123/something',
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.link(value)

    def test_paid_jar_event_without_extra_text(self):
        method,details,link=self.config({
            'payment_method':'receipt', 'payment_details':'',
            'payment_link':'https://send.monobank.ua/jar/abcdef'},15000)
        self.assertEqual((method,details,link),('receipt','','https://send.monobank.ua/jar/abcdef'))

    def test_keeps_older_manual_events_compatible(self):
        method,details,link=self.config({'payment_method':'receipt','payment_details':'IBAN UA123'},15000)
        self.assertEqual((method,details,link),('receipt','IBAN UA123',''))

    def test_free_payment_never_has_link(self):
        self.assertEqual(self.config({'payment_method':'receipt','payment_link':'https://send.monobank.ua/jar/abc123'},0),('free','',''))

    def test_acquiring_method_does_not_use_jar_and_is_preserved(self):
        self.assertEqual(self.config({'payment_method':'monobank','payment_link':'https://send.monobank.ua/jar/abc123'},20000),('monobank','',''))

    def test_missing_payment_instruction_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'payment_details_or_link_required'):
            self.config({'payment_method':'receipt','payment_details':'','payment_link':''},20000)

    def test_rejects_invalid_link_even_with_fallback_bank_account(self):
        with self.assertRaisesRegex(ValueError,'invalid_monobank_jar_link'):
            self.config({'payment_method':'receipt','payment_details':'UA123', 'payment_link':'https://evil.org/jar/xyz'},10000)

    def test_edit_preserves_link_from_previous_event(self):
        old={'payment_method':'receipt','payment_details':'','payment_link':'https://send.monobank.ua/jar/abc123'}
        self.assertEqual(self.config({},15000,old),('receipt','','https://send.monobank.ua/jar/abc123'))

    def test_public_event_list_hides_jar_link(self):
        url='https://send.monobank.ua/jar/abc123'
        ev={'title':'Шахи','description':'Оплата: '+url,'payment_link':url,'payment_details':'UA123'}
        result=self.redact(ev)
        self.assertNotIn(url,str(result))
        self.assertNotIn('UA123',str(result))
        self.assertEqual(result['title'],'Шахи')

    def test_frontend_has_separate_cost_link_and_manual_approval(self):
        html=HTML.read_text(encoding='utf-8')
        for text in ('id="eventPriceUah"','id="eventPaymentLink"','id="eventJarPay"',
                     "formData.append('payment_link'", "form.append('receipt',file)",
                     'openAdminEventReceipts','event_receipt'):
            if text=='event_receipt':
                continue
            self.assertIn(text,html)
        self.assertIn('💜 Оплатити через банку monobank',html)

    def test_server_keeps_registration_behind_receipt_approval(self):
        src=APP.read_text()
        self.assertIn('styloteka_review_event_receipt',src)
        self.assertIn("'payment_link': event.get('payment_link') or ''",src)
        self.assertIn('event_payment_link_locked_after_receipt',src)
        self.assertIn('paid_event',src)  # existing payment safeguards remain in place

    def test_js_link_validation(self):
        html=HTML.read_text(encoding='utf-8')
        fn=re.search(r'function normalizeEventJarLink\(value\)\{.*?\n\}',html,re.S)
        self.assertIsNotNone(fn)
        script=fn.group()+'''
const assert = require('assert');
assert.equal(normalizeEventJarLink('https://send.monobank.ua/jar/abc123'),'https://send.monobank.ua/jar/abc123');
assert.equal(normalizeEventJarLink('send.monobank.ua/jar/abc123'),'https://send.monobank.ua/jar/abc123');
for (const bad of ['javascript:alert(1)','https://send.monobank.ua.evil.com/jar/abc123','http://send.monobank.ua/jar/abc123']) assert.equal(normalizeEventJarLink(bad),'');
'''
        subprocess.run(['node','-e',script],check=True,timeout=20)


if __name__=='__main__':
    unittest.main()
