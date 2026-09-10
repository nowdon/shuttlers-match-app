"""Join HTTP/tail evidence and aggregate per-template, phase, and cache state."""
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
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(values[lower], 3)
    result = values[lower] + (values[upper] - values[lower]) * (position - lower)
    return round(result, 3)


def stats(records):
    success = [record for record in records if record["status"] == 200]
    cpu = [record.get("cpu_ms") for record in records]
    wall = [record.get("wall_ms") for record in records]
    success_cpu = [record.get("cpu_ms") for record in success]
    success_wall = [record.get("wall_ms") for record in success]
    client = [record.get("client_ms") for record in records]
    return {
        "requests": len(records),
        "success": len(success),
        "1101": sum(record["error"] == "error code: 1101" for record in records),
        "1102": sum(record["error"] == "error code: 1102" for record in records),
        "other": sum(
            record["status"] != 200
            and record["error"] not in ("error code: 1101", "error code: 1102")
            for record in records
        ),
        "tail_coverage": sum(record.get("cpu_ms") is not None for record in records),
        "client_p50": percentile(client, 50),
        "client_p95": percentile(client, 95),
        "client_max": max((value for value in client if value is not None), default=None),
        "cpu_p50": percentile(cpu, 50),
        "cpu_p95": percentile(cpu, 95),
        "cpu_max": max((value for value in cpu if value is not None), default=None),
        "wall_p50": percentile(wall, 50),
        "wall_p95": percentile(wall, 95),
        "wall_max": max((value for value in wall if value is not None), default=None),
        "success_cpu_p50": percentile(success_cpu, 50),
        "success_cpu_p95": percentile(success_cpu, 95),
        "success_cpu_max": max(
            (value for value in success_cpu if value is not None), default=None
        ),
        "success_wall_p50": percentile(success_wall, 50),
        "success_wall_p95": percentile(success_wall, 95),
        "success_wall_max": max(
            (value for value in success_wall if value is not None), default=None
        ),
        "outcomes": dict(Counter(record.get("outcome") for record in records if record.get("outcome"))),
        "exceptions": dict(
            Counter(
                exception["type"]
                for record in records
                for exception in record.get("exceptions", [])
            )
        ),
    }


def group(records, keys):
    output = {}
    values = sorted({tuple(record[key] for key in keys) for record in records})
    for value in values:
        subset = [
            record
            for record in records
            if tuple(record[key] for key in keys) == value
        ]
        output[" / ".join(str(part) for part in value)] = stats(subset)
    return output


def summarize(http_path, tail_path=None, deploy_path=None):
    http = json.loads(Path(http_path).read_text())
    records = [dict(record) for record in http["requests"]]
    events = json.loads(Path(tail_path).read_text()) if tail_path else []
    by_probe = {event["probe"]: event for event in events}
    version_id = None
    if deploy_path:
        version_id = json.loads(Path(deploy_path).read_text())["version_id"]
    for record in records:
        event = by_probe.get(record["probe"])
        if event and (not record.get("ray_id") or event.get("ray_id", "").split("-")[0] == record["ray_id"].split("-")[0]):
            record.update(
                cpu_ms=event["cpu_ms"],
                wall_ms=event["wall_ms"],
                outcome=event["outcome"],
                exceptions=event["exceptions"],
                version_id=event["version_id"],
                version_matches=not version_id or event["version_id"] == version_id,
            )
        else:
            record.update(
                cpu_ms=None,
                wall_ms=None,
                outcome=None,
                exceptions=[],
                version_id=None,
                version_matches=None,
            )
    valid = [record for record in records if record["version_matches"] is not False]
    repeat = [record for record in valid if record["phase"] == "representative-repeat"]
    cache_groups = []
    for record in repeat:
        item = dict(record)
        item["cache_state"] = (
            "first" if item["ordinal"] == 0 else "second" if item["ordinal"] == 1 else "warm"
        )
        before = item.get("cache_before")
        item["observed_cache"] = (
            "empty" if before == "0" else "populated" if before is not None else "unobserved"
        )
        after = item.get("cache_after")
        item["target_cache"] = (
            "miss"
            if before is not None and after is not None and int(after) > int(before)
            else "hit"
            if before is not None and after is not None
            else "unobserved"
        )
        cache_groups.append(item)
    result = {
        "label": http["label"],
        "version_id": version_id,
        "scheduled_requests": len(records),
        "version_mismatches": len(records) - len(valid),
        "overall": stats(valid),
        "by_template_mode": group(valid, ("template", "mode")),
        "by_phase": group(valid, ("phase",)),
        "repeat_first_warm": group(cache_groups, ("template", "cache_state")),
        "repeat_by_observed_cache": group(cache_groups, ("template", "observed_cache")),
        "repeat_by_target_cache": group(cache_groups, ("template", "target_cache")),
        "repeat_by_cadence": group(repeat, ("template", "cadence")),
        "failures": [
            {
                key: record.get(key)
                for key in (
                    "probe",
                    "template",
                    "mode",
                    "phase",
                    "ordinal",
                    "previous_template",
                    "cadence",
                    "status",
                    "error",
                    "cpu_ms",
                    "wall_ms",
                    "outcome",
                    "exceptions",
                    "invocation_marker",
                    "version_id",
                )
            }
            for record in valid
            if record["status"] != 200
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
