"""Supplement first new endpoints when rollout sent requests to an older version.

Wait until the stage's last operation ends, so supplementation does not overlap
its main benchmark. Each supplemental attempt remains in the evidence.
"""
import json
from pathlib import Path
import time
from collect_tail import collect
from measure import measure
from run_stage import BASE

ROOT=Path(__file__).resolve().parent
for stage,path,last in [('F','/url-map','F-url-map.json'),('G','/template/load','G-template_include.json')]:
    deadline=time.monotonic()+1800
    while not (ROOT/'results'/last).exists():
        if time.monotonic()>deadline:
            raise TimeoutError(stage)
        time.sleep(1)
    time.sleep(3)
    report=json.loads((ROOT/'results'/(stage+'-'+path.strip('/').replace('/','_')+'.json')).read_text())
    events={r['ray_id'].split('-')[0]:r for r in collect('/tmp/stability-tail.log') if r['ray_id']}
    version=json.loads((ROOT/'results'/f'{stage}-deploy.json').read_text())['version_id']
    valid=sum(events.get((r['ray_id'] or '').split('-')[0],{}).get('version_id')==version for r in report['requests'])
    if valid<50:
        extra=measure(stage,BASE,path,count=max(10,50-valid))
        out=ROOT/'results'/(stage+'-'+path.strip('/').replace('/','_')+'-supplement.json')
        out.write_text(json.dumps(extra,indent=2)+'\n')
        print('Supplement',stage,extra['summary'],flush=True)
    else:
        print(stage,'has 50 version-matched requests; no supplement',flush=True)
