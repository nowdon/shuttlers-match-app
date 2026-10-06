"""Disposable, gated Worker smoke for operator-provided card images only."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time

if __package__:
    from .card_asset_inventory import audit_cards
    from .prepare_worker_bundle import ROOT, prepare
else:
    from card_asset_inventory import audit_cards
    from prepare_worker_bundle import ROOT, prepare


def _wrangler(binary, args, cwd, token):
    env = dict(os.environ, WRANGLER_SEND_METRICS="false")
    result = subprocess.run([str(binary), *args], cwd=cwd, env=env,
                            capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout).replace(token, "[REDACTED]")
        raise RuntimeError(f"Wrangler {args[0]} failed: {detail[-1200:]}")
    return result.stdout


def _get(url, token=None):
    with tempfile.TemporaryDirectory(prefix="phase142c-http-") as temporary:
        directory = Path(temporary)
        headers = directory / "headers"
        body = directory / "body"
        command = ["curl", "--silent", "--show-error", "--max-time", "30",
                   "--dump-header", str(headers), "--output", str(body),
                   "--write-out", "%{http_code}", "--config", "-", url]
        config = f'header = "X-Phase14-Smoke-Token: {token}"\n' if token else ""
        result = subprocess.run(command, input=config, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError("Disposable Worker HTTP request failed")
        header_text = headers.read_text(encoding="utf-8", errors="replace")
        content = body.read_bytes()
        match = re.search(r"^content-type:\s*([^;\r\n]+)", header_text, re.I | re.M)
        marker = re.search(r"^x-phase14-version:\s*(\S+)", header_text, re.I | re.M)
        ray = re.search(r"^cf-ray:\s*(\S+)", header_text, re.I | re.M)
        return {"status": int(result.stdout), "content_type": match.group(1).lower() if match else None,
                "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                "marker": marker.group(1) if marker else None,
                "cf_ray": ray.group(1) if ray else None}


def _active_version(binary, name, directory, token):
    result = json.loads(_wrangler(binary, ["deployments", "status", "--name", name,
                                           "--json"], directory, token))
    return result.get("versions", [])


def _ready(binary, name, directory, url, token, marker, version_id):
    deadline = time.monotonic() + 60
    consecutive = 0
    probes = []
    while time.monotonic() < deadline and consecutive < 10:
        active = _active_version(binary, name, directory, token)
        active_ok = len(active) == 1 and active[0].get("version_id") == version_id and active[0].get("percentage") == 100
        probe = _get(url + "/__phase14/readiness", token)
        probe["active_version_match"] = active_ok
        probes.append(probe)
        consecutive = consecutive + 1 if active_ok and probe["status"] == 200 and probe["marker"] == marker else 0
        if consecutive < 10:
            time.sleep(1)
    if consecutive != 10:
        raise RuntimeError("Disposable Worker readiness gate failed")
    return probes


WORKER_SOURCE = """
export default {
  async fetch(request, env) {
    if (request.headers.get("X-Phase14-Smoke-Token") !== env.PHASE14_SMOKE_TOKEN) {
      return new Response("Not Found", {status: 404});
    }
    const path = new URL(request.url).pathname;
    if (path === "/__phase14/readiness") {
      return new Response(env.PHASE14_VERSION, {
        headers: {"X-Phase14-Version": env.PHASE14_VERSION}
      });
    }
    if (!path.startsWith("/static/cards/")) {
      return new Response("Not Found", {status: 404});
    }
    const assetUrl = new URL("https://assets.local" + path.slice("/static".length));
    const response = await env.ASSETS.fetch(new Request(assetUrl, request));
    const headers = new Headers(response.headers);
    headers.set("X-Phase14-Version", env.PHASE14_VERSION);
    return new Response(response.body, {status: response.status, headers});
  }
};
"""


def run(wrangler, report_path):
    wrangler = Path(wrangler).resolve()
    report_path = Path(report_path).resolve()
    source = audit_cards(ROOT / "static/cards")
    if not source["ok"]:
        raise ValueError("Operator card inventory is invalid; Worker staging was not started")
    name = "shuttlers-phase142c-disposable-" + secrets.token_hex(4)
    token = secrets.token_urlsafe(32)
    marker = "card-static-" + secrets.token_hex(6)
    report = {"script_name": name, "source_valid": source["valid"],
              "staged_valid": 0, "readiness_passed": False, "cards": [],
              "http_pass": 0, "deleted": False, "failure": None}
    deployed = False
    try:
        with tempfile.TemporaryDirectory(prefix="phase142c-stage-") as temporary:
            directory = Path(temporary)
            config = {
                "name": name, "main": "worker.mjs", "compatibility_date": "2026-10-01",
                "workers_dev": True, "preview_urls": False,
                "assets": {"directory": "static", "binding": "ASSETS", "run_worker_first": True},
                "vars": {"PHASE14_SMOKE_TOKEN": token, "PHASE14_VERSION": marker},
            }
            private_config = directory / "disposable.jsonc"
            private_config.write_text(json.dumps(config), encoding="utf-8")
            stage = prepare(directory / "stage", private_config)
            staged = audit_cards(stage / "static/cards")
            report["staged_valid"] = staged["valid"]
            source_hashes = {Path(item["path"]).name: item["sha256"] for item in source["files"]}
            staged_hashes = {Path(item["path"]).name: item["sha256"] for item in staged["files"]}
            if not staged["ok"] or source_hashes != staged_hashes:
                raise RuntimeError("Worker staging card inventory or SHA-256 parity failed")
            (stage / "worker.mjs").write_text(WORKER_SOURCE, encoding="utf-8")
            deployed = True  # Cleanup even if deploy creates a Worker then returns an error.
            output = _wrangler(wrangler, ["deploy", "--config", str(stage / "wrangler.jsonc")], stage, token)
            url_match = re.search(r"https://[A-Za-z0-9.-]+\.workers\.dev", output)
            version_match = re.search(r"(?:Current Version ID|Version ID):\s*([0-9a-f-]{36})", output, re.I)
            if not url_match or not version_match:
                raise RuntimeError("Disposable Worker URL or version ID missing from deploy output")
            url = url_match.group(0)
            report["deployed_version_id"] = version_match.group(1)
            report["deployed_utc"] = datetime.now(timezone.utc).isoformat()
            probes = _ready(wrangler, name, directory, url, token, marker, version_match.group(1))
            report["readiness_passed"] = True
            report["readiness_probes"] = len(probes)
            if _get(url + "/static/cards/hA.png")["status"] != 404:
                raise RuntimeError("Disposable static header gate failed")
            for filename in sorted(source_hashes):
                item = _get(url + "/static/cards/" + filename, token)
                item["filename"] = filename
                item["pass"] = (item["status"] == 200 and item["content_type"] == "image/png"
                                and item["bytes"] > 0 and item["sha256"] == source_hashes[filename]
                                and item["marker"] == marker)
                report["cards"].append(item)
                report["http_pass"] += int(item["pass"])
            if report["http_pass"] != 54:
                raise RuntimeError("Disposable remote static smoke was not 54/54")
    except Exception as error:
        report["failure"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        if deployed:
            try:
                _wrangler(wrangler, ["delete", name, "--force"], ROOT, token)
                report["deleted"] = True
            except Exception as error:
                report["delete_error"] = f"{type(error).__name__}: {error}"
                if report["failure"] is None:
                    report["failure"] = "Disposable Worker cleanup failed"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if deployed and not report["deleted"]:
        raise RuntimeError("Disposable Worker cleanup failed")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wrangler", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.wrangler, args.report)
    print(json.dumps({key: report[key] for key in
                      ("source_valid", "staged_valid", "readiness_passed",
                       "http_pass", "deleted", "failure")}))


if __name__ == "__main__":
    main()
