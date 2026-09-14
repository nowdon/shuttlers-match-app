"""Exercise Phase 5 runtime_state CAS and atomic lifecycle on disposable local D1."""
from concurrent.futures import ThreadPoolExecutor
import json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = [ROOT / f"migrations/d1/{name}" for name in (
    "0001_phase3_participants_config.sql", "0002_phase4_match_relational.sql",
    "0003_phase5_runtime_state.sql")]

WORKER = r'''export default { async fetch(request, env) {
  const u = new URL(request.url), ts = "2026-01-02T03:04:05.000000Z";
  const guard = (key, v) => env.DB.prepare("INSERT INTO runtime_state_cas_guard (id) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM runtime_state WHERE key=? AND version=?)").bind(key,v);
  const put = (key, value, v) => env.DB.prepare("UPDATE runtime_state SET state_json=?, version=version+1 WHERE key=? AND version=?").bind(JSON.stringify(value),key,v);
  try {
    if (u.pathname === "/session-create") { const v=Number(u.searchParams.get("version")||1), token=u.searchParams.get("token"); await env.DB.batch([guard('current_match',v),env.DB.prepare("INSERT INTO match_sessions (status,match_count,created_at,creation_token) VALUES ('draft',0,?,?)").bind(ts,token),env.DB.prepare("UPDATE runtime_state SET state_json=json_replace(json_set(state_json,'$.session_id','__session_id_placeholder__'),'$.session_id',CAST((SELECT id FROM match_sessions WHERE creation_token = ?) AS INTEGER)),version=version+1 WHERE key='current_match' AND version=?").bind(token,v)]); return Response.json({ok:true}); }
    if (u.pathname === "/draft") { const v=Number(u.searchParams.get("version")||1); await env.DB.batch([guard('current_draft',v),put('current_draft',{draft:true},v)]); return Response.json({ok:true}); }
    if (u.pathname === "/confirm") { const m=Number(u.searchParams.get("match_version")||1), d=Number(u.searchParams.get("draft_version")||2); await env.DB.batch([guard('current_draft',d),guard('current_match',m),env.DB.prepare("INSERT INTO match_rounds (session_id,round_number,created_at) VALUES (1,1,?)").bind(ts),env.DB.prepare("UPDATE runtime_state SET state_json=?,version=version+1 WHERE key='current_match' AND version=?").bind(JSON.stringify({confirmed:true}),m)]); return Response.json({ok:true}); }
    if (u.pathname === "/rollback") { await env.DB.batch([env.DB.prepare("INSERT INTO match_rounds (session_id,round_number,created_at) VALUES (999,1,?)").bind(ts),env.DB.prepare("INSERT INTO match_histories (round_id,court_number,team1_player1_id,team1_player2_id,team2_player1_id,team2_player2_id,created_at) VALUES (999,1,1,2,3,4,?)").bind(ts)]); return Response.json({ok:true}); }
    if (u.pathname === "/revert") { await env.DB.batch([env.DB.prepare("DELETE FROM match_rounds WHERE session_id=1 AND round_number=1"),env.DB.prepare("UPDATE runtime_state SET state_json=NULL,version=version+1 WHERE key='current_draft'")]); return Response.json({ok:true}); }
    if (u.pathname === "/reset") { await env.DB.batch([env.DB.prepare("DELETE FROM match_rounds"),env.DB.prepare("UPDATE runtime_state SET state_json=?,version=version+1 WHERE key='current_match'").bind(JSON.stringify({bench:[],match_active:false,match_count:0,matches:[]})),env.DB.prepare("UPDATE runtime_state SET state_json=NULL,version=version+1 WHERE key='current_draft'")]); return Response.json({ok:true}); }
    return new Response('not found',{status:404});
  } catch (e) { return Response.json({error:String(e)},{status:409}); }
} };'''

def main():
    if len(sys.argv) != 2: raise SystemExit("usage: run_phase5_wrangler_local.py /path/to/wrangler")
    wrangler = str(Path(sys.argv[1]).resolve())
    with tempfile.TemporaryDirectory(prefix="shuttlers-phase5-d1-") as d:
        work=Path(d); (work/"migrations").mkdir()
        for m in MIGRATIONS: shutil.copy2(m, work/"migrations"/m.name)
        (work/"worker.js").write_text(WORKER)
        cfg={"name":"shuttlers-phase5-local-only","main":"worker.js","compatibility_date":"2026-09-13","d1_databases":[{"binding":"DB","database_name":"phase5-local","database_id":"00000000-0000-0000-0000-000000000005","migrations_dir":"migrations"}]}
        (work/"wrangler.jsonc").write_text(json.dumps(cfg)); state=work/"state"; env=dict(os.environ,CI="1",NO_COLOR="1")
        base=[wrangler,"d1","execute","DB","--local","--persist-to",str(state),"--config",str(work/"wrangler.jsonc"),"--json"]
        migration = subprocess.run([wrangler,"d1","migrations","apply","DB","--local","--persist-to",str(state),"--config",str(work/"wrangler.jsonc")],cwd=work,env=env,capture_output=True,text=True)
        if migration.returncode:
            raise AssertionError(migration.stdout + migration.stderr)
        server=subprocess.Popen([wrangler,"dev","--local","--persist-to",str(state),"--config",str(work/"wrangler.jsonc"),"--port","18789"],cwd=work,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        try:
            def post(path):
                try:
                    with urlopen(Request("http://127.0.0.1:18789"+path,data=b"",method="POST"),timeout=4) as r:
                        return r.status, r.read().decode()
                except HTTPError as e:
                    return e.code, e.read().decode()
                except URLError as e:
                    return 0, str(e)
            ready = False
            for _ in range(100):
                if post("/missing")[0]==404: break
                time.sleep(.1)
            else:
                output = "".join(server.stdout.readlines()) if server.poll() is not None else ""
                raise AssertionError("wrangler dev did not start\n" + output)
            with ThreadPoolExecutor(2) as p:
                session_results = list(p.map(lambda token: post("/session-create?version=1&token=" + token), ("session-a", "session-b")))
            session_statuses = sorted(result[0] for result in session_results)
            assert session_statuses == [200,409], session_results
            winner_token = ("session-a", "session-b")[next(i for i, result in enumerate(session_results) if result[0] == 200)]
            loser_token = "session-b" if winner_token == "session-a" else "session-a"
            check_sql = f"SELECT COUNT(*) AS sessions, (SELECT version FROM runtime_state WHERE key='current_match') AS version, (SELECT state_json FROM runtime_state WHERE key='current_match') AS state, (SELECT COUNT(*) FROM match_sessions WHERE creation_token='{loser_token}') AS loser_rows, (SELECT COUNT(*) FROM match_sessions WHERE creation_token='{winner_token}') AS winner_rows"
            check = subprocess.run(base+["--command", check_sql],cwd=work,env=env,check=True,capture_output=True,text=True)
            session_row = json.loads(check.stdout)[0]["results"][0]
            assert session_row["sessions"] == 1 and session_row["version"] == 2 and session_row["loser_rows"] == 0 and session_row["winner_rows"] == 1, session_row
            parsed_state = json.loads(session_row["state"])
            assert isinstance(parsed_state["session_id"], int) and "placeholder" not in session_row["state"]
            with ThreadPoolExecutor(2) as p: draft_results = list(p.map(lambda _:post("/draft?version=1"),range(2)))
            draft_statuses = sorted(result[0] for result in draft_results)
            assert draft_statuses == [200,409], draft_results
            with ThreadPoolExecutor(2) as p: confirm_results = list(p.map(lambda _:post("/confirm?match_version=1&draft_version=2"),range(2)))
            confirm_statuses = sorted(result[0] for result in confirm_results)
            assert confirm_statuses == [200,409], confirm_results
            assert post("/rollback")[0]==409
            assert post("/revert")[0]==200 and post("/reset")[0]==200
        finally: server.terminate(); server.wait(timeout=10)
    print("Phase 5 Wrangler local D1 checks passed")
if __name__ == "__main__": main()
