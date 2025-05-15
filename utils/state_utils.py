import json
import os

MATCH_STATE_PATH = "match_state.json"

def load_match_state():
    """
    match_state.json を読み込んで dict を返す
    {"matches": [...], "bench": [...]} の形式
    """
    if not os.path.exists(MATCH_STATE_PATH):
        return {"matches": [], "bench": []}
    
    with open(MATCH_STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_match_state(state):
    """
    組み合わせ状態を match_state.json に保存する
    """
    with open(MATCH_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)