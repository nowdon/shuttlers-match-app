import importlib
import json
import sys
from types import SimpleNamespace


def import_app_with_config(monkeypatch, tmp_path, config=None):
    if config is None:
        config = {"level_map": {}, "gender_weight": {}}
    (tmp_path / "config.json").write_text(
        json.dumps(config),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("app", None)
    return importlib.import_module("app")


def test_secret_key_uses_environment_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "test-secret-from-env")

    app_module = import_app_with_config(monkeypatch, tmp_path)

    assert app_module.app.secret_key == "test-secret-from-env"


def test_secret_key_uses_fixed_development_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("SECRET_KEY", raising=False)

    first_app_module = import_app_with_config(monkeypatch, tmp_path)
    first_secret_key = first_app_module.app.secret_key
    sys.modules.pop("app", None)
    second_app_module = importlib.import_module("app")

    assert first_secret_key == app_module_secret_key(second_app_module)
    assert first_secret_key == first_app_module.DEFAULT_DEV_SECRET_KEY


def test_missing_secret_key_logs_warning_in_production(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("FLASK_ENV", "production")

    import_app_with_config(monkeypatch, tmp_path)

    assert "SECRET_KEY is not set" in caplog.text


def app_module_secret_key(app_module):
    return app_module.app.secret_key


def test_flash_messages_still_use_session(monkeypatch, tmp_path):
    app_module = import_app_with_config(
        monkeypatch,
        tmp_path,
        {
            "level_map": {"beginner": 1},
            "gender_weight": {"male": 1.0},
        },
    )
    monkeypatch.setattr(
        app_module,
        "Participant",
        SimpleNamespace(query=SimpleNamespace(all=lambda: [])),
    )
    client = app_module.app.test_client()

    response = client.post(
        "/register",
        data={"name": "", "gender": "male", "level": "beginner", "card": "♥A"},
    )

    assert response.status_code == 302
    with client.session_transaction() as flask_session:
        assert flask_session["_flashes"] == [("error", "すべての項目を正しく入力してください")]
