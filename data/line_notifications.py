"""Named LINE notification reads and writes across the storage boundary."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone

from flask import has_request_context

from data.match_sessions import MatchSessionRecord, format_utc_datetime, normalize_utc_datetime
from data.participants import ParticipantRecord

ACCOUNT_COLUMNS = "id, participant_id, line_user_id, display_name, active, created_at, updated_at"
SUBSCRIPTION_COLUMNS = "id, session_id, participant_id, channel, active, created_at, updated_at"
TOKEN_COLUMNS = "id, token, participant_id, session_id, used_at, expires_at, created_at"
NOTIFICATION_COLUMNS = "id, session_id, match_count, channel, status, created_at, sent_at"
DELIVERY_COLUMNS = "id, session_id, participant_id, match_count, channel, status, error_message, sent_at"


@dataclass(frozen=True)
class LineAccountRecord:
    id: int
    participant_id: int
    line_user_id: str
    display_name: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class NotificationSubscriptionRecord:
    id: int
    session_id: int
    participant_id: int
    channel: str
    active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class LineLinkTokenRecord:
    id: int
    token: str
    participant_id: int
    session_id: int
    used_at: datetime | None
    expires_at: datetime
    created_at: datetime
    participant: ParticipantRecord | None = None
    session: MatchSessionRecord | None = None


@dataclass(frozen=True)
class MatchNotificationRecord:
    id: int
    session_id: int
    match_count: int
    channel: str
    status: str
    created_at: datetime
    sent_at: datetime | None


@dataclass(frozen=True)
class NotificationDeliveryLogRecord:
    id: int
    session_id: int
    participant_id: int
    match_count: int
    channel: str
    status: str
    error_message: str | None
    sent_at: datetime


@dataclass(frozen=True)
class NotificationReservation:
    notification: MatchNotificationRecord
    owner: bool


def _adapter(storage):
    if storage is not None:
        return storage
    from storage.provider import create_storage, get_storage
    return get_storage() if has_request_context() else create_storage()


def _account(row):
    if row is None:
        return None
    return LineAccountRecord(
        int(row["id"]), int(row["participant_id"]), row["line_user_id"],
        row["display_name"], bool(row["active"]),
        normalize_utc_datetime(row["created_at"]), normalize_utc_datetime(row["updated_at"]),
    )


def _subscription(row):
    if row is None:
        return None
    return NotificationSubscriptionRecord(
        int(row["id"]), int(row["session_id"]), int(row["participant_id"]),
        row["channel"], bool(row["active"]), normalize_utc_datetime(row["created_at"]),
        normalize_utc_datetime(row["updated_at"]),
    )


def _token(row):
    if row is None:
        return None
    return LineLinkTokenRecord(
        int(row["id"]), row["token"], int(row["participant_id"]), int(row["session_id"]),
        normalize_utc_datetime(row["used_at"]), normalize_utc_datetime(row["expires_at"]),
        normalize_utc_datetime(row["created_at"]),
    )


def _notification(row):
    if row is None:
        return None
    return MatchNotificationRecord(
        int(row["id"]), int(row["session_id"]), int(row["match_count"]), row["channel"],
        row["status"], normalize_utc_datetime(row["created_at"]),
        normalize_utc_datetime(row["sent_at"]),
    )


def _delivery(row):
    return NotificationDeliveryLogRecord(
        int(row["id"]), int(row["session_id"]), int(row["participant_id"]),
        int(row["match_count"]), row["channel"], row["status"], row["error_message"],
        normalize_utc_datetime(row["sent_at"]),
    )


def get_notification_subscription(participant_id, session_id, *, storage=None):
    """Include inactive subscriptions so the caller can reactivate them."""
    return _subscription(_adapter(storage).first(
        f"SELECT {SUBSCRIPTION_COLUMNS} FROM notification_subscriptions "
        "WHERE session_id = ? AND participant_id = ? AND channel = 'line'",
        session_id, participant_id,
    ))


def get_line_notification_targets(session_id, *, storage=None):
    """Return active participant/account pairs without an N+1 query."""
    rows = _adapter(storage).all(
        "SELECT p.id AS p_id, p.name, p.gender, p.level, p.weight, p.games_played, "
        "p.active AS p_active, p.card, a.id AS a_id, "
        "a.participant_id AS a_participant_id, a.line_user_id, a.display_name, "
        "a.active AS a_active, a.created_at AS a_created_at, a.updated_at AS a_updated_at "
        "FROM participants p JOIN notification_subscriptions s ON s.participant_id = p.id "
        "JOIN line_accounts a ON a.participant_id = p.id "
        "WHERE s.session_id = ? AND s.channel = 'line' AND s.active = 1 "
        "AND a.active = 1 AND p.active = 1 ORDER BY p.id", session_id,
    )
    return [(
        ParticipantRecord(
            int(row["p_id"]), row["name"], row["gender"], row["level"],
            float(row["weight"]), int(row["games_played"] or 0),
            bool(row["p_active"]), row["card"],
        ),
        LineAccountRecord(
            int(row["a_id"]), int(row["a_participant_id"]), row["line_user_id"],
            row["display_name"], bool(row["a_active"]),
            normalize_utc_datetime(row["a_created_at"]),
            normalize_utc_datetime(row["a_updated_at"]),
        ),
    ) for row in rows]


def get_line_link_token_with_details(token_value, *, storage=None):
    adapter = _adapter(storage)
    record = get_line_link_token(token_value, storage=adapter)
    if record is None:
        return None
    participant_row = adapter.first(
        "SELECT id, name, gender, level, weight, games_played, active, card "
        "FROM participants WHERE id = ?", record.participant_id,
    )
    session_row = adapter.first(
        "SELECT id, status, match_count, created_at, confirmed_at, notification_sent_at "
        "FROM match_sessions WHERE id = ?", record.session_id,
    )
    participant = None if participant_row is None else ParticipantRecord(
        int(participant_row["id"]), participant_row["name"], participant_row["gender"],
        participant_row["level"], float(participant_row["weight"]),
        int(participant_row["games_played"] or 0), bool(participant_row["active"]),
        participant_row["card"],
    )
    session = None if session_row is None else MatchSessionRecord(
        int(session_row["id"]), session_row["status"], int(session_row["match_count"]),
        normalize_utc_datetime(session_row["created_at"]),
        normalize_utc_datetime(session_row["confirmed_at"]),
        normalize_utc_datetime(session_row["notification_sent_at"]),
    )
    return replace(record, participant=participant, session=session)


def get_conflicting_line_account(line_user_id, participant_id, *, storage=None):
    return _account(_adapter(storage).first(
        f"SELECT {ACCOUNT_COLUMNS} FROM line_accounts "
        "WHERE line_user_id = ? AND participant_id != ?", line_user_id, participant_id,
    ))


def get_line_account_for_participant(participant_id, *, storage=None):
    return _account(_adapter(storage).first(
        f"SELECT {ACCOUNT_COLUMNS} FROM line_accounts WHERE participant_id = ?", participant_id,
    ))


def get_past_line_subscription(participant_id, session_id, *, storage=None):
    return _subscription(_adapter(storage).first(
        f"SELECT {SUBSCRIPTION_COLUMNS} FROM notification_subscriptions "
        "WHERE participant_id = ? AND channel = 'line' AND session_id != ? LIMIT 1",
        participant_id, session_id,
    ))


def get_line_match_notification(session_id, match_count, *, storage=None):
    return _notification(_adapter(storage).first(
        f"SELECT {NOTIFICATION_COLUMNS} FROM match_notifications "
        "WHERE session_id = ? AND match_count = ? AND channel = 'line'",
        session_id, match_count,
    ))


def get_line_link_token(token_value, *, storage=None):
    return _token(_adapter(storage).first(
        f"SELECT {TOKEN_COLUMNS} FROM line_link_tokens WHERE token = ?", token_value,
    ))


def upsert_line_account(participant_id, line_user_id, display_name=None, *, now=None, storage=None):
    timestamp = format_utc_datetime(now or datetime.now(timezone.utc))
    result = _adapter(storage).run(
        "INSERT INTO line_accounts "
        "(participant_id, line_user_id, display_name, active, created_at, updated_at) "
        "VALUES (?, ?, ?, 1, ?, ?) ON CONFLICT(participant_id) DO UPDATE SET "
        "line_user_id = excluded.line_user_id, "
        "display_name = COALESCE(excluded.display_name, line_accounts.display_name), "
        "active = 1, updated_at = excluded.updated_at RETURNING " + ACCOUNT_COLUMNS,
        participant_id, line_user_id, display_name, timestamp, timestamp,
    )
    return _account(result.rows[0])


def upsert_line_subscription(participant_id, session_id, *, now=None, storage=None):
    timestamp = format_utc_datetime(now or datetime.now(timezone.utc))
    result = _adapter(storage).run(
        "INSERT INTO notification_subscriptions "
        "(session_id, participant_id, channel, active, created_at, updated_at) "
        "VALUES (?, ?, 'line', 1, ?, ?) "
        "ON CONFLICT(session_id, participant_id, channel) DO UPDATE SET "
        "active = 1, updated_at = excluded.updated_at RETURNING " + SUBSCRIPTION_COLUMNS,
        session_id, participant_id, timestamp, timestamp,
    )
    return _subscription(result.rows[0])


def set_line_subscription_active(participant_id, session_id, active, *, now=None, storage=None):
    timestamp = format_utc_datetime(now or datetime.now(timezone.utc))
    result = _adapter(storage).run(
        "UPDATE notification_subscriptions SET active = ?, updated_at = ? "
        "WHERE session_id = ? AND participant_id = ? AND channel = 'line' RETURNING "
        + SUBSCRIPTION_COLUMNS,
        1 if active else 0, timestamp, session_id, participant_id,
    )
    return _subscription(result.rows[0]) if result.rows else None


def create_line_link_token(token, participant_id, session_id, expires_at, *, now=None, storage=None):
    timestamp = format_utc_datetime(now or datetime.now(timezone.utc))
    result = _adapter(storage).run(
        "INSERT INTO line_link_tokens "
        "(token, participant_id, session_id, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?) RETURNING " + TOKEN_COLUMNS,
        token, participant_id, session_id, format_utc_datetime(expires_at), timestamp,
    )
    return _token(result.rows[0])


def consume_line_link_token_atomic(token, line_user_id, *, now=None, storage=None):
    """Claim a token and create/reactivate its link records in one transaction."""
    from storage.errors import StorageConflictError, StorageUniqueError

    adapter = _adapter(storage)
    timestamp = format_utc_datetime(now or datetime.now(timezone.utc))
    statements = [
        (
            "UPDATE line_link_tokens SET used_at = ? WHERE token = ? AND used_at IS NULL "
            "AND julianday(expires_at) > julianday(?) "
            "AND EXISTS (SELECT 1 FROM participants p "
            "WHERE p.id = line_link_tokens.participant_id AND p.active = 1)",
            (timestamp, token, timestamp),
        ),
        # changes() observes the guarded UPDATE inside the same SQLite/D1 batch.
        # A zero-row claim deliberately collides with the token unique key.
        (
            "INSERT INTO line_link_tokens "
            "(token, participant_id, session_id, expires_at, created_at) "
            "SELECT token, participant_id, session_id, expires_at, created_at "
            "FROM line_link_tokens WHERE token = ? AND changes() = 0", (token,),
        ),
        (
            "INSERT INTO line_accounts "
            "(participant_id, line_user_id, active, created_at, updated_at) "
            "SELECT participant_id, ?, 1, ?, ? FROM line_link_tokens "
            "WHERE token = ? AND used_at = ? ON CONFLICT(participant_id) DO UPDATE SET "
            "line_user_id = excluded.line_user_id, active = 1, updated_at = excluded.updated_at",
            (line_user_id, timestamp, timestamp, token, timestamp),
        ),
        (
            "INSERT INTO notification_subscriptions "
            "(session_id, participant_id, channel, active, created_at, updated_at) "
            "SELECT session_id, participant_id, 'line', 1, ?, ? FROM line_link_tokens "
            "WHERE token = ? AND used_at = ? "
            "ON CONFLICT(session_id, participant_id, channel) DO UPDATE SET "
            "active = 1, updated_at = excluded.updated_at",
            (timestamp, timestamp, token, timestamp),
        ),
    ]
    try:
        adapter.batch(statements)
    except StorageUniqueError as error:
        raise StorageConflictError() from error
    claimed = get_line_link_token_with_details(token, storage=adapter)
    if claimed is None:
        raise StorageConflictError()
    return claimed


def reserve_match_notification(session_id, match_count, channel="line", *, now=None, storage=None):
    adapter = _adapter(storage)
    timestamp = format_utc_datetime(now or datetime.now(timezone.utc))
    result = adapter.run(
        "INSERT INTO match_notifications "
        "(session_id, match_count, channel, status, created_at) "
        "VALUES (?, ?, ?, 'pending', ?) "
        "ON CONFLICT(session_id, match_count, channel) DO NOTHING RETURNING "
        + NOTIFICATION_COLUMNS,
        session_id, match_count, channel, timestamp,
    )
    if result.rows:
        return NotificationReservation(_notification(result.rows[0]), True)
    row = adapter.first(
        f"SELECT {NOTIFICATION_COLUMNS} FROM match_notifications "
        "WHERE session_id = ? AND match_count = ? AND channel = ?",
        session_id, match_count, channel,
    )
    return NotificationReservation(_notification(row), False)


def create_delivery_logs(session_id, participant_ids, match_count, channel="line", *, now=None, storage=None):
    ids = list(participant_ids)
    if not ids:
        return []
    timestamp = format_utc_datetime(now or datetime.now(timezone.utc))
    results = _adapter(storage).batch([(
        "INSERT INTO notification_delivery_logs "
        "(session_id, participant_id, match_count, channel, status, sent_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?) RETURNING " + DELIVERY_COLUMNS,
        (session_id, participant_id, match_count, channel, timestamp),
    ) for participant_id in ids])
    return [_delivery(result.rows[0]) for result in results]


def update_delivery_log_status(delivery_log_id, status, error_message=None, *, now=None, storage=None):
    timestamp = format_utc_datetime(now or datetime.now(timezone.utc))
    result = _adapter(storage).run(
        "UPDATE notification_delivery_logs SET status = ?, error_message = ?, sent_at = ? "
        "WHERE id = ? RETURNING " + DELIVERY_COLUMNS,
        status, error_message, timestamp, delivery_log_id,
    )
    return _delivery(result.rows[0]) if result.rows else None


def complete_match_notification(notification_id, *, now=None, storage=None):
    timestamp = format_utc_datetime(now or datetime.now(timezone.utc))
    result = _adapter(storage).run(
        "UPDATE match_notifications SET status = 'completed', sent_at = ? "
        "WHERE id = ? RETURNING " + NOTIFICATION_COLUMNS, timestamp, notification_id,
    )
    return _notification(result.rows[0]) if result.rows else None


def clear_line_notification_records(*, storage=None):
    return _adapter(storage).batch([
        ("DELETE FROM notification_delivery_logs", ()),
        ("DELETE FROM match_notifications", ()),
        ("DELETE FROM notification_subscriptions", ()),
        ("DELETE FROM line_link_tokens", ()),
        ("DELETE FROM line_accounts", ()),
    ])
