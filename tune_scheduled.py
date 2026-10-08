"""Offset selection on tuning seeds, then frozen-offset holdout validation."""
import argparse
import json
import random
import threading
import time
from collections import defaultdict
from pathlib import Path

from batch_lab import BatchHandler, grouped_report
from browser_lab import Config, LabServer
from durable_transfer import Coordinator
from lab_watcher import inspect_run
from run_browser_lab import BrowserPlatform, request
from retry_policy import RetryBrowserPlatform


OFFSETS = (0, 5, 10, 20, 40, 60)
SCENARIOS = {
    'baseline': {'network_ms':15, 'processing_ms':5, 'jitter_ms':10,
                 'competitor_interval_ms':50},
    'high_latency': {'network_ms':75, 'processing_ms':10, 'jitter_ms':40,
                     'competitor_interval_ms':50},
    'timing_uncertainty': {'network_ms':30, 'processing_ms':10, 'jitter_ms':50,
                           'competitor_interval_ms':20},
}


def select_offset(results, minimum_clean_rate=.9):
    candidates = []
    for offset in OFFSETS:
        rows = [r for r in results if r['phase']=='tuning' and r['offset_ms']==offset]
        groups = grouped_report(rows)
        if len(groups)!=3:
            continue
        rates = {g['competitors']:g['verified_rate'] for g in groups}
        if rates[0] < minimum_clean_rate:
            continue
        candidates.append((min(rates[1], rates[3]), (rates[1]+rates[3])/2, -offset, offset))
    if not candidates:
        return None
    return max(candidates)[-1]


def groups_for(results):
    buckets = defaultdict(list)
    for row in results:
        buckets[(row['phase'],row['scenario'],row['offset_ms'])].append(row)
    return [{**group,'phase':phase,'scenario':scenario,'offset_ms':offset}
            for (phase,scenario,offset), rows in sorted(buckets.items())
            for group in grouped_report(rows)]


def write_report(path, report):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(report,indent=2),encoding='utf-8')
    for attempt in range(40):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt==39:
                raise
            time.sleep(.05)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tuning-runs',type=int,default=10)
    parser.add_argument('--validation-runs',type=int,default=20)
    parser.add_argument('--output',default='artifacts/scheduled-tuning')
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--keep-open',action='store_true')
    parser.add_argument('--retry-study',action='store_true',help='Compare fixed 10 ms with/without retries and 40 ms single claim on new seeds')
    args=parser.parse_args()
    if not 1<=args.tuning_runs<=1000 or not 1<=args.validation_runs<=1000:
        parser.error('Run counts must be between 1 and 1000')
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    if (out/'results.jsonl').exists():
        parser.error('Choose a fresh output folder; prior evidence will not be overwritten')
    from playwright.sync_api import sync_playwright
    server=LabServer(('127.0.0.1',args.port));server.RequestHandlerClass=BatchHandler
    server.progress_lock=threading.Lock();server.progress={}
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    results=[];current=None;selected=None;status='running';start=time.perf_counter()
    planned=45*args.validation_runs if args.retry_study else 18*args.tuning_runs+18*args.validation_runs
    study_scenarios=dict(SCENARIOS)
    if args.retry_study:
        study_scenarios['rate_limit']={**SCENARIOS['baseline'],'min_interval_ms':80,'cooldown_ms':75}
        study_scenarios['response_loss']={**SCENARIOS['baseline'],'lost_claim_response':True}

    def checkpoint():
        report={'scope':'Local browser offset tuning and fresh-seed validation; not FOMO measurements',
                'status':status,'planned':planned,'completed':len(results),'elapsed_seconds':time.perf_counter()-start,
                'current':current,'selected_offset_ms':selected,'baseline_offset_ms':40,
                'selection_rule':('Fixed 10 ms single, 10 ms bounded retry, and 40 ms single policies; no selection using these results.' if args.retry_study else 'Clean win rate >=90%; maximize worst contested rate, then mean contested rate, then smaller offset. Validation never changes selection.'),
                'tuning_seed_start':None if args.retry_study else 10000,'validation_seed_start':90000 if args.retry_study else 50000,'scenarios':study_scenarios,
                'retry_policy':{'max_attempts':3,'deadline_ms':500,'ownership_checks':'before and after each claim','interval_floor_ms':20} if args.retry_study else None,
                'groups':groups_for(results),'runner_errors':sum('error' in r for r in results),
                'settings':'Paired seeds; parameter combinations shuffled per seed; no retries of failed attempts. Browser scheduling remains variable.'}
        write_report(out/'report.json',report)
        with server.progress_lock:server.progress=report

    checkpoint();print(f'Tuning progress: {base}/batch',flush=True)
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            contexts=[browser.new_context() for _ in range(2)];pages=[c.new_page() for c in contexts]
            try:
                with (out/'results.jsonl').open('a',encoding='utf-8') as ledger:
                    def trial(phase,scenario,offset,competitors,seed,retry=False):
                        nonlocal current
                        seller_error=(random.Random(seed).uniform(-20,20) if scenario=='timing_uncertainty' else 0)
                        current={'strategy':'scheduled_retry' if retry else 'scheduled','phase':phase,'scenario':scenario,'offset_ms':offset,
                                 'competitors':competitors,'seed':seed,'seller_error_ms':seller_error}
                        checkpoint()
                        config={**study_scenarios[scenario],'seed':seed,'competitor_count':competitors}
                        Config(**config)
                        run=request(base,'/runs',config);result={**current,'id':run['id'],'config':run['config']}
                        c=Coordinator(out/'transactions.sqlite')
                        try:
                            for page,actor in zip(pages,(run['seller'],run['buyer'])):
                                page.goto(f'{base}/?run={run["id"]}&actor={actor}')
                                page.wait_for_function('window.ready===true')
                            c.create(run['id'],run['username'],run['seller'],run['buyer'])
                            request(base,f'/runs/{run["id"]}/label',{'strategy':'scheduled'})
                            request(base,f'/runs/{run["id"]}/arm',{})
                            adapter_class=RetryBrowserPlatform if retry else BrowserPlatform
                            adapter=adapter_class(base,run,*pages,'scheduled',offset,25,seller_error)
                            record=c.run(run['id'],adapter);adapter.drain()
                            snapshot=request(base,f'/runs/{run["id"]}/finish',{})
                            watcher=inspect_run(snapshot)
                            if watcher['outcome']!=record['state']:raise RuntimeError('Coordinator/watcher mismatch')
                            result.update(outcome=record['state'],transaction=record,watcher=watcher,server_events=snapshot['events'])
                            if retry:
                                trace=pages[1].evaluate('window.retryTrace')
                                result['retry_trace']=trace
                                claim_requests=[e for e in snapshot['events'] if e['kind']=='server_received' and e.get('actor')==run['buyer'] and e.get('operation')=='claim']
                                if len(claim_requests)>RetryBrowserPlatform.MAX_ATTEMPTS:
                                    raise RuntimeError('Retry attempt cap exceeded')
                        except Exception as exc:
                            snapshot=request(base,f'/runs/{run["id"]}/finish',{})
                            result.update(outcome='unresolved',error=str(exc),watcher=inspect_run(snapshot),server_events=snapshot['events'])
                        finally:
                            c.close();request(base,f'/runs/{run["id"]}/finish',{})
                        ledger.write(json.dumps(result)+'\n');ledger.flush();results.append(result)
                        with server.registry_lock:server.runs.pop(run['id'],None)
                        checkpoint()
                        if len(results)%25==0:print(f'{len(results)}/{planned} completed',flush=True)

                    if args.retry_study:
                        selected=10
                        write_report(out/'selection.json',{'fixed_before_validation':True,
                            'policies':[{'offset_ms':10,'retry':False},{'offset_ms':10,'retry':True},{'offset_ms':40,'retry':False}],
                            'max_attempts':3,'deadline_ms':500,'seed_start':90000})
                        for i in range(args.validation_runs):
                            cells=[(scenario,offset,retry,comp) for scenario in study_scenarios
                                   for offset,retry in ((10,False),(10,True),(40,False)) for comp in (0,1,3)]
                            random.Random(90000+i).shuffle(cells)
                            for scenario,offset,retry,comp in cells:
                                trial('retry_validation',scenario,offset,comp,90000+i,retry)
                    for i in range(0 if args.retry_study else args.tuning_runs):
                        cells=[(offset,comp) for offset in OFFSETS for comp in (0,1,3)]
                        random.Random(10000+i).shuffle(cells)
                        for offset,comp in cells:trial('tuning','baseline',offset,comp,10000+i)
                    if not args.retry_study:
                        selected=select_offset(results)
                    # If no candidate clears the clean-control gate, validate
                    # only the incumbent; do not pretend a candidate qualified.
                    offsets=sorted({40,selected} if selected is not None else {40})
                    if not args.retry_study:
                        planned=len(results)+len(offsets)*9*args.validation_runs
                    if not args.retry_study:
                        write_report(out/'selection.json',{'selected_offset_ms':selected,'baseline_offset_ms':40,
                            'qualified':selected is not None,'frozen_before_validation':True,'tuning_groups':groups_for(results)})
                        print(f'Frozen tuning choice: {selected} ms; holdout begins',flush=True)
                    checkpoint()
                    for i in range(0 if args.retry_study else args.validation_runs):
                        cells=[(scenario,offset,comp) for scenario in SCENARIOS for offset in offsets for comp in (0,1,3)]
                        random.Random(50000+i).shuffle(cells)
                        for scenario,offset,comp in cells:trial('validation',scenario,offset,comp,50000+i)
            finally:
                for c in contexts:c.close()
                browser.close()
        status='complete'
    except BaseException:
        status='interrupted';raise
    finally:
        current=None;checkpoint();print(f'{status}: {len(results)}/{planned}',flush=True)
        if args.keep_open and status=='complete':
            try:
                while True:time.sleep(1)
            except KeyboardInterrupt:pass
        server.shutdown();server.server_close();thread.join(5)


if __name__=='__main__':main()
