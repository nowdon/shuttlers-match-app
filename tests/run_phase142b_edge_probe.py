"""One-hop, synthetic-only Phase 14.2B edge probe.

Deploys a minimal Python Worker first, then the staged Flask Worker with a
fresh disposable D1. Both deployments use the same disposable workers.dev
name and a temporary header gate; no Worker ever fetches another Worker.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from migration.export_sqlite import export_snapshot
from migration.fixture import create_synthetic_fixture
from migration.import_plan import write_import_plan
from migration.manifest import write_json
from migration.remote import RemoteWrangler
from migration.snapshot import create_snapshot
from scripts.prepare_worker_bundle import prepare


def _run(wrangler, args, directory, *, cwd=None, redactions=()):
    env = dict(os.environ, WRANGLER_LOG_PATH=str(directory / "wrangler.log"),
               WRANGLER_SEND_METRICS="false")
    completed = subprocess.run([str(wrangler), *args], cwd=cwd, env=env,
                               capture_output=True, text=True)
    if completed.returncode:
        codes = re.findall(r"\[code:\s*(\d+)\]", completed.stderr)
        detail = re.sub(r"\x1b\[[0-9;]*m", "", completed.stderr)
        for secret in redactions:
            detail = detail.replace(secret, "[REDACTED]")
        raise RuntimeError(
            f"Wrangler {args[0]} failed (code {codes[-1] if codes else 'unknown'}): "
            + detail[-1400:]
        )
    return completed.stdout


def _request(url, token, path, *, method="GET", form=None):
    with tempfile.TemporaryDirectory(prefix="phase142b-http-") as temporary:
        headers = Path(temporary) / "headers"
        body = Path(temporary) / "body"
        cmd = ["curl", "--silent", "--show-error", "--max-time", "30",
               "--dump-header", str(headers), "--output", str(body),
               "--write-out", "%{http_code}|%{content_type}|%{time_total}",
               "--config", "-", url + path]
        if method != "GET":
            cmd[1:1] = ["--request", method]
        for key, value in (form or {}).items():
            cmd[1:1] = ["--data-urlencode", f"{key}={value}"]
        # The secret never appears in process arguments or the persisted report.
        config = f'header = "X-Phase14-Smoke-Token: {token}"\n'
        done = subprocess.run(cmd, input=config, text=True, capture_output=True)
        if done.returncode:
            raise RuntimeError("Worker HTTP request failed")
        status, content_type, wall = done.stdout.split("|", 2)
        header_text = headers.read_text(encoding="utf-8", errors="replace")
        ray = re.search(r"^cf-ray:\s*(\S+)", header_text, re.I | re.M)
        error_type = re.search(r"^cf-error-type:\s*(\S+)", header_text, re.I | re.M)
        version_marker = re.search(r"^x-phase14-version:\s*(\S+)", header_text, re.I | re.M)
        d1_queries = re.search(r"^x-phase14-d1-queries:\s*(\d+)", header_text, re.I | re.M)
        config_loads = re.search(r"^x-phase14-config-loads:\s*(\d+)", header_text, re.I | re.M)
        templates = re.search(r"^x-phase14-template-loads:\s*(\d+)", header_text, re.I | re.M)
        request_count = re.search(r"^x-phase14-isolate-requests:\s*(\d+)", header_text, re.I | re.M)
        content = body.read_bytes()
        error = re.search(rb"(?:Error|code:)\s*(1\d{3})", content, re.I)
        result = {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
                  "path": path, "method": method, "status": int(status),
                  "cf_ray": ray.group(1) if ray else None,
                "cf_error_type": error_type.group(1) if error_type else None,
                "version_marker": version_marker.group(1) if version_marker else None,
                "content_type": content_type, "wall_ms_client": round(float(wall) * 1000, 2),
                "bytes": len(content),
                "d1_queries": int(d1_queries.group(1)) if d1_queries else None,
                "config_loads": int(config_loads.group(1)) if config_loads else None,
                "template_loads": int(templates.group(1)) if templates else None,
                "isolate_requests": int(request_count.group(1)) if request_count else None,
                "error_code": error.group(1).decode("ascii") if error else None}
        if int(status) >= 500 or error is not None:
            result["error_body_excerpt"] = content[:200].decode("utf-8", errors="replace")
        if path == "/__phase14/d1-marker" and int(status) == 200:
            result["marker"] = json.loads(content).get("participant_count")
        if path == "/__phase14/r2-keys" and int(status) == 200:
            result["keys"] = json.loads(content).get("keys")
        if path == "/admin/match_history_archives" and int(status) == 200:
            filenames = re.findall(rb"match_history_manual_dump_\d{8}_\d{6}_\d{6}\.json", content)
            result["archive_filenames"] = sorted(set(x.decode("ascii") for x in filenames))
        return result


def _start_tail(wrangler, name, directory, phase):
    log_path = directory / f"{phase}-tail.jsonl"
    log = log_path.open("w", encoding="utf-8")
    env = dict(os.environ, WRANGLER_LOG_PATH=str(directory / "wrangler-tail.log"),
               WRANGLER_SEND_METRICS="false")
    process = subprocess.Popen([str(wrangler), "tail", name, "--format=json"], env=env,
                               stdin=subprocess.DEVNULL, stdout=log,
                               stderr=subprocess.STDOUT, text=True)
    time.sleep(3)
    if process.poll() is not None:
        log.close()
        message = log_path.read_text(encoding="utf-8", errors="replace")
        raise RuntimeError("Wrangler tail exited before smoke: " + message[:1000])
    return process, log, log_path


def _stop_tail(process, log):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    log.close()


def _tail_summary(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    events = []
    decoder = json.JSONDecoder()
    position = 0
    while position < len(text):
        start = text.find("{", position)
        if start < 0:
            break
        try:
            event, position = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            position = start + 1
            continue
        if not isinstance(event, dict) or "scriptName" not in event:
            continue
        request = event.get("event") or {}
        if not isinstance(request, dict):
            request = {}
        request_details = request.get("request") or {}
        if not isinstance(request_details, dict):
            request_details = {}
        url = request_details.get("url")
        path_only = urlsplit(url).path if isinstance(url, str) else None
        values = {"outcome": event.get("outcome"), "cpu_ms": event.get("cpuTime"),
                  "wall_ms": event.get("wallTime"),
                  "path": path_only,
                  "cf_ray": request_details.get("headers", {}).get("cf-ray"),
                  "exceptions": event.get("exceptions"),
                  "logs": [{"message": item.get("message"), "level": item.get("level")}
                           for item in event.get("logs", []) if isinstance(item, dict)]}
        events.append(values)
    return events


def _base_config(name, token):
    return {"name": name, "main": "worker.py", "compatibility_date": "2026-10-01",
            "compatibility_flags": ["python_workers"], "workers_dev": True,
            "preview_urls": False,
            "observability": {"enabled": True, "logs": {"invocation_logs": True,
                                                    "head_sampling_rate": 1},
                              "traces": {"enabled": True}},
            "vars": {"PHASE14_SMOKE_TOKEN": token}}


def _version_id(output):
    match = re.search(r"(?:Current Version ID|Version ID):\s*([0-9a-f-]{36})", output, re.I)
    return match.group(1) if match else None


def _readiness(wrangler, name, directory, url, token, marker, version_id):
    """Require active version and ten consecutive matching edge responses."""
    status = json.loads(_run(wrangler, ["deployments", "status", "--name", name,
                                       "--json"], directory))
    active = [{"version_id": item.get("version_id"), "percentage": item.get("percentage")}
              for item in status.get("versions", [])]
    if not version_id or active != [{"version_id": version_id, "percentage": 100}]:
        raise RuntimeError("Disposable Worker active version differs from deploy")
    probes = []
    consecutive = 0
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and consecutive < 10:
        probe = _request(url, token, "/admin/settings")
        probes.append(probe)
        consecutive = consecutive + 1 if (probe["status"] == 200 and
                                           probe["version_marker"] == marker) else 0
        if consecutive < 10:
            time.sleep(1)
    if consecutive != 10:
        raise RuntimeError("Disposable Worker readiness gate did not pass")
    return {"deployed_version_id": version_id, "active_versions": active,
            "expected_marker": marker, "probes": probes, "passed": True}


def _minimal_worker(stage):
    (stage / "worker.py").write_text(
        "from urllib.parse import urlsplit\n"
        "from workers import WorkerEntrypoint, Response\n"
        "class Default(WorkerEntrypoint):\n"
        "    async def fetch(self, request):\n"
        "        if request.headers.get('X-Phase14-Smoke-Token') != self.env.PHASE14_SMOKE_TOKEN:\n"
        "            return Response('Not Found', status=404)\n"
        "        return Response('phase142b-minimal:' + urlsplit(request.url).path)\n",
        encoding="utf-8",
    )


def _guard_app_worker(stage):
    path = stage / "worker.py"
    code = path.read_text(encoding="utf-8")
    code = code.replace("from workers import WorkerEntrypoint, wsgi",
                        "from workers import Response, WorkerEntrypoint, wsgi")
    marker = "    async def fetch(self, request):\n"
    if code.count(marker) != 1:
        raise RuntimeError("Worker guard insertion point changed")
    guard = '''    async def fetch(self, request):
        if request.headers.get("X-Phase14-Smoke-Token") != self.env.PHASE14_SMOKE_TOKEN:
            return Response("Not Found", status=404)
        if urlsplit(request.url).path == "/__phase14/d1-marker":
            row = await self.env.DB.prepare("SELECT COUNT(*) AS n FROM participants").first()
            return Response.json({"participant_count": row.n})
        if urlsplit(request.url).path == "/__phase14/r2-keys":
            page = await self.env.HISTORY_ARCHIVES.list(prefix="history_dumps/")
            return Response.json({"keys": sorted(item.key for item in page.objects),
                                  "truncated": page.truncated})
'''
    path.write_text(code.replace(marker, guard), encoding="utf-8")


def _instrument_app_stage(stage):
    """Add counters and SQLite poison only to the disposable staging copy."""
    d1_path = stage / "storage/d1.py"
    d1 = d1_path.read_text(encoding="utf-8")
    d1 = d1.replace("        self._binding = binding\n",
                    "        self._phase14_queries = 0\n        self._binding = binding\n", 1)
    d1 = d1.replace("    def _statement(self, sql, params):\n",
                    "    def _statement(self, sql, params):\n"
                    "        self._phase14_queries += 1\n", 1)
    d1_path.write_text(d1, encoding="utf-8")

    sqlite_path = stage / "storage/sqlite.py"
    sqlite = sqlite_path.read_text(encoding="utf-8")
    sqlite = sqlite.replace("    def __init__(self, database_path=None, *, connection=None):\n",
                            "    def __init__(self, database_path=None, *, connection=None):\n"
                            "        raise RuntimeError('phase14_sqlite_fallback_forbidden')\n", 1)
    sqlite_path.write_text(sqlite, encoding="utf-8")

    config_path = stage / "utils/config.py"
    config = config_path.read_text(encoding="utf-8")
    config = config.replace("from flask import current_app, has_app_context",
                            "from flask import current_app, g, has_app_context, has_request_context")
    config = config.replace("def load_raw_config_with_version():\n",
                            "def load_raw_config_with_version():\n"
                            "    if has_request_context():\n"
                            "        g.phase14_config_loads = getattr(g, 'phase14_config_loads', 0) + 1\n", 1)
    config_path.write_text(config, encoding="utf-8")

    app_path = stage / "app.py"
    with app_path.open("a", encoding="utf-8") as output:
        output.write('''

# Disposable-only response counters; never copied to application source.
from flask import g, has_request_context
_phase14_get_template = app.jinja_env.get_template


def _phase14_tracked_template(*args, **kwargs):
    if has_request_context():
        g.phase14_template_loads = getattr(g, 'phase14_template_loads', 0) + 1
    return _phase14_get_template(*args, **kwargs)


app.jinja_env.get_template = _phase14_tracked_template


@app.after_request
def _phase14_response_counters(response):
    storage = getattr(g, '_storage_provider_instance', None)
    response.headers['X-Phase14-D1-Queries'] = str(getattr(storage, '_phase14_queries', 0))
    response.headers['X-Phase14-Config-Loads'] = str(getattr(g, 'phase14_config_loads', 0))
    response.headers['X-Phase14-Template-Loads'] = str(getattr(g, 'phase14_template_loads', 0))
    response.headers['X-Phase14-Isolate-Requests'] = str(app.config.get('_PHASE14_REQUEST_COUNT', 0))
    response.headers['X-Phase14-Version'] = str(app.config.get('_PHASE14_VERSION', ''))
    return response
''')

    worker_path = stage / "worker.py"
    worker = worker_path.read_text(encoding="utf-8")
    worker = worker.replace("from app import app\n",
                            "from app import app\n"
                            "_phase14_request_count = 0\n", 1)
    worker = worker.replace("    async def fetch(self, request):\n",
                            "    async def fetch(self, request):\n"
                            "        global _phase14_request_count\n"
                            "        _phase14_request_count += 1\n"
                            "        app.config['_PHASE14_REQUEST_COUNT'] = _phase14_request_count\n", 1)
    worker = worker.replace("        app.config['_PHASE14_REQUEST_COUNT'] = _phase14_request_count\n",
                            "        app.config['_PHASE14_REQUEST_COUNT'] = _phase14_request_count\n"
                            "        app.config['_PHASE14_VERSION'] = self.env.PHASE14_VERSION\n", 1)
    worker_path.write_text(worker, encoding="utf-8")


def run(wrangler, bundle, report_dir, *, d1_only=False, final_stability=False):
    wrangler = Path(wrangler).resolve()
    bundle = Path(bundle).resolve()
    report_dir = Path(report_dir).resolve()
    if not (bundle / "python_modules").is_dir() or not (bundle / "pylock.toml").is_file():
        raise ValueError("Prebuilt synthetic Python dependency bundle is required")
    report_dir.mkdir(parents=True, exist_ok=True)
    name = "shuttlers-phase14-disposable-" + secrets.token_hex(4)
    token = secrets.token_urlsafe(32)
    report = {"resource": name, "phases": {}, "d1_created": False,
              "d1_deleted": False, "r2_created": False, "r2_deleted": False,
              "r2_keys": [], "worker_deploy_attempted": False,
              "worker_deleted": False, "failure": None}
    report_path = report_dir / f"{name}-edge-report.json"
    with tempfile.TemporaryDirectory(prefix="phase142b-edge-") as temporary:
        directory = Path(temporary)
        tail_process = tail_log = None
        try:
            basic = directory / "basic"
            basic.mkdir()
            config = _base_config(name, token)
            (basic / "wrangler.jsonc").write_text(json.dumps(config), encoding="utf-8")
            _minimal_worker(basic)
            shutil.copytree(bundle / "python_modules", basic / "python_modules")
            shutil.copy2(bundle / "pylock.toml", basic / "pylock.toml")
            report["worker_deploy_attempted"] = True
            deployed = _run(wrangler, ["deploy", "--config", str(basic / "wrangler.jsonc")],
                            directory, cwd=basic, redactions=(token,))
            match = re.search(r"https://[A-Za-z0-9.-]+\.workers\.dev", deployed)
            if not match:
                raise RuntimeError("Disposable Worker URL missing")
            url = match.group(0)
            tail_process, tail_log, tail_path = _start_tail(wrangler, name, directory, "basic")
            basic_requests = [_request(url, token, path)
                              for path in ("/", "/viewer", "/admin/settings",
                                           "/viewer", "/admin/settings", "/")]
            time.sleep(15)
            basic_requests.extend(_request(url, token, path)
                                  for path in ("/", "/viewer", "/admin/settings"))
            time.sleep(3)
            _stop_tail(tail_process, tail_log)
            tail_process = tail_log = None
            report["phases"]["worker_only"] = {"requests": basic_requests,
                                                "events": _tail_summary(tail_path)}
            write_json(report_path, report)

            fixture = create_synthetic_fixture(directory / "fixture")
            # Phase 10's C1-C4 card IDs are legal migration data but cannot be
            # rendered by this Flask app. Adapt only the disposable edge copy.
            with sqlite3.connect(fixture["database"]) as source:
                source.executemany("UPDATE participants SET card = ? WHERE id = ?",
                                   [("♣2", 1), ("♣3", 2), ("♣4", 3), ("♣5", 4)])
            snapshot = directory / "snapshot.db"
            create_snapshot(fixture["database"], snapshot)
            export = directory / "export.json"
            export_snapshot(snapshot, export, legacy_directory=fixture["source_directory"],
                            config_path=fixture["config"])
            plan = directory / "plan"
            write_import_plan(export, plan)
            created = _run(wrangler, ["d1", "create", name, "--location=apac"], directory)
            report["d1_created"] = True
            match = re.search(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                              created, re.I)
            if not match:
                raise RuntimeError("Disposable D1 UUID missing")
            database_id = match.group(0)
            migration_config = directory / "d1.jsonc"
            (directory / "placeholder.js").write_text("export default {fetch(){return new Response('ok')}};\n")
            shutil.copytree(ROOT / "migrations/d1", directory / "migrations")
            migration_config.write_text(json.dumps({
                "name": name, "main": "placeholder.js", "compatibility_date": "2026-10-01",
                "d1_databases": [{"binding": "DB", "database_name": name,
                                  "database_id": database_id,
                                  "migrations_dir": "migrations"}]}), encoding="utf-8")
            _run(wrangler, ["d1", "migrations", "apply", "DB", "--remote",
                            "--config", str(migration_config)], directory)
            remote = RemoteWrangler(wrangler, migration_config, name, name)
            remote.import_plan(export, plan)
            remote.validate(export)

            app_config = _base_config(name, token)
            app_config["vars"].update(SECRET_KEY=secrets.token_urlsafe(32),
                                      STORAGE_BACKEND="d1",
                                      HISTORY_ARCHIVE_BACKEND="filesystem",
                                      LINE_MESSAGING_ENABLED="false",
                                      PHASE14_VERSION="d1-" + secrets.token_hex(5))
            app_config["d1_databases"] = [{"binding": "DB", "database_name": name,
                                            "database_id": database_id}]
            private_config = directory / "app.jsonc"
            private_config.write_text(json.dumps(app_config), encoding="utf-8")
            app_stage = directory / "app"
            prepare(app_stage, private_config)
            # Cards and other assets are not involved in this first D1-only phase.
            shutil.rmtree(app_stage / "static")
            shutil.copytree(bundle / "python_modules", app_stage / "python_modules")
            shutil.copy2(bundle / "pylock.toml", app_stage / "pylock.toml")
            _guard_app_worker(app_stage)
            _instrument_app_stage(app_stage)
            deployed = _run(wrangler, ["deploy", "--config", str(app_stage / "wrangler.jsonc")],
                            directory, cwd=app_stage,
                            redactions=(token, app_config["vars"]["SECRET_KEY"]))
            tail_process, tail_log, tail_path = _start_tail(wrangler, name, directory, "app-d1")
            # A workers.dev deployment can briefly serve the prior revision.
            # Require an application-only response header before measuring it.
            readiness = _readiness(wrangler, name, directory, url, token,
                                   app_config["vars"]["PHASE14_VERSION"], _version_id(deployed))
            propagation = readiness["probes"]
            requests = [_request(url, token, path)
                        for path in ("/", "/viewer", "/admin/settings",
                                     "/__phase14/d1-marker")]
            if (any(item["d1_queries"] is None for item in requests[:-1]) or
                    any(item["status"] not in (200, 302) for item in requests) or
                    requests[-1].get("marker") != 4):
                raise RuntimeError("Initial application D1 smoke failed")
            mixed = ["/", "/admin/match_history", "/match/result",
                     "/viewer", "/admin/settings"] * 2
            repeated = [] if final_stability else [_request(url, token, path)
                        for path in (["/viewer"] * 10 + ["/admin/settings"] * 10 + mixed)]
            requests.extend(repeated)
            time.sleep(3)
            _stop_tail(tail_process, tail_log)
            tail_process = tail_log = None
            report["phases"]["app_d1"] = {"readiness": readiness,
                                          "propagation": propagation,
                                          "requests": requests,
                                          "events": _tail_summary(tail_path)}
            write_json(report_path, report)
            if any(item["d1_queries"] is None or item["status"] not in (200, 302)
                   or item["error_code"]
                   for item in repeated):
                raise RuntimeError("Application D1 repeated smoke failed")
            if d1_only:
                return report

            _run(wrangler, ["r2", "bucket", "create", name, "--location=apac"], directory)
            report["r2_created"] = True
            app_config["vars"]["HISTORY_ARCHIVE_BACKEND"] = "r2"
            app_config["vars"]["PHASE14_VERSION"] = "r2-" + secrets.token_hex(5)
            app_config["r2_buckets"] = [{"binding": "HISTORY_ARCHIVES",
                                          "bucket_name": name}]
            (app_stage / "wrangler.jsonc").write_text(json.dumps(app_config), encoding="utf-8")
            deployed = _run(wrangler, ["deploy", "--config", str(app_stage / "wrangler.jsonc")],
                            directory, cwd=app_stage,
                            redactions=(token, app_config["vars"]["SECRET_KEY"]))
            tail_process, tail_log, tail_path = _start_tail(wrangler, name, directory, "app-r2")
            r2_readiness = _readiness(wrangler, name, directory, url, token,
                                      app_config["vars"]["PHASE14_VERSION"], _version_id(deployed))
            r2_requests = [_request(url, token, path)
                           for path in ("/", "/viewer", "/admin/settings")]
            r2_requests.append(_request(url, token, "/admin/match_history/dump", method="POST"))
            archive_list = _request(url, token, "/admin/match_history_archives")
            r2_requests.append(archive_list)
            for filename in archive_list.get("archive_filenames", []):
                r2_requests.append(_request(
                    url, token, "/admin/match_history_archives/" + filename))
            key_list = _request(url, token, "/__phase14/r2-keys")
            r2_requests.append(key_list)
            report["r2_keys"] = key_list.get("keys") or []
            time.sleep(3)
            _stop_tail(tail_process, tail_log)
            tail_process = tail_log = None
            report["phases"]["app_r2"] = {"readiness": r2_readiness,
                                          "requests": r2_requests,
                                          "events": _tail_summary(tail_path)}
            write_json(report_path, report)
            if (r2_requests[3]["status"] != 302 or
                    archive_list["status"] != 200 or
                    not archive_list.get("archive_filenames") or
                    key_list["status"] != 200 or
                    any(item["status"] != 200 for item in r2_requests[5:-1])):
                raise RuntimeError("R2 Flask archive smoke failed")

            # These are disposable diagnostic assets only; card packaging is
            # deliberately outside this task.
            static = app_stage / "static"
            cards = static / "cards"
            cards.mkdir(parents=True)
            shutil.copy2(ROOT / "static/participants_template.csv", static)
            shutil.copy2(ROOT / "static/cards/c5.png", cards)
            (static / "phase14-diagnostic.css").write_text(
                "body { color: #123456; }\n", encoding="utf-8")
            app_config["assets"] = {"directory": "static", "binding": "ASSETS",
                                    "run_worker_first": True}
            app_config["vars"]["PHASE14_VERSION"] = "static-" + secrets.token_hex(5)
            (app_stage / "wrangler.jsonc").write_text(json.dumps(app_config), encoding="utf-8")
            deployed = _run(wrangler, ["deploy", "--config", str(app_stage / "wrangler.jsonc")],
                            directory, cwd=app_stage,
                            redactions=(token, app_config["vars"]["SECRET_KEY"]))
            tail_process, tail_log, tail_path = _start_tail(wrangler, name, directory, "app-static")
            static_readiness = _readiness(wrangler, name, directory, url, token,
                                          app_config["vars"]["PHASE14_VERSION"], _version_id(deployed))
            static_requests = [_request(url, token, path)
                               for path in ("/static/participants_template.csv",
                                            "/static/phase14-diagnostic.css",
                                            "/static/cards/c5.png", "/viewer")]
            if final_stability:
                routes = ["/viewer", "/admin/settings", "/admin/match_history",
                          "/match/result"]
                stability = [_request(url, token, path)
                             for path in routes for _ in range(20)]
                report["phases"]["final_stability"] = {"requests": stability,
                                                          "readiness": static_readiness}
                write_json(report_path, report)
                if any(item["status"] != 200 or item["error_code"] or
                       item["version_marker"] != app_config["vars"]["PHASE14_VERSION"]
                       for item in stability):
                    raise RuntimeError("80-request stable application smoke failed")

                mutation = []
                mutation.append(_request(url, token, "/register", method="POST", form={
                    "name": "Phase14 Synthetic Edge", "gender": "male",
                    "level": "beginner", "card": "♣6", "mode": "admin"}))
                registered = remote.query("SELECT COUNT(*) AS n FROM participants WHERE card='♣6'")
                mutation.append(_request(url, token, "/match", method="POST",
                                         form={"court_count": "1", "mode": "admin"}))
                mutation.append(_request(url, token, "/match/confirm", method="POST",
                                         form={"mode": "admin"}))
                newest = remote.query("SELECT MAX(id) AS id FROM match_histories")
                match_id = newest[0]["id"] if newest else None
                if not isinstance(match_id, int) or match_id <= 11:
                    raise RuntimeError("Application confirm did not create synthetic history")
                mutation.append(_request(url, token, f"/match/result/{match_id}/score",
                                         method="POST", form={"mode": "admin",
                                                              "winner_team": "1"}))
                scored = remote.query(f"SELECT winner_team FROM match_histories WHERE id={match_id}")
                mutation.append(_request(url, token, "/admin/match_history/dump", method="POST"))
                archive_list = _request(url, token, "/admin/match_history_archives")
                mutation.append(archive_list)
                archive_names = archive_list.get("archive_filenames") or []
                if not archive_names:
                    raise RuntimeError("Flask archive list contains no synthetic dump")
                mutation.append(_request(url, token,
                                         "/admin/match_history_archives/" + archive_names[-1]))
                mutation.append(_request(url, token, "/match/revert_to_draft",
                                         method="POST", form={"mode": "admin"}))
                reverted = remote.query(f"SELECT COUNT(*) AS n FROM match_histories WHERE id={match_id}")
                keys_after = _request(url, token, "/__phase14/r2-keys")
                report["r2_keys"] = keys_after.get("keys") or report["r2_keys"]
                report["phases"]["mutation"] = {
                    "requests": mutation + [keys_after],
                    "registered": registered[0]["n"] if registered else None,
                    "new_match_id": match_id,
                    "saved_winner_team": scored[0]["winner_team"] if scored else None,
                    "remaining_match_rows_after_revert": reverted[0]["n"] if reverted else None,
                    "archive_count": len(archive_names),
                }
                write_json(report_path, report)
                if (any(item["status"] not in (200, 302) or item["error_code"]
                        for item in mutation) or
                        registered != [{"n": 1}] or
                        scored != [{"winner_team": 1}] or
                        reverted != [{"n": 0}] or
                        keys_after["status"] != 200):
                    raise RuntimeError("Synthetic Flask mutation flow failed")
            time.sleep(3)
            _stop_tail(tail_process, tail_log)
            tail_process = tail_log = None
            report["phases"]["app_static"] = {"readiness": static_readiness,
                                              "requests": static_requests,
                                              "events": _tail_summary(tail_path)}
            if final_stability:
                events = report["phases"]["app_static"]["events"]
                report["phases"]["final_stability"]["events"] = events
            write_json(report_path, report)
        except Exception as error:
            report["failure"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            if tail_process is not None:
                _stop_tail(tail_process, tail_log)
            write_json(report_path, report)
            if report["r2_created"] and "url" in locals():
                try:
                    latest = _request(url, token, "/__phase14/r2-keys")
                    if latest["status"] == 200:
                        report["r2_keys"] = sorted(set(report["r2_keys"])
                                                   | set(latest.get("keys") or []))
                except Exception:
                    pass
            if report["worker_deploy_attempted"]:
                try:
                    _run(wrangler, ["delete", name, "--force"], directory)
                    report["worker_deleted"] = True
                except Exception:
                    pass
            if report["r2_created"]:
                try:
                    for key in report["r2_keys"]:
                        _run(wrangler, ["r2", "object", "delete", f"{name}/{key}",
                                        "--remote"], directory)
                    _run(wrangler, ["r2", "bucket", "delete", name], directory)
                    listed = _run(wrangler, ["r2", "bucket", "list"], directory)
                    report["r2_deleted"] = name not in listed
                except Exception:
                    pass
            if report["d1_created"]:
                try:
                    _run(wrangler, ["d1", "delete", name, "--skip-confirmation"], directory)
                    listed = json.loads(_run(wrangler, ["d1", "list", "--json"], directory))
                    report["d1_deleted"] = not any(item.get("name") == name for item in listed)
                except Exception:
                    pass
            write_json(report_path, report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("wrangler", type=Path)
    parser.add_argument("--base-bundle", type=Path, required=True)
    parser.add_argument("--report-directory", type=Path, required=True)
    parser.add_argument("--d1-only", action="store_true")
    parser.add_argument("--final-stability", action="store_true")
    args = parser.parse_args()
    result = run(args.wrangler, args.base_bundle, args.report_directory,
                 d1_only=args.d1_only, final_stability=args.final_stability)
    print(json.dumps({"resource": result["resource"], "phases": list(result["phases"]),
                      "failure": result["failure"], "worker_deleted": result["worker_deleted"],
                      "d1_deleted": result["d1_deleted"], "r2_deleted": result["r2_deleted"]},
                     sort_keys=True))
