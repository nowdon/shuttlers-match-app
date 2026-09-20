"""Prepared-parameter D1 import plans and disposable SQLite execution."""

from contextlib import closing
from pathlib import Path
import sqlite3

from data.runtime_state import CURRENT_DRAFT, CURRENT_MATCH

from .errors import MigrationError
from .export_sqlite import validate_export_payload
from .manifest import read_json, write_json
from .snapshot import open_readonly
from .schema import (
    IMPORT_ORDER,
    TABLE_COLUMNS,
    migration_runtime_seed_rows,
    quote_identifier,
    validate_source_schema,
)


def _quote_sql_value(value):
    connection = sqlite3.connect(":memory:")
    try:
        quoted = connection.execute("SELECT quote(?)", (value,)).fetchone()[0]
        if quoted is None:
            return "NULL"
        return str(quoted)
    finally:
        connection.close()


def _render_insert(statement, params):
    values = ", ".join(_quote_sql_value(value) for value in params)
    return statement.replace(", ".join("?" for _ in params), values)


def _insert_statement(table):
    columns = TABLE_COLUMNS[table]
    quoted_columns = ", ".join(quote_identifier(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    return (
        f"INSERT INTO {quote_identifier(table)} ({quoted_columns}) "
        f"VALUES ({placeholders})"
    )


def target_nonempty(connection, migrations_directory=None):
    """Return safe counts for data rows that block a fresh-target import."""
    counts = {}
    for table in IMPORT_ORDER:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            continue
        counts[table] = int(connection.execute(
            f"SELECT COUNT(*) FROM {quote_identifier(table)}"
        ).fetchone()[0])

    # The expected rows are read by applying the authoritative 0003 DDL.  A
    # matching key set alone is insufficient because state_json/version may
    # have been changed in an already-used target.
    runtime_nonempty = counts.get("runtime_state", 0) not in (0, 2)
    if counts.get("runtime_state", 0) == 2:
        actual = [
            {"key": row[0], "state_json": row[1], "version": int(row[2])}
            for row in connection.execute(
                "SELECT key, state_json, version FROM runtime_state ORDER BY key"
            ).fetchall()
        ]
        runtime_nonempty = actual != migration_runtime_seed_rows(migrations_directory)
    blocking = {
        table: count for table, count in counts.items()
        if table != "runtime_state" and count
    }
    if runtime_nonempty:
        blocking["runtime_state"] = counts.get("runtime_state", 0)
    return blocking


def build_import_plan(export_artifact, *, batch_size=100):
    if batch_size < 1:
        raise MigrationError("batch_size must be positive")
    payload = read_json(export_artifact) if isinstance(export_artifact, (str, Path)) else export_artifact
    validate_export_payload(payload)
    batches = []
    sql_files = []
    prelude = [
        "DELETE FROM \"runtime_state\" "
        "WHERE \"key\" IN ('current_match', 'current_draft');"
    ]
    # Wrangler D1 rejects explicit BEGIN/COMMIT in ``d1 execute`` files.  The
    # prepared plan remains the source of truth; each file is submitted as one
    # Wrangler command and the SQLite executor below uses a real transaction.
    sql_files.append(str(Path("sql_batches") / "000-prelude.sql"))

    tables = payload.get("tables") or {}
    for table in IMPORT_ORDER:
        rows = tables.get(table)
        if not isinstance(rows, list):
            raise MigrationError(f"Export is missing table rows: {table}")
        statement = _insert_statement(table)
        columns = TABLE_COLUMNS[table]
        for offset in range(0, len(rows), batch_size):
            chunk = rows[offset:offset + batch_size]
            params = [[row[column] for column in columns] for row in chunk]
            batch_index = len(batches) + 1
            filename = f"{batch_index:04d}-{table}.sql"
            batches.append({
                "table": table,
                "statement": statement,
                "params": params,
                "row_count": len(params),
                "sql_file": str(Path("sql_batches") / filename),
            })
            sql_files.append(str(Path("sql_batches") / filename))
    return {
        "format_version": 1,
        "batch_size": batch_size,
        "import_order": list(IMPORT_ORDER),
        "prelude": prelude,
        "batches": batches,
        "sql_files": sql_files,
        "table_summaries": payload["table_summaries"],
    }


def write_import_plan(export_artifact, output_directory, *, batch_size=100):
    plan = build_import_plan(export_artifact, batch_size=batch_size)
    output_directory = Path(output_directory).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    sql_directory = output_directory / "sql_batches"
    sql_directory.mkdir(parents=True, exist_ok=True)
    (sql_directory / "000-prelude.sql").write_text(
        "\n".join(plan["prelude"]) + "\n", encoding="utf-8"
    )
    for batch in plan["batches"]:
        rendered = [
            _render_insert(batch["statement"], row_params)
            for row_params in batch["params"]
        ]
        (output_directory / batch["sql_file"]).write_text(
            ";\n".join(rendered) + ";\n", encoding="utf-8"
        )
    write_json(output_directory / "import_plan.json", plan)
    return plan


def initialize_sqlite_target(target, migrations_directory):
    target = Path(target).resolve()
    migrations_directory = Path(migrations_directory).resolve()
    migration_files = sorted(migrations_directory.glob("*.sql"))
    if not migration_files:
        raise MigrationError(f"No D1 migrations found: {migrations_directory}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(target)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for migration in migration_files:
            connection.executescript(migration.read_text(encoding="utf-8"))
        connection.commit()


def preflight_import_target(target, migrations_directory=None):
    """Validate a target using a read-only connection and no writes."""
    target = Path(target).resolve()
    if not target.is_file():
        raise MigrationError(f"Target database does not exist: {target}")
    with closing(open_readonly(target)) as connection:
        validate_source_schema(
            connection,
            migrations_directory,
            require_support_tables=True,
        )
        blocking = target_nonempty(connection, migrations_directory)
        if blocking:
            raise MigrationError(f"Target is not a fresh D1 target: {blocking}")
    return {"schema": "valid", "target": "fresh", "writes": 0}


def apply_import_plan(target, plan, *, allow_nonempty=False, migrations_directory=None):
    """Apply the prepared plan to a schema-initialized disposable SQLite D1.

    ``allow_nonempty`` is retained as a defensive API parameter but cannot be
    enabled: Phase 10 intentionally has no merge/overwrite importer.
    """
    if allow_nonempty:
        raise MigrationError("Non-empty target imports are not supported")
    target = Path(target).resolve()
    if not target.is_file():
        raise MigrationError(f"Target database does not exist: {target}")
    with closing(sqlite3.connect(target)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        validate_source_schema(
            connection,
            migrations_directory,
            require_support_tables=True,
        )
        blocking = target_nonempty(connection, migrations_directory)
        if blocking:
            raise MigrationError(f"Target is not empty: {blocking}")
        try:
            connection.execute("BEGIN")
            connection.execute(
                "DELETE FROM runtime_state WHERE key IN (?, ?)",
                (CURRENT_DRAFT, CURRENT_MATCH),
            )
            for batch in plan["batches"]:
                for params in batch["params"]:
                    connection.execute(batch["statement"], tuple(params))
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise MigrationError("D1 import failed; target transaction was rolled back") from error
    return {"imported_batches": len(plan["batches"]), "imported_rows": sum(
        batch["row_count"] for batch in plan["batches"]
    )}


def apply_plan_to_sqlite(target, plan_path):
    return apply_import_plan(target, read_json(plan_path))
