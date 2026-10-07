import base64
from types import SimpleNamespace

import pytest
from flask import Flask

import mail.cloudflare as cloudflare_module
from mail.cloudflare import CloudflareEmailTransport
from mail.errors import MailConfigurationError, MailDeliveryError
from mail.model import MailAttachment, MailMessage
from mail.provider import get_mail_transport, selected_mail_transport
from utils import mail_sender


class Promise:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error


class FakeEmailBinding:
    def __init__(self, result=None):
        self.result = result or {"messageId": "cf-message-123"}
        self.payloads = []

    def send(self, payload):
        self.payloads.append(payload)
        return Promise(self.result)


def run_sync(promise):
    if promise.error is not None:
        raise promise.error
    return promise.value


def message(*, attachment=True):
    return MailMessage(
        recipient="to@example.com",
        sender_email="sender@example.com",
        sender_name="Shuttlers Match App",
        subject="subject",
        body="body",
        attachment=(
            MailAttachment("history.json", b'{"rounds": []}', "application/json")
            if attachment else None
        ),
    )


def test_unset_transport_defaults_to_smtp(monkeypatch):
    monkeypatch.delenv("MAIL_TRANSPORT", raising=False)
    assert selected_mail_transport() == "smtp"


def test_unknown_transport_fails_closed(monkeypatch):
    monkeypatch.setenv("MAIL_TRANSPORT", "wat")
    with pytest.raises(MailConfigurationError, match="Unknown MAIL_TRANSPORT"):
        get_mail_transport()


def test_cloudflare_payload_uses_base64_and_returns_message_id():
    binding = FakeEmailBinding()
    transport = CloudflareEmailTransport(
        binding,
        run_sync=run_sync,
        to_js=lambda payload: payload,
    )

    result = transport.send_mail(message())

    assert result == "cf-message-123"
    payload = binding.payloads[0]
    assert payload["to"] == "to@example.com"
    assert payload["from"] == {
        "email": "sender@example.com",
        "name": "Shuttlers Match App",
    }
    assert payload["subject"] == "subject"
    assert payload["text"] == "body"
    attachment = payload["attachments"][0]
    assert attachment["filename"] == "history.json"
    assert attachment["type"] == "application/json"
    assert attachment["disposition"] == "attachment"
    assert base64.b64decode(attachment["content"]) == b'{"rounds": []}'


def test_cloudflare_plain_text_message_has_no_attachment():
    binding = FakeEmailBinding()
    transport = CloudflareEmailTransport(binding, run_sync=run_sync)

    transport.send_mail(message(attachment=False))

    assert "attachments" not in binding.payloads[0]
    assert binding.payloads[0]["from"]["name"] == "Shuttlers Match App"


def test_cloudflare_provider_error_is_normalized_without_detail_leak():
    class ProviderError(RuntimeError):
        code = "E_SENDER_NOT_VERIFIED"

    class FailingBinding:
        def send(self, _payload):
            raise ProviderError("private provider detail")

    transport = CloudflareEmailTransport(
        FailingBinding(), run_sync=run_sync, to_js=lambda payload: payload
    )

    with pytest.raises(MailDeliveryError) as raised:
        transport.send_mail(message())

    assert raised.value.code == "E_SENDER_NOT_VERIFIED"
    assert raised.value.detail == "private provider detail"
    assert "private provider detail" not in str(raised.value)


def test_cloudflare_transport_requires_run_sync():
    transport = CloudflareEmailTransport(object(), run_sync=None)
    with pytest.raises(MailConfigurationError, match="run_sync"):
        transport.send_mail(message())


def test_cloudflare_transport_requires_request_local_binding(monkeypatch):
    app = Flask(__name__)
    monkeypatch.setenv("MAIL_TRANSPORT", "cloudflare")
    with app.test_request_context("/"):
        with pytest.raises(MailConfigurationError, match="EMAIL binding"):
            get_mail_transport()


def test_cloudflare_adapter_is_cached_per_request_not_globally(monkeypatch):
    app = Flask(__name__)
    monkeypatch.setenv("MAIL_TRANSPORT", "cloudflare")
    first_binding = FakeEmailBinding()
    second_binding = FakeEmailBinding()

    with app.test_request_context(
        "/", environ_overrides={"workers.env": SimpleNamespace(EMAIL=first_binding)}
    ):
        first = get_mail_transport()
        assert first is get_mail_transport()
        assert first._binding is first_binding

    with app.test_request_context(
        "/", environ_overrides={"workers.env": SimpleNamespace(EMAIL=second_binding)}
    ):
        second = get_mail_transport()
        assert second is not first
        assert second._binding is second_binding


def test_legacy_helper_uses_cloudflare_transport_without_smtp_fallback(monkeypatch):
    app = Flask(__name__)
    binding = FakeEmailBinding()
    monkeypatch.setenv("MAIL_TRANSPORT", "cloudflare")
    monkeypatch.setenv("MAIL_FROM_EMAIL", "sender@example.com")
    monkeypatch.delenv("SMTP_FROM_EMAIL", raising=False)
    monkeypatch.setattr(cloudflare_module, "_run_sync", run_sync)
    monkeypatch.setattr(
        mail_sender.smtplib,
        "SMTP",
        lambda *args, **kwargs: pytest.fail("SMTP fallback was used"),
    )

    with app.test_request_context(
        "/", environ_overrides={"workers.env": SimpleNamespace(EMAIL=binding)}
    ):
        result = mail_sender.send_email_with_attachment(
            recipient="to@example.com",
            subject="subject",
            body="body",
            attachment_bytes=b"json-bytes",
            attachment_name="archive.json",
        )

    assert result == "cf-message-123"
    assert base64.b64decode(binding.payloads[0]["attachments"][0]["content"]) == b"json-bytes"
