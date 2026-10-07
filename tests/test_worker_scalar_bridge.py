"""Worker scalar settings reach LINE/mail adapters without provider calls."""

from types import SimpleNamespace

from flask import Flask

import mail.cloudflare as cloudflare_module
from mail.provider import get_mail_transport
from routes.helpers import is_line_messaging_enabled
from utils.line_push import push_line_message
from utils.mail_sender import send_email_with_attachment


def test_line_push_uses_bridged_token_with_fake_transport(monkeypatch):
    app = Flask(__name__)
    app.config.update(LINE_MESSAGING_ENABLED="true",
                      LINE_CHANNEL_ACCESS_TOKEN="synthetic-worker-token")
    seen = []
    monkeypatch.setattr("utils.line_push.send_line_push_request",
                        lambda user_id, message, token: seen.append((user_id, message, token)))
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    with app.app_context():
        assert is_line_messaging_enabled()
        assert push_line_message("U-synthetic", "hello")
    assert seen == [("U-synthetic", "hello", "synthetic-worker-token")]


def test_email_uses_bridged_sender_and_request_local_fake_binding(monkeypatch):
    app = Flask(__name__)
    app.config.update(MAIL_TRANSPORT="cloudflare", MAIL_FROM_EMAIL="sender@example.test")
    sent = []

    class FakeBinding:
        def send(self, payload):
            sent.append(payload)
            return {"messageId": "synthetic-id"}

    monkeypatch.setattr(cloudflare_module, "_run_sync", lambda value: value)
    monkeypatch.setattr(cloudflare_module, "_to_js", lambda value, **_kwargs: value)
    monkeypatch.delenv("MAIL_FROM_EMAIL", raising=False)
    with app.test_request_context(
        "/", environ_base={"workers.env": SimpleNamespace(EMAIL=FakeBinding())},
    ):
        assert get_mail_transport().__class__.__name__ == "CloudflareEmailTransport"
        result = send_email_with_attachment(
            "recipient@example.test", "subject", "body",
            attachment_bytes=b"{}", attachment_name="history.json",
        )
    assert result == "synthetic-id"
    assert sent[0]["from"] == "sender@example.test"
    assert sent[0]["to"] == "recipient@example.test"
