import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def import_app(monkeypatch, tmp_path, config=None):
    if config is None:
        config = {
            "paypay_links": {"adults": "", "students": ""},
            "level_map": {"beginner": 1, "intermediate": 2, "advanced": 3},
            "gender_weight": {"male": 1.0, "female": 1.0},
        }
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("ALLOW_DEV_SECRET_KEY", "1")
    sys.modules.pop("app", None)
    return importlib.import_module("app")


def test_register_validation_flash_is_rendered(monkeypatch, tmp_path):
    app_module = import_app(monkeypatch, tmp_path)
    monkeypatch.setattr(
        app_module,
        "Participant",
        SimpleNamespace(query=SimpleNamespace(all=lambda: [])),
    )
    client = app_module.app.test_client()

    response = client.post(
        "/register",
        data={"name": "", "gender": "male", "level": "beginner", "card": "♥A"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "すべての項目を正しく入力してください" in response.get_data(as_text=True)


def test_reset_match_flash_is_rendered_on_match_form(monkeypatch, tmp_path):
    app_module = import_app(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "reset_match_state", lambda: None)
    client = app_module.app.test_client()

    response = client.post("/reset_match", follow_redirects=True)

    assert response.status_code == 200
    assert "試合状態をリセットしました" in response.get_data(as_text=True)


def test_admin_settings_save_flash_is_rendered(monkeypatch, tmp_path):
    app_module = import_app(monkeypatch, tmp_path)
    client = app_module.app.test_client()

    response = client.post(
        "/admin/settings",
        data={
            "paypay_adults": "https://example.com/adults",
            "paypay_students": "https://example.com/students",
            "level_beginner": "1",
            "level_intermediate": "2",
            "level_advanced": "3",
            "weight_male": "1.0",
            "weight_female": "1.0",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "設定を保存しました" in response.get_data(as_text=True)


def test_reset_db_flash_is_rendered_on_admin_settings(monkeypatch, tmp_path):
    app_module = import_app(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "reset_match_state", lambda: None)
    monkeypatch.setattr(
        app_module,
        "Participant",
        SimpleNamespace(query=SimpleNamespace(delete=lambda: 0)),
    )
    monkeypatch.setattr(app_module, "dump_match_history_to_json", lambda reason: None)
    client = app_module.app.test_client()

    response = client.post("/admin/reset_db", follow_redirects=True)

    assert response.status_code == 200
    assert "参加者データと試合情報をすべて削除しました" in response.get_data(as_text=True)


def test_match_edit_includes_flash_messages_partial():
    template = Path(__file__).resolve().parents[1] / "templates" / "match_edit.html"

    assert '{% include "_flash_messages.html" %}' in template.read_text(encoding="utf-8")


def test_fixed_pair_bench_swap_rejection_flash_is_rendered(monkeypatch, tmp_path):
    app_module = import_app(monkeypatch, tmp_path)
    participants = [
        SimpleNamespace(id=1, card="♥A", name="player-1", games_played=0),
        SimpleNamespace(id=2, card="♥2", name="player-2", games_played=0),
        SimpleNamespace(id=3, card="♥3", name="player-3", games_played=0),
        SimpleNamespace(id=4, card="♥4", name="player-4", games_played=0),
        SimpleNamespace(id=5, card="♥5", name="player-5", games_played=0),
    ]
    monkeypatch.setattr(
        app_module,
        "Participant",
        SimpleNamespace(query=SimpleNamespace(all=lambda: participants)),
    )
    monkeypatch.setattr(
        app_module,
        "load_match_state",
        lambda: {"match_active": False, "matches": [], "bench": [], "match_count": 0},
    )
    monkeypatch.setattr(app_module, "get_match_count", lambda: 1)
    monkeypatch.setattr(app_module, "calculate_participant_win_stats", lambda: {})
    monkeypatch.setattr(
        app_module,
        "calculate_pair_score",
        lambda pair, *_: SimpleNamespace(
            players=[SimpleNamespace(score=0), SimpleNamespace(score=0)],
            total_score=0,
        ),
    )
    (tmp_path / "draft_state.json").write_text(
        json.dumps({"draft": True, "matches": [[1, 2, 3, 4]], "bench": [5], "fixed_pairs": [[1, 2]]}),
        encoding="utf-8",
    )
    client = app_module.app.test_client()

    response = client.post(
        "/match/swap",
        data={"swap_ids": "1,5", "mode": "admin"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "固定ペアはベンチ参加者と個別に入れ替えできません" in response.get_data(as_text=True)
