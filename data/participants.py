"""Database reads returning ORM objects; no commits or request handling."""

from models import Participant


def get_participant_by_card(card):
    return Participant.query.filter_by(card=card).first()


def get_all_participants():
    return Participant.query.all()


def get_participants_ordered_by_card():
    return Participant.query.order_by(Participant.card).all()


def get_participants_by_ids(participant_ids):
    return Participant.query.filter(Participant.id.in_(participant_ids)).all()


def get_active_participants():
    return Participant.query.filter_by(active=True).all()
