# Storage provider

## Purpose

This boundary provides backend-neutral SQL execution for the staged D1 migration.
Phase 3 moves Participant-facing reads and writes, the Participant API, and D1
application configuration onto it. Phase 4 adds MatchSession and match-history
relational storage, Phase 5 adds runtime state, and Phase 6 adds LINE relational
storage. Phase 7 uses a separate, deliberately smaller history-archive boundary
because R2 object storage is not relational storage.

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
writes. A concrete session ID can only revert its own round; a missing target is a
no-op and never falls back to a legacy NULL-session round. Passing no session ID
retains the legacy highest-ID lookup. Score commands persist one validated match or
a fully validated round.
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
History lists normalize both timestamp formats to UTC before application-side
datetime and ID ordering, so mixed legacy and canonical rows remain chronological.

Phase 5 moves `match_state.json` and `draft_state.json` into the versioned
`runtime_state` table (`current_match` and `current_draft`). Updates use optimistic
CAS guards and tombstone draft rows; generation, confirm, revert, reset, and session
creation include the runtime rows in the same atomic batch as relational writes.
SQLite startup performs a one-time, additive import of legacy JSON files and retains
them as untouched backups. The files are no longer read or written by application
runtime code.

### Caller inventory and transition status

- Storage-backed now: MatchSession lifecycle, match history reads, confirm/revert,
  score persistence, history clear, pair history, win statistics, and recent-round
  consecutive-play reads.
- Transitional ORM: draft generation/rendering Participant objects, complete
  database reset, and reset-time clearing of all Participant games counters.
- Storage-backed through Phase 6: LINE account, subscription, token, notification
  reservation, delivery-log, and completion operations.
- Storage-backed through Phase 7: history archive creation, write, list, read,
  metadata generation, detail rendering, and email attachment bytes.
- Later phases: production D1/R2 cutover and existing archive migration.

The Phase 4 disposable local check is:

```bash
python tests/run_phase4_wrangler_local.py \
  cloudflare-d1-binding-poc/node_modules/.bin/wrangler
```

It applies migrations 0001 and 0002 locally, races two confirms for the same key,
exercises score and revert SQL, and checks foreign-key enforcement. It rejects
production/remote operation.

The Phase 5 local runtime-state check is:

```bash
python tests/run_phase5_wrangler_local.py \
  cloudflare-d1-binding-poc/node_modules/.bin/wrangler
```

It applies migrations 0001–0003 to a disposable local D1 database and exercises
parallel draft writes, confirm contention, rollback injection, revert, reset, and
session creation races. It never targets a remote or preview database.

## Phase 6 LINE relational boundary

`data/line_notifications.py` owns named reads and commands for `line_accounts`,
`notification_subscriptions`, `line_link_tokens`, `match_notifications`, and
`notification_delivery_logs`. It returns immutable records and normalizes all
timestamps to aware UTC values. Target selection joins Participant, account, and
current-session subscription data in one query; a past subscription is never
treated as current.

Account and subscription upserts preserve the existing uniqueness and reactivate
existing rows. Link-token consumption uses a guarded unused/unexpired/active-
participant update in the same database batch as account and subscription upserts.
A zero-row guard becomes a batch conflict, so concurrent use of one token produces
one success and one rejection. A later constraint failure rolls back the claim.

Notification sending first inserts the unique `(session_id, match_count, channel)`
reservation with targeted `ON CONFLICT DO NOTHING RETURNING`. Only the request
receiving the inserted row owns target selection, pending delivery-log creation,
LINE pushes, result updates, and completion. Different match counts in one session
have distinct reservations. Partial failure and zero-target completion keep their
existing meanings.

The reservation is committed before target selection, then all pending delivery
logs are prepared in one database batch. A failure while preparing those logs can
therefore leave the unique reservation pending. Phase 6 intentionally does not
retry or lease that reservation automatically.

The external side-effect boundary is:

```text
DB reservation and pending logs
-> external LINE push
-> DB delivery result and notification completion
```

LINE HTTP calls are outside D1 transactions. If a process stops after LINE accepts
a push but before its result is stored, the reservation remains pending; Phase 6
does not add leases, timeouts, automatic retry, or exactly-once external delivery.
The send attempt and result persistence are separate failure domains: a LINE push
failure is stored as a failed delivery with its error message, while a successful
push followed by a delivery-result persistence failure is logged as a persistence
error and is never rewritten as a failed push. In the latter case the delivery log
can remain pending even though the participant may already have received LINE.
The external push is not retried. Notification completion is still attempted to
preserve the existing flow, with an explicit warning that one or more delivery
results may remain pending.

## Phase 7 history archive boundary

`storage/history_archives.py` owns the archive-only operations
`put_history_archive`, `get_history_archive`, `list_history_archives`, and
`delete_history_archive`. It does not extend the relational `Storage` interface.
`storage/history_archive_provider.py` selects `filesystem` by default or `r2`
when `HISTORY_ARCHIVE_BACKEND=r2` is explicit.

The filesystem adapter is the EC2/development compatibility backend and is the
only production module that reads or writes `instance/history_dumps/`. The R2
adapter requires the request-local `request.environ["workers.env"].HISTORY_ARCHIVES`
binding and bridges its promises with `pyodide.ffi.run_sync`. A missing R2 binding
fails closed; it never falls back to the Worker filesystem. A deployment that
selects R2 must declare a private binding similar to:

```json
{
  "vars": {"HISTORY_ARCHIVE_BACKEND": "r2"},
  "r2_buckets": [{
    "binding": "HISTORY_ARCHIVES",
    "bucket_name": "<deployment-specific-private-bucket>"
  }]
}
```

Archive filenames and schema version 1 remain unchanged. R2 keys are
`history_dumps/YYYY/MM/<existing-filename>`, and JSON is serialized exactly once
as indented UTF-8 with non-ASCII characters preserved. The same bytes are sent to
R2/filesystem and handed to the unchanged SMTP transport; the mail helper no
longer needs an archive path. R2 list follows every `truncated`/`cursor` page and
normalizes `uploaded` to an aware UTC `modified_at`. Missing cursors, repeated
cursors, and cursor cycles on truncated responses fail closed instead of looping.

Archive list metadata intentionally preserves the existing behavior of reading
each JSON object. On R2 this is one list operation (possibly multiple pages) plus
one get per object. A D1 metadata index is deferred. History dumps are expected to
remain small JSON objects, so Phase 7 buffers one complete object in memory.

The external/cross-store sequence is:

```text
build archive JSON bytes
-> archive storage put (filesystem or R2)
-> relational history clear when requested (SQLite or D1)
-> optional SMTP email using the retained bytes
```

R2 and D1/SQLite cannot participate in one transaction. Compatibility therefore
keeps the existing best-effort behavior: an archive put failure is reported but
does not prevent `dump_and_clear` or `reset_db` from continuing their relational
clear; an email failure does not remove the stored archive or restore cleared
rows. Existing local archives, production bucket creation/upload, archive data
migration, SMTP migration, and production cutover remain deferred.

The isolated Wrangler 4.131.1 R2 check uses only synthetic data and local
`--persist-to` state. Its Worker imports the production
`R2HistoryArchiveStorage` adapter and exercises its `pyodide.ffi.run_sync`
put/get/list/delete path, including `arrayBuffer`, uploaded/size metadata,
forced cursor pagination, and single-key deletion:

```bash
python tests/run_phase7_wrangler_local.py \
  cloudflare-d1-binding-poc/node_modules/.bin/wrangler
```

The dedicated local D1 check is:

```bash
python tests/run_phase6_wrangler_local.py \
  cloudflare-d1-binding-poc/node_modules/.bin/wrangler
```

It applies migrations 0001–0004 to disposable local state and checks constraints,
subscription upsert, concurrent token consumption, concurrent reservation,
next-match reservation, delivery logging, and completion. It never calls LINE or
a production D1 database. R2 history dumps, SMTP migration, production data
migration, and D1 cutover remain later work.

## Phase 8 full application reset boundary

`data/full_reset.py::reset_all_application_data_atomic` owns the destructive
`/admin/reset_db` database operation. In FK-safe child-to-parent order it deletes
all rows from the five LINE notification tables, match/bench history, rounds,
sessions, and participants. The same SQLite transaction or D1 `batch()` also
CAS-updates both fixed `runtime_state` rows. `current_match` becomes the compatible
empty match shape with `session_id=null` and a local-aware timestamp;
`current_draft.state_json` becomes `NULL`. Both versions increment from their
current values. The runtime rows themselves are never deleted, and `app_config`
is neither selected nor mutated by this command.

The route performs a bounded three-attempt retry only for a runtime version
conflict. Each retry reads fresh versions and submits a complete atomic batch;
provider errors other than the intentional CAS unique failure are not retried.
The FK delete order is:

```text
notification_delivery_logs -> match_notifications
-> notification_subscriptions -> line_link_tokens -> line_accounts
-> bench_histories -> match_histories -> match_rounds
-> match_sessions -> participants
```

The externally observable sequence remains three distinct failure domains:

```text
archive put attempt                 (filesystem or R2; best effort)
-> relational + runtime full reset (one SQLite transaction or D1 batch)
-> optional SMTP                   (only after archive and reset success)
```

An archive write failure does not block the reset. An atomic reset failure may
leave the newly written archive, but leaves every relational and runtime row
unchanged and skips email. An email failure cannot roll back the completed reset
or remove its archive. Existing archives are never deleted. Concurrent writes
are ordered by the database transaction: a participant inserted before the reset
transaction is deleted; one inserted after it commits remains. No long-lived
registration lock is introduced.

The local D1 verification applies migrations 0001–0004, seeds all ten relational
tables plus both runtime rows and `app_config/main`, enables foreign keys, and
checks the happy path, stale-CAS rollback, and an injected middle-statement
rollback using Wrangler 4.131.1:

The production Python command is covered by the shared SQLiteStorage/D1Storage
contract tests. The Wrangler harness uses equivalent SQL to validate actual local
D1 transaction, CAS, rollback, and foreign-key semantics.

```bash
python tests/run_phase8_wrangler_local.py \
  cloudflare-d1-binding-poc/node_modules/.bin/wrangler
```

No Phase 8 schema migration is required. Production D1/R2 cutover, production
data/archive migration, and SMTP transport migration remain deferred.
