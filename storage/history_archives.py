"""Small storage boundary dedicated to match-history JSON archives."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from js import Object as _JsObject
    from pyodide.ffi import run_sync as _run_sync, to_js as _to_js
except ImportError:  # Normal CPython/EC2 environments do not provide Pyodide.
    _JsObject = None
    _run_sync = None
    _to_js = None


R2_ARCHIVE_PREFIX = "history_dumps"
_DEFAULT_RUN_SYNC = object()


class HistoryArchiveStorageError(RuntimeError):
    """Backend-neutral archive failure with no provider details in its message."""

    def __init__(self, *, detail=None):
        self._detail = detail
        super().__init__("History archive storage operation failed.")


@dataclass(frozen=True)
class HistoryArchiveObject:
    key: str
    size: int
    modified_at: datetime


def _utc_datetime(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if hasattr(value, "toISOString"):
        value = str(value.toISOString())
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    raise HistoryArchiveStorageError()


def _safe_local_key(key):
    path = Path(str(key))
    if not key or path.name != str(key) or str(key) in {".", ".."}:
        raise HistoryArchiveStorageError()
    return str(key)


def _r2_http_metadata():
    metadata = {"contentType": "application/json; charset=utf-8"}
    if _to_js is None:
        return metadata
    return _to_js(metadata, dict_converter=_JsObject.fromEntries)


class FilesystemHistoryArchiveStorage:
    """Preserve the EC2/local ``instance/history_dumps`` archive layout."""

    def __init__(self, directory):
        self._directory = Path(directory)

    @staticmethod
    def key_for_filename(filename):
        return _safe_local_key(filename)

    def put_history_archive(self, key, data: bytes):
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            (self._directory / _safe_local_key(key)).write_bytes(bytes(data))
        except HistoryArchiveStorageError:
            raise
        except OSError as error:
            raise HistoryArchiveStorageError(detail=str(error)) from None

    def get_history_archive(self, key):
        try:
            path = self._directory / _safe_local_key(key)
            if not path.is_file():
                return None
            return path.read_bytes()
        except HistoryArchiveStorageError:
            raise
        except OSError as error:
            raise HistoryArchiveStorageError(detail=str(error)) from None

    def list_history_archives(self):
        try:
            if not self._directory.is_dir():
                return []
            objects = []
            for path in self._directory.iterdir():
                if not path.is_file():
                    continue
                stat_result = path.stat()
                objects.append(HistoryArchiveObject(
                    key=path.name,
                    size=stat_result.st_size,
                    modified_at=datetime.fromtimestamp(
                        stat_result.st_mtime, timezone.utc
                    ),
                ))
            return objects
        except OSError as error:
            raise HistoryArchiveStorageError(detail=str(error)) from None

    def delete_history_archive(self, key):
        try:
            path = self._directory / _safe_local_key(key)
            if path.is_file():
                path.unlink()
        except HistoryArchiveStorageError:
            raise
        except OSError as error:
            raise HistoryArchiveStorageError(detail=str(error)) from None


class R2HistoryArchiveStorage:
    """Synchronous Flask adapter for one request-local Workers R2 binding."""

    def __init__(self, binding, *, run_sync=_DEFAULT_RUN_SYNC, list_limit=None):
        if binding is None:
            raise HistoryArchiveStorageError()
        self._binding = binding
        self._run_sync = run_sync if run_sync is not _DEFAULT_RUN_SYNC else globals()["_run_sync"]
        self._list_limit = list_limit

    @staticmethod
    def key_for_filename(filename):
        # Filename validation remains at the application boundary. This method
        # only maps a validated legacy filename into the private R2 namespace.
        date_part = str(filename).rsplit("_", 3)[-3]
        return f"{R2_ARCHIVE_PREFIX}/{date_part[:4]}/{date_part[4:6]}/{filename}"

    def _wait(self, promise):
        if self._run_sync is None:
            raise HistoryArchiveStorageError()
        try:
            return self._run_sync(promise)
        except HistoryArchiveStorageError:
            raise
        except Exception as error:
            raise HistoryArchiveStorageError(detail=str(error)) from None

    def put_history_archive(self, key, data: bytes):
        try:
            promise = self._binding.put(
                key,
                bytes(data),
                httpMetadata=_r2_http_metadata(),
            )
            self._wait(promise)
        except HistoryArchiveStorageError:
            raise
        except Exception as error:
            raise HistoryArchiveStorageError(detail=str(error)) from None

    def get_history_archive(self, key):
        try:
            stored = self._wait(self._binding.get(key))
            if stored is None:
                return None
            return bytes(self._wait(stored.arrayBuffer()))
        except HistoryArchiveStorageError:
            raise
        except Exception as error:
            raise HistoryArchiveStorageError(detail=str(error)) from None

    def list_history_archives(self):
        objects = []
        cursor = None
        seen_cursors = set()
        while True:
            try:
                options = {"prefix": f"{R2_ARCHIVE_PREFIX}/"}
                if self._list_limit is not None:
                    options["limit"] = self._list_limit
                if cursor is not None:
                    options["cursor"] = cursor
                page = self._wait(self._binding.list(**options))
                for item in page.objects:
                    objects.append(HistoryArchiveObject(
                        key=str(item.key),
                        size=int(item.size),
                        modified_at=_utc_datetime(item.uploaded),
                    ))
                if not bool(page.truncated):
                    return objects
                next_cursor = str(getattr(page, "cursor", "") or "")
                if not next_cursor or next_cursor in seen_cursors:
                    raise HistoryArchiveStorageError()
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            except HistoryArchiveStorageError:
                raise
            except Exception as error:
                raise HistoryArchiveStorageError(detail=str(error)) from None

    def delete_history_archive(self, key):
        try:
            self._wait(self._binding.delete(key))
        except HistoryArchiveStorageError:
            raise
        except Exception as error:
            raise HistoryArchiveStorageError(detail=str(error)) from None
