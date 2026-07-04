import random
from dataclasses import dataclass

from models import MatchHistory
from utils.score import calculate_pair_score


INVALID_DRAFT_MESSAGE = '編集中の組み合わせデータが壊れています。再生成してください。'


@dataclass
class PairOptimizationResult:
    success: bool
    matches: list
    bench: list
    fixed_pairs: list
    court_count: int | None
    message: str


def _has_fixed_pairs(draft):
    return isinstance(draft, dict) and bool(draft.get('fixed_pairs'))


def split_editable_draft_matches_and_bench(draft):
    """Return renderable 4-player matches and bench IDs, preserving old short trailing groups.

    Older draft files could store bench-like players as the final short group in
    ``matches``.  Keep that shape editable/confirmable when there are no fixed
    pairs, but never treat the short group as a renderable court.
    """
    match_ids = draft.get('matches') if isinstance(draft, dict) else None
    bench_ids = draft.get('bench', []) if isinstance(draft, dict) else []
    if not isinstance(match_ids, list) or not isinstance(bench_ids, list):
        return None

    renderable_matches = []
    legacy_bench = []
    has_fixed_pairs = _has_fixed_pairs(draft)

    for index, group in enumerate(match_ids):
        if not isinstance(group, list):
            return None
        if len(group) == 4:
            if legacy_bench:
                return None
            renderable_matches.append(group)
            continue
        if has_fixed_pairs or index != len(match_ids) - 1 or len(group) == 0 or not renderable_matches:
            return None
        legacy_bench = group

    return renderable_matches, list(bench_ids) + legacy_bench


def validate_editable_draft(draft, participants):
    """Return whether an active draft can be safely edited/confirmed."""
    if not isinstance(draft, dict):
        return False

    split = split_editable_draft_matches_and_bench(draft)
    if split is None:
        return False
    renderable_match_ids, effective_bench_ids = split

    all_match_player_ids = []
    for group in renderable_match_ids:
        try:
            group_ids = [int(pid) for pid in group]
        except (TypeError, ValueError):
            return False
        if len(group_ids) != len(set(group_ids)):
            return False
        all_match_player_ids.extend(group_ids)

    if len(all_match_player_ids) != len(set(all_match_player_ids)):
        return False

    participant_ids = set(participants)
    if any(pid not in participant_ids for pid in all_match_player_ids):
        return False

    try:
        normalized_bench_ids = [int(pid) for pid in effective_bench_ids]
    except (TypeError, ValueError):
        return False
    if len(normalized_bench_ids) != len(set(normalized_bench_ids)):
        return False
    if any(pid not in participant_ids for pid in normalized_bench_ids):
        return False
    if set(normalized_bench_ids).intersection(all_match_player_ids):
        return False

    if 'fixed_pairs' in draft and not validate_fixed_pairs(
        draft.get('fixed_pairs'), renderable_match_ids, participant_ids
    ):
        return False

    return True

def validate_fixed_pairs(raw_fixed_pairs, match_ids, participant_ids):
    if not isinstance(raw_fixed_pairs, list):
        return False

    used_ids = set()
    for raw_pair in raw_fixed_pairs:
        if not isinstance(raw_pair, (list, tuple)) or len(raw_pair) != 2:
            return False
        if not all(isinstance(pid, int) and not isinstance(pid, bool) for pid in raw_pair):
            return False
        pair_ids = [raw_pair[0], raw_pair[1]]
        if pair_ids[0] == pair_ids[1]:
            return False
        if any(pid in used_ids for pid in pair_ids):
            return False
        if any(pid not in participant_ids for pid in pair_ids):
            return False

        position_1 = get_current_pair(match_ids, pair_ids[0])
        position_2 = get_current_pair(match_ids, pair_ids[1])
        if (
            position_1 is None
            or position_2 is None
            or position_1[0] != position_2[0]
            or position_1[1] != position_2[1]
            or len(position_1[2]) != 2
        ):
            return False
        used_ids.update(pair_ids)

    return True


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
        if not all(isinstance(pid, int) and not isinstance(pid, bool) for pid in raw_pair):
            continue
        pair_ids = [raw_pair[0], raw_pair[1]]
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


def _historical_pair_penalty(pairs, fixed_key_set, pair_counts):
    return sum(
        pair_counts.get(tuple(sorted(pair)), 0)
        for pair in pairs
        if tuple(sorted(pair)) not in fixed_key_set
    )


def _build_random_pair_candidate(remaining_ids):
    shuffled_ids = list(remaining_ids)
    random.shuffle(shuffled_ids)
    return [tuple(shuffled_ids[index:index + 2]) for index in range(0, len(shuffled_ids), 2)]


def optimize_draft_matches_by_pair_score(
    match_ids,
    fixed_pairs,
    participants,
    level_map,
    gender_weight,
    win_stats,
    pair_counts,
    random_trials=50,
):
    """Randomly re-pair draft players, then match pairs with similar pair scores."""
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

    fixed_key_set = {tuple(sorted(pair)) for pair in fixed_pairs}
    fixed_player_ids = {pid for pair in fixed_pairs for pid in pair}
    if not fixed_player_ids.issubset(set(player_ids)):
        return None

    fixed_pair_units = [tuple(pair) for pair in fixed_pairs]
    remaining_ids = [pid for pid in player_ids if pid not in fixed_player_ids]
    if len(remaining_ids) % 2 != 0:
        return None

    best_pairs = None
    best_penalty = None
    trials = max(1, int(random_trials or 1))
    for _ in range(trials):
        candidate_pairs = fixed_pair_units + _build_random_pair_candidate(remaining_ids)
        penalty = _historical_pair_penalty(candidate_pairs, fixed_key_set, pair_counts)
        if best_penalty is None or penalty < best_penalty or (penalty == best_penalty and random.choice([False, True])):
            best_pairs = candidate_pairs
            best_penalty = penalty

    if best_pairs is None or len(best_pairs) % 2 != 0:
        return None

    scored_pairs = []
    for pair in best_pairs:
        score = build_pair_score(pair, participants, level_map, gender_weight, win_stats)
        if score is None:
            return None
        scored_pairs.append({"pair": pair, "score": score})

    scored_pairs.sort(key=lambda item: item["score"])

    optimized = []
    for index in range(0, len(scored_pairs), 2):
        left = scored_pairs[index]["pair"]
        right = scored_pairs[index + 1]["pair"]
        optimized.append([left[0], left[1], right[0], right[1]])
    return optimized


def optimize_draft_pairs(draft, participants, level_map, gender_weight, win_stats):
    raw_bench_ids = draft.get('bench') if isinstance(draft, dict) else []
    if not isinstance(raw_bench_ids, list):
        raw_bench_ids = []

    if not validate_editable_draft(draft, participants):
        return PairOptimizationResult(
            False,
            [],
            raw_bench_ids,
            [],
            draft.get('court_count') if isinstance(draft, dict) else None,
            INVALID_DRAFT_MESSAGE,
        )

    editable_parts = split_editable_draft_matches_and_bench(draft)
    if editable_parts is None:
        return PairOptimizationResult(
            False,
            [],
            raw_bench_ids,
            [],
            draft.get('court_count') if isinstance(draft, dict) else None,
            INVALID_DRAFT_MESSAGE,
        )
    match_ids, bench_ids = editable_parts

    fixed_pairs = normalize_fixed_pairs(
        draft.get('fixed_pairs') if isinstance(draft, dict) else None,
        match_ids,
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
            INVALID_DRAFT_MESSAGE,
        )

    return PairOptimizationResult(
        True,
        optimized_matches,
        bench_ids,
        fixed_pairs,
        draft.get('court_count'),
        'ペアスコアが近いペア同士で対戦するように調整しました',
    )
