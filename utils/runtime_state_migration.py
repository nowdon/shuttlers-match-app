"""One-time SQLite bootstrap from the legacy runtime JSON files."""

import json
from pathlib import Path
import sqlite3

from data.runtime_state import (
    CURRENT_DRAFT,
    CURRENT_MATCH,
    DEFAULT_MATCH_STATE,
    serialize_state,
)


def _load_legacy_state(path, default):
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Legacy runtime state is invalid: {path.name}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"Legacy runtime state is invalid: {path.name}")
    return value


def ensure_runtime_state_storage(database_path, *, legacy_directory=None):
    """Create/seed runtime rows once, importing legacy JSON only when unseeded."""
    database_path = Path(database_path)
    legacy_directory = Path(legacy_directory or Path.cwd())
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS runtime_state ("
            "key TEXT PRIMARY KEY NOT NULL, state_json TEXT, version INTEGER NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS runtime_state_cas_guard ("
            "id INTEGER PRIMARY KEY NOT NULL)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO runtime_state_cas_guard (id) VALUES (1)"
        )

        session_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(match_sessions)")
        }
        if session_columns:
            if "creation_token" not in session_columns:
                connection.execute("ALTER TABLE match_sessions ADD COLUMN creation_token TEXT")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_match_sessions_creation_token "
                "ON match_sessions(creation_token) WHERE creation_token IS NOT NULL"
            )

        count = connection.execute(
            "SELECT COUNT(*) FROM runtime_state"
        ).fetchone()[0]
        if count == 0:
            match_state = _load_legacy_state(
                legacy_directory / "match_state.json", DEFAULT_MATCH_STATE.copy()
            )
            draft_state = _load_legacy_state(
                legacy_directory / "draft_state.json", None
            )
            connection.execute(
                "INSERT INTO runtime_state (key, state_json, version) VALUES (?, ?, 1)",
                (CURRENT_MATCH, serialize_state(match_state)),
            )
            connection.execute(
                "INSERT INTO runtime_state (key, state_json, version) VALUES (?, ?, 1)",
                (
                    CURRENT_DRAFT,
                    None if draft_state is None else serialize_state(draft_state),
                ),
            )
        else:
            connection.execute(
                "INSERT OR IGNORE INTO runtime_state (key, state_json, version) "
                "VALUES (?, ?, 1)",
                (CURRENT_MATCH, serialize_state(DEFAULT_MATCH_STATE)),
            )
            connection.execute(
                "INSERT OR IGNORE INTO runtime_state (key, state_json, version) "
                "VALUES (?, NULL, 1)",
                (CURRENT_DRAFT,),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
