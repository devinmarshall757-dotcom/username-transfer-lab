"""Offline experiment director: independent evidence audit and watcher fault campaign."""
import argparse
import copy
import html
import json
from pathlib import Path
from browser_lab import Config, Run
from lab_watcher import inspect_run


def audit_record(row):
    """Reconstruct terminal ownership from events without using watcher conclusions."""
    events = row.get('server_events', [])
    releases = [e for e in events if e['kind'] == 'released']
    winners = [e for e in events if e['kind'] == 'acquired']
    issues = []
    if len(releases) > 1 or len(winners) > 1:
        issues.append('non_unique_transition')
    if winners and (not releases or winners[0]['time_ms'] < releases[0]['time_ms']):
        issues.append('invalid_event_order')
    if winners and row.get('outcome') == 'verified' and winners[-1]['actor'] != 'bob':
        issues.append('false_verified')
    if row.get('outcome') == 'verified' and not winners:
        issues.append('success_without_acquisition')
    watcher = row.get('watcher', {})
    if winners and watcher.get('owner') != winners[-1]['actor']:
        issues.append('watcher_owner_disagreement')
    if releases and winners:
        expected = winners[-1]['time_ms'] - releases[0]['time_ms']
        actual = watcher.get('exposure_ms')
        if actual is None or abs(actual - expected) > 1:
            issues.append('watcher_timing_disagreement')
    claims = sum(e['kind'] == 'server_received' and e.get('actor') == 'bob' and e.get('operation') == 'claim' for e in events)
    if row.get('strategy') == 'scheduled_retry' and claims > 3:
        issues.append('retry_budget_exceeded')
    return issues


def fault_campaign():
    run = Run(Config(network_ms=0, processing_ms=0, jitter_ms=0, min_interval_ms=0))
    run.request('release', 'alice', run.replacement)
    run.request('claim', 'bob', run.username)
    run.finish()
    baseline = run.snapshot()
    cases = []
    def case(name, expected, mutate):
        snapshot = copy.deepcopy(baseline)
        mutate(snapshot)
        codes = {f['code'] for f in inspect_run(snapshot)['findings']}
        cases.append({'fault': name, 'expected_detection': expected, 'detected': expected in codes, 'observed_codes': sorted(codes)})
    case('duplicate release', 'duplicate_release', lambda s: s['events'].append(copy.deepcopy(next(e for e in s['events'] if e['kind'] == 'released'))))
    case('duplicate winner', 'multiple_winners', lambda s: s['events'].append(copy.deepcopy(next(e for e in s['events'] if e['kind'] == 'acquired'))))
    case('missing release', 'claim_before_release', lambda s: s.update(events=[e for e in s['events'] if e['kind'] != 'released']))
    case('false registry ownership', 'owner_mismatch', lambda s: s.update(owner='intruder'))
    case('missing acquisition', 'unlogged_ownership', lambda s: s.update(events=[e for e in s['events'] if e['kind'] != 'acquired']))
    case('cooldown violation', 'cooldown_bypass', lambda s: s['config'].update(cooldown_ms=100000))
    case('clock regression', 'time_regression', lambda s: s['events'].append({'kind': 'armed', 'time_ms': -1}))
    case('corrupted exposure measurement', 'exposure_mismatch', lambda s: s.update(exposure_ms=999999))
    return {'clean_control_critical_findings': sum(f['severity'] == 'critical' for f in inspect_run(baseline)['findings']), 'cases': cases, 'detected': sum(c['detected'] for c in cases), 'total': len(cases)}


def direct(source):
    audits = []
    count = 0
    scenarios = set()
    with Path(source).open(encoding='utf-8') as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            count += 1
            if row.get('scenario'):
                scenarios.add(row['scenario'])
            issues = audit_record(row)
            if issues:
                audits.append({'id': row.get('id'), 'issues': issues})
    campaign = fault_campaign()
    proposals = [
        {'priority': 1, 'hypothesis': 'The watcher detects deliberately corrupted evidence.', 'experiment': 'Run the injected-fault campaign before accepting benchmark conclusions.', 'pass_condition': 'All planted faults detected; zero critical findings on clean control.', 'status': 'passed' if campaign['detected'] == campaign['total'] and campaign['clean_control_critical_findings'] == 0 else 'blocked_by_detection_gap'},
        {'priority': 2, 'hypothesis': 'Bounded retries remain useful against competitors with varied reaction speeds.', 'experiment': 'Freeze policies; use unseen seeds and independently varied competitor timing and latency.', 'pass_condition': 'Report each condition separately; promote only if clean transfer reliability does not regress and contested outcomes improve.', 'status': 'proposed'},
        {'priority': 3, 'hypothesis': 'Recovery never reports success from an ambiguous response.', 'experiment': 'Interrupt workers after release and inject stale ownership reads and lost acknowledgements.', 'pass_condition': 'No false verified outcomes; every attempt has a recoverable or explicitly unresolved state.', 'status': 'proposed'}]
    if 'fast_competitors' in scenarios:
        proposals[1]['status'] = 'executed_requires_policy_comparison'
        proposals[1]['evidence_records'] = count
    return {'scope': 'Offline local simulation. Deterministic experiment director, not an autonomous LLM or security certification.', 'source': str(Path(source).resolve()), 'timing_tolerance_ms': 1, 'timing_tolerance_reason': 'Historical event clocks were sampled separately from state transition clocks.', 'records_audited': count, 'independent_audit_findings': audits, 'watcher_fault_campaign': campaign, 'experiments': proposals, 'authority': 'Read evidence and propose experiments; cannot alter policies, spend money, or access FOMO.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default='artifacts/retry-holdout/results.jsonl')
    parser.add_argument('--output', default='artifacts/director')
    args = parser.parse_args()
    report = direct(args.source)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    escaped = html.escape(json.dumps(report, indent=2))
    (output / 'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Experiment director</title><style>body{background:#101820;color:#dceee9;font:16px system-ui;margin:40px;max-width:1100px}pre{white-space:pre-wrap;background:#182730;padding:24px;border-radius:16px}h1{color:#74e9c3}</style><h1>Experiment director</h1><p>Independent evidence audit, watcher fault injections, and prioritized experiment proposals.</p><pre>' + escaped + '</pre>', encoding='utf-8')
    print(json.dumps({'records_audited': report['records_audited'], 'audit_findings': len(report['independent_audit_findings']), 'faults_detected': report['watcher_fault_campaign']['detected'], 'faults_injected': report['watcher_fault_campaign']['total'], 'report': str((output / 'index.html').resolve())}))


if __name__ == '__main__':
    main()
