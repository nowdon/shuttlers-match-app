from email.message import EmailMessage

import pytest

from utils import mail_sender


class DummySMTP:
    instances = []

    def __init__(self, host, port, timeout=None, **kwargs):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.kwargs = kwargs
        self.started_tls = False
        self.login_calls = []
        self.sent_messages = []
        DummySMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self, context=None):
        self.started_tls = True
        self.tls_context = context

    def login(self, username, password):
        self.login_calls.append((username, password))

    def send_message(self, message, from_addr=None, to_addrs=None):
        self.sent_messages.append((message, from_addr, to_addrs))


@pytest.fixture(autouse=True)
def smtp_env(monkeypatch):
    DummySMTP.instances = []
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_SECURITY", "starttls")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "sender@example.com")
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setattr(mail_sender.smtplib, "SMTP", DummySMTP)
    monkeypatch.setattr(mail_sender.smtplib, "SMTP_SSL", DummySMTP)


def write_attachment(tmp_path):
    attachment = tmp_path / "history.json"
    attachment.write_text('{"ok": true}', encoding="utf-8")
    return attachment


def send(tmp_path):
    mail_sender.send_email_with_attachment(
        recipient="to@example.com",
        subject="subject",
        body="body",
        attachment_path=write_attachment(tmp_path),
    )
    return DummySMTP.instances[-1]


def test_starttls_connection(monkeypatch, tmp_path):
    smtp = send(tmp_path)
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 2525
    assert smtp.started_tls is True


def test_ssl_connection(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_SECURITY", "ssl")
    smtp = send(tmp_path)
    assert smtp.started_tls is False
    assert "context" in smtp.kwargs


def test_none_connection(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_SECURITY", "none")
    smtp = send(tmp_path)
    assert smtp.started_tls is False


def test_login_when_credentials_present(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_USERNAME", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    smtp = send(tmp_path)
    assert smtp.login_calls == [("user", "secret")]


def test_does_not_login_without_username(tmp_path):
    smtp = send(tmp_path)
    assert smtp.login_calls == []


def test_json_file_is_attached_with_original_filename(tmp_path):
    smtp = send(tmp_path)
    message = smtp.sent_messages[0][0]
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "history.json"
    assert attachments[0].get_content_type() == "application/json"
