# Storage provider

## Purpose

This boundary provides backend-neutral SQL execution for the staged D1 migration.
Phase 3 moves Participant-facing reads and writes, the Participant API, and D1
application configuration onto it. Match/session/history state and LINE relational
storage remain on their transitional SQLAlchemy paths.

## Backend selection

The backend is selected in this order: an explicit `create_storage()` argument,
`app.config["STORAGE_BACKEND"]`, the `STORAGE_BACKEND` environment variable, then
the default `sqlite`. A Cloudflare binding never selects D1 implicitly.

`sqlite` uses `<current_app.instance_path>/participants.db`. `d1` requires an
explicit binding or `request.environ["workers.env"].DB`; a missing binding fails
closed and never falls back to SQLite.

## Result and error contracts

`first()` returns `dict | None`, `all()` returns `list[dict]`, `run()` returns one
`StorageResult`, and `batch()` returns one `StorageResult` per statement.
`StorageResult` contains `rows`, `changes`, and `last_row_id`; D1 operational
metadata is not exposed. For SQLite, `last_row_id` is populated only when the
statement itself generates a new row id; non-insert writes return `None`.

Backend errors are normalized to `StorageError`, `StorageUniqueError`,
`StorageForeignKeyError`, or `StorageUnavailableError` (with
`StorageConstraintError` as the constraint base). Their string messages are safe;
raw database details are not exposed through the public message.

## Request lifetime

`get_storage()` caches one adapter on `flask.g`. The same request receives the
same instance, different requests receive different instances, and Flask request
teardown closes SQLite connections. CLI and unit-test callers can explicitly use
`create_storage(backend="sqlite", database_path=...)`. D1 outside a request needs
an explicitly injected binding.

## Known migration limitation

Even with `STORAGE_BACKEND=d1`, `initialize_runtime()` still performs the current
SQLite bootstrap. Removing that bootstrap and cutting production data over to D1
belong to a later migration phase.

## Phase 3 Participant boundary

`data/participants.py` owns the Participant SQL and returns immutable
`ParticipantRecord` values. It normalizes `active` to a Python boolean and numeric
fields to stable Python types. Registration, Participant editing, CSV upload, viewer
and admin Participant display, and `/api/participants` use named reads or commands.
The API no longer opens SQLite directly.

Match generation and confirm/revert still require ORM identity or dirty tracking.
Those callers use explicitly named transitional ORM helpers; no relationship is
faked on `ParticipantRecord`.

The isolated migration
`migrations/d1/0001_phase3_participants_config.sql` contains only the Phase 3
`participants` and `app_config` tables. It is for synthetic local validation
and is not applied to a production D1 database by this change.

## Phase 3 configuration behavior

With the default `sqlite` backend, `config.json` remains the authority and its
existing read/write behavior is unchanged. Only an explicit `STORAGE_BACKEND=d1`
selects D1. D1 reads the single `app_config` row whose key is `main`; a missing or
invalid row fails closed and never falls back to the filesystem.

D1 saves use compare-and-swap: the update must match the version loaded by the
admin settings form, and a successful save increments it. A stale version raises a
storage conflict and the settings page returns a conflict response with the latest
values.

Admin-editable non-secret configuration belongs in `app_config`. `SECRET_KEY`,
`LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`, and `SMTP_PASSWORD` remain
Worker Secrets/environment values and are removed from D1 serialization if passed
accidentally. Production URL, DNS, data migration, and backend cutover are outside
Phase 3.

## Reproducing the Wrangler local D1 contract check

Run the dedicated integration harness with the repository-local Wrangler binary:

```bash
python tests/run_phase3_wrangler_local.py \
  cloudflare-d1-binding-poc/node_modules/.bin/wrangler
```

The harness creates an isolated temporary Worker project and D1 persistence
directory, applies the Phase 3 migration with `--local`, and deletes all temporary
state when it exits. It uses only synthetic Participant and configuration data.
It also starts a loopback-only `wrangler dev` Worker so the raw D1 binding result
and constraint error are checked through the existing Python D1 adapter. The
harness rejects `--remote` and `--preview`; it does not access production D1,
`config.json`, `participants.db`, DNS, custom domains, LINE, or SMTP.
