"""Exercise Phase 6 LINE token and notification races on disposable local D1."""
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
MIGRATIONS = [ROOT / "migrations/d1" / name for name in (
    "0001_phase3_participants_config.sql", "0002_phase4_match_relational.sql",
    "0003_phase5_runtime_state.sql", "0004_phase6_line_notifications.sql",
)]

WORKER = r'''export default { async fetch(request, env) {
  const u = new URL(request.url), ts = "2026-01-02T03:04:05.000000Z";
  try {
    if (u.pathname === "/consume") {
      const line = u.searchParams.get("line");
      await env.DB.batch([
        env.DB.prepare("UPDATE line_link_tokens SET used_at=? WHERE token='TOKEN1' AND used_at IS NULL AND julianday(expires_at)>julianday(?) AND EXISTS (SELECT 1 FROM participants p WHERE p.id=line_link_tokens.participant_id AND p.active=1)").bind(ts,ts),
        env.DB.prepare("INSERT INTO line_link_tokens (token,participant_id,session_id,expires_at,created_at) SELECT token,participant_id,session_id,expires_at,created_at FROM line_link_tokens WHERE token='TOKEN1' AND changes()=0"),
        env.DB.prepare("INSERT INTO line_accounts (participant_id,line_user_id,active,created_at,updated_at) SELECT participant_id,?,1,?,? FROM line_link_tokens WHERE token='TOKEN1' AND used_at=? ON CONFLICT(participant_id) DO UPDATE SET line_user_id=excluded.line_user_id,active=1,updated_at=excluded.updated_at").bind(line,ts,ts,ts),
        env.DB.prepare("INSERT INTO notification_subscriptions (session_id,participant_id,channel,active,created_at,updated_at) SELECT session_id,participant_id,'line',1,?,? FROM line_link_tokens WHERE token='TOKEN1' AND used_at=? ON CONFLICT(session_id,participant_id,channel) DO UPDATE SET active=1,updated_at=excluded.updated_at").bind(ts,ts,ts)
      ]);
      return Response.json({ok:true});
    }
    if (u.pathname === "/reserve") {
      const count = Number(u.searchParams.get("count") || 1);
      const result = await env.DB.prepare("INSERT INTO match_notifications (session_id,match_count,channel,status,created_at) VALUES (1,?,'line','pending',?) ON CONFLICT(session_id,match_count,channel) DO NOTHING RETURNING id").bind(count,ts).all();
      return Response.json({owner:result.results.length === 1});
    }
    if (u.pathname === "/delivery") {
      await env.DB.batch([
        env.DB.prepare("INSERT INTO notification_delivery_logs (session_id,participant_id,match_count,channel,status,sent_at) VALUES (1,1,1,'line','pending',?)").bind(ts),
        env.DB.prepare("UPDATE notification_delivery_logs SET status='success',sent_at=? WHERE session_id=1 AND participant_id=1 AND match_count=1").bind(ts),
        env.DB.prepare("UPDATE match_notifications SET status='completed',sent_at=? WHERE session_id=1 AND match_count=1 AND channel='line'").bind(ts)
      ]);
      return Response.json({ok:true});
    }
    if (u.pathname === "/fk") {
      await env.DB.prepare("INSERT INTO line_accounts (participant_id,line_user_id,active,created_at,updated_at) VALUES (999,'missing',1,?,?)").bind(ts,ts).run();
      return Response.json({ok:true});
    }
    return new Response("not found", {status:404});
  } catch (e) { return Response.json({error:String(e)}, {status:409}); }
} };'''


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_phase6_wrangler_local.py /path/to/wrangler")
    wrangler = str(Path(sys.argv[1]).resolve())
    version = subprocess.run([wrangler, "--version"], check=True, capture_output=True, text=True).stdout
    assert "4.131.1" in version, version
    with tempfile.TemporaryDirectory(prefix="shuttlers-phase6-d1-") as directory:
        work = Path(directory)
        (work / "migrations").mkdir()
        for migration in MIGRATIONS:
            shutil.copy2(migration, work / "migrations" / migration.name)
        (work / "worker.js").write_text(WORKER, encoding="utf-8")
        config = {
            "name": "shuttlers-phase6-local-only", "main": "worker.js",
            "compatibility_date": "2026-09-14",
            "d1_databases": [{"binding": "DB", "database_name": "phase6-local",
                              "database_id": "00000000-0000-0000-0000-000000000006",
                              "migrations_dir": "migrations"}],
        }
        (work / "wrangler.jsonc").write_text(json.dumps(config), encoding="utf-8")
        state = work / "state"
        env = dict(os.environ, CI="1", NO_COLOR="1")
        base = [wrangler, "d1", "execute", "DB", "--local", "--persist-to", str(state),
                "--config", str(work / "wrangler.jsonc"), "--json"]
        migration = subprocess.run(
            [wrangler, "d1", "migrations", "apply", "DB", "--local", "--persist-to",
             str(state), "--config", str(work / "wrangler.jsonc")],
            cwd=work, env=env, capture_output=True, text=True,
        )
        if migration.returncode:
            raise AssertionError(migration.stdout + migration.stderr)
        seed = (
            "INSERT INTO participants (id,name,gender,level,weight,games_played,active,card) "
            "VALUES (1,'p1','male','beginner',1,0,1,'C1'),(2,'p2','male','beginner',1,0,1,'C2');"
            "INSERT INTO match_sessions (id,status,match_count,created_at) VALUES (1,'draft',0,'2026-01-01T00:00:00.000000Z');"
            "INSERT INTO line_link_tokens (token,participant_id,session_id,expires_at,created_at) "
            "VALUES ('TOKEN1',1,1,'2027-01-01T00:00:00.000000Z','2026-01-01T00:00:00.000000Z');"
        )
        subprocess.run(base + ["--command", seed], cwd=work, env=env, check=True,
                       capture_output=True, text=True)
        server = subprocess.Popen(
            [wrangler, "dev", "--local", "--persist-to", str(state), "--config",
             str(work / "wrangler.jsonc"), "--port", "18790"],
            cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            def post(path):
                try:
                    with urlopen(Request("http://127.0.0.1:18790" + path, data=b"", method="POST"), timeout=4) as response:
                        return response.status, response.read().decode()
                except HTTPError as error:
                    return error.code, error.read().decode()
                except URLError as error:
                    return 0, str(error)
                except OSError as error:
                    return 0, str(error)

            for _ in range(100):
                if post("/missing")[0] == 404:
                    break
                time.sleep(.1)
            else:
                output = "".join(server.stdout.readlines()) if server.poll() is not None else ""
                raise AssertionError("wrangler dev did not start\n" + output)

            with ThreadPoolExecutor(2) as pool:
                consumed = list(pool.map(lambda line: post("/consume?line=" + line), ("U1", "U2")))
            assert sorted(status for status, _body in consumed) == [200, 409], consumed
            with ThreadPoolExecutor(2) as pool:
                reservations = list(pool.map(lambda _: post("/reserve?count=1"), range(2)))
            owners = sorted(json.loads(body)["owner"] for status, body in reservations if status == 200)
            assert owners == [False, True], reservations
            assert json.loads(post("/reserve?count=2")[1])["owner"] is True
            assert post("/fk")[0] == 409
            assert post("/delivery")[0] == 200

            check = subprocess.run(
                base + ["--command", "SELECT (SELECT COUNT(*) FROM line_accounts) accounts, "
                        "(SELECT COUNT(*) FROM notification_subscriptions) subscriptions, "
                        "(SELECT COUNT(*) FROM match_notifications) notifications, "
                        "(SELECT COUNT(*) FROM notification_delivery_logs WHERE status='success') successes, "
                        "(SELECT status FROM match_notifications WHERE match_count=1) status"],
                cwd=work, env=env, check=True, capture_output=True, text=True,
            )
            row = json.loads(check.stdout)[0]["results"][0]
            assert row == {"accounts": 1, "subscriptions": 1, "notifications": 2,
                           "successes": 1, "status": "completed"}, row
        finally:
            server.terminate()
            server.wait(timeout=10)
    print("Phase 6 Wrangler local D1 checks passed")


if __name__ == "__main__":
    main()
