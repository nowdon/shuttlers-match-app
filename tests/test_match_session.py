import json

from flask import Flask

from models import db, MatchSession, NotificationSubscription, Participant
from utils.match_session import (
    close_current_match_session,
    ensure_current_match_session,
    get_current_match_session,
)
from utils.match_state import load_match_state
from utils.reset import reset_match_state


import pytest


@pytest.fixture
def app_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def write_match_state(tmp_path, state):
    (tmp_path / "match_state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )


def test_ensure_current_match_session_creates_session_and_saves_id(app_context, tmp_path):
    session = ensure_current_match_session()

    assert session.id is not None
    assert session.status == "draft"
    state = json.loads((tmp_path / "match_state.json").read_text(encoding="utf-8"))
    assert state["session_id"] == session.id
    assert state["match_active"] is False
    assert state["matches"] == []


def test_ensure_current_match_session_reuses_existing_session(app_context, tmp_path):
    existing_session = MatchSession(status="draft")
    db.session.add(existing_session)
    db.session.commit()
    write_match_state(
        tmp_path,
        {
            "session_id": existing_session.id,
            "match_active": True,
            "match_count": 2,
            "matches": [[1, 2, 3, 4]],
            "bench": [5],
        },
    )

    session = ensure_current_match_session()

    assert session.id == existing_session.id
    assert MatchSession.query.count() == 1


def test_get_current_match_session_returns_session_for_state_id(app_context, tmp_path):
    existing_session = MatchSession(status="confirmed", match_count=1)
    db.session.add(existing_session)
    db.session.commit()
    write_match_state(tmp_path, {"session_id": existing_session.id})

    assert get_current_match_session().id == existing_session.id


def test_get_current_match_session_returns_none_without_session_id(app_context, tmp_path):
    write_match_state(tmp_path, {"match_active": False, "matches": [], "bench": []})

    assert get_current_match_session() is None


def test_get_current_match_session_returns_none_for_missing_db_session(app_context, tmp_path):
    write_match_state(tmp_path, {"session_id": 999})

    assert get_current_match_session() is None


def test_reset_match_closes_old_session_and_starts_new_session(app_context, tmp_path):
    player = Participant(
        name="player-1",
        gender="male",
        level="beginner",
        weight=1.0,
        card="C1",
        games_played=3,
    )
    old_session = MatchSession(status="draft", match_count=1)
    db.session.add_all([player, old_session])
    db.session.flush()
    subscription = NotificationSubscription(
        session_id=old_session.id,
        participant_id=player.id,
        channel="line",
    )
    db.session.add(subscription)
    db.session.commit()
    write_match_state(
        tmp_path,
        {
            "session_id": old_session.id,
            "match_active": True,
            "match_count": 1,
            "matches": [[player.id, player.id, player.id, player.id]],
            "bench": [],
        },
    )

    reset_match_state()

    db.session.refresh(old_session)
    new_session_id = load_match_state()["session_id"]
    new_session = db.session.get(MatchSession, new_session_id)
    assert old_session.status == "closed"
    assert new_session.id != old_session.id
    assert new_session.status == "draft"
    assert player.games_played == 0
    assert NotificationSubscription.query.count() == 1
    assert NotificationSubscription.query.one().session_id == old_session.id


def test_close_current_match_session_noops_without_current_session(app_context):
    assert close_current_match_session() is None
