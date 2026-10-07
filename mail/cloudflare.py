"""Cloudflare Email Service binding adapter for Python Workers."""

import base64

from flask import has_request_context, request

from mail.errors import MailConfigurationError, MailDeliveryError
from mail.model import MailMessage

try:
    from js import Object as _JsObject
    from pyodide.ffi import run_sync as _run_sync, to_js as _to_js
except ImportError:  # Normal CPython/EC2 environments do not provide Pyodide.
    _JsObject = None
    _run_sync = None
    _to_js = None


_DEFAULT = object()


def _as_python_mapping(value):
    if isinstance(value, dict):
        return value
    converter = getattr(value, "to_py", None)
    if converter is not None:
        try:
            return converter(dict_converter=dict)
        except TypeError:
            return converter()
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {}


def _message_id(result):
    if result is None:
        return None
    mapping = _as_python_mapping(result)
    if mapping:
        value = mapping.get("messageId")
        if value is not None:
            return str(value)
    value = getattr(result, "messageId", None)
    return None if value is None else str(value)


def _provider_code(error):
    code = getattr(error, "code", None)
    if code is None:
        mapping = _as_python_mapping(error)
        code = mapping.get("code")
    return None if code is None else str(code)


class CloudflareEmailTransport:
    """Synchronously expose one request-local ``EMAIL`` binding to Flask."""

    def __init__(self, binding, *, run_sync=_DEFAULT, to_js=_DEFAULT):
        if binding is None:
            raise MailConfigurationError("Cloudflare EMAIL binding is unavailable")
        self._binding = binding
        self._run_sync = _run_sync if run_sync is _DEFAULT else run_sync
        self._to_js = _to_js if to_js is _DEFAULT else to_js

    @classmethod
    def from_request(cls):
        if not has_request_context():
            raise MailConfigurationError(
                "Cloudflare mail transport requires a request context"
            )
        workers_env = request.environ.get("workers.env")
        if workers_env is None:
            raise MailConfigurationError("Workers environment is unavailable")
        binding = getattr(workers_env, "EMAIL", None)
        if binding is None:
            raise MailConfigurationError("Cloudflare EMAIL binding is unavailable")
        return cls(binding)

    def _payload(self, message: MailMessage):
        sender = {"email": message.sender_email}
        if message.sender_name:
            sender["name"] = message.sender_name
        payload = {
            "to": message.recipient,
            "from": sender if message.sender_name else message.sender_email,
            "subject": message.subject,
            "text": message.body,
        }
        if message.attachment is not None:
            attachment = message.attachment
            payload["attachments"] = [{
                "content": base64.b64encode(attachment.content).decode("ascii"),
                "filename": attachment.filename,
                "type": attachment.content_type,
                "disposition": "attachment",
            }]
        return payload

    def _to_binding_payload(self, payload):
        if self._to_js is None:
            return payload
        if _JsObject is None:
            return self._to_js(payload)
        return self._to_js(payload, dict_converter=_JsObject.fromEntries)

    def send_mail(self, message: MailMessage):
        if self._run_sync is None:
            raise MailConfigurationError(
                "Cloudflare mail transport requires pyodide.ffi.run_sync"
            )
        payload = self._payload(message)
        try:
            binding_payload = self._to_binding_payload(payload)
            result = self._run_sync(self._binding.send(binding_payload))
        except MailConfigurationError:
            raise
        except Exception as error:
            code = _provider_code(error)
            raise MailDeliveryError(code=code, detail=str(error)) from None
        return _message_id(result)
