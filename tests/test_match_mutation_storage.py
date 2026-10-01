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
                if isinstance(file, (str, os.PathLike)):
                    path = Path(file)
                    if path.name in {
                        "config.json", "match_state.json", "draft_state.json", "participants.db",
                    } or "history_dumps" in path.parts:
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
def test_d1_score_routes_use_storage_without_orm_or_legacy_files(mutation_app):
    ctx = mutation_app
    config = json.loads(ctx.storage.first(
        "SELECT config_json FROM app_config WHERE key = 'main'"
    )["config_json"])
    config.update(
        score_input_mode="score",
        scoring_system={"points_per_game": 21, "games_per_match": 1,
                        "deuce_enabled": True, "max_points": 30},
    )
    ctx.storage.run(
        "UPDATE app_config SET config_json = ?, version = version + 1 WHERE key = 'main'",
        json.dumps(config),
    )
    assert ctx.client.post("/match", data={"court_count": "1"},
                           environ_overrides=ctx.worker_env).status_code == 302
    assert ctx.client.post("/match/confirm", data={"mode": "admin"},
                           environ_overrides=ctx.worker_env).status_code == 302
    match = ctx.storage.first("SELECT id, round_id FROM match_histories")
    before_games = counts(ctx)
    before_state = load_current_match(storage=ctx.storage)

    cases = [
        (f"/match/result/{match['id']}/score",
         {"mode": "admin", "game1_team1_score": "21", "game1_team2_score": "15"},
         ("21-15", 1, 0, 1)),
        (f"/admin/match_history/{match['id']}/score",
         {"game1_team1_score": "15", "game1_team2_score": "21"},
         ("15-21", 0, 1, 2)),
        (f"/match/result/round/{match['round_id']}/score",
         {"mode": "admin", f"match_{match['id']}_game1_team1_score": "22",
          f"match_{match['id']}_game1_team2_score": "20"},
         ("22-20", 1, 0, 1)),
        (f"/admin/match_history/round/{match['round_id']}/score",
         {f"match_{match['id']}_game1_team1_score": "20",
          f"match_{match['id']}_game1_team2_score": "22"},
         ("20-22", 0, 1, 2)),
    ]
    for path, form, expected in cases:
        response = ctx.client.post(path, data=form, environ_overrides=ctx.worker_env)
        assert response.status_code == 302
        row = ctx.storage.first(
            "SELECT score_text, team1_score, team2_score, winner_team "
            "FROM match_histories WHERE id = ?", match["id"],
        )
        assert tuple(row.values()) == expected

    invalid = ctx.client.post(
        f"/match/result/{match['id']}/score",
        data={"mode": "admin", "game1_team1_score": "21", "game1_team2_score": ""},
        environ_overrides=ctx.worker_env,
    )
    assert invalid.status_code == 302
    row = ctx.storage.first(
        "SELECT score_text, team1_score, team2_score, winner_team "
        "FROM match_histories WHERE id = ?", match["id"],
    )
    assert tuple(row.values()) == ("20-22", 0, 1, 2)
    assert counts(ctx) == before_games
    assert load_current_match(storage=ctx.storage) == before_state


@pytest.mark.parametrize("mutation_app", ["d1"], indirect=True)
def test_d1_full_reset_continues_after_r2_archive_failure(mutation_app):
    ctx = mutation_app
    assert ctx.client.post("/match", data={"court_count": "1"},
                           environ_overrides=ctx.worker_env).status_code == 302
    assert ctx.client.post("/match/confirm", data={"mode": "admin"},
                           environ_overrides=ctx.worker_env).status_code == 302
    assert ctx.storage.first("SELECT COUNT(*) AS n FROM match_histories")["n"] == 1
    match_version = load_current_match(storage=ctx.storage).version
    draft_version = load_current_draft(storage=ctx.storage).version
    config_before = ctx.storage.first(
        "SELECT config_json, version FROM app_config WHERE key = 'main'"
    )

    class FailingR2:
        def put(self, _key, _data, **_options):
            raise RuntimeError("synthetic R2 failure")

    ctx.app.config["HISTORY_ARCHIVE_BACKEND"] = "r2"
    worker_env = {"workers.env": SimpleNamespace(
        DB=ctx.worker_env["workers.env"].DB,
        HISTORY_ARCHIVES=FailingR2(),
    )}
    response = ctx.client.post(
        "/admin/reset_db", environ_overrides=worker_env,
    )
    assert response.status_code == 302
    page = ctx.client.get(response.location, environ_overrides=worker_env)
    assert page.status_code == 200
    assert "試合履歴のJSON保存に失敗しました" in page.text
    for table in ("participants", "match_sessions", "match_rounds",
                  "match_histories", "bench_histories"):
        assert ctx.storage.first(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0
    match = load_current_match(storage=ctx.storage)
    draft = load_current_draft(storage=ctx.storage)
    assert match.version == match_version + 1
    assert draft.version == draft_version + 1
    assert match.state["session_id"] is None
    assert draft.state is None
    assert ctx.storage.first(
        "SELECT config_json, version FROM app_config WHERE key = 'main'"
    ) == config_before


@pytest.mark.parametrize("mutation_app", ["d1"], indirect=True)
def test_d1_full_reset_archives_to_r2_without_orm_or_legacy_files(mutation_app, monkeypatch):
    ctx = mutation_app
    assert ctx.client.post("/match", data={"court_count": "1"},
                           environ_overrides=ctx.worker_env).status_code == 302
    assert ctx.client.post("/match/confirm", data={"mode": "admin"},
                           environ_overrides=ctx.worker_env).status_code == 302
    match_version = load_current_match(storage=ctx.storage).version
    draft_version = load_current_draft(storage=ctx.storage).version
    config_before = ctx.storage.first(
        "SELECT config_json, version FROM app_config WHERE key = 'main'"
    )

    class RecordingR2:
        def __init__(self):
            self.objects = {}

        def put(self, key, data, **_options):
            self.objects[key] = bytes(data)
            return SimpleNamespace(value=None)

    import storage.history_archives as archives
    monkeypatch.setattr(archives, "_run_sync", lambda promise: promise.value)
    binding = RecordingR2()
    ctx.app.config["HISTORY_ARCHIVE_BACKEND"] = "r2"
    worker_env = {"workers.env": SimpleNamespace(
        DB=ctx.worker_env["workers.env"].DB,
        HISTORY_ARCHIVES=binding,
    )}
    response = ctx.client.post("/admin/reset_db", environ_overrides=worker_env)
    assert response.status_code == 302
    assert len(binding.objects) == 1
    archive = json.loads(next(iter(binding.objects.values())))
    assert archive["reason"] == "clear_all_data"
    assert archive["rounds"][0]["matches"][0]["team1_player1_name"].startswith("Synthetic-")
    for table in ("notification_delivery_logs", "match_notifications",
                  "notification_subscriptions", "line_link_tokens", "line_accounts",
                  "bench_histories", "match_histories", "match_rounds",
                  "match_sessions", "participants"):
        assert ctx.storage.first(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0
    match = load_current_match(storage=ctx.storage)
    draft = load_current_draft(storage=ctx.storage)
    assert match.version == match_version + 1
    assert draft.version == draft_version + 1
    assert {key: match.state[key] for key in (
        "match_active", "match_count", "matches", "bench", "session_id"
    )} == {"match_active": False, "match_count": 0,
           "matches": [], "bench": [], "session_id": None}
    assert draft.state is None
    assert ctx.storage.first(
        "SELECT config_json, version FROM app_config WHERE key = 'main'"
    ) == config_before


@pytest.mark.parametrize("mutation_app", ["d1"], indirect=True)
def test_d1_dump_and_clear_archives_history_only_without_orm_or_legacy_files(mutation_app, monkeypatch):
    ctx = mutation_app
    assert ctx.client.post("/match", data={"court_count": "1"},
                           environ_overrides=ctx.worker_env).status_code == 302
    assert ctx.client.post("/match/confirm", data={"mode": "admin"},
                           environ_overrides=ctx.worker_env).status_code == 302
    before_round = ctx.storage.first("SELECT * FROM match_rounds")
    before_match = ctx.storage.first("SELECT * FROM match_histories")
    before_bench = ctx.storage.all("SELECT * FROM bench_histories ORDER BY id")
    before_participants = ctx.storage.all("SELECT * FROM participants ORDER BY id")
    before_sessions = ctx.storage.all("SELECT * FROM match_sessions ORDER BY id")
    before_match_state = load_current_match(storage=ctx.storage)
    before_draft_state = load_current_draft(storage=ctx.storage)
    before_config = ctx.storage.first("SELECT * FROM app_config WHERE key = 'main'")

    class RecordingR2:
        def __init__(self):
            self.objects = {}

        def put(self, key, data, **_options):
            self.objects[key] = bytes(data)
            return SimpleNamespace(value=None)

    import storage.history_archives as archives
    monkeypatch.setattr(archives, "_run_sync", lambda promise: promise.value)
    binding = RecordingR2()
    ctx.app.config["HISTORY_ARCHIVE_BACKEND"] = "r2"
    worker_env = {"workers.env": SimpleNamespace(
        DB=ctx.worker_env["workers.env"].DB,
        HISTORY_ARCHIVES=binding,
    )}
    response = ctx.client.post(
        "/admin/match_history/dump_and_clear", environ_overrides=worker_env,
    )
    assert response.status_code == 302
    assert response.location.endswith("/admin/match_history")
    assert len(binding.objects) == 1
    archive = json.loads(next(iter(binding.objects.values())))
    assert archive["reason"] == "manual_dump_and_clear"
    archived_round = archive["rounds"][0]
    assert archived_round["id"] == before_round["id"]
    assert archived_round["matches"][0]["id"] == before_match["id"]
    assert archived_round["matches"][0]["team1_player1_id"] == before_match["team1_player1_id"]
    assert {entry["participant_id"] for entry in archived_round["bench"]} == {
        entry["participant_id"] for entry in before_bench
    }
    for table in ("match_rounds", "match_histories", "bench_histories"):
        assert ctx.storage.first(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0
    assert ctx.storage.all("SELECT * FROM participants ORDER BY id") == before_participants
    assert ctx.storage.all("SELECT * FROM match_sessions ORDER BY id") == before_sessions
    assert load_current_match(storage=ctx.storage) == before_match_state
    assert load_current_draft(storage=ctx.storage) == before_draft_state
    assert ctx.storage.first("SELECT * FROM app_config WHERE key = 'main'") == before_config


@pytest.mark.parametrize("mutation_app", ["d1"], indirect=True)
def test_d1_dump_and_clear_continues_when_r2_archive_fails(mutation_app):
    ctx = mutation_app
    assert ctx.client.post("/match", data={"court_count": "1"},
                           environ_overrides=ctx.worker_env).status_code == 302
    assert ctx.client.post("/match/confirm", data={"mode": "admin"},
                           environ_overrides=ctx.worker_env).status_code == 302
    before_participants = ctx.storage.all("SELECT * FROM participants ORDER BY id")
    before_sessions = ctx.storage.all("SELECT * FROM match_sessions ORDER BY id")
    before_match_state = load_current_match(storage=ctx.storage)
    before_draft_state = load_current_draft(storage=ctx.storage)

    class FailingR2:
        def put(self, _key, _data, **_options):
            raise RuntimeError("synthetic R2 failure")

    ctx.app.config["HISTORY_ARCHIVE_BACKEND"] = "r2"
    worker_env = {"workers.env": SimpleNamespace(
        DB=ctx.worker_env["workers.env"].DB,
        HISTORY_ARCHIVES=FailingR2(),
    )}
    response = ctx.client.post(
        "/admin/match_history/dump_and_clear", environ_overrides=worker_env,
    )
    assert response.status_code == 302
    page = ctx.client.get(response.location, environ_overrides=worker_env)
    assert page.status_code == 200
    assert "試合履歴のJSON保存に失敗しました" in page.text
    for table in ("match_rounds", "match_histories", "bench_histories"):
        assert ctx.storage.first(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0
    assert ctx.storage.all("SELECT * FROM participants ORDER BY id") == before_participants
    assert ctx.storage.all("SELECT * FROM match_sessions ORDER BY id") == before_sessions
    assert load_current_match(storage=ctx.storage) == before_match_state
    assert load_current_draft(storage=ctx.storage) == before_draft_state


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
