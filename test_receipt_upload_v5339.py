"""No live participant files or database writes. Exercises the actual app.py functions."""
import ast,io,json,re,logging,unittest
from pathlib import Path
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import Mock

APP=Path(__file__).with_name('app.py')

class UploadedFile:
    def __init__(self,raw,mimetype='application/pdf'):
        self.stream=io.BytesIO(raw)
        self.mimetype=mimetype

class MockRequest:
    def __init__(self,raw):
        self.files={'receipt':UploadedFile(raw)} if raw is not None else {}

def isolate(names,inject=None):
    tree=ast.parse(APP.read_text(encoding='utf8'))
    nodes=[]
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list=[]; nodes.append(node)
    assert len(nodes)==len(names)
    env={'re':re,'logging':logging,'datetime':datetime,'timezone':timezone,'jsonify':lambda value:value,
         'RECEIPT_MAX_BYTES':5*1024*1024}
    if inject:env.update(inject)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(APP),'exec'),env)
    return env

class Response:
    def __init__(self,val):self.val=val
    def json(self):return self.val
    def raise_for_status(self):pass

class ReceiptTests(unittest.TestCase):
    def setUp(self):self.detect=isolate(['_receipt_file_type'])['_receipt_file_type']

    def test_banking_pdf_with_bom_and_preamble(self):
        for prefix in (b'',b'\xef\xbb\xbf',b' \r\n\t',b'\x00\x00'+b' '*50, b'x'*1020):
            with self.subTest(prefix=len(prefix)):
                self.assertEqual(self.detect(prefix+b'%PDF-1.7\nabc\n%%EOF'),('application/pdf','pdf'))
        self.assertEqual(self.detect(b' '*1025+b'%PDF-1.7'),(None,None))
        self.assertEqual(self.detect(b'%PDF-2.0\n'),('application/pdf','pdf'))

    def test_recognizes_images_not_filenames(self):
        self.assertEqual(self.detect(b'\xff\xd8\xff\xe0...'),('image/jpeg','jpg'))
        self.assertEqual(self.detect(b'\x89PNG\r\n\x1a\n...'),('image/png','png'))
        self.assertEqual(self.detect(b'RIFF1234WEBPVP8 ...'),('image/webp','webp'))
        self.assertEqual(self.detect(b'\x00\x01\x02JPEG'),(None,None))
        self.assertEqual(self.detect(b'<html>not a real PDF</html>'),(None,None))
        self.assertEqual(self.detect(b'PK\x03\x04not a PDF'),(None,None))

    def run_submit(self,raw):
        posts=[]; calls=[]
        def post(url,**kwargs):
            posts.append((url,kwargs))
            if url.startswith('https://supabase.example/storage/'):
                assert kwargs['headers']['Content-Type']=='application/pdf'
                return Response({'Key':kwargs['data']})
            return Response([{'id':52}])
        inject={'_request_telegram_user':lambda:{'id':123,'username':'hi'},
                '_payment_identity':lambda user:(123,'member','Member'),
                'request':MockRequest(raw),'get_event_snapshot':lambda eid:{'status':'scheduled','payment_method':'receipt','price_kop':10000},
                '_my_receipt':lambda eid,uid:None,
                '_sync_paid_event_member_profile':lambda *args:None,
                '_receipt_file_type':self.detect,'RECEIPT_MAX_BYTES':5*1024*1024,
                'STORAGE':'https://supabase.example/storage/v1/object','RECEIPT_BUCKET':'event-receipts',
                'EVENT_RECEIPTS_REST':'https://supabase.example/rest/v1/event_payment_receipts',
                'HEADERS':{'apikey':'key'},'requests':SimpleNamespace(post=post,RequestException=OSError),
                'uuid':SimpleNamespace(uuid4=lambda:SimpleNamespace(hex='abc123')),
                'jsonify':lambda data:data,'logging':logging}
        env=isolate(['submit_event_receipt'],inject)
        result=env['submit_event_receipt'](20)
        return result,posts

    def test_server_saves_android_pdf_in_private_bucket_and_db(self):
        pdf=b'\xef\xbb\xbf'+b'\n'+b'%PDF-1.7\n'+b'0 0 obj\ntrailer\n%%EOF'
        result,posts=self.run_submit(pdf)
        self.assertEqual(result[1],201)
        self.assertEqual(result[0],{'status':'pending','receipt_id':52})
        self.assertEqual(len(posts),2)
        self.assertIn('event-receipts/20/123/abc123.pdf',posts[0][0])
        self.assertEqual(posts[1][1]['json']['receipt_mime'],'application/pdf')
        self.assertEqual(posts[1][1]['json']['status'],'pending')

    def test_malformed_pdf_is_rejected_without_storage_write(self):
        result,posts=self.run_submit(b'not actually a PDF')
        self.assertEqual(result[1],400)
        self.assertEqual(result[0]['error'],'receipt_invalid_format')
        self.assertEqual(posts,[])

    def test_zero_length_and_oversized(self):
        for raw in (b'', b'%PDF-1.7'+b'x'*(5*1024*1024)):
            with self.subTest(size=len(raw)):
                result,posts=self.run_submit(raw)
                self.assertEqual(result[1],400)
                self.assertEqual(result[0]['error'],'receipt_too_large_or_empty')
                self.assertFalse(posts)

if __name__=='__main__':unittest.main(verbosity=2)
