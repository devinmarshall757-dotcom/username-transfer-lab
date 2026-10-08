"""Automated browser runner + read-only watcher, with a live local progress page."""
import argparse
import json
import math
import threading
import time
from collections import Counter
from pathlib import Path

from browser_lab import LabServer, Handler
from durable_transfer import Coordinator
from lab_watcher import inspect_run
from run_browser_lab import BrowserPlatform, request


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)]


def grouped_report(results):
    groups = {}
    for result in results:
        key = (result['strategy'], result['competitors'])
        groups.setdefault(key, []).append(result)
    rows = []
    for (strategy, competitors), values in sorted(groups.items()):
        counts = Counter(v['outcome'] for v in values)
        # Successful-buyer exposure is separate from any-winner exposure.
        buyer = [v['watcher']['exposure_ms'] for v in values if v['outcome'] == 'verified' and v.get('watcher') and v['watcher']['exposure_ms'] is not None]
        any_winner = [v['watcher']['exposure_ms'] for v in values if v.get('watcher') and v['watcher']['exposure_ms'] is not None]
        rows.append({'strategy': strategy, 'competitors': competitors, 'attempts': len(values),
                     'outcomes': dict(counts), 'verified_rate': counts['verified'] / len(values),
                     'buyer_exposure_samples': len(buyer), 'buyer_exposure_median_ms': percentile(buyer, .5),
                     'buyer_exposure_p95_ms': percentile(buyer, .95),
                     'any_winner_exposure_median_ms': percentile(any_winner, .5),
                     'requests': sum(v.get('watcher', {}).get('requests', 0) for v in values),
                     'rate_limits': sum(v.get('watcher', {}).get('rate_limits', 0) for v in values),
                     'critical_findings': sum(f['severity'] == 'critical' for v in values for f in v.get('watcher', {}).get('findings', []))})
    return rows


class BatchHandler(Handler):
    def do_GET(self):
        if self.path.split('?')[0] == '/batch':
            data = Path(__file__).with_name('batch.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == '/progress':
            with self.server.progress_lock:
                self.send(self.server.progress)
        else:
            super().do_GET()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=int, default=100, help='Runs for each of nine strategy/competition groups')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--output', default='artifacts/batch-900')
    parser.add_argument('--channel', default='msedge')
    parser.add_argument('--keep-open', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Continue a saved ledger without replaying completed attempts')
    args = parser.parse_args()
    if not 1 <= args.runs <= 1000:
        parser.error('runs must be between 1 and 1000')
    from playwright.sync_api import sync_playwright
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    # Prevent accidentally replacing an existing benchmark's evidence.
    ledger = out / 'results.jsonl'
    if ledger.exists() and not args.resume:
        parser.error('Output already contains results; choose a new --output folder')
    results = []
    previous_elapsed = 0
    if args.resume:
        if not ledger.exists():
            parser.error('No saved ledger to resume')
        results = [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines() if line.strip()]
        previous = json.loads((out / 'report.json').read_text(encoding='utf-8'))
        if previous['planned'] != args.runs * 9:
            parser.error('Resume must preserve the original run count')
        previous_elapsed = previous.get('elapsed_seconds', 0)
    completed_keys = {(r['seed'], r['strategy'], r['competitors']) for r in results}
    if len(completed_keys) != len(results) or any(not 1000 <= seed < 1000 + args.runs or strategy not in ('confirmed','scheduled','polling') or competitors not in (0,1,3) for seed,strategy,competitors in completed_keys):
        parser.error('Ledger contains duplicate or incompatible experiment keys')
    server = LabServer(('127.0.0.1', args.port))
    server.RequestHandlerClass = BatchHandler
    server.progress_lock = threading.Lock()
    server.progress = {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    start = time.perf_counter()
    status = 'running'
    current = None

    def checkpoint():
        report = {'scope': 'Offline browser benchmark only; not FOMO performance or a security audit',
                  'status': status, 'planned': args.runs * 9, 'completed': len(results),
                  'elapsed_seconds': previous_elapsed + time.perf_counter() - start, 'current': current,
                  'resumed': args.resume,
                  'seed_start': 1000, 'timing': {'offset_ms': 40, 'poll_ms': 25, 'seller_error_ms': 0},
                  'groups': grouped_report(results), 'runner_errors': sum('error' in r for r in results),
                  'reproducibility': 'Seeded model delays; actual OS/browser scheduling varies'}
        temp = out / 'report.tmp'
        temp.write_text(json.dumps(report, indent=2), encoding='utf-8')
        # Windows readers may briefly deny delete-sharing during replacement.
        # Retry without discarding the append-only ledger or rerunning a race.
        for attempt in range(40):
            try:
                temp.replace(out / 'report.json')
                break
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(.05)
        with server.progress_lock:
            server.progress = report

    checkpoint()
    print(f'Live batch: {base}/batch', flush=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=args.channel or None, headless=True)
            contexts = [browser.new_context() for _ in range(2)]
            pages = [c.new_page() for c in contexts]
            # Keep reporting if one attempt errors, but stop if the browser dies.
            try:
                with ledger.open('a', encoding='utf-8') as log:
                    # Interleave strategies and competition groups to reduce
                    # confounding from changes in machine load over the batch.
                    for index in range(args.runs):
                        for competitors in (0, 1, 3):
                            for strategy in ('confirmed', 'scheduled', 'polling'):
                                if (1000 + index, strategy, competitors) in completed_keys:
                                    continue
                                current = {'strategy': strategy, 'competitors': competitors, 'seed': 1000 + index}
                                checkpoint()
                                config = {'seed': 1000 + index, 'competitor_count': competitors}
                                run = request(base, '/runs', config)
                                result = {**current, 'id': run['id'], 'config': run['config']}
                                coordinator = Coordinator(out / 'transactions.sqlite')
                                try:
                                    for page, actor in zip(pages, (run['seller'], run['buyer'])):
                                        page.goto(f'{base}/?run={run["id"]}&actor={actor}')
                                        page.wait_for_function('window.ready===true')
                                    coordinator.create(run['id'], run['username'], run['seller'], run['buyer'])
                                    request(base, f'/runs/{run["id"]}/label', {'strategy': strategy})
                                    request(base, f'/runs/{run["id"]}/arm', {})
                                    adapter = BrowserPlatform(base, run, *pages, strategy, 40, 25, 0)
                                    record = coordinator.run(run['id'], adapter)
                                    adapter.drain()
                                    snapshot = request(base, f'/runs/{run["id"]}/finish', {})
                                    observation = inspect_run(snapshot)
                                    if record['state'] != observation['outcome']:
                                        raise RuntimeError('Coordinator/watcher outcome mismatch')
                                    result.update(outcome=record['state'], watcher=observation, transaction=record,
                                                  server_events=snapshot['events'])
                                except Exception as exc:
                                    snapshot = request(base, f'/runs/{run["id"]}/finish', {})
                                    result.update(outcome='unresolved', error=str(exc), watcher=inspect_run(snapshot),
                                                  server_events=snapshot['events'])
                                finally:
                                    coordinator.close()
                                    request(base, f'/runs/{run["id"]}/finish', {})
                                log.write(json.dumps(result) + '\n')
                                log.flush()
                                results.append(result)
                                # Snapshots are saved to the ledger; retire the
                                # live registry entry so page polling stays cheap.
                                with server.registry_lock:
                                    server.runs.pop(run['id'], None)
                                checkpoint()
                                if len(results) % 25 == 0:
                                    print(f'{len(results)}/{args.runs*9} completed', flush=True)
                                if not browser.is_connected():
                                    raise RuntimeError('Browser disconnected')
            finally:
                for context in contexts:
                    context.close()
                browser.close()
        status = 'complete'
    except BaseException:
        status = 'interrupted'
        raise
    finally:
        current = None
        checkpoint()
        print(f'{status}: {len(results)}/{args.runs*9}. Report: {out.resolve() / "report.json"}', flush=True)
        if args.keep_open and status == 'complete':
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        server.shutdown()
        server.server_close()
        thread.join(5)


if __name__ == '__main__':
    main()
