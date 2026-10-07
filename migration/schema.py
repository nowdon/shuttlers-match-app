"""The application-aware Phase 10 source/target schema contract."""

from pathlib import Path
import re
import sqlite3

from .errors import MigrationError


RELATIONAL_TABLES = (
    "participants",
    "match_sessions",
    "match_rounds",
    "match_histories",
    "bench_histories",
    "line_accounts",
    "notification_subscriptions",
    "line_link_tokens",
    "match_notifications",
    "notification_delivery_logs",
)

SUPPORT_TABLES = ("runtime_state", "app_config")
INTERNAL_TABLES = ("runtime_state_cas_guard",)

DEFAULT_MIGRATIONS_DIRECTORY = Path(__file__).resolve().parents[1] / "migrations" / "d1"

TABLE_COLUMNS = {
    "participants": (
        "id", "name", "gender", "level", "weight", "games_played",
        "active", "card",
    ),
    "match_sessions": (
        "id", "status", "match_count", "created_at", "confirmed_at",
        "notification_sent_at", "creation_token",
    ),
    "match_rounds": ("id", "session_id", "round_number", "created_at"),
    "match_histories": (
        "id", "round_id", "court_number", "team1_player1_id",
        "team1_player2_id", "team2_player1_id", "team2_player2_id",
        "team1_score", "team2_score", "score_text", "winner_team",
        "created_at",
    ),
    "bench_histories": ("id", "round_id", "participant_id", "created_at"),
    "line_accounts": (
        "id", "participant_id", "line_user_id", "display_name", "active",
        "created_at", "updated_at",
    ),
    "notification_subscriptions": (
        "id", "session_id", "participant_id", "channel", "active",
        "created_at", "updated_at",
    ),
    "line_link_tokens": (
        "id", "token", "participant_id", "session_id", "used_at",
        "expires_at", "created_at",
    ),
    "match_notifications": (
        "id", "session_id", "match_count", "channel", "status",
        "created_at", "sent_at",
    ),
    "notification_delivery_logs": (
        "id", "session_id", "participant_id", "match_count", "channel",
        "status", "error_message", "sent_at",
    ),
    "runtime_state": ("key", "state_json", "version"),
    "app_config": ("key", "config_json", "version"),
    "runtime_state_cas_guard": ("id",),
}

BOOL_COLUMNS = {
    ("participants", "active"),
    ("line_accounts", "active"),
    ("notification_subscriptions", "active"),
}

UNIQUE_GROUPS = {
    "participants": (("card",),),
    "match_sessions": (("creation_token",),),
    "match_rounds": (("session_id", "round_number"),),
    "match_histories": (("round_id", "court_number"),),
    "bench_histories": (("round_id", "participant_id"),),
    "line_accounts": (("participant_id",), ("line_user_id",)),
    "notification_subscriptions": (("session_id", "participant_id", "channel"),),
    "line_link_tokens": (("token",),),
    "match_notifications": (("session_id", "match_count", "channel"),),
}

PARTIAL_UNIQUE_GROUPS = {
    ("match_sessions", ("creation_token",)),
    ("match_rounds", ("session_id", "round_number")),
}

IMPORT_ORDER = (
    "participants",
    "match_sessions",
    "match_rounds",
    "match_histories",
    "bench_histories",
    "line_accounts",
    "notification_subscriptions",
    "line_link_tokens",
    "match_notifications",
    "notification_delivery_logs",
    "runtime_state",
    "app_config",
)


def quote_identifier(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(value)):
        raise MigrationError(f"Unsupported schema identifier: {value}")
    return '"' + str(value).replace('"', '""') + '"'


def existing_tables(connection):
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return {row[0] for row in rows}


def table_columns(connection, table):
    if table not in TABLE_COLUMNS:
        raise MigrationError(f"Unsupported schema table: {table}")
    return {row[1] for row in connection.execute(
        f"PRAGMA table_info({quote_identifier(table)})"
    ).fetchall()}


def _normalize_sql(value):
    return re.sub(r"\s+", " ", (value or "").strip().rstrip(";")).lower()


def _index_predicate(connection, index_name):
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone()
    sql = "" if row is None else row[0] or ""
    match = re.search(r"\bwhere\b(.+)$", sql, flags=re.IGNORECASE | re.DOTALL)
    return "" if match is None else _normalize_sql(match.group(1))


def schema_contract(connection, tables=None):
    """Return the structural contract for the selected application tables.

    The contract deliberately includes nullable/primary-key shape, foreign
    keys, and unique indexes including partial-index predicates.  Non-unique
    operational indexes are not part of the migration data contract.
    """
    selected = tuple(tables or TABLE_COLUMNS)
    contract = {}
    for table in selected:
        columns = [
            {
                "name": row[1],
                "type": (row[2] or "").upper(),
                "notnull": int(row[3]),
                "default": _normalize_sql(row[4]),
                "pk": int(row[5]),
            }
            for row in connection.execute(f"PRAGMA table_info({quote_identifier(table)})")
        ]
        foreign_keys = sorted(
            (
                row[2],
                row[3],
                row[4],
                row[5].upper(),
                row[6].upper(),
                row[7].upper(),
            )
            for row in connection.execute(f"PRAGMA foreign_key_list({quote_identifier(table)})")
        )
        unique_indexes = []
        for row in connection.execute(f"PRAGMA index_list({quote_identifier(table)})"):
            if not int(row[2]):
                continue
            index_name = row[1]
            index_columns = [
                item[2]
                for item in connection.execute(
                    f"PRAGMA index_info({quote_identifier(index_name)})"
                )
            ]
            unique_indexes.append(
                {
                    "columns": index_columns,
                    "partial": bool(row[4]),
                    "predicate": _index_predicate(connection, index_name),
                }
            )
        contract[table] = {
            "columns": columns,
            "foreign_keys": foreign_keys,
            "unique_indexes": sorted(
                unique_indexes,
                key=lambda item: (
                    tuple(item["columns"]), item["partial"], item["predicate"]
                ),
            ),
        }
    return contract


def _migration_files(migrations_directory=None):
    directory = Path(migrations_directory or DEFAULT_MIGRATIONS_DIRECTORY).resolve()
    files = sorted(directory.glob("*.sql"))
    if not files:
        raise MigrationError(f"No D1 migrations found: {directory}")
    return files


def migration_schema_contract(migrations_directory=None):
    """Build the expected contract directly from the authoritative DDL."""
    connection = sqlite3.connect(":memory:")
    try:
        for migration in _migration_files(migrations_directory):
            connection.executescript(migration.read_text(encoding="utf-8"))
        return schema_contract(connection, TABLE_COLUMNS)
    finally:
        connection.close()


def migration_runtime_seed_rows(migrations_directory=None):
    """Read runtime seed rows by applying the authoritative migration DDL."""
    connection = sqlite3.connect(":memory:")
    try:
        for migration in _migration_files(migrations_directory):
            connection.executescript(migration.read_text(encoding="utf-8"))
        return [
            {"key": row[0], "state_json": row[1], "version": row[2]}
            for row in connection.execute(
                "SELECT key, state_json, version FROM runtime_state ORDER BY key"
            )
        ]
    finally:
        connection.close()


def _schema_mismatch(table, expected, actual):
    if expected != actual:
        raise MigrationError(f"Schema contract mismatch for table {table}")


def validate_source_schema(
    connection,
    migrations_directory=None,
    *,
    require_support_tables=False,
):
    """Fail closed for missing current relational columns.

    ``runtime_state`` and ``app_config`` are optional only because Phase 10
    supports their documented legacy-file fallback.  If either table exists,
    its columns must still be current.
    """
    tables = existing_tables(connection)
    required_tables = list(RELATIONAL_TABLES)
    if require_support_tables:
        required_tables.extend(SUPPORT_TABLES + INTERNAL_TABLES)
    elif "runtime_state" in tables:
        required_tables.extend(INTERNAL_TABLES)
    missing_tables = [table for table in required_tables if table not in tables]
    if missing_tables:
        raise MigrationError("Source is missing required tables: " + ", ".join(missing_tables))
    expected = migration_schema_contract(migrations_directory)
    for table in RELATIONAL_TABLES + SUPPORT_TABLES + INTERNAL_TABLES:
        if table not in tables:
            continue
        _schema_mismatch(table, expected[table], schema_contract(connection, (table,))[table])
    return tables


def sqlite_user_version(connection):
    return int(connection.execute("PRAGMA user_version").fetchone()[0])
