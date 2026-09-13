"""Named match relational reads and atomic commands."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone

from flask import has_request_context

from data.match_sessions import format_utc_datetime, normalize_utc_datetime


MATCH_COLUMNS = (
    "id, round_id, court_number, team1_player1_id, team1_player2_id, "
    "team2_player1_id, team2_player2_id, team1_score, team2_score, "
    "score_text, winner_team, created_at"
)
BENCH_COLUMNS = "id, round_id, participant_id, created_at"
ROUND_COLUMNS = "id, session_id, round_number, created_at"


@dataclass(frozen=True)
class MatchHistoryRecord:
    id: int
    round_id: int
    court_number: int
    team1_player1_id: int
    team1_player2_id: int
    team2_player1_id: int
    team2_player2_id: int
    team1_score: int | None
    team2_score: int | None
    score_text: str | None
    winner_team: int | None
    created_at: datetime


@dataclass(frozen=True)
class BenchHistoryRecord:
    id: int
    round_id: int
    participant_id: int
    created_at: datetime


@dataclass(frozen=True)
class MatchRoundRecord:
    id: int
    session_id: int | None
    round_number: int
    created_at: datetime
    matches: tuple[MatchHistoryRecord, ...] = ()
    bench_players: tuple[BenchHistoryRecord, ...] = ()


@dataclass(frozen=True)
class MatchScoreUpdate:
    match_history_id: int
    team1_score: int | None
    team2_score: int | None
    score_text: str | None
    winner_team: int | None


def _adapter(storage):
    if storage is not None:
        return storage
    from storage.provider import create_storage, get_storage
    return get_storage() if has_request_context() else create_storage()


def _optional_int(value):
    return None if value is None else int(value)


def _match_record(row):
    if row is None:
        return None
    return MatchHistoryRecord(
        id=int(row["id"]), round_id=int(row["round_id"]),
        court_number=int(row["court_number"]),
        team1_player1_id=int(row["team1_player1_id"]),
        team1_player2_id=int(row["team1_player2_id"]),
        team2_player1_id=int(row["team2_player1_id"]),
        team2_player2_id=int(row["team2_player2_id"]),
        team1_score=_optional_int(row["team1_score"]),
        team2_score=_optional_int(row["team2_score"]),
        score_text=row["score_text"], winner_team=_optional_int(row["winner_team"]),
        created_at=normalize_utc_datetime(row["created_at"]),
    )


def _bench_record(row):
    return BenchHistoryRecord(
        id=int(row["id"]), round_id=int(row["round_id"]),
        participant_id=int(row["participant_id"]),
        created_at=normalize_utc_datetime(row["created_at"]),
    )


def _round_record(row):
    return MatchRoundRecord(
        id=int(row["id"]), session_id=_optional_int(row["session_id"]),
        round_number=int(row["round_number"]),
        created_at=normalize_utc_datetime(row["created_at"]),
    )


def _assemble_rounds(round_rows, storage):
    rounds = [_round_record(row) for row in round_rows]
    if not rounds:
        return []
    ids = [item.id for item in rounds]
    placeholders = ", ".join("?" for _ in ids)
    matches = [_match_record(row) for row in storage.all(
        f"SELECT {MATCH_COLUMNS} FROM match_histories "
        f"WHERE round_id IN ({placeholders}) ORDER BY round_id, court_number, id", *ids
    )]
    benches = [_bench_record(row) for row in storage.all(
        f"SELECT {BENCH_COLUMNS} FROM bench_histories "
        f"WHERE round_id IN ({placeholders}) ORDER BY round_id, id", *ids
    )]
    matches_by_round = {round_id: [] for round_id in ids}
    benches_by_round = {round_id: [] for round_id in ids}
    for match in matches:
        matches_by_round[match.round_id].append(match)
    for bench in benches:
        benches_by_round[bench.round_id].append(bench)
    return [replace(
        item, matches=tuple(matches_by_round[item.id]),
        bench_players=tuple(benches_by_round[item.id]),
    ) for item in rounds]


def get_latest_match_round_with_matches(round_number, *, storage=None):
    adapter = _adapter(storage)
    row = adapter.first(
        f"SELECT {ROUND_COLUMNS} FROM match_rounds WHERE round_number = ? "
        "ORDER BY id DESC LIMIT 1", round_number
    )
    return _assemble_rounds([row], adapter)[0] if row else None


def get_match_rounds_for_dump(*, storage=None):
    adapter = _adapter(storage)
    rounds = _assemble_rounds(
        adapter.all(f"SELECT {ROUND_COLUMNS} FROM match_rounds"), adapter
    )
    return sorted(rounds, key=lambda item: (item.created_at, item.id))


def get_latest_match_round(round_number, *, session_id=None, storage=None):
    adapter = _adapter(storage)
    if session_id is not None:
        row = adapter.first(
            f"SELECT {ROUND_COLUMNS} FROM match_rounds "
            "WHERE session_id = ? AND round_number = ? ORDER BY id DESC LIMIT 1",
            session_id, round_number,
        )
        return _round_record(row) if row else None
    row = adapter.first(
        f"SELECT {ROUND_COLUMNS} FROM match_rounds WHERE session_id IS NULL "
        "AND round_number = ? ORDER BY id DESC LIMIT 1", round_number
    )
    return _round_record(row) if row else None


def get_match_round_with_matches(round_id, *, storage=None):
    adapter = _adapter(storage)
    row = adapter.first(f"SELECT {ROUND_COLUMNS} FROM match_rounds WHERE id = ?", round_id)
    return _assemble_rounds([row], adapter)[0] if row else None


def get_match_history_by_id(match_history_id, *, storage=None):
    return _match_record(_adapter(storage).first(
        f"SELECT {MATCH_COLUMNS} FROM match_histories WHERE id = ?", match_history_id
    ))


def get_match_rounds_with_details(*, storage=None):
    adapter = _adapter(storage)
    rounds = _assemble_rounds(
        adapter.all(f"SELECT {ROUND_COLUMNS} FROM match_rounds"), adapter
    )
    return sorted(
        rounds, key=lambda item: (item.created_at, item.id), reverse=True
    )


def get_recent_rounds_with_matches(limit, *, storage=None):
    adapter = _adapter(storage)
    return _assemble_rounds(adapter.all(
        f"SELECT {ROUND_COLUMNS} FROM match_rounds ORDER BY id DESC LIMIT ?", limit
    ), adapter)


def get_historical_pair_counts(*, storage=None):
    rows = _adapter(storage).all(
        "SELECT team1_player1_id, team1_player2_id, team2_player1_id, "
        "team2_player2_id FROM match_histories"
    )
    pair_counts = {}
    for row in rows:
        for pair in ((row["team1_player1_id"], row["team1_player2_id"]),
                     (row["team2_player1_id"], row["team2_player2_id"])):
            if pair[0] is None or pair[1] is None or pair[0] == pair[1]:
                continue
            key = tuple(sorted((int(pair[0]), int(pair[1]))))
            pair_counts[key] = pair_counts.get(key, 0) + 1
    return pair_counts


def get_decided_match_histories(*, storage=None):
    return [_match_record(row) for row in _adapter(storage).all(
        f"SELECT {MATCH_COLUMNS} FROM match_histories WHERE winner_team IN (1, 2)"
    )]


def confirm_match_relational(session_id, round_number, matches, bench_ids,
                             confirmed_at=None, *, storage=None):
    from storage.errors import StorageConflictError, StorageUniqueError

    adapter = _adapter(storage)
    timestamp = format_utc_datetime(confirmed_at or datetime.now(timezone.utc))
    statements = [(
        "INSERT INTO match_rounds (session_id, round_number, created_at) VALUES (?, ?, ?)",
        (session_id, round_number, timestamp),
    )]
    for court_number, group in enumerate(matches, start=1):
        statements.append((
            "INSERT INTO match_histories (round_id, court_number, team1_player1_id, "
            "team1_player2_id, team2_player1_id, team2_player2_id, created_at) "
            "SELECT id, ?, ?, ?, ?, ?, ? FROM match_rounds "
            "WHERE session_id = ? AND round_number = ?",
            (court_number, *group, timestamp, session_id, round_number),
        ))
    for participant_id in bench_ids:
        statements.append((
            "INSERT INTO bench_histories (round_id, participant_id, created_at) "
            "SELECT id, ?, ? FROM match_rounds WHERE session_id = ? AND round_number = ?",
            (participant_id, timestamp, session_id, round_number),
        ))
    confirmed_ids = list(dict.fromkeys(pid for group in matches for pid in group))
    if confirmed_ids:
        placeholders = ", ".join("?" for _ in confirmed_ids)
        statements.append((
            f"UPDATE participants SET games_played = COALESCE(games_played, 0) + 1 "
            f"WHERE id IN ({placeholders})", tuple(confirmed_ids),
        ))
    statements.append((
        "UPDATE match_sessions SET status = 'confirmed', match_count = ?, "
        "confirmed_at = COALESCE(confirmed_at, ?) WHERE id = ?",
        (round_number, timestamp, session_id),
    ))
    try:
        adapter.batch(statements)
    except StorageUniqueError as error:
        raise StorageConflictError() from error
    saved_round = get_latest_match_round(
        round_number, session_id=session_id, storage=adapter
    )
    return get_match_round_with_matches(saved_round.id, storage=adapter)


def revert_match_relational(session_id, round_number, participant_ids, *, storage=None):
    adapter = _adapter(storage)
    target = get_latest_match_round(round_number, session_id=session_id, storage=adapter)
    if target is None:
        return None

    statements = []
    ids = list(dict.fromkeys(participant_ids))
    if ids:
        placeholders = ", ".join("?" for _ in ids)
        statements.append((
            f"UPDATE participants SET games_played = MAX(COALESCE(games_played, 0) - 1, 0) "
            f"WHERE id IN ({placeholders})", tuple(ids),
        ))
    statements.extend([
        ("DELETE FROM bench_histories WHERE round_id = ?", (target.id,)),
        ("DELETE FROM match_histories WHERE round_id = ?", (target.id,)),
        ("DELETE FROM match_rounds WHERE id = ?", (target.id,)),
    ])
    adapter.batch(statements)
    return target


def update_match_score(update, *, storage=None):
    return _adapter(storage).run(
        "UPDATE match_histories SET team1_score = ?, team2_score = ?, "
        "score_text = ?, winner_team = ? WHERE id = ?",
        update.team1_score, update.team2_score, update.score_text,
        update.winner_team, update.match_history_id,
    )


def update_round_scores(updates, *, storage=None):
    statements = [(
        "UPDATE match_histories SET team1_score = ?, team2_score = ?, "
        "score_text = ?, winner_team = ? WHERE id = ?",
        (item.team1_score, item.team2_score, item.score_text,
         item.winner_team, item.match_history_id),
    ) for item in updates]
    return _adapter(storage).batch(statements) if statements else []


def clear_match_history(*, storage=None):
    return _adapter(storage).batch([
        ("DELETE FROM bench_histories", ()),
        ("DELETE FROM match_histories", ()),
        ("DELETE FROM match_rounds", ()),
    ])
