"""Backend selection and request-local lifetime for history archives."""

import os

from flask import current_app, g, has_app_context, has_request_context, request

from storage.history_archives import (
    FilesystemHistoryArchiveStorage,
    HistoryArchiveStorageError,
    R2HistoryArchiveStorage,
)


_NOT_PROVIDED = object()
_STORAGE_KEY = "_history_archive_storage_instance"


def selected_history_archive_backend(explicit_backend=None):
    if explicit_backend is not None:
        selected = explicit_backend
    elif has_app_context() and current_app.config.get("HISTORY_ARCHIVE_BACKEND"):
        selected = current_app.config["HISTORY_ARCHIVE_BACKEND"]
    else:
        selected = os.environ.get("HISTORY_ARCHIVE_BACKEND") or "filesystem"
    return str(selected).strip().lower()


def create_history_archive_storage(
    backend=None, *, binding=_NOT_PROVIDED, directory=None, run_sync=_NOT_PROVIDED
):
    selected = selected_history_archive_backend(backend)
    if selected == "filesystem":
        if directory is None:
            if not has_app_context():
                raise HistoryArchiveStorageError()
            directory = os.path.join(current_app.instance_path, "history_dumps")
        return FilesystemHistoryArchiveStorage(directory)

    if selected == "r2":
        if binding is _NOT_PROVIDED:
            if not has_request_context():
                raise HistoryArchiveStorageError()
            workers_env = request.environ.get("workers.env")
            binding = (
                getattr(workers_env, "HISTORY_ARCHIVES", None)
                if workers_env is not None else None
            )
        if run_sync is _NOT_PROVIDED:
            return R2HistoryArchiveStorage(binding)
        return R2HistoryArchiveStorage(binding, run_sync=run_sync)

    raise HistoryArchiveStorageError()


def get_history_archive_storage():
    if not has_request_context():
        raise HistoryArchiveStorageError()
    storage = g.get(_STORAGE_KEY)
    if storage is None:
        storage = create_history_archive_storage()
        setattr(g, _STORAGE_KEY, storage)
    return storage
