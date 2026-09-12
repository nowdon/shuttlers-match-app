"""Backend selection and request-local storage lifetime."""

import os
from pathlib import Path

from flask import current_app, g, has_app_context, has_request_context, request

from storage.d1 import D1Storage
from storage.errors import StorageUnavailableError
from storage.sqlite import SQLiteStorage


_NOT_PROVIDED = object()
_STORAGE_KEY = "_storage_provider_instance"


def _selected_backend(explicit_backend=None):
    if explicit_backend is not None:
        return explicit_backend
    if has_app_context():
        configured = current_app.config.get("STORAGE_BACKEND")
        if configured:
            return configured
    return os.environ.get("STORAGE_BACKEND") or "sqlite"


def create_storage(
    backend=None,
    *,
    binding=_NOT_PROVIDED,
    database_path=None,
    run_sync=_NOT_PROVIDED,
):
    """Create a storage adapter without caching it globally or on ``flask.g``."""
    selected = str(_selected_backend(backend)).strip().lower()
    if selected == "sqlite":
        if database_path is None:
            if not has_app_context():
                raise StorageUnavailableError()
            database_path = Path(current_app.instance_path) / "participants.db"
        return SQLiteStorage(database_path)

    if selected == "d1":
        if binding is _NOT_PROVIDED:
            if not has_request_context():
                raise StorageUnavailableError()
            workers_env = request.environ.get("workers.env")
            binding = getattr(workers_env, "DB", None) if workers_env is not None else None
        if run_sync is _NOT_PROVIDED:
            return D1Storage(binding)
        return D1Storage(binding, run_sync=run_sync)

    raise StorageUnavailableError()


def get_storage():
    """Return one storage adapter per Flask request."""
    if not has_request_context():
        raise StorageUnavailableError()
    storage = g.get(_STORAGE_KEY)
    if storage is None:
        storage = create_storage()
        setattr(g, _STORAGE_KEY, storage)
    return storage


def close_storage(_exception=None):
    """Close and discard the current request's adapter, if one was created."""
    storage = g.pop(_STORAGE_KEY, None)
    if storage is not None:
        storage.close()
