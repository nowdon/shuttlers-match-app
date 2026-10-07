import json
from pathlib import Path
import sqlite3

import pytest

from migration.archives import (
    build_archive_plan,
    copy_archives_to_directory,
    validate_archive_directory,
)
from migration.cli import main as migration_cli_main
from migration.errors import MigrationError, ValidationError
from migration.export_sqlite import export_snapshot
from migration.fixture import create_synthetic_fixture
from migration.import_plan import (
    apply_import_plan,
    initialize_sqlite_target,
    target_nonempty,
    write_import_plan,
)
from migration.manifest import read_json, sha256_file
from migration.schema import validate_source_schema
from migration.snapshot import create_snapshot
from migration.validate import (
    assert_valid,
    post_import_smoke,
    validate_sqlite_target,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fixture(tmp_path):
    return create_synthetic_fixture(tmp_path / "fixture")


def build_export(fixture, tmp_path):
    snapshot = tmp_path / "snapshot.db"
    create_snapshot(fixture["database"], snapshot)
    artifact = tmp_path / "export.json"
    payload = export_snapshot(
        snapshot,
        artifact,
        legacy_directory=fixture["source_directory"],
        config_path=fixture["config"],
    )
    return snapshot, artifact, payload


def test_snapshot_uses_backup_and_leaves_source_unchanged(fixture, tmp_path):
    before = sha256_file(fixture["database"])
    snapshot = tmp_path / "snapshot.db"
    metadata = create_snapshot(fixture["database"], snapshot)
    assert snapshot.exists()
    assert metadata["snapshot"]["row_counts"]["participants"] == 4
    assert metadata["source_sha256"] == before
    assert sha256_file(fixture["database"]) == before


def test_export_is_deterministic_and_preserves_ids_null_unicode_and_booleans(fixture, tmp_path):
    first_snapshot, _first_artifact, first = build_export(fixture, tmp_path / "one")
    second_snapshot = tmp_path / "two" / "snapshot.db"
    second_snapshot.parent.mkdir()
    create_snapshot(fixture["database"], second_snapshot)
    second = export_snapshot(
        second_snapshot,
        tmp_path / "two" / "export.json",
        legacy_directory=fixture["source_directory"],
        config_path=fixture["config"],
    )
    assert first["table_summaries"] == second["table_summaries"]
    assert first["tables"]["participants"][1]["name"] == "ボブ"
    assert first["tables"]["line_link_tokens"][0]["used_at"] is None
    assert first["tables"]["participants"][0]["active"] == 1
    assert first["tables"]["match_sessions"][0]["id"] == 7
    assert first["runtime_state"]["source"] == "runtime_state"
    assert first_snapshot.read_bytes() == second_snapshot.read_bytes()


def test_export_uses_legacy_runtime_and_config_only_when_support_rows_absent(tmp_path):
    fixture = create_synthetic_fixture(tmp_path / "fixture")
    source = tmp_path / "legacy.db"
    connection = sqlite3.connect(source)
    try:
        for migration in sorted((ROOT / "migrations/d1").glob("*.sql")):
            connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute("DELETE FROM runtime_state")
        connection.execute("DELETE FROM app_config")
        connection.commit()
    finally:
        connection.close()
    (fixture["source_directory"] / "match_state.json").write_text(
        json.dumps({"match_active": False, "match_count": 0, "matches": [], "bench": []}),
        encoding="utf-8",
    )
    (fixture["source_directory"] / "draft_state.json").write_text("null", encoding="utf-8")
    snapshot = tmp_path / "snapshot.db"
    create_snapshot(source, snapshot)
    payload = export_snapshot(
        snapshot,
        tmp_path / "export.json",
        legacy_directory=fixture["source_directory"],
        config_path=fixture["config"],
    )
    assert payload["runtime_state"]["source"] == "legacy_json"
    assert payload["app_config"]["source"] == "config.json"
    assert payload["tables"]["runtime_state"][1]["state_json"] is None
    assert payload["tables"]["app_config"][0]["version"] == 1


def test_orphan_fk_and_missing_schema_fail_closed(fixture, tmp_path):
    connection = sqlite3.connect(fixture["database"])
    try:
        connection.execute(
            "INSERT INTO bench_histories "
            "(id, round_id, participant_id, created_at) VALUES (?, ?, ?, ?)",
            (999, 9, 999, "2026-09-19T00:00:00Z"),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ValidationError):
        build_export(fixture, tmp_path)

    broken = tmp_path / "broken.db"
    connection = sqlite3.connect(broken)
    connection.execute("CREATE TABLE participants (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    with pytest.raises(MigrationError):
        create_snapshot(broken, tmp_path / "broken-snapshot.db")
        export_snapshot(tmp_path / "broken-snapshot.db", tmp_path / "broken-export.json")


def test_import_plan_is_batched_and_rejects_nonempty_targets(fixture, tmp_path):
    _snapshot, artifact, payload = build_export(fixture, tmp_path)
    plan_directory = tmp_path / "plan"
    plan = write_import_plan(artifact, plan_directory, batch_size=2)
    assert len(plan["batches"]) > len(payload["tables"])  # chunking is visible
    sql_text = (plan_directory / "sql_batches/0001-participants.sql").read_text()
    assert "Alice" in sql_text
    assert "PHASE10" not in sql_text

    target = tmp_path / "target.db"
    initialize_sqlite_target(target, ROOT / "migrations/d1")
    assert target_nonempty(sqlite3.connect(target)) == {}
    apply_import_plan(target, plan)
    with pytest.raises(MigrationError):
        apply_import_plan(target, plan)


def _build_fresh_import_target(fixture, tmp_path):
    _snapshot, artifact, _payload = build_export(fixture, tmp_path / "source")
    plan = write_import_plan(artifact, tmp_path / "plan", batch_size=100)
    target = tmp_path / "target.db"
    initialize_sqlite_target(target, ROOT / "migrations/d1")
    return plan, target


def test_runtime_seed_guard_allows_only_authoritative_fresh_seed(fixture, tmp_path):
    plan, target = _build_fresh_import_target(fixture, tmp_path)
    assert target_nonempty(sqlite3.connect(target)) == {}
    apply_import_plan(target, plan)


@pytest.mark.parametrize(
    "mutation",
    [
        "changed_json",
        "changed_version",
        "extra_row",
        "missing_row",
        "relational_row",
        "app_config_row",
    ],
)
def test_runtime_and_target_nonempty_guards_reject_modified_targets(
    fixture, tmp_path, mutation
):
    plan, target = _build_fresh_import_target(fixture, tmp_path / mutation)
    connection = sqlite3.connect(target)
    try:
        if mutation == "changed_json":
            connection.execute(
                "UPDATE runtime_state SET state_json=? WHERE key='current_match'",
                ('{"bench":[],"match_active":false,"match_count":99,"matches":[]}',),
            )
        elif mutation == "changed_version":
            connection.execute(
                "UPDATE runtime_state SET version=2 WHERE key='current_match'"
            )
        elif mutation == "extra_row":
            connection.execute(
                "INSERT INTO runtime_state (key, state_json, version) VALUES (?, ?, ?)",
                ("unexpected", None, 1),
            )
        elif mutation == "missing_row":
            connection.execute("DELETE FROM runtime_state WHERE key='current_draft'")
        elif mutation == "relational_row":
            connection.execute(
                "INSERT INTO participants "
                "(name, gender, level, weight, games_played, active, card) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("target", "male", "beginner", 1.0, 0, 1, "TARGET-CARD"),
            )
        elif mutation == "app_config_row":
            connection.execute(
                "INSERT INTO app_config (key, config_json, version) VALUES (?, ?, ?)",
                ("main", "{}", 1),
            )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(MigrationError):
        apply_import_plan(target, plan)


@pytest.mark.parametrize(
    "config",
    [
        {"smtp": {"password": "secret-value"}},
        {"mail": [{"api_key": "secret-value"}]},
        {"nested": {"auth": {"access_token": "secret-value"}}},
        {"smtp": {"Password": "secret-value", "API_KEY": "secret-value"}},
        {"nested": {"Client_Secret": "secret-value"}},
    ],
)
def test_nested_secret_config_fails_closed_without_logging_value(fixture, tmp_path, config):
    connection = sqlite3.connect(fixture["database"])
    try:
        connection.execute(
            "UPDATE app_config SET config_json=? WHERE key='main'",
            (json.dumps(config),),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(MigrationError) as error:
        build_export(fixture, tmp_path)
    assert "secret-value" not in str(error.value)


def test_legacy_nested_secret_config_fails_closed(fixture, tmp_path):
    connection = sqlite3.connect(fixture["database"])
    try:
        connection.execute("DELETE FROM app_config")
        connection.commit()
    finally:
        connection.close()
    fixture["config"].write_text(
        json.dumps({"mail": [{"api_key": "secret-value"}]}),
        encoding="utf-8",
    )
    with pytest.raises(MigrationError) as error:
        build_export(fixture, tmp_path)
    assert "secret-value" not in str(error.value)


def test_schema_contract_rejects_missing_unique_index(fixture):
    connection = sqlite3.connect(fixture["database"])
    try:
        connection.execute("DROP INDEX uq_match_history_round_court")
        with pytest.raises(MigrationError):
            validate_source_schema(connection)
    finally:
        connection.close()


def test_schema_contract_rejects_wrong_partial_unique_predicate(tmp_path):
    fixture = create_synthetic_fixture(tmp_path / "fixture")
    connection = sqlite3.connect(fixture["database"])
    try:
        connection.execute("DROP INDEX uq_match_round_session_round")
        connection.execute(
            "CREATE UNIQUE INDEX uq_match_round_session_round "
            "ON match_rounds(session_id, round_number) WHERE session_id IS NULL"
        )
        with pytest.raises(MigrationError):
            validate_source_schema(connection)
    finally:
        connection.close()


def test_schema_contract_rejects_wrong_foreign_key(tmp_path):
    fixture = create_synthetic_fixture(tmp_path / "fixture")
    connection = sqlite3.connect(fixture["database"])
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE match_histories")
        connection.execute(
            "CREATE TABLE match_histories ("
            "id INTEGER PRIMARY KEY, "
            "round_id INTEGER NOT NULL REFERENCES match_sessions(id), "
            "court_number INTEGER NOT NULL, "
            "team1_player1_id INTEGER NOT NULL REFERENCES participants(id), "
            "team1_player2_id INTEGER NOT NULL REFERENCES participants(id), "
            "team2_player1_id INTEGER NOT NULL REFERENCES participants(id), "
            "team2_player2_id INTEGER NOT NULL REFERENCES participants(id), "
            "team1_score INTEGER, team2_score INTEGER, score_text TEXT, "
            "winner_team INTEGER, created_at TEXT NOT NULL)"
        )
        with pytest.raises(MigrationError):
            validate_source_schema(connection)
    finally:
        connection.close()


def test_schema_contract_rejects_missing_not_null(tmp_path):
    fixture = create_synthetic_fixture(tmp_path / "fixture")
    connection = sqlite3.connect(fixture["database"])
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE match_rounds")
        connection.execute(
            "CREATE TABLE match_rounds ("
            "id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES match_sessions(id), "
            "round_number INTEGER, created_at TEXT NOT NULL)"
        )
        with pytest.raises(MigrationError):
            validate_source_schema(connection)
    finally:
        connection.close()


def test_schema_contract_rejects_missing_required_column(tmp_path):
    database = tmp_path / "missing-column.db"
    connection = sqlite3.connect(database)
    try:
        for migration in (
            ROOT / "migrations/d1/0001_phase3_participants_config.sql",
            ROOT / "migrations/d1/0002_phase4_match_relational.sql",
            ROOT / "migrations/d1/0004_phase6_line_notifications.sql",
        ):
            connection.executescript(migration.read_text(encoding="utf-8"))
        with pytest.raises(MigrationError):
            validate_source_schema(connection)
    finally:
        connection.close()


def test_target_schema_requires_runtime_cas_guard(fixture):
    connection = sqlite3.connect(fixture["database"])
    try:
        connection.execute("DROP TABLE runtime_state_cas_guard")
        with pytest.raises(MigrationError):
            validate_source_schema(connection, require_support_tables=True)
    finally:
        connection.close()


def test_target_validation_fk_join_runtime_config_and_cas(fixture, tmp_path):
    _snapshot, artifact, payload = build_export(fixture, tmp_path)
    plan = write_import_plan(artifact, tmp_path / "plan", batch_size=100)
    target = tmp_path / "target.db"
    initialize_sqlite_target(target, ROOT / "migrations/d1")
    apply_import_plan(target, plan)
    report = validate_sqlite_target(target, payload)
    assert_valid(report)
    smoke = post_import_smoke(target, participant_id_max=4)
    assert_valid(smoke)


def test_r2_archive_plan_ignores_nonarchive_and_is_idempotent(fixture, tmp_path):
    plan = build_archive_plan(fixture["archives"])
    assert plan["object_count"] == 2
    assert [item["filename"] for item in plan["ignored_files"]] == ["not_json.txt"]
    assert all(item["target_key"].startswith("history_dumps/") for item in plan["archives"])
    target = tmp_path / "r2"
    first = copy_archives_to_directory(plan, target)
    second = copy_archives_to_directory(plan, target)
    assert all(item["status"] == "uploaded" for item in first)
    assert all(item["status"] == "idempotent_existing" for item in second)
    assert validate_archive_directory(plan, target)["object_count"] == 2
    bad = target / plan["archives"][0]["target_key"]
    bad.write_bytes(b"different")
    with pytest.raises(ValidationError):
        copy_archives_to_directory(plan, target)


def test_dry_run_does_not_write_export(fixture, tmp_path):
    output = tmp_path / "dry-run-export.json"
    export_snapshot(
        fixture["database"],
        output,
        legacy_directory=fixture["source_directory"],
        config_path=fixture["config"],
        dry_run=True,
    )
    assert not output.exists()


def test_import_dry_run_preflights_without_writing_target_or_plan(
    fixture, tmp_path, capsys
):
    _snapshot, artifact, _payload = build_export(fixture, tmp_path / "source")
    archive_plan_path = tmp_path / "archive-plan.json"
    build_archive_plan(fixture["archives"], archive_plan_path)
    target = tmp_path / "target.db"
    initialize_sqlite_target(target, ROOT / "migrations/d1")
    before = target.read_bytes()
    plan_directory = tmp_path / "dry-run-plan"

    assert migration_cli_main([
        "import",
        "--export", str(artifact),
        "--plan-directory", str(plan_directory),
        "--target", str(target),
        "--archive-plan", str(archive_plan_path),
        "--dry-run",
    ]) == 0
    capsys.readouterr()
    assert target.read_bytes() == before
    assert not plan_directory.exists()

    connection = sqlite3.connect(target)
    try:
        connection.execute(
            "UPDATE runtime_state SET version=2 WHERE key='current_match'"
        )
        connection.commit()
    finally:
        connection.close()
    assert migration_cli_main([
        "import",
        "--export", str(artifact),
        "--plan-directory", str(plan_directory),
        "--target", str(target),
        "--archive-plan", str(archive_plan_path),
        "--dry-run",
    ]) == 2
    capsys.readouterr()
    assert not plan_directory.exists()
