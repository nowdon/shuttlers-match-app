"""Convert the known pre-Cloudflare SQLite shape to a fresh D1-schema source."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

from data.runtime_state import (
    CURRENT_DRAFT, CURRENT_MATCH, DEFAULT_MATCH_STATE, serialize_state,
)
from utils.runtime_state_migration import _load_legacy_state

from .errors import MigrationError, ValidationError
from .export_sqlite import export_snapshot, normalize_row
from .manifest import canonical_json_bytes, sha256_bytes, table_summary
from .schema import (
    DEFAULT_MIGRATIONS_DIRECTORY, IMPORT_ORDER, RELATIONAL_TABLES,
    TABLE_COLUMNS, existing_tables, migration_schema_contract,
    quote_identifier, schema_contract, sqlite_user_version,
    validate_source_schema,
)
from .snapshot import open_readonly
from .transform import (
    transform_config_from_legacy, transform_existing_config,
    validate_runtime_state_rows,
)


_MISSING_COLUMNS = {
    "match_sessions": {"creation_token"},
    "match_rounds": {"session_id"},
}
_MISSING_UNIQUE = {
    "match_sessions": {("creation_token",)},
    "match_rounds": {("session_id", "round_number")},
    "match_histories": {("round_id", "court_number")},
    "bench_histories": {("round_id", "participant_id")},
}
_VARCHAR = {
    "participants": {"name": 80, "gender": 10, "level": 20, "card": 10},
    "match_sessions": {"status": 20},
    "line_accounts": {"line_user_id": 128, "display_name": 100},
    "notification_subscriptions": {"channel": 20},
    "line_link_tokens": {"token": 20},
    "match_notifications": {"channel": 20, "status": 20},
    "notification_delivery_logs": {"channel": 20, "status": 20},
}
_TIMESTAMPS = {
    "created_at", "updated_at", "confirmed_at", "notification_sent_at",
    "used_at", "expires_at", "sent_at",
}
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _legacy_contract():
    """The observed legacy ORM shape, expressed as deltas from D1 schema."""
    contract = migration_schema_contract()
    for table in RELATIONAL_TABLES:
        shape = contract[table]
        columns = []
        for column in shape["columns"]:
            name = column["name"]
            if name in _MISSING_COLUMNS.get(table, ()):
                continue
            column = dict(column)
            if name in _VARCHAR.get(table, {}):
                column["type"] = f"VARCHAR({_VARCHAR[table][name]})"
            elif name in _TIMESTAMPS:
                column["type"] = "DATETIME"
            elif (table, name) == ("participants", "weight"):
                column["type"] = "FLOAT"
            elif name == "active":
                column["type"] = "BOOLEAN"
            if name == "id":
                column["notnull"] = 1
            if table == "participants" and name in {"games_played", "active"}:
                column["notnull"] = 0
            column["default"] = ""
            columns.append(column)
        shape["columns"] = columns
        if table == "match_rounds":
            shape["foreign_keys"] = []
        missing = _MISSING_UNIQUE.get(table, set())
        shape["unique_indexes"] = [
            item for item in shape["unique_indexes"]
            if tuple(item["columns"]) not in missing
        ]
    return {table: contract[table] for table in RELATIONAL_TABLES}


def _inspect_source(connection):
    tables = existing_tables(connection)
    if tables != set(RELATIONAL_TABLES) or sqlite_user_version(connection) != 0:
        raise MigrationError("Unsupported legacy schema table set or user_version")
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('view', 'trigger') LIMIT 1"
    ).fetchone():
        raise MigrationError("Unsupported legacy views or triggers")
    expected = _legacy_contract()
    actual = schema_contract(connection, RELATIONAL_TABLES)
    for table in RELATIONAL_TABLES:
        if any(not index[2] for index in connection.execute(
            f"PRAGMA index_list({quote_identifier(table)})"
        )):
            raise MigrationError(f"Unsupported legacy index: {table}")
        if actual[table] != expected[table]:
            raise MigrationError(f"Unsupported legacy schema: {table}")
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValidationError("Legacy source integrity_check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValidationError("Legacy source foreign_key_check failed")
    counts = {
        table: connection.execute(
            f"SELECT COUNT(*) FROM {quote_identifier(table)}"
        ).fetchone()[0]
        for table in RELATIONAL_TABLES
    }
    if counts["match_rounds"]:
        raise ValidationError(
            "Legacy match_rounds.session_id cannot be reconstructed safely"
        )
    fingerprint = sha256_bytes(canonical_json_bytes(actual))
    return counts, fingerprint


def _runtime_rows(directory, participant_ids, session_ids):
    try:
        match = _load_legacy_state(
            directory / "match_state.json", DEFAULT_MATCH_STATE.copy()
        )
    except RuntimeError as error:
        raise ValidationError("Legacy runtime state is invalid") from error
    draft_path = directory / "draft_state.json"
    if draft_path.exists():
        try:
            draft = json.loads(draft_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise ValidationError("Legacy draft state is invalid") from error
        if draft is not None and not isinstance(draft, dict):
            raise ValidationError("Legacy draft state is invalid")
    else:
        draft = None
    rows = [
        {"key": CURRENT_MATCH, "state_json": serialize_state(match), "version": 1},
        {"key": CURRENT_DRAFT,
         "state_json": None if draft is None else serialize_state(draft),
         "version": 1},
    ]
    try:
        validate_runtime_state_rows(rows, participant_ids, session_ids)
    except ValidationError as error:
        raise ValidationError("Legacy runtime state or references are invalid") from error
    return rows


def _read_rows(connection, table):
    columns = [column for column in TABLE_COLUMNS[table]
               if column not in _MISSING_COLUMNS.get(table, ())]
    sql = ", ".join(quote_identifier(column) for column in columns)
    records = connection.execute(
        f"SELECT {sql} FROM {quote_identifier(table)} ORDER BY id"
    ).fetchall()
    rows = []
    for record in records:
        row = dict(zip(columns, record))
        for column in _MISSING_COLUMNS.get(table, ()):
            row[column] = None
        # Phase 10 canonicalizes only BOOL_COLUMNS to D1 INTEGER 0/1.
        # All other source column values retain their SQLite value/text.
        rows.append(normalize_row(table, row))
    return rows


def _insert_rows(connection, table, rows):
    if not rows:
        return
    columns = TABLE_COLUMNS[table]
    sql = (
        f"INSERT INTO {quote_identifier(table)} ("
        + ", ".join(quote_identifier(column) for column in columns)
        + ") VALUES (" + ", ".join("?" for _ in columns) + ")"
    )
    connection.executemany(sql, [tuple(row[column] for column in columns) for row in rows])


def canonicalize_legacy(source, output, *, legacy_directory=None, config_path=None,
                        dry_run=False):
    """Inspect a known legacy DB and create a new, validated canonical DB."""
    source = Path(source).resolve()
    requested_output = Path(output)
    if requested_output.is_symlink():
        raise MigrationError("Canonical output must not be a symlink")
    output = requested_output.resolve()
    directory = Path(legacy_directory or source.parent).resolve()
    config = Path(config_path or directory / "config.json").resolve()
    if (output == source or output.exists() or output.is_relative_to(directory)
            or output.is_relative_to(source.parent)
            or output.is_relative_to(_REPOSITORY_ROOT)):
        raise MigrationError("Canonical output must be new and outside the source bundle")
    with closing(open_readonly(source)) as input_db:
        counts, fingerprint = _inspect_source(input_db)
        input_db.row_factory = sqlite3.Row
        rows = {table: _read_rows(input_db, table) for table in RELATIONAL_TABLES}
        runtime = _runtime_rows(
            directory,
            {row["id"] for row in rows["participants"]},
            {row["id"] for row in rows["match_sessions"]},
        )
        app_config = transform_config_from_legacy(config)
    summaries = {table: table_summary(rows[table]) for table in RELATIONAL_TABLES}
    report = {
        "supported_legacy_schema": True,
        "legacy_schema_fingerprint": fingerprint,
        "source_row_counts": counts,
        "table_summaries": summaries,
        "planned_added_columns": {
            table: sorted(columns) for table, columns in _MISSING_COLUMNS.items()
        },
        "runtime_source": "legacy_json",
        "runtime_versions": {row["key"]: row["version"] for row in runtime},
        "config_source": "config.json",
        "config_version": app_config["version"],
        "dry_run": bool(dry_run),
    }
    if dry_run:
        return report

    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(output)) as target:
            target.row_factory = sqlite3.Row
            target.execute("PRAGMA foreign_keys=ON")
            # Match the current authoritative D1 migration set on this checkout.
            for migration in sorted(DEFAULT_MIGRATIONS_DIRECTORY.glob("*.sql")):
                target.executescript(migration.read_text(encoding="utf-8"))
            target.execute("BEGIN")
            for table in IMPORT_ORDER:
                if table in RELATIONAL_TABLES:
                    _insert_rows(target, table, rows[table])
            target.execute("DELETE FROM runtime_state WHERE key IN (?, ?)",
                           (CURRENT_MATCH, CURRENT_DRAFT))
            _insert_rows(target, "runtime_state", runtime)
            _insert_rows(target, "app_config", [app_config])
            if target.execute("PRAGMA foreign_key_check").fetchall():
                raise ValidationError("Canonical foreign_key_check failed")
            target.commit()
            validate_source_schema(target, require_support_tables=True)
            persisted_runtime = [dict(row) for row in target.execute(
                "SELECT key, state_json, version FROM runtime_state ORDER BY key"
            )]
            try:
                validate_runtime_state_rows(
                    persisted_runtime,
                    {row["id"] for row in rows["participants"]},
                    {row["id"] for row in rows["match_sessions"]},
                )
            except ValidationError as error:
                raise ValidationError("Canonical runtime validation failed") from error
            stored_config = target.execute(
                "SELECT key, config_json, version FROM app_config"
            ).fetchone()
            transform_existing_config(dict(zip(
                ("key", "config_json", "version"), stored_config
            )))
        export = export_snapshot(
            output, output.parent / "unused-export.json",
            legacy_directory=directory, config_path=config, dry_run=True,
        )
        for table in RELATIONAL_TABLES:
            if export["table_summaries"][table] != summaries[table]:
                raise ValidationError(f"Canonical preservation mismatch: {table}")
        report["canonical_schema"] = "PASS"
        report["phase10_export_validation"] = "PASS"
        report["target_table_summaries"] = {
            table: export["table_summaries"][table] for table in RELATIONAL_TABLES
        }
        return report
    except Exception as error:
        output.unlink(missing_ok=True)
        if isinstance(error, (MigrationError, ValidationError)):
            raise
        raise MigrationError("Legacy canonicalization failed") from error
