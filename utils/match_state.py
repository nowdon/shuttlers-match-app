"""Public compatibility helpers for the DB-backed confirmed runtime state."""

import copy
from datetime import datetime

_UNSET = object()


def load_match_state_with_version(*, storage=None):
    from data.runtime_state import load_current_match
    record = load_current_match(storage=storage)
    return copy.deepcopy(record.state), record.version


def load_match_state(*, storage=None):
    state, _version = load_match_state_with_version(storage=storage)
    return state


def save_match_state(state, *, expected_version=None, storage=None):
    from data.runtime_state import save_current_match
    if expected_version is None:
        _current, expected_version = load_match_state_with_version(storage=storage)
    return save_current_match(state, expected_version, storage=storage).state


def build_match_state(
    match_active, matches, bench, match_count, *, court_count=None,
    session_id=_UNSET, existing_state=None,
):
    if existing_state is None:
        from data.runtime_state import DEFAULT_MATCH_STATE
        existing_state = DEFAULT_MATCH_STATE
    state = {
        "match_active": match_active,
        "match_count": match_count,
        "matches": matches,
        "bench": bench,
        "timestamp": datetime.now().astimezone().isoformat(),
    }
    if isinstance(court_count, int) and court_count > 0:
        state["court_count"] = court_count
    if session_id is _UNSET:
        session_id = existing_state.get("session_id")
    if session_id is not None:
        state["session_id"] = session_id
    return state


def save_match_state_full(
    match_active, matches, bench, match_count, *, court_count=None,
    session_id=_UNSET, expected_version=None, storage=None,
):
    existing_state, loaded_version = load_match_state_with_version(storage=storage)
    if expected_version is None:
        expected_version = loaded_version
    state = build_match_state(
        match_active, matches, bench, match_count,
        court_count=court_count, session_id=session_id,
        existing_state=existing_state,
    )
    return save_match_state(state, expected_version=expected_version, storage=storage)
