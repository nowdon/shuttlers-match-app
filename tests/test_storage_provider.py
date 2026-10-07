from pathlib import Path
from types import SimpleNamespace

from flask import Flask
import pytest

from storage.d1 import D1Storage
from storage.errors import StorageUnavailableError
from storage.provider import close_storage, create_storage, get_storage
from storage.sqlite import SQLiteStorage


def test_default_backend_is_sqlite():
    app = Flask(__name__)
    with app.test_request_context("/"):
        storage = get_storage()
        assert isinstance(storage, SQLiteStorage)
        assert storage.database_path == Path(app.instance_path) / "participants.db"


def test_backend_priority_is_explicit_then_config_then_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_BACKEND", "d1")
    app = Flask(__name__)
    app.config["STORAGE_BACKEND"] = "sqlite"
    with app.app_context():
        configured = create_storage(database_path=tmp_path / "configured.db")
        explicit = create_storage("sqlite", database_path=tmp_path / "explicit.db")
    assert isinstance(configured, SQLiteStorage)
    assert isinstance(explicit, SQLiteStorage)

    app.config.pop("STORAGE_BACKEND")
    binding = object()
    with app.test_request_context("/", environ_overrides={
        "workers.env": SimpleNamespace(DB=binding),
    }):
        assert isinstance(create_storage(), D1Storage)


def test_d1_backend_uses_request_binding_and_fails_closed_when_missing():
    app = Flask(__name__)
    app.config["STORAGE_BACKEND"] = "d1"
    binding = object()
    with app.test_request_context("/", environ_overrides={
        "workers.env": SimpleNamespace(DB=binding),
    }):
        storage = get_storage()
        assert isinstance(storage, D1Storage)
        assert storage._binding is binding

    with app.test_request_context("/"):
        with pytest.raises(StorageUnavailableError):
            get_storage()


def test_sqlite_remains_selected_when_d1_binding_exists():
    app = Flask(__name__)
    with app.test_request_context("/", environ_overrides={
        "workers.env": SimpleNamespace(DB=object()),
    }):
        assert isinstance(get_storage(), SQLiteStorage)


def test_storage_is_cached_only_for_one_request():
    app = Flask(__name__)
    captured = []

    @app.get("/")
    def index():
        first = get_storage()
        second = get_storage()
        assert first is second
        first.all("SELECT 1 AS value")
        captured.append(first)
        return "ok"

    app.teardown_request(close_storage)
    client = app.test_client()
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 200
    assert captured[0] is not captured[1]
    assert all(storage._connection is None for storage in captured)


def test_explicit_sqlite_creation_works_outside_request(tmp_path):
    storage = create_storage("sqlite", database_path=tmp_path / "cli.db")
    try:
        assert storage.first("SELECT 1 AS value") == {"value": 1}
    finally:
        storage.close()


def test_d1_outside_request_requires_explicit_binding():
    with pytest.raises(StorageUnavailableError):
        create_storage("d1")
    assert isinstance(create_storage("d1", binding=object()), D1Storage)


def test_unknown_backend_has_safe_error():
    with pytest.raises(StorageUnavailableError) as caught:
        create_storage("unknown")
    assert "unknown" not in str(caught.value)
