import importlib
import json
import os
import sys
from types import SimpleNamespace

from utils.score import calculate_pair_score


def load_stats_test_app(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"level_map": {}, "gender_weight": {}}), encoding="utf-8")
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


def add_round(app_module, round_number=1):
    match_round = app_module.MatchRound(round_number=round_number)
    app_module.db.session.add(match_round)
    app_module.db.session.commit()
    return match_round


def add_history(app_module, match_round, player_ids, winner_team):
    history = app_module.MatchHistory(
        round_id=match_round.id,
        court_number=1,
        team1_player1_id=player_ids[0],
        team1_player2_id=player_ids[1],
        team2_player1_id=player_ids[2],
        team2_player2_id=player_ids[3],
        winner_team=winner_team,
    )
    app_module.db.session.add(history)
    app_module.db.session.commit()
    return history


def test_winner_team_1_adds_wins_to_team1_and_losses_to_team2(monkeypatch, tmp_path):
    app_module = load_stats_test_app(monkeypatch, tmp_path)
    with app_module.app.app_context():
        players = add_participants(app_module, 4)
        match_round = add_round(app_module)
        add_history(app_module, match_round, [player.id for player in players], winner_team=1)

        stats = app_module.calculate_participant_win_stats()

        assert stats[players[0].id] == {"wins": 1, "losses": 0, "games": 1, "win_rate": 1.0}
        assert stats[players[1].id] == {"wins": 1, "losses": 0, "games": 1, "win_rate": 1.0}
        assert stats[players[2].id] == {"wins": 0, "losses": 1, "games": 1, "win_rate": 0.0}
        assert stats[players[3].id] == {"wins": 0, "losses": 1, "games": 1, "win_rate": 0.0}


def test_winner_team_2_adds_wins_to_team2_and_losses_to_team1(monkeypatch, tmp_path):
    app_module = load_stats_test_app(monkeypatch, tmp_path)
    with app_module.app.app_context():
        players = add_participants(app_module, 4)
        match_round = add_round(app_module)
        add_history(app_module, match_round, [player.id for player in players], winner_team=2)

        stats = app_module.calculate_participant_win_stats()

        assert stats[players[0].id] == {"wins": 0, "losses": 1, "games": 1, "win_rate": 0.0}
        assert stats[players[1].id] == {"wins": 0, "losses": 1, "games": 1, "win_rate": 0.0}
        assert stats[players[2].id] == {"wins": 1, "losses": 0, "games": 1, "win_rate": 1.0}
        assert stats[players[3].id] == {"wins": 1, "losses": 0, "games": 1, "win_rate": 1.0}


def test_unentered_winner_team_is_excluded_from_win_stats(monkeypatch, tmp_path):
    app_module = load_stats_test_app(monkeypatch, tmp_path)
    with app_module.app.app_context():
        players = add_participants(app_module, 4)
        match_round = add_round(app_module)
        add_history(app_module, match_round, [player.id for player in players], winner_team=None)

        stats = app_module.calculate_participant_win_stats()

        assert all(stats[player.id] == {"wins": 0, "losses": 0, "games": 0, "win_rate": 0.0} for player in players)


def test_multiple_matches_aggregate_participant_win_stats(monkeypatch, tmp_path):
    app_module = load_stats_test_app(monkeypatch, tmp_path)
    with app_module.app.app_context():
        players = add_participants(app_module, 6)
        first_round = add_round(app_module, 1)
        second_round = add_round(app_module, 2)
        add_history(app_module, first_round, [1, 2, 3, 4], winner_team=1)
        add_history(app_module, second_round, [1, 3, 5, 6], winner_team=2)

        stats = app_module.calculate_participant_win_stats()

        assert stats[players[0].id] == {"wins": 1, "losses": 1, "games": 2, "win_rate": 0.5}
        assert stats[players[1].id] == {"wins": 1, "losses": 0, "games": 1, "win_rate": 1.0}
        assert stats[players[2].id] == {"wins": 0, "losses": 2, "games": 2, "win_rate": 0.0}
        assert stats[players[4].id] == {"wins": 1, "losses": 0, "games": 1, "win_rate": 1.0}


def test_participant_without_match_history_has_zero_win_rate(monkeypatch, tmp_path):
    app_module = load_stats_test_app(monkeypatch, tmp_path)
    with app_module.app.app_context():
        players = add_participants(app_module, 5)
        match_round = add_round(app_module)
        add_history(app_module, match_round, [1, 2, 3, 4], winner_team=1)

        stats = app_module.calculate_participant_win_stats()

        assert stats[players[4].id] == {"wins": 0, "losses": 0, "games": 0, "win_rate": 0.0}


def test_pair_score_matches_existing_calculation_without_win_history():
    pair = [
        SimpleNamespace(id=1, name="Alice", level="beginner", gender="female"),
        SimpleNamespace(id=2, name="Bob", level="advanced", gender="male"),
    ]

    result = calculate_pair_score(
        pair,
        {"beginner": 1, "advanced": 3},
        {"female": 0.9, "male": 1.0},
        win_stats={},
    )

    assert result == {
        "players": [
            {"name": "Alice", "score": 0.9},
            {"name": "Bob", "score": 3.0},
        ],
        "total_score": 3.9,
    }


def test_pair_score_adds_win_rate_to_level_weight_score():
    pair = [SimpleNamespace(id=1, name="Alice", level="intermediate", gender="female")]

    result = calculate_pair_score(
        pair,
        {"intermediate": 2},
        {"female": 0.8},
        win_stats={1: {"wins": 3, "losses": 2, "games": 5, "win_rate": 0.6}},
    )

    assert result == {"players": [{"name": "Alice", "score": 2.2}], "total_score": 2.2}


def test_cleared_match_history_resets_win_rate_to_zero(monkeypatch, tmp_path):
    app_module = load_stats_test_app(monkeypatch, tmp_path)
    with app_module.app.app_context():
        players = add_participants(app_module, 4)
        match_round = add_round(app_module)
        add_history(app_module, match_round, [player.id for player in players], winner_team=1)
        app_module.MatchHistory.query.delete()
        app_module.db.session.commit()

        stats = app_module.calculate_participant_win_stats()

        assert all(stats[player.id]["win_rate"] == 0.0 for player in players)
