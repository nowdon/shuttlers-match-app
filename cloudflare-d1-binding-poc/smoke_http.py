"""Exercise local or public PoC endpoints with fixed synthetic inputs only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def call(base_url, method, path):
    request = Request(base_url.rstrip("/") + path, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "shuttlers-d1-poc-probe/1.0")
    try:
        with urlopen(request, timeout=60) as response:
            payload = response.read()
            body = json.loads(payload)
            return {"method": method, "path": path, "status": response.status, "body": body}
    except HTTPError as error:
        payload = error.read()
        try:
            body = json.loads(payload)
        except json.JSONDecodeError:
            body = {"non_json_error": payload.decode("utf-8", "replace")[:200]}
        return {"method": method, "path": path, "status": error.code, "body": body}


def require(result, status):
    if result["status"] != status:
        raise RuntimeError(f"{result['method']} {result['path']}: {result['status']} != {status}")
    return result


def run(base_url, waits):
    results = []
    results.append(require(call(base_url, "POST", "/d1-poc/reset"), 200))
    results.append(require(call(base_url, "GET", "/d1-poc/status"), 200))
    created = require(call(base_url, "POST", "/d1-poc/participants"), 201)
    results.append(created)
    participant_id = created["body"]["participant"]["id"]
    results.append(require(call(base_url, "GET", f"/d1-poc/participants/{participant_id}"), 200))
    results.append(require(call(base_url, "GET", "/d1-poc/participants/999999999"), 404))
    results.append(require(call(base_url, "GET", "/d1-poc/participants"), 200))
    results.append(require(call(base_url, "POST", "/d1-poc/participants"), 409))
    results.append(require(call(base_url, "POST", "/d1-poc/fk-failure"), 409))
    results.append(require(call(base_url, "POST", "/d1-poc/batch-success"), 200))
    results.append(require(call(base_url, "POST", "/d1-poc/batch-failure"), 409))
    results.append(require(call(base_url, "GET", "/d1-poc/batch-failure-state"), 200))
    results.append(require(call(base_url, "POST", "/d1-poc/multiple-run-sync"), 200))

    persistence = []
    for seconds in waits:
        if seconds:
            time.sleep(seconds)
        current = require(call(base_url, "GET", f"/d1-poc/participants/{participant_id}"), 200)
        persistence.append({"after_seconds": seconds, "found": current["body"]["found"]})
        results.append(current)

    results.append(require(call(base_url, "POST", "/d1-poc/reset"), 200))
    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent = list(executor.map(
            lambda _index: call(base_url, "POST", "/d1-poc/participants"),
            range(2),
        ))
    final = require(call(base_url, "GET", "/d1-poc/participants"), 200)
    results.extend(concurrent)
    results.append(final)
    concurrency = {
        "success": sum(item["status"] == 201 for item in concurrent),
        "conflict": sum(item["status"] == 409 for item in concurrent),
        "final_rows": len(final["body"]["participants"]),
    }
    if concurrency != {"success": 1, "conflict": 1, "final_rows": 1}:
        raise RuntimeError(f"unexpected concurrent result: {concurrency}")
    return {"requests": results, "persistence": persistence, "concurrency": concurrency}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--full-waits", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    waits = (0, 2, 15, 30) if args.full_waits else (0,)
    result = run(args.base_url, waits)
    if args.summary_only:
        result = {"persistence": result["persistence"], "concurrency": result["concurrency"]}
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
