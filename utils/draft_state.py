"""Public compatibility helpers for the DB-backed draft runtime state."""

from datetime import datetime

from data.runtime_state import load_current_draft, save_current_draft


def load_draft_state_with_version(*, storage=None):
    record = load_current_draft(storage=storage)
    return record.state, record.version


def load_draft_state(*, storage=None):
    state, _version = load_draft_state_with_version(storage=storage)
    return state


def build_draft_state(matches, bench, draft=True, court_count=None, fixed_pairs=None):
    data = {
        "draft": draft,
        "timestamp": datetime.now().astimezone().isoformat(),
        "matches": matches,
        "bench": bench,
    }
    if court_count is not None:
        data["court_count"] = court_count
    if fixed_pairs is not None:
        data["fixed_pairs"] = fixed_pairs
    return data


def save_draft_state(
    matches, bench, draft=True, court_count=None, fixed_pairs=None, *,
    expected_version=None, storage=None,
):
    if expected_version is None:
        _state, expected_version = load_draft_state_with_version(storage=storage)
    data = build_draft_state(
        matches, bench, draft=draft, court_count=court_count,
        fixed_pairs=fixed_pairs,
    )
    return save_current_draft(data, expected_version, storage=storage).state


def clear_draft_state(*, expected_version=None, storage=None):
    if expected_version is None:
        _state, expected_version = load_draft_state_with_version(storage=storage)
    return save_current_draft(None, expected_version, storage=storage)


def is_active_draft(state):
    return (
        isinstance(state, dict)
        and state.get("draft") is True
        and isinstance(state.get("matches"), list)
        and isinstance(state.get("bench"), list)
    )


def get_active_draft_with_version(*, storage=None):
    state, version = load_draft_state_with_version(storage=storage)
    return (state if is_active_draft(state) else None), version


def get_active_draft(*, storage=None):
    state, _version = get_active_draft_with_version(storage=storage)
    return state
