"""Strict evidence and real process-death recovery campaign, local SQLite fixture only."""
import argparse
import json
import multiprocessing
import os
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from durable_transfer import Coordinator, PersistentFakePlatform, InjectedCrash


def crash_worker(db, platform_db, boundary):
    c = Coordinator(db, require_evidence=True)
    p = PersistentFakePlatform(platform_db)
    try:
        c.run('tx', p, crash_after=boundary)
    except InjectedCrash:
        os._exit(23)
    os._exit(24)


class FaultReader:
    def __init__(self, platform, fault):
        self.platform = platform
        self.fault = fault
        self.username = platform.username
        self.resource_key = platform.resource_key
    def verify_evidence(self, nonce):
        evidence = self.platform.verify_evidence(nonce)
        if self.fault == 'expired_buyer':
            return replace(evidence, owner='buyer', observed_at=time.monotonic()-60)
        if self.fault == 'replayed_buyer':
            return replace(evidence, owner='buyer', nonce='previous-request')
        if self.fault == 'wrong_resource':
            return replace(evidence, owner='buyer', resource='another-platform')
        if self.fault == 'wrong_username':
            return replace(evidence, owner='buyer', username='another-handle')
        if self.fault == 'non_authoritative':
            return replace(evidence, owner='buyer', authoritative=False)
        if self.fault == 'future_timestamp':
            return replace(evidence, owner='buyer', observed_at=time.monotonic()+60)
        if self.fault == 'unavailable':
            return None
        return evidence
    def release(self, seller):
        return self.platform.release(seller)
    def claim(self, buyer):
        return self.platform.claim(buyer)


def run_case(boundary, fault='fresh', competitor=False, saved_success=False):
    with tempfile.TemporaryDirectory() as directory:
        db, platform_db = [str(Path(directory)/name) for name in ('c.sqlite','p.sqlite')]
        c = Coordinator(db, require_evidence=True)
        p = PersistentFakePlatform(platform_db)
        c.create('tx')
        c.close()
        p.close()
        process = multiprocessing.get_context('spawn').Process(target=crash_worker, args=(db,platform_db,boundary))
        process.start()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
            raise RuntimeError('Crash worker timed out')
        exitcode = process.exitcode
        process.close()
        if exitcode != 23:
            raise RuntimeError(f'Unexpected worker exit {exitcode}')
        c = Coordinator(db, require_evidence=True)
        p = PersistentFakePlatform(platform_db)
        try:
            before = c.get('tx')['state']
            if saved_success:
                c.run('tx', p)
            if competitor:
                p.db.execute("UPDATE usernames SET owner='competitor' WHERE name='demo'")
            result = c.run('tx', FaultReader(p,fault))
            actual = p.verify()[1]
            expected = ('unresolved' if fault != 'fresh' else 'competitor_capture' if competitor else 'verified')
            return {'boundary':boundary,'fault':fault,'competitor':competitor,'saved_success':saved_success,'worker_exit':exitcode,'durable_state_after_crash':before,'recovered_state':result['state'],'eligible':result['eligible'],'actual_owner':actual,'expected_state':expected,'passed':result['state']==expected and (not result['eligible'] or actual=='buyer')}
        finally:
            c.close()
            p.close()


def campaign():
    cases = [run_case('release'), run_case('claim'), run_case('release',competitor=True)]
    for fault in ('expired_buyer','replayed_buyer','wrong_resource','wrong_username','non_authoritative','future_timestamp','unavailable'):
        cases.append(run_case('claim',fault,competitor=True))
    cases.append(run_case('claim','expired_buyer',competitor=True,saved_success=True))
    return {'scope':'Strict evidence mode against a local persistent fixture; real spawned worker exits. Not proof against a dishonest authoritative source or live platform vulnerabilities.', 'cases':cases,'completed':len(cases),'passed':sum(c['passed'] for c in cases),'false_verified':sum(c['eligible'] and c['actual_owner']!='buyer' for c in cases)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='artifacts/recovery-evidence')
    args=parser.parse_args()
    report=campaign()
    output=Path(args.output)
    output.mkdir(parents=True,exist_ok=True)
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    if report['passed'] != report['completed']:
        raise SystemExit(1)

if __name__=='__main__':
    main()
