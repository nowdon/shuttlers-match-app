"""SMTP implementation for local and EC2 deployments."""

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from mail.errors import MailConfigurationError
from mail.model import MailMessage


VALID_SMTP_SECURITY = {"starttls", "ssl", "none"}


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


def build_smtp_message(message: MailMessage):
    """Build the legacy ``EmailMessage`` representation for SMTP."""
    sender = (
        formataddr((message.sender_name, message.sender_email))
        if message.sender_name
        else message.sender_email
    )
    email = EmailMessage()
    email["From"] = sender
    email["To"] = message.recipient
    email["Subject"] = message.subject
    email.set_content(message.body)

    if message.attachment is not None:
        maintype, subtype = message.attachment.content_type.split("/", 1)
        email.add_attachment(
            message.attachment.content,
            maintype=maintype,
            subtype=subtype,
            filename=message.attachment.filename,
        )
    return email


class SMTPTransport:
    """Send one message using the pre-existing SMTP environment contract."""

    def send_mail(self, message: MailMessage):
        host = _get_required_env("SMTP_HOST")
        security = os.environ.get("SMTP_SECURITY", "starttls").strip().lower() or "starttls"
        if security not in VALID_SMTP_SECURITY:
            raise MailConfigurationError(
                "SMTP_SECURITY must be one of: starttls, ssl, none"
            )

        port = _get_smtp_port(security)
        timeout = _get_timeout_seconds()
        email = build_smtp_message(message)
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
            smtp.send_message(
                email,
                from_addr=message.sender_email,
                to_addrs=[message.recipient],
            )
        return None
