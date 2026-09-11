"""Validate the isolated runtime SQLite PoC without touching repository data."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
POC = ROOT / "cloudflare-runtime-sqlite-poc"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def build_in(tmp_path):
    sys.path.insert(0, str(POC))
    try:
        builder = load_module("runtime_sqlite_builder", POC / "build.py")
        return builder.build(tmp_path / "build", tmp_path / "manifest.json")
    finally:
        sys.path.pop(0)


def test_real_runtime_wrapper_uses_only_disposable_database(tmp_path):
    real_database = ROOT / "instance" / "participants.db"
    before = fingerprint(real_database)
    build = build_in(tmp_path)
    code = f'''
import asyncio,json,os,sqlite3,sys,types
from pathlib import Path
from werkzeug.test import Client
from werkzeug.wrappers import Response

repo_database = Path({str(real_database)!r}).resolve()
def audit(event,args):
    if event == "sqlite3.connect" and Path(os.path.abspath(os.fspath(args[0]))).resolve() == repo_database:
        raise AssertionError("real repository database access")
sys.addaudithook(audit)

stub=types.ModuleType("workers")
stub.Response=Response
stub.WorkerEntrypoint=object
stub.wsgi=types.SimpleNamespace()
sys.modules["workers"]=stub
import worker

assert worker.app.wsgi_app is worker.app_module.runtime_wsgi_app
assert worker.EXPECTED_DB == Path(worker.app.instance_path, "participants.db").resolve()
assert worker.EXPECTED_DB != repo_database
assert worker.app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///" + str(worker.EXPECTED_DB)
assert not worker.app_module._runtime_initialized
assert worker.app.secret_key is not None
assert not worker.EXPECTED_DB.exists()

client=Client(worker.app.wsgi_app,Response)
status=client.get("/runtime-poc/status")
assert status.status_code == 200
assert status.json["runtime_initialized"] is True
assert status.json["secret_key_set"] is True
assert status.json["db_exists"] is True
assert status.json["db_is_disposable"] is True
assert status.json["participant_count"] == 0
assert status.json["table_count"] >= 1
assert client.get("/api/participants").json == []
assert client.get("/runtime-poc/count").json["participant_count"] == 0
first=client.get("/runtime-poc/seed").json
second=client.get("/runtime-poc/seed").json
assert first == {{"created": True, "participant_count": 1}}
assert second == {{"created": False, "participant_count": 1}}
rows=client.get("/api/participants").json
assert rows == [{{"id": 1, "name": "Runtime PoC Player", "gender": "male", "level": "intermediate", "active": True}}]
assert client.get("/runtime-poc/count").json["participant_count"] == 1

try:
    sqlite3.connect(repo_database)
except AssertionError as error:
    assert str(error) == "real repository database access"
else:
    raise AssertionError("outer audit hook allowed the real repository database")

for forbidden in [Path("other.db"), Path({str(ROOT / 'config.json')!r}), Path({str(ROOT / 'match_state.json')!r})]:
    try:
        if forbidden.suffix == ".db": sqlite3.connect(forbidden)
        else: forbidden.read_bytes()
    except worker.side_effect_guard.PocBoundaryViolation:
        pass
    else:
        raise AssertionError(f"guard allowed {{forbidden.name}}")
'''
    environment = dict(os.environ, PYTHONPATH=str(build / "src"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], cwd=tmp_path,
        env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert fingerprint(real_database) == before


def test_source_allowlist_and_worker_boundary(tmp_path):
    build = build_in(tmp_path)
    config = json.loads((build / "wrangler.jsonc").read_text())
    assert config["name"] == "shuttlers-match-runtime-poc"
    assert config["workers_dev"] and not config["preview_urls"]
    assert "routes" not in config and "route" not in config
    assert not any(key in config for key in ("d1_databases", "r2_buckets", "kv_namespaces", "durable_objects", "limits"))
    assert not list(build.rglob("*.db*"))
    assert not list(build.rglob("config.json"))
    assert not list(build.rglob("match_state.json"))
    assert not list(build.rglob("draft_state.json"))
    assert not list(build.rglob("history_dumps"))
    worker = (POC / "src" / "worker.py").read_text()
    assert "from app import app" in worker
    assert "wsgi.fetch(app.wsgi_app, request, self.env)" in worker
    assert "_flask_wsgi_app" not in worker
    assert "app.instance_path =" not in worker
    assert "SQLALCHEMY_DATABASE_URI\"] =" not in worker
    for path in ("/", "/register", "/match", "/admin", "/admin/settings", "/api/match_state", "/line/"):
        assert path not in {
            "/api/participants", "/runtime-poc/status", "/runtime-poc/count", "/runtime-poc/seed"
        }


def test_measurement_plan_and_tail_sanitization(tmp_path):
    measure = load_module("runtime_sqlite_measure", POC / "measure.py")
    plan = measure.build_plan()
    repeated_api = [item for item in plan if item["phase"] in ("repeat", "spaced") and item["path"] == "/api/participants"]
    repeated_count = [item for item in plan if item["phase"] in ("repeat", "spaced") and item["path"] == "/runtime-poc/count"]
    assert len(repeated_api) == len(repeated_count) == 55
    assert {item["cadence"] for item in plan} >= {"continuous", "spaced-2s", "after-15s-idle", "after-30s-idle"}

    collector = load_module("runtime_sqlite_tail", POC / "collect_tail.py")
    raw = tmp_path / "tail.log"
    raw.write_text(json.dumps({
        "outcome": "ok", "cpuTime": 3, "wallTime": 7,
        "scriptVersion": {"id": "version"}, "exceptions": [],
        "event": {
            "request": {
                "url": "https://example.workers.dev/runtime-poc/count?probe=runtime-test-1",
                "headers": {"cf-ray": "safe-ray", "authorization": "private-token", "cf-connecting-ip": "private-ip"},
                "cf": {"city": "private-city"},
            },
            "response": {"status": 200},
        },
    }))
    rows = collector.collect(raw)
    assert rows[0]["probe"] == "runtime-test-1"
    assert rows[0]["cpu_ms"] == 3
    assert "private-" not in json.dumps(rows)
