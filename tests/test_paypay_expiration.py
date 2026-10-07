import importlib
import json
import os
import sys
from datetime import date
from types import SimpleNamespace
from conftest import clear_app_modules, patch_app_dependency


def import_app(monkeypatch, tmp_path, config):
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("ALLOW_DEV_SECRET_KEY", "1")
    clear_app_modules()
    app_module = importlib.import_module("app")
    app_module.app.config.update(TESTING=True)
    return app_module


def stub_index_dependencies(monkeypatch, app_module):
    patch_app_dependency(monkeypatch, app_module, "generate_card_layout", lambda participants: ({}, {"♠": [], "♥": [], "♦": [], "♣": []}))
    patch_app_dependency(monkeypatch, app_module, "get_active_draft", lambda: None)
    patch_app_dependency(monkeypatch, app_module, "load_match_state", lambda: {"matches": [], "bench": [], "match_active": False, "match_count": 0})
    patch_app_dependency(monkeypatch, app_module, "Participant", SimpleNamespace(query=SimpleNamespace(order_by=lambda field: SimpleNamespace(all=lambda: [])), card=None))


def paypay_config(expiration):
    return {
        "paypay_links": {"adults": "https://example.com/adults", "students": ""},
        "paypay_link_expirations": {"adults": expiration, "students": ""},
        "level_map": {},
        "gender_weight": {},
    }


def test_paypay_warning_not_shown_two_days_before(monkeypatch, tmp_path):
    app_module = import_app(monkeypatch, tmp_path, paypay_config("2026-07-25"))

    warnings = app_module.build_paypay_expiration_warnings(app_module.load_config(), today=date(2026, 7, 23))

    assert warnings == []


def test_paypay_warning_shown_day_before(monkeypatch, tmp_path):
    app_module = import_app(monkeypatch, tmp_path, paypay_config("2026-07-25"))

    warnings = app_module.build_paypay_expiration_warnings(app_module.load_config(), today=date(2026, 7, 24))

    assert warnings[0]["message"] == "⚠️ 社会人用PayPayリンクは明日（2026年7月25日）期限切れになります"


def test_paypay_warning_shown_on_expiration_day(monkeypatch, tmp_path):
    app_module = import_app(monkeypatch, tmp_path, paypay_config("2026-07-25"))

    warnings = app_module.build_paypay_expiration_warnings(app_module.load_config(), today=date(2026, 7, 25))

    assert warnings[0]["message"] == "⚠️ 社会人用PayPayリンクは本日（2026年7月25日）期限切れになります"


def test_paypay_warning_shown_after_expiration(monkeypatch, tmp_path):
    app_module = import_app(monkeypatch, tmp_path, paypay_config("2026-07-25"))

    warnings = app_module.build_paypay_expiration_warnings(app_module.load_config(), today=date(2026, 7, 26))

    assert warnings[0]["message"] == "🚨 社会人用PayPayリンクは期限切れです（2026年7月25日）"


def test_paypay_warning_shown_when_url_has_no_expiration(monkeypatch, tmp_path):
    app_module = import_app(monkeypatch, tmp_path, paypay_config(""))

    warnings = app_module.build_paypay_expiration_warnings(app_module.load_config(), today=date(2026, 7, 24))

    assert warnings[0]["message"] == "⚠️ 社会人用PayPayリンクの有効期限が設定されていません"


def test_paypay_warning_not_shown_in_viewer_mode(monkeypatch, tmp_path):
    app_module = import_app(monkeypatch, tmp_path, paypay_config("2026-07-25"))
    patch_app_dependency(monkeypatch, app_module, "get_tokyo_today", lambda: date(2026, 7, 24))
    stub_index_dependencies(monkeypatch, app_module)

    html = app_module.app.test_client().get("/viewer").get_data(as_text=True)

    assert "PayPayリンクは明日" not in html


def test_old_config_loads_without_paypay_expirations(monkeypatch, tmp_path):
    app_module = import_app(monkeypatch, tmp_path, {"paypay_links": {"adults": "https://example.com/adults"}})

    config = app_module.load_config()

    assert config["paypay_link_expirations"] == {"adults": "", "students": ""}


def test_admin_settings_saves_expiration_dates_and_preserves_existing_settings(monkeypatch, tmp_path):
    app_module = import_app(monkeypatch, tmp_path, {
        "paypay_links": {"adults": "old-adults", "students": "old-students"},
        "paypay_link_expirations": {"adults": "", "students": ""},
        "level_map": {"beginner": 1, "intermediate": 2, "advanced": 3},
        "gender_weight": {"male": 1.0, "female": 0.9},
        "custom_setting": {"kept": True},
    })

    response = app_module.app.test_client().post("/admin/settings", data={
        "paypay_adults": "adults",
        "paypay_students": "students",
        "paypay_expiration_adults": "2026-07-25",
        "paypay_expiration_students": "2026-07-26",
        "level_beginner": "1",
        "level_intermediate": "2",
        "level_advanced": "3",
        "weight_male": "1.0",
        "weight_female": "0.9",
        "score_input_mode": "winner_only",
        "consecutive_play_limit": "3",
        "points_per_game": "21",
        "games_per_match": "1",
        "max_points": "21",
    })

    assert response.status_code == 302
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["paypay_link_expirations"] == {"adults": "2026-07-25", "students": "2026-07-26"}
    assert saved["custom_setting"] == {"kept": True}


def test_admin_settings_saves_history_dump_email_and_preserves_other_config(monkeypatch, tmp_path):
    app_module = import_app(monkeypatch, tmp_path, {
        "paypay_links": {"adults": "old-adults", "students": "old-students"},
        "paypay_link_expirations": {"adults": "2026-07-25", "students": "2026-07-26"},
        "level_map": {"beginner": 1, "intermediate": 2, "advanced": 3},
        "gender_weight": {"male": 1.0, "female": 0.9},
        "history_dump_email": {"enabled": False, "recipient": ""},
        "custom_setting": {"kept": True},
    })

    response = app_module.app.test_client().post("/admin/settings", data={
        "paypay_adults": "adults",
        "paypay_students": "students",
        "paypay_expiration_adults": "2026-07-25",
        "paypay_expiration_students": "2026-07-26",
        "level_beginner": "1",
        "level_intermediate": "2",
        "level_advanced": "3",
        "weight_male": "1.0",
        "weight_female": "0.9",
        "score_input_mode": "winner_only",
        "consecutive_play_limit": "3",
        "points_per_game": "21",
        "games_per_match": "1",
        "max_points": "21",
        "history_dump_email_enabled": "on",
        "history_dump_email_recipient": "dump@example.com",
    })

    assert response.status_code == 302
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["history_dump_email"] == {"enabled": True, "recipient": "dump@example.com"}
    assert saved["paypay_link_expirations"] == {"adults": "2026-07-25", "students": "2026-07-26"}
    assert saved["custom_setting"] == {"kept": True}


def test_admin_settings_rejects_enabled_history_dump_email_without_recipient(monkeypatch, tmp_path):
    app_module = import_app(monkeypatch, tmp_path, {
        "paypay_links": {"adults": "old-adults", "students": "old-students"},
        "level_map": {"beginner": 1, "intermediate": 2, "advanced": 3},
        "gender_weight": {"male": 1.0, "female": 0.9},
    })

    response = app_module.app.test_client().post("/admin/settings", data={
        "history_dump_email_enabled": "on",
        "history_dump_email_recipient": "",
    })

    assert response.status_code == 200
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert "history_dump_email" not in saved
