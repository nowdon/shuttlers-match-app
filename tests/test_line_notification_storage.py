from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from data.line_notifications import (
    complete_match_notification,
    consume_line_link_token_atomic,
    create_delivery_logs,
    create_line_link_token,
    get_line_account_for_participant,
    get_line_link_token,
    get_line_notification_targets,
    get_notification_subscription,
    reserve_match_notification,
    set_line_subscription_active,
    update_delivery_log_status,
    upsert_line_account,
    upsert_line_subscription,
)
from data.match_sessions import create_match_session
from storage.d1 import D1Storage
from storage.errors import StorageConflictError, StorageUniqueError
from storage.sqlite import SQLiteStorage


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = [ROOT / "migrations/d1" / name for name in (
    "0001_phase3_participants_config.sql",
    "0002_phase4_match_relational.sql",
    "0003_phase5_runtime_state.sql",
    "0004_phase6_line_notifications.sql",
)]
NOW = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)


class Promise:
    def __init__(self, value=None, error=None):
        self.value, self.error = value, error


class Statement:
    def __init__(self, binding, sql):
        self.binding, self.sql, self.params = binding, sql, ()

    def bind(self, *params):
        self.params = params
        return self

    def run(self):
        return self.binding.execute(self, "run")

    def first(self):
        return self.binding.execute(self, "first")

    def all(self):
        return self.binding.execute(self, "all")


class SQLiteD1Binding:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        for migration in MIGRATIONS:
            self.connection.executescript(migration.read_text(encoding="utf-8"))

    def prepare(self, sql):
        return Statement(self, sql)

    def execute(self, statement, method):
        try:
            cursor = self.connection.execute(statement.sql, statement.params)
            if method == "first":
                row = cursor.fetchone()
                return Promise(None if row is None else dict(row))
            rows = [dict(row) for row in cursor.fetchall()] if cursor.description else []
            self.connection.commit()
            return Promise({"success": True, "results": rows, "meta": {"changes": cursor.rowcount}})
        except sqlite3.Error as error:
            self.connection.rollback()
            return Promise(error=error)

    def batch(self, statements):
        results = []
        try:
            self.connection.execute("BEGIN")
            for statement in statements:
                cursor = self.connection.execute(statement.sql, statement.params)
                rows = [dict(row) for row in cursor.fetchall()] if cursor.description else []
                results.append({"success": True, "results": rows, "meta": {"changes": cursor.rowcount}})
            self.connection.commit()
            return Promise(results)
        except sqlite3.Error as error:
            self.connection.rollback()
            return Promise(error=error)


def run_sync(promise):
    if promise.error is not None:
        raise promise.error
    return promise.value


@pytest.fixture(params=["sqlite", "d1"])
def line_storage(request, tmp_path):
    if request.param == "sqlite":
        storage = SQLiteStorage(tmp_path / "phase6.db")
        for migration in MIGRATIONS:
            storage._get_connection().executescript(migration.read_text(encoding="utf-8"))
        yield storage
        storage.close()
    else:
        binding = SQLiteD1Binding()
        yield D1Storage(binding, run_sync=run_sync)
        binding.connection.close()


def seed_participant(storage, participant_id, *, active=True):
    storage.run(
        "INSERT INTO participants "
        "(id, name, gender, level, weight, games_played, active, card) "
        "VALUES (?, ?, 'male', 'beginner', 1.0, 0, ?, ?)",
        participant_id, f"player-{participant_id}", 1 if active else 0, f"C{participant_id}",
    )


def test_account_subscription_and_target_contract(line_storage):
    storage = line_storage
    seed_participant(storage, 1)
    seed_participant(storage, 2)
    session = create_match_session(created_at=NOW, storage=storage)

    account = upsert_line_account(1, "U1", now=NOW, storage=storage)
    assert account.active is True
    assert upsert_line_account(1, "U1-new", now=NOW + timedelta(seconds=1), storage=storage).id == account.id
    with pytest.raises(StorageUniqueError):
        upsert_line_account(2, "U1-new", now=NOW, storage=storage)

    subscription = upsert_line_subscription(1, session.id, now=NOW, storage=storage)
    assert subscription.active is True
    assert set_line_subscription_active(1, session.id, False, now=NOW, storage=storage).active is False
    assert upsert_line_subscription(1, session.id, now=NOW, storage=storage).active is True
    assert len(get_line_notification_targets(session.id, storage=storage)) == 1
    set_line_subscription_active(1, session.id, False, now=NOW, storage=storage)
    assert get_line_notification_targets(session.id, storage=storage) == []


def test_token_consume_once_and_transaction_rollback(line_storage):
    storage = line_storage
    seed_participant(storage, 1)
    seed_participant(storage, 2)
    session = create_match_session(created_at=NOW, storage=storage)
    create_line_link_token(
        "TOKEN1", 1, session.id, NOW + timedelta(minutes=30), now=NOW, storage=storage
    )
    consumed = consume_line_link_token_atomic(
        "TOKEN1", "U1", now=NOW + timedelta(seconds=1), storage=storage
    )
    assert consumed.used_at is not None
    assert get_line_account_for_participant(1, storage=storage).line_user_id == "U1"
    assert get_notification_subscription(1, session.id, storage=storage).active is True
    with pytest.raises(StorageConflictError):
        consume_line_link_token_atomic(
            "TOKEN1", "U-other", now=NOW + timedelta(seconds=2), storage=storage
        )

    upsert_line_account(2, "CONFLICT", now=NOW, storage=storage)
    second_session = create_match_session(created_at=NOW, storage=storage)
    create_line_link_token(
        "TOKEN2", 1, second_session.id, NOW + timedelta(minutes=30), now=NOW, storage=storage
    )
    with pytest.raises(StorageConflictError):
        consume_line_link_token_atomic(
            "TOKEN2", "CONFLICT", now=NOW + timedelta(seconds=1), storage=storage
        )
    assert get_line_link_token("TOKEN2", storage=storage).used_at is None
    assert storage.first(
        "SELECT COUNT(*) AS count FROM notification_subscriptions "
        "WHERE participant_id = 1 AND session_id = ?", second_session.id,
    )["count"] == 0


def test_expired_token_consume_is_rejected_without_side_effects(line_storage):
    storage = line_storage
    seed_participant(storage, 1)
    session = create_match_session(created_at=NOW, storage=storage)
    create_line_link_token(
        "EXPIRED", 1, session.id, NOW + timedelta(seconds=1), now=NOW, storage=storage
    )

    with pytest.raises(StorageConflictError):
        consume_line_link_token_atomic(
            "EXPIRED", "U-expired", now=NOW + timedelta(seconds=2), storage=storage
        )

    assert get_line_link_token("EXPIRED", storage=storage).used_at is None
    assert get_line_account_for_participant(1, storage=storage) is None
    assert get_notification_subscription(1, session.id, storage=storage) is None


def test_inactive_participant_token_consume_is_rejected_without_side_effects(
    line_storage,
):
    storage = line_storage
    seed_participant(storage, 1, active=False)
    session = create_match_session(created_at=NOW, storage=storage)
    create_line_link_token(
        "INACTIVE", 1, session.id, NOW + timedelta(minutes=30), now=NOW, storage=storage
    )

    with pytest.raises(StorageConflictError):
        consume_line_link_token_atomic(
            "INACTIVE", "U-inactive", now=NOW + timedelta(seconds=1), storage=storage
        )

    assert get_line_link_token("INACTIVE", storage=storage).used_at is None
    assert get_line_account_for_participant(1, storage=storage) is None
    assert get_notification_subscription(1, session.id, storage=storage) is None


def test_current_session_target_filtering_contract(line_storage):
    storage = line_storage
    for participant_id in range(1, 6):
        seed_participant(storage, participant_id, active=participant_id != 4)
    past = create_match_session(status="closed", created_at=NOW, storage=storage)
    current = create_match_session(created_at=NOW, storage=storage)

    for participant_id in range(1, 6):
        upsert_line_account(
            participant_id, f"U{participant_id}", now=NOW, storage=storage
        )
    upsert_line_subscription(1, past.id, now=NOW, storage=storage)
    for participant_id in range(2, 6):
        upsert_line_subscription(participant_id, current.id, now=NOW, storage=storage)
    storage.run("UPDATE line_accounts SET active = 0 WHERE participant_id = 3")
    set_line_subscription_active(5, current.id, False, now=NOW, storage=storage)

    assert [
        participant.id
        for participant, _account in get_line_notification_targets(
            current.id, storage=storage
        )
    ] == [2]
    assert [
        participant.id
        for participant, _account in get_line_notification_targets(
            past.id, storage=storage
        )
    ] == [1]


def test_reservation_delivery_and_next_match_contract(line_storage):
    storage = line_storage
    seed_participant(storage, 1)
    session = create_match_session(created_at=NOW, storage=storage)
    first = reserve_match_notification(session.id, 1, now=NOW, storage=storage)
    assert first.owner is True
    assert reserve_match_notification(session.id, 1, now=NOW, storage=storage).owner is False
    assert reserve_match_notification(session.id, 2, now=NOW, storage=storage).owner is True

    logs = create_delivery_logs(session.id, [1], 1, now=NOW, storage=storage)
    assert logs[0].status == "pending"
    assert update_delivery_log_status(logs[0].id, "failed", "test", now=NOW, storage=storage).status == "failed"
    assert update_delivery_log_status(logs[0].id, "success", now=NOW, storage=storage).status == "success"
    assert update_delivery_log_status(logs[0].id, "skipped", "not selected", now=NOW, storage=storage).status == "skipped"
    completed = complete_match_notification(first.notification.id, now=NOW, storage=storage)
    assert completed.status == "completed" and completed.sent_at == NOW


def test_no_target_reservation_can_be_completed(line_storage):
    storage = line_storage
    seed_participant(storage, 1)
    session = create_match_session(created_at=NOW, storage=storage)

    reservation = reserve_match_notification(session.id, 1, now=NOW, storage=storage)

    assert reservation.owner is True
    assert get_line_notification_targets(session.id, storage=storage) == []
    assert create_delivery_logs(session.id, [], 1, now=NOW, storage=storage) == []
    assert complete_match_notification(
        reservation.notification.id, now=NOW, storage=storage
    ).status == "completed"


def test_parallel_sqlite_reservation_has_one_owner(tmp_path):
    database = tmp_path / "parallel.db"
    setup = SQLiteStorage(database)
    for migration in MIGRATIONS:
        setup._get_connection().executescript(migration.read_text(encoding="utf-8"))
    seed_participant(setup, 1)
    session = create_match_session(created_at=NOW, storage=setup)
    setup.close()

    def reserve():
        storage = SQLiteStorage(database)
        try:
            return reserve_match_notification(session.id, 1, now=NOW, storage=storage).owner
        finally:
            storage.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: reserve(), range(2))) == [False, True]
    check = SQLiteStorage(database)
    assert check.first("SELECT COUNT(*) AS count FROM match_notifications")["count"] == 1
    check.close()


def test_parallel_sqlite_token_consume_has_one_success(tmp_path):
    database = tmp_path / "token-race.db"
    setup = SQLiteStorage(database)
    for migration in MIGRATIONS:
        setup._get_connection().executescript(migration.read_text(encoding="utf-8"))
    seed_participant(setup, 1)
    session = create_match_session(created_at=NOW, storage=setup)
    create_line_link_token(
        "RACE", 1, session.id, NOW + timedelta(minutes=30), now=NOW, storage=setup
    )
    setup.close()

    def consume(line_user_id):
        storage = SQLiteStorage(database)
        try:
            consume_line_link_token_atomic(
                "RACE", line_user_id, now=NOW + timedelta(seconds=1), storage=storage
            )
            return True
        except StorageConflictError:
            return False
        finally:
            storage.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(consume, ("U1", "U2"))) == [False, True]
    check = SQLiteStorage(database)
    assert check.first("SELECT COUNT(*) AS count FROM line_accounts")["count"] == 1
    assert check.first("SELECT COUNT(*) AS count FROM notification_subscriptions")["count"] == 1
    assert check.first("SELECT used_at FROM line_link_tokens WHERE token = 'RACE'")["used_at"] is not None
    check.close()
