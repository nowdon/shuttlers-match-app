"""Named MatchSession reads and lifecycle commands."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import secrets

from flask import has_request_context


SESSION_COLUMNS = "id, status, match_count, created_at, confirmed_at, notification_sent_at"


@dataclass(frozen=True)
class MatchSessionRecord:
    id: int
    status: str
    match_count: int
    created_at: datetime
    confirmed_at: datetime | None
    notification_sent_at: datetime | None


def normalize_utc_datetime(value):
    if value is None or isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_utc_datetime(value):
    return normalize_utc_datetime(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _record(row):
    if row is None:
        return None
    return MatchSessionRecord(
        id=int(row["id"]), status=row["status"], match_count=int(row["match_count"]),
        created_at=normalize_utc_datetime(row["created_at"]),
        confirmed_at=normalize_utc_datetime(row["confirmed_at"]),
        notification_sent_at=normalize_utc_datetime(row["notification_sent_at"]),
    )


def _adapter(storage):
    if storage is not None:
        return storage
    from storage.provider import create_storage, get_storage
    return get_storage() if has_request_context() else create_storage()


def get_match_session_by_id(session_id, *, storage=None):
    return _record(_adapter(storage).first(
        f"SELECT {SESSION_COLUMNS} FROM match_sessions WHERE id = ?", session_id
    ))


def create_match_session(*, status="draft", created_at=None, storage=None):
    result = _adapter(storage).run(
        "INSERT INTO match_sessions (status, match_count, created_at) "
        "VALUES (?, 0, ?) RETURNING " + SESSION_COLUMNS,
        status, format_utc_datetime(created_at or datetime.now(timezone.utc)),
    )
    return _record(result.rows[0])


def close_match_session(session_id, *, storage=None):
    result = _adapter(storage).run(
        "UPDATE match_sessions SET status = 'closed' WHERE id = ? RETURNING " + SESSION_COLUMNS,
        session_id,
    )
    return _record(result.rows[0]) if result.rows else None


def _state_update_with_created_session(
    state, expected_version, creation_token, placeholder
):
    from data.runtime_state import CURRENT_MATCH, serialize_state

    state = dict(state)
    state["session_id"] = placeholder
    return (
        "UPDATE runtime_state SET state_json = REPLACE(?, ?, CAST(("
        "SELECT id FROM match_sessions WHERE creation_token = ?) AS TEXT)), "
        "version = version + 1 WHERE key = ? AND version = ?",
        (
            serialize_state(state), json.dumps(placeholder), creation_token,
            CURRENT_MATCH, expected_version,
        ),
    )


def create_current_match_session_atomic(
    match_state, expected_match_version, *, created_at=None, storage=None
):
    """Create a session and CAS its generated ID into current_match atomically."""
    from data.runtime_state import CURRENT_MATCH, cas_guard_statement
    from storage.errors import StorageConflictError, StorageUniqueError

    adapter = _adapter(storage)
    token = secrets.token_urlsafe(32)
    placeholder = f"__session_id_{secrets.token_hex(16)}__"
    timestamp = format_utc_datetime(created_at or datetime.now(timezone.utc))
    statements = [
        cas_guard_statement(CURRENT_MATCH, expected_match_version),
        (
            "INSERT INTO match_sessions "
            "(status, match_count, created_at, creation_token) "
            "VALUES ('draft', 0, ?, ?)",
            (timestamp, token),
        ),
        _state_update_with_created_session(
            match_state, expected_match_version, token, placeholder
        ),
    ]
    try:
        adapter.batch(statements)
    except StorageUniqueError as error:
        raise StorageConflictError() from error
    return _record(adapter.first(
        f"SELECT {SESSION_COLUMNS} FROM match_sessions WHERE creation_token = ?", token
    ))


def reset_match_session_atomic(
    match_state, expected_match_version, expected_draft_version, *,
    old_session_id=None, create_new_session=True, created_at=None, storage=None,
):
    """Reset sessions, counters, and both runtime rows in one transaction."""
    from data.runtime_state import (
        CURRENT_DRAFT,
        CURRENT_MATCH,
        cas_guard_statement,
        cas_update_statement,
    )
    from storage.errors import StorageConflictError, StorageUniqueError

    adapter = _adapter(storage)
    statements = [
        cas_guard_statement(CURRENT_MATCH, expected_match_version),
        cas_guard_statement(CURRENT_DRAFT, expected_draft_version),
    ]
    if old_session_id is not None:
        statements.append((
            "UPDATE match_sessions SET status = 'closed' WHERE id = ?",
            (old_session_id,),
        ))
    statements.append(("UPDATE participants SET games_played = 0", ()))
    created_token = None
    if create_new_session:
        created_token = secrets.token_urlsafe(32)
        placeholder = f"__session_id_{secrets.token_hex(16)}__"
        timestamp = format_utc_datetime(created_at or datetime.now(timezone.utc))
        statements.append((
            "INSERT INTO match_sessions "
            "(status, match_count, created_at, creation_token) "
            "VALUES ('draft', 0, ?, ?)",
            (timestamp, created_token),
        ))
        statements.append(_state_update_with_created_session(
            match_state, expected_match_version, created_token, placeholder
        ))
    else:
        statements.append(cas_update_statement(
            CURRENT_MATCH, match_state, expected_match_version
        ))
    statements.append(cas_update_statement(
        CURRENT_DRAFT, None, expected_draft_version
    ))
    try:
        adapter.batch(statements)
    except StorageUniqueError as error:
        raise StorageConflictError() from error
    if created_token is None:
        return None
    return _record(adapter.first(
        f"SELECT {SESSION_COLUMNS} FROM match_sessions WHERE creation_token = ?",
        created_token,
    ))
