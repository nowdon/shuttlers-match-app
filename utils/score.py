def calculate_pair_score(pair, level_map, gender_weight):
    players_info = []
    total_score = 0
    for player in pair:
        level = getattr(player, "level", None)
        gender = getattr(player, "gender", None)
        score = round(level_map.get(level, 0) * gender_weight.get(gender, 1.0), 1)
        players_info.append({
            "name": player.name,
            "score": score
        })
        total_score += score
    return {
        "players": players_info,
        "total_score": round(total_score, 1)
    }