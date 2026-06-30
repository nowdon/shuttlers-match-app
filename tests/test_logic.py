from types import SimpleNamespace

import pytest
from flask import Flask

from logic import generate_matches
from models import db, MatchHistory, MatchRound


def make_player(player_id, games_played=0, active=True):
    return SimpleNamespace(id=player_id, games_played=games_played, active=active)


def test_generate_matches_uses_only_active_players_and_benches_over_capacity():
    participants = [
        make_player(1, games_played=0),
        make_player(2, games_played=0),
        make_player(3, games_played=1),
        make_player(4, games_played=1),
        make_player(5, games_played=2),
        make_player(6, games_played=0, active=False),
    ]

    matches, bench = generate_matches(participants, court_count=1)

    matched_ids = {player.id for match in matches for player in match}
    benched_ids = {player.id for player in bench}

    assert len(matches) == 1
    assert matched_ids == {1, 2, 3, 4}
    assert benched_ids == {5}
    assert 6 not in matched_ids | benched_ids


def test_generate_matches_increments_games_played_for_matched_players_only():
    participants = [make_player(player_id) for player_id in range(1, 6)]

    matches, bench = generate_matches(participants, court_count=1)

    for player in [player for match in matches for player in match]:
        assert player.games_played == 1
    for player in bench:
        assert player.games_played == 0


@pytest.fixture
def logic_app(tmp_path):
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{tmp_path / 'logic.db'}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def flatten_match_ids(matches):
    return {player.id for match in matches for player in match}


def add_round(round_number, player_ids):
    round_record = MatchRound(round_number=round_number)
    db.session.add(round_record)
    db.session.flush()
    db.session.add(
        MatchHistory(
            round_id=round_record.id,
            court_number=1,
            team1_player1_id=player_ids[0],
            team1_player2_id=player_ids[1],
            team2_player1_id=player_ids[2],
            team2_player2_id=player_ids[3],
        )
    )
    return round_record


def test_generate_matches_prioritizes_active_previous_bench_players():
    participants = [make_player(player_id, games_played=0) for player_id in range(1, 6)]
    participants[4].games_played = 99

    matches, bench = generate_matches(participants, court_count=1, previous_bench_ids={5})

    assert 5 in flatten_match_ids(matches)
    assert 5 not in {player.id for player in bench}
    assert len(bench) == 1


def test_generate_matches_does_not_select_inactive_previous_bench_players():
    participants = [make_player(player_id, games_played=0) for player_id in range(1, 5)]
    inactive_previous_bench = make_player(5, games_played=0, active=False)
    participants.append(inactive_previous_bench)

    matches, bench = generate_matches(participants, court_count=1, previous_bench_ids={5})

    selected_or_benched_ids = flatten_match_ids(matches) | {player.id for player in bench}
    assert 5 not in selected_or_benched_ids
    assert len(matches) == 1
    assert bench == []


def test_generate_matches_benches_three_consecutive_players_when_bench_slots_exist(logic_app):
    participants = [make_player(player_id, games_played=0) for player_id in range(1, 6)]
    with logic_app.app_context():
        for round_number in range(1, 4):
            add_round(round_number, [1, 2, 3, 4])
        db.session.commit()

        matches, bench = generate_matches(participants, court_count=1)

    assert {player.id for player in bench} <= {1, 2, 3, 4}
    assert 5 in flatten_match_ids(matches)


def test_generate_matches_does_not_treat_less_than_three_rounds_as_three_consecutive(logic_app):
    participants = [make_player(player_id, games_played=0) for player_id in range(1, 6)]
    participants[4].games_played = 99
    with logic_app.app_context():
        add_round(1, [1, 2, 3, 4])
        add_round(2, [1, 2, 3, 4])
        db.session.commit()

        matches, bench = generate_matches(participants, court_count=1)

    assert {player.id for player in bench} == {5}
    assert flatten_match_ids(matches) == {1, 2, 3, 4}


def test_generate_matches_selects_all_active_players_when_no_bench_slots_even_if_three_consecutive():
    participants = [make_player(player_id, games_played=0) for player_id in range(1, 5)]

    matches, bench = generate_matches(
        participants,
        court_count=1,
        three_consecutive_player_ids={1, 2, 3, 4},
    )

    assert len(matches) == 1
    assert flatten_match_ids(matches) == {1, 2, 3, 4}
    assert bench == []


def test_generate_matches_succeeds_when_three_consecutive_players_exceed_bench_slots():
    participants = [make_player(player_id, games_played=0) for player_id in range(1, 7)]

    matches, bench = generate_matches(
        participants,
        court_count=1,
        three_consecutive_player_ids={1, 2, 3, 4, 5},
    )

    assert len(matches) == 1
    assert len(bench) == 2
    assert flatten_match_ids(matches) | {player.id for player in bench} == set(range(1, 7))


def test_generate_matches_keeps_existing_return_shape():
    participants = [make_player(player_id, games_played=0) for player_id in range(1, 6)]

    matches, bench = generate_matches(participants, court_count=1)

    assert isinstance(matches, list)
    assert all(isinstance(match, list) for match in matches)
    assert all(len(match) == 4 for match in matches)
    assert isinstance(bench, list)
    assert all(hasattr(player, "id") for match in matches for player in match)
    assert all(hasattr(player, "id") for player in bench)
