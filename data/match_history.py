"""Database reads returning ORM objects; no commits or request handling."""

from sqlalchemy.orm import selectinload

from models import MatchHistory, MatchRound, db


def get_latest_match_round_with_matches(round_number):
    """Choose the highest id for this round number, not the newest timestamp."""
    return (
        MatchRound.query
        .options(selectinload(MatchRound.matches))
        .filter_by(round_number=round_number)
        .order_by(MatchRound.id.desc())
        .first()
    )


def get_match_rounds_for_dump():
    """Oldest timestamp first, breaking ties by id; load matches and bench."""
    return (
        MatchRound.query
        .options(selectinload(MatchRound.matches), selectinload(MatchRound.bench_players))
        .order_by(MatchRound.created_at.asc(), MatchRound.id.asc())
        .all()
    )


def get_latest_match_round(round_number):
    """Lookup for revert; retain the original lazy relationship loading."""
    return (
        MatchRound.query
        .filter_by(round_number=round_number)
        .order_by(MatchRound.id.desc())
        .first()
    )


def get_match_round_with_matches(round_id):
    return (
        MatchRound.query
        .options(selectinload(MatchRound.matches))
        .filter_by(id=round_id)
        .first()
    )


def get_match_history_by_id(match_history_id):
    return db.session.get(MatchHistory, match_history_id)


def get_match_rounds_with_details():
    """Newest timestamp first, breaking ties by id; load matches and bench."""
    return (
        MatchRound.query
        .options(selectinload(MatchRound.matches), selectinload(MatchRound.bench_players))
        .order_by(MatchRound.created_at.desc(), MatchRound.id.desc())
        .all()
    )
