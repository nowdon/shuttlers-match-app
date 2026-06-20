import json
from types import SimpleNamespace

from flask import Flask, session

import utils.reset as reset_module


def test_reset_clears_shared_match_state_without_managing_confirmed_session_keys(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test"
    state = {
        "match_active": True,
        "matches": [[1, 2, 3, 4]],
        "bench": [5],
        "match_count": 3,
    }
    saved_states = []
    participants = [SimpleNamespace(games_played=2), SimpleNamespace(games_played=1)]

    monkeypatch.setattr(reset_module, "load_match_state", lambda: state.copy())
    monkeypatch.setattr(
        reset_module,
        "save_match_state_full",
        lambda *args: saved_states.append(args),
    )
    monkeypatch.setattr(
        reset_module,
        "Participant",
        SimpleNamespace(query=SimpleNamespace(all=lambda: participants)),
    )
    monkeypatch.setattr(
        reset_module.db,
        "session",
        SimpleNamespace(commit=lambda: None),
    )
    monkeypatch.setattr(reset_module, "clear_draft_state", lambda: None)

    with app.test_request_context():
        session["last_confirmed_matches"] = [[9]]
        session["last_confirmed_bench"] = [10]

        reset_module.reset_match_state()

        assert saved_states == [(False, [], [], 0)]
        assert [participant.games_played for participant in participants] == [0, 0]
        assert session["last_confirmed_matches"] == [[9]]
        assert session["last_confirmed_bench"] == [10]


def test_reset_match_state_does_not_preserve_court_count(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "match_state.json").write_text(
        json.dumps(
            {
                "match_active": True,
                "matches": [[1, 2, 3, 4]],
                "bench": [5],
                "match_count": 2,
                "court_count": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        reset_module,
        "Participant",
        SimpleNamespace(query=SimpleNamespace(all=lambda: [])),
    )
    monkeypatch.setattr(reset_module.db, "session", SimpleNamespace(commit=lambda: None))
    monkeypatch.setattr(reset_module, "clear_draft_state", lambda: None)

    reset_module.reset_match_state()

    state = json.loads((tmp_path / "match_state.json").read_text(encoding="utf-8"))
    assert state["match_active"] is False
    assert state["matches"] == []
    assert state["bench"] == []
    assert state["match_count"] == 0
    assert "court_count" not in state
