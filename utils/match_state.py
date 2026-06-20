import copy
import json
import os
from datetime import datetime

STATE_FILE = 'match_state.json'
DEFAULT_MATCH_STATE = {
    "match_active": False,
    "match_count": 0,
    "matches": [],
    "bench": [],
}


def load_match_state():
    if not os.path.exists(STATE_FILE):
        return copy.deepcopy(DEFAULT_MATCH_STATE)
    with open(STATE_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_match_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def save_match_state_full(match_active, matches, bench, match_count, *, court_count=None):
    state = {
        "match_active": match_active,
        "match_count": match_count,
        "matches": matches,
        "bench": bench,
        "timestamp": datetime.now().astimezone().isoformat()
    }
    if isinstance(court_count, int) and court_count > 0:
        state["court_count"] = court_count
    save_match_state(state)
