import json
from pathlib import Path
import sqlite3

import pytest

from data.match_history import confirm_match_atomic, revert_match_atomic
from data.full_reset import (
    reset_all_application_data,
    reset_all_application_data_atomic,
)
from data.match_sessions import (
    create_current_match_session_atomic,
    reset_match_session_atomic,
)
from data.runtime_state import (
    load_current_draft,
    load_current_match,
    save_current_draft,
    save_current_match,
)
from storage.d1 import D1Storage
from storage.errors import StorageConflictError, StorageError, StorageUniqueError
from storage.sqlite import SQLiteStorage
from utils.draft_state import build_draft_state
from utils.match_state import build_match_state
from utils.runtime_state_migration import ensure_runtime_state_storage


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = sorted((ROOT / "migrations/d1").glob("*.sql"))


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
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
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
            return Promise({
                "success": True, "results": rows,
                "meta": {"changes": cursor.rowcount},
            })
        except sqlite3.Error as error:
            self.connection.rollback()
            return Promise(error=error)

    def batch(self, statements):
        results = []
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            for statement in statements:
                cursor = self.connection.execute(statement.sql, statement.params)
                rows = [dict(row) for row in cursor.fetchall()] if cursor.description else []
                results.append({
                    "success": True, "results": rows,
                    "meta": {"changes": cursor.rowcount},
                })
            self.connection.commit()
            return Promise(results)
        except sqlite3.Error as error:
            self.connection.rollback()
            return Promise(error=error)


def run_sync(promise):
    if promise.error is not None:
        raise promise.error
    return promise.value


@pytest.fixture(params=("sqlite", "d1"))
def runtime_storage(request, tmp_path):
    if request.param == "sqlite":
        adapter = SQLiteStorage(tmp_path / "runtime.db")
        for migration in MIGRATIONS:
            adapter._get_connection().executescript(migration.read_text(encoding="utf-8"))
        yield adapter
        adapter.close()
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


FULL_RESET_TABLES = (
    "participants", "match_sessions", "match_rounds", "match_histories",
    "bench_histories", "line_accounts", "notification_subscriptions",
    "line_link_tokens", "match_notifications", "notification_delivery_logs",
)


def seed_full_reset_data(storage):
    timestamp = "2026-09-17T01:02:03.000000Z"
    seed_participants(storage, 4)
    storage.run(
        "INSERT INTO app_config (key, config_json, version) VALUES (?, ?, ?)",
        "main", '{"preserved":true}', 9,
    )
    storage.run(
        "INSERT INTO match_sessions (id, status, match_count, created_at, creation_token) "
        "VALUES (1, 'confirmed', 1, ?, 'creation-token')",
        timestamp,
    )
    storage.run(
        "INSERT INTO match_rounds (id, session_id, round_number, created_at) "
        "VALUES (1, 1, 1, ?)", timestamp,
    )
    storage.run(
        "INSERT INTO match_histories "
        "(id, round_id, court_number, team1_player1_id, team1_player2_id, "
        "team2_player1_id, team2_player2_id, created_at) "
        "VALUES (1, 1, 1, 1, 2, 3, 4, ?)", timestamp,
    )
    storage.run(
        "INSERT INTO bench_histories (id, round_id, participant_id, created_at) "
        "VALUES (1, 1, 1, ?)", timestamp,
    )
    storage.run(
        "INSERT INTO line_accounts "
        "(id, participant_id, line_user_id, active, created_at, updated_at) "
        "VALUES (1, 1, 'U1', 1, ?, ?)", timestamp, timestamp,
    )
    storage.run(
        "INSERT INTO notification_subscriptions "
        "(id, session_id, participant_id, channel, active, created_at, updated_at) "
        "VALUES (1, 1, 1, 'line', 1, ?, ?)", timestamp, timestamp,
    )
    storage.run(
        "INSERT INTO line_link_tokens "
        "(id, token, participant_id, session_id, expires_at, created_at) "
        "VALUES (1, 'TOKEN', 1, 1, ?, ?)", timestamp, timestamp,
    )
    storage.run(
        "INSERT INTO match_notifications "
        "(id, session_id, match_count, channel, status, created_at) "
        "VALUES (1, 1, 1, 'line', 'completed', ?)", timestamp,
    )
    storage.run(
        "INSERT INTO notification_delivery_logs "
        "(id, session_id, participant_id, match_count, channel, status, sent_at) "
        "VALUES (1, 1, 1, 1, 'line', 'success', ?)", timestamp,
    )
    match = load_current_match(storage=storage)
    draft = load_current_draft(storage=storage)
    save_current_match(
        {"match_active": True, "match_count": 1, "matches": [[1, 2, 3, 4]],
         "bench": [], "session_id": 1, "timestamp": timestamp},
        match.version, storage=storage,
    )
    save_current_draft(
        {"draft": True, "matches": [[1, 2, 3, 4]], "bench": []},
        draft.version, storage=storage,
    )


def assert_full_reset_seed_survives(storage):
    for table in FULL_RESET_TABLES:
        assert storage.first(f"SELECT COUNT(*) AS n FROM {table}")["n"] > 0


def test_full_reset_is_atomic_and_preserves_config_and_runtime_rows(runtime_storage):
    storage = runtime_storage
    seed_full_reset_data(storage)
    before_match = load_current_match(storage=storage)
    before_draft = load_current_draft(storage=storage)

    reset_all_application_data_atomic(
        before_match.version,
        before_draft.version,
        storage=storage,
        timestamp="2026-09-17T12:34:56+09:00",
    )

    for table in FULL_RESET_TABLES:
        assert storage.first(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0
    assert storage.first("SELECT COUNT(*) AS n FROM runtime_state")["n"] == 2
    after_match = load_current_match(storage=storage)
    after_draft = load_current_draft(storage=storage)
    assert after_match.state == {
        "match_active": False, "match_count": 0, "matches": [], "bench": [],
        "session_id": None, "timestamp": "2026-09-17T12:34:56+09:00",
    }
    assert after_draft.state is None
    assert after_match.version == before_match.version + 1
    assert after_draft.version == before_draft.version + 1
    assert storage.first(
        "SELECT config_json, version FROM app_config WHERE key = 'main'"
    ) == {"config_json": '{"preserved":true}', "version": 9}

    storage.run(
        "INSERT INTO participants "
        "(id, name, gender, level, weight, games_played, active, card) "
        "VALUES (10, 'after-reset', 'male', 'beginner', 1.0, 0, 1, 'C10')"
    )
    current = load_current_match(storage=storage)
    recovered_session = create_current_match_session_atomic(
        current.state, current.version, storage=storage
    )
    assert recovered_session is not None
    assert load_current_match(storage=storage).state["session_id"] == recovered_session.id


def test_full_reset_stale_cas_rolls_back_every_relational_delete(runtime_storage):
    storage = runtime_storage
    seed_full_reset_data(storage)
    stale_match = load_current_match(storage=storage)
    draft = load_current_draft(storage=storage)
    changed_state = dict(stale_match.state, match_count=2)
    save_current_match(changed_state, stale_match.version, storage=storage)
    before_match = load_current_match(storage=storage)

    with pytest.raises(StorageConflictError):
        reset_all_application_data_atomic(
            stale_match.version, draft.version, storage=storage
        )

    assert_full_reset_seed_survives(storage)
    assert load_current_match(storage=storage) == before_match
    assert load_current_draft(storage=storage) == draft


def test_full_reset_middle_failure_rolls_back_relational_and_runtime(runtime_storage):
    storage = runtime_storage
    seed_full_reset_data(storage)
    before_match = load_current_match(storage=storage)
    before_draft = load_current_draft(storage=storage)

    class FailingBatchStorage:
        def first(self, *args):
            return storage.first(*args)

        def batch(self, statements):
            statements = list(statements)
            statements.insert(
                8, ("INSERT INTO runtime_state_cas_guard (id) VALUES (1)", ())
            )
            return storage.batch(statements)

    with pytest.raises(StorageConflictError):
        reset_all_application_data_atomic(
            before_match.version,
            before_draft.version,
            storage=FailingBatchStorage(),
        )

    assert_full_reset_seed_survives(storage)
    assert load_current_match(storage=storage) == before_match
    assert load_current_draft(storage=storage) == before_draft


def test_full_reset_retries_a_bounded_cas_conflict(runtime_storage):
    storage = runtime_storage
    seed_full_reset_data(storage)

    class ConflictOnceStorage:
        def __init__(self):
            self.batch_calls = 0

        def first(self, *args):
            return storage.first(*args)

        def batch(self, statements):
            self.batch_calls += 1
            if self.batch_calls == 1:
                raise StorageUniqueError()
            return storage.batch(statements)

    wrapper = ConflictOnceStorage()
    reset_all_application_data(storage=wrapper)
    assert wrapper.batch_calls == 2
    for table in FULL_RESET_TABLES:
        assert storage.first(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0


def test_runtime_state_cas_and_tombstone_contract(runtime_storage):
    match = load_current_match(storage=runtime_storage)
    draft = load_current_draft(storage=runtime_storage)
    assert match.state == {
        "bench": [], "match_active": False, "match_count": 0, "matches": [],
    }
    assert match.version == draft.version == 1 and draft.state is None

    saved = save_current_draft(
        {"draft": True, "matches": [[1, 2, 3, 4]], "bench": [],
         "fixed_pairs": [[1, 2]]},
        draft.version, storage=runtime_storage,
    )
    assert saved.version == 2 and saved.state["fixed_pairs"] == [[1, 2]]
    with pytest.raises(StorageConflictError):
        save_current_draft(
            {"draft": True, "matches": [], "bench": []},
            draft.version, storage=runtime_storage,
        )
    cleared = save_current_draft(None, saved.version, storage=runtime_storage)
    assert cleared.state is None and cleared.version == 3


def test_confirm_revert_and_reset_are_atomic_with_runtime_state(runtime_storage):
    storage = runtime_storage
    seed_participants(storage)
    match = load_current_match(storage=storage)
    session = create_current_match_session_atomic(
        match.state, match.version, storage=storage
    )
    draft = load_current_draft(storage=storage)
    draft_state = build_draft_state(
        [[1, 2, 3, 4]], [5], court_count=1, fixed_pairs=[[1, 2]]
    )
    save_current_draft(draft_state, draft.version, storage=storage)
    match = load_current_match(storage=storage)
    draft = load_current_draft(storage=storage)
    confirmed_state = build_match_state(
        True, [[1, 2, 3, 4]], [5], 1, court_count=1,
        existing_state=match.state,
    )
    confirm_match_atomic(
        session.id, 1, [[1, 2, 3, 4]], [5], confirmed_state,
        match.version, draft.version, storage=storage,
    )
    assert load_current_match(storage=storage).state["match_count"] == 1
    assert load_current_draft(storage=storage).state is None
    assert storage.first("SELECT COUNT(*) AS n FROM match_rounds")["n"] == 1
    assert storage.first(
        "SELECT games_played FROM participants WHERE id = 1"
    )["games_played"] == 1

    with pytest.raises(StorageConflictError):
        confirm_match_atomic(
            session.id, 1, [[1, 2, 3, 4]], [5], confirmed_state,
            match.version, draft.version, storage=storage,
        )
    assert storage.first("SELECT COUNT(*) AS n FROM match_rounds")["n"] == 1

    match = load_current_match(storage=storage)
    draft = load_current_draft(storage=storage)
    restored = build_draft_state([[1, 2, 3, 4]], [5], court_count=1)
    reverted = build_match_state(
        False, [], [], 0, court_count=1, existing_state=match.state
    )
    revert_match_atomic(
        session.id, 1, [1, 2, 3, 4], reverted, restored,
        match.version, draft.version, storage=storage,
    )
    assert storage.first("SELECT COUNT(*) AS n FROM match_rounds")["n"] == 0
    assert load_current_draft(storage=storage).state["matches"] == [[1, 2, 3, 4]]

    match = load_current_match(storage=storage)
    draft = load_current_draft(storage=storage)
    new_session = reset_match_session_atomic(
        {"match_active": False, "match_count": 0, "matches": [], "bench": []},
        match.version, draft.version, old_session_id=session.id,
        storage=storage,
    )
    assert load_current_match(storage=storage).state["session_id"] == new_session.id
    assert load_current_draft(storage=storage).state is None


def test_confirm_constraint_failure_rolls_back_runtime_and_relational(runtime_storage):
    storage = runtime_storage
    seed_participants(storage, 4)
    match = load_current_match(storage=storage)
    session = create_current_match_session_atomic(match.state, match.version, storage=storage)
    match = load_current_match(storage=storage)
    draft = load_current_draft(storage=storage)
    draft_state = build_draft_state([[1, 2, 3, 999]], [], court_count=1)
    save_current_draft(draft_state, draft.version, storage=storage)
    draft = load_current_draft(storage=storage)
    before_match = load_current_match(storage=storage)
    with pytest.raises(StorageError):
        confirm_match_atomic(
            session.id, 1, [[1, 2, 3, 999]], [],
            build_match_state(
                True, [[1, 2, 3, 999]], [], 1,
                existing_state=before_match.state,
            ),
            before_match.version, draft.version, storage=storage,
        )
    assert load_current_match(storage=storage) == before_match
    assert load_current_draft(storage=storage) == draft
    assert storage.first("SELECT COUNT(*) AS n FROM match_rounds")["n"] == 0
    assert storage.first(
        "SELECT games_played FROM participants WHERE id = 1"
    )["games_played"] == 0


def test_sqlite_legacy_import_is_one_time_and_keeps_files(tmp_path):
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        (ROOT / "migrations/d1/0001_phase3_participants_config.sql").read_text()
        + (ROOT / "migrations/d1/0002_phase4_match_relational.sql").read_text()
    )
    connection.close()
    match_path = tmp_path / "match_state.json"
    draft_path = tmp_path / "draft_state.json"
    match_path.write_text(json.dumps({"match_active": True, "match_count": 7,
                                      "matches": [], "bench": []}))
    draft_path.write_text(json.dumps({"draft": True, "matches": [], "bench": [],
                                      "fixed_pairs": [[1, 2]]}))
    ensure_runtime_state_storage(database, legacy_directory=tmp_path)
    storage = SQLiteStorage(database)
    assert load_current_match(storage=storage).state["match_count"] == 7
    assert load_current_draft(storage=storage).state["fixed_pairs"] == [[1, 2]]
    save_current_match(
        {"match_active": False, "match_count": 8, "matches": [], "bench": []},
        1, storage=storage,
    )
    storage.close()
    ensure_runtime_state_storage(database, legacy_directory=tmp_path)
    storage = SQLiteStorage(database)
    assert load_current_match(storage=storage).state["match_count"] == 8
    assert match_path.exists() and draft_path.exists()
    storage.close()


def test_sqlite_legacy_import_missing_and_corrupt_files(tmp_path):
    missing_db = tmp_path / "missing.db"
    connection = sqlite3.connect(missing_db)
    connection.executescript(
        (ROOT / "migrations/d1/0001_phase3_participants_config.sql").read_text()
        + (ROOT / "migrations/d1/0002_phase4_match_relational.sql").read_text()
    )
    connection.close()
    ensure_runtime_state_storage(missing_db, legacy_directory=tmp_path / "none")
    storage = SQLiteStorage(missing_db)
    assert load_current_match(storage=storage).state["match_count"] == 0
    assert load_current_draft(storage=storage).state is None
    storage.close()

    corrupt_dir = tmp_path / "corrupt"
    corrupt_dir.mkdir()
    (corrupt_dir / "match_state.json").write_text("{")
    corrupt_db = tmp_path / "corrupt.db"
    connection = sqlite3.connect(corrupt_db)
    connection.executescript(
        (ROOT / "migrations/d1/0001_phase3_participants_config.sql").read_text()
        + (ROOT / "migrations/d1/0002_phase4_match_relational.sql").read_text()
    )
    connection.close()
    with pytest.raises(RuntimeError, match="match_state.json"):
        ensure_runtime_state_storage(corrupt_db, legacy_directory=corrupt_dir)
