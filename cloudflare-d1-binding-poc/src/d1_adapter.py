"""Request-local synchronous adapter for a Workers D1 binding."""
from dataclasses import dataclass
import re

try:
    from pyodide.ffi import JsProxy, run_sync as _run_sync
except ImportError:  # Allows static/unit inspection under normal CPython.
    JsProxy = ()
    _run_sync = None


_SAFE_ERROR_PATTERNS = (
    ("unique", re.compile(r"unique constraint", re.IGNORECASE), "unique constraint failed"),
    ("foreign_key", re.compile(r"foreign key constraint", re.IGNORECASE), "foreign key constraint failed"),
    ("missing_table", re.compile(r"no such table", re.IGNORECASE), "required PoC table is missing"),
)


@dataclass(frozen=True)
class D1Call:
    value: object
    interop: dict


class D1AdapterError(RuntimeError):
    def __init__(self, category, runtime_type, message, cause_type, cause_message):
        super().__init__(message)
        self.category = category
        self.runtime_type = runtime_type
        self.safe_message = message
        self.cause_type = cause_type
        self.cause_message = cause_message

    def as_dict(self):
        return {
            "category": self.category,
            "runtime_type": self.runtime_type,
            "message": self.safe_message,
            "cause_type": self.cause_type,
            "cause_message": self.cause_message,
        }


def _safe_error_details(error):
    raw = str(error)
    category = "unexpected"
    message = "unexpected D1 error"
    for candidate, pattern, safe_message in _SAFE_ERROR_PATTERNS:
        if pattern.search(raw):
            category = candidate
            message = safe_message
            break
    cause = getattr(error, "__cause__", None)
    cause_raw = str(cause) if cause is not None else ""
    cause_type = type(cause).__name__ if cause is not None else None
    cause_message = None
    for _candidate, pattern, safe_message in _SAFE_ERROR_PATTERNS:
        if pattern.search(cause_raw):
            cause_message = safe_message
            break
    return category, type(error).__name__, message, cause_type, cause_message


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
            return converter(dict_converter=dict)
        except TypeError:
            return converter()
    return value


def _interop(value):
    observation = {
        "runtime_type": type(value).__name__,
        "is_js_proxy": bool(JsProxy and isinstance(value, JsProxy)),
        "dict_conversion": "not_applicable",
        "to_py_conversion": "not_available",
        "attribute_access": {},
    }
    if value is not None:
        try:
            converted = dict(value)
            observation["dict_conversion"] = {
                "supported": True,
                "python_type": type(converted).__name__,
                "keys": sorted(str(key) for key in converted),
            }
        except Exception:
            observation["dict_conversion"] = {"supported": False}
    converter = getattr(value, "to_py", None)
    if converter is not None:
        try:
            converted = converter(dict_converter=dict)
            observation["to_py_conversion"] = {
                "supported": True,
                "python_type": type(converted).__name__,
            }
        except Exception:
            observation["to_py_conversion"] = {"supported": False}
    for name in ("results", "success", "meta", "error"):
        try:
            attribute = getattr(value, name)
        except Exception:
            observation["attribute_access"][name] = {"available": False}
        else:
            observation["attribute_access"][name] = {
                "available": attribute is not None,
                "runtime_type": type(attribute).__name__,
            }
    return observation


class D1Adapter:
    """Wrap D1 promises so synchronous Flask routes never call run_sync directly."""

    def __init__(self, binding):
        if binding is None:
            raise RuntimeError("D1 binding is unavailable")
        self._binding = binding

    @classmethod
    def from_environ(cls, environ):
        workers_env = environ.get("workers.env")
        if workers_env is None:
            raise RuntimeError("Workers environment is unavailable")
        return cls(getattr(workers_env, "DB", None))

    @staticmethod
    def run_sync_available():
        return _run_sync is not None

    def _statement(self, sql, params):
        statement = self._binding.prepare(sql)
        return statement.bind(*params) if params else statement

    def _wait(self, promise):
        if _run_sync is None:
            raise RuntimeError("run_sync is unavailable")
        try:
            raw = _run_sync(promise)
        except Exception as error:
            raise D1AdapterError(*_safe_error_details(error)) from None
        return D1Call(_to_python(raw), _interop(raw))

    def run(self, sql, *params):
        return self._wait(self._statement(sql, params).run())

    def first(self, sql, *params):
        return self._wait(self._statement(sql, params).first())

    def all(self, sql, *params):
        return self._wait(self._statement(sql, params).all())

    def batch(self, statements):
        prepared = [self._statement(sql, params) for sql, params in statements]
        return self._wait(self._binding.batch(prepared))
