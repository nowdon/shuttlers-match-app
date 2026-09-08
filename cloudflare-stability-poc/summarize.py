"""Join sanitized HTTP and tail evidence; never infer cold starts from gaps."""
from collections import Counter
import json
from pathlib import Path
import statistics

from collect_tail import collect
from build_stage import STAGES

ROOT=Path(__file__).resolve().parent


def summarize():
    events=collect('/tmp/stability-tail.log')
    (ROOT/'results/tail-sanitized.json').write_text(json.dumps(events,indent=2)+'\n')
    by_ray={e['ray_id'].split('-')[0]:e for e in events if e['ray_id']}
    rows=[];joined=[]
    for stage in STAGES:
        deployed=ROOT/'results'/f'{stage}-deploy.json'
        if not deployed.exists():continue
        meta=json.loads(deployed.read_text())
        requests=[]
        for file in sorted((ROOT/'results').glob(stage+'-*.json')):
            if '-local' in file.name or '-rollout-' in file.name:continue
            report=json.loads(file.read_text())
            if not isinstance(report,dict) or 'requests' not in report:continue
            if report.get('summary',{}).get('stage') != stage:continue
            for original in report['requests']:
                r=dict(original)
                r['measurement_file']=file.name
                event=by_ray.get((r.get('ray_id') or '').split('-')[0])
                if event and event['probe']==r['probe']:
                    r['tail']=event
                    r['version_matches']=event['version_id']==meta['version_id']
                else:
                    r['tail']=None;r['version_matches']=None
                requests.append(r)
        if not requests:continue
        valid=[r for r in requests if r['version_matches'] is not False and not r.get('stale_stage')]
        row={**meta,'requests':len(valid),'scheduled_requests':len(requests),'success':sum(r['status']==200 for r in valid),
             '1101':sum(r['error']=='error code: 1101' for r in valid),
             '1102':sum(r['error']=='error code: 1102' for r in valid),
             'other':sum(r['status']!=200 and r['error'] not in ['error code: 1101','error code: 1102'] for r in valid),
             'version_mismatches':len(requests)-len(valid),
             'tail_coverage':sum(r['tail'] is not None for r in requests),
             'outcomes':dict(Counter(r['tail']['outcome'] for r in valid if r['tail'])),
             'exceptions':dict(Counter(e['type'] for r in valid if r['tail'] for e in r['tail']['exceptions'])),
             'by_cadence':{},'by_path':{}}
        for field,key in [('cadence','by_cadence'),('path','by_path')]:
            for value in sorted({r[field] for r in valid}):
                subset=[r for r in valid if r[field]==value]
                cpu=[r['tail']['cpu_ms'] for r in subset if r['tail'] and r['tail']['cpu_ms'] is not None]
                wall=[r['tail']['wall_ms'] for r in subset if r['tail'] and r['tail']['wall_ms'] is not None]
                success_cpu=[r['tail']['cpu_ms'] for r in subset if r['status']==200 and r['tail'] and r['tail']['cpu_ms'] is not None]
                row[key][value]={'requests':len(subset),'success':sum(r['status']==200 for r in subset),
                                 '1101':sum(r['error']=='error code: 1101' for r in subset),
                                 '1102':sum(r['error']=='error code: 1102' for r in subset),
                                 'cpu_median_ms':statistics.median(cpu) if cpu else None,
                                 'success_cpu_median_ms':statistics.median(success_cpu) if success_cpu else None,
                                 'cpu_max_ms':max(cpu) if cpu else None,
                                 'wall_median_ms':statistics.median(wall) if wall else None,
                                 'wall_max_ms':max(wall) if wall else None}
        rows.append(row);joined.extend(requests)
    (ROOT/'results/summary.json').write_text(json.dumps(rows,indent=2)+'\n')
    (ROOT/'results/http-tail-joined.json').write_text(json.dumps(joined,indent=2)+'\n')
    print('| Stage | gzip KiB | requests | success | 1101 | 1102 | other | tail |')
    print('| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |')
    for row in rows:
        print('| ' + ' | '.join(str(row[k]) for k in ['stage','gzip_kib','requests','success','1101','1102','other','tail_coverage']) + ' |')
    return rows


if __name__=='__main__':summarize()
