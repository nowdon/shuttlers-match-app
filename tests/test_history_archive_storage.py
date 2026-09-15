from datetime import datetime, timezone
from types import SimpleNamespace

from flask import Flask
import pytest

from storage.history_archive_provider import (
    create_history_archive_storage,
    get_history_archive_storage,
    selected_history_archive_backend,
)
from storage.history_archives import (
    FilesystemHistoryArchiveStorage,
    HistoryArchiveStorageError,
    R2HistoryArchiveStorage,
)


FILENAME = "match_history_manual_dump_20260915_120000_000001.json"
KEY = f"history_dumps/2026/09/{FILENAME}"
DATA = '{"schema_version": 1, "rounds": []}'.encode()


class Promise:
    def __init__(self, value):
        self.value = value


def run_sync(promise):
    return promise.value


class Body:
    def __init__(self, data):
        self._data = data

    def arrayBuffer(self):
        return Promise(self._data)


class FakeR2:
    def __init__(self):
        self.data = {}
        self.put_calls = []
        self.list_calls = []
        self.pages = None

    def put(self, key, data, **options):
        self.put_calls.append((key, bytes(data), options))
        self.data[key] = bytes(data)
        return Promise(None)

    def get(self, key):
        data = self.data.get(key)
        return Promise(None if data is None else Body(data))

    def list(self, **options):
        self.list_calls.append(options)
        if self.pages is not None:
            return Promise(self.pages[len(self.list_calls) - 1])
        objects = [
            SimpleNamespace(
                key=key,
                size=len(data),
                uploaded=datetime(2026, 9, 15, 12, tzinfo=timezone.utc),
            )
            for key, data in sorted(self.data.items())
            if key.startswith(options.get("prefix", ""))
        ]
        return Promise(SimpleNamespace(objects=objects, truncated=False))

    def delete(self, key):
        self.data.pop(key, None)
        return Promise(None)


def test_filesystem_archive_storage_round_trip_and_metadata(tmp_path):
    storage = FilesystemHistoryArchiveStorage(tmp_path / "history_dumps")
    assert storage.get_history_archive(FILENAME) is None
    storage.put_history_archive(FILENAME, DATA)

    assert storage.get_history_archive(FILENAME) == DATA
    objects = storage.list_history_archives()
    assert [(item.key, item.size) for item in objects] == [(FILENAME, len(DATA))]
    assert objects[0].modified_at.tzinfo is timezone.utc

    storage.delete_history_archive(FILENAME)
    assert storage.get_history_archive(FILENAME) is None


@pytest.mark.parametrize(
    "key",
    ["", "../archive.json", "sub/archive.json", ".."],
)
def test_filesystem_archive_storage_rejects_non_filename_keys(tmp_path, key):
    storage = FilesystemHistoryArchiveStorage(tmp_path)
    with pytest.raises(HistoryArchiveStorageError):
        storage.put_history_archive(key, DATA)


def test_r2_archive_storage_round_trip_bytes_metadata_and_delete():
    binding = FakeR2()
    storage = R2HistoryArchiveStorage(binding, run_sync=run_sync)
    assert storage.key_for_filename(FILENAME) == KEY

    storage.put_history_archive(KEY, DATA)
    assert binding.put_calls == [(KEY, DATA, {
        "httpMetadata": {"contentType": "application/json; charset=utf-8"},
    })]
    assert storage.get_history_archive(KEY) == DATA
    objects = storage.list_history_archives()
    assert [(item.key, item.size) for item in objects] == [(KEY, len(DATA))]
    assert objects[0].modified_at == datetime(2026, 9, 15, 12, tzinfo=timezone.utc)

    storage.delete_history_archive(KEY)
    assert storage.get_history_archive(KEY) is None


def test_r2_archive_storage_lists_every_cursor_page():
    binding = FakeR2()
    first = SimpleNamespace(
        key=KEY, size=10,
        uploaded=datetime(2026, 9, 15, 12, tzinfo=timezone.utc),
    )
    second = SimpleNamespace(
        key=KEY.replace("000001", "000002"), size=20,
        uploaded=datetime(2026, 9, 15, 13, tzinfo=timezone.utc),
    )
    binding.pages = [
        SimpleNamespace(objects=[first], truncated=True, cursor="next-page"),
        SimpleNamespace(objects=[second], truncated=False),
    ]

    objects = R2HistoryArchiveStorage(binding, run_sync=run_sync).list_history_archives()

    assert [item.key for item in objects] == [first.key, second.key]
    assert binding.list_calls == [
        {"prefix": "history_dumps/"},
        {"prefix": "history_dumps/", "cursor": "next-page"},
    ]


@pytest.mark.parametrize("cursors", [
    ["same", "same"],
    ["A", "B", "A"],
])
def test_r2_archive_storage_rejects_repeated_or_cyclic_cursors(cursors):
    binding = FakeR2()
    binding.pages = [
        SimpleNamespace(objects=[], truncated=True, cursor=cursor)
        for cursor in cursors
    ]

    with pytest.raises(HistoryArchiveStorageError):
        R2HistoryArchiveStorage(binding, run_sync=run_sync).list_history_archives()


def test_r2_archive_storage_rejects_missing_cursor_on_truncated_page():
    binding = FakeR2()
    binding.pages = [SimpleNamespace(objects=[], truncated=True, cursor=None)]

    with pytest.raises(HistoryArchiveStorageError):
        R2HistoryArchiveStorage(binding, run_sync=run_sync).list_history_archives()


def test_r2_archive_storage_normalizes_binding_failure():
    class BrokenR2(FakeR2):
        def get(self, key):
            raise RuntimeError(f"provider detail for {key}")

    storage = R2HistoryArchiveStorage(BrokenR2(), run_sync=run_sync)
    with pytest.raises(HistoryArchiveStorageError) as caught:
        storage.get_history_archive(KEY)
    assert "provider detail" not in str(caught.value)


def test_history_archive_provider_defaults_to_filesystem(monkeypatch, tmp_path):
    app = Flask(__name__)
    monkeypatch.delenv("HISTORY_ARCHIVE_BACKEND", raising=False)
    app.config.pop("HISTORY_ARCHIVE_BACKEND", None)
    with app.app_context():
        storage = create_history_archive_storage(directory=tmp_path)
        assert selected_history_archive_backend() == "filesystem"
        assert isinstance(storage, FilesystemHistoryArchiveStorage)


def test_history_archive_backend_priority_is_explicit_then_config_then_environment(monkeypatch, tmp_path):
    app = Flask(__name__)
    monkeypatch.setenv("HISTORY_ARCHIVE_BACKEND", "r2")
    app.config["HISTORY_ARCHIVE_BACKEND"] = "filesystem"
    with app.app_context():
        assert selected_history_archive_backend() == "filesystem"
        assert selected_history_archive_backend("r2") == "r2"
        assert isinstance(
            create_history_archive_storage(directory=tmp_path),
            FilesystemHistoryArchiveStorage,
        )


def test_history_archive_provider_uses_request_local_r2_binding(monkeypatch):
    app = Flask(__name__)
    monkeypatch.setenv("HISTORY_ARCHIVE_BACKEND", "r2")
    binding = FakeR2()
    with app.test_request_context(
        environ_overrides={"workers.env": SimpleNamespace(HISTORY_ARCHIVES=binding)}
    ):
        first = get_history_archive_storage()
        second = get_history_archive_storage()
        assert first is second
        assert isinstance(first, R2HistoryArchiveStorage)


def test_r2_selection_without_binding_fails_closed(monkeypatch):
    app = Flask(__name__)
    monkeypatch.setenv("HISTORY_ARCHIVE_BACKEND", "r2")
    with app.test_request_context():
        with pytest.raises(HistoryArchiveStorageError):
            get_history_archive_storage()


def test_unknown_history_archive_backend_fails_closed(monkeypatch):
    app = Flask(__name__)
    monkeypatch.setenv("HISTORY_ARCHIVE_BACKEND", "wat")
    with app.app_context():
        with pytest.raises(HistoryArchiveStorageError):
            create_history_archive_storage()
