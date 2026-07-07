import json
import os
import urllib.error
import urllib.request

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


class LinePushError(Exception):
    """Raised when a LINE push message cannot be sent."""


def send_line_push_request(line_user_id, text, channel_access_token):
    """Send the raw LINE push request. Kept separate so tests can mock it."""
    payload = json.dumps(
        {
            "to": line_user_id,
            "messages": [{"type": "text", "text": text}],
        }
    ).encode("utf-8")
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
    channel_access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
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
