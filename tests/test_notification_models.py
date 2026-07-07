from datetime import timedelta

import pytest
from flask import Flask
from sqlalchemy.exc import IntegrityError

from models import (
    db,
    LineAccount,
    LineLinkToken,
    MatchSession,
    NotificationDeliveryLog,
    NotificationSubscription,
    Participant,
    utc_now,
)


@pytest.fixture
def app_context(tmp_path):
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'test.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def participant(app_context):
    player = Participant(
        name="line-player",
        gender="male",
        level="beginner",
        weight=1.0,
        card="C1",
    )
    db.session.add(player)
    db.session.commit()
    return player


def add_participant(card="C2"):
    player = Participant(
        name=f"line-player-{card}",
        gender="female",
        level="beginner",
        weight=1.0,
        card=card,
    )
    db.session.add(player)
    db.session.commit()
    return player


def add_session(match_count=0):
    session = MatchSession(match_count=match_count)
    db.session.add(session)
    db.session.commit()
    return session


def assert_integrity_error(record):
    db.session.add(record)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_match_session_can_be_created(app_context):
    session = MatchSession()
    db.session.add(session)
    db.session.commit()

    saved = db.session.get(MatchSession, session.id)
    assert saved.status == "draft"
    assert saved.match_count == 0
    assert saved.created_at is not None
    assert saved.confirmed_at is None
    assert saved.notification_sent_at is None


def test_line_account_can_be_created_for_participant(app_context, participant):
    account = LineAccount(
        participant_id=participant.id,
        line_user_id="U1234567890",
        display_name="Line Player",
    )
    db.session.add(account)
    db.session.commit()

    saved = db.session.get(LineAccount, account.id)
    assert saved.participant == participant
    assert participant.line_account == saved
    assert saved.line_user_id == "U1234567890"
    assert saved.active is True
    assert saved.created_at is not None
    assert saved.updated_at is not None


def test_line_account_participant_id_is_unique(app_context, participant):
    db.session.add(LineAccount(participant_id=participant.id, line_user_id="U111"))
    db.session.commit()

    assert_integrity_error(
        LineAccount(participant_id=participant.id, line_user_id="U222")
    )


def test_line_account_line_user_id_is_unique(app_context, participant):
    another_participant = add_participant()
    db.session.add(LineAccount(participant_id=participant.id, line_user_id="U111"))
    db.session.commit()

    assert_integrity_error(
        LineAccount(participant_id=another_participant.id, line_user_id="U111")
    )


def test_notification_subscription_can_be_created_per_session_participant_channel(
    app_context, participant
):
    session = add_session(match_count=1)
    subscription = NotificationSubscription(
        session_id=session.id,
        participant_id=participant.id,
        channel="line",
    )
    db.session.add(subscription)
    db.session.commit()

    saved = db.session.get(NotificationSubscription, subscription.id)
    assert saved.session == session
    assert saved.participant == participant
    assert saved.channel == "line"
    assert saved.active is True
    assert saved in session.subscriptions
    assert saved in participant.notification_subscriptions


def test_notification_subscription_cannot_duplicate_session_participant_channel(
    app_context, participant
):
    session = add_session(match_count=1)
    db.session.add(
        NotificationSubscription(
            session_id=session.id, participant_id=participant.id, channel="line"
        )
    )
    db.session.commit()

    assert_integrity_error(
        NotificationSubscription(
            session_id=session.id, participant_id=participant.id, channel="line"
        )
    )


def test_notification_subscription_allows_same_participant_channel_for_different_session(
    app_context, participant
):
    first_session = add_session(match_count=1)
    second_session = add_session(match_count=2)
    db.session.add_all(
        [
            NotificationSubscription(
                session_id=first_session.id,
                participant_id=participant.id,
                channel="line",
            ),
            NotificationSubscription(
                session_id=second_session.id,
                participant_id=participant.id,
                channel="line",
            ),
        ]
    )
    db.session.commit()

    subscriptions = NotificationSubscription.query.order_by(
        NotificationSubscription.session_id
    ).all()
    assert [subscription.session_id for subscription in subscriptions] == [
        first_session.id,
        second_session.id,
    ]


def test_line_link_token_can_be_created_for_participant_and_session(
    app_context, participant
):
    session = add_session(match_count=1)
    token = LineLinkToken(
        token="ABC123",
        participant_id=participant.id,
        session_id=session.id,
        expires_at=utc_now() + timedelta(minutes=10),
    )
    db.session.add(token)
    db.session.commit()

    saved = db.session.get(LineLinkToken, token.id)
    assert saved.participant == participant
    assert saved.session == session
    assert saved.used_at is None
    assert saved.expires_at is not None
    assert saved in participant.line_link_tokens
    assert saved in session.link_tokens


def test_notification_delivery_log_can_be_created_for_participant_and_session(
    app_context, participant
):
    session = add_session(match_count=1)
    log = NotificationDeliveryLog(
        session_id=session.id,
        participant_id=participant.id,
        channel="line",
        status="success",
    )
    db.session.add(log)
    db.session.commit()

    saved = db.session.get(NotificationDeliveryLog, log.id)
    assert saved.participant == participant
    assert saved.session == session
    assert saved.channel == "line"
    assert saved.status == "success"
    assert saved.sent_at is not None
    assert saved in participant.notification_delivery_logs
    assert saved in session.delivery_logs
