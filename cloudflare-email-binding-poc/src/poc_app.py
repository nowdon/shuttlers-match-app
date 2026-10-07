"""Synthetic WSGI probe for the production Cloudflare mail adapter."""

import base64

from flask import Flask, jsonify, request

from mail.cloudflare import CloudflareEmailTransport
from mail.model import MailAttachment, MailMessage


app = Flask(__name__)
SYNTHETIC_BYTES = b'{"phase":9,"source":"fake-email-binding"}'


def _received_payload():
    binding = request.environ["workers.env"].EMAIL
    payload = getattr(binding, "last_payload", None)
    if not isinstance(payload, dict):
        raise RuntimeError("fake EMAIL binding did not record a Python payload")
    return payload


@app.post("/email-poc/send")
def send():
    transport = CloudflareEmailTransport.from_request()
    message_id = transport.send_mail(MailMessage(
        recipient="poc-recipient@example.com",
        sender_email="poc-sender@example.com",
        sender_name="Phase 9 PoC",
        subject="Phase 9 synthetic email",
        body="This message is captured by a fake binding.",
        attachment=MailAttachment(
            filename="match_history_phase9.json",
            content=SYNTHETIC_BYTES,
            content_type="application/json",
        ),
    ))
    payload = _received_payload()
    attachment = payload["attachments"][0]
    decoded = base64.b64decode(attachment["content"], validate=True)
    return jsonify(
        ok=(decoded == SYNTHETIC_BYTES),
        adapter="CloudflareEmailTransport",
        binding="EMAIL",
        run_sync=True,
        real_email_service_used=False,
        message_id=message_id,
        to=payload["to"],
        **{"from": payload["from"]},
        subject=payload["subject"],
        text=payload["text"],
        attachment={
            "filename": attachment["filename"],
            "type": attachment["type"],
            "disposition": attachment["disposition"],
            "base64_bytes_match": decoded == SYNTHETIC_BYTES,
        },
    )


def application(environ, start_response):
    if environ.get("PATH_INFO") != "/email-poc/send" or environ.get("REQUEST_METHOD") != "POST":
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"Not found"]
    return app.wsgi_app(environ, start_response)
