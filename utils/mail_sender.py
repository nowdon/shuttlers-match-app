"""Backward-compatible history-dump mail helper.

Callers retain the original ``send_email_with_attachment`` signature while
transport selection and provider-specific details live behind ``mail/``.
"""

import mimetypes
import os
import smtplib
from pathlib import Path

try:
    from mail.errors import MailConfigurationError, MailDeliveryError
except ModuleNotFoundError:  # Import-only Cloudflare PoCs omit the mail package.
    class MailConfigurationError(RuntimeError):
        pass

    class MailDeliveryError(RuntimeError):
        pass


def _sender_settings():
    sender_email = (
        os.environ.get("MAIL_FROM_EMAIL", "").strip()
        or os.environ.get("SMTP_FROM_EMAIL", "").strip()
    )
    if not sender_email:
        raise MailConfigurationError(
            "SMTP_FROM_EMAIL is required for mail delivery"
        )
    sender_name = (
        os.environ.get("MAIL_FROM_NAME", "").strip()
        or os.environ.get("SMTP_FROM_NAME", "").strip()
        or None
    )
    return sender_email, sender_name


def _attachment_from_legacy(
    attachment_path=None, *, attachment_bytes=None, attachment_name=None,
):
    if attachment_bytes is None:
        if attachment_path is None:
            raise ValueError("attachment_path or attachment_bytes is required")
        path = Path(attachment_path)
        attachment_bytes = path.read_bytes()
        attachment_name = path.name
    elif not attachment_name:
        raise ValueError("attachment_name is required with attachment_bytes")

    content_type, _encoding = mimetypes.guess_type(str(attachment_name))
    if content_type is None:
        content_type = "application/octet-stream"
    from mail.model import MailAttachment

    return MailAttachment(
        filename=str(attachment_name),
        content=bytes(attachment_bytes),
        content_type=content_type,
    )


def _build_mail_message(
    recipient, subject, body, attachment_path=None, *,
    attachment_bytes=None, attachment_name=None,
):
    from mail.model import MailMessage

    sender_email, sender_name = _sender_settings()
    attachment = _attachment_from_legacy(
        attachment_path,
        attachment_bytes=attachment_bytes,
        attachment_name=attachment_name,
    )
    return MailMessage(
        recipient=recipient,
        sender_email=sender_email,
        sender_name=sender_name,
        subject=subject,
        body=body,
        attachment=attachment,
    )


def _build_message(
    recipient, subject, body, attachment_path=None, *,
    attachment_bytes=None, attachment_name=None,
):
    """Preserve the old SMTP message helper for import compatibility."""
    from mail.smtp import build_smtp_message

    mail_message = _build_mail_message(
        recipient,
        subject,
        body,
        attachment_path,
        attachment_bytes=attachment_bytes,
        attachment_name=attachment_name,
    )
    return build_smtp_message(mail_message), mail_message.sender_email


def send_email_with_attachment(
    recipient, subject, body, attachment_path=None, *,
    attachment_bytes=None, attachment_name=None,
):
    """Send one history-dump message through the selected transport."""
    from mail.provider import get_mail_transport

    message = _build_mail_message(
        recipient,
        subject,
        body,
        attachment_path,
        attachment_bytes=attachment_bytes,
        attachment_name=attachment_name,
    )
    return get_mail_transport().send_mail(message)


__all__ = [
    "MailConfigurationError",
    "MailDeliveryError",
    "send_email_with_attachment",
]
