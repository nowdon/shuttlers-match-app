"""Three disposable one-hop Worker deployment propagation cycles."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time

from run_phase142b_edge_probe import (
    _base_config, _run, _start_tail, _stop_tail, _tail_summary,
)


OFFSETS = (0, 2, 5, 10, 15, 30, 60)


def _source(stage, marker):
    (stage / "worker.py").write_text(
        "from workers import WorkerEntrypoint, Response\n"
        "class Default(WorkerEntrypoint):\n"
        "    async def fetch(self, request):\n"
        "        if request.headers.get('X-Phase14-Smoke-Token') != self.env.PHASE14_SMOKE_TOKEN:\n"
        "            return Response('Not Found', status=404)\n"
        f"        return Response('{marker}', headers={{'X-Phase14-Version': '{marker}'}})\n",
        encoding="utf-8",
    )


def _probe(url, token):
    with tempfile.TemporaryDirectory(prefix="phase142b-probe-") as temporary:
        headers = Path(temporary) / "headers"
        body = Path(temporary) / "body"
        cmd = ["curl", "--silent", "--show-error", "--max-time", "30",
               "--dump-header", str(headers), "--output", str(body),
               "--write-out", "%{http_code}", "--config", "-", url + "/viewer"]
        now = datetime.now(timezone.utc).isoformat()
        result = subprocess.run(cmd, input=f'header = "X-Phase14-Smoke-Token: {token}"\n',
                                capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError("Disposable probe curl failed")
        header_text = headers.read_text(encoding="utf-8", errors="replace")

        def header(key):
            match = re.search(r"^" + re.escape(key) + r":\s*(\S+)", header_text,
                              re.I | re.M)
            return match.group(1) if match else None

        content = body.read_bytes()
        error = re.search(rb"error code:\s*(1\d{3})", content, re.I)
        return {"timestamp_utc": now, "path": "/viewer", "status": int(result.stdout),
                "cf_ray": header("cf-ray"), "cf_error_type": header("cf-error-type"),
                "response_marker": header("x-phase14-version"),
                "body_marker": content.decode("ascii", errors="replace") if len(content) < 120 else None,
                "error_code": error.group(1).decode() if error else None}


def _status(wrangler, name, directory):
    text = _run(wrangler, ["deployments", "status", "--name", name, "--json"], directory)
    data = json.loads(text)
    return [{"version_id": item.get("version_id"), "percentage": item.get("percentage")}
            for item in data.get("versions", [])]


def _version_id(output):
    match = re.search(r"(?:Current Version ID|Version ID):\s*([0-9a-f-]{36})", output, re.I)
    return match.group(1) if match else None


def _tail_presence(requests, events):
    rays = {str(event.get("cf_ray") or "").lower() for event in events}
    for request in requests:
        ray = str(request.get("cf_ray") or "").split("-")[0].lower()
        request["tail_invocation"] = bool(ray and ray in rays)


def run(wrangler, bundle, report_directory):
    wrangler = Path(wrangler).resolve()
    bundle = Path(bundle).resolve()
    report_directory = Path(report_directory).resolve()
    report_directory.mkdir(parents=True, exist_ok=True)
    report = {"cycles": [], "failure": None}
    report_path = report_directory / "phase142b-propagation.json"
    try:
        for number in range(1, 4):
            name = "shuttlers-phase14-disposable-" + secrets.token_hex(4)
            token = secrets.token_urlsafe(32)
            prior = "prior-" + secrets.token_hex(5)
            expected = "expected-" + secrets.token_hex(5)
            cycle = {"number": number, "script_name": name, "old_marker": prior,
                     "expected_marker": expected, "requests": [], "readiness": [],
                     "deleted": False, "failure": None}
            report["cycles"].append(cycle)
            with tempfile.TemporaryDirectory(prefix="phase142b-propagation-") as temporary:
                directory = Path(temporary)
                stage = directory / "worker"
                stage.mkdir()
                config = _base_config(name, token)
                (stage / "wrangler.jsonc").write_text(json.dumps(config), encoding="utf-8")
                shutil.copytree(bundle / "python_modules", stage / "python_modules")
                shutil.copy2(bundle / "pylock.toml", stage / "pylock.toml")
                tail_process = tail_log = None
                deployed = False
                try:
                    _source(stage, prior)
                    first = _run(wrangler, ["deploy", "--config", str(stage / "wrangler.jsonc")],
                                 directory, cwd=stage, redactions=(token,))
                    deployed = True
                    url_match = re.search(r"https://[A-Za-z0-9.-]+\.workers\.dev", first)
                    if not url_match:
                        raise RuntimeError("Disposable workers.dev URL missing")
                    url = url_match.group(0)
                    tail_process, tail_log, tail_path = _start_tail(wrangler, name, directory,
                                                                    f"cycle-{number}")
                    _source(stage, expected)
                    second = _run(wrangler, ["deploy", "--config", str(stage / "wrangler.jsonc")],
                                  directory, cwd=stage, redactions=(token,))
                    cycle["deployed_version_id"] = _version_id(second)
                    completed = time.monotonic()
                    cycle["deploy_completed_utc"] = datetime.now(timezone.utc).isoformat()
                    for offset in OFFSETS:
                        remaining = completed + offset - time.monotonic()
                        if remaining > 0:
                            time.sleep(remaining)
                        item = _probe(url, token)
                        item["target_offset_s"] = offset
                        item["actual_offset_s"] = round(time.monotonic() - completed, 2)
                        cycle["requests"].append(item)
                    cycle["active_status"] = _status(wrangler, name, directory)
                    successes = 0
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline and successes < 10:
                        item = _probe(url, token)
                        item["actual_offset_s"] = round(time.monotonic() - completed, 2)
                        cycle["readiness"].append(item)
                        successes = successes + 1 if (item["status"] == 200 and
                                                       item["response_marker"] == expected) else 0
                        if successes < 10:
                            time.sleep(1)
                    cycle["readiness_passed"] = successes == 10
                    time.sleep(3)
                    _stop_tail(tail_process, tail_log)
                    tail_process = tail_log = None
                    events = _tail_summary(tail_path)
                    _tail_presence(cycle["requests"] + cycle["readiness"], events)
                    cycle["tail_events"] = len(events)
                except Exception as error:
                    cycle["failure"] = f"{type(error).__name__}: {error}"
                    raise
                finally:
                    if tail_process is not None:
                        _stop_tail(tail_process, tail_log)
                    if deployed:
                        try:
                            _run(wrangler, ["delete", name, "--force"], directory)
                            cycle["deleted"] = True
                        except Exception as error:
                            cycle["delete_error"] = type(error).__name__
                    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except Exception as error:
        report["failure"] = f"{type(error).__name__}: {error}"
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        raise
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("wrangler", type=Path)
    parser.add_argument("--base-bundle", type=Path, required=True)
    parser.add_argument("--report-directory", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.wrangler, args.base_bundle, args.report_directory)
    print(json.dumps({"cycles": len(result["cycles"]), "failure": result["failure"],
                      "deleted": [x["deleted"] for x in result["cycles"]]}))
