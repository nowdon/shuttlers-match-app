import base64
import hashlib
import hmac
import json
from utils.config import scalar_setting
import urllib.error
import urllib.request

try:
    from js import Object as _JsObject, fetch as _worker_fetch
    from pyodide.ffi import run_sync as _run_sync, to_js as _to_js
except ImportError:  # Local/EC2 Python uses urllib as before.
    _JsObject = None
    _worker_fetch = None
    _run_sync = None
    _to_js = None

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


class LinePushError(Exception):
    """Raised when a LINE push message cannot be sent."""


def _worker_post_json(url, payload, channel_access_token):
    """Use Workers fetch for the same JSON POST used by local urllib."""
    options = {
        "method": "POST",
        "headers": {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {channel_access_token}",
        },
        "body": payload.decode("utf-8"),
    }
    options = _to_js(options, dict_converter=_JsObject.fromEntries)
    response = _run_sync(_worker_fetch(url, options))
    return int(response.status), str(_run_sync(response.text()))


def verify_line_signature(raw_body, signature, channel_secret):
    """Return True when a LINE webhook signature matches the raw request body."""
    if not channel_secret or not signature:
        return False
    digest = hmac.new(
        channel_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).digest()
    expected_signature = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected_signature, signature)


def send_line_reply(reply_token, message_text):
    """Send a LINE Messaging API reply message."""
    channel_access_token = scalar_setting("LINE_CHANNEL_ACCESS_TOKEN")
    if not channel_access_token or not reply_token:
        return False

    payload = json.dumps(
        {
            "replyToken": reply_token,
            "messages": [{"type": "text", "text": message_text}],
        }
    ).encode("utf-8")
    if _worker_fetch is not None:
        try:
            status, body = _worker_post_json(
                LINE_REPLY_URL, payload, channel_access_token
            )
        except Exception as error:
            raise urllib.error.URLError(str(error)) from error
        if not 200 <= status < 300:
            raise urllib.error.HTTPError(LINE_REPLY_URL, status, body, None, None)
        return True

    request = urllib.request.Request(
        LINE_REPLY_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {channel_access_token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return 200 <= response.status < 300


def send_line_push_request(line_user_id, text, channel_access_token):
    """Send the raw LINE push request. Kept separate so tests can mock it."""
    payload = json.dumps(
        {
            "to": line_user_id,
            "messages": [{"type": "text", "text": text}],
        }
    ).encode("utf-8")
    if _worker_fetch is not None:
        try:
            status, body = _worker_post_json(
                LINE_PUSH_URL, payload, channel_access_token
            )
        except Exception as error:
            raise LinePushError(str(error)) from error
        if not 200 <= status < 300:
            raise LinePushError(f"LINE push failed with status {status}: {body}")
        return body

    request = urllib.request.Request(
        LINE_PUSH_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {channel_access_token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8", errors="replace")
        if not 200 <= response.status < 300:
            raise LinePushError(f"LINE push failed with status {response.status}: {body}")
        return body


def push_line_message(line_user_id, text):
    """Push a text message to a LINE user using LINE_CHANNEL_ACCESS_TOKEN."""
    channel_access_token = scalar_setting("LINE_CHANNEL_ACCESS_TOKEN")
    if not channel_access_token:
        raise LinePushError("LINE_CHANNEL_ACCESS_TOKEN is not set")
    if not line_user_id:
        raise LinePushError("line_user_id is required")
    try:
        send_line_push_request(line_user_id, text, channel_access_token)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise LinePushError(f"LINE push failed with status {error.code}: {body}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise LinePushError(str(error)) from error
    return True
