"""Cloudflare Python Worker entrypoint for the existing Flask application."""

from workers import WorkerEntrypoint, wsgi

from app import app


class Default(WorkerEntrypoint):
    async def fetch(self, request):
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
        archive_backend = getattr(self.env, "HISTORY_ARCHIVE_BACKEND", None)
        if archive_backend is not None:
            app.config["HISTORY_ARCHIVE_BACKEND"] = archive_backend
        return await wsgi.fetch(app, request, self.env)
