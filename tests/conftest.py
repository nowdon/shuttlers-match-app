import sys
from pathlib import Path

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.engine import make_url

import pytest


def require_temporary_path(path, root):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise AssertionError(f"Test runtime path is outside temporary root: {resolved}")
    return resolved


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    """Isolate paths without importing app or creating a DB for pure unit tests.

    Patch the class initializer so aliases imported during collection are covered.
    Validate DB URLs BEFORE init_app creates engines; config changes afterwards
    cannot move an already initialized SQLAlchemy engine.
    """
    clear_app_modules()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("ALLOW_DEV_SECRET_KEY", "1")
    monkeypatch.setenv("LINE_MESSAGING_ENABLED", "0")
    for name in ("LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN",
                 "LINE_BOT_FRIEND_URL", "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD",
                 "SMTP_PORT", "SMTP_SECURITY", "SMTP_TIMEOUT_SECONDS",
                 "SMTP_FROM_EMAIL", "SMTP_FROM_NAME"):
        monkeypatch.delenv(name, raising=False)

    original_flask_init = Flask.__init__
    original_db_init = SQLAlchemy.init_app
    databases = []

    def isolated_flask_init(self, *args, **kwargs):
        instance = kwargs.setdefault("instance_path", str(tmp_path / "instance"))
        require_temporary_path(instance, tmp_path)
        original_flask_init(self, *args, **kwargs)

    def isolated_db_init(self, app):
        require_temporary_path(app.instance_path, tmp_path)
        urls = [app.config.get("SQLALCHEMY_DATABASE_URI")]
        for bind in app.config.get("SQLALCHEMY_BINDS", {}).values():
            urls.append(bind.get("url") if isinstance(bind, dict) else bind)
        for value in urls:
            if value is None:
                continue
            url = make_url(value)
            if url.get_backend_name() != "sqlite":
                raise AssertionError("Tests must use temporary SQLite databases")
            if url.database not in (None, "", ":memory:"):
                require_temporary_path(Path(app.instance_path) / url.database, tmp_path)
        original_db_init(self, app)
        # Keep every app, including ones removed from sys.modules mid-test.
        with app.app_context():
            databases.append((app, self.session, tuple(self.engines.values())))

    monkeypatch.setattr(Flask, "__init__", isolated_flask_init)
    monkeypatch.setattr(SQLAlchemy, "init_app", isolated_db_init)
    try:
        yield tmp_path
    finally:
        try:
            for app, session, engines in reversed(databases):
                with app.app_context():
                    session.remove()
                    for engine in engines:
                        engine.dispose()
        finally:
            clear_app_modules()


ROUTE_MODULE_NAMES = (
    "routes.api",
    "routes.helpers",
    "routes.participant",
    "routes.line",
    "routes.admin",
    "routes.match",
    "routes.history",
)


def clear_app_modules():
    sys.modules.pop("app", None)
    sys.modules.pop("init_db", None)
    for module_name in ROUTE_MODULE_NAMES:
        module = sys.modules.pop(module_name, None)
        parent_name, attribute = module_name.rsplit(".", 1)
        parent = sys.modules.get(parent_name)
        if module is not None and getattr(parent, attribute, None) is module:
            delattr(parent, attribute)


DATA_MODULE_NAMES = (
    "data.participants",
    "data.match_history",
    "data.match_sessions",
    "data.line_notifications",
)


def patch_app_dependency(monkeypatch, app_module, name, value):
    monkeypatch.setattr(app_module, name, value)
    # Model dependencies now live at the data boundary; keep existing fakes
    # attached to the code under test without changing their assertions.
    for module_name in ROUTE_MODULE_NAMES + DATA_MODULE_NAMES:
        route_module = sys.modules.get(module_name)
        if route_module is not None and hasattr(route_module, name):
            monkeypatch.setattr(route_module, name, value)
