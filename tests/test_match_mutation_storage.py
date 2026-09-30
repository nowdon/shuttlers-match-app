"""Match route contract on SQLite and isolated D1, without participant ORM reads.

Set MATCH_MUTATION_MINIFLARE to the installed miniflare entrypoint to run the
same D1 tests against disposable local workerd D1 rather than the SQLite fake.
No Cloudflare account, remote binding, or production data is used.
"""
import builtins
import importlib
import io
import inspect
import json
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from conftest import clear_app_modules
from data.participants import create_participant, get_all_participants
from data.runtime_state import (
    load_current_draft, load_current_match, save_current_draft, save_current_match,
)
from storage.d1 import D1Storage
from storage.errors import StorageUnavailableError
from storage.sqlite import SQLiteStorage
from test_line_notification_storage import MIGRATIONS, Promise, SQLiteD1Binding, Statement, run_sync


class LocalD1Binding:
    """Adapt the test binding protocol to a real local Miniflare D1 binding."""
    def __init__(self, entrypoint):
        self.process = subprocess.Popen(
            ["node", str(Path(__file__).with_name("local_d1_bridge.mjs")), entrypoint],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        try:
            for path in MIGRATIONS:
                # These migrations contain simple statements, no SQL triggers.
                sql = "\n".join(line for line in path.read_text().splitlines()
                                if not line.lstrip().startswith("--"))
                for statement in sql.split(";"):
                    if statement.strip():
                        run_sync(self.execute(self.prepare(statement), "run"))
        except BaseException:
            self.close()
            raise

    def prepare(self, sql):
        return Statement(self, sql)

    def _request(self, payload):
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("Local D1 bridge exited unexpectedly")
        result = json.loads(line)
        return Promise(error=RuntimeError(result["error"])) if "error" in result else Promise(result["value"])

    def execute(self, statement, method):
        return self._request({"method": method, "sql": statement.sql, "params": statement.params})

    def batch(self, statements):
        return self._request({"method": "batch", "statements": [
            {"sql": item.sql, "params": item.params} for item in statements
        ]})

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write('{"method":"close"}\n')
                self.process.stdin.flush()
                self.process.wait(timeout=10)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


@pytest.fixture(params=["sqlite", "d1"])
def mutation_app(request, monkeypatch, tmp_path):
    backend = request.param
    monkeypatch.setenv("STORAGE_BACKEND", backend)
    clear_app_modules()
    app_module = importlib.import_module("app")
    app_module.app.config.update(TESTING=True, STORAGE_BACKEND=backend, SECRET_KEY="synthetic-test-key")
    config = {"level_map": {"beginner": 2}, "gender_weight": {"male": 1}}
    binding = None
    if backend == "sqlite":
        (tmp_path / "config.json").write_text(json.dumps(config))
        app_module.initialize_runtime()
        storage = SQLiteStorage(tmp_path / "instance" / "participants.db")
        worker_env = {}
    else:
        app_module._runtime_initialized = True
        entrypoint = os.environ.get("MATCH_MUTATION_MINIFLARE")
        binding = LocalD1Binding(entrypoint) if entrypoint else SQLiteD1Binding()
        storage = D1Storage(binding, run_sync=run_sync)
        monkeypatch.setattr("storage.d1._run_sync", run_sync)
        storage.run("INSERT INTO app_config (key,config_json,version) VALUES ('main',?,1)", json.dumps(config))
        worker_env = {"workers.env": SimpleNamespace(DB=binding)}
    for index in range(1, 7):
        create_participant(f"Synthetic-{index}", "male", "beginner", 1, f"♥{index}", storage=storage)
    storage.run("UPDATE participants SET active = 0 WHERE id = 6")

    route = importlib.import_module("routes.match")
    line_calls = []
    monkeypatch.setattr(route, "send_match_confirmed_line_notifications", lambda *args: line_calls.append(args))
    attempts = []

    def forbidden(kind):
        def fail(*_args, **_kwargs):
            attempts.append(kind)
            raise AssertionError(f"Unexpected {kind}")
        return fail

    # Block DNS and outgoing sockets; tests use Flask's in-process client.
    monkeypatch.setattr(socket, "getaddrinfo", forbidden("DNS"))
    monkeypatch.setattr(socket.socket, "connect", forbidden("network"))
    if backend == "d1":
        participant_data = importlib.import_module("data.participants")
        for name in ("get_all_participants_for_orm", "get_active_participants_for_orm",
                     "get_participants_by_ids_for_orm", "get_participants_by_ids_for_orm_mutation"):
            monkeypatch.setattr(participant_data, name, forbidden("ORM helper"))
        monkeypatch.setattr(type(inspect.getattr_static(app_module.Participant, "query")),
                            "__get__", forbidden("ORM query"))
        for name in ("query", "execute", "get", "flush", "commit", "add", "expire_all"):
            monkeypatch.setattr(app_module.db.session, name, forbidden("ORM session"))
        with app_module.app.app_context():
            event.listen(app_module.db.engine, "do_connect", forbidden("SQLAlchemy connect"))
        for module in (builtins, io):
            original_open = module.open

            def guarded_open(file, *args, _open=original_open, **kwargs):
                if isinstance(file, (str, os.PathLike)) and Path(file).name in {
                    "config.json", "match_state.json", "draft_state.json", "participants.db",
                }:
                    return forbidden("legacy file")(file)
                return _open(file, *args, **kwargs)

            monkeypatch.setattr(module, "open", guarded_open)
    try:
        yield SimpleNamespace(
            app=app_module.app, route=route, client=app_module.app.test_client(),
            storage=storage, worker_env=worker_env, attempts=attempts, line_calls=line_calls,
            backend=backend,
        )
        assert attempts == []
    finally:
        storage.close()
        if isinstance(binding, LocalD1Binding):
            binding.close()
        elif binding is not None:
            binding.connection.close()


def counts(ctx):
    return [p.games_played for p in get_all_participants(storage=ctx.storage)]


def test_match_mutation_route_lifecycle(mutation_app, monkeypatch):
    ctx = mutation_app
    def post(path, **data):
        result = ctx.client.post(path, data={"mode": "admin", **data}, environ_overrides=ctx.worker_env)
        assert result.status_code == 302
        assert "/match/edit" in result.location or "/match/result" in result.location
        return result

    # Previous bench marker must appear without changing either record or DB.
    state = load_current_match(storage=ctx.storage)
    previous = dict(state.state, bench=[5])
    save_current_match(previous, state.version, storage=ctx.storage)
    assert counts(ctx) == [0] * 6
    post("/match", court_count="1")
    assert counts(ctx) == [0] * 6
    first = load_current_draft(storage=ctx.storage)
    assert 5 in first.state["matches"][0]
    assert 6 not in first.state["matches"][0] + first.state["bench"]

    seen = []
    original_read = ctx.route.get_participants_by_ids
    def capture(ids):
        records = original_read(ids)
        seen.extend(records)
        return records
    monkeypatch.setattr(ctx.route, "get_participants_by_ids", capture)
    page = ctx.client.get("/match/edit?mode=admin", environ_overrides=ctx.worker_env)
    assert page.status_code == 200
    assert "*Synthetic-5" in page.text
    assert "4.0" in page.text  # Pair score preview from config and participant levels.
    assert next(p.name for p in seen if p.id == 5) == "Synthetic-5"
    assert next(p.name for p in get_all_participants(storage=ctx.storage) if p.id == 5) == "Synthetic-5"

    # Regeneration and explicit discard never persist participant counters.
    post("/match", court_count="1")
    assert counts(ctx) == [0] * 6
    draft = load_current_draft(storage=ctx.storage)
    save_current_draft(None, draft.version, storage=ctx.storage)
    assert counts(ctx) == [0] * 6
    post("/update_court_count", court_count="1")
    draft = load_current_draft(storage=ctx.storage).state
    assert draft["court_count"] == 1
    assert counts(ctx) == [0] * 6
    pair = draft["matches"][0][:2]
    post("/match/swap", swap_ids=",".join(map(str, pair)))
    assert load_current_draft(storage=ctx.storage).state["fixed_pairs"] == [sorted(pair)]
    post("/match/optimize_pairs")
    optimized = load_current_draft(storage=ctx.storage).state
    assert optimized["fixed_pairs"] == [sorted(pair)]
    assert optimized["bench"] == draft["bench"]
    assert load_current_match(storage=ctx.storage).state["bench"] == [5]
    assert counts(ctx) == [0] * 6

    played = {pid for group in optimized["matches"] for pid in group}
    post("/match/confirm")
    expected = [int(i in played) for i in range(1, 7)]
    assert counts(ctx) == expected
    assert load_current_draft(storage=ctx.storage).state is None
    assert len(ctx.line_calls) == 1
    # Retrying a completed confirmation cannot increment or notify again.
    retry = ctx.client.post("/match/confirm", environ_overrides=ctx.worker_env)
    assert retry.status_code == 302
    assert counts(ctx) == expected
    assert len(ctx.line_calls) == 1
    post("/match/revert_to_draft")
    assert counts(ctx) == [0] * 6
    assert load_current_draft(storage=ctx.storage).state["matches"] == optimized["matches"]
    assert ctx.storage.first("SELECT COUNT(*) AS n FROM match_rounds")["n"] == 0
    retry = ctx.client.post("/match/revert_to_draft", data={"mode": "admin"}, environ_overrides=ctx.worker_env)
    assert retry.status_code == 302
    assert counts(ctx) == [0] * 6


@pytest.mark.parametrize("mutation_app", ["d1"], indirect=True)
def test_d1_mutation_routes_fail_closed_without_binding(mutation_app):
    ctx = mutation_app
    for method, path, data in [
        ("POST", "/match", {"court_count": "1"}),
        ("GET", "/match/edit", {}),
        ("POST", "/match/optimize_pairs", {"mode": "admin"}),
        ("POST", "/match/swap", {"swap_ids": "1,2"}),
        ("POST", "/match/confirm", {}),
        ("POST", "/match/revert_to_draft", {"mode": "admin"}),
        ("POST", "/update_court_count", {"court_count": "1"}),
    ]:
        with pytest.raises(StorageUnavailableError):
            ctx.client.open(path, method=method, data=data)


@pytest.mark.parametrize("mutation_app", ["sqlite"], indirect=True)
def test_sqlite_refreshes_retained_orm_counters(mutation_app):
    from models import Participant, db
    ctx = mutation_app
    with ctx.app.app_context():
        assert ctx.client.post("/match", data={"court_count": "1"}).status_code == 302
        draft = load_current_draft(storage=ctx.storage).state
        player = db.session.get(Participant, draft["matches"][0][0])
        assert player.games_played == 0
        assert ctx.client.post("/match/confirm").status_code == 302
        assert player.games_played == 1
        assert ctx.client.post("/match/revert_to_draft", data={"mode": "admin"}).status_code == 302
        assert player.games_played == 0


@pytest.mark.parametrize("unknown_id", [999, 2**80])
def test_unknown_draft_participants_are_rejected_without_writes(mutation_app, unknown_id):
    ctx = mutation_app
    current = load_current_draft(storage=ctx.storage)
    invalid = {"draft": True, "matches": [[unknown_id, 2, 3, 4]], "bench": [5]}
    save_current_draft(invalid, current.version, storage=ctx.storage)
    for method, path, data in [
        ("GET", "/match/edit", {}),
        ("POST", "/match/optimize_pairs", {"mode": "admin"}),
        ("POST", "/match/swap", {"swap_ids": "2,3", "mode": "admin"}),
        ("POST", "/match/confirm", {}),
    ]:
        result = ctx.client.open(path, method=method, data=data, environ_overrides=ctx.worker_env)
        assert result.status_code == 302
        assert result.location.split("?")[0].endswith("/match")
        assert load_current_draft(storage=ctx.storage).state == invalid
        assert counts(ctx) == [0] * 6
