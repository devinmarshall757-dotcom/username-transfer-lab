"""Conservative review of frozen acquisition policies; never promotes automatically."""
import argparse
import json
from collections import defaultdict, Counter
from pathlib import Path


def review(rows):
    buckets=defaultdict(list)
    for row in rows:
        buckets[(row['scenario'],row['competitors'],row['offset_ms'],row['strategy'])].append(row)
    groups=[]
    rates={}
    for key,attempts in sorted(buckets.items()):
        counts=Counter(r['outcome'] for r in attempts)
        rates[key]=counts['verified']/len(attempts)
        groups.append(dict(scenario=key[0],competitors=key[1],offset_ms=key[2],strategy=key[3],attempts=len(attempts),outcomes=dict(counts),verified_rate=rates[key]))
    scenarios=sorted({r['scenario'] for r in rows})
    evidence_errors=sum('error' in r for r in rows)
    audit_findings=sum(len(r.get('independent_findings',[])) for r in rows)
    critical_findings=sum(f.get('severity')=='critical' for r in rows for f in r.get('watcher',{}).get('findings',[]))
    decisions=[]
    for offset in (10,20):
        comparisons=[]
        clean=[]
        for scenario in scenarios:
            clean.append(rates.get((scenario,0,offset,'scheduled_retry'))==1)
            for competitors in (1,3):
                candidate=rates.get((scenario,competitors,offset,'scheduled_retry'))
                baseline=rates.get((scenario,competitors,40,'scheduled'))
                comparisons.append(dict(scenario=scenario,competitors=competitors,candidate_rate=candidate,baseline_rate=baseline,delta=None if candidate is None or baseline is None else candidate-baseline))
        complete=bool(scenarios) and all(v['delta'] is not None for v in comparisons)
        qualifies=complete and not evidence_errors and not audit_findings and not critical_findings and all(clean) and all(v['delta']>=0 for v in comparisons) and any(v['delta']>0 for v in comparisons)
        decisions.append({'offset_ms':offset,'decision':'larger_holdout_required' if qualifies else 'do_not_promote','clean_controls_all_verified':all(clean) and bool(clean),'comparisons':comparisons})
    return {'scope':'Local browser model; conditional timing and success rates are not live-platform estimates. Small per-condition samples; no automatic policy promotion.','recorded':len(rows),'runner_errors':evidence_errors,'independent_findings':audit_findings,'critical_findings':critical_findings,'groups':groups,'decisions':decisions}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    rows=[json.loads(line) for line in Path(args.source).read_text().splitlines() if line.strip()]
    report=review(rows)
    checkpoint=Path(args.source).with_name('report.json')
    if checkpoint.exists():
        metadata=json.loads(checkpoint.read_text())
        report['benchmark_complete']=metadata.get('status')=='complete' and metadata.get('planned')==len(rows)
        if not report['benchmark_complete']:
            for decision in report['decisions']:
                decision['decision']='incomplete_benchmark'
    Path(args.output).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'recorded':report['recorded'],'decisions':[(r['offset_ms'],r['decision']) for r in report['decisions']]}))

if __name__=='__main__':
    main()
