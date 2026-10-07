# v2.0.0 release preparation — 2026-10-07

## Scope and classification

The starting checkout matched `origin/develop`. The only uncommitted files
were `cloudflare-wsgi-poc/` and `tests/test_cloudflare_wsgi_poc.py`.
This cleanup does not change production behavior, resources, DNS, data, or
notification delivery. Historical GitHub PoC branches remain intact.

| Classification | Paths | Decision / reason |
| --- | --- | --- |
| Production required | `worker.py`, `app.py`, `data/`, `storage/`, `mail/`, `routes/`, `utils/`, templates, D1 migrations, Worker dependency locks | Retain unchanged; live Worker/D1/R2/LINE/Email implementation and local/legacy compatibility |
| Operational tooling | `migration/`, `scripts/card_asset_inventory.py`, `scripts/prepare_worker_bundle.py`, `scripts/run_phase142c_card_static.py`, `tests/run_phase14_remote_*.py`, `tests/run_phase142b_*.py`, related regression tests | Retain; target guards, migration/reconciliation, disposable remote smoke, readiness, static asset validation and incident reproduction |
| Operational tooling | `cloudflare-d1-binding-poc/`, `cloudflare-r2-binding-poc/`, `cloudflare-email-binding-poc/`, their tests, `tests/run_phase3_wrangler_local.py`, `tests/run_phase7_wrangler_local.py`, `tests/run_phase9_wrangler_local.py` | Retain despite the historical PoC names; local isolated binding/FFI checks are useful without production writes or real email delivery |
| Migration/rehearsal evidence | `docs/cloudflare-migration-runbook.md`, `docs/cloudflare-storage-migration-design.md`, retained binding harness evidence, synthetic migration fixtures/tests | Retain; historical decisions, validation, rollback, limitations and operator procedures |
| Obsolete PoC / temporary diagnostics | `cloudflare-poc/`, `cloudflare-import-poc/`, `cloudflare-runtime-sqlite-poc/`, `cloudflare-stability-poc/`, `cloudflare-jinja-request-poc/`, `cloudflare-wsgi-poc/` | Remove local experiment trees; initial Flask/import/Jinja/ephemeral-SQLite/staged-runtime questions have been superseded by the actual application Worker and Phase 14 validation |
| Obsolete experiment tests | `tests/test_cloudflare_poc.py`, `tests/test_cloudflare_import_poc.py`, `tests/test_cloudflare_runtime_sqlite_poc.py`, `tests/test_cloudflare_stability_poc.py`, `tests/test_cloudflare_jinja_request_poc.py`, `tests/test_cloudflare_wsgi_poc.py` | Remove with the experiments they test; production adapter and behavior tests remain |

Removed tracked experiment source and result files remain retrievable from
Git history. The uncommitted WSGI experiment was deleted at the operator's
request; it is not included in the release. Historical runbook references
to retaining it describe the earlier PR preparation, not the current tree.
Do not treat old PoC commands or historical runtime failures as current
production instructions or current test results.

The retained Email local harness no longer searches for Python modules in
the deleted WSGI experiment. Its D1/R2 module fallback locations remain.
No production adapter, binding logic, route behavior, or schema was changed.

## Test inventory

Before cleanup the local workspace collected 567 tests; 564 were tracked.
The removed experiment tests are:

| Experiment | Removed tests |
| --- | ---: |
| Initial Flask | 3 |
| Import | 5 |
| Ephemeral SQLite | 3 |
| Staged runtime stability | 5 |
| Jinja request | 6 |
| Uncommitted WSGI | 3 |
| Total | 25 |

The remaining suite contains 542 tests. This reduction removes obsolete
experiment coverage only; no failing production test was removed or relaxed.

## Release boundary

README, CHANGELOG and the manual header badge describe v2.0.0. Local/legacy
SQLite and SMTP remain supported; production uses Workers Paid, D1, R2,
LINE Messaging API and Cloudflare Email Service. Card artwork is supplied
separately by the operator, remains ignored, and is not distributed by Git.

This preparation does not create a commit, push, PR, main merge, tag, release,
or production deployment. Those actions require a separate instruction.
