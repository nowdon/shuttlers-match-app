from types import SimpleNamespace

from logic import generate_matches


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
