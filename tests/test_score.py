from types import SimpleNamespace

from utils.score import calculate_pair_score


def test_calculate_pair_score_returns_player_scores_and_total():
    pair = [
        SimpleNamespace(name="Alice", level="beginner", gender="female"),
        SimpleNamespace(name="Bob", level="advanced", gender="male"),
    ]
    level_map = {"beginner": 1, "advanced": 3}
    gender_weight = {"female": 0.9, "male": 1.0}

    result = calculate_pair_score(pair, level_map, gender_weight)

    assert result == {
        "players": [
            {"name": "Alice", "score": 0.9},
            {"name": "Bob", "score": 3.0},
        ],
        "total_score": 3.9,
    }
