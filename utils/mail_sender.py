"""SMTP mail helpers for history dump delivery."""

import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

VALID_SMTP_SECURITY = {"starttls", "ssl", "none"}


class MailConfigurationError(RuntimeError):
    """Raised when required SMTP settings are missing or invalid."""


def _get_required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise MailConfigurationError(f"{name} is required for SMTP email delivery")
    return value


def _get_smtp_port(security):
    port_value = os.environ.get("SMTP_PORT", "").strip()
    if port_value:
        try:
            return int(port_value)
        except ValueError as exc:
            raise MailConfigurationError("SMTP_PORT must be an integer") from exc
    return 465 if security == "ssl" else 587


def _get_timeout_seconds():
    timeout_value = os.environ.get("SMTP_TIMEOUT_SECONDS", "").strip()
    if not timeout_value:
        return 10.0
    try:
        timeout = float(timeout_value)
    except ValueError as exc:
        raise MailConfigurationError("SMTP_TIMEOUT_SECONDS must be a number") from exc
    if timeout <= 0:
        raise MailConfigurationError("SMTP_TIMEOUT_SECONDS must be greater than 0")
    return timeout


def _build_message(recipient, subject, body, attachment_path):
    from_email = _get_required_env("SMTP_FROM_EMAIL")
    from_name = os.environ.get("SMTP_FROM_NAME", "").strip()
    sender = f"{from_name} <{from_email}>" if from_name else from_email

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    path = Path(attachment_path)
    content_type, _encoding = mimetypes.guess_type(str(path))
    if content_type is None:
        content_type = "application/octet-stream"
    maintype, subtype = content_type.split("/", 1)
    message.add_attachment(
        path.read_bytes(),
        maintype=maintype,
        subtype=subtype,
        filename=path.name,
    )
    return message, from_email


def send_email_with_attachment(recipient, subject, body, attachment_path):
    """Send an email with a single file attachment using direct SMTP."""
    host = _get_required_env("SMTP_HOST")
    security = os.environ.get("SMTP_SECURITY", "starttls").strip().lower() or "starttls"
    if security not in VALID_SMTP_SECURITY:
        raise MailConfigurationError("SMTP_SECURITY must be one of: starttls, ssl, none")

    port = _get_smtp_port(security)
    timeout = _get_timeout_seconds()
    message, from_email = _build_message(recipient, subject, body, attachment_path)
    username = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    if username and not password:
        raise MailConfigurationError(
            "SMTP_PASSWORD is required when SMTP_USERNAME is set"
        )
    context = ssl.create_default_context()

    if security == "ssl":
        smtp_factory = smtplib.SMTP_SSL
        smtp_kwargs = {"context": context}
    else:
        smtp_factory = smtplib.SMTP
        smtp_kwargs = {}

    with smtp_factory(host, port, timeout=timeout, **smtp_kwargs) as smtp:
        if security == "starttls":
            smtp.starttls(context=context)
        if username:
            smtp.login(username, password)
        smtp.send_message(message, from_addr=from_email, to_addrs=[recipient])
