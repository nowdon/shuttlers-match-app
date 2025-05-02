import os
import json

DRAFT_FILE = 'draft_state.json'

def save_draft_state(matches, bench):
    data = {
        "matches": [[p.id for p in group] for group in matches],
        "bench": [p.id for p in bench]
    }
    with open(DRAFT_FILE, 'w') as f:
        json.dump(data, f)

def load_draft_state():
    if not os.path.exists(DRAFT_FILE):
        return None
    with open(DRAFT_FILE, 'r') as f:
        return json.load(f)

def clear_draft_state():
    if os.path.exists(DRAFT_FILE):
        os.remove(DRAFT_FILE)