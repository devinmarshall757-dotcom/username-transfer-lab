"""Local browser evidence faults with 1/3 competitors and buyer-session recovery."""
import argparse
import itertools
import json
import random
import threading
from collections import Counter
from pathlib import Path
from browser_lab import LabServer
from durable_transfer import Coordinator, InjectedCrash
from run_browser_lab import BrowserPlatform, request
from lab_watcher import inspect_run


def assess(first, final, snapshot, fault):
    issues=[]
    for label, record in [('first',first),('final',final)]:
        if record['eligible'] and snapshot['owner'] != snapshot['buyer']:
            issues.append(label+'_false_verified')
    if fault != 'none' and first['eligible']:
        issues.append('fault_granted_eligibility')
    releases=sum(e['kind']=='released' for e in snapshot['events'])
    requests=sum(e['kind']=='server_received' and e.get('operation')=='release' for e in snapshot['events'])
    if releases != 1 or requests != 1:
        issues.append('release_replayed_or_missing')
    issues += [f['code'] for f in inspect_run(snapshot)['findings'] if f['severity']=='critical']
    return issues


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runs',type=int,default=1)
    parser.add_argument('--seed-start',type=int,default=180000)
    parser.add_argument('--output',default='artifacts/browser-race-recovery')
    args=parser.parse_args()
    if args.runs < 1:
        parser.error('runs must be positive')
    output=Path(args.output)
    output.mkdir(parents=True,exist_ok=True)
    if (output/'results.jsonl').exists():
        parser.error('Use a fresh output directory')
    cells=list(itertools.product(('scheduled','confirmed','polling'),(1,3),('none','stale','replayed','delayed'),(False,True)))
    plan={'scope':'Local Edge browser session closure after release; server remains alive. Not OS process death or live FOMO performance.', 'planned':len(cells)*args.runs,'runs_per_cell':args.runs,'seed_start':args.seed_start,'competitors':[1,3],'faults':['none','stale','replayed','delayed'],'recovery':'Fresh replacement buyer session and fresh ownership evidence; no release replay.'}
    (output/'plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    server=LabServer(('127.0.0.1',0))
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    rows=[]
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                with (output/'results.jsonl').open('w',encoding='utf-8') as ledger:
                    for index in range(args.runs):
                        random.Random(args.seed_start+index).shuffle(cells)
                        for strategy, competitors, fault, interrupted in cells:
                            run=request(base,'/runs',{'seed':args.seed_start+index,'competitor_count':competitors,'competitor_interval_ms':20})
                            contexts=[browser.new_context() for _ in range(2)]
                            c=Coordinator(output/'transactions.sqlite',require_evidence=True)
                            row={'id':run['id'],'seed':args.seed_start+index,'strategy':strategy,'competitors':competitors,'fault':fault,'interrupted':interrupted}
                            try:
                                pages=[context.new_page() for context in contexts]
                                for page,actor in zip(pages,(run['seller'],run['buyer'])):
                                    page.goto(f'{base}/?run={run["id"]}&actor={actor}')
                                    page.wait_for_function('window.ready===true')
                                c.create(run['id'],run['username'],run['seller'],run['buyer'])
                                adapter=BrowserPlatform(base,run,*pages,strategy,40,25,0)
                                adapter.evidence_fault=fault
                                request(base,f'/runs/{run["id"]}/arm',{})
                                if interrupted:
                                    try:
                                        c.run(run['id'],adapter,crash_after='release')
                                    except InjectedCrash:
                                        row['interrupted_state']=c.get(run['id'])['state']
                                    else:
                                        raise RuntimeError('Did not reach intended interruption boundary')
                                    contexts[1].close()
                                    replacement=browser.new_context()
                                    contexts.append(replacement)
                                    pages[1]=replacement.new_page()
                                    pages[1].goto(f'{base}/?run={run["id"]}&actor={run["buyer"]}')
                                    pages[1].wait_for_function('window.ready===true')
                                    adapter=BrowserPlatform(base,run,*pages,'confirmed',40,25,0)
                                    adapter.started=True
                                    adapter.claim_finished=True
                                    adapter.evidence_fault=fault
                                first=c.run(run['id'],adapter)
                                request(base,f'/runs/{run["id"]}/finish',{})
                                recovery=BrowserPlatform(base,run,*pages,'confirmed',40,25,0)
                                recovery.started=True
                                recovery.claim_finished=True
                                final=c.run(run['id'],recovery)
                                snapshot=request(base,f'/runs/{run["id"]}/finish',{})
                                row.update(first=first,final=final,server=snapshot,findings=assess(first,final,snapshot,fault))
                            except Exception as exc:
                                row.update(error=str(exc),findings=['runner_error'])
                            finally:
                                c.close()
                                for context in contexts:
                                    context.close()
                                request(base,f'/runs/{run["id"]}/finish',{})
                            rows.append(row)
                            ledger.write(json.dumps(row)+'\n')
                            ledger.flush()
                            print(f'{len(rows)}/{plan["planned"]}: {strategy} competitors={competitors} {fault} interrupted={interrupted} findings={row["findings"]}',flush=True)
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    groups={}
    for row in rows:
        key=(row['competitors'],row['fault'],row['interrupted'])
        counts=groups.setdefault(key,Counter())
        counts[row.get('final',{}).get('state','runner_error')]+=1
    report=dict(plan,status='complete',completed=len(rows),runner_errors=sum('error' in r for r in rows),finding_count=sum(len(r['findings']) for r in rows),groups=[{'competitors':c,'fault':f,'interrupted':i,'final_outcomes':dict(v)} for (c,f,i),v in sorted(groups.items())])
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    if report['finding_count']:
        raise SystemExit(1)

if __name__=='__main__':
    main()
