"""Local-only Worker entrypoint using a fake EMAIL binding."""

from types import SimpleNamespace

from js import Promise
from workers import WorkerEntrypoint, wsgi

from poc_app import application


def _to_python(value):
    if isinstance(value, dict):
        return {str(key): _to_python(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_python(item) for item in value]
    converter = getattr(value, "to_py", None)
    if converter is not None:
        try:
            return _to_python(converter(dict_converter=dict))
        except TypeError:
            return _to_python(converter())
    try:
        return {str(key): _to_python(item) for key, item in dict(value).items()}
    except (TypeError, ValueError):
        return value


class FakeEmailBinding:
    """JS-compatible local binding that records payloads and never delivers."""

    def __init__(self):
        self.last_payload = None

    def send(self, payload):
        self.last_payload = _to_python(payload)
        return Promise.resolve({"messageId": "phase9-fake-message-id"})


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        # Do not pass the platform Email binding to the app. Local Email Service binding
        # simulation can deliver real email; this request-local fake is the
        # deliberate safety boundary for the Phase 9 proof.
        fake_env = SimpleNamespace(EMAIL=FakeEmailBinding())
        return await wsgi.fetch(application, request, fake_env)
