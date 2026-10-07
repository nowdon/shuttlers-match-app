"""Cloudflare Python Worker entrypoint for the existing Flask application."""

from urllib.parse import urlsplit

from workers import Response, WorkerEntrypoint, wsgi

from app import app


WORKER_SCALAR_SETTINGS = (
    "LINE_MESSAGING_ENABLED", "LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN",
    "LINE_BOT_FRIEND_URL", "MAIL_TRANSPORT", "MAIL_FROM_EMAIL", "MAIL_FROM_NAME",
)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urlsplit(request.url)
        path = url.path
        if url.scheme == "http" and url.hostname == "app.tbystg.org":
            location = "https://app.tbystg.org" + path
            if url.query:
                location += "?" + url.query
            return Response("", status=308, headers={"Location": location})
        if getattr(self.env, "PHASE14_PRE_CUTOVER_READ_ONLY", "false") == "true":
            token = getattr(self.env, "PHASE14_SMOKE_TOKEN", None)
            if not token:
                raise RuntimeError("Pre-cutover smoke token is required.")
            if request.headers.get("X-Phase14-Smoke-Token") != token:
                return Response("Not Found", status=404)
            if request.method not in ("GET", "HEAD"):
                return Response("Method Not Allowed", status=405)
            if path == "/__phase14/readiness":
                marker = getattr(self.env, "PHASE14_VERSION", None)
                if not marker:
                    raise RuntimeError("Pre-cutover version marker is required.")
                return Response(marker, headers={"X-Phase14-Version": marker,
                                                 "Cache-Control": "no-store"})

        secret_key = getattr(self.env, "SECRET_KEY", None)
        backend = getattr(self.env, "STORAGE_BACKEND", None)
        if not secret_key:
            raise RuntimeError("SECRET_KEY is required.")
        if backend != "d1":
            raise RuntimeError("Worker STORAGE_BACKEND must be d1.")

        # Worker vars and secrets are bindings, not process environment vars.
        # D1/R2 objects remain request-local through workers.env in WSGI.
        app.config["SECRET_KEY"] = secret_key
        app.config["_WORKER_SECRET_KEY_CONFIGURED"] = True
        app.config["STORAGE_BACKEND"] = backend
        app.config["PHASE14_PRE_CUTOVER_READ_ONLY"] = getattr(
            self.env, "PHASE14_PRE_CUTOVER_READ_ONLY", "false"
        )
        archive_backend = getattr(self.env, "HISTORY_ARCHIVE_BACKEND", None)
        if archive_backend is not None:
            app.config["HISTORY_ARCHIVE_BACKEND"] = archive_backend
        for name in WORKER_SCALAR_SETTINGS:
            app.config[name] = getattr(self.env, name, None)
        if path.startswith("/static/") and request.method in ("GET", "HEAD"):
            assets = getattr(self.env, "ASSETS", None)
            if assets is None:
                raise RuntimeError("Worker ASSETS binding is required for static files.")
            return await assets.fetch("https://assets.local/" + path[len("/static/"):])
        return await wsgi.fetch(app, request, self.env)
