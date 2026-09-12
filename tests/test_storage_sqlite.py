from pathlib import Path
import sqlite3

from flask import Flask
import pytest

from storage.errors import StorageForeignKeyError, StorageUniqueError
from storage.provider import create_storage
from storage.result import StorageResult
from storage.sqlite import SQLiteStorage


@pytest.fixture
def storage(tmp_path):
    adapter = SQLiteStorage(tmp_path / "storage.db")
    adapter.run("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    adapter.run(
        "CREATE TABLE item ("
        "id INTEGER PRIMARY KEY, card TEXT NOT NULL UNIQUE, parent_id INTEGER NOT NULL "
        "REFERENCES parent(id), label TEXT NOT NULL)"
    )
    adapter.run("INSERT INTO parent (id) VALUES (?)", 1)
    try:
        yield adapter
    finally:
        adapter.close()


def test_sqlite_run_first_all_and_returning_contract(storage):
    assert sqlite3.sqlite_version_info >= (3, 35, 0)
    inserted = storage.run(
        "INSERT INTO item (card, parent_id, label) VALUES (?, ?, ?) RETURNING id, card",
        "A", 1, "alpha",
    )
    assert isinstance(inserted, StorageResult)
    assert inserted.rows == [{"id": 1, "card": "A"}]
    assert inserted.changes == 1
    assert inserted.last_row_id == 1
    assert storage.first("SELECT id, label FROM item WHERE card = ?", "A") == {
        "id": 1,
        "label": "alpha",
    }
    assert storage.first("SELECT id FROM item WHERE card = ?", "missing") is None
    assert storage.all("SELECT id, card FROM item ORDER BY id") == [{"id": 1, "card": "A"}]

    updated = storage.run("UPDATE item SET label = ? WHERE id = ?", "updated", 1)
    deleted = storage.run("DELETE FROM item WHERE id = ? RETURNING card", 1)
    assert updated.changes == 1 and updated.rows == []
    assert deleted.changes == 1 and deleted.rows == [{"card": "A"}]
    assert updated.last_row_id is None
    assert deleted.last_row_id is None


def test_sqlite_constraint_errors_are_typed_and_safe(storage):
    storage.run("INSERT INTO item (card, parent_id, label) VALUES (?, ?, ?)", "A", 1, "one")
    with pytest.raises(StorageUniqueError) as unique:
        storage.run("INSERT INTO item (card, parent_id, label) VALUES (?, ?, ?)", "A", 1, "two")
    assert "item.card" not in str(unique.value)
    assert unique.value.category == "unique"

    with pytest.raises(StorageForeignKeyError) as foreign_key:
        storage.run("INSERT INTO item (card, parent_id, label) VALUES (?, ?, ?)", "B", 999, "orphan")
    assert "FOREIGN KEY" not in str(foreign_key.value)
    assert foreign_key.value.category == "foreign_key"


def test_sqlite_noop_insert_does_not_return_stale_row_id(storage):
    storage.run(
        "INSERT INTO item (card, parent_id, label) VALUES (?, ?, ?)",
        "A", 1, "original",
    )
    ignored = storage.run(
        "INSERT OR IGNORE INTO item (card, parent_id, label) VALUES (?, ?, ?)",
        "A", 1, "duplicate",
    )
    inserted = storage.run(
        "INSERT OR IGNORE INTO item (card, parent_id, label) VALUES (?, ?, ?)",
        "B", 1, "inserted",
    )
    assert ignored.changes == 0
    assert ignored.last_row_id is None
    assert inserted.changes == 1
    assert inserted.last_row_id == 2


def test_sqlite_batch_returns_per_statement_results(storage):
    results = storage.batch([
        ("INSERT INTO item (card, parent_id, label) VALUES (?, ?, ?) RETURNING id", ("A", 1, "a")),
        ("INSERT INTO item (card, parent_id, label) VALUES (?, ?, ?) RETURNING id", ("B", 1, "b")),
    ])
    assert [result.rows for result in results] == [[{"id": 1}], [{"id": 2}]]
    assert [result.changes for result in results] == [1, 1]
    mixed = storage.batch([
        ("INSERT INTO item (card, parent_id, label) VALUES (?, ?, ?)", ("C", 1, "c")),
        ("UPDATE item SET label = ? WHERE id = ?", ("updated", 1)),
        ("DELETE FROM item WHERE id = ?", (2,)),
    ])
    assert mixed[0].last_row_id == 3
    assert mixed[1].last_row_id is None
    assert mixed[2].last_row_id is None


def test_sqlite_batch_rolls_back_all_statements_on_middle_error(storage):
    with pytest.raises(StorageUniqueError):
        storage.batch([
            ("INSERT INTO item (card, parent_id, label) VALUES (?, ?, ?)", ("A", 1, "a")),
            ("INSERT INTO item (card, parent_id, label) VALUES (?, ?, ?)", ("A", 1, "duplicate")),
            ("INSERT INTO item (card, parent_id, label) VALUES (?, ?, ?)", ("B", 1, "b")),
        ])
    assert storage.all("SELECT id FROM item") == []


def test_sqlite_batch_rolls_back_on_foreign_key_error(storage):
    with pytest.raises(StorageForeignKeyError):
        storage.batch([
            ("INSERT INTO item (card, parent_id, label) VALUES (?, ?, ?)", ("A", 1, "a")),
            ("INSERT INTO item (card, parent_id, label) VALUES (?, ?, ?)", ("B", 999, "orphan")),
        ])
    assert storage.all("SELECT id FROM item") == []


def test_provider_uses_temporary_current_app_instance_database(monkeypatch, tmp_path):
    app = Flask(__name__)
    expected = Path(app.instance_path) / "participants.db"
    real_connect = sqlite3.connect
    connected_paths = []

    def guarded_connect(path, *args, **kwargs):
        resolved = Path(path).resolve()
        assert resolved.is_relative_to(tmp_path.resolve())
        connected_paths.append(resolved)
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
    with app.app_context():
        adapter = create_storage("sqlite")
        try:
            assert adapter.first("SELECT 1 AS value") == {"value": 1}
        finally:
            adapter.close()
    assert connected_paths == [expected.resolve()]
