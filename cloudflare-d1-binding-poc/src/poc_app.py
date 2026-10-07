"""D1 PoC routes registered on the current production Flask application."""
import os
import re

# Synthetic and PoC-local. It is never returned or logged.
os.environ["SECRET_KEY"] = "synthetic-cloudflare-d1-poc-secret-not-for-production"

from app import app
import app as app_module
from flask import jsonify, request

from d1_adapter import D1Adapter, D1AdapterError


PREFIX = "/d1-poc"
SYNTHETIC_NAME = "D1 PoC Player"
SYNTHETIC_CARD = "D1-POC-001"
SYNTHETIC_CREATED_AT = "2026-09-12T00:00:00Z"
BATCH_KEY = "D1-POC-BATCH"
SYNC_KEY = "D1-POC-MULTI-SYNC"


def _adapter():
    return D1Adapter.from_environ(request.environ)


def _result_object(call):
    return call.value if isinstance(call.value, dict) else {}


def _meta(call):
    value = _result_object(call).get("meta", {})
    return value if isinstance(value, dict) else {}


def _results(call):
    value = _result_object(call).get("results", [])
    return value if isinstance(value, list) else []


def _safe_result(value):
    if not isinstance(value, dict):
        return value
    meta = value.get("meta")
    safe_meta = {}
    if isinstance(meta, dict):
        for key in ("changed_db", "changes", "duration", "last_row_id",
                    "rows_read", "rows_written", "total_attempts"):
            if key in meta:
                safe_meta[key] = meta[key]
    return {
        "results": value.get("results", []),
        "success": value.get("success"),
        "meta": safe_meta,
        "shape": {
            "top_level_keys": sorted(str(key) for key in value),
            "meta_keys": sorted(str(key) for key in meta) if isinstance(meta, dict) else [],
        },
    }


def _error_response(error, *, foreign_key_status=409):
    if error.category == "unique":
        status = 409
    elif error.category == "foreign_key":
        status = foreign_key_status
    else:
        status = 500
    return jsonify(ok=False, error=error.as_dict()), status


def status():
    workers_env = request.environ.get("workers.env")
    binding_available = workers_env is not None and getattr(workers_env, "DB", None) is not None
    if not binding_available:
        return jsonify(
            binding_available=False,
            d1_binding_available=False,
            run_sync_available=D1Adapter.run_sync_available(),
            workers_env_available=workers_env is not None,
            table_exists=False,
            count=0,
        ), 500
    try:
        call = _adapter().all("SELECT COUNT(*) AS count FROM d1_poc_participants")
    except D1AdapterError as error:
        return _error_response(error)
    rows = _results(call)
    return jsonify(
        binding_available=True,
        d1_binding_available=True,
        run_sync_available=D1Adapter.run_sync_available(),
        workers_env_available=True,
        table_exists=True,
        count=rows[0]["count"] if rows else 0,
        all_interop=call.interop,
    )


def create_participant():
    try:
        call = _adapter().run(
            "INSERT INTO d1_poc_participants (name, card, created_at) "
            "VALUES (?, ?, ?) RETURNING id, name, card",
            SYNTHETIC_NAME,
            SYNTHETIC_CARD,
            SYNTHETIC_CREATED_AT,
        )
    except D1AdapterError as error:
        return _error_response(error)
    meta = _meta(call)
    returned = _results(call)
    row = returned[0] if returned else None
    row_id = row.get("id") if isinstance(row, dict) else None
    last_row_id = meta.get("last_row_id")
    return jsonify(
        ok=True,
        participant=row,
        returning=returned,
        last_row_id=last_row_id,
        changes=meta.get("changes"),
        id_matches_last_row_id=row_id == last_row_id,
        run_interop=call.interop,
    ), 201


def get_participant(participant_id):
    try:
        call = _adapter().first(
            "SELECT id, name, card, created_at FROM d1_poc_participants WHERE id = ?",
            participant_id,
        )
    except D1AdapterError as error:
        return _error_response(error)
    if call.value is None:
        return jsonify(found=False, participant=None, first_interop=call.interop), 404
    return jsonify(found=True, participant=call.value, first_interop=call.interop)


def list_participants():
    try:
        call = _adapter().all(
            "SELECT id, name, card, created_at FROM d1_poc_participants ORDER BY id"
        )
    except D1AdapterError as error:
        return _error_response(error)
    return jsonify(
        participants=_results(call),
        all_result=_safe_result(_result_object(call)),
        all_interop=call.interop,
    )


def reset():
    adapter = _adapter()
    statements = (
        "DELETE FROM d1_poc_child",
        "DELETE FROM d1_poc_parent",
        "DELETE FROM d1_poc_batch_items",
        "DELETE FROM d1_poc_participants",
    )
    try:
        calls = [adapter.run(sql) for sql in statements]
    except D1AdapterError as error:
        return _error_response(error)
    return jsonify(ok=True, changes=[_meta(call).get("changes") for call in calls])


def batch_success():
    adapter = _adapter()
    try:
        adapter.run("DELETE FROM d1_poc_batch_items WHERE batch_key = ?", BATCH_KEY)
        call = adapter.batch([
            ("INSERT INTO d1_poc_batch_items (batch_key, value) VALUES (?, ?)", (BATCH_KEY, "A")),
            ("INSERT INTO d1_poc_batch_items (batch_key, value) VALUES (?, ?)", (BATCH_KEY, "B")),
            ("SELECT value FROM d1_poc_batch_items WHERE batch_key = ? ORDER BY value", (BATCH_KEY,)),
        ])
    except D1AdapterError as error:
        return _error_response(error)
    safe_batch = [_safe_result(item) for item in call.value] if isinstance(call.value, list) else []
    return jsonify(ok=True, batch_result=safe_batch, batch_interop=call.interop)


def batch_failure():
    adapter = _adapter()
    try:
        adapter.run("DELETE FROM d1_poc_batch_items WHERE batch_key = ?", BATCH_KEY)
        adapter.batch([
            ("INSERT INTO d1_poc_batch_items (batch_key, value) VALUES (?, ?)", (BATCH_KEY, "A")),
            ("INSERT INTO d1_poc_batch_items (batch_key, value) VALUES (?, ?)", (BATCH_KEY, "A")),
            ("INSERT INTO d1_poc_batch_items (batch_key, value) VALUES (?, ?)", (BATCH_KEY, "B")),
        ])
    except D1AdapterError as error:
        return _error_response(error)
    return jsonify(ok=False, error={"category": "batch unexpectedly succeeded"}), 500


def batch_failure_state():
    try:
        call = _adapter().all(
            "SELECT value FROM d1_poc_batch_items WHERE batch_key = ? ORDER BY value",
            BATCH_KEY,
        )
    except D1AdapterError as error:
        return _error_response(error)
    rows = _results(call)
    return jsonify(ok=not rows, rows=rows, rollback_verified=not rows, all_interop=call.interop)


def fk_failure():
    adapter = _adapter()
    try:
        adapter.run("DELETE FROM d1_poc_child WHERE id = ?", 9001)
        adapter.run(
            "INSERT INTO d1_poc_child (id, parent_id, value) VALUES (?, ?, ?)",
            9001,
            999999,
            "D1 PoC orphan",
        )
    except D1AdapterError as error:
        return _error_response(error)
    return jsonify(ok=False, error={"category": "foreign key unexpectedly succeeded"}), 500


def multiple_run_sync():
    adapter = _adapter()
    try:
        deleted = adapter.run("DELETE FROM d1_poc_batch_items WHERE batch_key = ?", SYNC_KEY)
        inserted = adapter.run(
            "INSERT INTO d1_poc_batch_items (batch_key, value) VALUES (?, ?)",
            SYNC_KEY,
            "before",
        )
        selected_before = adapter.first(
            "SELECT value FROM d1_poc_batch_items WHERE batch_key = ?",
            SYNC_KEY,
        )
        updated = adapter.run(
            "UPDATE d1_poc_batch_items SET value = ? WHERE batch_key = ?",
            "after",
            SYNC_KEY,
        )
        selected_after = adapter.first(
            "SELECT value FROM d1_poc_batch_items WHERE batch_key = ?",
            SYNC_KEY,
        )
    except D1AdapterError as error:
        return _error_response(error)
    return jsonify(
        ok=True,
        sequence=["run DELETE", "run INSERT", "first SELECT", "run UPDATE", "first SELECT"],
        before=selected_before.value,
        after=selected_after.value,
        changes={
            "delete": _meta(deleted).get("changes"),
            "insert": _meta(inserted).get("changes"),
            "update": _meta(updated).get("changes"),
        },
    )


ROUTES = (
    ("status", "/status", status, ("GET",)),
    ("participants_create", "/participants", create_participant, ("POST",)),
    ("participants_get", "/participants/<int:participant_id>", get_participant, ("GET",)),
    ("participants_list", "/participants", list_participants, ("GET",)),
    ("reset", "/reset", reset, ("POST",)),
    ("batch_success", "/batch-success", batch_success, ("POST",)),
    ("batch_failure", "/batch-failure", batch_failure, ("POST",)),
    ("batch_failure_state", "/batch-failure-state", batch_failure_state, ("GET",)),
    ("fk_failure", "/fk-failure", fk_failure, ("POST",)),
    ("multiple_run_sync", "/multiple-run-sync", multiple_run_sync, ("POST",)),
)

for name, rule, handler, methods in ROUTES:
    app.add_url_rule(PREFIX + rule, endpoint="d1_poc_" + name, view_func=handler, methods=methods)


ALLOWED_METHODS = {
    (method, PREFIX + rule)
    for _name, rule, _handler, methods in ROUTES
    for method in methods
}
ALLOWED_TEMPLATES = (("GET", re.compile(r"^/d1-poc/participants/[0-9]+$")),)


def allowed_request(method, path):
    if (method, path) in ALLOWED_METHODS:
        return True
    return any(method == expected and pattern.fullmatch(path)
               for expected, pattern in ALLOWED_TEMPLATES)


def allowed_path(path):
    if any(candidate_path == path for _method, candidate_path in ALLOWED_METHODS):
        return True
    return any(pattern.fullmatch(path) for _method, pattern in ALLOWED_TEMPLATES)


def safe_wsgi(environ, start_response):
    """Reject non-PoC routes before invoking the real runtime WSGI wrapper."""
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "")
    if not allowed_request(method, path):
        path_is_allowed = allowed_path(path)
        status = "405 Method Not Allowed" if path_is_allowed else "404 Not Found"
        start_response(status, [("Content-Type", "application/json")])
        return [b'{"error":"method not allowed"}' if path_is_allowed else b'{"error":"not found"}']
    return app.wsgi_app(environ, start_response)


assert app.wsgi_app is app_module.runtime_wsgi_app
