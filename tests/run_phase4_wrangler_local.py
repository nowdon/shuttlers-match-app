"""Exercise Phase 4 migrations and concurrent confirm on disposable local D1."""

from concurrent.futures import ThreadPoolExecutor
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
MIGRATIONS = [
    ROOT / "migrations/d1/0001_phase3_participants_config.sql",
    ROOT / "migrations/d1/0002_phase4_match_relational.sql",
]


WORKER = r"""
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (url.pathname === "/confirm") {
        const timestamp = "2026-01-02T03:04:05.000000Z";
        await env.DB.batch([
          env.DB.prepare("INSERT INTO match_rounds (session_id, round_number, created_at) VALUES (?, ?, ?)").bind(1, 1, timestamp),
          env.DB.prepare("INSERT INTO match_histories (round_id, court_number, team1_player1_id, team1_player2_id, team2_player1_id, team2_player2_id, created_at) SELECT id, 1, 1, 2, 3, 4, ? FROM match_rounds WHERE session_id = 1 AND round_number = 1").bind(timestamp),
          env.DB.prepare("UPDATE participants SET games_played = games_played + 1 WHERE id IN (1, 2, 3, 4)"),
          env.DB.prepare("UPDATE match_sessions SET status = 'confirmed', match_count = 1, confirmed_at = ? WHERE id = 1").bind(timestamp)
        ]);
        return Response.json({ ok: true });
      }
      if (url.pathname === "/revert") {
        await env.DB.batch([
          env.DB.prepare("UPDATE participants SET games_played = MAX(games_played - 1, 0) WHERE id IN (1, 2, 3, 4)"),
          env.DB.prepare("DELETE FROM bench_histories WHERE round_id = (SELECT id FROM match_rounds WHERE session_id = 1 AND round_number = 1)"),
          env.DB.prepare("DELETE FROM match_histories WHERE round_id = (SELECT id FROM match_rounds WHERE session_id = 1 AND round_number = 1)"),
          env.DB.prepare("DELETE FROM match_rounds WHERE session_id = 1 AND round_number = 1")
        ]);
        return Response.json({ ok: true });
      }
      return new Response("not found", { status: 404 });
    } catch (error) {
      return Response.json({ error: String(error) }, { status: 409 });
    }
  }
};
"""


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_phase4_wrangler_local.py /path/to/wrangler")
    wrangler = str(Path(sys.argv[1]).resolve())

    with tempfile.TemporaryDirectory(prefix="shuttlers-phase4-d1-") as directory:
        work = Path(directory)
        migrations = work / "migrations"
        migrations.mkdir()
        for migration in MIGRATIONS:
            shutil.copy2(migration, migrations / migration.name)
        (work / "worker.js").write_text(WORKER, encoding="utf-8")
        config = {
            "name": "shuttlers-phase4-storage-local-only",
            "main": "worker.js",
            "compatibility_date": "2026-09-13",
            "d1_databases": [{
                "binding": "DB",
                "database_name": "shuttlers-phase4-local-only",
                "database_id": "00000000-0000-0000-0000-000000000004",
                "migrations_dir": "migrations",
            }],
        }
        config_path = work / "wrangler.jsonc"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        state_path = work / "state"
        environment = dict(os.environ, CI="1", NO_COLOR="1", WRANGLER_LOG_PATH=str(work / "wrangler.log"))
        common = [wrangler, "d1", "execute", "DB", "--local", "--persist-to", str(state_path), "--config", str(config_path), "--json"]

        def command(arguments, *, succeeds=True):
            assert "--remote" not in arguments and "--preview" not in arguments
            result = subprocess.run(arguments, cwd=work, env=environment, capture_output=True, text=True)
            if succeeds and result.returncode != 0:
                raise AssertionError(result.stdout + result.stderr)
            if not succeeds and result.returncode == 0:
                raise AssertionError("command unexpectedly succeeded")
            return result

        command([wrangler, "d1", "migrations", "apply", "DB", "--local", "--persist-to", str(state_path), "--config", str(config_path)])
        seed = (
            "INSERT INTO participants (id,name,gender,level,weight,games_played,active,card) VALUES "
            "(1,'p1','male','beginner',1,0,1,'C1'),(2,'p2','male','beginner',1,0,1,'C2'),"
            "(3,'p3','male','beginner',1,0,1,'C3'),(4,'p4','male','beginner',1,0,1,'C4'),"
            "(5,'p5','male','beginner',1,0,1,'C5');"
            "INSERT INTO match_sessions (id,status,match_count,created_at) VALUES "
            "(1,'draft',0,'2026-01-01T00:00:00.000000Z')"
        )
        command([*common, "--command", seed])

        server = subprocess.Popen(
            [wrangler, "dev", "--local", "--persist-to", str(state_path), "--config", str(config_path), "--port", "18788"],
            cwd=work, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            def post(path):
                request = Request("http://127.0.0.1:18788" + path, data=b"", method="POST")
                try:
                    with urlopen(request, timeout=3) as response:
                        return response.status
                except HTTPError as error:
                    return error.code

            for _ in range(100):
                try:
                    status = post("/missing")
                    if status == 404:
                        break
                except URLError:
                    time.sleep(0.1)
            else:
                raise AssertionError("wrangler dev did not start")

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = sorted(pool.map(lambda _item: post("/confirm"), range(2)))
            assert statuses == [200, 409]

            check = command([*common, "--command", "SELECT (SELECT COUNT(*) FROM match_rounds) AS rounds, (SELECT COUNT(*) FROM match_histories) AS matches, (SELECT games_played FROM participants WHERE id=1) AS games"])
            row = json.loads(check.stdout)[0]["results"][0]
            assert row == {"rounds": 1, "matches": 1, "games": 1}

            command([*common, "--command", "UPDATE match_histories SET team1_score=1, team2_score=0, score_text='21-15', winner_team=1 WHERE id=1"])
            assert post("/revert") == 200
            reverted = command([*common, "--command", "SELECT (SELECT COUNT(*) FROM match_rounds) AS rounds, (SELECT games_played FROM participants WHERE id=1) AS games"])
            assert json.loads(reverted.stdout)[0]["results"][0] == {"rounds": 0, "games": 0}

            command([*common, "--command", "INSERT INTO match_histories (round_id,court_number,team1_player1_id,team1_player2_id,team2_player1_id,team2_player2_id,created_at) VALUES (999,1,1,2,3,4,'2026-01-01T00:00:00.000000Z')"], succeeds=False)
            command([*common, "--command", "INSERT INTO match_rounds (id,session_id,round_number,created_at) VALUES (2,1,2,'2026-01-03T00:00:00.000000Z'); INSERT INTO match_histories (round_id,court_number,team1_player1_id,team1_player2_id,team2_player1_id,team2_player2_id,created_at) VALUES (2,1,1,2,3,4,'2026-01-03T00:00:00.000000Z'); INSERT INTO bench_histories (round_id,participant_id,created_at) VALUES (2,5,'2026-01-03T00:00:00.000000Z')"])
            command([*common, "--command", "INSERT INTO match_histories (round_id,court_number,team1_player1_id,team1_player2_id,team2_player1_id,team2_player2_id,created_at) VALUES (2,1,1,2,3,4,'2026-01-03T00:00:00.000000Z')"], succeeds=False)
            command([*common, "--command", "INSERT INTO bench_histories (round_id,participant_id,created_at) VALUES (2,5,'2026-01-03T00:00:00.000000Z')"], succeeds=False)
        finally:
            server.terminate()
            server.wait(timeout=10)

    print("Phase 4 Wrangler local D1 checks passed")


if __name__ == "__main__":
    main()
