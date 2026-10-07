from pathlib import Path

from flask import Flask
import pytest

from routes.api import api_bp
from storage.errors import StorageConflictError
from storage.sqlite import SQLiteStorage
from utils.match_state import (
    load_match_state,
    load_match_state_with_version,
    save_match_state,
    save_match_state_full,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATCH_STATE = {
    "match_active": False, "match_count": 0, "matches": [], "bench": [],
}


@pytest.fixture
def runtime_storage(tmp_path):
    storage = SQLiteStorage(tmp_path / "runtime.db")
    for migration in sorted((ROOT / "migrations/d1").glob("*.sql")):
        storage._get_connection().executescript(migration.read_text(encoding="utf-8"))
    yield storage
    storage.close()


def test_load_save_and_versioned_conflict(runtime_storage):
    assert load_match_state(storage=runtime_storage) == DEFAULT_MATCH_STATE
    _state, version = load_match_state_with_version(storage=runtime_storage)
    state = {
        "match_active": True, "match_count": 2,
        "matches": [[1, 2, 3, 4]], "bench": [5],
    }
    save_match_state(state, expected_version=version, storage=runtime_storage)
    assert load_match_state(storage=runtime_storage) == state
    with pytest.raises(StorageConflictError):
        save_match_state(
            DEFAULT_MATCH_STATE, expected_version=version, storage=runtime_storage
        )


def test_match_state_api_uses_storage_without_exposing_version(runtime_storage):
    app = Flask(__name__)
    app.config.update(
        STORAGE_BACKEND="sqlite",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{runtime_storage.database_path}",
    )
    app.register_blueprint(api_bp)
    response = app.test_client().get("/api/match_state")
    assert response.status_code == 200
    assert response.get_json() == DEFAULT_MATCH_STATE
    assert "version" not in response.get_json()


def test_save_match_state_full_shape_and_session_compatibility(runtime_storage):
    save_match_state_full(
        True, [[1, 2, 3, 4]], [5], 2, storage=runtime_storage
    )
    state = load_match_state(storage=runtime_storage)
    assert state["match_active"] is True
    assert state["matches"] == [[1, 2, 3, 4]]
    assert state["bench"] == [5] and state["match_count"] == 2
    assert "court_count" not in state

    save_match_state_full(False, [], [], 0, court_count=3, storage=runtime_storage)
    assert load_match_state(storage=runtime_storage)["court_count"] == 3
    save_match_state_full(False, [], [], 0, court_count=0, storage=runtime_storage)
    assert "court_count" not in load_match_state(storage=runtime_storage)

    current, version = load_match_state_with_version(storage=runtime_storage)
    current["session_id"] = 12
    save_match_state(
        current, expected_version=version, storage=runtime_storage
    )
    save_match_state_full(False, [], [], 0, storage=runtime_storage)
    assert load_match_state(storage=runtime_storage)["session_id"] == 12
