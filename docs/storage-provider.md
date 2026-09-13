# Storage provider

## Purpose

This boundary provides backend-neutral SQL execution for the staged D1 migration.
Phase 3 moves Participant-facing reads and writes, the Participant API, and D1
application configuration onto it. Phase 4 adds MatchSession and match-history
relational storage. Runtime JSON state and LINE relational storage remain outside
this boundary.

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

Match generation still uses explicitly named transitional ORM helpers because it
mutates candidate objects while constructing a draft. Confirm/revert games-played
changes are explicit relational commands; no relationship is faked on
`ParticipantRecord`.

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

## Phase 4 match relational boundary

`data/match_sessions.py` returns immutable `MatchSessionRecord` values and owns
session lookup, creation, and close commands. `data/match_history.py` returns
immutable `MatchRoundRecord`, `MatchHistoryRecord`, and `BenchHistoryRecord`
values. Multi-round reads use one rounds query, one matches query, and one bench
query instead of per-round reads.

`confirm_match_relational()` atomically creates a round and its court/bench rows,
increments `Participant.games_played`, and marks the session confirmed.
`revert_match_relational()` atomically applies the inverse history and games-played
writes. Score commands persist one validated match or a fully validated round.
`clear_match_history()` deletes bench rows, match rows, and rounds in FK-safe order
without deleting Participants, MatchSessions, or LINE data.

New rounds carry nullable `match_rounds.session_id`. The stable
`(session_id, round_number)` key lets D1 batch statements locate the parent without
depending on a Python-visible `last_row_id`. Unique indexes guard the session/round,
round/court, and round/bench-participant keys. Duplicate confirms become
`StorageConflictError` and the failed batch cannot increment games played.

SQLite startup adds the column and indexes additively. Existing rows remain with a
NULL session ID. Index creation fails explicitly if legacy duplicates exist and
never silently deletes them. Fresh SQLite metadata and D1 migration 0002 describe
the same adjuncts. Record timestamps are timezone-aware UTC; writes use
`YYYY-MM-DDTHH:MM:SS.ffffffZ` and reads also accept legacy SQLAlchemy SQLite values.

Phase 4 relational writes are atomic within DB. `match_state.json` and
`draft_state.json` remain outside that transaction. Confirm commits relational data
before publishing match JSON, clearing draft JSON, and sending LINE notifications.
A failure between relational commit and filesystem state update remains a temporary
migration limitation until Phase 5.

### Caller inventory and transition status

- Storage-backed now: MatchSession lifecycle, match history reads, confirm/revert,
  score persistence, history clear, pair history, win statistics, and recent-round
  consecutive-play reads.
- Phase 4 transitional ORM: draft generation/rendering Participant objects, all
  LINE relational operations, complete database reset, and reset-time clearing of
  all Participant games counters.
- Phase 5 or later: runtime JSON/version/CAS and cross-store atomicity; LINE tables
  and notification reservation; R2 dumps; production D1 cutover.

The Phase 4 disposable local check is:

```bash
python tests/run_phase4_wrangler_local.py \
  cloudflare-d1-binding-poc/node_modules/.bin/wrangler
```

It applies migrations 0001 and 0002 locally, races two confirms for the same key,
exercises score and revert SQL, and checks foreign-key enforcement. It rejects
production/remote operation.
