"""Selection and request-local lifetime for the mail transports."""

import os

from flask import current_app, g, has_app_context, has_request_context, request

from mail.cloudflare import CloudflareEmailTransport
from mail.errors import MailConfigurationError
from mail.smtp import SMTPTransport


_NOT_PROVIDED = object()
_TRANSPORT_KEY = "_mail_transport_instance"


def _selected_transport(explicit_transport=None):
    if explicit_transport is not None:
        return explicit_transport
    if has_app_context():
        configured = current_app.config.get("MAIL_TRANSPORT")
        if configured:
            return configured
    return os.environ.get("MAIL_TRANSPORT") or "smtp"


def selected_mail_transport(explicit_transport=None):
    return str(_selected_transport(explicit_transport)).strip().lower()


def create_mail_transport(
    transport=None, *, binding=_NOT_PROVIDED, run_sync=_NOT_PROVIDED,
):
    selected = selected_mail_transport(transport)
    if selected == "smtp":
        return SMTPTransport()
    if selected == "cloudflare":
        if binding is _NOT_PROVIDED:
            if not has_request_context():
                raise MailConfigurationError(
                    "Cloudflare mail transport requires a request context"
                )
            workers_env = request.environ.get("workers.env")
            binding = getattr(workers_env, "EMAIL", None) if workers_env else None
        if run_sync is _NOT_PROVIDED:
            return CloudflareEmailTransport(binding)
        return CloudflareEmailTransport(binding, run_sync=run_sync)
    raise MailConfigurationError(f"Unknown MAIL_TRANSPORT: {selected}")


def get_mail_transport():
    """Return one adapter per request; SMTP remains usable outside Flask."""
    if not has_request_context():
        if selected_mail_transport() == "smtp":
            return create_mail_transport("smtp")
        return create_mail_transport()
    transport = g.get(_TRANSPORT_KEY)
    if transport is None:
        transport = create_mail_transport()
        setattr(g, _TRANSPORT_KEY, transport)
    return transport
