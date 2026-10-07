"""Synthetic Phase 10 source fixture used by tests and the local rehearsal."""

import json
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]


def create_synthetic_fixture(directory):
    directory = Path(directory).resolve()
    source_directory = directory / "source"
    archive_directory = source_directory / "instance" / "history_dumps"
    source_directory.mkdir(parents=True, exist_ok=True)
    archive_directory.mkdir(parents=True, exist_ok=True)
    database = source_directory / "participants.db"

    connection = sqlite3.connect(database)
    try:
        for migration in sorted((ROOT / "migrations/d1").glob("*.sql")):
            connection.executescript(migration.read_text(encoding="utf-8"))
        participants = [
            (1, "Alice", "female", "beginner", 0.9, 2, 1, "C1"),
            (2, "ボブ", "male", "intermediate", 1.1, 1, 1, "C2"),
            (3, "Céline", "female", "advanced", 1.0, 3, 1, "C3"),
            (4, "Дмитрий", "male", "beginner", 1.2, 0, 1, "C4"),
        ]
        connection.executemany(
            "INSERT INTO participants "
            "(id, name, gender, level, weight, games_played, active, card) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            participants,
        )
        timestamp = "2026-09-19T01:02:03.000000Z"
        connection.execute(
            "INSERT INTO app_config (key, config_json, version) VALUES (?, ?, ?)",
            ("main", json.dumps({
                "level_map": {"beginner": 1, "intermediate": 2, "advanced": 3},
                "gender_weight": {"male": 1.0, "female": 0.9},
                "score_input_mode": "winner_only",
                "history_dump_email": {"enabled": False, "recipient": ""},
            }, ensure_ascii=False, separators=(",", ":")), 7),
        )
        connection.execute(
            "INSERT INTO match_sessions "
            "(id, status, match_count, created_at, confirmed_at, notification_sent_at, creation_token) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (7, "confirmed", 1, timestamp, timestamp, timestamp, "fixture-session-token"),
        )
        connection.execute(
            "INSERT INTO match_rounds (id, session_id, round_number, created_at) "
            "VALUES (?, ?, ?, ?)", (9, 7, 1, timestamp),
        )
        connection.execute(
            "INSERT INTO match_histories "
            "(id, round_id, court_number, team1_player1_id, team1_player2_id, "
            "team2_player1_id, team2_player2_id, team1_score, team2_score, "
            "score_text, winner_team, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (11, 9, 1, 1, 2, 3, 4, 21, 18, "21-18", 1, timestamp),
        )
        connection.execute(
            "INSERT INTO bench_histories (id, round_id, participant_id, created_at) "
            "VALUES (?, ?, ?, ?)", (12, 9, 4, timestamp),
        )
        connection.execute(
            "INSERT INTO line_accounts "
            "(id, participant_id, line_user_id, display_name, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (13, 1, "U-synthetic-1", "Alice LINE", 1, timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO notification_subscriptions "
            "(id, session_id, participant_id, channel, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (14, 7, 1, "line", 1, timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO line_link_tokens "
            "(id, token, participant_id, session_id, used_at, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (15, "synthetic-link-token", 1, 7, None, timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO match_notifications "
            "(id, session_id, match_count, channel, status, created_at, sent_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (16, 7, 1, "line", "sent", timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO notification_delivery_logs "
            "(id, session_id, participant_id, match_count, channel, status, error_message, sent_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (17, 7, 1, 1, "line", "sent", None, timestamp),
        )
        connection.execute(
            "UPDATE runtime_state SET state_json=?, version=? WHERE key='current_match'",
            (json.dumps({
                "match_active": True, "match_count": 1,
                "matches": [[1, 2, 3, 4]], "bench": [],
                "timestamp": timestamp, "session_id": 7,
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")), 4),
        )
        connection.execute(
            "UPDATE runtime_state SET state_json=?, version=? WHERE key='current_draft'",
            (json.dumps({
                "draft": True, "matches": [[1, 2, 3, 4]], "bench": [],
                "fixed_pairs": [[1, 2]], "timestamp": timestamp,
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")), 3),
        )
        connection.commit()
    finally:
        connection.close()

    (source_directory / "config.json").write_text(
        json.dumps({"level_map": {"beginner": 1}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (source_directory / "match_state.json").write_text("{}", encoding="utf-8")
    (source_directory / "draft_state.json").write_text("null", encoding="utf-8")
    first = archive_directory / "match_history_manual_dump_20260919_010203_000001.json"
    second = archive_directory / "match_history_manual_dump_20260818_010203_000002.json"
    first.write_bytes(json.dumps({"rounds": [{"matches": [], "bench": []}]}, ensure_ascii=False).encode("utf-8"))
    second.write_bytes(b"{not-json-but-valid-archive-bytes")
    (archive_directory / "not_json.txt").write_bytes(b"ignore me")
    return {
        "root": directory,
        "source_directory": source_directory,
        "database": database,
        "archives": archive_directory,
        "config": source_directory / "config.json",
        "match_state": source_directory / "match_state.json",
        "draft_state": source_directory / "draft_state.json",
    }
