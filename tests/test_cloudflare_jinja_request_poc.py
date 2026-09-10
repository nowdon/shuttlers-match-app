import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from jinja2 import Environment, meta


ROOT = Path(__file__).resolve().parents[1]
POC = ROOT / "cloudflare-jinja-request-poc"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_matches_all_templates_and_static_analysis():
    sys.path.insert(0, str(POC))
    try:
        catalog = load_module("jinja_poc_catalog", POC / "catalog.py")
    finally:
        sys.path.pop(0)
    actual = {path.name for path in (ROOT / "templates").glob("*.html")}
    configured = {spec["name"] for spec in catalog.TEMPLATES.values()}
    assert len(actual) == 14
    assert configured == actual
    assert set(catalog.RENDER_SLUGS) == {
        slug for slug, spec in catalog.TEMPLATES.items() if spec["render"]
    }
    environment = Environment()
    for spec in catalog.TEMPLATES.values():
        source = (ROOT / "templates" / spec["name"]).read_text()
        parsed = environment.parse(source)
        list(meta.find_referenced_templates(parsed))
        meta.find_undeclared_variables(parsed)
        assert spec["classification"] in {"A", "B", "C"}


def test_build_and_config_exclude_runtime_data_static_and_bindings(tmp_path):
    sys.path.insert(0, str(POC))
    try:
        builder = load_module("jinja_poc_builder", POC / "build.py")
    finally:
        sys.path.pop(0)
    target = tmp_path / "build"
    builder.build(target, tmp_path / "manifest.json")
    config = json.loads((target / "wrangler.jsonc").read_text())
    assert config["name"] == "shuttlers-match-jinja-poc"
    assert config["workers_dev"] and not config["preview_urls"]
    assert config["compatibility_flags"] == ["python_workers"]
    assert not any(
        key in config
        for key in (
            "route",
            "routes",
            "assets",
            "d1_databases",
            "r2_buckets",
            "kv_namespaces",
            "durable_objects",
            "limits",
            "vars",
        )
    )
    assert not (target / "src" / "static").exists()
    assert not (target / "assets").exists()
    assert len(list((target / "src" / "templates").glob("*.html"))) == 14
    assert not list(target.glob(".env*"))
    assert not list(target.glob(".dev.vars*"))


def test_worker_uses_original_app_one_template_routes_and_never_clears_cache():
    source = (POC / "src" / "worker.py").read_text()
    assert "from app import app" in source
    assert "ORIGINAL_WSGI = app_module._flask_wsgi_app" in source
    assert "env.cache.clear" not in source
    assert "initialize_runtime(" not in source
    assert "app.wsgi_app =" not in source


def test_all_load_compile_and_allowlisted_render_are_side_effect_free(tmp_path):
    build = tmp_path / "build"
    sys.path.insert(0, str(POC))
    try:
        builder = load_module("jinja_poc_builder_runtime", POC / "build.py")
        builder.build(build, tmp_path / "manifest.json")
    finally:
        sys.path.pop(0)
    code = r'''
import json,os,sys,types
from werkzeug.test import Client
from werkzeug.wrappers import Response

def audit(event,args):
    if event in ('sqlite3.connect','socket.connect'):
        raise AssertionError('Unexpected I/O')
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        path=os.fsdecode(args[0]).replace('\\','/')
        name=path.rsplit('/',1)[-1]
        if name in ('config.json','match_state.json','draft_state.json'):
            raise AssertionError('Runtime data access')
        if name.endswith(('.db','.db-wal','.db-shm','.db-journal')) or '/history_dumps/' in path:
            raise AssertionError('Runtime data access')
sys.addaudithook(audit)
stub=types.ModuleType('workers')
stub.Response=Response
stub.WorkerEntrypoint=object
stub.wsgi=object()
sys.modules['workers']=stub
import worker
assert worker.ORIGINAL_WSGI is worker.app_module._flask_wsgi_app
assert worker.app.wsgi_app is worker.app_module.runtime_wsgi_app
assert not worker.app_module._runtime_initialized
assert worker.app.secret_key is None
client=Client(worker.safe_wsgi,Response)
for slug,spec in worker.TEMPLATES.items():
    for mode in ('load','compile'):
        response=client.get(f'/jinja/{slug}/{mode}')
        assert response.status_code==200,(slug,mode,response.status_code)
    response=client.get(f'/jinja/{slug}/render')
    assert response.status_code==(200 if spec['render'] else 409),(slug,response.status_code)
    if spec['render']:
        assert response.headers['X-Jinja-Template']==spec['name']
        assert response.headers['X-Jinja-Mode']=='render'
for path in ['/','/viewer','/admin','/register','/match','/match/result','/api/participants']:
    assert path not in worker.ALLOWED
assert not worker.app_module._runtime_initialized
assert worker.app.secret_key is None
'''
    environment = dict(
        os.environ,
        PYTHONPATH=str(build / "src"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_measurement_plan_has_required_sequences():
    sys.path.insert(0, str(POC))
    try:
        measure = load_module("jinja_poc_measure", POC / "measure.py")
    finally:
        sys.path.pop(0)
    plan = measure.build_plan()
    repeats = [item for item in plan if item["phase"] == "representative-repeat"]
    round_robin = [item for item in plan if item["phase"] == "round-robin"]
    assert len(repeats) == 4 * 100
    assert len(round_robin) == 160
    assert {item["cadence"] for item in repeats} == {
        "continuous",
        "spaced-2s",
        "after-15s-idle",
        "after-30s-idle",
    }
    assert all(item["mode"] == "render" for item in round_robin)


def test_tail_sanitization_discards_headers_ip_and_location(tmp_path):
    module = load_module("jinja_poc_tail", POC / "collect_tail.py")
    raw = tmp_path / "tail.log"
    raw.write_text(
        "startup\n"
        + json.dumps(
            {
                "outcome": "exception",
                "cpuTime": 3,
                "wallTime": 7,
                "scriptVersion": {"id": "version"},
                "exceptions": [{"name": "ErrnoError", "message": "#<Object>"}],
                "event": {
                    "request": {
                        "url": "https://example.workers.dev/jinja/x/load?probe=workers-dev-1",
                        "headers": {
                            "cf-ray": "safe-ray",
                            "cf-connecting-ip": "private-ip",
                            "authorization": "private-token",
                        },
                        "cf": {"city": "private-city"},
                    },
                    "response": {"status": 500},
                },
            }
        )
    )
    rows = module.collect(raw)
    assert rows[0]["probe"] == "workers-dev-1"
    assert rows[0]["cpu_ms"] == 3
    assert "private-" not in json.dumps(rows)
