# Cloudflare runtime + disposable SQLite PoC

This isolated PoC checks whether the current Flask application can execute its real
`app.wsgi_app` runtime wrapper on Cloudflare Python Workers and use the naturally
computed, ephemeral `instance/participants.db`. It never treats that filesystem as
persistent storage.

The Worker is `shuttlers-match-runtime-poc` on workers.dev only. It has no custom
domain, route, D1, R2, KV, Durable Object, CPU-limit override, production secret, or
real runtime data. The only public paths are:

- `GET /api/participants`
- `GET /runtime-poc/status`
- `GET /runtime-poc/count`
- `GET /runtime-poc/seed`

The synthetic secret value is intentionally not reproduced here or in result files.
The fixed seed is `Runtime PoC Player`; repeated seed requests are idempotent within
the disposable database seen by that request.

## Reproduction

```bash
python cloudflare-runtime-sqlite-poc/build.py
cd cloudflare-runtime-sqlite-poc/.build
pywrangler dev --local --port 8789
```

In a second terminal:

```bash
python cloudflare-runtime-sqlite-poc/measure.py local-worker http://127.0.0.1:8789
```

Public deployment must use `deploy.py`, which performs a dry-run bundle audit before
deploying the dedicated workers.dev Worker. Raw tail logs belong only in `/tmp`; use
`collect_tail.py` to write sanitized evidence.

## Result

Measured 2026-09-11 JST from `origin/develop` commit `165b5c0` (PR #74). The
first deployed version was `2fc0b1e3-c17d-4cf5-a6f3-94420c4cea9d`; the
same-code redeploy was `06a07371-7c69-4e60-9fae-5f1a48c89412`.

The result is **A + B**:

- **A.** The current runtime wrapper and disposable SQLite work on the public
  Python Worker.
- **B.** Initialization works, but SQLite state is not guaranteed between
  requests.

This is not evidence that filesystem SQLite is persistent. Cloudflare documents
the Python Worker filesystem as ephemeral, in-memory, and unshared between
isolates: <https://developers.cloudflare.com/workers/languages/python/stdlib/>.

### Runtime initialization

| Item | Result |
| --- | --- |
| synthetic `SECRET_KEY` | set before `from app import app`; value never returned or stored in results |
| runtime wrapper | actual `app.wsgi_app` passed to `workers.wsgi` |
| `initialize_runtime()` | success |
| natural instance directory creation | success; no path override |
| SQLite creation | absent before first request, present after |
| `db.create_all()` | success; 10 tables |
| schema compatibility | success |
| `_runtime_initialized` | false before first request, true after |

### DB interoperability

| Operation | Result |
| --- | --- |
| SQLAlchemy create/count | success |
| synthetic insert/commit | success |
| existing raw sqlite3 `/api/participants` read | success |
| duplicate prevention | same local disposable DB stayed at 1 after second seed |

Local `workerd` ran the complete 121-request plan: 121 success, no HTTP error.
The first status observed participant count 0 and 10 tables. The seed/count/raw
API/seed/count/raw API sequence observed 1 throughout after the first insert.
All repeated reads and the 2-second, 15-second, and 30-second observations stayed
at 1. Local timings are client elapsed only and are not Cloudflare CPU metrics.

### Public Worker

The public first-version tail covered all 121 requests. CPU and wall values are ms.

| Operation | requests | success | 1101 | 1102 | CPU p50 | p95 | max | wall max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| initialization status | 1 | 1 | 0 | 0 | 267 | 267 | 267 | 269 |
| raw API read | 59 | 59 | 0 | 0 | 8 | 240.9 | 481 | 488 |
| SQLAlchemy count | 59 | 59 | 0 | 0 | 8 | 243.6 | 674 | 681 |
| synthetic write | 2 | 2 | 0 | 0 | 43 | 59.2 | 61 | 66 |
| **all** | **121** | **121** | **0** | **0** | **8** | **249** | **674** | **681** |

Twelve requests observed runtime/DB absent before dispatch and initialized both
during that request. Their CPU p50/p95/max was 249/567.85/674. The other 109
requests observed an initialized runtime, with CPU p50/p95/max 8/75.4/167.

### Request-to-request state

Public responses observed participant count 0 in 100 requests and 1 in 21.
Sequence B demonstrated the important boundary: a seed returned count 1, the next
SQLAlchemy count returned 0, and a following raw API read returned 1. The second
seed also returned `created=true`, consistent with reaching a different disposable
database; this does not disprove idempotency within one database.

| Cadence | requests | count 0 | count 1 | runtime absent before request |
| --- | ---: | ---: | ---: | ---: |
| continuous | 109 | 90 | 19 | 11 |
| 2 seconds | 10 | 10 | 0 | 1 |
| after 15 seconds idle | 1 | 0 | 1 | 0 |
| after 30 seconds idle | 1 | 0 | 1 | 0 |

These are state observations only. No marker is treated as proof of isolate
identity. After same-code redeploy, a version-filtered request on the second
version returned an empty participant list and observed runtime/DB false before,
true after. Tail confirmed the second version, with no version mismatch.

### Safety boundary

- Local and public probes returned 404 for all 10 sampled business paths,
  including `/`, `/register`, `/match`, `/admin`, `/api/match_state`, and LINE /
  notification paths. A POST to the status route returned 405.
- The final dry-run bundle contained 1180 files, with forbidden DB/data files 0,
  credential matches 0, and real participant data files 0.
- The repository DB, config, match state, missing draft state, and 838 history dump
  files had identical pre/local/predeploy/postdeploy fingerprints. Hash values are
  intentionally not stored in result artifacts.
- D1, R2, KV, Durable Objects, custom domains, DNS, production Workers, and CPU
  limit changes were not used.

Detailed sanitized evidence is under `results/`. Raw tail logs were retained only
under `/tmp` during collection and are not part of the repository.
