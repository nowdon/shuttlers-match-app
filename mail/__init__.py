"""Application-scoped mail transport boundary."""

from mail.errors import MailConfigurationError, MailDeliveryError
from mail.model import MailAttachment, MailMessage
from mail.provider import get_mail_transport, selected_mail_transport

__all__ = [
    "MailAttachment",
    "MailConfigurationError",
    "MailDeliveryError",
    "MailMessage",
    "get_mail_transport",
    "selected_mail_transport",
]
