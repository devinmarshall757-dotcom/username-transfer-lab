"""Authenticated loopback product API over a durable local fixture. No live adapters."""
import argparse
import hmac
import json
import re
import secrets
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from durable_transfer import Coordinator, PersistentFakePlatform, WorkerBusy

IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{3,64}\Z')


class ProductServer(ThreadingHTTPServer):
    def __init__(self, address, directory, token=None):
        if address[0] != '127.0.0.1':
            raise ValueError('This development API must bind to loopback')
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        key = self.directory/'api.key'
        if token is None:
            if not key.exists():
                key.write_text(secrets.token_urlsafe(32), encoding='utf-8')
            token = key.read_text(encoding='utf-8').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{24,128}', token):
            raise ValueError('API key must be 24–128 URL-safe characters')
        self.token = token
        self.coordinator_path = self.directory/'coordinator.sqlite'
        self.platform_path = self.directory/'platform.sqlite'
        coordinator = Coordinator(self.coordinator_path, require_evidence=True)
        coordinator.close()
        super().__init__(address, ProductHandler)


class ProductHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
            pass

    def trusted_request(self):
        host = f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host') != host:
            self.send({'error':'invalid_host'},403)
            return False
        origin = self.headers.get('Origin')
        if origin is not None and origin != 'http://'+host:
            self.send({'error':'cross_origin_denied'},403)
            return False
        return True

    def authenticated(self):
        authorization = self.headers.get('Authorization','')
        supplied = authorization[7:] if authorization.startswith('Bearer ') else ''
        if not supplied:
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get('Cookie',''))
                supplied = cookie['lab_session'].value if 'lab_session' in cookie else ''
            except Exception:
                supplied = ''
        if not hmac.compare_digest(supplied.encode(), self.server.token.encode()):
            self.send({'error':'authentication_required'},401)
            return False
        return True

    def do_GET(self):
        if not self.trusted_request():
            return
        path = urlsplit(self.path).path
        if path == '/':
            data = Path(__file__).with_name('product.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == '/health':
            self.send({'status':'ok','adapter':'persistent-local-fixture','live_platform':False,'payments':False})
            return
        if not self.authenticated():
            return
        c = Coordinator(self.server.coordinator_path, require_evidence=True)
        try:
            if path == '/v1/transactions':
                ids = [r[0] for r in c.db.execute('SELECT id FROM transfers ORDER BY rowid DESC LIMIT 100')]
                self.send({'transactions':[c.get(identifier) for identifier in ids],'verification':'Historical observations, not settlement authorization'})
            elif path.startswith('/v1/transactions/') and IDENTIFIER.fullmatch(path.rsplit('/',1)[-1]):
                self.send(c.get(path.rsplit('/',1)[-1]))
            else:
                self.send({'error':'not_found'},404)
        except KeyError:
            self.send({'error':'not_found'},404)
        finally:
            c.close()

    def do_POST(self):
        if not self.trusted_request() or not self.authenticated():
            return
        path = urlsplit(self.path).path
        if path == '/session':
            self.send_response(204)
            self.send_header('Set-Cookie',f'lab_session={self.server.token}; HttpOnly; SameSite=Strict; Path=/')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length','0')
            self.end_headers()
            return
        if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
            self.send({'error':'json_required'},415)
            return
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0 < size <= 4096:
                self.send({'error':'invalid_body_size'},413)
                return
            body = json.loads(self.rfile.read(size))
            if not isinstance(body,dict):
                raise ValueError('JSON object required')
        except (ValueError,json.JSONDecodeError):
            self.send({'error':'invalid_json'},400)
            return
        c = Coordinator(self.server.coordinator_path, require_evidence=True)
        platform = None
        try:
            if path == '/v1/transactions':
                if set(body) != {'id','username','seller','buyer'} or not all(isinstance(v,str) and IDENTIFIER.fullmatch(v) for v in body.values()):
                    self.send({'error':'Provide id, username, seller, buyer: 3â€“64 letters, digits, underscores or hyphens'},400)
                    return
                exists = c.db.execute('SELECT 1 FROM transfers WHERE id=?',(body['id'],)).fetchone() is not None
                self.send(c.create(body['id'],body['username'],body['seller'],body['buyer']),200 if exists else 201)
            elif path.startswith('/v1/transactions/') and path.endswith('/execute'):
                identifier = path.split('/')[-2]
                if not IDENTIFIER.fullmatch(identifier) or body:
                    self.send({'error':'Invalid transaction or execution body'},400)
                    return
                record = c.get(identifier)
                platform = PersistentFakePlatform(self.server.platform_path,username=record['username'],initial_owner=record['seller'])
                self.send(c.run(identifier,platform))
            else:
                self.send({'error':'not_found'},404)
        except KeyError:
            self.send({'error':'not_found'},404)
        except WorkerBusy:
            self.send({'error':'worker_busy','retryable':True},409)
        except ValueError as exc:
            self.send({'error':str(exc)},409)
        finally:
            if platform is not None:
                platform.close()
            c.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--data',default='artifacts/product-api')
    args=parser.parse_args()
    server=ProductServer(('127.0.0.1',args.port),args.data)
    print(f'Local product: http://127.0.0.1:{server.server_port}/',flush=True)
    print(f'Access key file: {server.directory / "api.key"}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__=='__main__':
    main()
