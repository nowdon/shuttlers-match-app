"""Create, verify, and delete one synthetic disposable remote D1.

Requires an authenticated Wrangler account. Never accepts a source DB path.
"""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from migration.export_sqlite import export_snapshot
from migration.fixture import create_synthetic_fixture
from migration.import_plan import write_import_plan
from migration.manifest import write_json
from migration.remote import RemoteWrangler, _load_d1_export
from migration.snapshot import create_snapshot
from migration.validate import assert_valid, validate_sqlite_target


def _run(wrangler, args, run_dir):
    env = dict(os.environ)
    env["WRANGLER_LOG_PATH"] = str(run_dir / "wrangler.log")
    env["WRANGLER_SEND_METRICS"] = "false"
    result = subprocess.run(
        [str(wrangler), *args], capture_output=True, text=True, env=env,
    )
    if result.returncode:
        raise RuntimeError(f"Wrangler {args[0]} {args[1]} failed; see private log")
    return result.stdout


def run(wrangler, report_directory):
    wrangler = Path(wrangler).resolve()
    report_directory = Path(report_directory).resolve()
    report_directory.mkdir(parents=True, exist_ok=True)
    suffix = secrets.token_hex(4)
    name = f"shuttlers-phase14-disposable-{suffix}"
    report = {"resource": name, "created": False, "migrations": [],
              "import": None, "validation": None, "export": None,
              "insert_cas": None, "deleted": False, "failure": None}
    with tempfile.TemporaryDirectory(prefix="phase14-remote-d1-") as temporary:
        run_dir = Path(temporary)
        fixture = create_synthetic_fixture(run_dir / "fixture")
        snapshot = run_dir / "snapshot.db"
        create_snapshot(fixture["database"], snapshot)
        export = run_dir / "export.json"
        export_payload = export_snapshot(
            snapshot, export, legacy_directory=fixture["source_directory"],
            config_path=fixture["config"],
        )
        plan_dir = run_dir / "import-plan"
        write_import_plan(export, plan_dir)
        config = run_dir / "wrangler.jsonc"
        shutil.copytree(ROOT / "migrations/d1", run_dir / "migrations")
        (run_dir / "worker.js").write_text(
            "export default { fetch() { return new Response('disposable'); } };\n",
            encoding="utf-8",
        )
        try:
            created = _run(wrangler, ["d1", "create", name, "--location=apac"], run_dir)
            report["created"] = True
            match = re.search(
                r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                created, re.IGNORECASE,
            )
            if not match:
                raise RuntimeError("D1 create succeeded but UUID was not returned")
            config.write_text(json.dumps({
                "name": name,
                "main": "worker.js",
                "compatibility_date": "2026-10-01",
                "d1_databases": [{
                    "binding": "DB", "database_name": name,
                    "database_id": match.group(0), "migrations_dir": "migrations",
                }],
            }, indent=2) + "\n", encoding="utf-8")
            _run(wrangler, ["d1", "migrations", "apply", "DB", "--remote",
                            "--config", str(config)], run_dir)
            report["migrations"] = [path.name for path in sorted(
                (ROOT / "migrations/d1").glob("*.sql"))]
            remote = RemoteWrangler(wrangler, config, name, name)
            report["import"] = remote.import_plan(export, plan_dir)
            report["validation"] = remote.validate(export)
            exported_sql = run_dir / "remote-export.sql"
            remote.export(exported_sql)
            replay = run_dir / "remote-export.db"
            _load_d1_export(exported_sql, replay)
            assert_valid(validate_sqlite_target(replay, export_payload))
            report["export"] = {"replay": "PASS", "synthetic_only": True}
            report["insert_cas"] = remote.disposable_insert_cas_smoke(export)
        except Exception as error:
            report["failure"] = type(error).__name__
            raise
        finally:
            # Preserve validation evidence before removing the disposable target.
            report_path = report_directory / f"{name}-report.json"
            write_json(report_path, report)
            if report["created"]:
                try:
                    _run(wrangler, ["d1", "delete", name, "--skip-confirmation"], run_dir)
                    listed = json.loads(_run(wrangler, ["d1", "list", "--json"], run_dir))
                    report["deleted"] = not any(item.get("name") == name for item in listed)
                except Exception:
                    report["deleted"] = False
            write_json(report_path, report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wrangler", type=Path)
    parser.add_argument("--report-directory", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.wrangler, args.report_directory)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    if not report["deleted"]:
        raise SystemExit("Disposable D1 deletion was not verified")


if __name__ == "__main__":
    main()
