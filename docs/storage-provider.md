# Storage provider

## Purpose

This Phase 2 boundary provides backend-neutral SQL execution for the staged D1
migration. Existing routes, models, state files, configuration, and LINE flows
continue to use their current implementations; none are migrated in this phase.

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
metadata is not exposed.

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
