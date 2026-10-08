"""Direct local-server race stress; no browser overhead or external endpoints."""
import argparse
import json
import html
import random
import statistics
from dataclasses import replace
import threading
import time
from collections import Counter
from pathlib import Path
from browser_lab import Config, Run
from lab_watcher import inspect_run
from experiment_director import audit_record

SCENARIOS = {
    'fast_competitors': dict(network_ms=15, processing_ms=5, jitter_ms=10, competitor_interval_ms=10),
    'high_latency_fast_competitors': dict(network_ms=75, processing_ms=10, jitter_ms=40, competitor_interval_ms=10),
    'uncertain_release': dict(network_ms=30, processing_ms=10, jitter_ms=50, competitor_interval_ms=20),
}
POLICIES = {'single10': (10, 1), 'retry10': (10, 3), 'single40': (40, 1), 'adaptive_retry': (None, 3)}

def choose_offset(seller_rtt, buyer_rtt):
    # Frozen before holdout; observes only measured preflight round trips.
    estimate = (seller_rtt - buyer_rtt) / 2 + .15 * statistics.median([seller_rtt, buyer_rtt])
    return max(10, min(60, round(estimate)))


def trial(seed, scenario, competitors, policy, calibrate=False):
    offset, budget = POLICIES[policy]
    run = Run(Config(seed=seed, competitor_count=competitors, **SCENARIOS[scenario]))
    calibration = {}
    if calibrate or policy == 'adaptive_retry':
        for actor in (run.seller, run.buyer):
            start = time.perf_counter()
            status, _ = run.request('availability', actor)
            if status != 200:
                raise RuntimeError('Calibration failed')
            calibration[actor] = (time.perf_counter() - start) * 1000
        if policy == 'adaptive_retry':
            offset = choose_offset(calibration[run.seller], calibration[run.buyer])
        if scenario == 'latency_shift':
            run.config = replace(run.config, network_ms=100, jitter_ms=60)
    run.strategy = 'scheduled_retry' if budget > 1 else 'scheduled'
    error = random.Random(seed).uniform(-30, 30) if scenario == 'uncertain_release' else 0
    epoch = time.perf_counter() + .05
    errors = []
    trace = []
    def seller():
        try:
            time.sleep(max(0, epoch + error / 1000 - time.perf_counter()))
            run.request('release', run.seller, run.replacement)
        except Exception as exc:
            errors.append(repr(exc))
    run.arm()
    worker = threading.Thread(target=seller)
    worker.start()
    try:
        time.sleep(max(0, epoch + offset / 1000 - time.perf_counter()))
        deadline = time.perf_counter() + .5
        for attempt in range(budget):
            if time.perf_counter() >= deadline:
                break
            owner = run.snapshot()['owner']
            if owner not in (None, run.seller):
                break
            sent = time.perf_counter()
            status, response = run.request('claim', run.buyer, run.username)
            trace.append({'attempt': attempt + 1, 'status': status, 'response': response})
            if response.get('accepted'):
                break
            wait = max(0, run.config.min_interval_ms / 1000 - (time.perf_counter() - sent))
            if status == 429:
                wait = max(wait, response['retry_ms'] / 1000)
            if time.perf_counter() + wait >= deadline:
                break
            time.sleep(wait)
    finally:
        worker.join()
        run.finish()
    snapshot = run.snapshot()
    watcher = inspect_run(snapshot)
    row = {'id': run.id, 'seed': seed, 'scenario': scenario, 'competitors': competitors, 'policy': policy, 'strategy': run.strategy, 'seller_error_ms': error, 'outcome': watcher['outcome'], 'watcher': watcher, 'server_events': snapshot['events'], 'retry_trace': trace, 'runner_errors': errors, 'selected_offset_ms': offset, 'calibration_rtt_ms': calibration}
    row['independent_findings'] = audit_record(row)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--adaptive-study', action='store_true')
    parser.add_argument('--runs', type=int, default=20)
    parser.add_argument('--seed-start', type=int, default=120000)
    parser.add_argument('--output', default='artifacts/adversarial-races')
    args = parser.parse_args()
    if args.runs < 1:
        parser.error('--runs must be positive')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    ledger = output / 'results.jsonl'
    if ledger.exists():
        parser.error('Use a fresh output directory to preserve prior evidence')
    scenarios = list(SCENARIOS)
    policies = ['single10', 'retry10', 'single40']
    competitor_counts = [0, 1, 3]
    if args.adaptive_study:
        SCENARIOS['latency_shift'] = dict(SCENARIOS['fast_competitors'])
        scenarios.append('latency_shift')
        policies = ['retry10', 'single40', 'adaptive_retry']
        competitor_counts = [0, 3]
    groups = {}
    failures = critical = audited = 0
    count = 0
    plan = {'scope': 'Direct threaded local-server model; differs from browser benchmarks; not FOMO measurements.', 'seed_start': args.seed_start, 'runs_per_cell': args.runs, 'scenarios': {s: SCENARIOS[s] for s in scenarios}, 'policies': {p: POLICIES[p] for p in policies}, 'competitors': competitor_counts, 'planned': args.runs * len(scenarios) * len(policies) * len(competitor_counts), 'calibration': 'One availability RTT per actor for every policy' if args.adaptive_study else 'none', 'adaptive_rule': 'clamp(round((seller_rtt-buyer_rtt)/2 + 0.15*median_rtt),10,60); max 3 claims in 500 ms', 'latency_shift': 'After calibration, network=100 ms and jitter=60 ms; hidden from offset selection'}
    (output / 'plan.json').write_text(json.dumps(plan, indent=2))
    with ledger.open('w', encoding='utf-8') as stream:
        for index in range(args.runs):
            cells = [(s, c, p) for s in scenarios for c in competitor_counts for p in policies]
            random.Random(args.seed_start + index).shuffle(cells)
            for scenario, competitors, policy in cells:
                row = trial(args.seed_start + index, scenario, competitors, policy, calibrate=args.adaptive_study)
                stream.write(json.dumps(row) + '\n')
                stream.flush()
                key = (scenario, competitors, policy)
                groups.setdefault(key, Counter())[row['outcome']] += 1
                failures += len(row['runner_errors'])
                critical += sum(f['severity'] == 'critical' for f in row['watcher']['findings'])
                audited += len(row['independent_findings'])
                count += 1
            print(f'{count}/{plan["planned"]} complete', flush=True)
    report = dict(plan, status='complete', completed=count, runner_errors=failures, critical_findings=critical, independent_findings=audited, groups=[{'scenario': s, 'competitors': c, 'policy': p, 'attempts': sum(outcomes.values()), 'outcomes': dict(outcomes), 'verified_rate': outcomes['verified']/sum(outcomes.values())} for (s,c,p),outcomes in sorted(groups.items())])
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    rows = ''.join('<tr><td>'+html.escape(g['scenario'])+'</td><td>'+str(g['competitors'])+'</td><td>'+html.escape(g['policy'])+'</td><td>'+str(g['attempts'])+'</td><td>'+format(g['verified_rate'], '.0%')+'</td><td>'+html.escape(str(g['outcomes']))+'</td></tr>' for g in report['groups'])
    (output / 'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Local policy study</title><style>body{background:#101820;color:#dceee9;font:16px system-ui;margin:32px}table{border-collapse:collapse}td,th{padding:12px;text-align:left;border-bottom:1px solid #375047}h1{color:#74e9c3}</style><h1>'+str(count)+' local policy trials</h1><p>Direct server model, excludes browser overhead. Small holdout samples; not FOMO measurements.</p><p>Runner errors: '+str(failures)+'; critical watcher findings: '+str(critical)+'; independent audit findings: '+str(audited)+'</p><table><tr><th>Condition</th><th>Competitors</th><th>Policy</th><th>Attempts</th><th>Verified</th><th>Outcomes</th></tr>'+rows+'</table>', encoding='utf-8')
    print(json.dumps({'completed': count, 'runner_errors': failures, 'critical_findings': critical, 'independent_findings': audited}))

if __name__ == '__main__':
    main()
