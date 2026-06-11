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
