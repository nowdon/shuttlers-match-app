"""MatchSession helpers coordinated with DB-backed current_match state."""

from data.match_sessions import (
    close_match_session,
    create_current_match_session_atomic,
    get_match_session_by_id,
)
from storage.errors import StorageConflictError
from utils.match_state import load_match_state, load_match_state_with_version


def get_current_session_id():
    session_id = load_match_state().get("session_id")
    if session_id is None:
        return None
    try:
        return int(session_id)
    except (TypeError, ValueError):
        return None


def get_current_match_session():
    session_id = get_current_session_id()
    if session_id is None:
        return None
    return get_match_session_by_id(session_id)


def ensure_current_match_session():
    """Return the adopted session; concurrent creators leave no orphan session."""
    for _attempt in range(3):
        state, version = load_match_state_with_version()
        session_id = state.get("session_id")
        try:
            session_id = int(session_id) if session_id is not None else None
        except (TypeError, ValueError):
            session_id = None
        current_session = (
            get_match_session_by_id(session_id) if session_id is not None else None
        )
        if current_session is not None and current_session.status != "closed":
            return current_session
        try:
            return create_current_match_session_atomic(state, version)
        except StorageConflictError:
            continue
    raise StorageConflictError()


def close_current_match_session():
    current_session = get_current_match_session()
    if current_session is None:
        return None
    if current_session.status == "closed":
        return current_session
    return close_match_session(current_session.id)
