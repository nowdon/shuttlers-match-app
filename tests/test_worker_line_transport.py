"""The Worker uses fetch because Pyodide cannot use urllib sockets."""

from types import SimpleNamespace
import urllib.error

import pytest

import utils.line_push as line_push


class Promise:
    def __init__(self, value):
        self.value = value


class Response:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    def text(self):
        return Promise(self.body)


def install_worker_fetch(monkeypatch, *, status=200, body="{}"):
    calls = []

    def fetch(url, options):
        calls.append((url, options))
        return Promise(Response(status, body))

    monkeypatch.setattr(line_push, "_worker_fetch", fetch)
    monkeypatch.setattr(line_push, "_run_sync", lambda promise: promise.value)
    monkeypatch.setattr(line_push, "_to_js", lambda value, **_kwargs: value)
    monkeypatch.setattr(line_push, "_JsObject", SimpleNamespace(fromEntries=object()))
    monkeypatch.setattr(
        line_push.urllib.request, "urlopen",
        lambda *_args, **_kwargs: pytest.fail("urllib must not run in Worker"),
    )
    return calls


def test_worker_push_uses_fetch_with_same_line_payload(monkeypatch):
    calls = install_worker_fetch(monkeypatch)

    assert line_push.send_line_push_request("U-test", "hello", "synthetic-token") == "{}"

    url, options = calls[0]
    assert url == line_push.LINE_PUSH_URL
    assert options["method"] == "POST"
    assert options["headers"]["Authorization"] == "Bearer synthetic-token"
    assert '"to": "U-test"' in options["body"]
    assert '"text": "hello"' in options["body"]


def test_worker_reply_uses_fetch_and_preserves_error_status(monkeypatch):
    calls = install_worker_fetch(monkeypatch, status=429, body="rate limited")
    monkeypatch.setattr(line_push, "scalar_setting", lambda _name: "synthetic-token")

    with pytest.raises(urllib.error.HTTPError) as error:
        line_push.send_line_reply("reply-token", "hello")

    assert error.value.code == 429
    assert calls[0][0] == line_push.LINE_REPLY_URL
    assert '"replyToken": "reply-token"' in calls[0][1]["body"]


def test_worker_push_non_success_remains_delivery_failure(monkeypatch):
    install_worker_fetch(monkeypatch, status=401, body="unauthorized")

    with pytest.raises(line_push.LinePushError, match="status 401"):
        line_push.send_line_push_request("U-test", "hello", "synthetic-token")
