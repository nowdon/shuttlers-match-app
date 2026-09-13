from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from data.match_history import (
    MatchScoreUpdate,
    clear_match_history,
    confirm_match_relational,
    get_match_rounds_for_dump,
    get_match_rounds_with_details,
    revert_match_relational,
    update_match_score,
    update_round_scores,
)
from data.match_sessions import (
    close_match_session,
    create_match_session,
    format_utc_datetime,
    get_match_session_by_id,
    normalize_utc_datetime,
)
from storage.d1 import D1Storage
from storage.errors import StorageConflictError
from storage.sqlite import SQLiteStorage


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = [
    ROOT / "migrations/d1/0001_phase3_participants_config.sql",
    ROOT / "migrations/d1/0002_phase4_match_relational.sql",
]


class Promise:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error


class Statement:
    def __init__(self, binding, sql):
        self.binding = binding
        self.sql = sql
        self.params = ()

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
def relational_storage(request, tmp_path):
    if request.param == "sqlite":
        storage = SQLiteStorage(tmp_path / "phase4.db")
        for migration in MIGRATIONS:
            storage._get_connection().executescript(migration.read_text(encoding="utf-8"))
        yield storage
        storage.close()
    else:
        binding = SQLiteD1Binding()
        yield D1Storage(binding, run_sync=run_sync)
        binding.connection.close()


def seed_participants(storage, count=5):
    for participant_id in range(1, count + 1):
        storage.run(
            "INSERT INTO participants "
            "(id, name, gender, level, weight, games_played, active, card) "
            "VALUES (?, ?, 'male', 'beginner', 1.0, 0, 1, ?)",
            participant_id, f"player-{participant_id}", f"C{participant_id}",
        )


def test_session_confirm_score_revert_and_clear_contract(relational_storage):
    storage = relational_storage
    seed_participants(storage)
    session = create_match_session(storage=storage)
    assert get_match_session_by_id(session.id, storage=storage) == session

    confirmed = confirm_match_relational(
        session.id, 1, [[1, 2, 3, 4]], [5], storage=storage
    )
    assert confirmed.session_id == session.id
    assert [match.court_number for match in confirmed.matches] == [1]
    assert [bench.participant_id for bench in confirmed.bench_players] == [5]
    assert storage.all("SELECT games_played FROM participants ORDER BY id") == [
        {"games_played": 1}, {"games_played": 1}, {"games_played": 1},
        {"games_played": 1}, {"games_played": 0},
    ]
    saved_session = get_match_session_by_id(session.id, storage=storage)
    assert saved_session.status == "confirmed" and saved_session.match_count == 1

    match_id = confirmed.matches[0].id
    update_match_score(MatchScoreUpdate(match_id, 1, 0, "21-15", 1), storage=storage)
    update_round_scores(
        [MatchScoreUpdate(match_id, 1, 1, "21-15\n10-21", None)], storage=storage
    )
    assert get_match_rounds_with_details(storage=storage)[0].matches[0].team2_score == 1

    reverted = revert_match_relational(session.id, 1, [1, 2, 3, 4], storage=storage)
    assert reverted.id == confirmed.id
    assert get_match_rounds_for_dump(storage=storage) == []
    assert storage.first("SELECT games_played FROM participants WHERE id = 1")["games_played"] == 0

    # Repeating the same session-scoped revert is a complete no-op.
    assert revert_match_relational(session.id, 1, [1], storage=storage) is None
    assert storage.first("SELECT games_played FROM participants WHERE id = 1")["games_played"] == 0
    assert close_match_session(session.id, storage=storage).status == "closed"
    clear_match_history(storage=storage)


def test_double_confirm_is_conflict_and_does_not_double_increment(relational_storage):
    storage = relational_storage
    seed_participants(storage, 4)
    session = create_match_session(storage=storage)
    confirm_match_relational(session.id, 1, [[1, 2, 3, 4]], [], storage=storage)
    with pytest.raises(StorageConflictError):
        confirm_match_relational(session.id, 1, [[1, 2, 3, 4]], [], storage=storage)
    assert storage.first("SELECT COUNT(*) AS count FROM match_rounds")["count"] == 1
    assert storage.first("SELECT COUNT(*) AS count FROM match_histories")["count"] == 1
    assert storage.first("SELECT games_played FROM participants WHERE id = 1")["games_played"] == 1


def test_history_ordering_normalizes_mixed_timestamps_and_ties(relational_storage):
    storage = relational_storage
    timestamps = [
        "2026-09-14 09:00:00.000000",
        "2026-09-14T08:00:00.000000Z",
        "2026-09-14 10:00:00.000000",
        "2026-09-14T10:00:00.000000Z",
    ]
    for round_number, created_at in enumerate(timestamps, start=1):
        storage.run(
            "INSERT INTO match_rounds (round_number, created_at) VALUES (?, ?)",
            round_number, created_at,
        )

    ascending = get_match_rounds_for_dump(storage=storage)
    descending = get_match_rounds_with_details(storage=storage)
    assert [item.round_number for item in ascending] == [2, 1, 3, 4]
    assert [item.round_number for item in descending] == [4, 3, 1, 2]
    assert all(item.created_at.tzinfo == timezone.utc for item in ascending)
    assert format_utc_datetime(datetime(2026, 1, 1, tzinfo=timezone.utc)) < format_utc_datetime(
        datetime(2026, 1, 2, tzinfo=timezone.utc)
    )
    assert normalize_utc_datetime("2026-01-01 12:00:00.000000").tzinfo == timezone.utc


def test_session_revert_does_not_fall_back_to_legacy_round(relational_storage):
    storage = relational_storage
    seed_participants(storage)
    storage.run(
        "INSERT INTO match_rounds (round_number, created_at) VALUES (1, ?)",
        "2026-09-14 07:00:00.000000",
    )
    legacy_round_id = storage.first(
        "SELECT id FROM match_rounds ORDER BY id DESC LIMIT 1"
    )["id"]
    storage.run(
        "INSERT INTO match_histories "
        "(round_id, court_number, team1_player1_id, team1_player2_id, "
        "team2_player1_id, team2_player2_id, created_at) "
        "VALUES (?, 1, 1, 2, 3, 4, ?)",
        legacy_round_id, "2026-09-14 07:00:00.000000",
    )
    storage.run(
        "INSERT INTO bench_histories (round_id, participant_id, created_at) "
        "VALUES (?, 5, ?)",
        legacy_round_id, "2026-09-14 07:00:00.000000",
    )

    session = create_match_session(storage=storage)
    confirmed = confirm_match_relational(
        session.id, 1, [[1, 2, 3, 4]], [5], storage=storage
    )
    assert revert_match_relational(
        session.id, 1, [1, 2, 3, 4], storage=storage
    ).id == confirmed.id
    assert revert_match_relational(
        session.id, 1, [1, 2, 3, 4], storage=storage
    ) is None

    assert storage.first(
        "SELECT id FROM match_rounds WHERE id = ?", legacy_round_id
    ) == {"id": legacy_round_id}
    assert storage.first(
        "SELECT COUNT(*) AS count FROM match_histories WHERE round_id = ?",
        legacy_round_id,
    )["count"] == 1
    assert storage.first(
        "SELECT COUNT(*) AS count FROM bench_histories WHERE round_id = ?",
        legacy_round_id,
    )["count"] == 1
    assert storage.all("SELECT games_played FROM participants ORDER BY id") == [
        {"games_played": 0}, {"games_played": 0}, {"games_played": 0},
        {"games_played": 0}, {"games_played": 0},
    ]


def test_legacy_revert_uses_highest_id_round(relational_storage):
    storage = relational_storage
    seed_participants(storage, 4)
    storage.run("UPDATE participants SET games_played = 1")
    round_ids = []
    for created_at in ("2026-09-14 07:00:00.000000", "2026-09-14 08:00:00.000000"):
        storage.run(
            "INSERT INTO match_rounds (round_number, created_at) VALUES (1, ?)",
            created_at,
        )
        round_id = storage.first(
            "SELECT id FROM match_rounds ORDER BY id DESC LIMIT 1"
        )["id"]
        round_ids.append(round_id)
        storage.run(
            "INSERT INTO match_histories "
            "(round_id, court_number, team1_player1_id, team1_player2_id, "
            "team2_player1_id, team2_player2_id, created_at) "
            "VALUES (?, 1, 1, 2, 3, 4, ?)",
            round_id, created_at,
        )

    reverted = revert_match_relational(None, 1, [1, 2, 3, 4], storage=storage)
    assert reverted.id == round_ids[1]
    assert storage.all("SELECT id FROM match_rounds ORDER BY id") == [
        {"id": round_ids[0]}
    ]
    assert storage.all("SELECT games_played FROM participants ORDER BY id") == [
        {"games_played": 0}, {"games_played": 0},
        {"games_played": 0}, {"games_played": 0},
    ]


def test_detailed_round_read_uses_three_queries_not_n_plus_one(relational_storage):
    storage = relational_storage
    seed_participants(storage, 4)
    session = create_match_session(storage=storage)
    confirm_match_relational(session.id, 1, [[1, 2, 3, 4]], [], storage=storage)
    confirm_match_relational(session.id, 2, [[1, 2, 3, 4]], [], storage=storage)

    class CountingStorage:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.all_calls = 0

        def all(self, sql, *params):
            self.all_calls += 1
            return self.wrapped.all(sql, *params)

    counted = CountingStorage(storage)
    rounds = get_match_rounds_with_details(storage=counted)
    assert len(rounds) == 2
    assert counted.all_calls == 3
