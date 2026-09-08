"""Match session lookup; lifecycle and JSON coordination stay in utils."""

from models import MatchSession, db


def get_match_session_by_id(session_id):
    return db.session.get(MatchSession, session_id)
