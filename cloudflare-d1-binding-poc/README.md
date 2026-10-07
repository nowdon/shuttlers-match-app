# Cloudflare Python Workers + Flask + D1 binding PoC

This workers.dev-only PoC tests the exact boundary proposed in
`docs/cloudflare-storage-migration-design.md`:

```text
sync Flask route
  -> request.environ["workers.env"]
  -> request-local D1Adapter
  -> pyodide.ffi.run_sync
  -> DB D1 binding
```

The Worker imports the current application with `from app import app`. Its outer
WSGI allowlist rejects every production route before Flask dispatch, while each
allowed request still traverses `app.wsgi_app -> runtime_wsgi_app ->
initialize_runtime()`. The Flask/SQLAlchemy bootstrap database is disposable
Worker-local SQLite only; all participant-like PoC data is stored in D1.

## Safety boundary

- Worker: `shuttlers-match-d1-poc`, workers.dev only, no routes or custom domain.
- Database: `shuttlers-match-d1-poc-db`, binding `DB`, created solely for this PoC.
- No R2, KV, Durable Object, Assets, service, production database, or production
  credential binding exists.
- The bundle links application Python source only. It contains no `config.json`,
  state JSON, SQLite database, history dump, template, static file, or credential.
- The only secret is a fixed synthetic Flask signing key scoped to the PoC source.
  It is never returned or logged.
- Migrations create only `d1_poc_*` tables. Routes never issue DDL.

## API

| Method | Route | D1 behavior |
|---|---|---|
| GET | `/d1-poc/status` | `all()` table/count and safe binding flags |
| POST | `/d1-poc/participants` | prepared/bound `INSERT ... RETURNING` via `run()` |
| GET | `/d1-poc/participants/<id>` | prepared/bound `first()`, including missing-row 404 |
| GET | `/d1-poc/participants` | `all()` and actual result shape |
| POST | `/d1-poc/reset` | `run()` DELETEs for PoC tables only |
| POST | `/d1-poc/batch-success` | atomic three-statement `batch()` success |
| POST | `/d1-poc/batch-failure` | duplicate in middle of `batch()` |
| GET | `/d1-poc/batch-failure-state` | separate-request rollback verification |
| POST | `/d1-poc/fk-failure` | bound orphan insert and normalized FK error |
| POST | `/d1-poc/multiple-run-sync` | five sequential adapter calls in one sync request |

All SQL values use `prepare(...).bind(...)`. Request bodies are ignored; only
fixed synthetic values are written. Expected UNIQUE and FK failures map to HTTP
409, missing rows to 404, and unexpected D1 failures to 500.

## Setup and migration

```bash
python prepare_source_links.py
uv sync
npm install
npx wrangler d1 migrations apply shuttlers-match-d1-poc-db --local
uv run pywrangler dev --port 8787
```

Remote migration and deployment deliberately target only the database and Worker
named above:

```bash
npx wrangler d1 migrations apply shuttlers-match-d1-poc-db --remote
uv run pywrangler deploy
```

## Measurement plan

Run `python smoke_http.py BASE_URL --full-waits` while `wrangler tail --format
json` is active. Join tail events by path and report request count, success,
expected conflict, outcomes 1101/1102, and CPU/wall p50, p95, and max separately
for status, insert, first, all, batch success, batch failure, UNIQUE, and FK.
Expected 409 responses are conflicts, not Worker errors.

The smoke runner verifies immediate, 2-second, 15-second, and 30-second reads,
batch rollback in a later request, and two concurrent inserts of the same unique
card. Redeploy persistence is checked by inserting once, recording the deployed
version ID, redeploying identical code, recording the new version ID, and reading
the same participant ID again. Tail evidence from mixed version IDs is excluded.

## Observed Python interop and result shapes

Local Miniflare and the public Worker produced the same Python-side shapes:

| D1 value | Python runtime type | `dict()` | `to_py()` | attribute access |
|---|---|---|---|---|
| `run()` D1Result | `JsDict` | works | not present | `results`, `success`, `meta` work |
| `all()` D1Result | `JsDict` | works | not present | `results`, `success`, `meta` work |
| `first()` row | `JsDict` | works | not present | row field access works |
| `first()` no row | `NoneType` / `None` | n/a | n/a | n/a |
| `batch()` result | Python `list` of `JsDict` | list itself is not a mapping | not present | index then D1Result attributes |
| `meta` | `JsDict` | works | not present | named fields work |
| constraint error | `JsException` | n/a | n/a | no Python `__cause__` observed |

`all()` has top-level `results`, `success`, and `meta`. Each successful batch
element has that same D1Result shape. Public D1 `meta` contained operational and
serving metadata in addition to changes, duration, row counts, and last row ID;
responses therefore expose only the required numeric values and key names, never
serving-location values.

`INSERT ... RETURNING id, name, card` through `run()` returned one row. Its ID was
equal to `meta.last_row_id`, and `meta.changes` was 1. The request-local adapter
does not need to call `to_py()` for current D1 results because Workers Python has
already materialized `JsDict`/`list`; it retains a `to_py(dict_converter=dict)`
fallback for a raw `JsProxy` returned by another compatibility/runtime version.

## Constraint and transaction results

- Duplicate `card`: first insert 201; second insert `JsException`, no cause,
  sanitized as `unique constraint failed`, HTTP 409; final row count 1.
- Missing FK parent: `JsException`, no cause, sanitized as
  `foreign key constraint failed`, HTTP 409.
- Successful batch: two inserts and one select returned a three-element Python
  list; the select element contained `A` and `B`.
- Failed batch: the middle duplicate raised `JsException`; a separate subsequent
  HTTP request found neither `A` nor `B`. Middle-failure rollback is **yes**.
- Five sequential `run_sync` calls in one synchronous Flask request completed
  without nested-loop, reentrancy, Promise reuse, deadlock, or timeout errors.

The recommended production mapping is UNIQUE/FK 409, missing row 404, and any
unclassified D1 failure 500. Matching raw database messages is isolated in the
adapter and the public response contains only normalized categories/messages.

## Persistence and concurrency results

- Local and public D1 reads both succeeded immediately and after 2, 15, and 30
  seconds in separate HTTP requests.
- Initial deploy version `227cce46-d0ed-4323-bfed-8fb9b16e2af6` was replaced by
  `8f8b9f57-428f-481f-a202-ff5b3f644a4a`; the previously inserted row remained.
  The final sanitized/materializing adapter deploy is
  `684f60a7-4d54-42da-9ddf-823ed8b80b14`, and the same row remains readable.
- Two simultaneous requests for `D1-POC-001` produced one 201, one 409, and one
  final row. There is no pre-check SELECT; the UNIQUE constraint is authoritative.

## Public Worker performance

Sanitized `wrangler tail --format json` evidence from only version
`227cce46-d0ed-4323-bfed-8fb9b16e2af6` was used. p95 below uses nearest-rank;
single-request operations therefore have p50 = p95 = max. All observed outcomes
were `ok`, exception arrays were empty, and 1101/1102 counts were zero.

| Operation | Requests | Success | Expected conflict | 1101 | 1102 | CPU ms p50 / p95 / max | Wall ms p50 / p95 / max |
|---|---:|---:|---:|---:|---:|---:|---:|
| status | 2 | 2 | 0 | 0 | 0 | 154.5 / 291 / 291 | 333.5 / 636 / 636 |
| insert success | 2 | 2 | 0 | 0 | 0 | 12 / 16 / 16 | 35 / 40 / 40 |
| select first, found | 5 | 5 | 0 | 0 | 0 | 8 / 14 / 14 | 21 / 35 / 35 |
| select first, missing | 1 | 1 | 0 | 0 | 0 | 15 / 15 / 15 | 27 / 27 / 27 |
| select all | 2 | 2 | 0 | 0 | 0 | 11 / 12 / 12 | 21.5 / 23 / 23 |
| batch success | 1 | 1 | 0 | 0 | 0 | 17 / 17 / 17 | 45 / 45 / 45 |
| batch failure | 1 | 0 | 1 | 0 | 0 | 17 / 17 / 17 | 57 / 57 / 57 |
| UNIQUE failure | 2 | 0 | 2 | 0 | 0 | 35 / 46 / 46 | 52.5 / 70 / 70 |
| FK failure | 1 | 0 | 1 | 0 | 0 | 32 / 32 / 32 | 57 / 57 / 57 |
| multiple `run_sync` | 1 | 1 | 0 | 0 | 0 | 19 / 19 / 19 | 80 / 80 / 80 |

The first status sample includes Python isolate/application cold initialization;
warm status used 18 ms CPU and 31 ms wall.

The performance table above is the measured dataset for version
`227cce46-d0ed-4323-bfed-8fb9b16e2af6`. A shorter regression smoke was also run
against the final version `684f60a7-4d54-42da-9ddf-823ed8b80b14`: status,
insert/select, batch success, batch failure plus separate-request rollback,
UNIQUE/FK conflicts, and multiple `run_sync` all behaved as expected. All 17 tail
events belonged to that final version and had `outcome=ok`, no exceptions, and
zero 1101/1102 outcomes. The final-version smoke is a regression confirmation,
not a replacement performance dataset.

## Safety verification

Local Worker, pytest, both deploys, and public HTTP probes left the SHA-256 hashes
of `instance/participants.db`, `config.json`, and `match_state.json` unchanged;
`draft_state.json` remained absent. The 838 history dump files and their aggregate
hash were unchanged. Bundle output listed application `.py` modules and vendored
packages only: no production DB, config/state, dumps, R2/KV/DO/assets binding,
production credential, custom route, or custom domain. All listed production
routes returned 404 both locally and on workers.dev.

## Out of scope / Not validated

This deliberately narrow binding PoC does not implement or validate:

- production schema or production data migration;
- conversion of the real `Participant` storage path to D1;
- match, session, or history transaction design;
- `runtime_state` or application config migration;
- LINE, R2, or Durable Objects integration;
- production cutover, production domains, or production traffic;
- request cancellation or timeout behavior;
- generated round ID allocation or a stale-draft atomic guard.

## Conclusion

**B. sync Flask + `run_sync` + D1 is viable with adapter/result-conversion
caveats.** The architectural boundary works. Current results arrive as
`JsDict`/`list` rather than raw `JsProxy`, errors arrive as `JsException`, and D1
meta must be allowlisted before it is exposed. These are contained by the small
request-local adapter and do not block the proposed migration architecture.
