import json
import os

STATE_FILE = 'match_state.json'

def load_match_state():
    if not os.path.exists(STATE_FILE):
        return {"match_active": False, "match_count": 0}
    with open(STATE_FILE, 'r') as f:
        return json.load(f)

def save_match_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)