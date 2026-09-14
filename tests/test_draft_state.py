from pathlib import Path

import pytest

from storage.errors import StorageConflictError
from storage.sqlite import SQLiteStorage
from utils.draft_state import (
    clear_draft_state,
    get_active_draft,
    get_active_draft_with_version,
    is_active_draft,
    load_draft_state,
    save_draft_state,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runtime_storage(tmp_path):
    storage = SQLiteStorage(tmp_path / "runtime.db")
    for migration in sorted((ROOT / "migrations/d1").glob("*.sql")):
        storage._get_connection().executescript(migration.read_text(encoding="utf-8"))
    yield storage
    storage.close()


@pytest.mark.parametrize("state, expected", [
    ({"draft": True, "matches": [[1, 2, 3, 4]], "bench": [5]}, True),
    ({"draft": False, "matches": [[1, 2, 3, 4]], "bench": [5]}, False),
    ({"draft": True, "matches": [], "bench": []}, True),
    ({"draft": False, "matches": [], "bench": []}, False),
    ({"draft": True, "matches": [[1, 2, 3, 4]], "bench": []}, True),
    ({"draft": True, "matches": [], "bench": [1]}, True),
])
def test_active_draft_shape(state, expected):
    assert is_active_draft(state) is expected


def test_save_clear_uses_tombstone_and_keeps_version(runtime_storage):
    _draft, version = get_active_draft_with_version(storage=runtime_storage)
    save_draft_state(
        [[1, 2, 3, 4]], [5], expected_version=version, storage=runtime_storage
    )
    assert get_active_draft(storage=runtime_storage) == load_draft_state(
        storage=runtime_storage
    )
    state, saved_version = get_active_draft_with_version(storage=runtime_storage)
    assert state["draft"] is True and "timestamp" in state
    assert "court_count" not in state
    clear_draft_state(
        expected_version=saved_version, storage=runtime_storage
    )
    assert get_active_draft(storage=runtime_storage) is None
    _none, tombstone_version = get_active_draft_with_version(storage=runtime_storage)
    assert tombstone_version == saved_version + 1
    with pytest.raises(StorageConflictError):
        save_draft_state(
            [], [], expected_version=saved_version, storage=runtime_storage
        )
