"""CPython route tests; the real WSGI adapter is checked by smoke_http.py."""

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture
def poc_app(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("ALLOW_DEV_SECRET_KEY", raising=False)
    source = Path(__file__).resolve().parents[1] / "cloudflare-poc/src/poc_app.py"
    spec = importlib.util.spec_from_file_location("poc_app", source)
    module = importlib.util.module_from_spec(spec)
    # Flask resolves its template root using the module's file.
    monkeypatch.setitem(sys.modules, "poc_app", module)
    spec.loader.exec_module(module)
    module.app.config["TESTING"] = True
    return module.app


def test_poc_health_without_secret(poc_app):
    assert poc_app.secret_key is None
    response = poc_app.test_client().get("/cloudflare-poc/health")
    assert response.status_code == 200
    assert response.json == {
        "status": "ok",
        "runtime": "cloudflare-python-worker",
        "framework": "flask",
    }
    assert "Set-Cookie" not in response.headers


def test_poc_blueprint(poc_app):
    assert "cloudflare_poc" in poc_app.blueprints
    response = poc_app.test_client().get("/cloudflare-poc/blueprint")
    assert response.status_code == 200
    assert response.json == {"status": "ok", "blueprint": "cloudflare_poc"}


def test_poc_loads_jinja_template(poc_app):
    response = poc_app.test_client().get("/cloudflare-poc/template")
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert "<h1>shuttlers-match-app</h1>" in response.text
    assert "<p>Cloudflare Flask PoC</p>" in response.text
    assert "{{" not in response.text
