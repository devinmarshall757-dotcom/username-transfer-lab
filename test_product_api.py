import json
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from product_api import ProductServer

class ProductApiTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.server=ProductServer(('127.0.0.1',0),self.directory.name,token='fixture-key-for-local-tests-only')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(5)
        self.directory.cleanup()
    def call(self,path,body=None,auth=True,extra=None):
        headers={'Content-Type':'application/json'}
        if auth:headers['Authorization']='Bearer '+self.server.token
        headers.update(extra or {})
        request=Request(self.base+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
        try:
            with urlopen(request,timeout=5) as response:
                data=response.read()
                return response.status,json.loads(data) if data else {},response.headers
        except HTTPError as exc:
            return exc.code,json.load(exc),exc.headers
    def details(self):
        return dict(id='transaction-123',username='target-test',seller='alice',buyer='bob')
    def test_authentication_and_cross_origin(self):
        self.assertEqual(self.call('/v1/transactions',auth=False)[0],401)
        self.assertEqual(self.call('/v1/transactions',extra={'Authorization':'Bearer wrong'})[0],401)
        self.assertEqual(self.call('/v1/transactions',extra={'Origin':'https://other.example'})[0],403)
        self.assertEqual(self.call('/health',auth=False)[1]['live_platform'],False)
    def test_idempotent_create_and_immutable_identity(self):
        details=self.details()
        self.assertEqual(self.call('/v1/transactions',details)[0],201)
        self.assertEqual(self.call('/v1/transactions',details)[0],200)
        self.assertEqual(self.call('/v1/transactions',dict(details,buyer='charlie'))[0],409)
    def test_execute_and_recheck_strict_fixture_ownership(self):
        self.call('/v1/transactions',self.details())
        result=self.call('/v1/transactions/transaction-123/execute',{})[1]
        self.assertEqual(result['state'],'verified')
        again=self.call('/v1/transactions/transaction-123/execute',{})[1]
        self.assertEqual(sum(e['state']=='release_intent' for e in again['log']),1)
        self.assertEqual(len(self.call('/v1/transactions')[1]['transactions']),1)
    def test_different_transaction_cannot_reuse_transferred_resource(self):
        self.call('/v1/transactions',self.details())
        self.call('/v1/transactions/transaction-123/execute',{})
        self.call('/v1/transactions',dict(self.details(),id='transaction-456'))
        self.assertEqual(self.call('/v1/transactions/transaction-456/execute',{})[0],409)
    def test_cookie_session_and_no_token_in_response(self):
        status,body,headers=self.call('/session',{})
        self.assertEqual(status,204)
        self.assertNotIn(self.server.token,json.dumps(body))
        cookie=headers['Set-Cookie'].split(';')[0]
        self.assertIn('HttpOnly',headers['Set-Cookie'])
        self.assertEqual(self.call('/v1/transactions',auth=False,extra={'Cookie':cookie})[0],200)
    def test_invalid_input_and_missing_resource(self):
        self.assertEqual(self.call('/v1/transactions',dict(self.details(),id='../escape'))[0],400)
        self.assertEqual(self.call('/v1/transactions/missing')[0],404)
    def test_record_survives_connection_recreation(self):
        self.call('/v1/transactions',self.details())
        other=ProductServer(('127.0.0.1',0),self.directory.name,token=self.server.token)
        thread=threading.Thread(target=other.serve_forever,daemon=True);thread.start()
        try:
            request=Request(f'http://127.0.0.1:{other.server_port}/v1/transactions/transaction-123',headers={'Authorization':'Bearer '+self.server.token})
            with urlopen(request,timeout=5) as response:
                self.assertEqual(json.load(response)['username'],'target-test')
        finally:
            other.shutdown();other.server_close();thread.join(5)
