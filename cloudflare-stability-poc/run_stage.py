"""Deploy only the new stability Worker, audit first, then measure each operation."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time

from audit_bundle import audit
from measure import measure

ROOT=Path(__file__).resolve().parent
BASE='https://shuttlers-match-stability-poc.nowdon.workers.dev'


def cli(stage,args,tag):
    logfile=Path('/tmp')/f'stability-{stage}-{tag}.log'
    with logfile.open('w') as output:
        subprocess.run(['uv','run','--directory',str(ROOT/'.build'/stage),'pywrangler',*args],stdout=output,stderr=subprocess.STDOUT,check=True)
    return logfile.read_text()


def metadata(stage,dry,deploy):
    size=re.search(r'Total Upload: ([\d.]+) KiB / gzip: ([\d.]+) KiB',deploy)
    modules=re.search(r'Total \((\d+) modules\)',dry)
    startup=re.search(r'Worker Startup Time: (\d+) ms',deploy)
    version=re.search(r'Current Version ID: ([a-f0-9-]+)',deploy)
    assert size and version
    result={'stage':stage,'module_count':int(modules[1]) if modules else None,
            'uncompressed_kib':float(size[1]),'gzip_kib':float(size[2]),
            'startup_ms':int(startup[1]) if startup else None,'version_id':version[1],
            'static_asset_count':55 if stage[0] in 'IJ' else 0,
            'deploy_url':BASE}
    (ROOT/'results'/f'{stage}-deploy.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)


def operations(stage):
    if stage=='F':return ['/url-map']
    if stage=='G':return ['/template/load','/template/compile','/template/render','/template/include']
    if stage=='H':return ['/raw','/wsgi','/url-map','/template/load','/template/compile','/template/render','/template/include','/template/full']
    if stage=='I':return ['/raw','/assets/participants_template.csv','/assets/cards/c8.png','/template/full']
    if stage in ['H-off','H-cached']:return ['/raw','/template/full']
    if stage=='J':return ['/raw','/static/participants_template.csv','/static/cards/c8.png','/template/full']
    if stage.startswith('E'):return ['/raw','/wsgi']
    return ['/raw']


def run(stage,deploy=True):
    if deploy:
        bundle='/tmp/stability-'+stage+'-bundle'
        dry=cli(stage,['deploy','--dry-run','--outdir',bundle],'dry')
        audit(stage,bundle)
        deployed=cli(stage,['deploy'],'deploy')
        metadata(stage,dry,deployed)
    for index,path in enumerate(operations(stage)):
        for attempt in range(3):
            report=measure(stage,BASE,path,50,timing=index==0)
            name=stage+'-'+path.strip('/').replace('/','_')
            stale=report['summary']['stale_stage_responses']
            suffix=f'-rollout-{attempt+1}' if stale else ''
            (ROOT/'results'/(name+suffix+'.json')).write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps(report['summary']),flush=True)
            if not stale:
                break
            time.sleep(30)
        else:
            raise RuntimeError('Stage rollout still incomplete; preserve all attempts')



if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stages',nargs='+');parser.add_argument('--measure-only',action='store_true');a=parser.parse_args()
    for stage in a.stages:run(stage,deploy=not a.measure_only)
