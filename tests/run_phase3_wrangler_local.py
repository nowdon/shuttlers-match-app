"""Exercise the Phase 3 SQL against a disposable Wrangler local D1 database."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/d1/0001_phase3_participants_config.sql"


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_phase3_wrangler_local.py /path/to/wrangler")
    wrangler = str(Path(sys.argv[1]).resolve())

    sys.path.insert(0, str(ROOT))
    from data.participants import _record
    from storage.d1 import D1Storage
    from storage.errors import StorageUniqueError, normalize_storage_error

    with tempfile.TemporaryDirectory(prefix="shuttlers-phase3-d1-") as directory:
        work = Path(directory)
        migrations = work / "migrations"
        migrations.mkdir()
        shutil.copy2(MIGRATION, migrations / MIGRATION.name)
        (work / "worker.js").write_text(
            """export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    try {
      if (path === "/participant") {
        const result = await env.DB.prepare(
          "INSERT INTO participants (name, gender, level, weight, games_played, active, card) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id, name, gender, level, weight, games_played, active, card"
        ).bind("binding", "male", "beginner", 1.0, 0, 1, "C3").run();
        return Response.json(result);
      }
      if (path === "/duplicate") {
        await env.DB.prepare(
          "INSERT INTO participants (name, gender, level, weight, games_played, active, card) VALUES (?, ?, ?, ?, ?, ?, ?)"
        ).bind("duplicate", "male", "beginner", 1.0, 0, 1, "C1").run();
      }
      if (path === "/cas") {
        const result = await env.DB.prepare(
          "UPDATE app_config SET config_json = ?, version = version + 1 WHERE key = ? AND version = ?"
        ).bind('{"label":"binding","level_map":{"beginner":3}}', "main", 8).run();
        return Response.json(result);
      }
      return new Response("not found", { status: 404 });
    } catch (error) {
      return Response.json({ error: String(error) }, { status: 409 });
    }
  }
};
""",
            encoding="utf-8",
        )
        config = {
            "$schema": str(
                ROOT
                / "cloudflare-d1-binding-poc/node_modules/wrangler/config-schema.json"
            ),
            "name": "shuttlers-phase3-storage-local-only",
            "main": "worker.js",
            "compatibility_date": "2026-09-13",
            "d1_databases": [{
                "binding": "DB",
                "database_name": "shuttlers-phase3-local-only",
                "database_id": "00000000-0000-0000-0000-000000000003",
                "migrations_dir": "migrations",
            }],
        }
        config_path = work / "wrangler.jsonc"
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        state_path = work / "state"
        environment = dict(
            os.environ,
            CI="1",
            NO_COLOR="1",
            WRANGLER_LOG_PATH=str(work / "wrangler.log"),
        )

        common = [
            wrangler,
            "d1",
            "execute",
            "DB",
            "--local",
            "--persist-to",
            str(state_path),
            "--config",
            str(config_path),
            "--json",
        ]

        def execute(sql, *, succeeds=True):
            assert "--remote" not in common and "--preview" not in common
            result = subprocess.run(
                [*common, "--command", sql],
                cwd=work,
                env=environment,
                capture_output=True,
                text=True,
            )
            if succeeds:
                if result.returncode != 0:
                    raise AssertionError(result.stdout + result.stderr)
                payload = json.loads(result.stdout)
                assert isinstance(payload, list) and len(payload) == 1
                return payload[0]
            assert result.returncode != 0
            return result

        migration = subprocess.run(
            [
                wrangler,
                "d1",
                "migrations",
                "apply",
                "DB",
                "--local",
                "--persist-to",
                str(state_path),
                "--config",
                str(config_path),
            ],
            cwd=work,
            env=environment,
            capture_output=True,
            text=True,
        )
        if migration.returncode != 0:
            raise AssertionError(migration.stdout + migration.stderr)

        insert = execute(
            "INSERT INTO participants "
            "(name, gender, level, weight, games_played, active, card) "
            "VALUES ('alpha', 'male', 'beginner', 1.0, 0, 1, 'C2') "
            "RETURNING id, name, gender, level, weight, games_played, active, card"
        )
        normalized_insert = D1Storage._result(insert)
        # Wrangler CLI JSON intentionally exposes only a subset of binding meta.
        assert normalized_insert.changes is None
        assert len(normalized_insert.rows) == 1
        assert _record(normalized_insert.rows[0]).active is True

        execute(
            "INSERT INTO participants "
            "(name, gender, level, weight, games_played, active, card) "
            "VALUES ('beta', 'female', 'advanced', 2.7, 3, 0, 'C1')"
        )
        by_card = D1Storage._result(execute(
            "SELECT id, name, gender, level, weight, games_played, active, card "
            "FROM participants WHERE card = 'C1'"
        ))
        inactive = _record(by_card.rows[0])
        assert inactive.card == "C1" and inactive.active is False
        assert inactive.games_played == 3 and inactive.weight == 2.7

        ordered = D1Storage._result(execute(
            "SELECT id, name, gender, level, weight, games_played, active, card "
            "FROM participants ORDER BY card"
        ))
        assert [row["card"] for row in ordered.rows] == ["C1", "C2"]

        updated = D1Storage._result(execute(
            "UPDATE participants SET name = 'alpha-updated', gender = 'female', "
            "level = 'advanced', active = 0 WHERE card = 'C2' "
            "RETURNING id, name, gender, level, weight, games_played, active, card"
        ))
        assert updated.changes is None and len(updated.rows) == 1
        updated_record = _record(updated.rows[0])
        assert updated_record.name == "alpha-updated"
        assert updated_record.active is False
        assert updated_record.weight == 1.0

        duplicate = execute(
            "INSERT INTO participants "
            "(name, gender, level, weight, games_played, active, card) "
            "VALUES ('duplicate', 'male', 'beginner', 1.0, 0, 1, 'C1')",
            succeeds=False,
        )
        normalized_error = normalize_storage_error(
            RuntimeError(duplicate.stdout + duplicate.stderr)
        )
        assert isinstance(normalized_error, StorageUniqueError)
        assert "participants.card" not in str(normalized_error)

        first_config = {"level_map": {"beginner": 1}, "label": "ローカル"}
        first_json = json.dumps(
            first_config, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        execute(
            "INSERT INTO app_config (key, config_json, version) VALUES ("
            f"'main', {sql_literal(first_json)}, 7)"
        )
        loaded = D1Storage._result(execute(
            "SELECT config_json, version FROM app_config WHERE key = 'main'"
        )).rows[0]
        assert json.loads(loaded["config_json"]) == first_config
        assert loaded["version"] == 7

        second_config = {"level_map": {"beginner": 2}, "label": "更新済み"}
        second_json = json.dumps(
            second_config, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        saved = D1Storage._result(execute(
            "UPDATE app_config SET "
            f"config_json = {sql_literal(second_json)}, version = version + 1 "
            "WHERE key = 'main' AND version = 7 RETURNING version"
        ))
        assert saved.rows == [{"version": 8}]

        stale = D1Storage._result(execute(
            "UPDATE app_config SET "
            f"config_json = {sql_literal(first_json)}, version = version + 1 "
            "WHERE key = 'main' AND version = 7 RETURNING version"
        ))
        assert stale.rows == []
        final_config = D1Storage._result(execute(
            "SELECT config_json, version FROM app_config WHERE key = 'main'"
        )).rows[0]
        assert json.loads(final_config["config_json"]) == second_config
        assert final_config["version"] == 8

        server = subprocess.Popen(
            [
                wrangler,
                "dev",
                "--local",
                "--persist-to",
                str(state_path),
                "--config",
                str(config_path),
                "--port",
                "18787",
            ],
            cwd=work,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        def request_json(path, *, expected_status=200):
            url = "http://127.0.0.1:18787" + path
            last_error = None
            for _attempt in range(100):
                try:
                    with urlopen(url, timeout=2) as response:
                        assert response.status == expected_status
                        return json.loads(response.read())
                except HTTPError as error:
                    if error.code == expected_status:
                        return json.loads(error.read())
                    last_error = error
                except OSError as error:
                    last_error = error
                    if server.poll() is not None:
                        output = server.stdout.read() if server.stdout else ""
                        raise AssertionError(output) from error
                    time.sleep(0.1)
            raise AssertionError(f"Wrangler local server did not become ready: {last_error}")

        try:
            binding_insert = D1Storage._result(request_json("/participant"))
            assert binding_insert.changes == 1
            assert _record(binding_insert.rows[0]).active is True

            binding_cas = D1Storage._result(request_json("/cas"))
            assert binding_cas.changes == 1
            binding_stale = D1Storage._result(request_json("/cas"))
            assert binding_stale.changes == 0

            binding_duplicate = request_json("/duplicate", expected_status=409)
            binding_error = normalize_storage_error(
                RuntimeError(binding_duplicate["error"])
            )
            assert isinstance(binding_error, StorageUniqueError)
            assert "participants.card" not in str(binding_error)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)

        final_binding_config = D1Storage._result(execute(
            "SELECT config_json, version FROM app_config WHERE key = 'main'"
        )).rows[0]
        assert json.loads(final_binding_config["config_json"]) == {
            "label": "binding",
            "level_map": {"beginner": 3},
        }
        assert final_binding_config["version"] == 9

        print(json.dumps({
            "backend": "wrangler-local-d1",
            "migration": MIGRATION.name,
            "participants": {
                "count": 3,
                "ordered_cards": [row["card"] for row in ordered.rows] + ["C3"],
                "unique_error": binding_error.category,
                "active_types": [
                    type(inactive.active).__name__,
                    type(updated_record.active).__name__,
                ],
            },
            "app_config": {
                "seed_version": 7,
                "saved_version": final_binding_config["version"],
                "stale_changes": binding_stale.changes,
                "unicode_round_trip": final_config["config_json"] == second_json,
            },
            "state_directory": "temporary-and-removed",
        }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
