"""Regression coverage for filters, ordering and eager loads at the DB boundary."""
from datetime import datetime, timedelta

import pytest
from flask import Flask
from sqlalchemy import inspect

from data import line_notifications as line
from data import match_history as history
from data import participants
from data.match_sessions import get_match_session_by_id
from models import (
    BenchHistory, LineAccount, LineLinkToken, MatchHistory, MatchNotification,
    MatchRound, MatchSession, NotificationSubscription, Participant, db,
)


@pytest.fixture
def database():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield
        db.session.remove()
        db.drop_all()


def add_participant(card, active=True):
    participant = Participant(
        name=f'player-{card}', card=card, active=active,
        gender='male', level='beginner', weight=1.0,
    )
    db.session.add(participant)
    db.session.flush()
    return participant


def test_participant_filters_card_lookup_and_ordering(database):
    second = add_participant('C2')
    first = add_participant('C1', active=False)
    assert {p.id for p in participants.get_all_participants()} == {first.id, second.id}
    assert participants.get_active_participants() == [second]
    assert participants.get_participants_ordered_by_card() == [first, second]
    assert participants.get_participant_by_card('C1') is first
    assert participants.get_participant_by_card('missing') is None
    assert participants.get_participants_by_ids([first.id, first.id, 999]) == [first]
    assert participants.get_participants_by_ids([]) == []


def test_round_ordering_latest_by_id_and_eager_loading(database):
    now = datetime(2026, 1, 2)
    # Latest id intentionally has an older timestamp, and an unrelated round
    # has a higher id. Latest-for-number must use id, not timestamp/global max.
    rounds = [
        MatchRound(round_number=7, created_at=now),
        MatchRound(round_number=7, created_at=now - timedelta(days=1)),
        MatchRound(round_number=8, created_at=now),
    ]
    db.session.add_all(rounds)
    player = add_participant('C1')
    match = MatchHistory(
        round_id=rounds[1].id, court_number=1,
        team1_player1_id=player.id, team1_player2_id=player.id,
        team2_player1_id=player.id, team2_player2_id=player.id,
    )
    db.session.add_all([match, BenchHistory(round_id=rounds[1].id, participant_id=player.id)])
    db.session.commit()
    ids = [r.id for r in rounds]
    match_id = match.id
    db.session.expunge_all()

    assert history.get_latest_match_round(7).id == ids[1]
    assert history.get_latest_match_round(999) is None
    db.session.expunge_all()
    latest = history.get_latest_match_round_with_matches(7)
    assert latest.id == ids[1]
    assert 'matches' not in inspect(latest).unloaded
    assert [m.id for m in latest.matches] == [match_id]
    db.session.expunge_all()
    by_id = history.get_match_round_with_matches(ids[1])
    assert 'matches' not in inspect(by_id).unloaded
    assert history.get_match_round_with_matches(999) is None
    assert history.get_match_history_by_id(match_id).round_id == ids[1]
    assert history.get_match_history_by_id(999) is None

    for query, expected in (
        (history.get_match_rounds_for_dump, [ids[1], ids[0], ids[2]]),
        (history.get_match_rounds_with_details, [ids[2], ids[0], ids[1]]),
    ):
        db.session.expunge_all()
        result = query()
        assert [r.id for r in result] == expected
        assert all(not {'matches', 'bench_players'} & inspect(r).unloaded for r in result)
        detailed = next(r for r in result if r.id == ids[1])
        assert len(detailed.matches) == len(detailed.bench_players) == 1


@pytest.mark.parametrize('excluded_by', [
    'participant', 'account', 'subscription', 'session', 'channel',
    'missing_account', 'missing_subscription',
])
def test_line_targets_require_every_active_join_and_session_condition(database, excluded_by):
    current, past = MatchSession(), MatchSession()
    db.session.add_all([current, past])
    included = add_participant('C1')
    excluded = add_participant('C2', active=excluded_by != 'participant')
    for p in (included, excluded):
        rejected = p is excluded
        if not (rejected and excluded_by == 'missing_account'):
            db.session.add(LineAccount(
                participant_id=p.id, line_user_id=f'test-{p.id}',
                active=not (rejected and excluded_by == 'account'),
            ))
        if not (rejected and excluded_by == 'missing_subscription'):
            db.session.add(NotificationSubscription(
                participant_id=p.id,
                session_id=past.id if rejected and excluded_by == 'session' else current.id,
                channel='other' if rejected and excluded_by == 'channel' else 'line',
                active=not (rejected and excluded_by == 'subscription'),
            ))
    db.session.commit()
    assert [p.id for p, account in line.get_line_notification_targets(current.id)] == [included.id]


def test_line_lookup_preserves_inactive_past_and_duplicate_scope(database):
    current, past = MatchSession(), MatchSession(status='closed')
    db.session.add_all([current, past])
    player = add_participant('C1')
    other = add_participant('C2')
    account = LineAccount(participant_id=player.id, line_user_id='test-user', active=False)
    subscription = NotificationSubscription(participant_id=player.id, session_id=current.id, active=False)
    old = NotificationSubscription(participant_id=player.id, session_id=past.id, active=False)
    notification = MatchNotification(session_id=current.id, match_count=2, channel='line')
    db.session.add_all([account, subscription, old, notification,
        MatchNotification(session_id=current.id, match_count=3, channel='other'),
        NotificationSubscription(participant_id=other.id, session_id=past.id, channel='other'),
    ])
    db.session.commit()
    assert line.get_notification_subscription(player.id, current.id) is subscription
    assert line.get_notification_subscription(other.id, current.id) is None
    assert line.get_past_line_subscription(player.id, current.id) is old
    assert line.get_past_line_subscription(other.id, current.id) is None
    assert line.get_line_account_for_participant(player.id) is account
    assert line.get_conflicting_line_account('test-user', other.id) is account
    assert line.get_conflicting_line_account('test-user', player.id) is None
    assert line.get_line_match_notification(current.id, 2) is notification
    assert line.get_line_match_notification(past.id, 2) is None
    assert line.get_line_match_notification(current.id, 3) is None


def test_token_lookup_eager_loads_and_session_lookup_does_not_create(database):
    session = MatchSession(status='confirmed', match_count=4)
    db.session.add(session)
    player = add_participant('C1')
    token = LineLinkToken(
        token='ABC123', participant_id=player.id, session_id=session.id,
        expires_at=datetime(2026, 1, 1), used_at=datetime(2025, 12, 31),
    )
    db.session.add(token)
    db.session.commit()
    session_id = session.id
    db.session.expunge_all()
    # Expiry/used checks remain in the caller; reads do not silently filter them.
    found = line.get_line_link_token_with_details('ABC123')
    assert not {'participant', 'session'} & inspect(found).unloaded
    assert found.participant.card == 'C1'
    assert found.session.id == session_id
    assert line.get_line_link_token('ABC123') is found
    assert line.get_line_link_token('missing') is None
    assert line.get_line_link_token_with_details('missing') is None
    assert get_match_session_by_id(session_id).status == 'confirmed'
    assert get_match_session_by_id(999) is None
    assert MatchSession.query.count() == 1
