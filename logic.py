import random

def generate_matches(participants, court_count):
    # activeで絞って、games_playedで昇順ソート（回数少ない人優先）
    candidates = [p for p in participants if p.active]
    candidates.sort(key=lambda p: p.games_played)

    max_players = min(len(candidates), court_count * 4)
    selected = candidates[:max_players]
    bench = candidates[max_players:]

    random.shuffle(selected)  # とりあえずシャッフル（後で改善可）

    matches = []
    for i in range(0, len(selected), 4):
        group = selected[i:i+4]
        if len(group) == 4:
            matches.append(group)
            # ゲームに出た人はgames_playedを+1
            for p in group:
                p.games_played +=1

    return matches, bench