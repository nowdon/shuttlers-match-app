"""The two versioned runtime-state rows and their CAS statements."""

from dataclasses import dataclass
import json

from flask import has_request_context

CURRENT_MATCH = "current_match"
CURRENT_DRAFT = "current_draft"
RUNTIME_KEYS = frozenset((CURRENT_MATCH, CURRENT_DRAFT))
DEFAULT_MATCH_STATE = {
    "match_active": False,
    "match_count": 0,
    "matches": [],
    "bench": [],
}


@dataclass(frozen=True)
class RuntimeStateRecord:
    key: str
    state: dict | None
    version: int


def _adapter(storage):
    if storage is not None:
        return storage
    from storage.provider import create_storage, get_storage
    return get_storage() if has_request_context() else create_storage()


def serialize_state(state):
    if not isinstance(state, dict):
        raise TypeError("Runtime state must be a dictionary.")
    return json.dumps(
        state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def deserialize_state(value):
    if value is None:
        return None
    try:
        state = json.loads(value)
    except (TypeError, ValueError) as error:
        from storage.errors import StorageError
        raise StorageError("Stored runtime state is invalid.") from error
    if not isinstance(state, dict):
        from storage.errors import StorageError
        raise StorageError("Stored runtime state is invalid.")
    return state


def load_runtime_state(key, *, storage=None):
    if key not in RUNTIME_KEYS:
        raise ValueError("Unknown runtime state key.")
    row = _adapter(storage).first(
        "SELECT key, state_json, version FROM runtime_state WHERE key = ?", key
    )
    if row is None:
        from storage.errors import StorageError
        raise StorageError("Runtime state is not initialized.")
    return RuntimeStateRecord(
        key=row["key"], state=deserialize_state(row["state_json"]),
        version=int(row["version"]),
    )


def load_current_match(*, storage=None):
    return load_runtime_state(CURRENT_MATCH, storage=storage)


def load_current_draft(*, storage=None):
    return load_runtime_state(CURRENT_DRAFT, storage=storage)


def cas_guard_statement(key, expected_version):
    """Abort the transaction by violating the seeded guard PK when stale."""
    if key not in RUNTIME_KEYS:
        raise ValueError("Unknown runtime state key.")
    return (
        "INSERT INTO runtime_state_cas_guard (id) "
        "SELECT 1 WHERE NOT EXISTS ("
        "SELECT 1 FROM runtime_state WHERE key = ? AND version = ?)",
        (key, expected_version),
    )


def cas_update_statement(key, state, expected_version):
    if key not in RUNTIME_KEYS:
        raise ValueError("Unknown runtime state key.")
    encoded = None if state is None else serialize_state(state)
    return (
        "UPDATE runtime_state SET state_json = ?, version = version + 1 "
        "WHERE key = ? AND version = ?",
        (encoded, key, expected_version),
    )


def cas_statements(key, state, expected_version):
    return [
        cas_guard_statement(key, expected_version),
        cas_update_statement(key, state, expected_version),
    ]


def save_runtime_state(key, state, expected_version, *, storage=None):
    from storage.errors import StorageConflictError, StorageUniqueError
    adapter = _adapter(storage)
    try:
        adapter.batch(cas_statements(key, state, expected_version))
    except StorageUniqueError as error:
        raise StorageConflictError() from error
    return load_runtime_state(key, storage=adapter)


def save_current_match(state, expected_version, *, storage=None):
    return save_runtime_state(
        CURRENT_MATCH, state, expected_version, storage=storage
    )


def save_current_draft(state, expected_version, *, storage=None):
    return save_runtime_state(
        CURRENT_DRAFT, state, expected_version, storage=storage
    )


def publish_generated_draft(
    match_state, draft_state, expected_match_version, expected_draft_version,
    *, storage=None,
):
    from storage.errors import StorageConflictError, StorageUniqueError
    adapter = _adapter(storage)
    statements = []
    statements.extend(cas_statements(CURRENT_MATCH, match_state, expected_match_version))
    statements.extend(cas_statements(CURRENT_DRAFT, draft_state, expected_draft_version))
    try:
        adapter.batch(statements)
    except StorageUniqueError as error:
        raise StorageConflictError() from error


def reset_runtime_rows(
    match_state, expected_match_version, expected_draft_version, *, storage=None
):
    from storage.errors import StorageConflictError, StorageUniqueError
    adapter = _adapter(storage)
    statements = []
    statements.extend(cas_statements(CURRENT_MATCH, match_state, expected_match_version))
    statements.extend(cas_statements(CURRENT_DRAFT, None, expected_draft_version))
    try:
        adapter.batch(statements)
    except StorageUniqueError as error:
        raise StorageConflictError() from error
