import json

from flask import Flask

from routes.api import api_bp
from utils.match_state import load_match_state, save_match_state


DEFAULT_MATCH_STATE = {
    "match_active": False,
    "match_count": 0,
    "matches": [],
    "bench": [],
}


def test_load_and_save_match_state_use_shared_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    assert load_match_state() == DEFAULT_MATCH_STATE

    state = {
        "match_active": True,
        "match_count": 2,
        "matches": [[1, 2, 3, 4]],
        "bench": [5],
    }
    save_match_state(state)

    assert load_match_state() == state
    assert json.loads((tmp_path / "match_state.json").read_text(encoding="utf-8")) == state


def test_match_state_api_uses_shared_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app = Flask(__name__)
    app.register_blueprint(api_bp)

    response = app.test_client().get("/api/match_state")

    assert response.status_code == 200
    assert response.get_json() == DEFAULT_MATCH_STATE
