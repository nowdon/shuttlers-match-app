"""Application-specific runtime and configuration transformations."""

import json
from pathlib import Path
import re

from data.runtime_state import CURRENT_DRAFT, CURRENT_MATCH, serialize_state
from utils.config import normalize_config

from .errors import MigrationError, ValidationError


SECRET_KEY_NAMES = {
    "SECRET_KEY",
    "LINE_CHANNEL_SECRET",
    "LINE_CHANNEL_ACCESS_TOKEN",
    "SMTP_PASSWORD",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "CLOUDFLARE_API_TOKEN",
    "CF_API_TOKEN",
}
SECRET_KEY_PATTERN = re.compile(
    r"(?:^|[_-])(?:PASSWORD|PASSWD|SECRET|TOKEN|CREDENTIALS?|"
    r"API[_-]?KEY|ACCESS[_-]?(?:KEY|TOKEN)|REFRESH[_-]?TOKEN|"
    r"PRIVATE[_-]?KEY|CLIENT[_-]?SECRET)(?:$|[_-])",
    re.IGNORECASE,
)


def is_secret_config_key(key):
    name = str(key)
    return name.upper() in SECRET_KEY_NAMES or bool(SECRET_KEY_PATTERN.search(name))


def _secret_key_paths(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = path + (key_text,)
            if is_secret_config_key(key_text):
                yield ".".join(child_path)
            else:
                yield from _secret_key_paths(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _secret_key_paths(child, path + (f"[{index}]",))


def safe_config(config):
    if not isinstance(config, dict):
        raise MigrationError("Application config must be a JSON object")
    secret_paths = list(_secret_key_paths(config))
    if secret_paths:
        raise MigrationError(
            "Secret-like config key detected; secrets must be migrated out-of-band: "
            + secret_paths[0]
        )
    return config


def canonical_config_json(config):
    return json.dumps(
        safe_config(config), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _load_json_object(path, label):
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MigrationError(f"{label} is invalid: {path}") from error
    if not isinstance(value, dict):
        raise MigrationError(f"{label} must be a JSON object: {path}")
    return value


def _load_json_value(path, label):
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MigrationError(f"{label} is invalid: {path}") from error


def _integer(value, label):
    if isinstance(value, bool):
        raise ValidationError(f"{label} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValidationError(f"{label} must be an integer") from error
    if parsed != value and not isinstance(value, str):
        raise ValidationError(f"{label} must be an integer")
    return parsed


def _participant_ids_from_state(state, label):
    if not isinstance(state, dict):
        raise ValidationError(f"{label} must be an object")
    ids = []
    for field in ("matches", "bench"):
        value = state.get(field)
        if not isinstance(value, list):
            raise ValidationError(f"{label}.{field} must be a list")
        if field == "matches":
            for match_index, match in enumerate(value):
                if not isinstance(match, list) or len(match) != 4:
                    raise ValidationError(
                        f"{label}.matches[{match_index}] must contain four IDs"
                    )
                ids.extend(_integer(item, f"{label}.matches[{match_index}]") for item in match)
        else:
            ids.extend(_integer(item, f"{label}.bench") for item in value)
    fixed_pairs = state.get("fixed_pairs", [])
    if fixed_pairs is not None:
        if not isinstance(fixed_pairs, list):
            raise ValidationError(f"{label}.fixed_pairs must be a list")
        for pair_index, pair in enumerate(fixed_pairs):
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValidationError(f"{label}.fixed_pairs[{pair_index}] must contain two IDs")
            ids.extend(_integer(item, f"{label}.fixed_pairs[{pair_index}]") for item in pair)
    return ids


def validate_runtime_state_rows(rows, participant_ids, session_ids):
    by_key = {row.get("key"): row for row in rows}
    if set(by_key) != {CURRENT_MATCH, CURRENT_DRAFT}:
        raise ValidationError(
            "runtime_state must contain exactly current_match and current_draft rows"
        )
    for row in rows:
        version = _integer(row.get("version"), f"runtime_state[{row.get('key')}].version")
        if version < 1:
            raise ValidationError("runtime_state version must be positive")
        state_json = row.get("state_json")
        if state_json is None:
            if row["key"] == CURRENT_MATCH:
                raise ValidationError("current_match cannot be NULL")
            continue
        try:
            state = json.loads(state_json)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValidationError(f"runtime_state[{row.get('key')}] contains invalid JSON") from error
        if not isinstance(state, dict):
            raise ValidationError(f"runtime_state[{row.get('key')}] must contain an object")
        if row["key"] == CURRENT_MATCH:
            required = {"match_active", "match_count", "matches", "bench"}
            if not required.issubset(state):
                raise ValidationError("current_match is missing required fields")
        ids = _participant_ids_from_state(state, f"runtime_state[{row['key']}]" )
        unknown = sorted(set(ids) - participant_ids)
        if unknown:
            raise ValidationError(
                f"runtime_state[{row['key']}] references unknown participants: {unknown}"
            )
        session_id = state.get("session_id")
        if session_id is not None:
            session_id = _integer(session_id, f"runtime_state[{row['key']}].session_id")
            if session_id not in session_ids:
                raise ValidationError(
                    f"runtime_state[{row['key']}] references unknown session: {session_id}"
                )


def transform_runtime_from_legacy(legacy_directory, participant_ids, session_ids):
    directory = Path(legacy_directory)
    match_path = directory / "match_state.json"
    draft_path = directory / "draft_state.json"
    if not match_path.is_file() or not draft_path.is_file():
        raise MigrationError(
            "Legacy runtime fallback requires both match_state.json and draft_state.json"
        )
    match_state = _load_json_object(match_path, "Legacy match state")
    draft_state = _load_json_value(draft_path, "Legacy draft state")
    if draft_state is not None and not isinstance(draft_state, dict):
        raise MigrationError(f"Legacy draft state must be an object or null: {draft_path}")
    rows = [
        {"key": CURRENT_MATCH, "state_json": serialize_state(match_state), "version": 1},
        {
            "key": CURRENT_DRAFT,
            "state_json": None if draft_state is None else serialize_state(draft_state),
            "version": 1,
        },
    ]
    validate_runtime_state_rows(rows, participant_ids, session_ids)
    return rows


def transform_config_from_legacy(config_path):
    config = _load_json_object(config_path, "Legacy config")
    # Reuse the application's normalizer.  No migration-only config schema is
    # introduced; only the D1 storage representation is produced afterwards.
    normalized = normalize_config(config)
    return {"key": "main", "config_json": canonical_config_json(normalized), "version": 1}


def transform_existing_config(row):
    if not isinstance(row, dict):
        raise MigrationError("Stored app_config row is invalid")
    try:
        config = json.loads(row["config_json"])
        version = _integer(row["version"], "app_config.version")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise MigrationError("Stored app_config row is invalid") from error
    if not isinstance(config, dict) or version < 1:
        raise MigrationError("Stored app_config row is invalid")
    return {
        "key": "main",
        "config_json": canonical_config_json(config),
        "version": version,
    }
