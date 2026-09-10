"""Run deterministic one-template-per-request sequences without storing bodies or IPs."""
import argparse
from email.parser import Parser
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time

from catalog import RENDER_SLUGS, TEMPLATES


ROOT = Path(__file__).resolve().parent


def operation(slug, mode, phase, ordinal, *, delay=0.1, cadence="continuous"):
    return {
        "slug": slug,
        "template": TEMPLATES[slug]["name"],
        "mode": mode,
        "phase": phase,
        "ordinal": ordinal,
        "delay": delay,
        "cadence": cadence,
    }


def build_plan():
    plan = []
    slugs = tuple(TEMPLATES)
    for mode in ("load", "compile"):
        for slug in slugs:
            for ordinal in range(3):
                plan.append(operation(slug, mode, f"catalog-{mode}", ordinal))

    for slug in RENDER_SLUGS:
        for ordinal in range(100):
            delay, cadence = 0.1, "continuous"
            if 79 <= ordinal <= 97:
                delay, cadence = 2.0, "spaced-2s"
            elif ordinal == 98:
                delay, cadence = 15.0, "after-15s-idle"
            elif ordinal == 99:
                delay, cadence = 30.0, "after-30s-idle"
            plan.append(
                operation(
                    slug,
                    "render",
                    "representative-repeat",
                    ordinal,
                    delay=delay,
                    cadence=cadence,
                )
            )

    for mode in ("load", "compile"):
        for ordinal, slug in enumerate(slugs):
            plan.append(operation(slug, mode, f"template-switch-{mode}", ordinal))
    for ordinal, slug in enumerate(RENDER_SLUGS):
        plan.append(operation(slug, "render", "template-switch-render", ordinal))
    for ordinal in range(160):
        slug = RENDER_SLUGS[ordinal % len(RENDER_SLUGS)]
        plan.append(operation(slug, "render", "round-robin", ordinal))
    return plan


def request(base, item, probe, previous_template, headers_path, body_path):
    url = f"{base.rstrip('/')}/jinja/{item['slug']}/{item['mode']}?probe={probe}"
    started = time.monotonic()
    completed = subprocess.run(
        [
            "curl",
            "-sS",
            "--max-time",
            "30",
            "-H",
            f"X-Jinja-Probe: {probe}",
            "-D",
            str(headers_path),
            "-o",
            str(body_path),
            "-w",
            "%{http_code}",
            url,
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
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "curl failed"
        raise RuntimeError(f"Transport failure before valid measurement: {detail}")
    effects = parsed.get("X-Jinja-Effects")
    import_effects = parsed.get("X-Jinja-Import-Effects")
    error = raw.decode(errors="replace").strip() if raw.startswith(b"error code:") else None
    record = {
        **{key: value for key, value in item.items() if key != "delay"},
        "probe": probe,
        "previous_template": previous_template,
        "status": status,
        "error": error,
        "ray_id": parsed.get("CF-Ray"),
        "client_ms": client_ms,
        "bytes": len(raw),
        "body_sha256": hashlib.sha256(raw).hexdigest(),
        "returned_template": parsed.get("X-Jinja-Template"),
        "returned_mode": parsed.get("X-Jinja-Mode"),
        "cache_before": parsed.get("X-Jinja-Cache-Before"),
        "cache_after": parsed.get("X-Jinja-Cache-After"),
        "invocation_marker": parsed.get("X-Jinja-Invocation"),
        "runtime_initialized": parsed.get("X-Jinja-Runtime"),
        "secret_key_set": parsed.get("X-Jinja-Secret-Key"),
        "side_effects": json.loads(effects) if effects else None,
        "import_side_effects": json.loads(import_effects) if import_effects else None,
        "transport_exit": completed.returncode,
    }
    for counts in (record["side_effects"], record["import_side_effects"]):
        assert not counts or not any(counts.values()), "Forbidden side effect observed"
    if status == 200:
        assert record["returned_template"] == item["template"]
        assert record["returned_mode"] == item["mode"]
        assert record["runtime_initialized"] == "false"
        assert record["secret_key_set"] == "false"
    assert "Set-Cookie" not in parsed
    return record


def measure(base, label, plan=None):
    plan = plan or build_plan()
    records = []
    batch = time.time_ns()
    previous_template = None
    with tempfile.TemporaryDirectory(prefix="jinja-request-http-") as temporary:
        headers_path = Path(temporary) / "headers"
        body_path = Path(temporary) / "body"
        for sequence, item in enumerate(plan):
            if sequence:
                time.sleep(item["delay"])
            probe = f"{label}-{batch}-{sequence}"
            record = request(
                base,
                item,
                probe,
                previous_template,
                headers_path,
                body_path,
            )
            records.append(record)
            previous_template = item["template"]
            if sequence % 25 == 0 or record["status"] != 200:
                print(
                    json.dumps(
                        {
                            "sequence": sequence,
                            "template": item["template"],
                            "mode": item["mode"],
                            "status": record["status"],
                            "error": record["error"],
                        }
                    ),
                    flush=True,
                )
    report = {"label": label, "base": base, "requests": records}
    (ROOT / "results").mkdir(exist_ok=True)
    destination = ROOT / "results" / f"{label}-http.json"
    destination.write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("label", choices=("local-worker", "workers-dev"))
    parser.add_argument("base")
    args = parser.parse_args()
    result = measure(args.base, args.label)
    print(json.dumps({"requests": len(result["requests"])}), flush=True)
