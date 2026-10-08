"""Read-only invariant and consistency analysis of local lab snapshots."""
import statistics
from collections import Counter


def inspect_run(snapshot):
    events = snapshot['events']
    releases = [e for e in events if e['kind'] == 'released']
    acquisitions = [e for e in events if e['kind'] == 'acquired']
    findings = []
    def flag(code, severity, message):
        findings.append({'code': code, 'severity': severity, 'message': message})
    if len(releases) > 1:
        flag('duplicate_release', 'critical', 'Username was released more than once in one run.')
    if len(acquisitions) > 1:
        flag('multiple_winners', 'critical', 'More than one acquisition was recorded.')
    if acquisitions:
        winner = acquisitions[-1]
        if not releases or winner['time_ms'] < releases[0]['time_ms']:
            flag('claim_before_release', 'critical', 'Acquisition occurred without a preceding release.')
        if snapshot['owner'] != winner['actor']:
            flag('owner_mismatch', 'critical', 'Registry ownership differs from the recorded winner.')
        if releases and winner['time_ms'] < releases[0]['time_ms'] + snapshot['config']['cooldown_ms']:
            flag('cooldown_bypass', 'critical', 'Claim was accepted before cooldown ended.')
    if any(events[i]['time_ms'] < events[i-1]['time_ms'] for i in range(1, len(events))):
        flag('time_regression', 'critical', 'Server event timestamps moved backwards.')
    # Historical logs sampled event clocks separately; allow 1 ms of logging skew.
    if releases and acquisitions:
        expected = acquisitions[-1]['time_ms'] - releases[0]['time_ms']
        actual = snapshot.get('exposure_ms')
        if actual is None or abs(actual - expected) > 1:
            flag('exposure_mismatch', 'critical', 'Exposure measurement differs from raw transition timestamps.')
    rate_limits = sum(e['kind'] == 'rate_limited' for e in events)
    lost = sum(e['kind'] == 'response_lost' for e in events)
    if rate_limits:
        flag('rate_limits', 'warning', f'{rate_limits} requests were rate-limited.')
    if lost:
        flag('response_loss', 'warning', 'A claim response was lost; rely on registry ownership.')
    owner = snapshot['owner']
    if owner == snapshot['buyer']:
        outcome = 'verified'
        if not acquisitions:
            flag('unlogged_ownership', 'critical', 'Buyer owns target without an acquisition event.')
    elif owner not in (None, snapshot['seller']):
        outcome = 'competitor_capture'
        flag('capture', 'warning', 'A competitor acquired the target.')
    elif snapshot.get('finished'):
        outcome = 'unresolved' if releases else 'seller_retained'
    else:
        outcome = 'in_progress' if releases else 'prepared'
    if releases and owner is None:
        flag('unclaimed_release', 'warning', 'Released handle has no verified owner yet.')
    submissions = [e for e in events if e['kind'] == 'server_received' and e.get('actor') == snapshot['seller'] and e.get('operation') == 'release']
    total_ms = acquisitions[-1]['time_ms'] - submissions[0]['time_ms'] if acquisitions and submissions else None
    return {'id': snapshot['id'], 'username': snapshot['username'], 'outcome': outcome,
            'strategy': snapshot.get('strategy', 'manual'), 'config': snapshot['config'],
            'owner': owner, 'exposure_ms': snapshot['exposure_ms'], 'server_transfer_ms': total_ms,
            'requests': sum(snapshot['request_counts'].values()), 'rate_limits': rate_limits,
            'findings': findings}


def timing(values):
    if not values:
        return {'samples': 0, 'mean_ms': None, 'min_ms': None, 'max_ms': None, 'stddev_ms': None}
    return {'samples': len(values), 'mean_ms': statistics.mean(values), 'min_ms': min(values),
            'max_ms': max(values), 'stddev_ms': statistics.pstdev(values)}


def summarize(snapshots):
    rows = [inspect_run(s) for s in snapshots]
    counts = Counter(r['outcome'] for r in rows)
    released = [r for r, s in zip(rows, snapshots) if s['released_at_ms'] is not None]
    return {'scope': 'This server session; local simulation only. Invariant checks are not a security audit.',
            'runs_created': len(rows), 'released_runs': len(released), 'outcomes': dict(counts),
            'verified_fraction_of_released': None if not released else sum(r['outcome'] == 'verified' for r in released) / len(released),
            'exposure': timing([r['exposure_ms'] for r in released if r['exposure_ms'] is not None]),
            'critical_findings': sum(f['severity'] == 'critical' for r in rows for f in r['findings']),
            'runs': rows}
