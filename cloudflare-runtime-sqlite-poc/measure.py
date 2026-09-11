"""Run fixed runtime/SQLite request sequences without storing paths or credentials."""
import argparse
from email.parser import Parser
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parent


def operation(path, kind, phase, ordinal, delay=0, cadence="continuous"):
    return {
        "path": path,
        "operation": kind,
        "phase": phase,
        "ordinal": ordinal,
        "delay": delay,
        "cadence": cadence,
    }


def build_plan(post_redeploy=False):
    if post_redeploy:
        return [operation("/api/participants", "raw-api-read", "post-redeploy", 0)]
    plan = [
        operation("/runtime-poc/status", "initialization", "sequence-a", 0),
        operation("/api/participants", "raw-api-read", "sequence-a", 1),
        operation("/runtime-poc/count", "sqlalchemy-count", "sequence-a", 2),
        operation("/runtime-poc/seed", "synthetic-write", "sequence-b", 0),
        operation("/runtime-poc/count", "sqlalchemy-count", "sequence-b", 1),
        operation("/api/participants", "raw-api-read", "sequence-b", 2),
        operation("/runtime-poc/seed", "synthetic-write", "sequence-b", 3),
        operation("/runtime-poc/count", "sqlalchemy-count", "sequence-b", 4),
        operation("/api/participants", "raw-api-read", "sequence-b", 5),
    ]
    for ordinal in range(50):
        plan.append(operation("/api/participants", "raw-api-read", "repeat", ordinal))
        plan.append(operation("/runtime-poc/count", "sqlalchemy-count", "repeat", ordinal))
    for ordinal in range(5):
        plan.append(operation(
            "/api/participants", "raw-api-read", "spaced", ordinal,
            delay=2, cadence="spaced-2s",
        ))
        plan.append(operation(
            "/runtime-poc/count", "sqlalchemy-count", "spaced", ordinal,
            delay=2, cadence="spaced-2s",
        ))
    plan.append(operation(
        "/runtime-poc/count", "sqlalchemy-count", "idle", 0,
        delay=15, cadence="after-15s-idle",
    ))
    plan.append(operation(
        "/api/participants", "raw-api-read", "idle", 1,
        delay=30, cadence="after-30s-idle",
    ))
    return plan


def request(base, item, probe, headers_path, body_path):
    url = f"{base.rstrip('/')}{item['path']}?probe={probe}"
    started = time.monotonic()
    completed = subprocess.run(
        [
            "curl", "-sS", "--max-time", "30", "-H", f"X-Runtime-Probe: {probe}",
            "-D", str(headers_path), "-o", str(body_path), "-w", "%{http_code}", url,
        ],
        capture_output=True,
        text=True,
    )
    client_ms = round((time.monotonic() - started) * 1000, 3)
    raw = body_path.read_bytes() if body_path.exists() else b""
    header_text = headers_path.read_text() if headers_path.exists() else ""
    block = header_text.strip().replace("\r\n", "\n").split("\n\n")[-1]
    parsed = Parser().parsestr(block.split("\n", 1)[1] if "\n" in block else "")
    status = int(completed.stdout) if completed.stdout.isdigit() else 0
    error = raw.decode(errors="replace").strip() if raw.startswith(b"error code:") else None
    payload = None
    if status == 200:
        payload = json.loads(raw)
        if isinstance(payload, list):
            assert all(row.get("name") == "Runtime PoC Player" for row in payload)
            participant_count = len(payload)
        else:
            participant_count = payload.get("participant_count")
        assert b"cloudflare-runtime-poc-only-secret-not-production" not in raw
        assert not any("/" in str(value) for value in payload.values()) if isinstance(payload, dict) else True
    else:
        participant_count = None
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "curl failed"
        error = f"transport: {detail}"
    return {
        **{key: value for key, value in item.items() if key != "delay"},
        "probe": probe,
        "status": status,
        "error": error,
        "ray_id": parsed.get("CF-Ray"),
        "client_ms": client_ms,
        "body_sha256": hashlib.sha256(raw).hexdigest(),
        "participant_count": participant_count,
        "created": payload.get("created") if isinstance(payload, dict) else None,
        "runtime_initialized": payload.get("runtime_initialized") if isinstance(payload, dict) else None,
        "db_exists": payload.get("db_exists") if isinstance(payload, dict) else None,
        "runtime_before": parsed.get("X-Runtime-Poc-Initialized-Before"),
        "db_before": parsed.get("X-Runtime-Poc-Db-Before"),
        "runtime_after": parsed.get("X-Runtime-Poc-Initialized-After"),
        "db_after": parsed.get("X-Runtime-Poc-Db-After"),
        "transport_exit": completed.returncode,
    }


def measure(base, label, post_redeploy=False):
    records = []
    batch = time.time_ns()
    with tempfile.TemporaryDirectory(prefix="runtime-sqlite-http-") as temporary:
        headers_path = Path(temporary) / "headers"
        body_path = Path(temporary) / "body"
        for sequence, item in enumerate(build_plan(post_redeploy)):
            if item["delay"]:
                time.sleep(item["delay"])
            probe = f"runtime-{label}-{batch}-{sequence}"
            record = request(base, item, probe, headers_path, body_path)
            records.append(record)
            if sequence % 20 == 0 or record["status"] != 200:
                print(json.dumps({"sequence": sequence, "operation": item["operation"], "status": record["status"]}), flush=True)
    report = {"label": label, "base": base, "requests": records}
    (ROOT / "results").mkdir(exist_ok=True)
    destination = ROOT / "results" / f"{label}-http.json"
    destination.write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("label", choices=("local-worker", "workers-dev", "post-redeploy"))
    parser.add_argument("base")
    args = parser.parse_args()
    report = measure(args.base, args.label, args.label == "post-redeploy")
    print(json.dumps({"requests": len(report["requests"])}), flush=True)
