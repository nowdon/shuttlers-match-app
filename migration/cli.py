"""Command line interface for Phase 10 migration rehearsal tooling."""

import argparse
import json
from pathlib import Path
import sys

from .archives import build_archive_plan, validate_archive_plan
from .errors import MigrationError, ValidationError
from .export_sqlite import export_snapshot
from .import_plan import (
    apply_import_plan,
    build_import_plan,
    initialize_sqlite_target,
    preflight_import_target,
)
from .manifest import read_json, write_json
from .rehearsal import run_rehearsal
from .snapshot import create_snapshot
from .validate import assert_valid, post_import_smoke, validate_sqlite_target


def _json_print(value):
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(
        description="Safe, local-only Phase 10 SQLite/D1/R2 migration rehearsal tooling"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot", help="Create a SQLite backup-API snapshot")
    snapshot.add_argument("--source", required=True)
    snapshot.add_argument("--output", required=True)
    snapshot.add_argument("--manifest")

    export = subparsers.add_parser("export", help="Export an application-aware snapshot")
    export.add_argument("--snapshot", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--legacy-directory")
    export.add_argument("--config")
    export.add_argument("--dry-run", action="store_true")

    plan_r2 = subparsers.add_parser("plan-r2", help="Build a safe history archive upload plan")
    plan_r2.add_argument("--archives", required=True)
    plan_r2.add_argument("--output", required=True)
    plan_r2.add_argument("--dry-run", action="store_true")

    import_command = subparsers.add_parser("import", help="Apply a plan to a disposable SQLite D1")
    import_command.add_argument("--export", required=True)
    import_command.add_argument("--plan-directory", required=True)
    import_command.add_argument("--target", required=True)
    import_command.add_argument("--migrations-directory")
    import_command.add_argument("--archive-plan")
    import_command.add_argument("--dry-run", action="store_true")

    validate = subparsers.add_parser("validate", help="Validate an imported SQLite D1")
    validate.add_argument("--export", required=True)
    validate.add_argument("--target", required=True)
    validate.add_argument("--report")
    validate.add_argument("--post-import-smoke", action="store_true")

    rehearsal = subparsers.add_parser("rehearsal", help="Run synthetic local D1 + R2 rehearsal")
    rehearsal.add_argument("--wrangler", required=True)
    rehearsal.add_argument("--workdir")

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "snapshot":
            result = create_snapshot(args.source, args.output, manifest_path=args.manifest)
        elif args.command == "export":
            result = export_snapshot(
                args.snapshot,
                args.output,
                legacy_directory=args.legacy_directory,
                config_path=args.config,
                dry_run=args.dry_run,
            )
        elif args.command == "plan-r2":
            result = build_archive_plan(args.archives, args.output, dry_run=args.dry_run)
        elif args.command == "import":
            if args.dry_run:
                if not args.archive_plan:
                    raise MigrationError("import --dry-run requires --archive-plan")
                plan = build_import_plan(read_json(args.export))
                target_report = preflight_import_target(
                    args.target,
                    args.migrations_directory,
                )
                archive_plan = read_json(args.archive_plan)
                validate_archive_plan(archive_plan)
                result = {
                    "dry_run": True,
                    "writes": 0,
                    "target": target_report,
                    "import_plan": {
                        "batch_size": plan["batch_size"],
                        "batch_count": len(plan["batches"]),
                        "sql_files": plan["sql_files"],
                        "table_summaries": plan["table_summaries"],
                    },
                    "archive_plan": {
                        "object_count": archive_plan["object_count"],
                        "ignored_files": archive_plan["ignored_files"],
                    },
                }
            else:
                plan_directory = Path(args.plan_directory)
                plan = read_json(plan_directory / "import_plan.json")
                payload = read_json(args.export)
                batch_size = plan.get("batch_size")
                if not isinstance(batch_size, int) or batch_size < 1:
                    raise MigrationError("Import plan batch_size is invalid")
                expected_plan = build_import_plan(payload, batch_size=batch_size)
                if plan != expected_plan:
                    raise MigrationError("Import plan does not match export artifact")
                if args.migrations_directory:
                    initialize_sqlite_target(args.target, args.migrations_directory)
                result = apply_import_plan(
                    args.target,
                    plan,
                    migrations_directory=args.migrations_directory,
                )
        elif args.command == "validate":
            payload = read_json(args.export)
            result = validate_sqlite_target(args.target, payload)
            if args.post_import_smoke:
                result["post_import_smoke"] = post_import_smoke(args.target)
            if args.report:
                write_json(args.report, result)
            assert_valid(result)
        else:
            result = run_rehearsal(args.wrangler, args.workdir)
        _json_print(result)
        return 0
    except (MigrationError, ValidationError, OSError, ValueError) as error:
        print(f"migration: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
