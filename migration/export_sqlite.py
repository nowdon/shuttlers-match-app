"""Deterministic, application-aware export from a SQLite snapshot."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .errors import MigrationError, ValidationError
from .manifest import table_summary, write_json
from .schema import (
    BOOL_COLUMNS,
    IMPORT_ORDER,
    RELATIONAL_TABLES,
    SUPPORT_TABLES,
    TABLE_COLUMNS,
    PARTIAL_UNIQUE_GROUPS,
    UNIQUE_GROUPS,
    quote_identifier,
    validate_source_schema,
)
from .snapshot import open_readonly, snapshot_metadata
from .transform import (
    transform_config_from_legacy,
    transform_existing_config,
    transform_runtime_from_legacy,
    validate_runtime_state_rows,
)


def _normalize_boolean(value, table, column):
    if value in (False, True):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return int(value)
    if isinstance(value, str) and value.lower() in {"0", "1", "false", "true"}:
        return 1 if value.lower() in {"1", "true"} else 0
    raise ValidationError(f"{table}.{column} is not a boolean value")


def normalize_row(table, row):
    normalized = {}
    for column in TABLE_COLUMNS[table]:
        value = row[column]
        if (table, column) in BOOL_COLUMNS and value is not None:
            value = _normalize_boolean(value, table, column)
        normalized[column] = value
    return normalized


def read_table_rows(connection, table):
    columns = TABLE_COLUMNS[table]
    column_sql = ", ".join(quote_identifier(column) for column in columns)
    order_column = "id" if "id" in columns else "key"
    rows = connection.execute(
        f"SELECT {column_sql} FROM {quote_identifier(table)} "
        f"ORDER BY {quote_identifier(order_column)}"
    ).fetchall()
    return [normalize_row(table, row) for row in rows]


def _validate_unique_groups(rows_by_table):
    groups = dict(UNIQUE_GROUPS)
    groups["runtime_state"] = (("key",),)
    groups["app_config"] = (("key",),)
    for table, columns_list in groups.items():
        rows = rows_by_table.get(table, [])
        for columns in columns_list:
            seen = set()
            duplicates = []
            for row in rows:
                value = tuple(row[column] for column in columns)
                if (table, columns) in PARTIAL_UNIQUE_GROUPS and any(
                    item is None for item in value
                ):
                    continue
                if value in seen:
                    duplicates.append(value)
                seen.add(value)
            if duplicates:
                raise ValidationError(
                    f"Duplicate unique key in {table}: {columns} {duplicates[:3]}"
                )


def _validate_export_foreign_keys(rows_by_table):
    participants = {row["id"] for row in rows_by_table["participants"]}
    sessions = {row["id"] for row in rows_by_table["match_sessions"]}
    rounds = {row["id"] for row in rows_by_table["match_rounds"]}
    def ensure(value, values, label):
        if value is not None and value not in values:
            raise ValidationError(f"Orphan foreign key {label}: {value}")

    for row in rows_by_table["match_rounds"]:
        ensure(row["session_id"], sessions, "match_rounds.session_id")
    for row in rows_by_table["match_histories"]:
        ensure(row["round_id"], rounds, "match_histories.round_id")
        for column in (
            "team1_player1_id", "team1_player2_id", "team2_player1_id",
            "team2_player2_id",
        ):
            ensure(row[column], participants, f"match_histories.{column}")
    for row in rows_by_table["bench_histories"]:
        ensure(row["round_id"], rounds, "bench_histories.round_id")
        ensure(row["participant_id"], participants, "bench_histories.participant_id")
    for row in rows_by_table["line_accounts"]:
        ensure(row["participant_id"], participants, "line_accounts.participant_id")
    for table in ("notification_subscriptions", "line_link_tokens"):
        for row in rows_by_table[table]:
            ensure(row["session_id"], sessions, f"{table}.session_id")
            ensure(row["participant_id"], participants, f"{table}.participant_id")
    for row in rows_by_table["match_notifications"]:
        ensure(row["session_id"], sessions, "match_notifications.session_id")
    for row in rows_by_table["notification_delivery_logs"]:
        ensure(row["session_id"], sessions, "notification_delivery_logs.session_id")
        ensure(row["participant_id"], participants, "notification_delivery_logs.participant_id")


def _validate_foreign_keys(connection, rows_by_table):
    pragma_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
    if pragma_rows:
        raise ValidationError("Source foreign_key_check is not empty")
    _validate_export_foreign_keys(rows_by_table)


def _read_support_rows(connection, table):
    return read_table_rows(connection, table) if table in _table_names(connection) else []


def _table_names(connection):
    return {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _runtime_rows(connection, tables, rows_by_table, legacy_directory):
    if "runtime_state" in tables:
        rows = read_table_rows(connection, "runtime_state")
        if rows:
            validate_runtime_state_rows(
                rows,
                {row["id"] for row in rows_by_table["participants"]},
                {row["id"] for row in rows_by_table["match_sessions"]},
            )
            return rows, "runtime_state"
    rows = transform_runtime_from_legacy(
        legacy_directory,
        {row["id"] for row in rows_by_table["participants"]},
        {row["id"] for row in rows_by_table["match_sessions"]},
    )
    return rows, "legacy_json"


def _config_row(connection, tables, legacy_config_path):
    if "app_config" in tables:
        rows = read_table_rows(connection, "app_config")
        if rows:
            main_rows = [row for row in rows if row.get("key") == "main"]
            if len(main_rows) != 1 or len(rows) != 1:
                raise MigrationError("app_config must contain exactly one main row")
            return transform_existing_config(main_rows[0]), "app_config"
    return transform_config_from_legacy(legacy_config_path), "config.json"


def export_snapshot(
    snapshot,
    output,
    *,
    legacy_directory=None,
    config_path=None,
    dry_run=False,
):
    """Export a snapshot to a JSON artifact and return the export payload."""
    snapshot = Path(snapshot).resolve()
    legacy_directory = Path(legacy_directory or snapshot.parent).resolve()
    config_path = Path(config_path or legacy_directory / "config.json").resolve()
    with closing(open_readonly(snapshot)) as connection:
        tables = validate_source_schema(connection)
        rows_by_table = {
            table: read_table_rows(connection, table)
            for table in RELATIONAL_TABLES
        }
        _validate_unique_groups(rows_by_table)
        _validate_foreign_keys(connection, rows_by_table)
        runtime_rows, runtime_source = _runtime_rows(
            connection, tables, rows_by_table, legacy_directory
        )
        config_row, config_source = _config_row(connection, tables, config_path)

    rows_by_table["runtime_state"] = runtime_rows
    rows_by_table["app_config"] = [config_row]
    table_summaries = {
        table: table_summary(rows, primary_key="id" if "id" in TABLE_COLUMNS[table] else "key")
        for table, rows in rows_by_table.items()
    }
    payload = {
        "format_version": 1,
        "source": snapshot_metadata(snapshot),
        "schema": {
            "required_tables": list(RELATIONAL_TABLES),
            "support_tables": list(SUPPORT_TABLES),
        },
        "tables": rows_by_table,
        "table_summaries": table_summaries,
        "runtime_state": {"source": runtime_source, "version": 1},
        "app_config": {
            "source": config_source,
            "key": config_row["key"],
            "version": config_row["version"],
        },
    }
    if not dry_run:
        write_json(output, payload)
    return payload


def validate_export_payload(payload):
    """Validate an export artifact before it is used for planning/import."""
    if not isinstance(payload, dict) or payload.get("format_version") != 1:
        raise ValidationError("Unsupported export artifact")
    source = payload.get("source")
    snapshot = source.get("snapshot") if isinstance(source, dict) else None
    if (
        not isinstance(source, dict)
        or not isinstance(snapshot, dict)
        or not isinstance(source.get("source_sha256"), str)
        or not isinstance(source.get("snapshot_sha256"), str)
        or source["snapshot_sha256"] != snapshot.get("sha256")
    ):
        raise ValidationError("Export source manifest is invalid")
    tables = payload.get("tables")
    summaries = payload.get("table_summaries")
    if not isinstance(tables, dict) or not isinstance(summaries, dict):
        raise ValidationError("Export artifact is missing table metadata")
    if set(tables) != set(IMPORT_ORDER) or set(summaries) != set(IMPORT_ORDER):
        raise ValidationError("Export artifact table set is invalid")

    normalized_tables = {}
    for table in IMPORT_ORDER:
        rows = tables[table]
        if not isinstance(rows, list):
            raise ValidationError(f"Export rows are invalid for table {table}")
        columns = TABLE_COLUMNS[table]
        normalized_rows = []
        primary_key = "id" if "id" in columns else "key"
        primary_keys = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(columns):
                raise ValidationError(f"Export row shape is invalid for table {table}")
            normalized = normalize_row(table, row)
            if normalized != row:
                raise ValidationError(f"Export row types are invalid for table {table}")
            primary_keys.append(row[primary_key])
            normalized_rows.append(normalized)
        if len(primary_keys) != len(set(primary_keys)):
            raise ValidationError(f"Duplicate primary key in export table {table}")
        expected_summary = table_summary(
            normalized_rows,
            primary_key=primary_key,
        )
        if summaries[table] != expected_summary:
            raise ValidationError(f"Export summary mismatch for table {table}")
        normalized_tables[table] = normalized_rows

    _validate_unique_groups(normalized_tables)
    _validate_export_foreign_keys(normalized_tables)
    validate_runtime_state_rows(
        normalized_tables["runtime_state"],
        {row["id"] for row in normalized_tables["participants"]},
        {row["id"] for row in normalized_tables["match_sessions"]},
    )

    config_rows = normalized_tables["app_config"]
    if len(config_rows) != 1 or config_rows[0]["key"] != "main":
        raise ValidationError("Export must contain exactly one app_config main row")
    try:
        config = json.loads(config_rows[0]["config_json"])
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValidationError("Export app_config JSON is invalid") from error
    if not isinstance(config, dict):
        raise ValidationError("Export app_config must be a JSON object")
    from .transform import canonical_config_json
    if canonical_config_json(config) != config_rows[0]["config_json"]:
        raise ValidationError("Export app_config is not canonical or contains secrets")

    runtime_metadata = payload.get("runtime_state")
    if (
        not isinstance(runtime_metadata, dict)
        or runtime_metadata.get("version") != 1
        or runtime_metadata.get("source") not in {"runtime_state", "legacy_json"}
    ):
        raise ValidationError("Export runtime metadata is invalid")
    config_metadata = payload.get("app_config")
    if (
        not isinstance(config_metadata, dict)
        or config_metadata.get("key") != "main"
        or config_metadata.get("version") != config_rows[0]["version"]
        or config_metadata.get("source") not in {"app_config", "config.json"}
    ):
        raise ValidationError("Export config metadata is invalid")
    return payload


# Short alias for callers that prefer the module's filename as the API name.
export_sqlite = export_snapshot
