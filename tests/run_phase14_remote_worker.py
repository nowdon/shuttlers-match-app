"""Restricted synthetic Python Worker smoke with disposable D1 and R2.

The staged-only header gate and diagnostic paths are never copied to the
application source. Every resource name is randomized and disposable.
"""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from migration.archives import build_archive_plan
from migration.export_sqlite import export_snapshot
from migration.fixture import create_synthetic_fixture
from migration.import_plan import write_import_plan
from migration.manifest import write_json
from migration.remote import RemoteWrangler
from migration.snapshot import create_snapshot
from scripts.prepare_worker_bundle import prepare


def _run(wrangler, args, run_dir, *, cwd=None, input_text=None):
    env = dict(os.environ)
    env["WRANGLER_LOG_PATH"] = str(run_dir / "wrangler.log")
    env["WRANGLER_SEND_METRICS"] = "false"
    result = subprocess.run(
        [str(wrangler), *args], cwd=cwd, input=input_text,
        capture_output=True, text=True, env=env,
    )
    if result.returncode:
        codes = re.findall(r"\[code:\s*(\d+)\]", result.stderr)
        code = codes[-1] if codes else "unknown"
        raise RuntimeError(f"Wrangler {args[0]} failed (Cloudflare code {code})")
    return result.stdout


def _request(url, token=None):
    # The host's Python urllib path receives a synthetic 1010 from its network
    # proxy for Cloudflare domains, while curl reaches the same sites normally.
    # Feed the disposable header via stdin so it is absent from process args.
    with tempfile.TemporaryDirectory(prefix="phase14-http-") as directory:
        body = Path(directory) / "body"
        command = ["curl", "--silent", "--show-error", "--max-time", "30",
                   "--output", str(body), "--write-out", "%{http_code}|%{content_type}"]
        input_text = None
        if token:
            command.extend(("--config", "-"))
            input_text = f'header = "X-Phase14-Smoke-Token: {token}"\n'
        command.append(url)
        result = subprocess.run(command, input=input_text, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError("Worker HTTP request failed")
        status, content_type = result.stdout.split("|", 1)
        return int(status), content_type, body.read_bytes()


def _stage_guard(stage):
    source = stage / "worker.py"
    code = source.read_text(encoding="utf-8")
    code = code.replace(
        "from workers import WorkerEntrypoint, wsgi",
        "from workers import Response, WorkerEntrypoint, wsgi",
    )
    marker = "    async def fetch(self, request):\n"
    if code.count(marker) != 1:
        raise RuntimeError("Worker guard insertion point changed")
    gate = '''    async def fetch(self, request):
        if request.headers.get("X-Phase14-Smoke-Token") != self.env.PHASE14_SMOKE_TOKEN:
            return Response("Not Found", status=404)
        path = urlsplit(request.url).path
        if path == "/__phase14/r2-key-set":
            result = await self.env.HISTORY_ARCHIVES.list()
            return Response.json({"keys": sorted(item.key for item in result.objects),
                                  "truncated": result.truncated})
        if path == "/__phase14/d1-marker":
            count = await self.env.DB.prepare(
                "SELECT COUNT(*) AS n FROM participants").first("n")
            return Response.json({"participant_count": count})
'''
    source.write_text(code.replace(marker, gate), encoding="utf-8")


def run(wrangler, base_bundle, report_directory, *, remote_dev=False):
    wrangler = Path(wrangler).resolve()
    base_bundle = Path(base_bundle).resolve()
    report_directory = Path(report_directory).resolve()
    if not (base_bundle / "python_modules").is_dir() or not (base_bundle / "pylock.toml").is_file():
        raise ValueError("Previously built Python dependency bundle is missing")
    report_directory.mkdir(parents=True, exist_ok=True)
    name = f"shuttlers-phase14-disposable-{secrets.token_hex(4)}"
    token = secrets.token_urlsafe(32)
    report = {"resource": name, "d1_created": False, "r2_created": False,
              "worker_deploy_attempted": False, "worker_deployed": False,
              "worker_url": None, "http": {}, "r2_key_set": None,
              "d1_marker": None, "worker_deleted": False,
              "remote_preview_started": False, "remote_preview_stopped": False,
              "r2_deleted": False, "d1_deleted": False, "failure": None}
    report_path = report_directory / f"{name}-worker-report.json"
    with tempfile.TemporaryDirectory(prefix="phase14-remote-worker-") as temporary:
        run_dir = Path(temporary)
        fixture = create_synthetic_fixture(run_dir / "fixture")
        snapshot = run_dir / "snapshot.db"
        create_snapshot(fixture["database"], snapshot)
        export = run_dir / "export.json"
        export_snapshot(snapshot, export, legacy_directory=fixture["source_directory"],
                        config_path=fixture["config"])
        plan_dir = run_dir / "import-plan"
        write_import_plan(export, plan_dir)
        archive_plan = build_archive_plan(fixture["archives"])
        config = run_dir / "wrangler.jsonc"
        (run_dir / "worker.js").write_text(
            "export default { fetch() { return new Response('disposable'); } };\n",
            encoding="utf-8",
        )
        shutil.copytree(ROOT / "migrations/d1", run_dir / "migrations")
        stage = run_dir / "stage"
        preview_process = None
        preview_log = None
        try:
            created = _run(wrangler, ["d1", "create", name, "--location=apac"], run_dir)
            report["d1_created"] = True
            match = re.search(
                r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                created, re.IGNORECASE,
            )
            if not match:
                raise RuntimeError("D1 create succeeded but UUID was not returned")
            database_id = match.group(0)
            config.write_text(json.dumps({
                "name": name, "main": "worker.js", "compatibility_date": "2026-10-01",
                "d1_databases": [{"binding": "DB", "database_name": name,
                                  "database_id": database_id,
                                  "migrations_dir": "migrations"}],
            }), encoding="utf-8")
            _run(wrangler, ["d1", "migrations", "apply", "DB", "--remote",
                            "--config", str(config)], run_dir)
            remote = RemoteWrangler(wrangler, config, name, name)
            remote.import_plan(export, plan_dir)
            remote.validate(export)
            _run(wrangler, ["r2", "bucket", "create", name, "--location=apac"], run_dir)
            report["r2_created"] = True
            remote.copy_validate_r2(archive_plan)

            worker_config = json.loads((ROOT / "wrangler.production.example.jsonc").read_text())
            worker_config.update(name=name, workers_dev=not remote_dev, preview_urls=False)
            worker_config["d1_databases"][0].update(database_name=name, database_id=database_id)
            worker_config["r2_buckets"][0]["bucket_name"] = name
            worker_config["vars"].update(SECRET_KEY=secrets.token_urlsafe(32),
                                          PHASE14_SMOKE_TOKEN=token)
            private_config = run_dir / "worker-config.jsonc"
            private_config.write_text(json.dumps(worker_config), encoding="utf-8")
            prepare(stage, private_config)
            shutil.copytree(base_bundle / "python_modules", stage / "python_modules")
            shutil.copy2(base_bundle / "pylock.toml", stage / "pylock.toml")
            _stage_guard(stage)
            if remote_dev:
                environment = dict(os.environ)
                environment["WRANGLER_LOG_PATH"] = str(run_dir / "wrangler-dev.log")
                environment["WRANGLER_SEND_METRICS"] = "false"
                preview_log = (run_dir / "remote-preview.log").open("w", encoding="utf-8")
                preview_process = subprocess.Popen(
                    [str(wrangler), "dev", "--remote", "--ip", "127.0.0.1",
                     "--port", "8792", "--config", str(stage / "wrangler.jsonc")],
                    cwd=stage, stdin=subprocess.DEVNULL, stdout=preview_log,
                    stderr=subprocess.STDOUT, text=True, env=environment,
                )
                report["remote_preview_started"] = True
                url = "http://127.0.0.1:8792"
                for _ in range(20):
                    if preview_process.poll() is not None:
                        raise RuntimeError("Wrangler remote preview exited before HTTP")
                    try:
                        _request(url + "/")
                        break
                    except RuntimeError:
                        time.sleep(3)
                else:
                    raise RuntimeError("Wrangler remote preview did not open localhost")
            else:
                report["worker_deploy_attempted"] = True
                deployed = _run(
                    wrangler, ["deploy", "--config", str(stage / "wrangler.jsonc")],
                    run_dir, cwd=stage,
                )
                report["worker_deployed"] = True
                url_match = re.search(r"https://[A-Za-z0-9.-]+\.workers\.dev", deployed)
                if not url_match:
                    raise RuntimeError("Worker URL missing from deployment output")
                url = url_match.group(0)
            report["worker_url"] = url
            for attempt in range(6):
                status, content_type, body = _request(url + "/")
                auth_status, auth_content_type, auth_body = _request(url + "/", token)
                if status == 404 and body == b"Not Found" and auth_status in (200, 302):
                    break
                if attempt < 5:
                    time.sleep(3)
            report["http"]["unauthorized"] = status
            error_code = re.search(rb"(?:Error|code:)\s*(\d{3,5})", body, re.I)
            report["http"]["unauthorized_error_code"] = (
                error_code.group(1).decode("ascii") if error_code else None)
            if status != 404:
                report["http"]["unauthorized_content_type"] = content_type
            error_code = re.search(rb"(?:Error|code:)\s*(\d{3,5})", auth_body, re.I)
            report["http"]["authorized_root"] = {"status": auth_status,
                                                   "content_type": auth_content_type,
                                                   "bytes": len(auth_body),
                                                   "error_code": (error_code.group(1).decode("ascii")
                                                                  if error_code else None)}
            if auth_status not in (200, 302):
                raise RuntimeError("Worker protected root was not reachable")
            if status != 404 or body != b"Not Found":
                raise RuntimeError("Worker header gate did not return expected 404")
            failed_paths = []
            for path in ("/", "/viewer", "/admin/settings", "/match/result",
                         "/api/participants", "/static/participants_template.csv",
                         "/static/cards/c5.png"):
                for attempt in range(3):
                    status, content_type, body = _request(url + path, token)
                    error_code = re.search(rb"(?:Error|code:)\s*(\d{3,5})", body, re.I)
                    if not error_code or attempt == 2:
                        break
                    time.sleep(2)
                report["http"][path] = {"status": status, "content_type": content_type,
                                        "bytes": len(body),
                                        "error_code": (error_code.group(1).decode("ascii")
                                                       if error_code else None)}
                expected_type = ("text/csv" if path.endswith(".csv") else
                                 "image/png" if path.endswith(".png") else None)
                if (status not in (200, 302) or (status == 200 and not body) or
                        (expected_type and
                         (status != 200 or not content_type.startswith(expected_type)))):
                    failed_paths.append(path)
            status, _, body = _request(url + "/__phase14/d1-marker", token)
            report["http"]["/__phase14/d1-marker"] = status
            marker = json.loads(body) if status == 200 else {}
            report["d1_marker"] = marker.get("participant_count")
            if report["d1_marker"] != 4:
                failed_paths.append("/__phase14/d1-marker")
            status, _, body = _request(url + "/__phase14/r2-key-set", token)
            report["http"]["/__phase14/r2-key-set"] = status
            listed = json.loads(body) if status == 200 else {}
            expected_keys = sorted(item["target_key"] for item in archive_plan["archives"])
            report["r2_key_set"] = {"count": len(listed.get("keys", [])),
                                    "exact": listed.get("keys") == expected_keys and
                                    listed.get("truncated") is False}
            if not report["r2_key_set"]["exact"]:
                failed_paths.append("/__phase14/r2-key-set")
            if failed_paths:
                raise RuntimeError("Worker protected smoke failed")
        except Exception as error:
            report["failure"] = type(error).__name__
            raise
        finally:
            write_json(report_path, report)
            if preview_process is not None:
                try:
                    preview_process.terminate()
                    preview_process.wait(timeout=10)
                    report["remote_preview_stopped"] = True
                except subprocess.TimeoutExpired:
                    preview_process.kill()
                    preview_process.wait(timeout=10)
                    report["remote_preview_stopped"] = True
            if preview_log is not None:
                preview_log.close()
                if report["failure"]:
                    debug_text = (run_dir / "remote-preview.log").read_text(
                        encoding="utf-8", errors="replace")
                    debug_text = debug_text.replace(token, "[REDACTED]")
                    if "worker_config" in locals():
                        debug_text = debug_text.replace(
                            worker_config["vars"]["SECRET_KEY"], "[REDACTED]")
                    (report_directory / f"{name}-preview-debug.log").write_text(
                        debug_text, encoding="utf-8")
            if report["worker_deploy_attempted"]:
                try:
                    _run(wrangler, ["delete", name, "--force", "--config",
                                    str(stage / "wrangler.jsonc")], run_dir, cwd=stage,
                         input_text="y\n")
                    report["worker_deleted"] = True
                except Exception:
                    pass
            if report["r2_created"]:
                try:
                    for item in archive_plan["archives"]:
                        _run(wrangler, ["r2", "object", "delete",
                                        f"{name}/{item['target_key']}", "--remote"], run_dir)
                    _run(wrangler, ["r2", "bucket", "delete", name], run_dir)
                    report["r2_deleted"] = name not in _run(
                        wrangler, ["r2", "bucket", "list"], run_dir)
                except Exception:
                    pass
            if report["d1_created"]:
                try:
                    _run(wrangler, ["d1", "delete", name, "--skip-confirmation"], run_dir)
                    listed = json.loads(_run(wrangler, ["d1", "list", "--json"], run_dir))
                    report["d1_deleted"] = not any(item.get("name") == name for item in listed)
                except Exception:
                    pass
            write_json(report_path, report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wrangler", type=Path)
    parser.add_argument("--base-bundle", type=Path, required=True)
    parser.add_argument("--report-directory", type=Path, required=True)
    parser.add_argument("--remote-dev", action="store_true")
    args = parser.parse_args()
    report = run(args.wrangler, args.base_bundle, args.report_directory,
                 remote_dev=args.remote_dev)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    worker_stopped = (report["remote_preview_stopped"] if args.remote_dev else
                      report["worker_deleted"])
    if not worker_stopped or not report["r2_deleted"] or not report["d1_deleted"]:
        raise SystemExit("Disposable resource deletion was not fully verified")


if __name__ == "__main__":
    main()
