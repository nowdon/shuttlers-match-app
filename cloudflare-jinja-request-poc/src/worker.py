"""One request performs one top-level template operation."""
import hashlib
import json
from urllib.parse import urlsplit

from workers import Response, WorkerEntrypoint, wsgi

from side_effect_guard import guard

with guard() as IMPORT_COUNTS:
    from app import app
    import app as app_module
    from catalog import TEMPLATES, render_context

ORIGINAL_WSGI = app_module._flask_wsgi_app


def probe(slug, operation):
    spec = TEMPLATES.get(slug)
    if spec is None or operation not in {"load", "compile", "render"}:
        return "Not found", 404
    if operation == "render" and not spec["render"]:
        return "Render excluded by static classification", 409

    env = app.jinja_env
    cache_before = len(env.cache)
    if operation == "load":
        source = env.loader.get_source(env, spec["name"])[0]
        body = f"loaded {len(source.encode())} bytes"
    elif operation == "compile":
        source = env.loader.get_source(env, spec["name"])[0]
        env.compile(source, name=spec["name"])
        body = "compiled"
    else:
        body = env.get_template(spec["name"]).render(**render_context(slug))

    response = app.response_class(body)
    response.headers["X-Jinja-Template"] = spec["name"]
    response.headers["X-Jinja-Mode"] = operation
    response.headers["X-Jinja-Class"] = spec["classification"]
    response.headers["X-Jinja-Cache-Before"] = str(cache_before)
    response.headers["X-Jinja-Cache-After"] = str(len(env.cache))
    response.headers["X-Jinja-Body-SHA256"] = hashlib.sha256(
        body.encode()
    ).hexdigest()
    return response


for template_slug in TEMPLATES:
    for probe_operation in ("load", "compile", "render"):
        endpoint = f"jinja_poc_{template_slug.replace('-', '_')}_{probe_operation}"
        path = f"/jinja/{template_slug}/{probe_operation}"
        app.add_url_rule(
            path,
            endpoint,
            lambda slug=template_slug, operation=probe_operation: probe(slug, operation),
        )

ALLOWED = {
    f"/jinja/{slug}/{operation}"
    for slug, spec in TEMPLATES.items()
    for operation in ("load", "compile", "render")
    if operation != "render" or spec["render"]
}


def safe_wsgi(environ, start_response):
    state = {}

    def capture(status, headers, exc_info=None):
        state.update(status=status, headers=headers)

    with guard() as counts:
        result = ORIGINAL_WSGI(environ, capture)
        try:
            chunks = list(result)
        finally:
            if hasattr(result, "close"):
                result.close()

    if any(counts.values()):
        state = {"status": "500 Internal Server Error", "headers": []}
        chunks = [b"Blocked side effect"]
    state["headers"].extend(
        [
            ("X-Jinja-Effects", json.dumps(counts, separators=(",", ":"))),
            (
                "X-Jinja-Import-Effects",
                json.dumps(IMPORT_COUNTS, separators=(",", ":")),
            ),
            ("X-Jinja-Runtime", str(app_module._runtime_initialized).lower()),
            ("X-Jinja-Secret-Key", str(app.secret_key is not None).lower()),
        ]
    )
    start_response(state["status"], state["headers"])
    return chunks


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        path = urlsplit(request.url).path
        if str(request.method) != "GET":
            return Response("Method not allowed", status=405)
        if path not in ALLOWED:
            return Response("Not found", status=404)
        assert not app_module._runtime_initialized
        assert app.secret_key is None
        self.seen = getattr(self, "seen", 0) + 1
        response = await wsgi.fetch(safe_wsgi, request, self.env)
        response.headers.set("X-Jinja-Invocation", str(self.seen))
        return response
