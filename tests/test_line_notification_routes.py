import importlib
import json
import os
import sys
import hmac
import hashlib
import base64
from datetime import timedelta
import pytest
from conftest import clear_app_modules, patch_app_dependency


def load_test_app(monkeypatch, tmp_path):
    monkeypatch.setenv("LINE_MESSAGING_ENABLED", "1")
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-line-secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-access-token")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "paypay_links": {
                    "adults": "https://example.com/pay/adults",
                    "students": "https://example.com/pay/students",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    clear_app_modules()
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
    app_module, participant, tmp_path, monkeypatch
):
    monkeypatch.setenv("LINE_BOT_FRIEND_URL", "https://lin.ee/example")
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
        html = response.get_data(as_text=True)
        assert "LINE通知登録" in html
        assert "LINEでBotを開く" in html
        assert "https://lin.ee/example" in html
        assert "id=\"line-link-code\"" in html
        assert "id=\"copy-status\"" in html
        assert "コピーしました" in html
        assert "トップへ戻る" in html
        assert "/thanks" not in html


def test_line_link_token_page_without_bot_url_is_safe(
    app_module, participant, monkeypatch
):
    client = app_module.app.test_client()
    monkeypatch.delenv("LINE_BOT_FRIEND_URL", raising=False)

    with app_module.app.app_context():
        response = client.get("/notifications/line/start/C1")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "LINE Bot URLが未設定です" in html
        assert "LINEでBotを開く" not in html
        assert app_module.LineLinkToken.query.count() == 1


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
        assert "まずLINE通知登録をお願いします" in unlinked_html
        assert "組み合わせ確定通知と支払いリンクをLINEで受け取れます" in unlinked_html
        assert "LINE通知を登録する" in unlinked_html
        assert unlinked_html.index("📱 PayPay") > unlinked_html.index("参加費")
        assert unlinked_html.index("支払い完了 → トップに戻る") < unlinked_html.index("🔔 LINE通知")
        assert unlinked_html.index("🔔 LINE通知") > unlinked_html.index("📱 PayPay")
        assert unlinked_html.index("🔔 LINE通知") > unlinked_html.index("社会人：600円")
        assert unlinked_html.index("🔔 LINE通知") > unlinked_html.index("学生：300円")
        assert unlinked_html.index("🔔 LINE通知") < unlinked_html.index("現在のモード")
        assert "LINEを使わない場合、または先に支払う場合はこちら" in unlinked_html

        add_line_account(app_module, participant)
        linked_html = client.get("/thanks?card=C1").get_data(as_text=True)
        assert "LINE連携済みです" in linked_html
        assert "今回の組み合わせ通知を受け取るには通知登録してください" in linked_html
        assert "通知登録後、LINE上で支払いリンクも案内されます" in linked_html
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
        assert "LINE通知を登録する" not in html
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



def line_signature(body, secret="test-line-secret"):
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def post_line_webhook(client, payload, secret="test-line-secret", signature=True):
    body = json.dumps(payload).encode("utf-8")
    headers = {}
    if signature:
        headers["X-Line-Signature"] = line_signature(body, secret)
    return client.post(
        "/line/webhook", data=body, content_type="application/json", headers=headers
    )


def add_link_token(app_module, participant_id, session_id, token="ABC123", minutes=30):
    token_row = app_module.LineLinkToken(
        token=token,
        participant_id=participant_id,
        session_id=session_id,
        expires_at=app_module.utc_now() + timedelta(minutes=minutes),
    )
    app_module.db.session.add(token_row)
    app_module.db.session.commit()
    return token_row


def line_text_event(text="ABC123", user_id="U111", reply_token="reply-token"):
    return {
        "type": "message",
        "replyToken": reply_token,
        "source": {"type": "user", "userId": user_id},
        "message": {"type": "text", "text": text},
    }


def test_line_webhook_valid_text_code_creates_account_subscription_and_uses_token(
    app_module, participant, monkeypatch
):
    client = app_module.app.test_client()
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-line-secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-access-token")
    replies = []
    patch_app_dependency(
        monkeypatch,
        app_module,
        "send_line_reply",
        lambda token, text: replies.append((token, text)) or True,
    )

    with app_module.app.app_context():
        session = add_session(app_module)
        add_link_token(app_module, participant, session.id)

        response = post_line_webhook(client, {"events": [line_text_event()]})

        assert response.status_code == 200
        account = app_module.LineAccount.query.one()
        assert account.participant_id == participant
        assert account.line_user_id == "U111"
        subscription = app_module.NotificationSubscription.query.one()
        assert subscription.session_id == session.id
        assert subscription.participant_id == participant
        assert subscription.channel == "line"
        assert subscription.active is True
        assert app_module.LineLinkToken.query.one().used_at is not None
        assert len(replies) == 1
        assert replies[0][0] == "reply-token"
        reply_text = replies[0][1]
        assert "LINE通知登録が完了しました🏸" in reply_text
        assert "組み合わせが確定したらLINEでお知らせします。" in reply_text
        assert "続けて参加費のお支払いをお願いします。" in reply_text
        assert "社会人：600円" in reply_text
        assert "学生：300円" in reply_text
        assert "PayPayはこちら:" in reply_text
        assert "社会人の方はこちら（600円）\nhttps://example.com/pay/adults" in reply_text
        assert "学生の方はこちら（300円）\nhttps://example.com/pay/students" in reply_text


def test_line_webhook_same_code_cannot_be_reused(app_module, participant, monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-line-secret")
    patch_app_dependency(monkeypatch, app_module, "send_line_reply", lambda token, text: True)

    with app_module.app.app_context():
        session = add_session(app_module)
        add_link_token(app_module, participant, session.id)
        post_line_webhook(client, {"events": [line_text_event(user_id="U111")]})
        post_line_webhook(client, {"events": [line_text_event(user_id="U222")]})

        assert app_module.LineAccount.query.count() == 1
        assert app_module.LineAccount.query.one().line_user_id == "U111"
        assert app_module.NotificationSubscription.query.count() == 1


def test_line_webhook_expired_code_is_rejected_without_creating_records(app_module, participant, monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-line-secret")
    patch_app_dependency(monkeypatch, app_module, "send_line_reply", lambda token, text: True)

    with app_module.app.app_context():
        session = add_session(app_module)
        token = add_link_token(app_module, participant, session.id, minutes=-1)
        response = post_line_webhook(client, {"events": [line_text_event()]})

        assert response.status_code == 200
        assert app_module.LineAccount.query.count() == 0
        assert app_module.NotificationSubscription.query.count() == 0
        assert app_module.db.session.get(app_module.LineLinkToken, token.id).used_at is None


def test_line_webhook_unknown_code_is_rejected(app_module, monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-line-secret")
    patch_app_dependency(monkeypatch, app_module, "send_line_reply", lambda token, text: True)

    with app_module.app.app_context():
        response = post_line_webhook(client, {"events": [line_text_event(text="NOPE")]})

        assert response.status_code == 200
        assert app_module.LineAccount.query.count() == 0
        assert app_module.NotificationSubscription.query.count() == 0


def test_line_webhook_inactive_participant_code_is_rejected(app_module, participant, monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-line-secret")
    patch_app_dependency(monkeypatch, app_module, "send_line_reply", lambda token, text: True)

    with app_module.app.app_context():
        player = get_participant(app_module, participant)
        player.active = False
        session = add_session(app_module)
        token = add_link_token(app_module, participant, session.id)
        app_module.db.session.commit()

        response = post_line_webhook(client, {"events": [line_text_event()]})

        assert response.status_code == 200
        assert app_module.LineAccount.query.count() == 0
        assert app_module.NotificationSubscription.query.count() == 0
        assert app_module.db.session.get(app_module.LineLinkToken, token.id).used_at is None


def test_line_webhook_existing_participant_account_is_updated(app_module, participant, monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-line-secret")
    patch_app_dependency(monkeypatch, app_module, "send_line_reply", lambda token, text: True)

    with app_module.app.app_context():
        account = add_line_account(app_module, participant, active=False)
        session = add_session(app_module)
        add_link_token(app_module, participant, session.id)

        post_line_webhook(client, {"events": [line_text_event(user_id="UNEW")]})

        assert app_module.LineAccount.query.count() == 1
        updated = app_module.db.session.get(app_module.LineAccount, account.id)
        assert updated.line_user_id == "UNEW"
        assert updated.active is True


def test_line_webhook_rejects_line_user_id_linked_to_another_participant(app_module, participant, monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-line-secret")
    patch_app_dependency(monkeypatch, app_module, "send_line_reply", lambda token, text: True)

    with app_module.app.app_context():
        other = app_module.Participant(name="other", gender="male", level="beginner", weight=1.0, card="C2")
        app_module.db.session.add(other)
        app_module.db.session.commit()
        add_line_account(app_module, other.id)
        account = app_module.LineAccount.query.filter_by(participant_id=other.id).one()
        account.line_user_id = "UCONFLICT"
        session = add_session(app_module)
        token = add_link_token(app_module, participant, session.id)
        app_module.db.session.commit()

        post_line_webhook(client, {"events": [line_text_event(user_id="UCONFLICT")]})

        assert app_module.NotificationSubscription.query.count() == 0
        assert app_module.db.session.get(app_module.LineLinkToken, token.id).used_at is None
        assert app_module.LineAccount.query.count() == 1


def test_line_webhook_reactivates_inactive_subscription(app_module, participant, monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-line-secret")
    patch_app_dependency(monkeypatch, app_module, "send_line_reply", lambda token, text: True)

    with app_module.app.app_context():
        session = add_session(app_module)
        subscription = app_module.NotificationSubscription(session_id=session.id, participant_id=participant, channel="line", active=False)
        app_module.db.session.add(subscription)
        add_link_token(app_module, participant, session.id)
        app_module.db.session.commit()

        post_line_webhook(client, {"events": [line_text_event()]})

        assert app_module.NotificationSubscription.query.count() == 1
        assert app_module.db.session.get(app_module.NotificationSubscription, subscription.id).active is True


def test_line_webhook_does_not_duplicate_active_subscription(app_module, participant, monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-line-secret")
    patch_app_dependency(monkeypatch, app_module, "send_line_reply", lambda token, text: True)

    with app_module.app.app_context():
        session = add_session(app_module)
        app_module.db.session.add(app_module.NotificationSubscription(session_id=session.id, participant_id=participant, channel="line", active=True))
        add_link_token(app_module, participant, session.id)
        app_module.db.session.commit()

        post_line_webhook(client, {"events": [line_text_event()]})

        assert app_module.NotificationSubscription.query.count() == 1


def test_line_webhook_invalid_missing_or_unconfigured_signature_is_not_processed(app_module, participant, monkeypatch):
    client = app_module.app.test_client()
    body_payload = {"events": [line_text_event()]}

    with app_module.app.app_context():
        session = add_session(app_module)
        add_link_token(app_module, participant, session.id)

        monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-line-secret")
        bad_body = json.dumps(body_payload).encode("utf-8")
        assert client.post("/line/webhook", data=bad_body, content_type="application/json", headers={"X-Line-Signature": "bad"}).status_code == 403
        assert post_line_webhook(client, body_payload, signature=False).status_code == 403
        monkeypatch.delenv("LINE_CHANNEL_SECRET", raising=False)
        assert post_line_webhook(client, body_payload).status_code == 403
        assert app_module.LineAccount.query.count() == 0
        assert app_module.NotificationSubscription.query.count() == 0
        assert app_module.LineLinkToken.query.one().used_at is None


def test_line_webhook_ignores_non_text_or_userless_events(app_module, participant, monkeypatch):
    client = app_module.app.test_client()
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-line-secret")

    with app_module.app.app_context():
        session = add_session(app_module)
        add_link_token(app_module, participant, session.id)
        payload = {"events": [
            {"type": "follow", "replyToken": "r", "source": {"type": "user", "userId": "U1"}},
            {"type": "message", "replyToken": "r", "source": {"type": "user", "userId": "U1"}, "message": {"type": "image"}},
            {"type": "message", "replyToken": "r", "source": {"type": "group"}, "message": {"type": "text", "text": "ABC123"}},
        ]}

        response = post_line_webhook(client, payload)

        assert response.status_code == 200
        assert app_module.LineAccount.query.count() == 0
        assert app_module.NotificationSubscription.query.count() == 0
        assert app_module.LineLinkToken.query.one().used_at is None


def test_line_push_send_route_is_not_implemented(app_module):
    client = app_module.app.test_client()

    assert client.post("/notifications/line/send").status_code == 404


def test_line_webhook_success_without_paypay_links_still_registers(
    app_module, participant, monkeypatch, tmp_path
):
    client = app_module.app.test_client()
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-line-secret")
    (tmp_path / "config.json").write_text(
        json.dumps({"paypay_links": {}}), encoding="utf-8"
    )
    replies = []
    patch_app_dependency(
        monkeypatch,
        app_module,
        "send_line_reply",
        lambda token, text: replies.append((token, text)) or True,
    )

    with app_module.app.app_context():
        session = add_session(app_module)
        add_link_token(app_module, participant, session.id)

        response = post_line_webhook(client, {"events": [line_text_event()]})

        assert response.status_code == 200
        assert app_module.LineAccount.query.count() == 1
        assert app_module.NotificationSubscription.query.count() == 1
        assert app_module.LineLinkToken.query.one().used_at is not None
        assert len(replies) == 1
        assert replies[0][0] == "reply-token"
        reply_text = replies[0][1]
        assert "LINE通知登録が完了しました🏸" in reply_text
        assert "続けて参加費のお支払いをお願いします。" in reply_text
        assert "社会人：600円" in reply_text
        assert "学生：300円" in reply_text
        assert "PayPayリンクが未設定のため、現地でお支払いください。" in reply_text


def test_line_webhook_success_with_one_paypay_link_includes_available_link(
    app_module, participant, monkeypatch, tmp_path
):
    client = app_module.app.test_client()
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-line-secret")
    (tmp_path / "config.json").write_text(
        json.dumps({"paypay_links": {"adults": "https://example.com/pay/adults"}}),
        encoding="utf-8",
    )
    replies = []
    patch_app_dependency(
        monkeypatch,
        app_module,
        "send_line_reply",
        lambda token, text: replies.append((token, text)) or True,
    )

    with app_module.app.app_context():
        session = add_session(app_module)
        add_link_token(app_module, participant, session.id)

        response = post_line_webhook(client, {"events": [line_text_event()]})

        assert response.status_code == 200
        assert app_module.LineAccount.query.count() == 1
        assert app_module.NotificationSubscription.query.count() == 1
        reply_text = replies[0][1]
        assert "社会人：600円" in reply_text
        assert "学生：300円" in reply_text
        assert "社会人の方はこちら（600円）\nhttps://example.com/pay/adults" in reply_text
        assert "学生の方はこちら（300円）" not in reply_text
        assert "PayPayリンクが未設定" not in reply_text


def test_thanks_page_hides_line_notification_ui_when_line_disabled(app_module, participant, monkeypatch):
    monkeypatch.delenv("LINE_MESSAGING_ENABLED", raising=False)
    client = app_module.app.test_client()

    with app_module.app.app_context():
        html = client.get("/thanks?card=C1").get_data(as_text=True)

        assert "🔔 LINE通知" not in html
        assert "LINE通知を登録する" not in html
        assert "📱 PayPay" in html


def test_start_line_notification_redirects_safely_when_line_disabled(app_module, participant, monkeypatch):
    monkeypatch.delenv("LINE_MESSAGING_ENABLED", raising=False)
    client = app_module.app.test_client()

    with app_module.app.app_context():
        response = client.get("/notifications/line/start/C1", follow_redirects=True)

        assert response.status_code == 200
        assert "LINE通知は現在無効です" in response.get_data(as_text=True)
        assert app_module.LineLinkToken.query.count() == 0
        assert app_module.NotificationSubscription.query.count() == 0


def test_line_webhook_returns_disabled_response_when_line_disabled(app_module, monkeypatch):
    monkeypatch.delenv("LINE_MESSAGING_ENABLED", raising=False)
    client = app_module.app.test_client()

    response = client.post("/line/webhook", json={"events": []})

    assert response.status_code == 200
    assert response.get_json()["status"] == "disabled"
