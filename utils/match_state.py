import json
import os
from datetime import datetime

STATE_FILE = 'match_state.json'

def load_match_state():
    if not os.path.exists(STATE_FILE):
        return {"match_active": False, "match_count": 0, "matches": [], "bench": []}
    with open(STATE_FILE, 'r') as f:
        return json.load(f)

def save_match_state_full(matches, bench, match_count):
    state = {
        "match_active": True,
        "match_count": match_count,
        "matches": matches,
        "bench": bench,
        "timestamp": datetime.now().astimezone().isoformat()
    }
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# 残すけど非推奨
def save_match_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)