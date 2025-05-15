from flask import Blueprint, jsonify
from utils.db_utils import get_all_participants
from utils.state_utils import load_match_state

api_bp = Blueprint('api', __name__)

@api_bp.route("/api/participants")
def api_participants():
    """
    参加者一覧をJSONで返すAPI
    例: [{"id": 1, "name": "Alice", "gender": "F", "level": "B", "active": true}, ...]
    """
    participants = get_all_participants()
    return jsonify(participants)

@api_bp.route("/api/match_state")
def api_match_state():
    """
    現在の組み合わせ状態（matchesとbench）をJSONで返すAPI
    例: {"matches": [[{"name": "A"}, {"name": "B"}]], "bench": [{"name": "C"}]}
    """
    match_state = load_match_state()
    return jsonify(match_state)