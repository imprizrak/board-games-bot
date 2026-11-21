"""Route regression tests without contacting Render/Supabase or processing payments."""
import ast, unittest, logging
from pathlib import Path

SOURCE=Path(__file__).with_name('app.py').read_text('utf-8')

def build(event, token='', user=None):
    tree=ast.parse(SOURCE)
    matches=[x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='start_event_payment']
    assert len(matches)==1
    fn=matches[0];fn.decorator_list=[]
    touched=[]
    env={
       '_request_telegram_user':lambda:{'id':123,'username':'member'} if user is None else user,
       'MONOBANK_ACQUIRING_TOKEN':token,
       'get_event_snapshot':lambda eid: event,
       'jsonify':lambda x:x,
       '_payment_identity':lambda x:touched.append('payment_identity') or (123,'member','Member'),
       '_sync_paid_event_member_profile':lambda *a:touched.append('profile_write'),
       'logging':logging,
       'requests':type('MockRequests',(),{'RequestException':OSError}),
    }
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'app.py','exec'),env)
    result=env['start_event_payment'](21)
    return result,touched

class PaymentRoutingTest(unittest.TestCase):
    def test_jar_receipt_event_never_requires_acquiring_token(self):
        value,writes=build({'id':21,'status':'scheduled','price_kop':10000,
                            'payment_method':'receipt','payment_link':'https://send.monobank.ua/jar/example'},token='')
        self.assertEqual(value,({'error':'receipt_payment_required','payment_method':'receipt'},409))
        self.assertEqual(writes,[])
    def test_real_monobank_event_still_requires_acquiring_setup(self):
        value,writes=build({'id':22,'status':'scheduled','price_kop':10000,'payment_method':'monobank'},token='')
        self.assertEqual(value,({'error':'monobank_not_configured'},503))
        self.assertEqual(writes,[])
    def test_closed_event_is_not_misdiagnosed_as_token_error(self):
        value,writes=build({'id':21,'status':'completed','price_kop':10000,'payment_method':'receipt'},token='')
        self.assertEqual(value,({'error':'event_closed'},409))
        self.assertEqual(writes,[])
    def test_free_event_is_not_misdiagnosed_as_token_error(self):
        value,writes=build({'id':22,'status':'scheduled','price_kop':0,'payment_method':'free'},token='')
        self.assertEqual(value,({'error':'free_event'},409))
        self.assertEqual(writes,[])
    def test_missing_event_is_404(self):
        value,writes=build(None,token='')
        self.assertEqual(value,({'error':'event_not_found'},404))
        self.assertEqual(writes,[])
    def test_unknown_paid_mode_does_not_trigger_acquiring(self):
        value,writes=build({'id':21,'status':'scheduled','price_kop':10000,'payment_method':None},token='')
        self.assertEqual(value,({'error':'invalid_event_payment_method'},409))
        self.assertEqual(writes,[])

if __name__=='__main__':unittest.main()
