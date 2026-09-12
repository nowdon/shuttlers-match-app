"""Static and CPython contract tests for the isolated D1 binding PoC."""
import ast
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
POC = ROOT / "cloudflare-d1-binding-poc"
SRC = POC / "src"


def load_adapter():
    spec = importlib.util.spec_from_file_location("d1_poc_adapter", SRC / "d1_adapter.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_current_flask_app_and_runtime_wsgi_are_used():
    source = (SRC / "poc_app.py").read_text()
    worker = (SRC / "worker.py").read_text()
    assert "from app import app" in source
    assert "return app.wsgi_app(environ, start_response)" in source
    assert "app.wsgi_app is app_module.runtime_wsgi_app" in source
    assert "wsgi.fetch(safe_wsgi, request, self.env)" in worker
    assert "Flask(" not in source + worker


def test_source_links_reference_current_app_only():
    expected = {Path("app.py"), Path("models.py"), Path("logic.py")}
    for directory in ("data", "routes", "utils"):
        expected.update(path.relative_to(ROOT) for path in (ROOT / directory).glob("*.py"))
    for relative in expected:
        link = SRC / relative
        assert link.is_symlink(), relative
        assert link.resolve() == (ROOT / relative).resolve()
    bundled_names = {path.name for path in POC.rglob("*") if path.is_file() or path.is_symlink()}
    assert "config.json" not in bundled_names
    assert "match_state.json" not in bundled_names
    assert "draft_state.json" not in bundled_names
    assert not any(name.endswith((".db", ".db-wal", ".db-shm")) for name in bundled_names)
    assert "history_dumps" not in {path.name for path in POC.rglob("*")}


def test_wrangler_has_one_poc_d1_binding_and_no_other_resources():
    config = json.loads((POC / "wrangler.jsonc").read_text())
    assert config["name"] == "shuttlers-match-d1-poc"
    assert config["workers_dev"] is True
    assert config["preview_urls"] is False
    assert config["compatibility_flags"] == ["python_workers"]
    assert len(config["d1_databases"]) == 1
    binding = config["d1_databases"][0]
    assert binding["binding"] == "DB"
    assert binding["database_name"] == "shuttlers-match-d1-poc-db"
    assert binding["migrations_dir"] == "migrations"
    assert re.fullmatch(r"[0-9a-f-]{36}", binding["database_id"])
    forbidden = {
        "r2_buckets", "kv_namespaces", "durable_objects", "assets", "routes", "route",
        "services", "queues", "hyperdrive", "vectorize", "vars",
    }
    assert forbidden.isdisjoint(config)


def test_migration_is_poc_only_and_valid_sqlite():
    sql = (POC / "migrations" / "0001_init.sql").read_text()
    assert "CREATE TABLE d1_poc_participants" in sql
    assert "card TEXT NOT NULL UNIQUE" in sql
    assert "REFERENCES d1_poc_parent(id)" in sql
    assert all(name.startswith("d1_poc_") for name in re.findall(r"CREATE TABLE ([a-z0-9_]+)", sql))
    connection = sqlite3.connect(":memory:")
    connection.executescript(sql)
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )}
    assert tables == {
        "d1_poc_participants", "d1_poc_batch_items", "d1_poc_parent", "d1_poc_child"
    }


def test_prepared_statements_bind_every_sql_value():
    source = (SRC / "poc_app.py").read_text()
    assert ".prepare(" not in source
    tree = ast.parse(source)
    assert not any(isinstance(node, ast.ImportFrom) and node.module.startswith("pyodide")
                   for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in {"run_sync", "_run_sync"} for node in ast.walk(tree))
    assert "VALUES (?, ?, ?)" in source
    assert "WHERE id = ?" in source
    assert "WHERE batch_key = ?" in source
    assert not re.search(r'f["\'](?:INSERT|UPDATE|DELETE|SELECT)', source)
    adapter = (SRC / "d1_adapter.py").read_text()
    assert "self._binding.prepare(sql)" in adapter
    assert "statement.bind(*params)" in adapter
    assert "_run_sync(promise)" in adapter


def test_route_and_method_allowlist_excludes_production_routes():
    script = r'''
import poc_app
allowed = poc_app.allowed_request
assert allowed("GET", "/d1-poc/status")
assert allowed("POST", "/d1-poc/participants")
assert allowed("GET", "/d1-poc/participants/1")
assert not allowed("POST", "/d1-poc/status")
for path in ["/", "/viewer", "/register", "/match", "/match/result", "/admin",
             "/admin/settings", "/api/participants", "/api/match_state",
             "/line/webhook", "/notifications/test"]:
    assert not poc_app.allowed_path(path), path
assert poc_app.app.wsgi_app is poc_app.app_module.runtime_wsgi_app
assert poc_app.app.secret_key is not None
assert not poc_app.app_module._runtime_initialized
'''
    env = {"PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-c", script], cwd=POC, env=env,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_adapter_conversion_binding_and_error_normalization():
    adapter_module = load_adapter()

    class Proxy:
        results = [{"id": 1}]
        success = True
        meta = {"changes": 1, "last_row_id": 1}
        error = None

        def to_py(self, *, dict_converter):
            return dict_converter((
                ("results", self.results), ("success", self.success), ("meta", self.meta)
            ))

    class Promise:
        def __init__(self, value=None, error=None):
            self.value = value
            self.error = error

    class Statement:
        def __init__(self, sql, calls):
            self.sql = sql
            self.params = ()
            self.calls = calls

        def bind(self, *params):
            self.params = params
            self.calls.append(("bind", self.sql, params))
            return self

        def run(self):
            return Promise(Proxy())

        def first(self):
            return Promise(None)

        def all(self):
            return Promise(Proxy())

    class Binding:
        def __init__(self):
            self.calls = []

        def prepare(self, sql):
            self.calls.append(("prepare", sql))
            return Statement(sql, self.calls)

        def batch(self, statements):
            self.calls.append(("batch", len(statements)))
            return Promise([Proxy() for _statement in statements])

    def fake_run_sync(promise):
        if promise.error:
            raise promise.error
        return promise.value

    adapter_module._run_sync = fake_run_sync
    binding = Binding()
    adapter = adapter_module.D1Adapter(binding)
    run = adapter.run("INSERT INTO test(value) VALUES (?)", "synthetic")
    assert run.value["meta"]["last_row_id"] == 1
    assert run.interop["to_py_conversion"]["supported"]
    assert adapter.first("SELECT * FROM test WHERE id = ?", 999).value is None
    assert adapter.all("SELECT * FROM test").value["results"] == [{"id": 1}]
    assert len(adapter.batch([
        ("INSERT INTO test(value) VALUES (?)", ("A",)),
        ("INSERT INTO test(value) VALUES (?)", ("B",)),
    ]).value) == 2
    assert ("bind", "INSERT INTO test(value) VALUES (?)", ("synthetic",)) in binding.calls

    adapter_module._run_sync = lambda _promise: (_ for _ in ()).throw(
        RuntimeError("D1_ERROR: UNIQUE constraint failed: hidden SQL details")
    )
    try:
        adapter.run("INSERT", "synthetic")
    except adapter_module.D1AdapterError as error:
        assert error.as_dict() == {
            "category": "unique",
            "runtime_type": "RuntimeError",
            "message": "unique constraint failed",
            "cause_type": None,
            "cause_message": None,
        }
        assert "hidden" not in json.dumps(error.as_dict())
    else:
        raise AssertionError("expected normalized error")


def test_synthetic_data_sanitization_and_measurement_plan():
    source = (SRC / "poc_app.py").read_text()
    readme = (POC / "README.md").read_text()
    assert "D1 PoC Player" in source and "D1-POC-001" in source
    assert "request.get_json" not in source and "request.form" not in source
    assert "database_id" not in source
    assert "request.headers" not in source
    assert "request.remote_addr" not in source
    assert '"served_by"' not in source and '"served_by_colo"' not in source
    assert "stack" not in source.lower()
    for term in ("CPU/wall", "p50", "p95", "30-second", "redeploy", "concurrent", "version ID"):
        assert term in readme
    inspect_files = [
        POC / ".gitignore", POC / "README.md", POC / "package.json",
        POC / "pyproject.toml", POC / "wrangler.jsonc", POC / "smoke_http.py",
        POC / "prepare_source_links.py", POC / "migrations" / "0001_init.sql",
        SRC / "d1_adapter.py", SRC / "poc_app.py", SRC / "worker.py",
    ]
    all_text = "\n".join(path.read_text(errors="ignore") for path in inspect_files)
    assert "LINE_CHANNEL_ACCESS_TOKEN" not in all_text
    assert "SMTP_PASSWORD" not in all_text
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", all_text)
