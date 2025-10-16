from flask import session
from models import Participant, db
from utils.match_state import load_match_state, save_match_state_full
from utils.draft_state import clear_draft_state

def reset_match_state():
    # セッション情報のリセット
    session.pop('court_count', None)
    session.pop('draft_matches', None)
    session.pop('draft_bench', None)
    session.pop('last_confirmed_matches', None)
    session.pop('last_confirmed_bench', None)

    # JSONファイルのリセット
    state = load_match_state()
    state['match_active'] = False
    state['match_count'] = 0
    state['matches'] = []
    state['bench'] = []
    save_match_state_full(state.get('match_active', False), state.get('matches', []), state.get('bench', []), state.get('match_count', 0))

    # 試合回数のリセット（※reset_dbで全削除するなら不要だが共通化）
    participants = Participant.query.all()
    for p in participants:
        p.games_played = 0
    db.session.commit()

    # 下書き状態の削除
    clear_draft_state()