"""Exercise Phase 8 full-reset atomicity on disposable Wrangler local D1."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = [ROOT / "migrations/d1" / name for name in (
    "0001_phase3_participants_config.sql",
    "0002_phase4_match_relational.sql",
    "0003_phase5_runtime_state.sql",
    "0004_phase6_line_notifications.sql",
)]

WORKER = r'''const guard = (env, key, version) => env.DB.prepare(
  "INSERT INTO runtime_state_cas_guard (id) SELECT 1 WHERE NOT EXISTS " +
  "(SELECT 1 FROM runtime_state WHERE key=? AND version=?)"
).bind(key, version);

const resetStatements = (env, matchVersion, draftVersion, fail) => {
  const empty = JSON.stringify({
    bench: [], match_active: false, match_count: 0, matches: [],
    session_id: null, timestamp: "2026-09-17T12:34:56+09:00"
  });
  const statements = [
    guard(env, "current_match", matchVersion),
    guard(env, "current_draft", draftVersion),
    env.DB.prepare("DELETE FROM notification_delivery_logs"),
    env.DB.prepare("DELETE FROM match_notifications"),
    env.DB.prepare("DELETE FROM notification_subscriptions"),
    env.DB.prepare("DELETE FROM line_link_tokens"),
    env.DB.prepare("DELETE FROM line_accounts"),
    env.DB.prepare("DELETE FROM bench_histories"),
    env.DB.prepare("DELETE FROM match_histories"),
    env.DB.prepare("DELETE FROM match_rounds"),
    env.DB.prepare("DELETE FROM match_sessions"),
    env.DB.prepare("DELETE FROM participants"),
    env.DB.prepare(
      "UPDATE runtime_state SET state_json=?,version=version+1 " +
      "WHERE key='current_match' AND version=?"
    ).bind(empty, matchVersion),
    env.DB.prepare(
      "UPDATE runtime_state SET state_json=NULL,version=version+1 " +
      "WHERE key='current_draft' AND version=?"
    ).bind(draftVersion),
  ];
  if (fail) {
    statements.splice(8, 0, env.DB.prepare(
      "INSERT INTO runtime_state_cas_guard (id) VALUES (1)"
    ));
  }
  return statements;
};

export default { async fetch(request, env) {
  const url = new URL(request.url);
  if (url.pathname !== "/reset" && url.pathname !== "/reset-fail") {
    return new Response("not found", {status: 404});
  }
  try {
    await env.DB.batch(resetStatements(
      env,
      Number(url.searchParams.get("match")),
      Number(url.searchParams.get("draft")),
      url.pathname === "/reset-fail",
    ));
    return Response.json({ok: true});
  } catch (error) {
    return Response.json({error: String(error)}, {status: 409});
  }
} };'''


SEED_SQL = """
INSERT INTO participants (id,name,gender,level,weight,games_played,active,card) VALUES
  (1,'p1','male','beginner',1,1,1,'C1'),
  (2,'p2','male','beginner',1,1,1,'C2'),
  (3,'p3','male','beginner',1,1,1,'C3'),
  (4,'p4','male','beginner',1,1,1,'C4');
INSERT INTO app_config (key,config_json,version)
  VALUES ('main','{"preserved":true}',9);
INSERT INTO match_sessions (id,status,match_count,created_at,creation_token)
  VALUES (1,'confirmed',1,'2026-09-17T01:02:03.000000Z','creation-token');
INSERT INTO match_rounds (id,session_id,round_number,created_at)
  VALUES (1,1,1,'2026-09-17T01:02:03.000000Z');
INSERT INTO match_histories
  (id,round_id,court_number,team1_player1_id,team1_player2_id,
   team2_player1_id,team2_player2_id,created_at)
  VALUES (1,1,1,1,2,3,4,'2026-09-17T01:02:03.000000Z');
INSERT INTO bench_histories (id,round_id,participant_id,created_at)
  VALUES (1,1,1,'2026-09-17T01:02:03.000000Z');
INSERT INTO line_accounts
  (id,participant_id,line_user_id,active,created_at,updated_at)
  VALUES (1,1,'U1',1,'2026-09-17T01:02:03.000000Z','2026-09-17T01:02:03.000000Z');
INSERT INTO notification_subscriptions
  (id,session_id,participant_id,channel,active,created_at,updated_at)
  VALUES (1,1,1,'line',1,'2026-09-17T01:02:03.000000Z','2026-09-17T01:02:03.000000Z');
INSERT INTO line_link_tokens
  (id,token,participant_id,session_id,expires_at,created_at)
  VALUES (1,'TOKEN',1,1,'2027-09-17T01:02:03.000000Z','2026-09-17T01:02:03.000000Z');
INSERT INTO match_notifications
  (id,session_id,match_count,channel,status,created_at)
  VALUES (1,1,1,'line','completed','2026-09-17T01:02:03.000000Z');
INSERT INTO notification_delivery_logs
  (id,session_id,participant_id,match_count,channel,status,sent_at)
  VALUES (1,1,1,1,'line','success','2026-09-17T01:02:03.000000Z');
UPDATE runtime_state SET
  state_json='{"bench":[],"match_active":true,"match_count":1,"matches":[[1,2,3,4]],"session_id":1}',
  version=7 WHERE key='current_match';
UPDATE runtime_state SET
  state_json='{"bench":[],"draft":true,"matches":[[1,2,3,4]]}',
  version=11 WHERE key='current_draft';
"""


COUNT_SQL = "SELECT " + ",".join(
    f"(SELECT COUNT(*) FROM {table}) {table}"
    for table in (
        "participants", "match_sessions", "match_rounds", "match_histories",
        "bench_histories", "line_accounts", "notification_subscriptions",
        "line_link_tokens", "match_notifications", "notification_delivery_logs",
    )
) + ", (SELECT COUNT(*) FROM runtime_state) runtime_rows"


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_phase8_wrangler_local.py /path/to/wrangler")
    wrangler = str(Path(sys.argv[1]).resolve())
    version = subprocess.run(
        [wrangler, "--version"], check=True, capture_output=True, text=True
    ).stdout
    assert "4.131.1" in version, version

    with tempfile.TemporaryDirectory(prefix="shuttlers-phase8-d1-") as directory:
        work = Path(directory)
        (work / "migrations").mkdir()
        for migration in MIGRATIONS:
            shutil.copy2(migration, work / "migrations" / migration.name)
        (work / "worker.js").write_text(WORKER, encoding="utf-8")
        config = {
            "name": "shuttlers-phase8-local-only",
            "main": "worker.js",
            "compatibility_date": "2026-09-17",
            "d1_databases": [{
                "binding": "DB",
                "database_name": "phase8-local",
                "database_id": "00000000-0000-0000-0000-000000000008",
                "migrations_dir": "migrations",
            }],
        }
        config_path = work / "wrangler.jsonc"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        state = work / "state"
        env = dict(os.environ, CI="1", NO_COLOR="1")
        base = [
            wrangler, "d1", "execute", "DB", "--local", "--persist-to", str(state),
            "--config", str(config_path), "--json",
        ]
        migration = subprocess.run(
            [wrangler, "d1", "migrations", "apply", "DB", "--local",
             "--persist-to", str(state), "--config", str(config_path)],
            cwd=work, env=env, capture_output=True, text=True,
        )
        if migration.returncode:
            raise AssertionError(migration.stdout + migration.stderr)
        subprocess.run(
            base + ["--command", SEED_SQL], cwd=work, env=env, check=True,
            capture_output=True, text=True,
        )

        def query(sql):
            result = subprocess.run(
                base + ["--command", sql], cwd=work, env=env, check=True,
                capture_output=True, text=True,
            )
            return json.loads(result.stdout)[0]["results"]

        def assert_seed_survives():
            counts = query(COUNT_SQL)[0]
            assert counts["runtime_rows"] == 2, counts
            assert counts["participants"] == 4, counts
            assert all(
                counts[table] == 1
                for table in counts
                if table not in ("participants", "runtime_rows")
            ), counts
            runtime = query(
                "SELECT key,state_json,version FROM runtime_state ORDER BY key"
            )
            assert runtime[0]["key"] == "current_draft" and runtime[0]["version"] == 11
            assert runtime[1]["key"] == "current_match" and runtime[1]["version"] == 7

        server = subprocess.Popen(
            [wrangler, "dev", "--local", "--persist-to", str(state),
             "--config", str(config_path), "--port", "18792"],
            cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            def post(path):
                try:
                    with urlopen(Request(
                        "http://127.0.0.1:18792" + path, data=b"", method="POST"
                    ), timeout=4) as response:
                        return response.status, response.read().decode()
                except HTTPError as error:
                    return error.code, error.read().decode()
                except (URLError, OSError) as error:
                    return 0, str(error)

            for _ in range(100):
                if post("/missing")[0] == 404:
                    break
                time.sleep(0.1)
            else:
                output = "".join(server.stdout.readlines()) if server.poll() is not None else ""
                raise AssertionError("wrangler dev did not start\n" + output)

            assert query("PRAGMA foreign_keys")[0]["foreign_keys"] == 1
            assert post("/reset-fail?match=7&draft=11")[0] == 409
            assert_seed_survives()
            assert post("/reset?match=6&draft=11")[0] == 409
            assert_seed_survives()
            assert post("/reset?match=7&draft=11")[0] == 200

            counts = query(COUNT_SQL)[0]
            assert counts == {
                "participants": 0, "match_sessions": 0, "match_rounds": 0,
                "match_histories": 0, "bench_histories": 0, "line_accounts": 0,
                "notification_subscriptions": 0, "line_link_tokens": 0,
                "match_notifications": 0, "notification_delivery_logs": 0,
                "runtime_rows": 2,
            }, counts
            runtime = query(
                "SELECT key,state_json,version FROM runtime_state ORDER BY key"
            )
            assert runtime[0] == {
                "key": "current_draft", "state_json": None, "version": 12,
            }, runtime
            match = runtime[1]
            assert match["key"] == "current_match" and match["version"] == 8, runtime
            assert json.loads(match["state_json"]) == {
                "bench": [], "match_active": False, "match_count": 0,
                "matches": [], "session_id": None,
                "timestamp": "2026-09-17T12:34:56+09:00",
            }
            assert query(
                "SELECT key,config_json,version FROM app_config WHERE key='main'"
            )[0] == {
                "key": "main", "config_json": '{"preserved":true}', "version": 9,
            }
        finally:
            server.terminate()
            server.wait(timeout=10)

    print("Phase 8 Wrangler local D1 checks passed")


if __name__ == "__main__":
    main()
