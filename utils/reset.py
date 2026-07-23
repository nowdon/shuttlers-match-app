from datetime import datetime

from models import Participant, db

from utils.match_state import load_match_state, save_match_state, save_match_state_full
from utils.draft_state import clear_draft_state
from utils.match_session import close_current_match_session, ensure_current_match_session

def reset_match_state(create_new_session=True):
    close_current_match_session()

    # JSONファイルのリセット
    state = load_match_state()
    state['match_active'] = False
    state['match_count'] = 0
    state['matches'] = []
    state['bench'] = []
    save_match_state_full(
        state.get('match_active', False),
        state.get('matches', []),
        state.get('bench', []),
        state.get('match_count', 0),
        session_id=None,
    )

    # 試合回数のリセット（※reset_dbで全削除するなら不要だが共通化）
    participants = Participant.query.all()
    for p in participants:
        p.games_played = 0
    db.session.commit()

    # 下書き状態の削除
    clear_draft_state()

    if create_new_session:
        ensure_current_match_session()


def clear_match_runtime_state():
    """Clear shared match runtime files without touching the database."""
    save_match_state({
        "match_active": False,
        "match_count": 0,
        "matches": [],
        "bench": [],
        "session_id": None,
        "timestamp": datetime.now().astimezone().isoformat(),
    })
    clear_draft_state()
