import json
import random

from flask import has_app_context

from models import MatchRound
from utils.match_state import load_match_state

DEFAULT_CONSECUTIVE_PLAY_LIMIT = 3
MIN_CONSECUTIVE_PLAY_LIMIT = 2
MAX_CONSECUTIVE_PLAY_LIMIT = 10


def normalize_consecutive_play_limit(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CONSECUTIVE_PLAY_LIMIT
    if parsed < MIN_CONSECUTIVE_PLAY_LIMIT or parsed > MAX_CONSECUTIVE_PLAY_LIMIT:
        return DEFAULT_CONSECUTIVE_PLAY_LIMIT
    return parsed


def load_consecutive_play_limit():
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_CONSECUTIVE_PLAY_LIMIT
    if not isinstance(config, dict):
        return DEFAULT_CONSECUTIVE_PLAY_LIMIT
    return normalize_consecutive_play_limit(config.get('consecutive_play_limit'))


def get_previous_bench_ids():
    """Return participant IDs from the latest confirmed match state's bench."""
    return set(load_match_state().get("bench", []))


def get_consecutive_player_ids(consecutive_play_limit=None):
    """Return participant IDs that played in all latest configured persisted rounds."""
    if not has_app_context():
        return set()

    limit = (
        normalize_consecutive_play_limit(consecutive_play_limit)
        if consecutive_play_limit is not None
        else load_consecutive_play_limit()
    )

    match_count = int(load_match_state().get("match_count", 0) or 0)
    if match_count < limit:
        return set()

    latest_rounds = (
        MatchRound.query.order_by(MatchRound.id.desc())
        .limit(min(match_count, limit))
        .all()
    )
    if len(latest_rounds) < limit:
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


def get_three_consecutive_player_ids():
    """Backward-compatible wrapper for the former fixed-three helper."""
    return get_consecutive_player_ids(3)


def generate_matches(
    participants,
    court_count,
    previous_bench_ids=None,
    consecutive_player_ids=None,
    three_consecutive_player_ids=None,
):
    # activeで絞って、優先順位でソート
    candidates = [p for p in participants if p.active]

    if previous_bench_ids is None:
        previous_bench_ids = get_previous_bench_ids()
    else:
        previous_bench_ids = set(previous_bench_ids)

    if consecutive_player_ids is None and three_consecutive_player_ids is not None:
        consecutive_player_ids = three_consecutive_player_ids

    if consecutive_player_ids is None:
        consecutive_player_ids = get_consecutive_player_ids()
    else:
        consecutive_player_ids = set(consecutive_player_ids)

    # 同順位の中では既存のランダム性を残す
    random.shuffle(candidates)
    candidates.sort(
        key=lambda p: (
            p.id not in previous_bench_ids,
            p.id in consecutive_player_ids,
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
