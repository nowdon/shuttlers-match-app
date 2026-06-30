import random

from flask import has_app_context

from models import MatchRound
from utils.match_state import load_match_state


def get_previous_bench_ids():
    """Return participant IDs from the latest confirmed match state's bench."""
    return set(load_match_state().get("bench", []))


def get_three_consecutive_player_ids():
    """Return participant IDs that played in all of the latest three persisted rounds."""
    if not has_app_context():
        return set()

    latest_rounds = MatchRound.query.order_by(MatchRound.id.desc()).limit(3).all()
    if len(latest_rounds) < 3:
        return set()

    played_by_round = []
    for round_record in latest_rounds:
        player_ids = set()
        for match in round_record.matches:
            player_ids.update(
                (
                    match.team1_player1_id,
                    match.team1_player2_id,
                    match.team2_player1_id,
                    match.team2_player2_id,
                )
            )
        played_by_round.append(player_ids)

    return set.intersection(*played_by_round) if played_by_round else set()


def generate_matches(
    participants, court_count, previous_bench_ids=None, three_consecutive_player_ids=None
):
    # activeで絞って、優先順位でソート
    candidates = [p for p in participants if p.active]

    if previous_bench_ids is None:
        previous_bench_ids = get_previous_bench_ids()
    else:
        previous_bench_ids = set(previous_bench_ids)

    if three_consecutive_player_ids is None:
        three_consecutive_player_ids = get_three_consecutive_player_ids()
    else:
        three_consecutive_player_ids = set(three_consecutive_player_ids)

    # 同順位の中では既存のランダム性を残す
    random.shuffle(candidates)
    candidates.sort(
        key=lambda p: (
            p.id not in previous_bench_ids,
            p.id in three_consecutive_player_ids,
            p.games_played,
        )
    )

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
                p.games_played += 1

    return matches, bench
