"""Synthetic end-to-end Phase 10 rehearsal against local Wrangler resources."""

from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile

from .archives import (
    build_archive_plan,
    upload_archives_with_wrangler,
)
from .errors import MigrationError
from .export_sqlite import export_snapshot
from .fixture import create_synthetic_fixture
from .import_plan import write_import_plan
from .manifest import (
    new_migration_id,
    sha256_file,
    utc_now_text,
    write_json,
)
from .snapshot import create_snapshot
from .validate import assert_valid, post_import_smoke, validate_sqlite_target


ROOT = Path(__file__).resolve().parents[1]


def _run(wrangler, args, *, cwd, environment):
    result = subprocess.run(
        [str(wrangler), *args], cwd=cwd, env=environment,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        detail = (result.stdout + "\n" + result.stderr).strip()
        raise MigrationError(
            f"Wrangler local command failed ({result.returncode}): {detail}"
        )
    return result.stdout


def _find_d1_sqlite(persist_to):
    for path in Path(persist_to).rglob("*"):
        if not path.is_file():
            continue
        try:
            with path.open("rb") as candidate:
                header = candidate.read(16)
            if header != b"SQLite format 3\x00":
                continue
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("SELECT COUNT(*) FROM participants").fetchone()
            return path
        except (OSError, sqlite3.Error):
            continue
    raise MigrationError("Could not locate Wrangler local D1 SQLite state")


def _safety_snapshot(fixture):
    archive_items = []
    for path in sorted(fixture["archives"].iterdir(), key=lambda item: item.name):
        if path.is_file():
            archive_items.append((path.name, path.stat().st_size, sha256_file(path)))
    return {
        "database_sha256": sha256_file(fixture["database"]),
        "config_sha256": sha256_file(fixture["config"]),
        "match_state_sha256": sha256_file(fixture["match_state"]),
        "draft_state_sha256": sha256_file(fixture["draft_state"]),
        "archives": archive_items,
    }


def _write_wrangler_config(directory):
    migrations = directory / "migrations"
    shutil.copytree(ROOT / "migrations/d1", migrations)
    (directory / "worker.js").write_text(
        "export default { fetch() { return new Response('phase10-local'); } };\n",
        encoding="utf-8",
    )
    config = {
        "name": "phase10-local-rehearsal",
        "main": "worker.js",
        "compatibility_date": "2026-09-19",
        "workers_dev": False,
        "preview_urls": False,
        "d1_databases": [{
            "binding": "DB",
            "database_name": "phase10-local-d1",
            "database_id": "00000000-0000-0000-0000-000000000010",
            "migrations_dir": "migrations",
        }],
        "r2_buckets": [{
            "binding": "HISTORY_ARCHIVES",
            "bucket_name": "phase10-local-r2",
        }],
    }
    path = directory / "wrangler.jsonc"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path, config


def run_rehearsal(wrangler, workdir=None):
    """Run every Phase 10 step using synthetic data and local-only resources."""
    wrangler = Path(wrangler).resolve()
    if not wrangler.is_file():
        raise MigrationError(f"Wrangler binary does not exist: {wrangler}")
    migration_id = new_migration_id()
    owned_temp = workdir is None
    if owned_temp:
        workdir = Path(tempfile.mkdtemp(prefix="phase10-rehearsal-"))
    else:
        workdir = Path(workdir).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
    run_directory = workdir / migration_id
    run_directory.mkdir(parents=True, exist_ok=False)
    fixture = create_synthetic_fixture(run_directory / "fixture")
    before = _safety_snapshot(fixture)

    snapshot_path = run_directory / "snapshot.db"
    snapshot_manifest = run_directory / "snapshot-manifest.json"
    snapshot_metadata = create_snapshot(
        fixture["database"], snapshot_path, manifest_path=snapshot_manifest
    )
    export_path = run_directory / "export.json"
    export_payload = export_snapshot(
        snapshot_path,
        export_path,
        legacy_directory=fixture["source_directory"],
        config_path=fixture["config"],
    )
    plan_directory = run_directory / "import-plan"
    plan = write_import_plan(export_path, plan_directory, batch_size=100)
    archive_plan_path = run_directory / "archive-plan.json"
    archive_plan = build_archive_plan(fixture["archives"], archive_plan_path)

    wrangler_root = run_directory / "wrangler"
    wrangler_root.mkdir()
    wrangler_config, wrangler_values = _write_wrangler_config(wrangler_root)
    persist_to = run_directory / "local-cloudflare-state"
    environment = dict(os.environ)
    environment.update({
        "CI": "1",
        "NO_COLOR": "1",
        "WRANGLER_LOG_PATH": str(run_directory / "wrangler.log"),
        "WRANGLER_SEND_METRICS": "false",
        "LINE_MESSAGING_ENABLED": "false",
    })
    base_args = ["--config", str(wrangler_config), "--persist-to", str(persist_to)]
    _run(
        wrangler,
        ["d1", "migrations", "apply", "phase10-local-d1", "--local", *base_args],
        cwd=wrangler_root,
        environment=environment,
    )
    for relative_sql in plan["sql_files"]:
        _run(
            wrangler,
            ["d1", "execute", "phase10-local-d1", "--local", *base_args,
             "--file", str(plan_directory / relative_sql), "--yes"],
            cwd=wrangler_root,
            environment=environment,
        )
    local_d1 = _find_d1_sqlite(persist_to)
    d1_report = validate_sqlite_target(local_d1, export_payload)
    assert_valid(d1_report)
    smoke_report = post_import_smoke(local_d1, participant_id_max=4)
    assert_valid(smoke_report)

    r2_upload = upload_archives_with_wrangler(
        archive_plan,
        wrangler=wrangler,
        bucket=wrangler_values["r2_buckets"][0]["bucket_name"],
        persist_to=persist_to,
        config=wrangler_config,
    )
    r2_results = []
    for index, item in enumerate(archive_plan["archives"]):
        downloaded = run_directory / f"r2-download-{index}.bin"
        _run(
            wrangler,
            ["r2", "object", "get",
             f"{wrangler_values['r2_buckets'][0]['bucket_name']}/{item['target_key']}",
             "--local", *base_args, "--file", str(downloaded)],
            cwd=wrangler_root,
            environment=environment,
        )
        r2_results.append({
            "key": item["target_key"],
            "size": downloaded.stat().st_size,
            "sha256": sha256_file(downloaded),
            "matches_source": downloaded.stat().st_size == item["size"]
            and sha256_file(downloaded) == item["sha256"],
        })
        downloaded.unlink(missing_ok=True)
    if not all(item["matches_source"] for item in r2_results):
        raise MigrationError("Local R2 validation failed")

    after = _safety_snapshot(fixture)
    safety = {
        "before": before,
        "after": after,
        "unchanged": before == after,
    }
    if not safety["unchanged"]:
        raise MigrationError("Synthetic source changed during rehearsal")
    report = {
        "migration_id": migration_id,
        "created_at": utc_now_text(),
        "source": snapshot_metadata,
        "tables": export_payload["table_summaries"],
        "runtime_state": export_payload["runtime_state"],
        "app_config": export_payload["app_config"],
        "archives": {
            "object_count": archive_plan["object_count"],
            "ignored_files": archive_plan["ignored_files"],
            "upload": r2_upload,
            "validation": r2_results,
        },
        "validation": {
            "d1": d1_report,
            "post_import_smoke": smoke_report,
        },
        "safety": safety,
        "artifacts": {"workdir": str(run_directory)},
        "production_resources_used": False,
        "line_messaging_enabled": False,
        "email_sent": False,
    }
    write_json(run_directory / "rehearsal-manifest.json", report)
    # The caller can retain an explicit workdir.  Temporary workdirs are still
    # returned for diagnostics; cleanup is the caller's responsibility.
    return report
