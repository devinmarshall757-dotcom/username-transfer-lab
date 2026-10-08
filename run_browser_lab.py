"""Measure local browser-driven transfers; requires Playwright and Chromium/Edge."""
import argparse
import json
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

from browser_lab import LabServer
from durable_transfer import Coordinator


def request(base, path, data=None):
    req = Request(base + path, data=None if data is None else json.dumps(data).encode(),
                  headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=15) as response:
        return json.load(response)


class BrowserPlatform:
    def __init__(self, base, run, seller, buyer, strategy, offset, poll, seller_error):
        self.base, self.run, self.seller, self.buyer = base, run, seller, buyer
        self.username = run['username']
        self.resource_key = f'local-browser-lab:{run["id"]}'
        self.strategy, self.offset, self.poll, self.seller_error = strategy, offset, poll, seller_error
        self.started = False
        self.claim_finished = False

    def verify(self):
        if self.started:
            self.drain()
            if self.strategy != 'confirmed' or self.claim_finished:
                # Stop future competitor attempts and drain already received
                # requests before deciding the terminal owner. Confirmed mode
                # must keep competitors running until its buyer attempt ends.
                return True, request(self.base, f'/runs/{self.run["id"]}/finish', {})['owner']
        return True, request(self.base, '/runs/' + self.run['id'])['owner']

    def release(self, seller):
        self.started = True
        target = time.time() * 1000 + 150
        if self.strategy != 'confirmed':
            self.buyer.evaluate('(x)=>{window.startBuyer(...x)}',
                                [self.strategy, target, self.offset, self.poll, 3000])
        self.seller.evaluate('async x=>{await new Promise(r=>setTimeout(r,Math.max(0,x-Date.now())));document.getElementById("profile").requestSubmit();await window.pendingSubmission}', target + self.seller_error)

    def claim(self, buyer):
        if self.strategy == 'confirmed':
            self.buyer.locator('#submit').click()
            self.buyer.evaluate('async()=>{await window.pendingSubmission}')
            self.claim_finished = True
        else:
            self.buyer.evaluate('async()=>{await window.pendingBuyer}')

    def drain(self):
        if self.strategy != 'confirmed':
            self.buyer.evaluate('async()=>{await window.pendingBuyer}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--channel', default='msedge')
    parser.add_argument('--runs', type=int, default=3, help='Runs per strategy')
    parser.add_argument('--competitors', type=int, default=0)
    parser.add_argument('--offset-ms', type=float, default=40)
    parser.add_argument('--seller-error-ms', type=float, default=0)
    parser.add_argument('--poll-ms', type=float, default=25)
    parser.add_argument('--cooldown-ms', type=float, default=0)
    parser.add_argument('--lost-claim-response', action='store_true')
    parser.add_argument('--output', default='artifacts/browser-lab')
    args = parser.parse_args()
    if not 1 <= args.runs <= 100:
        parser.error('runs must be between 1 and 100')
    from playwright.sync_api import sync_playwright
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    server = LabServer(('127.0.0.1', 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    results = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=args.channel or None, headless=not args.headed)
            try:
                for strategy in ('scheduled', 'confirmed', 'polling'):
                    for index in range(args.runs):
                        config = {'seed': 42 + index, 'competitor_count': args.competitors,
                                  'cooldown_ms': args.cooldown_ms, 'lost_claim_response': args.lost_claim_response}
                        run = request(base, '/runs', config)
                        contexts = [browser.new_context() for _ in range(2)]
                        coordinator = Coordinator(out / 'transactions.sqlite')
                        try:
                            pages = [context.new_page() for context in contexts]
                            for page, actor in zip(pages, (run['seller'], run['buyer'])):
                                page.goto(f'{base}/?run={run["id"]}&actor={actor}')
                                page.wait_for_function('window.ready===true')
                            coordinator.create(run['id'], run['username'], run['seller'], run['buyer'])
                            request(base, f'/runs/{run["id"]}/arm', {})
                            adapter = BrowserPlatform(base, run, *pages, strategy, args.offset_ms, args.poll_ms, args.seller_error_ms)
                            # Existing coordinator owns readiness checks, locks,
                            # intent logging, and ownership-based reconciliation.
                            record = coordinator.run(run['id'], adapter)
                            adapter.drain()
                            snapshot = request(base, f'/runs/{run["id"]}/finish', {})
                            owner = snapshot['owner']
                            outcome = ('verified' if owner == run['buyer'] else 'seller_retained' if owner == run['seller']
                                       else 'unresolved' if owner is None else 'competitor_capture')
                            if record['state'] != outcome:
                                raise RuntimeError('Final observation differs from coordinator outcome')
                            results.append({'strategy': strategy, 'outcome': outcome, 'transaction': record,
                                            'server': snapshot, 'buyer_response': pages[1].evaluate('window.claimResult'),
                                            'browser_observations': [p.evaluate('window.observations') for p in pages]})
                            print(f'{strategy}: {outcome} ({run["id"][:8]})', flush=True)
                            pages[1].evaluate('async()=>{await refresh()}')
                            pages[1].screenshot(path=str(out / 'latest.png'), full_page=True)
                        except Exception as exc:
                            if not any(r['server']['id'] == run['id'] for r in results):
                                snapshot = request(base, f'/runs/{run["id"]}/finish', {})
                                results.append({'strategy': strategy, 'outcome': 'unresolved',
                                                'error': str(exc), 'server': snapshot})
                            raise
                        finally:
                            coordinator.close()
                            for context in contexts:
                                context.close()
                            request(base, f'/runs/{run["id"]}/finish', {})
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        summary = {}
        for result in results:
            counts = summary.setdefault(result['strategy'], {'initiated': 0, 'verified': 0,
                'competitor_capture': 0, 'seller_retained': 0, 'unresolved': 0})
            counts['initiated'] += 1
            counts[result['outcome']] += 1
        report = {'model': 'local real-time browser experiment, not FOMO measurements',
                  'reproducibility': 'Seeds fix delay distributions, not OS/network scheduling or race winners',
                  'arguments': vars(args), 'initiated_runs': len(server.runs), 'recorded_runs': len(results),
                  'summary': summary, 'results': results}
        (out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Report: {out.resolve() / "report.json"}')


if __name__ == '__main__':
    main()
