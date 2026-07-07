import importlib
import json
import os
import sys
import pytest


def load_test_app(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"paypay_links": {"adults": "#", "students": "#"}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("app", None)
    app_module = importlib.import_module("app")
    os.makedirs(app_module.app.instance_path, exist_ok=True)
    app_module.app.config.update(TESTING=True)
    with app_module.app.app_context():
        app_module.db.drop_all()
        app_module.db.create_all()
    return app_module


@pytest.fixture
def app_module(monkeypatch, tmp_path):
    return load_test_app(monkeypatch, tmp_path)


@pytest.fixture
def participant(app_module):
    with app_module.app.app_context():
        player = app_module.Participant(
            name="line-player",
            gender="male",
            level="beginner",
            weight=1.0,
            card="C1",
        )
        app_module.db.session.add(player)
        app_module.db.session.commit()
        return player.id


def get_participant(app_module, participant_id):
    return app_module.db.session.get(app_module.Participant, participant_id)


def add_line_account(app_module, participant_id, active=True):
    account = app_module.LineAccount(
        participant_id=participant_id,
        line_user_id=f"U{participant_id}{active}",
        display_name="Line Player",
        active=active,
    )
    app_module.db.session.add(account)
    app_module.db.session.commit()
    return account


def add_session(app_module, match_count=0):
    session = app_module.MatchSession(match_count=match_count)
    app_module.db.session.add(session)
    app_module.db.session.commit()
    return session


def write_current_session(tmp_path, session_id):
    (tmp_path / "match_state.json").write_text(
        json.dumps({"session_id": session_id}), encoding="utf-8"
    )


def test_unlinked_participant_start_creates_line_link_token_for_current_session(
    app_module, participant, tmp_path
):
    client = app_module.app.test_client()

    with app_module.app.app_context():
        response = client.get("/notifications/line/start/C1")

        assert response.status_code == 200
        token = app_module.LineLinkToken.query.one()
        current_session_id = app_module.ensure_current_match_session().id
        assert token.participant_id == participant
        assert token.session_id == current_session_id
        assert token.expires_at is not None
        assert app_module.NotificationSubscription.query.count() == 0


def test_linked_participant_start_creates_subscription_for_current_session(
    app_module, participant
):
    client = app_module.app.test_client()

    with app_module.app.app_context():
        add_line_account(app_module, participant)
        response = client.get("/notifications/line/start/C1")

        assert response.status_code == 302
        subscription = app_module.NotificationSubscription.query.one()
        assert subscription.participant_id == participant
        assert subscription.session_id == app_module.ensure_current_match_session().id
        assert subscription.channel == "line"
        assert subscription.active is True
        assert app_module.LineLinkToken.query.count() == 0


def test_same_session_registration_is_idempotent(app_module, participant):
    client = app_module.app.test_client()

    with app_module.app.app_context():
        add_line_account(app_module, participant)
        client.get("/notifications/line/start/C1")
        client.get("/notifications/line/start/C1")

        assert app_module.NotificationSubscription.query.count() == 1
        assert app_module.NotificationSubscription.query.one().active is True


def test_inactive_subscription_is_reactivated(app_module, participant):
    client = app_module.app.test_client()

    with app_module.app.app_context():
        add_line_account(app_module, participant)
        current_session = app_module.ensure_current_match_session()
        subscription = app_module.NotificationSubscription(
            session_id=current_session.id,
            participant_id=participant,
            channel="line",
            active=False,
        )
        app_module.db.session.add(subscription)
        app_module.db.session.commit()

        client.get("/notifications/line/start/C1")

        assert app_module.NotificationSubscription.query.count() == 1
        assert app_module.NotificationSubscription.query.one().active is True


def test_unsubscribe_only_deactivates_current_session_subscription_and_keeps_line_account(
    app_module, participant, tmp_path
):
    client = app_module.app.test_client()

    with app_module.app.app_context():
        add_line_account(app_module, participant)
        past_session = add_session(app_module, match_count=1)
        current_session = add_session(app_module, match_count=2)
        write_current_session(tmp_path, current_session.id)
        past_subscription = app_module.NotificationSubscription(
            session_id=past_session.id,
            participant_id=participant,
            channel="line",
            active=True,
        )
        current_subscription = app_module.NotificationSubscription(
            session_id=current_session.id,
            participant_id=participant,
            channel="line",
            active=True,
        )
        app_module.db.session.add_all([past_subscription, current_subscription])
        app_module.db.session.commit()

        response = client.post("/notifications/line/unsubscribe/C1")

        assert response.status_code == 302
        assert app_module.db.session.get(app_module.LineAccount, 1) is not None
        assert app_module.db.session.get(
            app_module.NotificationSubscription, past_subscription.id
        ).active is True
        assert app_module.db.session.get(
            app_module.NotificationSubscription, current_subscription.id
        ).active is False


def test_past_subscription_does_not_count_as_current_session_registered(
    app_module, participant, tmp_path
):
    with app_module.app.app_context():
        player = get_participant(app_module, participant)
        add_line_account(app_module, participant)
        past_session = add_session(app_module, match_count=1)
        current_session = add_session(app_module, match_count=2)
        write_current_session(tmp_path, current_session.id)
        app_module.db.session.add(
            app_module.NotificationSubscription(
                session_id=past_session.id,
                participant_id=participant,
                channel="line",
                active=True,
            )
        )
        app_module.db.session.commit()

        status = app_module.get_line_notification_status(player, current_session)

        assert status["state"] == "linked_past_session_only"
        assert status["current_subscription_active"] is False
        assert status["has_past_subscription"] is True


def test_thanks_page_line_notification_display_branches(app_module, participant):
    client = app_module.app.test_client()

    with app_module.app.app_context():
        unlinked_html = client.get("/thanks?card=C1").get_data(as_text=True)
        assert "LINE連携して通知を受け取る" in unlinked_html

        add_line_account(app_module, participant)
        linked_html = client.get("/thanks?card=C1").get_data(as_text=True)
        assert "今回のLINE通知を受け取る" in linked_html
        assert "LINE通知登録済み" not in linked_html

        client.get("/notifications/line/start/C1")
        subscribed_html = client.get("/thanks?card=C1").get_data(as_text=True)
        assert "LINE通知登録済み" in subscribed_html
        assert "LINE通知を解除する" in subscribed_html


def test_inactive_linked_participant_start_does_not_create_current_subscription(
    app_module, participant
):
    client = app_module.app.test_client()

    with app_module.app.app_context():
        player = get_participant(app_module, participant)
        player.active = False
        add_line_account(app_module, participant)
        app_module.db.session.commit()

        response = client.get("/notifications/line/start/C1", follow_redirects=True)

        assert response.status_code == 200
        assert "現在参加中の方のみLINE通知登録できます" in response.get_data(
            as_text=True
        )
        assert app_module.NotificationSubscription.query.count() == 0
        assert app_module.LineLinkToken.query.count() == 0


def test_inactive_unlinked_participant_start_does_not_create_line_link_token(
    app_module, participant
):
    client = app_module.app.test_client()

    with app_module.app.app_context():
        player = get_participant(app_module, participant)
        player.active = False
        app_module.db.session.commit()

        response = client.get("/notifications/line/start/C1", follow_redirects=True)

        assert response.status_code == 200
        assert "現在参加中の方のみLINE通知登録できます" in response.get_data(
            as_text=True
        )
        assert app_module.LineLinkToken.query.count() == 0
        assert app_module.NotificationSubscription.query.count() == 0


def test_inactive_participant_thanks_page_hides_line_notification_buttons(
    app_module, participant
):
    client = app_module.app.test_client()

    with app_module.app.app_context():
        player = get_participant(app_module, participant)
        player.active = False
        app_module.db.session.commit()

        html = client.get("/thanks?card=C1").get_data(as_text=True)

        assert "現在参加中の方のみLINE通知登録できます" in html
        assert "LINE連携して通知を受け取る" not in html
        assert "今回のLINE通知を受け取る" not in html
        assert "LINE通知を解除する" not in html


def test_inactive_participant_with_past_subscription_is_not_registered_for_current_session(
    app_module, participant, tmp_path
):
    client = app_module.app.test_client()

    with app_module.app.app_context():
        player = get_participant(app_module, participant)
        player.active = False
        add_line_account(app_module, participant)
        past_session = add_session(app_module, match_count=1)
        current_session = add_session(app_module, match_count=2)
        write_current_session(tmp_path, current_session.id)
        app_module.db.session.add(
            app_module.NotificationSubscription(
                session_id=past_session.id,
                participant_id=participant,
                channel="line",
                active=True,
            )
        )
        app_module.db.session.commit()

        response = client.get("/notifications/line/start/C1", follow_redirects=True)

        assert response.status_code == 200
        subscriptions = app_module.NotificationSubscription.query.order_by(
            app_module.NotificationSubscription.session_id
        ).all()
        assert len(subscriptions) == 1
        assert subscriptions[0].session_id == past_session.id
        assert app_module.NotificationSubscription.query.filter_by(
            session_id=current_session.id,
            participant_id=participant,
            channel="line",
        ).first() is None


def test_line_webhook_and_send_routes_are_not_implemented(app_module):
    client = app_module.app.test_client()

    assert client.post("/line/webhook").status_code == 404
    assert client.post("/notifications/line/send").status_code == 404
