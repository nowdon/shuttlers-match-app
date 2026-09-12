import pytest

import storage.d1 as d1_module
from storage.d1 import D1Storage
from storage.errors import StorageForeignKeyError, StorageUnavailableError, StorageUniqueError
from storage.result import StorageResult


class Promise:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error


class JsLikeDict:
    def __init__(self, value):
        self.value = value

    def to_py(self, *, dict_converter):
        return dict_converter(self.value.items())


class Statement:
    def __init__(self, binding, sql):
        self.binding = binding
        self.sql = sql
        self.params = ()

    def bind(self, *params):
        self.params = params
        self.binding.calls.append(("bind", self.sql, params))
        return self

    def run(self):
        return self.binding.promise("run", self)

    def first(self):
        return self.binding.promise("first", self)

    def all(self):
        return self.binding.promise("all", self)


class Binding:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def prepare(self, sql):
        self.calls.append(("prepare", sql))
        return Statement(self, sql)

    def promise(self, method, statement):
        self.calls.append((method, statement.sql, statement.params))
        return self.responses.pop(0)

    def batch(self, statements):
        self.calls.append(("batch", [(statement.sql, statement.params) for statement in statements]))
        return self.responses.pop(0)


def run_sync(promise):
    if promise.error is not None:
        raise promise.error
    return promise.value


def result(rows=None, *, changes=0, last_row_id=None):
    return JsLikeDict({
        "results": rows or [],
        "success": True,
        "meta": {"changes": changes, "last_row_id": last_row_id, "duration": 99},
    })


def test_d1_module_import_is_safe_on_cpython():
    assert d1_module._run_sync is None


def test_d1_prepare_bind_and_result_contracts():
    binding = Binding([
        Promise(result([{"id": 1}], changes=1, last_row_id=1)),
        Promise(JsLikeDict({"id": 1, "label": "alpha"})),
        Promise(None),
        Promise(result([{"id": 1}, {"id": 2}])),
    ])
    storage = D1Storage(binding, run_sync=run_sync)

    run_result = storage.run("INSERT INTO item (label) VALUES (?) RETURNING id", "alpha")
    assert run_result == StorageResult(rows=[{"id": 1}], changes=1, last_row_id=1)
    assert storage.first("SELECT * FROM item WHERE id = ?", 1) == {"id": 1, "label": "alpha"}
    assert storage.first("SELECT * FROM item WHERE id = ?", 999) is None
    assert storage.all("SELECT id FROM item") == [{"id": 1}, {"id": 2}]
    assert ("bind", "INSERT INTO item (label) VALUES (?) RETURNING id", ("alpha",)) in binding.calls


def test_d1_batch_normalizes_each_statement_result():
    binding = Binding([Promise([
        result([{"id": 1}], changes=1, last_row_id=1),
        result([{"id": 2}], changes=1, last_row_id=2),
    ])])
    storage = D1Storage(binding, run_sync=run_sync)
    results = storage.batch([
        ("INSERT INTO item (label) VALUES (?) RETURNING id", ("a",)),
        ("INSERT INTO item (label) VALUES (?) RETURNING id", ("b",)),
    ])
    assert results == [
        StorageResult(rows=[{"id": 1}], changes=1, last_row_id=1),
        StorageResult(rows=[{"id": 2}], changes=1, last_row_id=2),
    ]
    assert binding.calls[-1] == ("batch", [
        ("INSERT INTO item (label) VALUES (?) RETURNING id", ("a",)),
        ("INSERT INTO item (label) VALUES (?) RETURNING id", ("b",)),
    ])


@pytest.mark.parametrize(
    ("raw_message", "error_type"),
    [
        ("D1_ERROR: UNIQUE constraint failed: item.card", StorageUniqueError),
        ("D1_ERROR: FOREIGN KEY constraint failed", StorageForeignKeyError),
    ],
)
def test_d1_constraint_errors_are_typed_and_safe(raw_message, error_type):
    binding = Binding([Promise(error=RuntimeError(raw_message))])
    storage = D1Storage(binding, run_sync=run_sync)
    with pytest.raises(error_type) as caught:
        storage.run("INSERT", "value")
    assert raw_message not in str(caught.value)
    assert "item.card" not in str(caught.value)


def test_d1_batch_error_returns_no_partial_results():
    binding = Binding([Promise(error=RuntimeError("UNIQUE constraint failed: item.card"))])
    storage = D1Storage(binding, run_sync=run_sync)
    with pytest.raises(StorageUniqueError):
        storage.batch([("INSERT", ("a",)), ("INSERT", ("a",))])


def test_run_sync_unavailable_fails_only_when_adapter_is_used():
    storage = D1Storage(Binding([Promise(None)]), run_sync=None)
    with pytest.raises(StorageUnavailableError):
        storage.run("SELECT 1")
