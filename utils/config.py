"""Configuration loading and normalization helpers."""

import json


CONFIG_FILE = "config.json"

VALID_SCORE_INPUT_MODES = {"winner_only", "score"}
DEFAULT_SCORE_INPUT_MODE = "winner_only"
DEFAULT_SCORING_SYSTEM = {
    "points_per_game": 21,
    "games_per_match": 1,
    "deuce_enabled": False,
    "max_points": 21,
}

DEFAULT_CONSECUTIVE_PLAY_LIMIT = 3
MIN_CONSECUTIVE_PLAY_LIMIT = 2
MAX_CONSECUTIVE_PLAY_LIMIT = 10


def parse_positive_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def parse_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "on", "yes"}


def normalize_score_input_mode(value):
    if value in VALID_SCORE_INPUT_MODES:
        return value
    return DEFAULT_SCORE_INPUT_MODE


def normalize_scoring_system(value):
    if not isinstance(value, dict):
        value = {}
    points_per_game = parse_positive_int(
        value.get("points_per_game"),
        DEFAULT_SCORING_SYSTEM["points_per_game"],
    )
    games_per_match = parse_positive_int(
        value.get("games_per_match"),
        DEFAULT_SCORING_SYSTEM["games_per_match"],
    )
    max_points = parse_positive_int(value.get("max_points"), points_per_game)
    if max_points < points_per_game:
        max_points = points_per_game
    return {
        "points_per_game": points_per_game,
        "games_per_match": games_per_match,
        "deuce_enabled": parse_bool(
            value.get("deuce_enabled"),
            DEFAULT_SCORING_SYSTEM["deuce_enabled"],
        ),
        "max_points": max_points,
    }


def normalize_history_dump_email(value):
    if not isinstance(value, dict):
        value = {}
    return {
        "enabled": parse_bool(value.get("enabled"), False),
        "recipient": (value.get("recipient") or "").strip(),
    }


def normalize_paypay_link_expirations(value):
    if not isinstance(value, dict):
        value = {}
    return {
        "adults": value.get("adults") or "",
        "students": value.get("students") or "",
    }


def normalize_consecutive_play_limit(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CONSECUTIVE_PLAY_LIMIT
    if parsed < MIN_CONSECUTIVE_PLAY_LIMIT or parsed > MAX_CONSECUTIVE_PLAY_LIMIT:
        return DEFAULT_CONSECUTIVE_PLAY_LIMIT
    return parsed


def normalize_config(config):
    if not isinstance(config, dict):
        config = {}
    normalized = dict(config)
    normalized["score_input_mode"] = normalize_score_input_mode(
        config.get("score_input_mode")
    )
    normalized["scoring_system"] = normalize_scoring_system(
        config.get("scoring_system")
    )
    normalized["consecutive_play_limit"] = normalize_consecutive_play_limit(
        config.get("consecutive_play_limit")
    )
    normalized.setdefault("paypay_links", {})
    normalized["paypay_link_expirations"] = normalize_paypay_link_expirations(
        config.get("paypay_link_expirations")
    )
    normalized["history_dump_email"] = normalize_history_dump_email(
        config.get("history_dump_email")
    )
    normalized.setdefault("level_map", {})
    normalized.setdefault("gender_weight", {})
    return normalized


def load_raw_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as config_file:
        return json.load(config_file)


def load_config():
    return normalize_config(load_raw_config())


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file, indent=4, ensure_ascii=False)


def load_consecutive_play_limit():
    try:
        config = load_raw_config()
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_CONSECUTIVE_PLAY_LIMIT
    if not isinstance(config, dict):
        return DEFAULT_CONSECUTIVE_PLAY_LIMIT
    return normalize_consecutive_play_limit(config.get("consecutive_play_limit"))
