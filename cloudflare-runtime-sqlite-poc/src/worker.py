"""Cloudflare runtime-initialization PoC using only disposable synthetic data."""
import os
from pathlib import Path
from urllib.parse import urlsplit

from workers import Response, WorkerEntrypoint, wsgi

import side_effect_guard


# Synthetic and intentionally confined to this PoC. Never return the value publicly.
os.environ["SECRET_KEY"] = "cloudflare-runtime-poc-only-secret-not-production"

from app import app
import app as app_module
from models import Participant, db
from sqlalchemy import inspect


SYNTHETIC_NAME = "Runtime PoC Player"
SYNTHETIC_CARD = "JOKER_RED"
ALLOWED_PATHS = {
    "/api/participants",
    "/runtime-poc/status",
    "/runtime-poc/count",
    "/runtime-poc/seed",
}


def _normal(path):
    return Path(os.path.abspath(os.fspath(path))).resolve(strict=False)


def _is_within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _assert_disposable_database():
    instance = _normal(app.instance_path)
    expected = _normal(Path(app.instance_path) / "participants.db")
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    prefix = "sqlite:///"
    if not uri.startswith(prefix) or _normal(uri[len(prefix):]) != expected:
        raise RuntimeError("PoC SQLite URI does not match app.instance_path")

    # Keep Flask's natural instance location. Pyodide may place it outside the
    # metadata directory containing app.py, so do not assume a sibling layout.
    bundled_module_dir = Path(os.path.abspath(app_module.__file__)).parent
    if expected.parent != instance or expected.name != "participants.db":
        raise RuntimeError("PoC database does not match the natural Flask instance path")

    # Local builds live below the repository. Reject every enclosing Git root's
    # real instance directory, including symlink-resolved aliases.
    for ancestor in (bundled_module_dir, *bundled_module_dir.parents):
        if (ancestor / ".git").exists():
            repo_instance = _normal(ancestor / "instance")
            if expected == repo_instance or _is_within(expected, repo_instance):
                raise RuntimeError("PoC database resolves inside a repository instance directory")
    return expected


EXPECTED_DB = _assert_disposable_database()
side_effect_guard.configure_runtime(EXPECTED_DB)

# This identity assertion is evaluated before any request can reach workers.wsgi.
if app.wsgi_app is not app_module.runtime_wsgi_app:
    raise RuntimeError("PoC must dispatch the current runtime WSGI wrapper")


def _counts():
    return {
        "table_count": len(inspect(db.engine).get_table_names()),
        "participant_count": Participant.query.count(),
    }


@app.get("/runtime-poc/status")
def runtime_poc_status():
    counts = _counts()
    return {
        "runtime_initialized": bool(app_module._runtime_initialized),
        "secret_key_set": bool(app.secret_key),
        "db_exists": EXPECTED_DB.exists(),
        "db_is_disposable": True,
        **counts,
    }


@app.get("/runtime-poc/count")
def runtime_poc_count():
    return {
        "runtime_initialized": bool(app_module._runtime_initialized),
        "db_exists": EXPECTED_DB.exists(),
        "participant_count": Participant.query.count(),
    }


@app.get("/runtime-poc/seed")
def runtime_poc_seed():
    participant = Participant.query.filter_by(name=SYNTHETIC_NAME).first()
    created = participant is None
    if created:
        participant = Participant(
            name=SYNTHETIC_NAME,
            gender="male",
            level="intermediate",
            weight=2.0,
            games_played=0,
            active=True,
            card=SYNTHETIC_CARD,
        )
        db.session.add(participant)
        db.session.commit()
    return {
        "created": created,
        "participant_count": Participant.query.count(),
    }


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        path = urlsplit(request.url).path
        if str(request.method) not in {"GET", "HEAD"}:
            return Response("Method not allowed", status=405)
        if path not in ALLOWED_PATHS:
            return Response("Not found", status=404)

        runtime_before = app_module._runtime_initialized
        database_before = EXPECTED_DB.exists()
        response = await wsgi.fetch(app.wsgi_app, request, self.env)
        response.headers.set("X-Runtime-Poc-Initialized-Before", str(runtime_before).lower())
        response.headers.set("X-Runtime-Poc-Db-Before", str(database_before).lower())
        response.headers.set(
            "X-Runtime-Poc-Initialized-After",
            str(app_module._runtime_initialized).lower(),
        )
        response.headers.set("X-Runtime-Poc-Db-After", str(EXPECTED_DB.exists()).lower())
        return response
