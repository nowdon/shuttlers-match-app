import json
import importlib
import io
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from data.participants import (
    ParticipantRecord,
    create_participant,
    create_participants_bulk,
    get_active_participants,
    get_all_participants,
    get_api_participants,
    get_participant_by_card,
    get_participants_by_ids,
    get_participants_ordered_by_card,
    update_participant_by_card,
)
from storage.d1 import D1Storage
from storage.errors import StorageConflictError, StorageUnavailableError, StorageUniqueError
from storage.sqlite import SQLiteStorage
import utils.config as config_module
import storage.d1 as d1_module


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations/d1/0001_phase3_participants_config.sql"
)


class Promise:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error


class SQLiteD1Statement:
    def __init__(self, binding, sql):
        self.binding = binding
        self.sql = sql
        self.params = ()

    def bind(self, *params):
        self.params = params
        return self

    def run(self):
        return self.binding.execute(self.sql, self.params, "run")

    def first(self):
        return self.binding.execute(self.sql, self.params, "first")

    def all(self):
        return self.binding.execute(self.sql, self.params, "all")


class SQLiteD1Binding:
    """Production-like D1 binding contract backed by isolated SQLite."""

    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(MIGRATION.read_text(encoding="utf-8"))

    def prepare(self, sql):
        return SQLiteD1Statement(self, sql)

    def execute(self, sql, params, method):
        try:
            cursor = self.connection.execute(sql, params)
            if method == "first":
                row = cursor.fetchone()
                return Promise(None if row is None else dict(row))
            rows = [dict(row) for row in cursor.fetchall()] if cursor.description else []
            self.connection.commit()
            if method == "all":
                return Promise({"success": True, "results": rows, "meta": {"changes": 0}})
            return Promise({
                "success": True,
                "results": rows,
                "meta": {"changes": cursor.rowcount, "last_row_id": cursor.lastrowid},
            })
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
                results.append({
                    "success": True,
                    "results": rows,
                    "meta": {"changes": cursor.rowcount, "last_row_id": cursor.lastrowid},
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


@pytest.fixture(params=["sqlite", "d1"])
def participant_storage(request, tmp_path):
    if request.param == "sqlite":
        storage = SQLiteStorage(tmp_path / "participants-contract.db")
        for statement in MIGRATION.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                storage.run(statement)
        yield storage
        storage.close()
    else:
        binding = SQLiteD1Binding()
        yield D1Storage(binding, run_sync=run_sync)
        binding.connection.close()


def test_participant_contract_parity(participant_storage):
    first = create_participant(
        "second", "male", "intermediate", 2, "C2", storage=participant_storage
    )
    second = create_participant(
        "first", "female", "beginner", 0.9, "C1", storage=participant_storage
    )
    assert isinstance(first, ParticipantRecord)
    assert first.active is True
    assert isinstance(first.weight, float)
    assert isinstance(first.games_played, int)
    assert [row.card for row in get_participants_ordered_by_card(storage=participant_storage)] == ["C1", "C2"]
    assert [row.id for row in get_all_participants(storage=participant_storage)] == [first.id, second.id]
    assert get_participants_by_ids([second.id, second.id, 999], storage=participant_storage) == [second]
    assert get_participant_by_card("missing", storage=participant_storage) is None

    updated = update_participant_by_card(
        "C2", name="updated", gender="male", level="advanced", active=False,
        storage=participant_storage,
    )
    assert updated.weight == 2.0  # Participant edit intentionally does not recalculate it.
    assert updated.active is False
    assert get_active_participants(storage=participant_storage) == [second]
    assert get_api_participants(storage=participant_storage) == [
        {"id": first.id, "name": "updated", "gender": "male", "level": "advanced", "active": False},
        {"id": second.id, "name": "first", "gender": "female", "level": "beginner", "active": True},
    ]

    with pytest.raises(StorageUniqueError):
        create_participant("duplicate", "male", "beginner", 1, "C1", storage=participant_storage)


def test_participant_bulk_insert_is_atomic_and_ordered(participant_storage):
    create_participants_bulk([
        {"name": "b", "gender": "male", "level": "beginner", "weight": 1, "card": "C2"},
        {"name": "a", "gender": "female", "level": "beginner", "weight": 0.9, "card": "C1"},
    ], storage=participant_storage)
    assert [row.card for row in get_participants_ordered_by_card(storage=participant_storage)] == ["C1", "C2"]


def test_d1_config_round_trip_cas_and_missing_row(monkeypatch):
    binding = SQLiteD1Binding()
    storage = D1Storage(binding, run_sync=run_sync)
    monkeypatch.setenv("STORAGE_BACKEND", "d1")
    monkeypatch.setattr(config_module, "get_storage", lambda: storage)
    try:
        with pytest.raises(StorageUnavailableError):
            config_module.load_config()

        initial = {"level_map": {"beginner": 1}, "SECRET_KEY": "must-not-persist"}
        storage.run(
            "INSERT INTO app_config (key, config_json, version) VALUES (?, ?, ?)",
            "main", json.dumps(initial), 4,
        )
        loaded, version = config_module.load_config_with_version()
        assert loaded["level_map"] == {"beginner": 1}
        assert version == 4

        assert config_module.save_config(
            {"level_map": {"beginner": 2}, "SMTP_PASSWORD": "secret"},
            expected_version=4,
        ) == 5
        raw, version = config_module.load_raw_config_with_version()
        assert raw == {"level_map": {"beginner": 2}}
        assert version == 5
        assert storage.first(
            "SELECT config_json FROM app_config WHERE key = ?", "main"
        )["config_json"] == '{"level_map":{"beginner":2}}'
        with pytest.raises(StorageConflictError):
            config_module.save_config({"level_map": {}}, expected_version=4)
    finally:
        binding.connection.close()


def test_d1_admin_settings_carries_version_and_rejects_stale_post(monkeypatch):
    binding = SQLiteD1Binding()
    storage = D1Storage(binding, run_sync=run_sync)
    monkeypatch.setenv("STORAGE_BACKEND", "d1")
    monkeypatch.setattr(config_module, "get_storage", lambda: storage)
    storage.run(
        "INSERT INTO app_config (key, config_json, version) VALUES (?, ?, ?)",
        "main",
        json.dumps({
            "level_map": {"beginner": 1, "intermediate": 2, "advanced": 3},
            "gender_weight": {"male": 1.0, "female": 0.9},
        }),
        1,
    )
    app_module = importlib.import_module("app")
    client = app_module.app.test_client()
    try:
        response = client.get("/admin/settings")
        assert response.status_code == 200
        assert 'name="config_version" value="1"' in response.get_data(as_text=True)

        storage.run(
            "UPDATE app_config SET config_json = ?, version = 2 WHERE key = ?",
            json.dumps({"level_map": {}, "gender_weight": {}}),
            "main",
        )
        response = client.post("/admin/settings", data={"config_version": "1"})
        assert response.status_code == 409
        assert "設定が別の画面で更新されました" in response.get_data(as_text=True)
        assert config_module.load_raw_config_with_version()[1] == 2
    finally:
        binding.connection.close()


def test_register_maps_unique_race_to_existing_400_response(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({
        "level_map": {"beginner": 1},
        "gender_weight": {"male": 1.0},
    }), encoding="utf-8")
    app_module = importlib.import_module("app")
    participant_routes = importlib.import_module("routes.participant")

    def duplicate(*_args, **_kwargs):
        raise StorageUniqueError()

    monkeypatch.setattr(participant_routes, "create_participant", duplicate)
    response = app_module.app.test_client().post("/register", data={
        "name": "racer", "gender": "male", "level": "beginner", "card": "♥A",
    })
    assert response.status_code == 400
    assert response.get_data(as_text=True) == "このカードは既に選ばれています"


def test_d1_api_participants_uses_storage_projection(monkeypatch):
    binding = SQLiteD1Binding()
    storage = D1Storage(binding, run_sync=run_sync)
    create_participant("api", "female", "beginner", 0.9, "C1", storage=storage)
    monkeypatch.setenv("STORAGE_BACKEND", "d1")
    monkeypatch.setattr(d1_module, "_run_sync", run_sync)
    app_module = importlib.import_module("app")
    try:
        response = app_module.app.test_client().get(
            "/api/participants",
            environ_overrides={"workers.env": SimpleNamespace(DB=binding)},
        )
        assert response.status_code == 200
        assert response.get_json() == [{
            "id": 1, "name": "api", "gender": "female",
            "level": "beginner", "active": True,
        }]
    finally:
        binding.connection.close()


def test_sqlite_participant_routes_preserve_edit_csv_and_api_behavior(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({
        "level_map": {"beginner": 1, "advanced": 3},
        "gender_weight": {"male": 1.0, "female": 0.9},
    }), encoding="utf-8")
    app_module = importlib.import_module("app")
    client = app_module.app.test_client()

    assert client.post("/register", data={
        "name": "before", "gender": "male", "level": "beginner", "card": "♥A",
    }).status_code == 302
    assert client.post("/participant/♥A", data={
        "name": "after", "gender": "female", "level": "advanced", "mode": "admin",
    }).status_code == 302

    csv_data = (
        "name,gender,level,card\n"
        "missing-card,male,beginner,\n"
        "used,male,beginner,♥A\n"
        "invalid,male,unknown,♥2\n"
        "valid,female,advanced,♥3\n"
    ).encode()
    assert client.post("/upload", data={
        "file": (io.BytesIO(csv_data), "participants.csv"),
    }).status_code == 302

    with app_module.app.app_context():
        edited = app_module.Participant.query.filter_by(card="♥A").one()
        assert (edited.name, edited.gender, edited.level, edited.active) == (
            "after", "female", "advanced", False,
        )
        assert edited.weight == 1.0
        assert [row.card for row in app_module.Participant.query.order_by(app_module.Participant.id)] == ["♥A", "♥3"]

    assert client.get("/api/participants").get_json() == [
        {"id": 1, "name": "after", "gender": "female", "level": "advanced", "active": False},
        {"id": 2, "name": "valid", "gender": "female", "level": "advanced", "active": True},
    ]
