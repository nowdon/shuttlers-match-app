import importlib
import json
import sys
from types import SimpleNamespace


def test_creating_draft_preserves_confirmed_matches(monkeypatch, tmp_path):
    config = {
        "level_map": {},
        "gender_weight": {},
    }
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("app", None)
    app_module = importlib.import_module("app")

    participants = [SimpleNamespace(id=player_id) for player_id in range(1, 6)]
    confirmed_matches = [[11, 12, 13, 14]]
    state = {
        "match_active": True,
        "matches": confirmed_matches,
        "bench": [15],
        "match_count": 3,
    }
    saved_state = []

    participant_model = SimpleNamespace(query=SimpleNamespace(all=lambda: participants))
    monkeypatch.setattr(app_module, "Participant", participant_model)
    monkeypatch.setattr(app_module, "generate_matches", lambda players, courts: ([players[:4]], players[4:]))
    monkeypatch.setattr(app_module, "load_match_state", lambda: state.copy())
    monkeypatch.setattr(app_module, "save_draft_state", lambda matches, bench: None)
    monkeypatch.setattr(app_module, "save_match_state_full", lambda *args: saved_state.append(args))

    response = app_module.app.test_client().post("/match", data={"court_count": "1"})

    assert response.status_code == 302
    assert saved_state == [(True, confirmed_matches, [15], 3)]


def load_test_app(monkeypatch, tmp_path):
    config = {
        "level_map": {},
        "gender_weight": {},
    }
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("app", None)
    app_module = importlib.import_module("app")

    participants = [SimpleNamespace(id=player_id) for player_id in range(1, 6)]
    participant_model = SimpleNamespace(query=SimpleNamespace(all=lambda: participants))
    monkeypatch.setattr(app_module, "Participant", participant_model)
    monkeypatch.setattr(
        app_module,
        "load_match_state",
        lambda: {"match_active": False, "matches": [], "bench": [], "match_count": 0},
    )
    monkeypatch.setattr(app_module, "calculate_pair_score", lambda pair, *_: {"pair": pair})
    monkeypatch.setattr(
        app_module,
        "render_template",
        lambda template, **context: json.dumps(
            {
                "template": template,
                "matches": [[player.id for player in group] for group in context.get("matches", [])],
                "bench": [player.id for player in context.get("bench", [])],
            }
        ),
    )
    return app_module


def test_match_edit_without_session_does_not_clear_shared_draft(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    draft = {
        "draft": True,
        "timestamp": "2026-06-12T00:00:00+00:00",
        "matches": [[1, 2, 3, 4]],
        "bench": [5],
    }
    (tmp_path / "draft_state.json").write_text(json.dumps(draft), encoding="utf-8")

    response = app_module.app.test_client().get("/match/edit")

    saved_draft = json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8"))
    assert response.status_code == 200
    assert saved_draft["matches"] == draft["matches"]
    assert saved_draft["bench"] == draft["bench"]


def test_match_edit_restores_active_shared_draft_to_session(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    draft = {
        "draft": True,
        "matches": [[1, 2, 3, 4]],
        "bench": [5],
    }
    (tmp_path / "draft_state.json").write_text(json.dumps(draft), encoding="utf-8")
    client = app_module.app.test_client()

    response = client.get("/match/edit")

    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True)) == {
        "template": "match_edit.html",
        "matches": draft["matches"],
        "bench": draft["bench"],
    }
    with client.session_transaction() as draft_session:
        assert draft_session["draft_matches"] == draft["matches"]
        assert draft_session["draft_bench"] == draft["bench"]


def test_match_edit_without_available_draft_redirects_to_match_form(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)

    response = app_module.app.test_client().get("/match/edit")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match")
    assert not (tmp_path / "draft_state.json").exists()


def test_match_edit_keeps_using_existing_session_draft(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    shared_draft = {
        "draft": True,
        "matches": [[5, 4, 3, 2]],
        "bench": [1],
    }
    (tmp_path / "draft_state.json").write_text(json.dumps(shared_draft), encoding="utf-8")
    client = app_module.app.test_client()
    with client.session_transaction() as draft_session:
        draft_session["draft_matches"] = [[1, 2, 3, 4]]
        draft_session["draft_bench"] = [5]

    response = client.get("/match/edit")

    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True)) == {
        "template": "match_edit.html",
        "matches": [[1, 2, 3, 4]],
        "bench": [5],
    }
    saved_draft = json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8"))
    assert saved_draft["matches"] == [[1, 2, 3, 4]]
    assert saved_draft["bench"] == [5]
