"""Loopback-only username transfer lab. No external platform integration."""
import argparse
import json
import math
import random
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from lab_watcher import inspect_run, summarize


@dataclass(frozen=True)
class Config:
    target_handle: str = ''
    seed: int = 42
    network_ms: float = 15
    processing_ms: float = 5
    jitter_ms: float = 10
    cooldown_ms: float = 0
    min_interval_ms: float = 20
    competitor_count: int = 0
    competitor_interval_ms: float = 50
    lost_claim_response: bool = False

    def __post_init__(self):
        if not isinstance(self.target_handle, str) or (self.target_handle and not re.fullmatch(r'[A-Za-z0-9_-]{3,32}', self.target_handle)):
            raise ValueError('Handle must be 3–32 letters, digits, underscores or hyphens, without @')
        if type(self.seed) is not int or type(self.competitor_count) is not int:
            raise ValueError('Seed and competitor count must be integers')
        if not 0 <= self.competitor_count <= 20:
            raise ValueError('Competitor count must be between 0 and 20')
        for key, value in asdict(self).items():
            if key.endswith('_ms') and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2000):
                raise ValueError('Durations must be finite and between 0 and 2000 ms')
        if self.competitor_interval_ms < 10:
            raise ValueError('Competitor interval must be at least 10 ms')
        if type(self.lost_claim_response) is not bool:
            raise ValueError('lost_claim_response must be boolean')


class Run:
    def __init__(self, config):
        self.config = config
        self.id = uuid.uuid4().hex
        self.username = config.target_handle or 'target-' + self.id[:8]
        self.seller, self.buyer = 'alice', 'bob'
        self.owner = self.seller
        self.handles = {self.seller: self.username, self.buyer: 'buyer-' + self.id[:8]}
        self.replacement = 'seller-' + self.id[:8]
        if self.username in (self.replacement, self.handles[self.buyer]):
            raise ValueError('Target cannot match a prepared replacement or buyer handle')
        self.lock = threading.RLock()
        self.events = []
        self.start = time.perf_counter()
        self.released_at = self.won_at = None
        self.last_request = {}
        self.request_counts = {}
        self.actor_rng = {}
        self.stop = threading.Event()
        self.armed = False
        self.finished = False
        self.strategy = 'manual'
        self.workers = []

    def now(self):
        return (time.perf_counter() - self.start) * 1000

    def event(self, kind, **fields):
        self.events.append({'time_ms': self.now(), 'kind': kind, **fields})

    def arm(self):
        with self.lock:
            if self.armed:
                raise ValueError('Run is already armed')
            self.armed = True
            self.event('armed')
            for i in range(self.config.competitor_count):
                actor = f'competitor-{i}'
                self.handles[actor] = actor + '-' + self.id[:8]
                worker = threading.Thread(target=self.compete, args=(actor, i), daemon=True)
                self.workers.append(worker)
                worker.start()

    def compete(self, actor, index):
        rng = random.Random(self.config.seed + index)
        self.stop.wait(rng.random() * self.config.competitor_interval_ms / 1000)
        while not self.stop.is_set():
            # Competitors use the same claim path; no release notification or schedule.
            self.request('claim', actor, self.username)
            self.stop.wait(self.config.competitor_interval_ms / 1000)

    def request(self, operation, actor, handle=None):
        with self.lock:
            if actor not in self.handles or operation not in ('release', 'claim', 'availability'):
                raise ValueError('Unknown actor or operation')
            if operation == 'release' and (actor != self.seller or handle != self.replacement):
                raise ValueError('Only the seller can release to its prepared replacement')
            if operation == 'claim' and (actor == self.seller or handle != self.username):
                raise ValueError('Claim must target the run username')
            self.request_counts[actor] = self.request_counts.get(actor, 0) + 1
            self.event('server_received', actor=actor, operation=operation)
            now = self.now()
            if now - self.last_request.get(actor, -float('inf')) < self.config.min_interval_ms:
                self.event('rate_limited', actor=actor, operation=operation)
                return 429, {'error': 'rate_limited', 'retry_ms': self.config.min_interval_ms}
            self.last_request[actor] = now
            rng = self.actor_rng.setdefault(actor, random.Random(str(self.config.seed) + actor))
            delay = self.config.network_ms + self.config.processing_ms + rng.uniform(0, self.config.jitter_ms)
        # Do not hold the registry lock while a request experiences modeled delay.
        time.sleep(delay / 1000)
        with self.lock:
            available = self.owner is None and self.released_at is not None and self.now() >= self.released_at + self.config.cooldown_ms
            if operation == 'availability':
                answer = {'available': available}
            elif operation == 'release':
                accepted = self.owner == self.seller
                if accepted:
                    self.owner = None
                    self.handles[actor] = self.replacement
                    self.released_at = self.now()
                    self.event('released', actor=actor, time_ms=self.released_at)
                answer = {'accepted': accepted}
            else:
                accepted = available
                if accepted:
                    self.owner = actor
                    self.handles[actor] = self.username
                    self.won_at = self.now()
                    self.event('acquired', actor=actor, time_ms=self.won_at)
                    self.stop.set()
                answer = {'accepted': accepted}
            self.event('server_processed', actor=actor, operation=operation, **answer)
        time.sleep(self.config.network_ms / 1000)
        if operation == 'claim' and actor == self.buyer and self.config.lost_claim_response:
            with self.lock:
                self.event('response_lost', actor=actor)
            return 504, {'error': 'response_unknown'}
        return 200, answer

    def snapshot(self):
        with self.lock:
            return {'id': self.id, 'username': self.username, 'seller': self.seller, 'buyer': self.buyer,
                    'finished': self.finished, 'strategy': self.strategy,
                    'replacement': self.replacement, 'owner': self.owner, 'handles': dict(self.handles),
                    'config': asdict(self.config), 'events': list(self.events),
                    'request_counts': dict(self.request_counts),
                    'exposure_ms': None if self.released_at is None or self.won_at is None else self.won_at - self.released_at,
                    'released_at_ms': self.released_at}

    def finish(self):
        self.stop.set()
        for worker in self.workers:
            worker.join(7)
        with self.lock:
            self.finished = True


class LabServer(ThreadingHTTPServer):
    def __init__(self, address=('127.0.0.1', 8765)):
        super().__init__(address, Handler)
        self.runs = {}
        self.registry_lock = threading.Lock()

    def server_close(self):
        for run in list(self.runs.values()):
            run.finish()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == '/watcher':
            with self.server.registry_lock:
                runs = list(self.server.runs.values())
            self.send(summarize([run.snapshot() for run in runs]))
            return
        if self.path.split('?')[0] == '/':
            data = Path(__file__).with_name('lab.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        try:
            run = self.server.runs[self.path.split('/')[2]]
            self.send(run.snapshot())
        except (KeyError, IndexError):
            self.send({'error': 'not_found'}, 404)

    def do_POST(self):
        # No cross-origin writes: the lab uses unauthenticated fixture accounts.
        origin = self.headers.get('Origin')
        expected = f'http://127.0.0.1:{self.server.server_port}'
        if origin and origin != expected:
            self.send({'error': 'origin_rejected'}, 403)
            return
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 16384:
                raise ValueError('Invalid body size')
            body = json.loads(self.rfile.read(size))
            if self.path == '/runs':
                run = Run(Config(**body))
                with self.server.registry_lock:
                    self.server.runs[run.id] = run
                self.send(run.snapshot(), 201)
                return
            _, _, run_id, action = self.path.split('/')
            run = self.server.runs[run_id]
            if action == 'arm':
                run.arm()
                self.send(run.snapshot())
            elif action == 'label':
                strategy = body.get('strategy')
                if strategy not in ('scheduled', 'confirmed', 'polling'):
                    raise ValueError('Unknown strategy')
                with run.lock:
                    if run.released_at is not None or run.finished:
                        raise ValueError('Strategy must be selected before release')
                    run.strategy = strategy
                self.send({'ok': True})
            elif action == 'finish':
                run.finish()
                self.send(run.snapshot())
            elif action == 'browser-event':
                with run.lock:
                    run.event('browser_observation', actor=body['actor'], observation=body['observation'],
                              browser_time_ms=body['browser_time_ms'])
                self.send({'ok': True})
            else:
                status, result = run.request(action, body['actor'], body.get('handle'))
                self.send(result, status)
        except KeyError:
            self.send({'error': 'not_found'}, 404)
        except (ValueError, TypeError) as exc:
            self.send({'error': str(exc)}, 400)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = LabServer(('127.0.0.1', args.port))
    print(f'Offline browser lab: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
