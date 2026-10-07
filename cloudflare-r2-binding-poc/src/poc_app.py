"""Synchronous WSGI probe that executes the production R2 adapter."""

import json

from pyodide.ffi import run_sync

from storage.history_archives import R2HistoryArchiveStorage


PREFIX = "history_dumps/2026/09/"
PAYLOADS = {
    f"{PREFIX}match_history_manual_dump_20260915_120000_000001.json":
        '{"schema_version":1,"rounds":[]}'.encode(),
    f"{PREFIX}match_history_manual_dump_20260915_120000_000002.json":
        '{"schema_version":1,"rounds":[{}]}'.encode(),
}


def application(environ, start_response):
    if environ.get("PATH_INFO") != "/exercise" or environ.get("REQUEST_METHOD") != "POST":
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"Not found"]

    binding = environ["workers.env"].HISTORY_ARCHIVES
    storage = R2HistoryArchiveStorage(binding, list_limit=1)
    for key, payload in PAYLOADS.items():
        storage.put_history_archive(key, payload)

    first_key = next(iter(PAYLOADS))
    raw = storage.get_history_archive(first_key)
    objects = storage.list_history_archives()

    # The production archive record intentionally exposes only application
    # metadata. Verify the HTTP metadata separately through the same run_sync
    # bridge before deleting the objects through the production adapter.
    stored = run_sync(binding.get(first_key))
    content_type = str(stored.httpMetadata.contentType)
    for key in PAYLOADS:
        storage.delete_history_archive(key)
    deleted = storage.get_history_archive(first_key) is None

    result = {
        "ok": raw == PAYLOADS[first_key] and deleted,
        "adapter": "R2HistoryArchiveStorage",
        "binding": "HISTORY_ARCHIVES",
        "run_sync": True,
        "bytes_round_trip": raw == PAYLOADS[first_key],
        "content_type": content_type,
        "listed": len(objects),
        "pagination_limit": 1,
        "sizes": [item.size for item in objects],
        "uploaded_iso": [item.modified_at.isoformat() for item in objects],
        "deleted": deleted,
        "single_key_delete": True,
    }
    body = json.dumps(result).encode()
    start_response("200 OK", [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ])
    return [body]
