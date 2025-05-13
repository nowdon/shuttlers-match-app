import os
import json

def is_draft_active():
    if not os.path.exists("draft_state.json"):
        return False
    with open("draft_state.json", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("draft", False)