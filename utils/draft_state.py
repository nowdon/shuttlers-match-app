from datetime import datetime
import os
import json

DRAFT_FILE = 'draft_state.json'

def save_draft_state(matches, bench, draft=True):
    '''
    data = {
        "draft": draft,
        "timestamp": datetime.now().astimezone().isoformat(),
        "matches": [[p.id for p in group] for group in matches],
        "bench": [p.id for p in bench]
    }
    '''
    data = {
        "draft": draft,
        "timestamp": datetime.now().astimezone().isoformat(),
        "matches": matches,
        "bench": bench
    }
    with open(DRAFT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)

def load_draft_state():
    if not os.path.exists(DRAFT_FILE):
        return None
    with open(DRAFT_FILE, 'r') as f:
        return json.load(f)

def clear_draft_state():
    if os.path.exists(DRAFT_FILE):
        os.remove(DRAFT_FILE)