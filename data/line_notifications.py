"""Database reads returning ORM objects; no commits or request handling."""

from sqlalchemy.orm import selectinload

from models import (
    LineAccount,
    LineLinkToken,
    MatchNotification,
    NotificationSubscription,
    Participant,
    db,
)


def get_notification_subscription(participant_id, session_id):
    """Include inactive subscriptions so the caller can reactivate them."""
    return (
        NotificationSubscription.query
        .filter_by(session_id=session_id, participant_id=participant_id, channel="line")
        .first()
    )


def get_line_notification_targets(session_id):
    return (
        db.session.query(Participant, LineAccount)
        .join(
            NotificationSubscription,
            NotificationSubscription.participant_id == Participant.id,
        )
        .join(LineAccount, LineAccount.participant_id == Participant.id)
        .filter(
            NotificationSubscription.session_id == session_id,
            NotificationSubscription.channel == "line",
            NotificationSubscription.active.is_(True),
            LineAccount.active.is_(True),
            Participant.active.is_(True),
        )
        .all()
    )


def get_line_link_token_with_details(token_value):
    return (
        LineLinkToken.query
        .options(selectinload(LineLinkToken.participant), selectinload(LineLinkToken.session))
        .filter_by(token=token_value)
        .first()
    )


def get_conflicting_line_account(line_user_id, participant_id):
    return (
        LineAccount.query
        .filter(
            LineAccount.line_user_id == line_user_id,
            LineAccount.participant_id != participant_id,
        )
        .first()
    )


def get_line_account_for_participant(participant_id):
    return LineAccount.query.filter_by(participant_id=participant_id).first()


def get_past_line_subscription(participant_id, session_id):
    """Find any other session subscription, including an inactive one."""
    return (
        NotificationSubscription.query
        .filter(
            NotificationSubscription.participant_id == participant_id,
            NotificationSubscription.channel == "line",
            NotificationSubscription.session_id != session_id,
        )
        .first()
    )


def get_line_match_notification(session_id, match_count):
    """Find an existing notification regardless of delivery status."""
    return (
        MatchNotification.query
        .filter_by(session_id=session_id, match_count=match_count, channel="line")
        .first()
    )


def get_line_link_token(token_value):
    return LineLinkToken.query.filter_by(token=token_value).first()
