import importlib
import json
import sys
from flask import render_template as flask_render_template
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
    saved_kwargs = []

    participant_model = SimpleNamespace(query=SimpleNamespace(all=lambda: participants))
    monkeypatch.setattr(app_module, "Participant", participant_model)
    monkeypatch.setattr(app_module, "generate_matches", lambda players, courts: ([players[:4]], players[4:]))
    monkeypatch.setattr(app_module, "load_match_state", lambda: state.copy())
    monkeypatch.setattr(app_module, "save_draft_state", lambda matches, bench, **kwargs: None)
    def save_state(*args, **kwargs):
        saved_state.append(args)
        saved_kwargs.append(kwargs)

    monkeypatch.setattr(app_module, "save_match_state_full", save_state)

    client = app_module.app.test_client()
    response = client.post("/match", data={"court_count": "1"})

    assert response.status_code == 302
    assert saved_state == [(True, confirmed_matches, [15], 3)]
    assert saved_kwargs == [{"court_count": 1}]
    with client.session_transaction() as draft_session:
        assert "draft_matches" not in draft_session
        assert "draft_bench" not in draft_session
        assert "court_count" not in draft_session


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
                **({"court_count": context.get("court_count")} if template == "match_edit.html" else {}),
            }
        ),
    )
    return app_module


def load_db_test_app(monkeypatch, tmp_path):
    config = {
        "level_map": {},
        "gender_weight": {},
    }
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("app", None)
    app_module = importlib.import_module("app")
    app_module.app.config["TESTING"] = True
    participants = [
        SimpleNamespace(id=player_id, games_played=games_played)
        for player_id, games_played in enumerate([2, 1, 0, 3, 4], start=1)
    ]

    class IdField:
        @staticmethod
        def in_(ids):
            return set(ids)

    class ParticipantQuery:
        def __init__(self, players):
            self.players = players
            self.selected_ids = None

        def filter(self, selected_ids):
            query = ParticipantQuery(self.players)
            query.selected_ids = selected_ids
            return query

        def all(self):
            if self.selected_ids is None:
                return list(self.players)
            return [player for player in self.players if player.id in self.selected_ids]

        def order_by(self, _):
            return self

        def get(self, player_id):
            return next(player for player in self.players if player.id == player_id)

    participant_model = SimpleNamespace(id=IdField(), query=ParticipantQuery(participants))
    monkeypatch.setattr(app_module, "Participant", participant_model)
    monkeypatch.setattr(app_module.db, "session", SimpleNamespace(commit=lambda: None, remove=lambda: None))
    return app_module


def test_rematch_draft_preserves_confirmed_court_count_before_confirmation(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    participants = [SimpleNamespace(id=player_id) for player_id in range(1, 10)]
    confirmed_matches = [[1, 2, 3, 4], [5, 6, 7, 8]]
    confirmed_state = {
        "match_active": True,
        "matches": confirmed_matches,
        "bench": [9],
        "match_count": 4,
        "court_count": 2,
    }
    saved_state = confirmed_state.copy()

    def save_state(match_active, matches, bench, match_count, *, court_count=None):
        saved_state.update(
            match_active=match_active,
            matches=matches,
            bench=bench,
            match_count=match_count,
        )
        if isinstance(court_count, int) and court_count > 0:
            saved_state["court_count"] = court_count
        else:
            saved_state.pop("court_count", None)

    participant_model = SimpleNamespace(query=SimpleNamespace(all=lambda: participants))
    monkeypatch.setattr(app_module, "Participant", participant_model)
    monkeypatch.setattr(app_module, "load_match_state", lambda: saved_state.copy())
    monkeypatch.setattr(
        app_module,
        "generate_matches",
        lambda players, courts: ([players[:4], players[4:8]], players[8:]),
    )
    monkeypatch.setattr(app_module, "save_match_state_full", save_state)
    client = app_module.app.test_client()

    response = client.post("/match", data={"mode": "admin"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match/edit?mode=admin")
    saved_draft = json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8"))
    assert saved_draft["draft"] is True
    assert saved_draft["court_count"] == 2
    assert saved_state == confirmed_state
    with client.session_transaction() as draft_session:
        assert "court_count" not in draft_session


def test_get_draft_court_count_returns_positive_integer(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)

    assert app_module.get_draft_court_count({"court_count": 3, "matches": [[1, 2, 3, 4]]}) == 3


def test_get_draft_court_count_falls_back_for_zero(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)

    assert app_module.get_draft_court_count({"court_count": 0, "matches": []}) == 1


def test_get_draft_court_count_falls_back_for_invalid_value(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)

    assert app_module.get_draft_court_count({"court_count": "2", "matches": []}) == 1


def test_get_draft_court_count_uses_match_count_for_old_schema(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)

    assert app_module.get_draft_court_count({"matches": [[1, 2, 3, 4], [5, 6, 7, 8]]}) == 2


def test_get_draft_court_count_falls_back_for_empty_matches(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)

    assert app_module.get_draft_court_count({"matches": []}) == 1


def test_get_draft_court_count_falls_back_for_invalid_matches(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)

    assert app_module.get_draft_court_count({"matches": "invalid"}) == 1


def test_match_edit_without_session_does_not_clear_shared_draft(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    draft = {
        "draft": True,
        "timestamp": "2026-06-12T00:00:00+00:00",
        "matches": [[1, 2, 3, 4]],
        "bench": [5],
        "court_count": 2,
    }
    (tmp_path / "draft_state.json").write_text(json.dumps(draft), encoding="utf-8")

    response = app_module.app.test_client().get("/match/edit")

    saved_draft = json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8"))
    assert response.status_code == 200
    assert saved_draft["matches"] == draft["matches"]
    assert saved_draft["bench"] == draft["bench"]
    assert saved_draft["court_count"] == draft["court_count"]


def test_match_edit_uses_active_shared_draft_without_saving_session(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    draft = {
        "draft": True,
        "matches": [[1, 2, 3, 4]],
        "bench": [5],
        "court_count": 3,
    }
    (tmp_path / "draft_state.json").write_text(json.dumps(draft), encoding="utf-8")
    client = app_module.app.test_client()

    response = client.get("/match/edit")

    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True)) == {
        "template": "match_edit.html",
        "matches": draft["matches"],
        "bench": draft["bench"],
        "court_count": draft["court_count"],
    }
    with client.session_transaction() as draft_session:
        assert "draft_matches" not in draft_session
        assert "draft_bench" not in draft_session
        assert "court_count" not in draft_session


def test_match_edit_without_available_draft_redirects_to_match_form(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)

    response = app_module.app.test_client().get("/match/edit")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match")
    assert not (tmp_path / "draft_state.json").exists()


def test_match_edit_prefers_active_shared_draft_over_session_draft(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    shared_draft = {
        "draft": True,
        "matches": [[5, 4, 3, 2]],
        "bench": [1],
        "court_count": 4,
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
        "matches": shared_draft["matches"],
        "bench": shared_draft["bench"],
        "court_count": shared_draft["court_count"],
    }
    saved_draft = json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8"))
    assert saved_draft == shared_draft
    with client.session_transaction() as draft_session:
        assert draft_session["draft_matches"] == [[1, 2, 3, 4]]
        assert draft_session["draft_bench"] == [5]
        assert "court_count" not in draft_session


def test_match_edit_uses_match_count_when_shared_draft_has_old_schema(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    old_schema_draft = {
        "draft": True,
        "matches": [[1, 2, 3, 4], [5]],
        "bench": [],
    }
    (tmp_path / "draft_state.json").write_text(json.dumps(old_schema_draft), encoding="utf-8")
    client = app_module.app.test_client()

    response = client.get("/match/edit")

    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True)) == {
        "template": "match_edit.html",
        "matches": old_schema_draft["matches"],
        "bench": old_schema_draft["bench"],
        "court_count": 2,
    }
    with client.session_transaction() as draft_session:
        assert "draft_matches" not in draft_session
        assert "draft_bench" not in draft_session
        assert "court_count" not in draft_session


def test_match_edit_without_active_shared_draft_ignores_stale_session_draft(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as draft_session:
        draft_session["draft_matches"] = [[1, 2, 3, 4]]
        draft_session["draft_bench"] = [5]

    response = client.get("/match/edit")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match")
    assert not (tmp_path / "draft_state.json").exists()


def test_swap_players_updates_shared_draft_immediately(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    draft = {
        "draft": True,
        "matches": [[1, 2, 3, 4]],
        "bench": [5],
        "court_count": 1,
    }
    (tmp_path / "draft_state.json").write_text(json.dumps(draft), encoding="utf-8")

    client = app_module.app.test_client()
    response = client.post(
        "/match/swap",
        data={"swap_ids": "1,5", "mode": "admin"},
    )

    saved_draft = json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8"))
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match/edit?mode=admin")
    assert saved_draft["matches"] == [[5, 2, 3, 4]]
    assert saved_draft["bench"] == [1]
    assert saved_draft["court_count"] == 1

    edit_response = client.get(response.headers["Location"])
    saved_draft = json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8"))
    assert edit_response.status_code == 200
    assert saved_draft["court_count"] == 1
    with client.session_transaction() as draft_session:
        assert "draft_matches" not in draft_session
        assert "draft_bench" not in draft_session
        assert "court_count" not in draft_session


def test_swap_players_with_old_schema_draft_keeps_edit_flow_working(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    old_schema_draft = {
        "draft": True,
        "matches": [[1, 2, 3, 4], [5]],
        "bench": [],
    }
    draft_path = tmp_path / "draft_state.json"
    draft_path.write_text(json.dumps(old_schema_draft), encoding="utf-8")

    client = app_module.app.test_client()
    response = client.post(
        "/match/swap",
        data={"swap_ids": "1,5", "mode": "admin"},
    )

    saved_draft = json.loads(draft_path.read_text(encoding="utf-8"))
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match/edit?mode=admin")
    assert saved_draft["matches"] == [[5, 2, 3, 4], [1]]
    assert saved_draft["bench"] == []
    assert "court_count" not in saved_draft

    edit_response = client.get(response.headers["Location"])

    assert edit_response.status_code == 200
    assert json.loads(edit_response.get_data(as_text=True)) == {
        "template": "match_edit.html",
        "matches": saved_draft["matches"],
        "bench": saved_draft["bench"],
        "court_count": 2,
    }


def test_swap_players_without_active_draft_does_not_overwrite_state(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    inactive_draft = {
        "draft": False,
        "matches": [[1, 2, 3, 4]],
        "bench": [5],
    }
    draft_path = tmp_path / "draft_state.json"
    draft_path.write_text(json.dumps(inactive_draft), encoding="utf-8")

    response = app_module.app.test_client().post(
        "/match/swap",
        data={"swap_ids": "1,5", "mode": "admin"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match?mode=admin")
    assert json.loads(draft_path.read_text(encoding="utf-8")) == inactive_draft


def test_update_court_count_saves_shared_draft(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    participants = [SimpleNamespace(id=player_id) for player_id in range(1, 7)]

    class ParticipantQuery:
        def filter_by(self, **kwargs):
            assert kwargs == {"active": True}
            return self

        def all(self):
            return participants

    app_module.Participant.query = ParticipantQuery()
    monkeypatch.setattr(
        app_module,
        "generate_matches",
        lambda players, courts: ([players[:4]], players[4:]),
    )

    client = app_module.app.test_client()
    response = client.post(
        "/update_court_count",
        data={"court_count": "2", "mode": "admin"},
    )

    saved_draft = json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8"))
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match/edit?mode=admin")
    assert saved_draft["matches"] == [[1, 2, 3, 4]]
    assert saved_draft["bench"] == [5, 6]
    assert saved_draft["court_count"] == 2

    edit_response = client.get(response.headers["Location"])
    saved_draft = json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8"))
    assert edit_response.status_code == 200
    assert saved_draft["court_count"] == 2
    with client.session_transaction() as draft_session:
        assert "draft_matches" not in draft_session
        assert "draft_bench" not in draft_session
        assert "court_count" not in draft_session


def configure_confirmation_state(monkeypatch, app_module, initial_state):
    participants = [SimpleNamespace(id=player_id, games_played=0) for player_id in range(1, 10)]

    class ParticipantId:
        @staticmethod
        def in_(player_ids):
            return set(player_ids)

    class ParticipantQuery:
        def __init__(self, selected_ids=None):
            self.selected_ids = selected_ids

        def all(self):
            if self.selected_ids is None:
                return participants
            return [player for player in participants if player.id in self.selected_ids]

        def filter(self, selected_ids):
            return ParticipantQuery(selected_ids)

    participant_model = SimpleNamespace(id=ParticipantId(), query=ParticipantQuery())
    state = initial_state.copy()

    def save_state(match_active, matches, bench, match_count, *, court_count=None):
        state.update(
            match_active=match_active,
            matches=matches,
            bench=bench,
            match_count=match_count,
        )
        if isinstance(court_count, int) and court_count > 0:
            state["court_count"] = court_count
        else:
            state.pop("court_count", None)

    monkeypatch.setattr(app_module, "Participant", participant_model)
    monkeypatch.setattr(app_module, "load_match_state", lambda: state.copy())
    monkeypatch.setattr(app_module, "save_match_state_full", save_state)
    monkeypatch.setattr(app_module.db, "session", SimpleNamespace(commit=lambda: None, remove=lambda: None))
    return participants, state


def test_confirm_match_prefers_active_shared_draft_over_session_draft(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    _, state = configure_confirmation_state(
        monkeypatch,
        app_module,
        {"match_active": True, "matches": [[9]], "bench": [], "match_count": 2},
    )
    shared_draft = {"draft": True, "matches": [[5, 6, 7, 8]], "bench": [9]}
    (tmp_path / "draft_state.json").write_text(json.dumps(shared_draft), encoding="utf-8")
    client = app_module.app.test_client()
    with client.session_transaction() as draft_session:
        draft_session["draft_matches"] = [[1, 2, 3, 4]]
        draft_session["draft_bench"] = [5]

    response = client.post("/match/confirm", data={"mode": "admin"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match/result?mode=admin")
    assert state == {
        "match_active": True,
        "matches": shared_draft["matches"],
        "bench": shared_draft["bench"],
        "match_count": 3,
    }
    with client.session_transaction() as confirmed_session:
        assert "last_confirmed_matches" not in confirmed_session
        assert "last_confirmed_bench" not in confirmed_session
    assert not (tmp_path / "draft_state.json").exists()


def test_confirm_match_uses_active_shared_draft_without_session(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    participants, state = configure_confirmation_state(
        monkeypatch,
        app_module,
        {"match_active": False, "matches": [], "bench": [], "match_count": 0},
    )
    shared_draft = {"draft": True, "matches": [[1, 2, 3, 4]], "bench": [5]}
    (tmp_path / "draft_state.json").write_text(json.dumps(shared_draft), encoding="utf-8")

    response = app_module.app.test_client().post("/match/confirm")

    assert response.status_code == 302
    assert state == {
        "match_active": True,
        "matches": shared_draft["matches"],
        "bench": shared_draft["bench"],
        "match_count": 1,
    }
    assert [player.games_played for player in participants[:5]] == [1, 1, 1, 1, 0]
    assert not (tmp_path / "draft_state.json").exists()


def test_confirm_match_saves_draft_court_count_to_confirmed_state(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    _, state = configure_confirmation_state(
        monkeypatch,
        app_module,
        {"match_active": False, "matches": [], "bench": [], "match_count": 0},
    )
    shared_draft = {
        "draft": True,
        "matches": [[1, 2, 3, 4]],
        "bench": [5],
        "court_count": 3,
    }
    (tmp_path / "draft_state.json").write_text(json.dumps(shared_draft), encoding="utf-8")

    response = app_module.app.test_client().post("/match/confirm")

    assert response.status_code == 302
    assert state["court_count"] == 3
    assert not (tmp_path / "draft_state.json").exists()


def test_revert_match_to_draft_overwrites_draft_and_clears_confirmed_state(monkeypatch, tmp_path):
    app_module = load_db_test_app(monkeypatch, tmp_path)
    confirmed_state = {
        "match_active": True,
        "matches": [[1, 2, 3, 4]],
        "bench": [5],
        "match_count": 2,
        "court_count": 1,
    }
    (tmp_path / "match_state.json").write_text(json.dumps(confirmed_state), encoding="utf-8")
    (tmp_path / "draft_state.json").write_text(
        json.dumps({"draft": True, "matches": [[5, 4, 3, 2]], "bench": [1], "court_count": 1}),
        encoding="utf-8",
    )

    response = app_module.app.test_client().post("/match/revert_to_draft", data={"mode": "admin"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match/edit?mode=admin")
    saved_draft = json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8"))
    assert saved_draft["draft"] is True
    assert saved_draft["matches"] == confirmed_state["matches"]
    assert saved_draft["bench"] == confirmed_state["bench"]
    assert saved_draft["court_count"] == confirmed_state["court_count"]
    saved_state = json.loads((tmp_path / "match_state.json").read_text(encoding="utf-8"))
    assert saved_state["match_active"] is False
    assert saved_state["matches"] == []
    assert saved_state["bench"] == []
    assert saved_state["match_count"] == 1
    assert saved_state["court_count"] == 1
    with app_module.app.app_context():
        games_played_by_id = {
            participant.id: participant.games_played
            for participant in app_module.Participant.query.order_by(app_module.Participant.id).all()
        }
    assert games_played_by_id == {1: 1, 2: 0, 3: 0, 4: 2, 5: 4}


def test_revert_match_to_draft_does_not_make_counts_negative(monkeypatch, tmp_path):
    app_module = load_db_test_app(monkeypatch, tmp_path)
    (tmp_path / "match_state.json").write_text(
        json.dumps({"match_active": True, "matches": [[2, 3]], "bench": [], "match_count": 0}),
        encoding="utf-8",
    )

    response = app_module.app.test_client().post("/match/revert_to_draft", data={"mode": "admin"})

    assert response.status_code == 302
    saved_state = json.loads((tmp_path / "match_state.json").read_text(encoding="utf-8"))
    assert saved_state["match_count"] == 0
    with app_module.app.app_context():
        assert app_module.Participant.query.get(2).games_played == 0
        assert app_module.Participant.query.get(3).games_played == 0


def test_revert_match_to_draft_viewer_redirects_without_changing_draft(monkeypatch, tmp_path):
    app_module = load_db_test_app(monkeypatch, tmp_path)
    original_draft = {"draft": True, "matches": [[5, 4, 3, 2]], "bench": [1], "court_count": 1}
    (tmp_path / "match_state.json").write_text(
        json.dumps({"match_active": True, "matches": [[1, 2, 3, 4]], "bench": [5], "match_count": 2}),
        encoding="utf-8",
    )
    (tmp_path / "draft_state.json").write_text(json.dumps(original_draft), encoding="utf-8")

    response = app_module.app.test_client().post("/match/revert_to_draft", data={"mode": "viewer"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match/result?mode=viewer")
    assert json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8")) == original_draft


def test_revert_match_to_draft_without_confirmed_match_keeps_draft(monkeypatch, tmp_path):
    app_module = load_db_test_app(monkeypatch, tmp_path)
    original_draft = {"draft": True, "matches": [[1, 2, 3, 4]], "bench": [5]}
    (tmp_path / "match_state.json").write_text(
        json.dumps({"match_active": False, "matches": [], "bench": [], "match_count": 0}),
        encoding="utf-8",
    )
    (tmp_path / "draft_state.json").write_text(json.dumps(original_draft), encoding="utf-8")

    response = app_module.app.test_client().post("/match/revert_to_draft", data={"mode": "admin"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match/result?mode=admin")
    assert json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8")) == original_draft


def test_match_post_without_court_count_uses_confirmed_court_count_after_draft_clear(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    participants = [SimpleNamespace(id=player_id) for player_id in range(1, 10)]
    app_module.Participant = SimpleNamespace(query=SimpleNamespace(all=lambda: participants))
    monkeypatch.setattr(
        app_module,
        "load_match_state",
        lambda: {
            "match_active": True,
            "matches": [[1, 2, 3, 4], [5, 6, 7, 8]],
            "bench": [9],
            "match_count": 1,
            "court_count": 2,
        },
    )
    observed = {}

    def generate(players, courts):
        observed["courts"] = courts
        return [players[:4], players[4:8]], players[8:]

    monkeypatch.setattr(app_module, "generate_matches", generate)

    client = app_module.app.test_client()
    response = client.post("/match")

    saved_draft = json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8"))
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match/edit?mode=admin")
    assert observed["courts"] == 2
    assert saved_draft["court_count"] == 2
    with client.session_transaction() as draft_session:
        assert "court_count" not in draft_session


def test_match_post_without_court_count_falls_back_to_confirmed_match_count_old_schema(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    participants = [SimpleNamespace(id=player_id) for player_id in range(1, 10)]
    app_module.Participant = SimpleNamespace(query=SimpleNamespace(all=lambda: participants))
    monkeypatch.setattr(
        app_module,
        "load_match_state",
        lambda: {
            "match_active": True,
            "matches": [[1, 2, 3, 4], [5, 6, 7, 8]],
            "bench": [9],
            "match_count": 1,
        },
    )
    observed = {}
    monkeypatch.setattr(
        app_module,
        "generate_matches",
        lambda players, courts: (
            observed.setdefault("courts", courts) and [players[:4], players[4:8]],
            players[8:],
        ),
    )

    response = app_module.app.test_client().post("/match")

    saved_draft = json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8"))
    assert response.status_code == 302
    assert observed["courts"] == 2
    assert saved_draft["court_count"] == 2


def test_match_post_without_court_count_returns_form_when_confirmed_state_has_no_count(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    monkeypatch.setattr(
        app_module,
        "load_match_state",
        lambda: {"match_active": False, "matches": [], "bench": [], "match_count": 0},
    )

    response = app_module.app.test_client().post("/match")

    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True))["template"] == "match_form.html"
    assert not (tmp_path / "draft_state.json").exists()


def test_match_post_without_court_count_returns_form_when_confirmed_matches_invalid(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    monkeypatch.setattr(
        app_module,
        "load_match_state",
        lambda: {"match_active": True, "matches": "invalid", "bench": [], "match_count": 1},
    )

    response = app_module.app.test_client().post("/match")

    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True))["template"] == "match_form.html"
    assert not (tmp_path / "draft_state.json").exists()


def test_confirm_match_without_valid_draft_returns_to_match_form(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    participants, state = configure_confirmation_state(
        monkeypatch,
        app_module,
        {"match_active": True, "matches": [[1, 2, 3, 4]], "bench": [5], "match_count": 4},
    )
    inactive_draft = {"draft": False, "matches": [[5, 6, 7, 8]], "bench": [9]}
    (tmp_path / "draft_state.json").write_text(json.dumps(inactive_draft), encoding="utf-8")
    client = app_module.app.test_client()
    with client.session_transaction() as draft_session:
        draft_session["draft_matches"] = [[5, 6, 7, 8]]
        draft_session["draft_bench"] = [9]

    response = client.post("/match/confirm")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match")
    assert state == {
        "match_active": True,
        "matches": [[1, 2, 3, 4]],
        "bench": [5],
        "match_count": 4,
    }
    assert all(player.games_played == 0 for player in participants)
    assert json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8")) == inactive_draft


def test_match_result_uses_confirmed_match_state_after_confirmation(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    _, state = configure_confirmation_state(
        monkeypatch,
        app_module,
        {"match_active": False, "matches": [], "bench": [], "match_count": 0},
    )
    shared_draft = {"draft": True, "matches": [[1, 2, 3, 4]], "bench": [5]}
    (tmp_path / "draft_state.json").write_text(json.dumps(shared_draft), encoding="utf-8")
    client = app_module.app.test_client()

    confirm_response = client.post("/match/confirm")
    result_response = client.get(confirm_response.headers["Location"])

    assert result_response.status_code == 200
    assert json.loads(result_response.get_data(as_text=True)) == {
        "template": "match_result.html",
        "matches": state["matches"],
        "bench": state["bench"],
    }
    assert not (tmp_path / "draft_state.json").exists()


def test_match_result_and_draft_routes_use_separate_state(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    confirmed_state = {
        "match_active": True,
        "matches": [[1, 2, 3, 4]],
        "bench": [5],
        "match_count": 7,
    }
    draft_state = {"draft": True, "matches": [[2, 3, 4, 5]], "bench": [1]}
    (tmp_path / "draft_state.json").write_text(json.dumps(draft_state), encoding="utf-8")
    monkeypatch.setattr(app_module, "load_match_state", lambda: confirmed_state.copy())
    monkeypatch.setattr(
        app_module,
        "render_template",
        lambda template, **context: json.dumps(
            {
                "template": template,
                "matches": [[player.id for player in group] for group in context["matches"]],
                "bench": [player.id for player in context["bench"]],
                "match_count": context["match_count"],
                "has_draft": context["has_draft"],
                "has_confirmed": context["has_confirmed"],
                "is_draft": context["is_draft"],
            }
        ),
    )

    client = app_module.app.test_client()
    result_response = client.get("/match/result")
    draft_response = client.get("/match/draft?mode=admin")

    assert result_response.status_code == 200
    assert json.loads(result_response.get_data(as_text=True)) == {
        "template": "match_result.html",
        "matches": confirmed_state["matches"],
        "bench": confirmed_state["bench"],
        "match_count": 7,
        "has_draft": True,
        "has_confirmed": True,
        "is_draft": False,
    }
    assert draft_response.status_code == 200
    assert json.loads(draft_response.get_data(as_text=True)) == {
        "template": "match_result.html",
        "matches": draft_state["matches"],
        "bench": draft_state["bench"],
        "match_count": 8,
        "has_draft": True,
        "has_confirmed": True,
        "is_draft": True,
    }


def test_viewer_match_result_shows_active_draft_link(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    for participant, card in zip(app_module.Participant.query.all(), ["♥A", "♥2", "♥3", "♥4", "♥5"]):
        participant.card = card
        participant.name = f"player-{participant.id}"
        participant.games_played = participant.id
    (tmp_path / "draft_state.json").write_text(
        json.dumps({"draft": True, "matches": [[2, 3, 4, 5]], "bench": [1]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        app_module,
        "load_match_state",
        lambda: {
            "match_active": True,
            "matches": [[1, 2, 3, 4]],
            "bench": [5],
            "match_count": 3,
        },
    )
    monkeypatch.setattr(app_module, "render_template", flask_render_template)

    response = app_module.app.test_client().get("/match/result?mode=viewer")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "/match/draft?mode=viewer" in html
    assert "/match/edit" not in html
    assert "次の組み合わせ(編集中)を表示" in html
    assert "確定済み組み合わせ" in html
    assert "確定を取り消して編集に戻す" not in html


def test_viewer_match_draft_shows_active_draft_without_redirect(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    (tmp_path / "draft_state.json").write_text(
        json.dumps({"draft": True, "matches": [[2, 3, 4, 5]], "bench": [1]}),
        encoding="utf-8",
    )

    for participant, card in zip(app_module.Participant.query.all(), ["♥A", "♥2", "♥3", "♥4", "♥5"]):
        participant.card = card
        participant.name = f"draft-player-{participant.id}"
    monkeypatch.setattr(app_module, "render_template", flask_render_template)

    response = app_module.app.test_client().get("/match/draft?mode=viewer")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "次の組み合わせ(編集中)" in html
    assert "draft-player-2" in html
    assert "/match/result?mode=viewer" in html
    assert "/match/edit" not in html
    assert "この組み合わせを確定する" not in html
    assert "もう一度組み合わせを生成" not in html


def test_viewer_match_edit_redirects_to_viewer_draft(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    (tmp_path / "draft_state.json").write_text(
        json.dumps({"draft": True, "matches": [[1, 2, 3, 4]], "bench": [5]}),
        encoding="utf-8",
    )

    response = app_module.app.test_client().get("/match/edit?mode=viewer")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match/draft?mode=viewer")


def test_admin_match_result_links_to_edit_not_draft_and_hides_rematch(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    for participant, card in zip(app_module.Participant.query.all(), ["♥A", "♥2", "♥3", "♥4", "♥5"]):
        participant.card = card
        participant.name = f"player-{participant.id}"
    (tmp_path / "draft_state.json").write_text(
        json.dumps({"draft": True, "matches": [[2, 3, 4, 5]], "bench": [1]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        app_module,
        "load_match_state",
        lambda: {
            "match_active": True,
            "matches": [[1, 2, 3, 4]],
            "bench": [5],
            "match_count": 3,
        },
    )
    monkeypatch.setattr(app_module, "render_template", flask_render_template)

    response = app_module.app.test_client().get("/match/result?mode=admin")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "/match/edit?mode=admin" in html
    assert "/match/draft?mode=admin" not in html
    assert "もう一度組み合わせを生成" not in html
    assert "確定を取り消して編集に戻す" in html
    assert 'action="/match/revert_to_draft"' in html


def test_admin_draft_hides_rematch_and_links_to_edit(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    for participant, card in zip(app_module.Participant.query.all(), ["♥A", "♥2", "♥3", "♥4", "♥5"]):
        participant.card = card
        participant.name = f"player-{participant.id}"
    (tmp_path / "draft_state.json").write_text(
        json.dumps({"draft": True, "matches": [[2, 3, 4, 5]], "bench": [1]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "render_template", flask_render_template)

    response = app_module.app.test_client().get("/match/draft?mode=admin")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "/match/edit?mode=admin" in html
    assert "もう一度組み合わせを生成" not in html


def test_match_draft_redirects_to_result_without_active_draft(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)

    response = app_module.app.test_client().get("/match/draft?mode=admin")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match/result?mode=admin")


def test_legacy_match_result_redirects_to_match_result(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)

    response = app_module.app.test_client().get("/match_result?mode=admin")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/match/result?mode=admin")


def test_admin_index_shows_confirmed_and_draft_links(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    app_module.Participant.card = object()
    app_module.Participant.query.order_by = lambda _: app_module.Participant.query
    for participant, card in zip(app_module.Participant.query.all(), ["♥A", "♥2", "♥3", "♥4", "♥5"]):
        participant.card = card
        participant.name = f"player-{participant.id}"
    (tmp_path / "draft_state.json").write_text(
        json.dumps({"draft": True, "matches": [[1, 2, 3, 4]], "bench": [5]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        app_module,
        "load_match_state",
        lambda: {
            "match_active": True,
            "matches": [[5, 6, 7, 8]],
            "bench": [9],
            "match_count": 3,
        },
    )
    monkeypatch.setattr(app_module, "render_template", flask_render_template)
    client = app_module.app.test_client()

    response = client.get("/admin")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "/match/result?mode=admin" in html
    assert "/match/draft?mode=admin" in html
    assert "確定済み組み合わせ" in html
    assert "次の組み合わせ(編集中)" in html


def test_admin_ignores_stale_confirmed_session_when_shared_state_is_empty(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    app_module.Participant.card = object()
    app_module.Participant.query.order_by = lambda _: app_module.Participant.query
    for participant, card in zip(app_module.Participant.query.all(), ["♥A", "♥2", "♥3", "♥4", "♥5"]):
        participant.card = card
        participant.name = f"player-{participant.id}"
    monkeypatch.setattr(
        app_module,
        "load_match_state",
        lambda: {"match_active": False, "matches": [], "bench": [], "match_count": 0},
    )
    monkeypatch.setattr(
        app_module,
        "render_template",
        lambda template, **context: json.dumps(
            {
                "template": template,
                "has_confirmed": context["has_confirmed"],
            }
        ),
    )

    client = app_module.app.test_client()
    with client.session_transaction() as stale_session:
        stale_session["last_confirmed_matches"] = [[9]]
        stale_session["last_confirmed_bench"] = []

    response = client.get("/admin")

    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True)) == {
        "template": "index.html",
        "has_confirmed": False,
    }


def test_admin_has_confirmed_when_shared_match_state_has_results(monkeypatch, tmp_path):
    app_module = load_test_app(monkeypatch, tmp_path)
    app_module.Participant.card = object()
    app_module.Participant.query.order_by = lambda _: app_module.Participant.query
    for participant, card in zip(app_module.Participant.query.all(), ["♥A", "♥2", "♥3", "♥4", "♥5"]):
        participant.card = card
        participant.name = f"player-{participant.id}"
    monkeypatch.setattr(
        app_module,
        "load_match_state",
        lambda: {
            "match_active": True,
            "matches": [[1, 2, 3, 4]],
            "bench": [5],
            "match_count": 1,
        },
    )
    monkeypatch.setattr(
        app_module,
        "render_template",
        lambda template, **context: json.dumps(
            {
                "template": template,
                "has_confirmed": context["has_confirmed"],
            }
        ),
    )

    response = app_module.app.test_client().get("/admin")

    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True)) == {
        "template": "index.html",
        "has_confirmed": True,
    }
