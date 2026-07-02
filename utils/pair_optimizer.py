from dataclasses import dataclass

from models import MatchHistory
from utils.score import calculate_pair_score


@dataclass
class PairOptimizationResult:
    success: bool
    matches: list
    bench: list
    fixed_pairs: list
    court_count: int | None
    message: str


def get_current_pair(match_ids, participant_id):
    for match_index, group in enumerate(match_ids):
        for start in range(0, len(group), 2):
            pair = group[start:start + 2]
            if participant_id in pair:
                return match_index, start, pair
    return None


def normalize_fixed_pairs(raw_fixed_pairs, match_ids):
    if not isinstance(raw_fixed_pairs, list):
        return []

    used_ids = set()
    normalized = []
    for raw_pair in raw_fixed_pairs:
        if not isinstance(raw_pair, (list, tuple)) or len(raw_pair) != 2:
            continue
        try:
            pair_ids = [int(raw_pair[0]), int(raw_pair[1])]
        except (TypeError, ValueError):
            continue
        if pair_ids[0] == pair_ids[1] or any(pid in used_ids for pid in pair_ids):
            continue

        position_1 = get_current_pair(match_ids, pair_ids[0])
        position_2 = get_current_pair(match_ids, pair_ids[1])
        if (
            position_1 is None
            or position_2 is None
            or position_1[0] != position_2[0]
            or position_1[1] != position_2[1]
            or len(position_1[2]) != 2
        ):
            continue

        normalized_pair = sorted(pair_ids)
        normalized.append(normalized_pair)
        used_ids.update(normalized_pair)

    return normalized


def get_fixed_pair_for_player(fixed_pairs, participant_id):
    for pair in fixed_pairs:
        if participant_id in pair:
            return pair
    return None


def get_historical_pair_counts():
    """Return how often each unordered doubles pair appears in MatchHistory."""
    pair_counts = {}
    for history in MatchHistory.query.all():
        for pair in (
            (history.team1_player1_id, history.team1_player2_id),
            (history.team2_player1_id, history.team2_player2_id),
        ):
            if pair[0] is None or pair[1] is None or pair[0] == pair[1]:
                continue
            key = tuple(sorted(pair))
            pair_counts[key] = pair_counts.get(key, 0) + 1
    return pair_counts


def get_player_score(participant, level_map, gender_weight, win_stats):
    return calculate_pair_score([participant], level_map, gender_weight, win_stats)["total_score"]


def build_pair_score(pair, participants, level_map, gender_weight, win_stats):
    players = [participants[pid] for pid in pair if pid in participants]
    if len(players) != 2:
        return None
    return calculate_pair_score(players, level_map, gender_weight, win_stats)["total_score"]


def optimize_draft_matches_by_pair_score(match_ids, fixed_pairs, participants, level_map, gender_weight, win_stats, pair_counts):
    """Re-pair draft players and match pairs with similar pair scores."""
    if not isinstance(match_ids, list) or not match_ids:
        return None

    player_ids = []
    for group in match_ids:
        if not isinstance(group, list) or len(group) != 4:
            return None
        player_ids.extend(group)

    try:
        player_ids = [int(pid) for pid in player_ids]
    except (TypeError, ValueError):
        return None

    if len(player_ids) % 4 != 0 or len(player_ids) != len(set(player_ids)):
        return None
    if any(pid not in participants for pid in player_ids):
        return None

    fixed_key_set = {tuple(pair) for pair in fixed_pairs}
    fixed_player_ids = {pid for pair in fixed_pairs for pid in pair}
    if not fixed_player_ids.issubset(set(player_ids)):
        return None

    pair_units = [tuple(pair) for pair in fixed_pairs]
    remaining_ids = [pid for pid in player_ids if pid not in fixed_player_ids]
    player_scores = {
        pid: get_player_score(participants[pid], level_map, gender_weight, win_stats)
        for pid in remaining_ids
    }

    while remaining_ids:
        first = remaining_ids[0]
        if len(remaining_ids) == 1:
            return None
        best_partner = min(
            remaining_ids[1:],
            key=lambda pid: (
                pair_counts.get(tuple(sorted((first, pid))), 0),
                abs(player_scores.get(first, 0) - player_scores.get(pid, 0)),
                pid,
            ),
        )
        pair_units.append((first, best_partner))
        remaining_ids = [pid for pid in remaining_ids if pid not in (first, best_partner)]

    if len(pair_units) % 2 != 0:
        return None

    scored_pairs = []
    for pair in pair_units:
        score = build_pair_score(pair, participants, level_map, gender_weight, win_stats)
        if score is None:
            return None
        scored_pairs.append({"pair": pair, "score": score, "fixed": tuple(sorted(pair)) in fixed_key_set})

    scored_pairs.sort(key=lambda item: (item["score"], item["pair"]))

    optimized = []
    for index in range(0, len(scored_pairs), 2):
        left = scored_pairs[index]["pair"]
        right = scored_pairs[index + 1]["pair"]
        optimized.append([left[0], left[1], right[0], right[1]])
    return optimized


def optimize_draft_pairs(draft, participants, level_map, gender_weight, win_stats):
    match_ids = draft.get('matches') if isinstance(draft, dict) else None
    bench_ids = draft.get('bench') if isinstance(draft, dict) else []
    if not isinstance(bench_ids, list):
        bench_ids = []

    fixed_pairs = normalize_fixed_pairs(
        draft.get('fixed_pairs') if isinstance(draft, dict) else None,
        match_ids if isinstance(match_ids, list) else [],
    )
    optimized_matches = optimize_draft_matches_by_pair_score(
        match_ids,
        fixed_pairs,
        participants,
        level_map,
        gender_weight,
        win_stats,
        get_historical_pair_counts(),
    )
    if optimized_matches is None:
        return PairOptimizationResult(
            False,
            [],
            bench_ids,
            fixed_pairs,
            draft.get('court_count') if isinstance(draft, dict) else None,
            '編集中の組み合わせを調整できませんでした。内容を確認してください',
        )

    return PairOptimizationResult(
        True,
        optimized_matches,
        bench_ids,
        fixed_pairs,
        draft.get('court_count'),
        'ペアスコアが近いペア同士で対戦するように調整しました',
    )
