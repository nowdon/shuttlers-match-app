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


def test_ensure_database_tables_adds_missing_history_tables_for_existing_db(monkeypatch, tmp_path):
    app_module = load_history_test_app(monkeypatch, tmp_path)
    matches = [[1, 2, 3, 4]]
    write_draft(tmp_path, matches, [])

    with app_module.app.app_context():
        add_participants(app_module, 4)
        app_module.BenchHistory.__table__.drop(app_module.db.engine)
        app_module.MatchHistory.__table__.drop(app_module.db.engine)
        app_module.MatchRound.__table__.drop(app_module.db.engine)

        app_module.ensure_database_tables()
        response = app_module.app.test_client().post("/match/confirm")

        assert response.status_code == 302
        assert app_module.MatchRound.query.count() == 1
        assert app_module.MatchHistory.query.count() == 1
        assert app_module.BenchHistory.query.count() == 0


def test_revert_match_to_draft_deletes_corresponding_history(monkeypatch, tmp_path):
    app_module = load_history_test_app(monkeypatch, tmp_path)
    matches = [[1, 2, 3, 4]]
    bench = [5]
    write_draft(tmp_path, matches, bench)

    with app_module.app.app_context():
        add_participants(app_module, 5)
        client = app_module.app.test_client()
        client.post("/match/confirm")

        response = client.post("/match/revert_to_draft", data={"mode": "admin"})

        assert response.status_code == 302
        assert app_module.BenchHistory.query.count() == 0
        assert app_module.MatchHistory.query.count() == 0
        assert app_module.MatchRound.query.count() == 0


def test_revert_then_reconfirm_does_not_keep_cancelled_duplicate_history(monkeypatch, tmp_path):
    app_module = load_history_test_app(monkeypatch, tmp_path)
    original_matches = [[1, 2, 3, 4]]
    edited_matches = [[4, 3, 2, 1]]
    write_draft(tmp_path, original_matches, [5])

    with app_module.app.app_context():
        add_participants(app_module, 5)
        client = app_module.app.test_client()
        client.post("/match/confirm")
        client.post("/match/revert_to_draft", data={"mode": "admin"})
        write_draft(tmp_path, edited_matches, [5])
        response = client.post("/match/confirm")

        assert response.status_code == 302
        rounds = app_module.MatchRound.query.all()
        assert len(rounds) == 1
        assert rounds[0].round_number == 1
        histories = app_module.MatchHistory.query.all()
        assert len(histories) == 1
        assert [
            histories[0].team1_player1_id,
            histories[0].team1_player2_id,
            histories[0].team2_player1_id,
            histories[0].team2_player2_id,
        ] == edited_matches[0]


def test_confirm_rolls_back_db_when_saving_match_state_fails(monkeypatch, tmp_path):
    app_module = load_history_test_app(monkeypatch, tmp_path)
    write_draft(tmp_path, [[1, 2, 3, 4]], [])

    def fail_save(*args, **kwargs):
        raise OSError("cannot save match state")

    monkeypatch.setattr(app_module, "save_match_state_full", fail_save)

    with app_module.app.app_context():
        add_participants(app_module, 4)
        client = app_module.app.test_client()
        try:
            client.post("/match/confirm")
            assert False, "expected save_match_state_full failure"
        except OSError:
            pass

        assert app_module.MatchRound.query.count() == 0
        assert app_module.MatchHistory.query.count() == 0
        assert [p.games_played for p in app_module.Participant.query.order_by(app_module.Participant.id).all()] == [0, 0, 0, 0]
        assert (tmp_path / "draft_state.json").exists()


def test_confirm_rolls_back_db_when_clearing_draft_fails(monkeypatch, tmp_path):
    app_module = load_history_test_app(monkeypatch, tmp_path)
    write_draft(tmp_path, [[1, 2, 3, 4]], [])

    def fail_clear():
        raise OSError("cannot clear draft")

    monkeypatch.setattr(app_module, "clear_draft_state", fail_clear)

    with app_module.app.app_context():
        add_participants(app_module, 4)
        client = app_module.app.test_client()
        try:
            client.post("/match/confirm")
            assert False, "expected clear_draft_state failure"
        except OSError:
            pass

        assert app_module.MatchRound.query.count() == 0
        assert app_module.MatchHistory.query.count() == 0
        assert [p.games_played for p in app_module.Participant.query.order_by(app_module.Participant.id).all()] == [0, 0, 0, 0]
        assert (tmp_path / "draft_state.json").exists()


def test_admin_match_history_page_displays_round_matches_bench_and_scores(monkeypatch, tmp_path):
    app_module = load_history_test_app(monkeypatch, tmp_path)

    with app_module.app.app_context():
        add_participants(app_module, 9)
        from datetime import datetime, timezone
        round_record = app_module.MatchRound(
            round_number=5,
            created_at=datetime(2026, 6, 27, 19, 30, tzinfo=timezone.utc),
        )
        app_module.db.session.add(round_record)
        app_module.db.session.flush()
        app_module.db.session.add(app_module.MatchHistory(
            round_id=round_record.id,
            court_number=1,
            team1_player1_id=1,
            team1_player2_id=2,
            team2_player1_id=3,
            team2_player2_id=4,
        ))
        app_module.db.session.add(app_module.MatchHistory(
            round_id=round_record.id,
            court_number=2,
            team1_player1_id=5,
            team1_player2_id=6,
            team2_player1_id=7,
            team2_player2_id=8,
            team1_score=21,
            team2_score=18,
            winner_team=1,
        ))
        app_module.db.session.add(app_module.BenchHistory(round_id=round_record.id, participant_id=9))
        app_module.db.session.commit()

        response = app_module.app.test_client().get("/admin/match_history")

        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "第5試合" in html
        assert "2026-06-27 19:30" in html
        assert "1コート" in html
        assert "player-1・player-2" in html
        assert "player-3・player-4" in html
        assert "2コート" in html
        assert "player-5・player-6" in html
        assert "player-7・player-8" in html
        assert "player-9" in html
        assert "結果未入力" in html
        assert "21 - 18 / 勝者: team1" in html


def test_admin_match_history_page_orders_newest_round_first(monkeypatch, tmp_path):
    app_module = load_history_test_app(monkeypatch, tmp_path)

    with app_module.app.app_context():
        from datetime import datetime, timezone
        app_module.db.session.add(app_module.MatchRound(round_number=1, created_at=datetime(2026, 6, 27, 18, 0, tzinfo=timezone.utc)))
        app_module.db.session.add(app_module.MatchRound(round_number=2, created_at=datetime(2026, 6, 27, 19, 0, tzinfo=timezone.utc)))
        app_module.db.session.commit()

        html = app_module.app.test_client().get("/admin/match_history").get_data(as_text=True)

        assert html.index("第2試合") < html.index("第1試合")

def test_admin_match_history_eager_loads_round_relationships(monkeypatch, tmp_path):
    app_module = load_history_test_app(monkeypatch, tmp_path)

    with app_module.app.app_context():
        add_participants(app_module, 15)
        for round_number in range(1, 4):
            round_record = app_module.MatchRound(round_number=round_number)
            app_module.db.session.add(round_record)
            app_module.db.session.flush()
            player_offset = (round_number - 1) * 4
            app_module.db.session.add(app_module.MatchHistory(
                round_id=round_record.id,
                court_number=1,
                team1_player1_id=player_offset + 1,
                team1_player2_id=player_offset + 2,
                team2_player1_id=player_offset + 3,
                team2_player2_id=player_offset + 4,
            ))
            app_module.db.session.add(app_module.BenchHistory(
                round_id=round_record.id,
                participant_id=13 + (round_number - 1),
            ))
        app_module.db.session.commit()

        from sqlalchemy import event

        relationship_selects = {"match_histories": 0, "bench_histories": 0}

        def count_relationship_selects(conn, cursor, statement, parameters, context, executemany):
            normalized = statement.lower()
            if normalized.lstrip().startswith("select"):
                for table_name in relationship_selects:
                    if f"from {table_name}" in normalized:
                        relationship_selects[table_name] += 1

        event.listen(app_module.db.engine, "before_cursor_execute", count_relationship_selects)
        try:
            response = app_module.app.test_client().get("/admin/match_history")
        finally:
            event.remove(app_module.db.engine, "before_cursor_execute", count_relationship_selects)

        assert response.status_code == 200
        assert relationship_selects == {"match_histories": 1, "bench_histories": 1}


def test_admin_match_history_page_displays_empty_message(monkeypatch, tmp_path):
    app_module = load_history_test_app(monkeypatch, tmp_path)

    with app_module.app.app_context():
        response = app_module.app.test_client().get("/admin/match_history")

        assert response.status_code == 200
        assert "試合履歴はまだありません" in response.get_data(as_text=True)


def test_admin_index_links_to_match_history_but_viewer_index_does_not(monkeypatch, tmp_path):
    app_module = load_history_test_app(monkeypatch, tmp_path)

    with app_module.app.app_context():
        admin_html = app_module.app.test_client().get("/admin").get_data(as_text=True)
        viewer_html = app_module.app.test_client().get("/viewer").get_data(as_text=True)

        assert "/admin/match_history" in admin_html
        assert "試合履歴" in admin_html
        assert "/admin/match_history" not in viewer_html
        assert "試合履歴" not in viewer_html
