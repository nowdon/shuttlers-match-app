import importlib
import json
import os
import sys


def load_history_test_app(monkeypatch, tmp_path):
    config = {"level_map": {}, "gender_weight": {}}
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("app", None)
    app_module = importlib.import_module("app")
    os.makedirs(app_module.app.instance_path, exist_ok=True)
    app_module.app.config.update(TESTING=True)
    with app_module.app.app_context():
        app_module.db.drop_all()
        app_module.db.create_all()
    return app_module


def add_participants(app_module, count):
    participants = []
    for player_id in range(1, count + 1):
        participant = app_module.Participant(
            name=f"player-{player_id}",
            gender="male",
            level="beginner",
            weight=1.0,
            card=f"C{player_id}",
        )
        participants.append(participant)
        app_module.db.session.add(participant)
    app_module.db.session.commit()
    return participants


def write_draft(tmp_path, matches, bench, court_count=None):
    draft = {"draft": True, "matches": matches, "bench": bench}
    if court_count is not None:
        draft["court_count"] = court_count
    (tmp_path / "draft_state.json").write_text(json.dumps(draft), encoding="utf-8")


def test_confirm_match_persists_round_matches_bench_and_preserves_state_flow(monkeypatch, tmp_path):
    app_module = load_history_test_app(monkeypatch, tmp_path)
    matches = [[1, 2, 3, 4], [5, 6, 7, 8]]
    bench = [9]
    write_draft(tmp_path, matches, bench, court_count=2)

    with app_module.app.app_context():
        add_participants(app_module, 9)
        response = app_module.app.test_client().post("/match/confirm", data={"mode": "admin"})

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/match/result?mode=admin")

        rounds = app_module.MatchRound.query.all()
        assert len(rounds) == 1
        assert rounds[0].round_number == 1

        histories = app_module.MatchHistory.query.order_by(app_module.MatchHistory.court_number).all()
        assert len(histories) == len(matches)
        assert [
            [
                history.team1_player1_id,
                history.team1_player2_id,
                history.team2_player1_id,
                history.team2_player2_id,
            ]
            for history in histories
        ] == matches
        assert all(history.team1_score is None for history in histories)
        assert all(history.team2_score is None for history in histories)
        assert all(history.winner_team is None for history in histories)

        bench_histories = app_module.BenchHistory.query.all()
        assert len(bench_histories) == len(bench)
        assert [history.participant_id for history in bench_histories] == bench

        state = json.loads((tmp_path / "match_state.json").read_text(encoding="utf-8"))
        assert state["match_active"] is True
        assert state["match_count"] == 1
        assert state["matches"] == matches
        assert state["bench"] == bench
        assert state["court_count"] == 2
        assert not (tmp_path / "draft_state.json").exists()

        with app_module.app.test_client().session_transaction() as session:
            assert "draft_matches" not in session
            assert "draft_bench" not in session
            assert "court_count" not in session
            assert "last_confirmed_matches" not in session
            assert "last_confirmed_bench" not in session


def test_reset_match_keeps_history_records(monkeypatch, tmp_path):
    app_module = load_history_test_app(monkeypatch, tmp_path)
    write_draft(tmp_path, [[1, 2, 3, 4]], [5])

    with app_module.app.app_context():
        add_participants(app_module, 5)
        client = app_module.app.test_client()
        client.post("/match/confirm")
        response = client.post("/reset_match")

        assert response.status_code == 302
        assert app_module.MatchRound.query.count() == 1
        assert app_module.MatchHistory.query.count() == 1
        assert app_module.BenchHistory.query.count() == 1


def test_reset_db_deletes_history_before_participants_without_constraint_error(monkeypatch, tmp_path):
    app_module = load_history_test_app(monkeypatch, tmp_path)
    write_draft(tmp_path, [[1, 2, 3, 4]], [5])

    with app_module.app.app_context():
        add_participants(app_module, 5)
        client = app_module.app.test_client()
        client.post("/match/confirm")
        response = client.post("/admin/reset_db")

        assert response.status_code == 302
        assert app_module.BenchHistory.query.count() == 0
        assert app_module.MatchHistory.query.count() == 0
        assert app_module.MatchRound.query.count() == 0
        assert app_module.Participant.query.count() == 0
