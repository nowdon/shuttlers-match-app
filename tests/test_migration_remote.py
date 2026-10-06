"""Remote driver safety checks without Cloudflare account access."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from migration.errors import MigrationError, ValidationError
from migration.export_sqlite import export_snapshot
from migration.fixture import create_synthetic_fixture
from migration.import_plan import apply_import_plan, initialize_sqlite_target, write_import_plan
from migration.remote import RemoteWrangler, _verified_plan
from migration.snapshot import create_snapshot


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def remote_fixture(tmp_path):
    fixture = create_synthetic_fixture(tmp_path / "fixture")
    snapshot = tmp_path / "snapshot.db"
    create_snapshot(fixture["database"], snapshot)
    export = tmp_path / "export.json"
    export_snapshot(
        snapshot, export, legacy_directory=fixture["source_directory"],
        config_path=fixture["config"],
    )
    plan_dir = tmp_path / "plan"
    plan = write_import_plan(export, plan_dir, batch_size=2)
    target = tmp_path / "target.db"
    initialize_sqlite_target(target, ROOT / "migrations/d1")
    with closing(sqlite3.connect(target)) as connection:
        connection.execute("CREATE TABLE d1_migrations (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO d1_migrations (id, name) VALUES (?, ?)",
            [(index, path.name) for index, path in enumerate(
                sorted((ROOT / "migrations/d1").glob("*.sql")), start=1,
            )],
        )
        connection.commit()
    wrangler = tmp_path / "wrangler"
    wrangler.touch()
    config = tmp_path / "wrangler.jsonc"
    config.write_text("{}", encoding="utf-8")
    remote = RemoteWrangler(
        wrangler, config, "shuttlers-phase14-disposable-test",
        "shuttlers-phase14-disposable-test",
    )
    return remote, export, plan_dir, plan, target


def test_remote_rejects_production_names_and_tampered_sql(remote_fixture):
    remote, export, plan_dir, _plan, _target = remote_fixture
    with pytest.raises(MigrationError, match="disposable"):
        RemoteWrangler(remote.wrangler, remote.config, "shuttlers-match-app-prod",
                       "shuttlers-phase14-disposable-test")
    _verified_plan(export, plan_dir)
    (plan_dir / "sql_batches/000-prelude.sql").write_text(
        "DELETE FROM participants;\n", encoding="utf-8",
    )
    with pytest.raises(MigrationError, match="differs"):
        remote.import_plan(export, plan_dir)


def test_production_remote_requires_exact_restricted_config(remote_fixture, tmp_path):
    remote, export, _plan_dir, _plan, _target = remote_fixture
    config = tmp_path / "production.jsonc"
    db_id = "00000000-0000-4000-8000-000000000014"
    settings = {
        "name": "shuttlers-match-app", "vars": {
            "PHASE14_PRE_CUTOVER_READ_ONLY": "true",
            "STORAGE_BACKEND": "d1", "HISTORY_ARCHIVE_BACKEND": "r2",
        },
        "d1_databases": [{"binding": "DB", "database_name": "shuttlers-match-app-prod",
                          "database_id": db_id, "migrations_dir": "migrations/d1"}],
        "r2_buckets": [{"binding": "HISTORY_ARCHIVES",
                        "bucket_name": "shuttlers-match-history-prod"}],
    }
    config.write_text(json.dumps(settings), encoding="utf-8")
    production = RemoteWrangler(
        remote.wrangler, config, "shuttlers-match-app-prod",
        "shuttlers-match-history-prod", production_database_id=db_id,
    )
    assert production.production
    with pytest.raises(MigrationError, match="ad-hoc"):
        production.query("DELETE FROM participants")
    with pytest.raises(MigrationError, match="ad-hoc"):
        production.query("SELECT 1; DELETE FROM participants")
    with pytest.raises(MigrationError, match="Synthetic"):
        production.disposable_insert_cas_smoke(export)
    settings["vars"]["PHASE14_PRE_CUTOVER_READ_ONLY"] = "false"
    config.write_text(json.dumps(settings), encoding="utf-8")
    with pytest.raises(MigrationError, match="restricted"):
        RemoteWrangler(remote.wrangler, config, "shuttlers-match-app-prod",
                       "shuttlers-match-history-prod", production_database_id=db_id)


def test_remote_import_stops_at_first_failed_file(remote_fixture, monkeypatch):
    remote, export, plan_dir, plan, _target = remote_fixture
    monkeypatch.setattr(remote, "preflight", lambda: {"target": "fresh"})
    calls = []

    def execute(path):
        calls.append(Path(path).name)
        if len(calls) == 2:
            raise MigrationError("synthetic batch failure")

    monkeypatch.setattr(remote, "execute_file", execute)
    with pytest.raises(MigrationError, match="target rejected"):
        remote.import_plan(export, plan_dir)
    assert calls == [Path(item).name for item in plan["sql_files"][:2]]


def test_remote_file_execution_accepts_successful_empty_stdout(remote_fixture, monkeypatch):
    remote, _export, _plan_dir, _plan, target = remote_fixture
    monkeypatch.setattr(remote, "_run", lambda *_args, **_kwargs: "")
    remote.execute_file(target)
    monkeypatch.setattr(remote, "_run", lambda *_args, **_kwargs: "Executed 1 command successfully")
    remote.execute_file(target)


def test_remote_export_replay_validates_schema_and_all_source_checks(remote_fixture, monkeypatch):
    remote, export, _plan_dir, plan, target = remote_fixture

    def export_from_target(path):
        with closing(sqlite3.connect(target)) as connection:
            Path(path).write_text("\n".join(connection.iterdump()) + "\n", encoding="utf-8")
        return Path(path)

    def query_target(sql):
        with closing(sqlite3.connect(target)) as connection:
            connection.row_factory = sqlite3.Row
            rows = [dict(row) for row in connection.execute(sql)]
            connection.commit()
            return rows

    monkeypatch.setattr(remote, "export", export_from_target)
    monkeypatch.setattr(remote, "query", query_target)
    assert remote.preflight() == {"schema": "valid", "target": "fresh"}
    apply_import_plan(target, plan)
    report = remote.validate(export)
    assert report["ok"]
    assert any(item["name"] == "foreign_key_check" for item in report["checks"])
    assert all(set(item) == {"name", "status"} for item in report["checks"])
    assert remote.disposable_insert_cas_smoke(export)["config_cas"] == "PASS"


def test_remote_r2_rejects_different_existing_bytes(remote_fixture, monkeypatch, tmp_path):
    remote, _export, _plan_dir, _plan, _target = remote_fixture
    source = tmp_path / "match_history_manual_dump_20260919_010203_000001.json"
    source.write_bytes(b"source")
    from migration.archives import build_archive_plan
    plan = build_archive_plan(tmp_path)

    def wrong_existing(*args, **_kwargs):
        Path(args[args.index("--file") + 1]).write_bytes(b"other")
        return ""

    monkeypatch.setattr(remote, "_run", wrong_existing)
    with pytest.raises(ValidationError, match="overwrite forbidden"):
        remote.copy_validate_r2(plan)


def test_remote_r2_upload_download_and_idempotence(remote_fixture, monkeypatch, tmp_path):
    remote, _export, _plan_dir, _plan, _target = remote_fixture
    source = tmp_path / "match_history_manual_dump_20260919_010203_000001.json"
    source.write_bytes(b'{"synthetic":true}')
    from migration.archives import build_archive_plan
    plan = build_archive_plan(tmp_path)
    stored = {}

    def object_command(*args, allow_missing=False):
        key = args[3]
        if args[2] == "get":
            if key not in stored and allow_missing:
                return None
            Path(args[args.index("--file") + 1]).write_bytes(stored[key])
        elif args[2] == "put":
            stored[key] = Path(args[args.index("--file") + 1]).read_bytes()
        return ""

    monkeypatch.setattr(remote, "_run", object_command)
    first = remote.copy_validate_r2(plan)
    assert first["verified_count"] == 1
    assert first["objects"][0]["status"] == "uploaded"
    assert stored[f"{remote.bucket}/{plan['archives'][0]['target_key']}"] == source.read_bytes()
    second = remote.copy_validate_r2(plan)
    assert second["objects"][0]["status"] == "idempotent_existing"
    assert not second["exact_bucket_key_set_verified"]
