"""Fail-closed Wrangler remote D1/R2 operations for explicit targets.

Disposable targets are the default. Production use requires exact resource
names, the expected D1 UUID, and a restricted private Worker config.
"""

from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile

from .archives import validate_archive_plan
from .errors import MigrationError, ValidationError
from .export_sqlite import validate_export_payload
from .import_plan import build_import_plan, preflight_import_target, write_import_plan
from .manifest import read_json, sha256_file
from .schema import DEFAULT_MIGRATIONS_DIRECTORY, IMPORT_ORDER, migration_runtime_seed_rows
from .validate import assert_valid, validate_sqlite_target


DISPOSABLE_PREFIX = "shuttlers-phase14-disposable-"
PRODUCTION_D1_NAME = "shuttlers-match-app-prod"
PRODUCTION_R2_NAME = "shuttlers-match-history-prod"


def _disposable_name(name):
    if not re.fullmatch(r"shuttlers-phase14-disposable-[a-z0-9-]+", str(name)):
        raise MigrationError("Phase 14.2A accepts only a named disposable resource")
    return str(name)


def _load_d1_export(sql_path, database_path):
    sql_path = Path(sql_path)
    if not sql_path.is_file() or sql_path.stat().st_size == 0:
        raise MigrationError("D1 export is missing or empty")
    with closing(sqlite3.connect(database_path)) as connection:
        try:
            connection.executescript(sql_path.read_text(encoding="utf-8"))
        except sqlite3.Error as error:
            raise MigrationError("D1 export cannot be replayed as SQLite") from error


def _verified_plan(export_path, plan_directory):
    export_path = Path(export_path).resolve()
    plan_directory = Path(plan_directory).resolve()
    payload = read_json(export_path)
    validate_export_payload(payload)
    plan = read_json(plan_directory / "import_plan.json")
    batch_size = plan.get("batch_size")
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise MigrationError("Invalid import plan batch size")
    if plan != build_import_plan(payload, batch_size=batch_size):
        raise MigrationError("Import plan differs from export")
    with tempfile.TemporaryDirectory(prefix="phase14-verify-plan-") as directory:
        expected = write_import_plan(export_path, directory, batch_size=batch_size)
        if expected != plan:
            raise MigrationError("Import plan cannot be reproduced")
        for relative in plan["sql_files"]:
            path = (plan_directory / relative).resolve()
            if not path.is_relative_to(plan_directory) or not path.is_file():
                raise MigrationError("Import SQL file is missing or outside plan")
            if sha256_file(path) != sha256_file(Path(directory) / relative):
                raise MigrationError("Import SQL file differs from deterministic plan")
    return payload, plan


class RemoteWrangler:
    """One explicit remote D1/R2 pair with a production opt-in guard."""

    def __init__(self, wrangler, config, database, bucket, *, runner=subprocess.run,
                 production_database_id=None):
        self.wrangler = str(Path(wrangler).resolve())
        self.config = str(Path(config).resolve())
        self.production = production_database_id is not None
        if self.production:
            if (database != PRODUCTION_D1_NAME or bucket != PRODUCTION_R2_NAME
                    or not re.fullmatch(r"[0-9a-fA-F-]{36}", production_database_id)):
                raise MigrationError("Production resource identity mismatch")
            try:
                settings = json.loads(Path(self.config).read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise MigrationError("Private production Wrangler config is invalid") from error
            if (settings.get("name") != "shuttlers-match-app"
                    or settings.get("routes") or settings.get("route")
                    or settings.get("vars", {}).get("PHASE14_PRE_CUTOVER_READ_ONLY") != "true"
                    or settings.get("vars", {}).get("STORAGE_BACKEND") != "d1"
                    or settings.get("vars", {}).get("HISTORY_ARCHIVE_BACKEND") != "r2"
                    or settings.get("d1_databases") != [{
                        "binding": "DB", "database_name": database,
                        "database_id": production_database_id,
                        "migrations_dir": "migrations/d1",
                    }]
                    or settings.get("r2_buckets") != [{
                        "binding": "HISTORY_ARCHIVES", "bucket_name": bucket,
                    }]):
                raise MigrationError("Production Wrangler config does not match restricted target")
            self.database, self.bucket = database, bucket
        else:
            self.database = _disposable_name(database)
            self.bucket = _disposable_name(bucket)
        self.runner = runner
        if not Path(self.wrangler).is_file() or not Path(self.config).is_file():
            raise MigrationError("Wrangler or private config is missing")

    def _run(self, *args, allow_missing=False):
        command = [self.wrangler, *args, "--config", self.config]
        environment = dict(os.environ)
        environment["WRANGLER_SEND_METRICS"] = "false"
        environment["WRANGLER_LOG_PATH"] = str(Path(self.config).parent / "wrangler-remote.log")
        result = self.runner(command, capture_output=True, text=True, env=environment)
        if result.returncode:
            # Never include CLI stderr: SQL, config, or names may contain data.
            if allow_missing and any(marker in result.stderr.lower() for marker in (
                "specified key does not exist", "object not found", "no such key",
            )):
                return None
            raise MigrationError(f"Remote Wrangler command failed: {args[0]} {args[1]}")
        return result.stdout

    def query(self, sql):
        if self.production and (
            not re.match(r"\s*SELECT\b", sql, re.IGNORECASE) or ";" in sql
        ):
            raise MigrationError("Production ad-hoc D1 mutation is forbidden")
        output = self._run(
            "d1", "execute", self.database, "--remote", "--command", sql,
            "--json", "--yes",
        )
        return self._parse_d1_result(output)

    @staticmethod
    def _parse_d1_result(output):
        try:
            envelope = json.loads(output)
            if not isinstance(envelope, list) or not envelope or not all(
                isinstance(item, dict) and item.get("success") is True for item in envelope
            ):
                raise ValueError("D1 query did not succeed")
            return envelope[-1].get("results", [])
        except (json.JSONDecodeError, ValueError, TypeError) as error:
            raise MigrationError("Invalid remote D1 JSON response") from error

    def execute_file(self, path):
        output = self._run(
            "d1", "execute", self.database, "--remote", "--file", str(path),
            "--yes", "--json",
        )
        # Wrangler 4.131.1 can return human-readable text (or no stdout)
        # despite --json for a successful remote --file operation. A nonzero
        # exit is rejected by _run; full export/checksum validation is required
        # before accepting the target.
        if output.lstrip().startswith("["):
            self._parse_d1_result(output)

    def export(self, path):
        path = Path(path).resolve()
        if path.exists():
            raise MigrationError("Remote export output must be a new file")
        self._run("d1", "export", self.database, "--remote", "--output", str(path),
                  "--skip-confirmation")
        if not path.is_file() or path.stat().st_size == 0:
            raise MigrationError("Remote D1 export was not created")
        return path

    def _snapshot_target(self, directory):
        sql_path = Path(directory) / "remote.sql"
        db_path = Path(directory) / "remote.db"
        self.export(sql_path)
        _load_d1_export(sql_path, db_path)
        return db_path

    def preflight(self):
        with tempfile.TemporaryDirectory(prefix="phase14-remote-preflight-") as directory:
            target = self._snapshot_target(directory)
            result = preflight_import_target(target)
            with closing(sqlite3.connect(target)) as connection:
                names = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                expected_names = set(IMPORT_ORDER) | {"runtime_state_cas_guard"}
                if not expected_names <= names:
                    raise MigrationError("Remote D1 schema is incomplete")
                if connection.execute("SELECT COUNT(*) FROM runtime_state_cas_guard").fetchone()[0] != 1:
                    raise MigrationError("Remote D1 CAS guard is invalid")
                seeds = [
                    {"key": row[0], "state_json": row[1], "version": row[2]}
                    for row in connection.execute(
                        "SELECT key, state_json, version FROM runtime_state ORDER BY key"
                    )
                ]
                if seeds != migration_runtime_seed_rows():
                    raise MigrationError("Remote D1 runtime seed is not fresh")
                migrations = [row[0] for row in connection.execute(
                    "SELECT name FROM d1_migrations ORDER BY id"
                )]
                expected_migrations = [path.name for path in sorted(
                    DEFAULT_MIGRATIONS_DIRECTORY.glob("*.sql"))]
                if migrations != expected_migrations:
                    raise MigrationError("Remote D1 migration ledger differs")
            # Query the actual remote target as well; the export replay checks
            # complete schema and indexes, while this catches a changed target.
            for table in IMPORT_ORDER:
                rows = self.query(f'SELECT COUNT(*) AS n FROM "{table}"')
                expected = 2 if table == "runtime_state" else 0
                if len(rows) != 1 or rows[0].get("n") != expected:
                    raise MigrationError("Remote D1 changed during preflight")
            return {"schema": result["schema"], "target": result["target"]}

    def import_plan(self, export_path, plan_directory):
        payload, plan = _verified_plan(export_path, plan_directory)
        self.preflight()
        plan_directory = Path(plan_directory).resolve()
        applied = 0
        try:
            for relative in plan["sql_files"]:
                self.execute_file(plan_directory / relative)
                applied += 1
        except MigrationError as error:
            raise MigrationError(
                "Remote D1 target rejected after import failure; recreate a fresh target"
            ) from error
        return {
            "sql_file_count": applied,
            "batch_count": len(plan["batches"]),
            "table_summaries": payload["table_summaries"],
        }

    def validate(self, export_path):
        payload = read_json(export_path)
        validate_export_payload(payload)
        with tempfile.TemporaryDirectory(prefix="phase14-remote-validate-") as directory:
            target = self._snapshot_target(directory)
            report = assert_valid(validate_sqlite_target(target, payload))
            # Remote row counts are separately checked against the export so an
            # export/replay anomaly cannot silently replace a direct D1 check.
            for table in IMPORT_ORDER:
                rows = self.query(f'SELECT COUNT(*) AS n FROM "{table}"')
                if len(rows) != 1 or rows[0].get("n") != payload["table_summaries"][table]["row_count"]:
                    raise ValidationError("Remote D1 row count mismatch")
        # Existing validation details include runtime/config values; the report
        # returned to callers contains only non-sensitive check names/statuses.
        return {"ok": True, "checks": [
            {"name": item["name"], "status": item["status"]}
            for item in report["checks"]
        ]}

    def disposable_insert_cas_smoke(self, export_path):
        """Mutate only a disposable target; the caller must delete it afterward."""
        if self.production:
            raise MigrationError("Synthetic CAS smoke is forbidden on production")
        payload = read_json(export_path)
        validate_export_payload(payload)
        max_id = payload["table_summaries"]["participants"]["max_id"] or 0
        smoke_id = max_id + 1
        self.query(
            "INSERT INTO participants "
            "(id,name,gender,level,weight,games_played,active,card) VALUES "
            f"({smoke_id},'phase14-smoke','male','beginner',1,0,1,'PHASE14-SMOKE')"
        )
        rows = self.query(f"SELECT COUNT(*) AS n FROM participants WHERE id={smoke_id}")
        if len(rows) != 1 or rows[0].get("n") != 1:
            raise ValidationError("Remote insert smoke failed")
        for table, key, column in (
            ("runtime_state", "current_match", "state_json"),
            ("app_config", "main", "config_json"),
        ):
            before = self.query(f"SELECT version FROM {table} WHERE key='{key}'")
            if len(before) != 1:
                raise ValidationError("Remote CAS smoke row is missing")
            version = before[0]["version"]
            self.query(
                f"UPDATE {table} SET version=version+1 WHERE key='{key}' "
                f"AND version={int(version)}"
            )
            after = self.query(f"SELECT version FROM {table} WHERE key='{key}'")
            if len(after) != 1 or after[0]["version"] != version + 1:
                raise ValidationError("Remote CAS smoke failed")
            self.query(
                f"UPDATE {table} SET version=version+1 WHERE key='{key}' "
                f"AND version={int(version)}"
            )
            stale_after = self.query(f"SELECT version FROM {table} WHERE key='{key}'")
            if len(stale_after) != 1 or stale_after[0]["version"] != version + 1:
                raise ValidationError("Remote stale CAS was not rejected")
        return {"insert": "PASS", "runtime_cas": "PASS", "config_cas": "PASS"}

    def copy_validate_r2(self, archive_plan):
        plan = read_json(archive_plan) if isinstance(archive_plan, (str, Path)) else archive_plan
        validate_archive_plan(plan)
        results = []
        for item in plan["archives"]:
            object_path = f"{self.bucket}/{item['target_key']}"
            with tempfile.TemporaryDirectory(prefix="phase14-r2-object-") as directory:
                downloaded = Path(directory) / "object.bin"
                existing = self._run(
                    "r2", "object", "get", object_path, "--remote", "--file",
                    str(downloaded), allow_missing=True,
                )
                if existing is not None:
                    if (not downloaded.is_file() or
                            downloaded.stat().st_size != item["size"] or
                            sha256_file(downloaded) != item["sha256"]):
                        raise ValidationError("Remote R2 key differs; overwrite forbidden")
                    status = "idempotent_existing"
                else:
                    self._run(
                        "r2", "object", "put", object_path, "--remote", "--file",
                        item["source_path"], "--content-type", "application/json",
                    )
                    status = "uploaded"
                downloaded.unlink(missing_ok=True)
                self._run("r2", "object", "get", object_path, "--remote", "--file",
                          str(downloaded))
                if (not downloaded.is_file() or
                        downloaded.stat().st_size != item["size"] or
                        sha256_file(downloaded) != item["sha256"]):
                    raise ValidationError("Remote R2 download checksum mismatch")
                results.append({"key": item["target_key"], "size": item["size"],
                                "sha256": item["sha256"], "status": status})
        return {"planned_count": plan["object_count"], "verified_count": len(results),
                "objects": results,
                "exact_bucket_key_set_verified": False}
