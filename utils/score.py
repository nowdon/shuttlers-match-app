from flask import has_app_context

from utils.stats import calculate_participant_win_stats


def calculate_pair_score(pair, level_map, gender_weight, win_stats=None):
    if win_stats is None:
        win_stats = calculate_participant_win_stats() if has_app_context() else {}
    players_info = []
    total_score = 0
    for player in pair:
        level = getattr(player, "level", None)
        gender = getattr(player, "gender", None)
        participant_id = getattr(player, "id", None)
        win_rate = win_stats.get(participant_id, {}).get("win_rate", 0.0)
        score = round(level_map.get(level, 0) * gender_weight.get(gender, 1.0) + win_rate, 1)
        players_info.append({
            "name": player.name,
            "score": score
        })
        total_score += score
    return {
        "players": players_info,
        "total_score": round(total_score, 1)
    }