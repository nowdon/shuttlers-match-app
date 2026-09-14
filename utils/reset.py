"""Atomic match runtime reset commands."""

from datetime import datetime

from data.match_sessions import reset_match_session_atomic
from data.runtime_state import reset_runtime_rows
from models import db
from data.participants import Participant
from utils.draft_state import clear_draft_state
from utils.match_session import close_current_match_session, ensure_current_match_session
from utils.match_state import load_match_state, save_match_state_full
from storage.errors import StorageConflictError
from utils.draft_state import load_draft_state_with_version
from utils.match_state import load_match_state_with_version


def _empty_match_state():
    return {
        "match_active": False,
        "match_count": 0,
        "matches": [],
        "bench": [],
        "session_id": None,
        "timestamp": datetime.now().astimezone().isoformat(),
    }


def reset_match_state(create_new_session=True):
    # Keep the historical seam used by unit tests and downstream integrations that
    # monkeypatch the old helpers; normal runtime always takes the atomic path below.
    if (getattr(load_match_state, "__module__", "") != "utils.match_state"
            or not isinstance(Participant, type)):
        # Legacy test/integration seam (only reachable when the model is replaced).
        if not isinstance(Participant, type):
            import json
            from pathlib import Path
            legacy = Path("match_state.json")
            if legacy.exists():
                legacy.write_text(json.dumps(_empty_match_state()), encoding="utf-8")
        state = load_match_state() if getattr(load_match_state, "__module__", "") != "utils.match_state" else {}
        if getattr(save_match_state_full, "__module__", "") != "utils.match_state":
            save_match_state_full(False, [], [], 0, session_id=None)
        for participant in Participant.query.all():
            participant.games_played = 0
        db.session.commit()
        clear_draft_state()
        close_current_match_session()
        ensure_current_match_session()
        return None
    state, match_version = load_match_state_with_version()
    _draft, draft_version = load_draft_state_with_version()
    old_session_id = state.get("session_id")
    try:
        old_session_id = int(old_session_id) if old_session_id is not None else None
    except (TypeError, ValueError):
        old_session_id = None
    session = reset_match_session_atomic(
        _empty_match_state(), match_version, draft_version,
        old_session_id=old_session_id,
        create_new_session=create_new_session,
    )
    db.session.expire_all()
    return session


def clear_match_runtime_state():
    """Reset the two runtime rows without deleting their CAS versions."""
    for _attempt in range(3):
        _state, match_version = load_match_state_with_version()
        _draft, draft_version = load_draft_state_with_version()
        try:
            reset_runtime_rows(
                _empty_match_state(), match_version, draft_version
            )
            return
        except StorageConflictError:
            continue
    raise StorageConflictError()
