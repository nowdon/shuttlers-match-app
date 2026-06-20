import json
import os
from datetime import datetime

DRAFT_FILE = 'draft_state.json'


def load_draft_state():
    if not os.path.exists(DRAFT_FILE):
        return None
    with open(DRAFT_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_draft_state(matches, bench, draft=True,court_count=None):
    data = {
        "draft": draft,
        "timestamp": datetime.now().astimezone().isoformat(),
        "matches": matches,
        "bench": bench,
    }
    if court_count is not None:
        data["court_count"] = court_count

    with open(DRAFT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def clear_draft_state():
    if os.path.exists(DRAFT_FILE):
        os.remove(DRAFT_FILE)


def is_active_draft(state):
    return (
        isinstance(state, dict)
        and state.get("draft") is True
        and isinstance(state.get("matches"), list)
        and isinstance(state.get("bench"), list)
    )


def get_active_draft():
    state = load_draft_state()
    return state if is_active_draft(state) else None
