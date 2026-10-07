"""Storage-backed command for the administrator full application reset."""

from datetime import datetime

from flask import has_request_context

from data.runtime_state import (
    CURRENT_DRAFT,
    CURRENT_MATCH,
    cas_guard_statement,
    cas_update_statement,
    load_current_draft,
    load_current_match,
)
from storage.errors import StorageConflictError, StorageUniqueError


FULL_RESET_DELETE_ORDER = (
    "notification_delivery_logs",
    "match_notifications",
    "notification_subscriptions",
    "line_link_tokens",
    "line_accounts",
    "bench_histories",
    "match_histories",
    "match_rounds",
    "match_sessions",
    "participants",
)


def _adapter(storage):
    if storage is not None:
        return storage
    from storage.provider import create_storage, get_storage
    return get_storage() if has_request_context() else create_storage()


def _empty_match_state(timestamp=None):
    return {
        "match_active": False,
        "match_count": 0,
        "matches": [],
        "bench": [],
        "session_id": None,
        "timestamp": timestamp or datetime.now().astimezone().isoformat(),
    }


def _full_reset_statements(
    expected_match_version, expected_draft_version, *, timestamp=None
):
    statements = [
        cas_guard_statement(CURRENT_MATCH, expected_match_version),
        cas_guard_statement(CURRENT_DRAFT, expected_draft_version),
    ]
    statements.extend((f"DELETE FROM {table}", ()) for table in FULL_RESET_DELETE_ORDER)
    statements.append(cas_update_statement(
        CURRENT_MATCH,
        _empty_match_state(timestamp),
        expected_match_version,
    ))
    statements.append(cas_update_statement(
        CURRENT_DRAFT,
        None,
        expected_draft_version,
    ))
    return statements


def reset_all_application_data_atomic(
    expected_match_version,
    expected_draft_version,
    *,
    storage=None,
    timestamp=None,
):
    """Delete application rows and reset both runtime rows in one transaction."""
    adapter = _adapter(storage)
    try:
        adapter.batch(_full_reset_statements(
            expected_match_version,
            expected_draft_version,
            timestamp=timestamp,
        ))
    except StorageUniqueError as error:
        # The seeded runtime_state_cas_guard intentionally raises a unique
        # constraint when either expected version is stale.
        raise StorageConflictError() from error


def reset_all_application_data(*, storage=None, max_attempts=3):
    """Retry bounded CAS conflicts while keeping each attempt all-or-nothing."""
    adapter = _adapter(storage)
    for _attempt in range(max_attempts):
        match = load_current_match(storage=adapter)
        draft = load_current_draft(storage=adapter)
        try:
            reset_all_application_data_atomic(
                match.version,
                draft.version,
                storage=adapter,
            )
            return
        except StorageConflictError:
            continue
    raise StorageConflictError()
