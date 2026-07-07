from models import MatchSession, db
from utils.match_state import load_match_state, save_match_state


def get_current_session_id():
    """Return the current match_state.json session_id, or None if absent/invalid."""
    session_id = load_match_state().get("session_id")
    if session_id is None:
        return None
    try:
        return int(session_id)
    except (TypeError, ValueError):
        return None


def get_current_match_session():
    """Load the current MatchSession referenced by match_state.json."""
    session_id = get_current_session_id()
    if session_id is None:
        return None
    return db.session.get(MatchSession, session_id)


def ensure_current_match_session():
    """Return the current MatchSession, creating and storing one when needed."""
    current_session = get_current_match_session()
    if current_session is not None:
        return current_session

    current_session = MatchSession(status="draft")
    db.session.add(current_session)
    db.session.commit()

    state = load_match_state()
    state["session_id"] = current_session.id
    save_match_state(state)
    return current_session


def close_current_match_session():
    """Mark the current MatchSession closed when one exists."""
    current_session = get_current_match_session()
    if current_session is None:
        return None
    if current_session.status != "closed":
        current_session.status = "closed"
    db.session.commit()
    return current_session
