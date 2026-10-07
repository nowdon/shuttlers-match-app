"""Synchronous adapter for a request-scoped Cloudflare D1 binding."""

try:
    from pyodide.ffi import run_sync as _run_sync
except ImportError:  # Normal CPython/EC2 environments do not provide Pyodide.
    _run_sync = None

from storage.errors import StorageError, StorageUnavailableError, normalize_storage_error
from storage.result import StorageResult


_DEFAULT_RUN_SYNC = object()


def _to_python(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _to_python(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_python(item) for item in value]
    converter = getattr(value, "to_py", None)
    if converter is not None:
        try:
            return _to_python(converter(dict_converter=dict))
        except TypeError:
            return _to_python(converter())
    try:
        return {str(key): _to_python(item) for key, item in dict(value).items()}
    except (TypeError, ValueError):
        return value


class D1Storage:
    """Normalize D1 prepared-statement results for synchronous Flask callers."""

    def __init__(self, binding, *, run_sync=_DEFAULT_RUN_SYNC):
        if binding is None:
            raise StorageUnavailableError()
        self._binding = binding
        self._run_sync = run_sync if run_sync is not _DEFAULT_RUN_SYNC else globals()["_run_sync"]

    def _statement(self, sql, params):
        statement = self._binding.prepare(sql)
        return statement.bind(*params) if params else statement

    def _wait(self, promise):
        if self._run_sync is None:
            raise StorageUnavailableError()
        try:
            return _to_python(self._run_sync(promise))
        except StorageError:
            raise
        except Exception as error:
            raise normalize_storage_error(error) from None

    @staticmethod
    def _mapping(value):
        if isinstance(value, dict):
            return value
        raise StorageError()

    @classmethod
    def _result(cls, raw):
        result = cls._mapping(raw)
        if result.get("success") is False:
            raise StorageError()
        meta = result.get("meta") or {}
        if not isinstance(meta, dict):
            meta = _to_python(meta)
        rows = result.get("results") or []
        if not isinstance(rows, list):
            raise StorageError()
        return StorageResult(
            rows=[cls._mapping(row) for row in rows],
            changes=meta.get("changes"),
            last_row_id=meta.get("last_row_id"),
        )

    def run(self, sql, *params):
        return self._result(self._wait(self._statement(sql, params).run()))

    def first(self, sql, *params):
        row = self._wait(self._statement(sql, params).first())
        return None if row is None else self._mapping(row)

    def all(self, sql, *params):
        return self._result(self._wait(self._statement(sql, params).all())).rows

    def batch(self, statements):
        prepared = [self._statement(sql, tuple(params)) for sql, params in statements]
        raw_results = self._wait(self._binding.batch(prepared))
        if not isinstance(raw_results, list):
            raise StorageError()
        return [self._result(result) for result in raw_results]

    def close(self):
        """D1 bindings are request-owned by Workers and need no explicit close."""
