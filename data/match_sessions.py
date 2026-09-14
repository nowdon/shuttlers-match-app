"""Named MatchSession reads and lifecycle commands."""

from dataclasses import dataclass
from datetime import datetime, timezone

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
