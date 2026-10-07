import json
import sqlite3

import pytest
from sqlalchemy import create_engine

from migration.cli import main as migration_cli_main
from migration.errors import MigrationError, ValidationError
from migration.export_sqlite import export_snapshot
from migration.legacy_upgrade import canonicalize_legacy
from migration import legacy_upgrade
from migration.manifest import sha256_file
from migration.schema import RELATIONAL_TABLES, validate_source_schema
from models import db


def legacy_fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    database = source / "participants.snapshot.db"
    engine = create_engine("sqlite:///" + str(database))
    with engine.begin() as connection:
        db.Model.metadata.create_all(connection)
    engine.dispose()
    with sqlite3.connect(database) as connection:
        connection.execute("DROP INDEX uq_match_history_round_court")
        connection.execute("DROP INDEX uq_bench_history_round_participant")
        connection.execute("DROP TABLE match_rounds")
        connection.execute(
            "CREATE TABLE match_rounds (id INTEGER NOT NULL PRIMARY KEY, "
            "round_number INTEGER NOT NULL, created_at DATETIME NOT NULL)"
        )
    (source / "config.json").write_text(json.dumps({
        "level_map": {"beginner": 1},
        "gender_weight": {"male": 1.0},
        "history_dump_email": {"enabled": False, "recipient": ""},
    }))
    (source / "match_state.json").write_text(json.dumps({
        "match_active": False, "match_count": 0, "matches": [], "bench": [],
    }))
    return source, database


def run(source, database, tmp_path, **kwargs):
    return canonicalize_legacy(
        database, tmp_path / "canonical.db", legacy_directory=source,
        config_path=source / "config.json", **kwargs,
    )


def add_related_rows(database):
    stamp = "2026-09-29 12:34:56.123456+09:00"
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO participants (id,name,gender,level,weight,games_played,active,card) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(i, "日本語🏸 O'Reilly \"quoted\"" if i == 11 else f"Synthetic {i}",
              "male", "beginner", 1.0, 0, 1, "C'11" if i == 11 else f"S{i}")
             for i in (11, 12, 13, 14)],
        )
        connection.execute(
            "INSERT INTO match_sessions (id,status,match_count,created_at) "
            "VALUES (7,'draft',0,?)", (stamp,),
        )
        connection.execute(
            "INSERT INTO line_accounts "
            "(id,participant_id,line_user_id,display_name,active,created_at,updated_at) "
            "VALUES (21,11,'U-opaque:/+==\"','Synthetic LINE',1,?,?)", (stamp, stamp),
        )
        connection.execute(
            "INSERT INTO notification_subscriptions "
            "(id,session_id,participant_id,channel,active,created_at,updated_at) "
            "VALUES (22,7,11,'line',1,?,?)", (stamp, stamp),
        )
        connection.execute(
            "INSERT INTO line_link_tokens "
            "(id,token,participant_id,session_id,expires_at,created_at) "
            "VALUES (23,'T''opaque\"',11,7,?,?)", (stamp, stamp),
        )
        connection.execute(
            "INSERT INTO match_notifications "
            "(id,session_id,match_count,channel,status,created_at) "
            "VALUES (24,7,0,'line','pending',?)", (stamp,),
        )
        connection.execute(
            "INSERT INTO notification_delivery_logs "
            "(id,session_id,participant_id,match_count,channel,status,sent_at) "
            "VALUES (25,7,11,0,'line','sent',?)", (stamp,),
        )


def test_empty_legacy_canonicalizes_and_phase10_accepts(tmp_path):
    source, database = legacy_fixture(tmp_path)
    before = {p.name: sha256_file(p) for p in source.iterdir()}
    report = run(source, database, tmp_path)
    assert report["canonical_schema"] == "PASS"
    assert report["phase10_export_validation"] == "PASS"
    assert set(report["table_summaries"]) == set(RELATIONAL_TABLES)
    assert all(item["row_count"] == 0 for item in report["table_summaries"].values())
    with sqlite3.connect(tmp_path / "canonical.db") as connection:
        validate_source_schema(connection, require_support_tables=True)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM runtime_state").fetchone()[0] == 2
        assert connection.execute("SELECT state_json FROM runtime_state WHERE key='current_draft'").fetchone()[0] is None
        assert connection.execute("SELECT version FROM app_config WHERE key='main'").fetchone()[0] == 1
        config = json.loads(connection.execute(
            "SELECT config_json FROM app_config WHERE key='main'"
        ).fetchone()[0])
        assert config["level_map"] == {"beginner": 1}
        assert config["score_input_mode"] == "winner_only"
    assert {p.name: sha256_file(p) for p in source.iterdir()} == before


def test_nonempty_legacy_preserves_ids_and_values(tmp_path):
    source, database = legacy_fixture(tmp_path)
    add_related_rows(database)
    (source / "draft_state.json").write_text(json.dumps({
        "draft": True, "matches": [], "bench": [11], "fixed_pairs": [[12, 13]],
    }))
    (source / "match_state.json").write_text(json.dumps({
        "match_active": False, "match_count": 0, "matches": [], "bench": [],
        "session_id": 7,
    }))
    before = {p.name: sha256_file(p) for p in source.iterdir()}
    report = run(source, database, tmp_path)
    assert report["table_summaries"]["participants"]["min_id"] == 11
    assert report["table_summaries"]["notification_delivery_logs"]["max_id"] == 25
    payload = export_snapshot(tmp_path / "canonical.db", tmp_path / "unused.json", dry_run=True)
    assert [row["id"] for row in payload["tables"]["participants"]] == [11, 12, 13, 14]
    assert payload["tables"]["match_sessions"][0]["creation_token"] is None
    assert payload["tables"]["runtime_state"][1]["version"] == 1
    assert payload["table_summaries"]["line_accounts"]["row_count"] == 1
    assert payload["table_summaries"]["notification_subscriptions"]["row_count"] == 1
    assert payload["table_summaries"]["line_link_tokens"]["row_count"] == 1
    participant = payload["tables"]["participants"][0]
    assert participant["name"] == "日本語🏸 O'Reilly \"quoted\""
    assert participant["card"] == "C'11"
    assert payload["tables"]["match_sessions"][0]["created_at"] == "2026-09-29 12:34:56.123456+09:00"
    assert payload["tables"]["match_sessions"][0]["confirmed_at"] is None
    assert payload["tables"]["line_accounts"][0]["line_user_id"] == 'U-opaque:/+=="'
    assert payload["tables"]["line_link_tokens"][0]["token"] == 'T\'opaque"'
    assert payload["tables"]["line_link_tokens"][0]["used_at"] is None
    assert payload["tables"]["notification_delivery_logs"][0]["error_message"] is None
    assert {p.name: sha256_file(p) for p in source.iterdir()} == before


@pytest.mark.parametrize("value,expected", [
    (0, 0), (1, 1), (False, 0), (True, 1),
    ("0", 0), ("1", 1), ("false", 0), ("true", 1),
])
def test_boolean_forms_follow_phase10_normalization(tmp_path, value, expected):
    source, database = legacy_fixture(tmp_path)
    add_related_rows(database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE participants SET active=? WHERE id=11", (value,))
        connection.execute("UPDATE line_accounts SET active=? WHERE id=21", (value,))
        connection.execute("UPDATE notification_subscriptions SET active=? WHERE id=22", (value,))
    run(source, database, tmp_path)
    with sqlite3.connect(tmp_path / "canonical.db") as connection:
        for table, row_id in (("participants", 11), ("line_accounts", 21),
                              ("notification_subscriptions", 22)):
            assert connection.execute(
                f"SELECT active FROM {table} WHERE id=?", (row_id,)
            ).fetchone()[0] == expected


@pytest.mark.parametrize("value", ["yes", "no", 2, -1, "TRUE "])
def test_invalid_boolean_fails_closed(tmp_path, value):
    source, database = legacy_fixture(tmp_path)
    add_related_rows(database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE participants SET active=? WHERE id=11", (value,))
    with pytest.raises(ValidationError, match="not a boolean"):
        run(source, database, tmp_path)
    assert not (tmp_path / "canonical.db").exists()


def test_explicit_null_draft_bootstraps_to_null(tmp_path):
    source, database = legacy_fixture(tmp_path)
    (source / "draft_state.json").write_text("null")
    run(source, database, tmp_path)
    with sqlite3.connect(tmp_path / "canonical.db") as connection:
        assert connection.execute(
            "SELECT state_json FROM runtime_state WHERE key='current_draft'"
        ).fetchone()[0] is None


def test_ambiguous_round_fails_closed(tmp_path):
    source, database = legacy_fixture(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO match_rounds (id,round_number,created_at) "
            "VALUES (1,1,'2026-09-29')"
        )
    with pytest.raises(ValidationError, match="cannot be reconstructed"):
        run(source, database, tmp_path)
    assert not (tmp_path / "canonical.db").exists()


def test_legacy_foreign_key_anomaly_fails_closed(tmp_path):
    source, database = legacy_fixture(tmp_path)
    stamp = "2026-09-29T00:00:00Z"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO line_accounts "
            "(id,participant_id,line_user_id,active,created_at,updated_at) "
            "VALUES (1,999,'U-synthetic',1,?,?)", (stamp, stamp),
        )
    with pytest.raises(ValidationError, match="foreign_key_check"):
        run(source, database, tmp_path)
    assert not (tmp_path / "canonical.db").exists()


def test_unexpected_operational_index_fails_closed(tmp_path):
    source, database = legacy_fixture(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE INDEX unexpected_name ON participants(name)")
    with pytest.raises(MigrationError, match="Unsupported legacy index"):
        run(source, database, tmp_path)
    assert not (tmp_path / "canonical.db").exists()


@pytest.mark.parametrize("config", [
    {"history_dump_email": {"SMTP_PASSWORD": "secret-value"}},
    {"smtp": {"password": "secret-value"}},
    {"nested": {"auth": {"access_token": "secret-value"}}},
    {"mail": [{"api_key": "secret-value"}]},
])
def test_raw_nested_secret_fails_dry_run_and_actual(tmp_path, capsys, config):
    source, database = legacy_fixture(tmp_path)
    (source / "config.json").write_text(json.dumps(config))
    args = [
        "canonicalize-legacy", "--source", str(database),
        "--legacy-directory", str(source),
        "--config", str(source / "config.json"),
        "--output", str(tmp_path / "canonical.db"),
    ]
    for dry_run in (True, False):
        with pytest.raises(MigrationError) as error:
            run(source, database, tmp_path, dry_run=dry_run)
        assert "secret-value" not in str(error.value)
        assert migration_cli_main(args + (["--dry-run"] if dry_run else [])) == 2
        captured = capsys.readouterr()
        assert "secret-value" not in captured.err + captured.out
        assert not (tmp_path / "canonical.db").exists()


def test_malformed_config_fails_before_output(tmp_path):
    source, database = legacy_fixture(tmp_path)
    (source / "config.json").write_text("{not JSON")
    with pytest.raises(MigrationError):
        run(source, database, tmp_path)
    assert not (tmp_path / "canonical.db").exists()


@pytest.mark.parametrize("state,draft", [
    ("{not JSON", None),
    ({"match_active": False, "match_count": 0, "matches": [[99, 99, 99, 99]], "bench": []}, None),
    ({"match_active": False, "match_count": 0, "matches": [], "bench": [], "session_id": 99}, None),
    ({"match_active": False, "match_count": 0, "matches": [], "bench": []}, {"draft": True, "matches": [], "bench": [99]}),
])
def test_invalid_runtime_fails_before_output(tmp_path, state, draft):
    source, database = legacy_fixture(tmp_path)
    (source / "match_state.json").write_text(
        state if isinstance(state, str) else json.dumps(state)
    )
    if draft is not None:
        (source / "draft_state.json").write_text(json.dumps(draft))
    with pytest.raises((MigrationError, ValidationError)):
        run(source, database, tmp_path)
    assert not (tmp_path / "canonical.db").exists()


def test_output_protection_and_failure_cleanup(tmp_path):
    source, database = legacy_fixture(tmp_path)
    output = tmp_path / "canonical.db"
    output.write_bytes(b"preserve")
    with pytest.raises(MigrationError):
        run(source, database, tmp_path)
    assert output.read_bytes() == b"preserve"
    with pytest.raises(MigrationError):
        canonicalize_legacy(database, database, legacy_directory=source)
    with pytest.raises(MigrationError):
        canonicalize_legacy(database, source / "inside.db", legacy_directory=source)
    output.unlink()
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE participants SET name='unused' WHERE 0")
        connection.execute("ALTER TABLE participants ADD COLUMN unexpected TEXT")
    with pytest.raises(MigrationError, match="Unsupported legacy schema"):
        run(source, database, tmp_path)
    assert not output.exists()


def test_target_insert_failure_removes_partial_output(tmp_path):
    source, database = legacy_fixture(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO participants (id,name,gender,level,weight,card) "
            "VALUES (1,'Synthetic','male','beginner',1.0,'S1')"
        )
    before = sha256_file(database)
    with pytest.raises(MigrationError, match="canonicalization failed"):
        run(source, database, tmp_path)
    assert sha256_file(database) == before
    assert not (tmp_path / "canonical.db").exists()


def test_migration_sql_failure_removes_partial_output(tmp_path, monkeypatch):
    source, database = legacy_fixture(tmp_path)
    before = {p.name: sha256_file(p) for p in source.iterdir()}
    migrations = tmp_path / "bad-migrations"
    migrations.mkdir()
    (migrations / "0001.sql").write_text("CREATE TABLE transient (id INTEGER PRIMARY KEY);")
    (migrations / "0002.sql").write_text("THIS IS INVALID SQL;")
    monkeypatch.setattr(legacy_upgrade, "DEFAULT_MIGRATIONS_DIRECTORY", migrations)
    with pytest.raises(MigrationError, match="canonicalization failed"):
        run(source, database, tmp_path)
    assert not (tmp_path / "canonical.db").exists()
    assert {p.name: sha256_file(p) for p in source.iterdir()} == before


def test_post_copy_validation_failure_removes_output(tmp_path, monkeypatch):
    source, database = legacy_fixture(tmp_path)
    before = {p.name: sha256_file(p) for p in source.iterdir()}
    def fail_validation(*_args, **_kwargs):
        raise ValidationError("synthetic validation failure")
    monkeypatch.setattr(legacy_upgrade, "validate_source_schema", fail_validation)
    with pytest.raises(ValidationError, match="synthetic validation failure"):
        run(source, database, tmp_path)
    assert not (tmp_path / "canonical.db").exists()
    assert {p.name: sha256_file(p) for p in source.iterdir()} == before


def test_dry_run_and_cli_leave_no_artifact(tmp_path, capsys):
    source, database = legacy_fixture(tmp_path)
    (source / "draft_state.json").write_text(json.dumps({
        "draft": True, "matches": [], "bench": [], "fixed_pairs": [],
    }))
    before = {p.name: sha256_file(p) for p in source.iterdir()}
    report = run(source, database, tmp_path, dry_run=True)
    assert report["dry_run"] is True
    assert report["supported_legacy_schema"] is True
    assert not (tmp_path / "canonical.db").exists()
    assert migration_cli_main([
        "canonicalize-legacy", "--source", str(database),
        "--legacy-directory", str(source),
        "--config", str(source / "config.json"),
        "--output", str(tmp_path / "canonical.db"), "--dry-run",
    ]) == 0
    assert "synthetic" not in capsys.readouterr().out.lower()
    assert not (tmp_path / "canonical.db").exists()
    assert {p.name: sha256_file(p) for p in source.iterdir()} == before
