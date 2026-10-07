"""Create, verify, and delete one synthetic disposable remote R2 bucket.

Requires R2 to be enabled in the authenticated Wrangler account. This script
never accepts a source archive directory or production bucket name.
"""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from migration.archives import build_archive_plan
from migration.fixture import create_synthetic_fixture
from migration.manifest import write_json
from migration.remote import RemoteWrangler


def _run(wrangler, args, run_dir, *, input_text=None):
    env = dict(os.environ)
    env["WRANGLER_LOG_PATH"] = str(run_dir / "wrangler.log")
    env["WRANGLER_SEND_METRICS"] = "false"
    result = subprocess.run(
        [str(wrangler), *args], input=input_text,
        capture_output=True, text=True, env=env,
    )
    if result.returncode:
        raise RuntimeError(f"Wrangler {args[0]} {args[1]} failed; see private log")
    return result.stdout


def _get(url, token=None):
    with tempfile.TemporaryDirectory(prefix="phase14-r2-http-") as directory:
        body = Path(directory) / "response"
        command = ["curl", "--silent", "--show-error", "--max-time", "30",
                   "--output", str(body), "--write-out", "%{http_code}"]
        input_text = None
        if token:
            command.extend(("--config", "-"))
            input_text = f'header = "X-Phase14-Smoke-Token: {token}"\n'
        result = subprocess.run(
            [*command, url], input=input_text, capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError("R2 listing Worker HTTP request failed")
        return int(result.stdout), body.read_bytes()


def run(wrangler, report_directory):
    wrangler = Path(wrangler).resolve()
    report_directory = Path(report_directory).resolve()
    report_directory.mkdir(parents=True, exist_ok=True)
    name = f"shuttlers-phase14-disposable-{secrets.token_hex(4)}"
    token = secrets.token_urlsafe(32)
    report = {"resource": name, "created": False, "upload": None,
              "idempotent": None, "exact_key_set_verified": False,
              "listing_worker_deploy_attempted": False,
              "listing_worker_deployed": False, "listing_worker_deleted": False,
              "objects_deleted": False,
              "deleted": False, "failure": None}
    report_path = report_directory / f"{name}-r2-report.json"
    with tempfile.TemporaryDirectory(prefix="phase14-remote-r2-") as temporary:
        run_dir = Path(temporary)
        fixture = create_synthetic_fixture(run_dir / "fixture")
        plan = build_archive_plan(fixture["archives"])
        config = run_dir / "wrangler.jsonc"
        (run_dir / "worker.js").write_text(
            "export default { async fetch(request, env) {\n"
            "  if (request.headers.get('X-Phase14-Smoke-Token') !== env.PHASE14_SMOKE_TOKEN)"
            " return new Response('Not Found', {status: 404});\n"
            "  if (request.method !== 'GET') return new Response('Method Not Allowed', {status: 405});\n"
            "  const objects = []; let cursor;\n"
            "  do { const page = await env.HISTORY_ARCHIVES.list({cursor});\n"
            "       objects.push(...page.objects.map(item => ({key: item.key, size: item.size})));\n"
            "       cursor = page.truncated ? page.cursor : undefined;\n"
            "  } while (cursor);\n"
            "  return Response.json({objects});\n"
            "} };\n",
            encoding="utf-8",
        )
        config.write_text(json.dumps({
            "name": name, "main": "worker.js",
            "compatibility_date": "2026-10-01",
            "workers_dev": True, "preview_urls": False,
            "r2_buckets": [{"binding": "HISTORY_ARCHIVES", "bucket_name": name}],
            "vars": {"PHASE14_SMOKE_TOKEN": token},
        }, indent=2) + "\n", encoding="utf-8")
        try:
            _run(wrangler, ["r2", "bucket", "create", name, "--location=apac"], run_dir)
            report["created"] = True
            remote = RemoteWrangler(wrangler, config, name, name)
            report["upload"] = remote.copy_validate_r2(plan)
            second = remote.copy_validate_r2(plan)
            if (second["verified_count"] != plan["object_count"] or
                    any(item["status"] != "idempotent_existing" for item in second["objects"])):
                raise RuntimeError("R2 second pass was not idempotent")
            report["idempotent"] = True
            report["listing_worker_deploy_attempted"] = True
            deployed = _run(wrangler, ["deploy", "--config", str(config)], run_dir)
            report["listing_worker_deployed"] = True
            match = re.search(r"https://[A-Za-z0-9.-]+\.workers\.dev", deployed)
            if not match:
                raise RuntimeError("R2 listing Worker URL missing")
            url = match.group(0)
            for attempt in range(5):
                denied, denied_body = _get(url)
                status, body = _get(url, token)
                if denied == 404 and denied_body == b"Not Found" and status == 200:
                    break
                if attempt < 4:
                    time.sleep(2)
            if denied != 404 or denied_body != b"Not Found" or status != 200:
                raise RuntimeError("R2 listing Worker protection or HTTP failed")
            actual = json.loads(body)["objects"]
            expected = sorted(
                ({"key": item["target_key"], "size": item["size"]}
                 for item in plan["archives"]),
                key=lambda item: item["key"],
            )
            if sorted(actual, key=lambda item: item["key"]) != expected:
                raise RuntimeError("Remote R2 exact key/size set differs")
            report["exact_key_set_verified"] = True
        except Exception as error:
            report["failure"] = type(error).__name__
            raise
        finally:
            # Save the validation outcome before deleting any remote object.
            write_json(report_path, report)
            if report["listing_worker_deploy_attempted"]:
                try:
                    _run(wrangler, ["delete", name, "--force", "--config", str(config)],
                         run_dir, input_text="y\n")
                    report["listing_worker_deleted"] = True
                except Exception:
                    pass
            if report["created"]:
                try:
                    for item in plan["archives"]:
                        _run(wrangler, ["r2", "object", "delete",
                                        f"{name}/{item['target_key']}", "--remote"], run_dir)
                    report["objects_deleted"] = True
                    _run(wrangler, ["r2", "bucket", "delete", name], run_dir)
                    listed = _run(wrangler, ["r2", "bucket", "list"], run_dir)
                    report["deleted"] = name not in listed
                except Exception:
                    report["deleted"] = False
            write_json(report_path, report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wrangler", type=Path)
    parser.add_argument("--report-directory", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.wrangler, args.report_directory)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    if not report["deleted"] or not report["listing_worker_deleted"]:
        raise SystemExit("Disposable R2/Worker deletion was not verified")


if __name__ == "__main__":
    main()
