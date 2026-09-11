"""Join HTTP and tail evidence and aggregate runtime operations."""
from collections import Counter
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def percentile(values, percent):
    values = sorted(value for value in values if value is not None)
    if not values:
        return None
    position = (len(values) - 1) * percent / 100
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return round(values[lower], 3)
    return round(values[lower] + (values[upper] - values[lower]) * (position - lower), 3)


def stats(records):
    cpu = [record.get("cpu_ms") for record in records]
    wall = [record.get("wall_ms") for record in records]
    return {
        "requests": len(records),
        "success": sum(record["status"] == 200 for record in records),
        "1101": sum(record["error"] == "error code: 1101" for record in records),
        "1102": sum(record["error"] == "error code: 1102" for record in records),
        "other": sum(record["status"] != 200 and record["error"] not in ("error code: 1101", "error code: 1102") for record in records),
        "tail_coverage": sum(value is not None for value in cpu),
        "cpu_p50": percentile(cpu, 50),
        "cpu_p95": percentile(cpu, 95),
        "cpu_max": max((value for value in cpu if value is not None), default=None),
        "wall_max": max((value for value in wall if value is not None), default=None),
        "outcomes": dict(Counter(record.get("outcome") for record in records if record.get("outcome"))),
    }


def summarize(http_path, tail_path=None, deploy_path=None):
    http = json.loads(Path(http_path).read_text())
    records = [dict(record) for record in http["requests"]]
    events = json.loads(Path(tail_path).read_text()) if tail_path else []
    by_probe = {event["probe"]: event for event in events}
    deployed_version = json.loads(Path(deploy_path).read_text())["version_id"] if deploy_path else None
    for record in records:
        event = by_probe.get(record["probe"])
        ray_matches = event and (
            not record.get("ray_id")
            or not event.get("ray_id")
            or event["ray_id"].split("-")[0] == record["ray_id"].split("-")[0]
        )
        if ray_matches:
            record.update(
                cpu_ms=event["cpu_ms"], wall_ms=event["wall_ms"],
                outcome=event["outcome"], exceptions=event["exceptions"],
                version_id=event["version_id"],
                version_matches=not deployed_version or event["version_id"] == deployed_version,
            )
        else:
            record.update(cpu_ms=None, wall_ms=None, outcome=None, exceptions=[], version_id=None, version_matches=None)
    valid = [record for record in records if record["version_matches"] is not False]
    groups = {}
    for operation in sorted({record["operation"] for record in valid}):
        groups[operation] = stats([record for record in valid if record["operation"] == operation])
    result = {
        "label": http["label"],
        "version_id": deployed_version,
        "scheduled_requests": len(records),
        "version_mismatches": len(records) - len(valid),
        "overall": stats(valid),
        "by_operation": groups,
        "state_observations": [
            {key: record.get(key) for key in (
                "phase", "ordinal", "cadence", "operation", "status",
                "participant_count", "created", "runtime_initialized", "db_exists",
                "runtime_before", "db_before", "runtime_after", "db_after",
            )}
            for record in valid
            if record["phase"] in ("sequence-a", "sequence-b", "spaced", "idle", "post-redeploy")
        ],
        "failures": [
            {key: record.get(key) for key in (
                "probe", "operation", "phase", "ordinal", "status", "error",
                "cpu_ms", "wall_ms", "outcome", "exceptions", "version_id",
            )}
            for record in valid if record["status"] != 200
        ],
    }
    destination = ROOT / "results" / f"{http['label']}-summary.json"
    destination.write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("http")
    parser.add_argument("--tail")
    parser.add_argument("--deploy")
    args = parser.parse_args()
    result = summarize(args.http, args.tail, args.deploy)
    print(json.dumps(result["overall"], indent=2))
