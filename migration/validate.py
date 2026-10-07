"""Source/target checks for migration artifacts."""

from contextlib import closing
from pathlib import Path
import sqlite3

from data.runtime_state import CURRENT_DRAFT, CURRENT_MATCH

from .errors import MigrationError, ValidationError
from .export_sqlite import (
    _validate_unique_groups,
    normalize_row,
    read_table_rows,
)
from .manifest import read_json, table_summary
from .schema import IMPORT_ORDER, TABLE_COLUMNS, quote_identifier, validate_source_schema


def _check(report, name, actual, expected):
    passed = actual == expected
    item = {"name": name, "status": "PASS" if passed else "FAIL"}
    if isinstance(actual, (int, float, str, bool, dict, list)) or actual is None:
        item["actual"] = actual
    if isinstance(expected, (int, float, str, bool, dict, list)) or expected is None:
        item["expected"] = expected
    report["checks"].append(item)
    if not passed:
        report["ok"] = False


def _join_counts(connection):
    queries = {
        "session_rounds": (
            "SELECT COUNT(*) FROM match_sessions s JOIN match_rounds r "
            "ON r.session_id = s.id"
        ),
        "round_matches": (
            "SELECT COUNT(*) FROM match_rounds r JOIN match_histories h "
            "ON h.round_id = r.id"
        ),
        "round_bench": (
            "SELECT COUNT(*) FROM match_rounds r JOIN bench_histories b "
            "ON b.round_id = r.id"
        ),
        "participant_line_accounts": (
            "SELECT COUNT(*) FROM participants p JOIN line_accounts a "
            "ON a.participant_id = p.id"
        ),
        "notification_delivery_logs": (
            "SELECT COUNT(*) FROM match_notifications n JOIN notification_delivery_logs l "
            "ON l.session_id = n.session_id AND l.match_count = n.match_count "
            "AND l.channel = n.channel"
        ),
    }
    return {
        name: int(connection.execute(sql).fetchone()[0])
        for name, sql in queries.items()
    }


def _source_join_counts(payload):
    rows = payload["tables"]
    sessions = {row["id"] for row in rows["match_sessions"]}
    rounds = rows["match_rounds"]
    round_ids = {row["id"] for row in rounds}
    notification_keys = {
        (row["session_id"], row["match_count"], row["channel"])
        for row in rows["match_notifications"]
    }
    return {
        "session_rounds": sum(row["session_id"] in sessions for row in rounds),
        "round_matches": sum(row["round_id"] in round_ids for row in rows["match_histories"]),
        "round_bench": sum(row["round_id"] in round_ids for row in rows["bench_histories"]),
        "participant_line_accounts": sum(
            row["participant_id"] in {item["id"] for item in rows["participants"]}
            for row in rows["line_accounts"]
        ),
        "notification_delivery_logs": sum(
            (row["session_id"], row["match_count"], row["channel"]) in notification_keys
            for row in rows["notification_delivery_logs"]
        ),
    }


def _ordering(connection, table):
    if table not in {"match_rounds", "match_histories", "notification_delivery_logs"}:
        return []
    timestamp_column = "sent_at" if table == "notification_delivery_logs" else "created_at"
    rows = connection.execute(
        f"SELECT id FROM {quote_identifier(table)} "
        f"ORDER BY {quote_identifier(timestamp_column)} DESC, id DESC LIMIT 5"
    ).fetchall()
    return [int(row[0]) for row in rows]


def _source_ordering(payload, table):
    timestamp_column = "sent_at" if table == "notification_delivery_logs" else "created_at"
    rows = sorted(
        payload["tables"][table],
        key=lambda row: (row[timestamp_column], row["id"]),
        reverse=True,
    )
    return [row["id"] for row in rows[:5]]


def validate_sqlite_target(target, export_payload):
    """Compare a schema-initialized target with exported safe metadata."""
    if isinstance(export_payload, (str, Path)):
        export_payload = read_json(export_payload)
    target = Path(target).resolve()
    if not target.is_file():
        raise MigrationError(f"Target database does not exist: {target}")
    report = {"ok": True, "checks": []}
    with closing(sqlite3.connect(target)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        validate_source_schema(connection, require_support_tables=True)
        actual_rows = {
            table: read_table_rows(connection, table)
            for table in IMPORT_ORDER
        }
        for table in IMPORT_ORDER:
            actual_summary = table_summary(
                actual_rows[table],
                primary_key="id" if "id" in TABLE_COLUMNS[table] else "key",
            )
            _check(
                report,
                f"table {table} checksum",
                actual_summary,
                export_payload["table_summaries"][table],
            )
        fk_rows = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        _check(report, "foreign_key_check", len(fk_rows), 0)
        try:
            _validate_unique_groups(actual_rows)
            _check(report, "unique invariants", "valid", "valid")
        except ValidationError as error:
            report["checks"].append({"name": "unique invariants", "status": "FAIL"})
            report["ok"] = False

        source_joins = _source_join_counts(export_payload)
        for name, actual in _join_counts(connection).items():
            _check(report, f"join {name}", actual, source_joins[name])
        for table in ("match_rounds", "match_histories", "notification_delivery_logs"):
            _check(
                report,
                f"ordering {table}",
                _ordering(connection, table),
                _source_ordering(export_payload, table),
            )

        for runtime_key in (CURRENT_MATCH, CURRENT_DRAFT):
            actual_runtime = connection.execute(
                "SELECT state_json, version FROM runtime_state WHERE key=?",
                (runtime_key,),
            ).fetchone()
            expected_runtime = next(
                row for row in export_payload["tables"]["runtime_state"]
                if row["key"] == runtime_key
            )
            _check(
                report,
                f"runtime {runtime_key}",
                None if actual_runtime is None else
                (actual_runtime["state_json"], actual_runtime["version"]),
                (expected_runtime["state_json"], expected_runtime["version"]),
            )
        config = connection.execute(
            "SELECT config_json, version FROM app_config WHERE key='main'"
        ).fetchone()
        exported_config = export_payload["tables"]["app_config"][0]
        _check(
            report,
            "app_config main",
            None if config is None else (config["config_json"], config["version"]),
            (exported_config["config_json"], exported_config["version"]),
        )
    return report


def assert_valid(report):
    if not report.get("ok"):
        failed = [item["name"] for item in report.get("checks", []) if item["status"] == "FAIL"]
        raise ValidationError("Validation failed: " + ", ".join(failed))
    return report


def post_import_smoke(target, *, participant_id_max=None):
    """Exercise new-ID allocation and both versioned CAS rows on a temp D1."""
    target = Path(target).resolve()
    report = {"ok": True, "checks": []}
    with closing(sqlite3.connect(target)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        max_id = int(connection.execute("SELECT COALESCE(MAX(id), 0) FROM participants").fetchone()[0])
        if participant_id_max is not None:
            _check(report, "participant max id", max_id, participant_id_max)
        connection.execute(
            "INSERT INTO participants "
            "(name, gender, level, weight, games_played, active, card) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("phase10-smoke", "male", "beginner", 1.0, 0, 1, "PHASE10-SMOKE"),
        )
        new_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        _check(report, "post-import participant insert", new_id > max_id, True)
        connection.execute("DELETE FROM participants WHERE id=?", (new_id,))

        connection.execute(
            "INSERT INTO match_sessions (status, match_count, created_at, creation_token) "
            "VALUES (?, ?, ?, ?)",
            ("draft", 0, "2026-09-19T00:00:00.000000Z", "phase10-smoke-token"),
        )
        session_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        _check(report, "post-import session insert", session_id > 0, True)
        connection.execute("DELETE FROM match_sessions WHERE id=?", (session_id,))
        connection.commit()

        match = connection.execute(
            "SELECT state_json, version FROM runtime_state WHERE key='current_match'"
        ).fetchone()
        connection.execute("BEGIN")
        changed = connection.execute(
            "UPDATE runtime_state SET state_json=?, version=version+1 "
            "WHERE key='current_match' AND version=?",
            (match["state_json"], match["version"]),
        ).rowcount
        connection.rollback()
        _check(report, "runtime CAS", changed, 1)

        config = connection.execute(
            "SELECT config_json, version FROM app_config WHERE key='main'"
        ).fetchone()
        connection.execute("BEGIN")
        changed = connection.execute(
            "UPDATE app_config SET config_json=?, version=version+1 "
            "WHERE key='main' AND version=?",
            (config["config_json"], config["version"]),
        ).rowcount
        connection.rollback()
        _check(report, "config CAS", changed, 1)
        connection.commit()
    return report


def validate_target_database(target, export_artifact, *, report_path=None):
    payload = read_json(export_artifact) if isinstance(export_artifact, (str, Path)) else export_artifact
    report = validate_sqlite_target(target, payload)
    if report_path is not None:
        from .manifest import write_json
        write_json(report_path, report)
    return assert_valid(report)
