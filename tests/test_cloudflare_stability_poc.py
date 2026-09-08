import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import os
import shutil
import pytest

ROOT = Path(__file__).resolve().parents[1]
POC = ROOT / 'cloudflare-stability-poc'


@pytest.fixture
def built_stages(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('stability_builder', POC / 'build_stage.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    temporary = tmp_path / 'poc'
    (temporary / 'results').mkdir(parents=True)
    shutil.copytree(POC / 'src', temporary / 'src', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copyfile(POC / 'pyproject.toml', temporary / 'pyproject.toml')
    shutil.copytree(POC / 'profiles', temporary / 'profiles')
    monkeypatch.setattr(builder, 'ROOT', temporary)
    for stage in builder.STAGES:
        builder.build(stage)
    return temporary


def test_stage_source_and_configuration_boundaries(built_stages):
    for stage in ['A','B','C','D-off','D-on','E-off','E-on','F','G','H','I','J','H-off','H-cached']:
        build = built_stages / '.build' / stage
        config = json.loads((build / 'wrangler.jsonc').read_text())
        assert config['name'] == 'shuttlers-match-stability-poc'
        assert config['workers_dev'] and not config.get('limits')
        assert not any(k in config for k in ['route','routes','d1_databases','r2_buckets','kv_namespaces','durable_objects'])
        src = build / 'src'
        assert (src / 'static').exists() == (stage == 'J')
        assert (build / 'assets').exists() == (stage in ['I','J'])
        assert (src / 'app.py').exists() == (stage[0] >= 'D')
        assert (src / 'side_effect_guard.py').exists() == (stage.endswith('-on') or stage in ['F','G','H','I','J','H-cached'])
        assert not list(build.glob('.env*')) and not list(build.glob('.dev.vars*'))


@pytest.mark.parametrize('stage', ['H', 'H-off', 'H-cached'])
def test_staged_flask_requests_never_initialize_runtime(tmp_path, built_stages, stage):
    code = r'''
import asyncio,sys,types,json,os
from werkzeug.wrappers import Response
from werkzeug.test import Client

def audit(event,args):
    if event in ('sqlite3.connect','socket.connect'):
        raise AssertionError('Unexpected I/O')
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        name=os.fsdecode(args[0])
        if name.endswith(('config.json','match_state.json','draft_state.json','.db')):
            raise AssertionError('Runtime data access')
sys.addaudithook(audit)
stub=types.ModuleType('workers');stub.Response=Response;stub.WorkerEntrypoint=object;stub.wsgi=object()
sys.modules['workers']=stub
import worker
assert not worker.app_module._runtime_initialized
assert worker.app.wsgi_app is worker.app_module.runtime_wsgi_app
client=Client(worker.safe_wsgi,Response)
for path in ['/wsgi','/url-map','/template/load','/template/compile','/template/render','/template/include','/template/full']:
    r=client.get(path)
    assert r.status_code==200,(path,r.status_code)
    counts=json.loads(r.headers['X-Stability-Effects'])
    assert counts is None or not any(counts.values())
assert not worker.app_module._runtime_initialized
'''
    env = dict(os.environ, PYTHONPATH=str(built_stages / '.build' / stage / 'src'), PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-B', '-c', code], cwd=tmp_path,
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_tail_sanitization_and_correlation(tmp_path):
    spec = importlib.util.spec_from_file_location('stability_tail', POC / 'collect_tail.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = tmp_path / 'tail.log'
    raw.write_text('Wrangler startup\n' + json.dumps({
        'outcome':'exception','cpuTime':3,'wallTime':7,
        'scriptVersion':{'id':'version'},
        'exceptions':[{'name':'ErrnoError','message':'#<Object>'}],
        'event':{'request':{'url':'https://example.workers.dev/raw?probe=A-raw-1',
                            'headers':{'cf-ray':'safe-ray','cf-connecting-ip':'private-ip','authorization':'private-token'},
                            'cf':{'city':'private-city'}},'response':{'status':500}},
    }))
    rows=module.collect(raw)
    assert rows[0]['probe']=='A-raw-1'
    assert rows[0]['outcome']=='exception' and rows[0]['cpu_ms']==3
    assert rows[0]['exceptions'][0]['top_frame'] is None
    assert 'private-' not in json.dumps(rows)
