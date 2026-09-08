"""Serial curl measurements. Store no IP, location, full headers or bodies."""
import argparse
from collections import Counter
from email.parser import Parser
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parent


def measure(stage,base,path,count=50,timing=False):
    records=[]
    batch=time.time_ns()
    with tempfile.TemporaryDirectory(prefix='stability-http-') as temp:
        headers=Path(temp)/'headers';body=Path(temp)/'body'
        for seq in range(count):
            delay=0.1
            cadence='continuous'
            if timing and 40<=seq<49:
                delay=2;cadence='spaced-2s'
            elif timing and seq==49:
                delay=15;cadence='after-15s-idle'
            if seq:
                time.sleep(delay)
            probe=f'{stage}-{path.strip("/").replace("/","_")}-{batch}-{seq}'
            url=base.rstrip('/')+path+'?probe='+probe
            started=time.monotonic()
            completed=subprocess.run(['curl','-sS','--max-time','25','-D',str(headers),'-o',str(body),'-w','%{http_code}',url],capture_output=True,text=True)
            latency=round((time.monotonic()-started)*1000,3)
            raw=body.read_bytes() if body.exists() else b''
            status=int(completed.stdout) if completed.stdout.isdigit() else 0
            header_text=headers.read_text() if headers.exists() else ''
            block=header_text.strip().split('\n\n')[-1]
            h=Parser().parsestr(block.split('\n',1)[1] if '\n' in block else '')
            error=raw.decode(errors='replace').strip() if raw.startswith(b'error code:') else None
            counters=h.get('X-Stability-Effects')
            import_counters=h.get('X-Stability-Import-Effects')
            returned_stage=h.get('X-Stability-Stage')
            record={'stage':stage,'path':path,'probe':probe,'seq':seq,'cadence':cadence,
                    'status':status,'error':error,'ray_id':h.get('CF-Ray'),
                    'client_latency_ms':latency,'bytes':len(raw),
                    'body_sha256':hashlib.sha256(raw).hexdigest(),
                    'returned_stage':returned_stage,
                    'invocation_marker':h.get('X-Stability-Invocation'),
                    'side_effects':json.loads(counters) if counters else None,
                    'import_side_effects':json.loads(import_counters) if import_counters else None,
                    'transport_exit':completed.returncode}
            if status==200 and returned_stage and returned_stage!=stage:
                record['stale_stage']=True
            for counts in [record['side_effects'],record['import_side_effects']]:
                assert not counts or not any(counts.values()),'Forbidden effect observed'
            assert 'Set-Cookie' not in h
            records.append(record)
    summary={'stage':stage,'path':path,'requests':len(records),
             'success':sum(r['status']==200 and not r.get('stale_stage') for r in records),
             '1101':sum(r['error']=='error code: 1101' for r in records),
             '1102':sum(r['error']=='error code: 1102' for r in records),
             'other_statuses':dict(Counter(str(r['status']) for r in records if r['status']!=200 and r['error'] not in ['error code: 1101','error code: 1102'])),
             'stale_stage_responses':sum(bool(r.get('stale_stage')) for r in records)}
    return {'summary':summary,'requests':records}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage');parser.add_argument('base');parser.add_argument('path');parser.add_argument('--count',type=int,default=50);parser.add_argument('--timing',action='store_true');parser.add_argument('--suffix',default='')
    args=parser.parse_args();report=measure(args.stage,args.base,args.path,args.count,args.timing)
    name=args.stage+'-'+args.path.strip('/').replace('/','_')+args.suffix+'.json'
    (ROOT/'results'/name).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report['summary']),flush=True)
