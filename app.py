import csv
import os
import json
import io
import logging
import re
from io import TextIOWrapper
from flask import Flask, render_template, request, redirect, url_for, abort
from models import db, Participant, MatchRound, MatchHistory, BenchHistory
# from flask_sqlalchemy import SQLAlchemy
from flask import flash
from flask import Response
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import selectinload
from flask import send_from_directory
from flask import send_file
import qrcode
from logic import generate_matches
from itertools import zip_longest
from utils.match_state import load_match_state, save_match_state_full
from utils.draft_state import clear_draft_state, get_active_draft, save_draft_state
from utils.score import calculate_pair_score
from utils.reset import reset_match_state
from routes.api import api_bp

app = Flask(__name__, instance_relative_config=True)

# ログ設定をgunicornに合わせる
gunicorn_logger = logging.getLogger('gunicorn.error')
if gunicorn_logger.handlers:
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

DEFAULT_DEV_SECRET_KEY = 'shuttlers-match-app-dev-secret-key'


def get_secret_key():
    secret_key = os.environ.get('SECRET_KEY')
    if secret_key:
        return secret_key

    if os.environ.get('ALLOW_DEV_SECRET_KEY') == '1':
        app.logger.warning(
            'SECRET_KEY is not set. Using the development fallback secret key '
            'because ALLOW_DEV_SECRET_KEY=1 is set. Do not use this setting in production.'
        )
        return DEFAULT_DEV_SECRET_KEY

    raise RuntimeError(
        'SECRET_KEY is required. Set SECRET_KEY to a strong secret, or set '
        'ALLOW_DEV_SECRET_KEY=1 only for local development.'
    )


app.config['SECRET_KEY'] = get_secret_key()
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(app.instance_path, 'participants.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# モデル側のdbをアプリに紐づけ
db.init_app(app)


def ensure_database_tables():
    """Create any missing tables for the configured application database."""
    os.makedirs(app.instance_path, exist_ok=True)
    with app.app_context():
        db.create_all()
        ensure_match_history_score_text_column()


def is_duplicate_score_text_column_error(error):
    """Return True when SQLite reports score_text was already added."""
    message = str(getattr(error, "orig", error)).lower()
    return "duplicate column" in message and "score_text" in message


def ensure_match_history_score_text_column():
    """Add score_text to existing SQLite match history tables when missing."""
    inspector = inspect(db.engine)
    if not inspector.has_table(MatchHistory.__tablename__):
        return
    column_names = {column["name"] for column in inspector.get_columns(MatchHistory.__tablename__)}
    if "score_text" not in column_names:
        try:
            db.session.execute(text("ALTER TABLE match_histories ADD COLUMN score_text TEXT"))
            db.session.commit()
        except OperationalError as error:
            db.session.rollback()
            if is_duplicate_score_text_column_error(error):
                return
            raise


ensure_database_tables()
app.register_blueprint(api_bp)

ALL_CARDS = [
    f"{suit}{rank}"
    for suit in ['♥', '♦', '♣', '♠']
    for rank in ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
] + ['JOKER_RED', 'JOKER_BLACK']

    
VALID_SCORE_INPUT_MODES = {"winner_only", "score"}
DEFAULT_SCORE_INPUT_MODE = "winner_only"
DEFAULT_SCORING_SYSTEM = {"points_per_game": 21, "games_per_match": 1, "deuce_enabled": False, "max_points": 21}


def parse_positive_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def normalize_score_input_mode(value):
    if value in VALID_SCORE_INPUT_MODES:
        return value
    return DEFAULT_SCORE_INPUT_MODE


def parse_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "on", "yes"}


def normalize_scoring_system(value):
    if not isinstance(value, dict):
        value = {}
    points_per_game = parse_positive_int(
        value.get("points_per_game"),
        DEFAULT_SCORING_SYSTEM["points_per_game"],
    )
    games_per_match = parse_positive_int(
        value.get("games_per_match"),
        DEFAULT_SCORING_SYSTEM["games_per_match"],
    )
    max_points = parse_positive_int(value.get("max_points"), points_per_game)
    if max_points < points_per_game:
        max_points = points_per_game
    return {
        "points_per_game": points_per_game,
        "games_per_match": games_per_match,
        "deuce_enabled": parse_bool(value.get("deuce_enabled"), DEFAULT_SCORING_SYSTEM["deuce_enabled"]),
        "max_points": max_points,
    }


def normalize_config(config):
    if not isinstance(config, dict):
        config = {}
    normalized = dict(config)
    normalized["score_input_mode"] = normalize_score_input_mode(config.get("score_input_mode"))
    normalized["scoring_system"] = normalize_scoring_system(config.get("scoring_system"))
    normalized.setdefault("paypay_links", {})
    normalized.setdefault("level_map", {})
    normalized.setdefault("gender_weight", {})
    return normalized


def load_config():
    with open('config.json', 'r', encoding='utf-8') as f:
        return normalize_config(json.load(f))
    
config = load_config()
LEVEL_MAP = config.get("level_map", {})
GENDER_WEIGHT = config.get("gender_weight", {})

def get_match_count():
    state = load_match_state()
    count = state['match_count']
    if get_active_draft() is not None:
        return count + 1  # 表示上だけ+1
    return count


def get_draft_court_count(draft):
    court_count = draft.get('court_count')
    if isinstance(court_count, int) and court_count > 0:
        return court_count
    matches = draft.get('matches', [])
    if isinstance(matches, list) and matches:
        return len(matches)
    return 1


def get_confirmed_court_count(state):
    court_count = state.get('court_count')
    if isinstance(court_count, int) and court_count > 0:
        return court_count

    matches = state.get('matches')
    if isinstance(matches, list) and matches:
        return len(matches)

    return None

def card_to_filename(card):
    if card.startswith('JOKER'):
        return 'joker_red.png' if 'RED' in card else 'joker_black.png'
    suit_map = {'♥': 'h', '♦': 'd', '♣': 'c', '♠': 's'}
    suit = card[0]
    rank = card[1:]
    return f"{suit_map[suit]}{rank}.png"

def get_all_cards():
    suits = ['♥', '♦', '♣', '♠']
    ranks = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
    cards = [s + r for s in suits for r in ranks]
    cards.append('JOKER_RED')
    cards.append('JOKER_BLACK')
    return cards

def generate_card_layout(participants):
    suits = ['♥', '♦', '♣', '♠']
    columns = {suit: [] for suit in suits}

    all_cards = get_all_cards()
    participants_dict = {p.card: p for p in participants}

    card_map = {}
    for card in all_cards:
        card_map[card] = participants_dict.get(card)

        if card.startswith('JOKER'):
            if 'RED' in card:
                columns['♦'].append(card)
            else:
                columns['♠'].append(card)
        else:
            suit = card[0]
            columns[suit].append(card)

    return card_map, columns

def render_index_view(mode='viewer'):
    participants = Participant.query.order_by(Participant.card).all()
    card_map, columns = generate_card_layout(participants)

    has_draft = get_active_draft() is not None
    state = load_match_state()
    has_confirmed = bool(state.get('matches') or state.get('bench'))
    is_match_active = state.get('match_active', False)

    max_rows = max(len(col) for col in columns.values())

    return render_template(
        'index.html',
        participants=participants,
        card_map=card_map,
        columns=columns,
        card_to_filename=card_to_filename,
        has_draft=has_draft,
        has_confirmed=has_confirmed,
        mode=mode,
        is_match_active=is_match_active,
        max_rows=max_rows
    )

@app.route('/')
def root_redirect():
    return redirect(url_for('viewer_index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    used_cards = {p.card for p in Participant.query.all()}
    available_cards = [c for c in ALL_CARDS if c not in used_cards]
    mode = request.args.get('mode', 'viewer')

    if request.method == 'POST':
        mode = request.form.get('mode', 'viewer')

        name = request.form.get("name", "").strip()
        gender = request.form.get("gender", "")
        level = request.form.get("level", "")
        
        card = request.form['card']
        if card not in available_cards:
            return "このカードは既に選ばれています", 400
        
        # 安全対策：空欄チェック
        if not name or gender not in GENDER_WEIGHT or level not in LEVEL_MAP:
            flash("すべての項目を正しく入力してください", "error")
            return redirect(url_for("register", card=card, mode=mode))

        weight = LEVEL_MAP[level] * GENDER_WEIGHT[gender]
        
        p = Participant(
            name=name, gender=gender, level=level, weight=weight, card=card
        )

        db.session.add(p)
        db.session.commit()

        return redirect(url_for('thanks', mode=mode))

    card = request.args.get('card')
    return render_template('register.html', card=card, mode=mode)

@app.route('/qrcode/<user_type>')
def qrcode_image(user_type):
    with open('config.json') as f:
        config = json.load(f)
    url = config.get("paypay_links", {}).get(user_type)
    if not url:
        return "Invalid user type", 400

    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/thanks')
def thanks():
    mode = request.args.get('mode', 'viewer')
    config = load_config()
    paypay_links = config.get("paypay_links", {})
    return render_template('thanks.html', paypay_links=paypay_links, mode=mode)

@app.route('/participant/<card>', methods=['GET', 'POST'])
def participant_view(card):
    mode = request.args.get('mode', 'viewer')
    participant = Participant.query.filter_by(card=card).first()

    if request.method == 'POST' and participant:
        mode = request.form.get('mode', 'viewer')

        participant.name = request.form['name']
        participant.gender = request.form['gender']
        participant.level = request.form['level']
        participant.active = 'active' in request.form  # チェックされてれば True

        db.session.commit()
        if mode == 'admin':
            return redirect(url_for('admin_index'))
        else:
            return redirect(url_for('viewer_index'))

    if participant:
        return render_template('participant_edit.html', participant=participant, mode=mode)
    else:
        return redirect(url_for('register', card=card, mode=mode))

@app.route('/upload', methods=['GET', 'POST'])
def upload_csv():
    used_cards = {p.card for p in Participant.query.all() if p.card}
    available_cards = [c for c in ALL_CARDS if c not in used_cards]

    if request.method == 'POST':
        file = request.files['file']
        if file and file.filename.endswith('.csv'):
            stream = TextIOWrapper(file.stream, encoding='utf-8')
            reader = csv.DictReader(stream)

            for row in reader:
                name = row.get('name')
                gender = row.get('gender')
                level = row.get('level')
                card = row.get('card')

                if not (name and gender and level and card):
                    continue  # 不完全な行はスキップ

                if card not in available_cards:
                    continue  # 使用済みカードはスキップ

                weight = LEVEL_MAP.get(level) * GENDER_WEIGHT.get(gender)
                if weight is None:
                    continue  # 無効な値はスキップ

                p = Participant(
                    name=name,
                    gender=gender,
                    level=level,
                    weight=weight,
                    card=card
                )
                db.session.add(p)
                available_cards.remove(card)

            db.session.commit()
            return redirect(url_for('admin_index'))

    return render_template('upload_csv.html')

@app.route('/download_template')
def download_template():
    return send_from_directory(
        directory='static',
        path='participants_template.csv',
        as_attachment=True
    )

@app.route('/match', methods=['GET', 'POST'])
def match_form():
    state = load_match_state()
    

    mode = request.form.get('mode', 'admin')

    court_count = None
    if request.method == 'POST':
        form_value = request.form.get('court_count')
        if form_value:
            court_count = int(form_value)
        else:
            court_count = get_confirmed_court_count(state)

    if court_count is None:
        # 最初のアクセス or リセット後はフォーム表示
        return render_template('match_form.html', mode=mode)

    participants = Participant.query.all()
    matches, bench = generate_matches(participants, court_count)

    # → IDだけに変換
    match_ids = [[p.id for p in group] for group in matches]
    bench_ids = [p.id for p in bench]

    # draft_state.jsonをIDベースで保存
    save_draft_state(match_ids, bench_ids, court_count=court_count)

    state['match_active'] = True
    save_match_state_full(
        state.get('match_active', True),
        state.get('matches', []),
        state.get('bench', []),
        state.get('match_count', 0),
        court_count=court_count,
    )

    
    return redirect(url_for('edit_matches', mode=mode))

@app.route('/match/edit')
def edit_matches():
    mode = request.args.get('mode', 'admin')
    if mode != 'admin':
        return redirect(url_for('match_draft', mode=mode))

    draft = get_active_draft()

    # 共有中の未確定 draft を表示元の正とする。
    if draft is None:
        return redirect(url_for('match_form'))

    match_ids = draft['matches']
    bench_ids = draft['bench']

    participants = {p.id: p for p in Participant.query.all()}
    
    # ✅ 前回待機者のIDを取得
    previous_bench_ids = set(load_match_state().get("bench", []))

    # ✅ 名前加工関数（元Participantを壊さずコピー）
    def mark_bench_player(p):
        if p.id in previous_bench_ids:
            # SQLAlchemyインスタンスのコピーを作成
            p_copy = p.__class__(**{col.name: getattr(p, col.name) for col in p.__table__.columns})
            p_copy.name = f"*{p.name}"
            return p_copy
        return p

    # 参加者を加工したものに変換
    matches = [[mark_bench_player(participants[pid]) for pid in group] for group in match_ids]
    bench = [mark_bench_player(participants[pid]) for pid in bench_ids]

    court_count = get_draft_court_count(draft)
    match_count = get_match_count()

    # config.jsonの読み込み
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)

    level_map = config["level_map"]
    gender_weight = config["gender_weight"]

    # 各コート内を2人ずつペアにしてスコアをつける
    match_data = []  # 画面表示用
    for group in matches:  # group = [p1, p2, p3, p4]
        pairs = [group[i:i+2] for i in range(0, len(group), 2)]
        scored_pairs = [calculate_pair_score(pair, level_map, gender_weight) for pair in pairs]
        match_data.append(scored_pairs)

    return render_template(
        'match_edit.html',
        matches=matches,
        match_data=match_data,  # 追加
        bench=bench,
        card_to_filename=card_to_filename,
        match_count=match_count,
        court_count=court_count,
        mode=mode
    )

@app.route('/match/swap', methods=['POST'])
def swap_players():
    raw = request.form.get('swap_ids', '')
    selected_ids = raw.split(',') if raw else []
    mode = request.form.get('mode', 'viewer')

    if len(selected_ids) != 2:
        return redirect(url_for('edit_matches', mode=mode))  # 2人以外選ばれてたら無視

    id1, id2 = map(int, selected_ids)

    # 共有中の未確定 draft を正として現在の状態を取得
    draft = get_active_draft()
    if draft is None:
        return redirect(url_for('match_form', mode=mode))

    match_ids = draft['matches']
    bench_ids = draft['bench']

    # 両方をまとめて探索・入れ替え
    all_groups = match_ids + [bench_ids]  # 最後の1枠は bench 扱い

    for group in all_groups:
        for i, pid in enumerate(group):
            if pid == id1:
                group[i] = id2
            elif pid == id2:
                group[i] = id1

    # bench_ids を再構成（マッチに含まれていない人を待機者とみなす）
    used_ids = set(pid for group in match_ids for pid in group)
    all_selected_ids = used_ids.union(set(bench_ids))
    new_bench_ids = [pid for pid in all_selected_ids if pid not in used_ids]

    save_draft_state(
        match_ids,
        new_bench_ids,
        court_count=draft.get('court_count'),
    )

    return redirect(url_for('edit_matches', mode=mode))

def has_valid_draft(matches, bench):
    return (
        isinstance(matches, list)
        and isinstance(bench, list)
        and bool(matches or bench)
    )


@app.route('/match/confirm', methods=['POST'])
def confirm_match():
    # 共有中の未確定 draft を確定対象の正とし、古い session draft は採用しない。
    draft = get_active_draft()
    if draft is None:
        return redirect(url_for('match_form'))

    match_ids = draft['matches']
    bench_ids = draft['bench']

    # 組み合わせ回数カウントアップ
    state = load_match_state()
    match_count = state.get('match_count', 0) + 1

    match_round = MatchRound(round_number=match_count)
    db.session.add(match_round)
    db.session.flush()

    for court_number, group in enumerate(match_ids, start=1):
        db.session.add(MatchHistory(
            round_id=match_round.id,
            court_number=court_number,
            team1_player1_id=group[0],
            team1_player2_id=group[1],
            team2_player1_id=group[2],
            team2_player2_id=group[3],
        ))

    for participant_id in bench_ids:
        db.session.add(BenchHistory(
            round_id=match_round.id,
            participant_id=participant_id,
        ))

    # 対象参加者IDを集める
    confirmed_ids = [pid for group in match_ids for pid in group]

    # DBから該当参加者を取得＆games_playedを+1
    for p in Participant.query.filter(Participant.id.in_(confirmed_ids)).all():
        p.games_played += 1

    # ワーカー切替時のセッション消失問題の調査用ログ（2025/10 対応）
    app.logger.debug(f"[confirm_match] Saving match_state_full: matches={match_ids}, bench={bench_ids}, count={match_count}")

    try:
        # 確定状態をファイル保存
        save_match_state_full(True, match_ids, bench_ids, match_count, court_count=draft.get('court_count'))

        # 確定済み state だけを表示の正とするため、未確定 draft を削除する
        clear_draft_state()

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    mode = request.form.get('mode', 'viewer')
    return redirect(url_for('match_result', mode=mode))


@app.route('/match/revert_to_draft', methods=['POST'])
def revert_match_to_draft():
    mode = request.form.get('mode', request.args.get('mode', 'admin'))
    if mode == 'viewer':
        return redirect(url_for('match_result', mode='viewer'))

    state = load_match_state()
    match_ids = state.get('matches', [])
    bench_ids = state.get('bench', [])
    if not (match_ids or bench_ids):
        flash('確定済み組み合わせがありません')
        return redirect(url_for('match_result', mode='admin'))

    save_draft_state(
        match_ids,
        bench_ids,
        court_count=state.get('court_count'),
    )

    confirmed_ids = {pid for group in match_ids for pid in group}
    if confirmed_ids:
        for participant in Participant.query.filter(Participant.id.in_(confirmed_ids)).all():
            participant.games_played = max((participant.games_played or 0) - 1, 0)

    current_match_count = state.get('match_count', 0)
    match_round = (
        MatchRound.query
        .filter_by(round_number=current_match_count)
        .order_by(MatchRound.id.desc())
        .first()
    )

    if match_round is not None:
        BenchHistory.query.filter_by(round_id=match_round.id).delete(synchronize_session=False)
        MatchHistory.query.filter_by(round_id=match_round.id).delete(synchronize_session=False)
        db.session.delete(match_round)

    db.session.commit()

    match_count = max(current_match_count - 1, 0)
    save_match_state_full(
        False,
        [],
        [],
        match_count,
        court_count=state.get('court_count'),
    )

    return redirect(url_for('edit_matches', mode='admin'))


@app.route('/update_court_count', methods=['POST'])
def update_court_count():
    new_count = int(request.form['court_count'])

    # 参加者データ取得
    participants = Participant.query.filter_by(active=True).all()

    # 新しい組み合わせ生成
    matches, bench = generate_matches(participants, new_count)
    match_ids = [[p.id for p in group] for group in matches]
    bench_ids = [p.id for p in bench]
    save_draft_state(match_ids, bench_ids, court_count=new_count)

    mode = request.form.get('mode', 'viewer')

    return redirect(url_for('edit_matches', mode=mode))

def render_match_result_page(match_ids, bench_ids, match_count, mode, *, is_draft, has_draft, has_confirmed):
    participants = {p.id: p for p in Participant.query.all()}

    matches = [[participants[pid] for pid in group] for group in match_ids]
    bench = [participants[pid] for pid in bench_ids] if bench_ids else []

    return render_template(
        'match_result.html',
        matches=matches,
        bench=bench,
        card_to_filename=card_to_filename,
        match_count=match_count,
        mode=mode,
        is_draft=is_draft,
        has_draft=has_draft,
        has_confirmed=has_confirmed,
    )


@app.route('/match/result')
def match_result():
    mode = request.args.get('mode', 'viewer')
    state = load_match_state()
    draft = get_active_draft()
    match_ids = state.get('matches', [])
    bench_ids = state.get('bench', [])
    has_confirmed = bool(match_ids or bench_ids)

    return render_match_result_page(
        match_ids,
        bench_ids,
        state.get('match_count', 0),
        mode,
        is_draft=False,
        has_draft=draft is not None,
        has_confirmed=has_confirmed,
    )


@app.route('/match/draft')
def match_draft():
    mode = request.args.get('mode', 'viewer')
    draft = get_active_draft()
    if draft is None:
        return redirect(url_for('match_result', mode=mode))

    state = load_match_state()
    match_ids = draft.get('matches', [])
    bench_ids = draft.get('bench', [])
    confirmed_match_ids = state.get('matches', [])
    confirmed_bench_ids = state.get('bench', [])

    return render_match_result_page(
        match_ids,
        bench_ids,
        state.get('match_count', 0) + 1,
        mode,
        is_draft=True,
        has_draft=True,
        has_confirmed=bool(confirmed_match_ids or confirmed_bench_ids),
    )


@app.route('/match_result')
def legacy_match_result():
    mode = request.args.get('mode', 'viewer')
    return redirect(url_for('match_result', mode=mode))

@app.route('/reset_match', methods=['POST'])
def reset_match():
    reset_match_state()
    flash('試合状態をリセットしました')
    return redirect(url_for('match_form'))

def parse_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@app.route('/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    #if request.args.get('key') != 'supersecret':
    #    abort(403)  # Forbidden
    current_config = load_config()
    if request.method == 'POST':
        # configの保存処理
        config = {
            "paypay_links": {
                "adults": request.form.get('paypay_adults'),
                "students": request.form.get('paypay_students')
            },
            "level_map": {
                "beginner": parse_positive_int(request.form.get('level_beginner'), current_config["level_map"].get("beginner", 1)),
                "intermediate": parse_positive_int(request.form.get('level_intermediate'), current_config["level_map"].get("intermediate", 2)),
                "advanced": parse_positive_int(request.form.get('level_advanced'), current_config["level_map"].get("advanced", 3))
            },
            "gender_weight": {
                "male": parse_float(request.form.get('weight_male'), current_config["gender_weight"].get("male", 1.0)),
                "female": parse_float(request.form.get('weight_female'), current_config["gender_weight"].get("female", 0.9))
            },
            "score_input_mode": normalize_score_input_mode(request.form.get('score_input_mode')),
            "scoring_system": normalize_scoring_system({
                "points_per_game": request.form.get('points_per_game'),
                "games_per_match": request.form.get('games_per_match'),
                "deuce_enabled": request.form.get('deuce_enabled'),
                "max_points": request.form.get('max_points'),
            }),
        }
        with open('config.json', 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        flash('設定を保存しました')
        return redirect(url_for('admin_settings'))

    return render_template('admin_settings.html', config=current_config)

@app.route('/admin/reset_db', methods=['POST'])
def reset_db():
    # 先にマッチ状態をリセット
    reset_match_state()
    db.create_all()
    # その後で履歴と参加者データをすべて削除
    BenchHistory.query.delete()
    MatchHistory.query.delete()
    MatchRound.query.delete()
    Participant.query.delete()
    db.session.commit()
    flash('参加者データと試合情報をすべて削除しました')
    return redirect(url_for('admin_settings'))


def format_participant_label(participant):
    if participant is None:
        return "[]不明な参加者"

    card = participant.card or ""
    if card in ("JOKER_RED", "JOKER_BLACK"):
        card = "JK"

    return f"[{card}]{participant.name}"


def get_participant_label_map(rounds):
    participant_ids = set()
    for match_round in rounds:
        for match in match_round.matches:
            participant_ids.update([
                match.team1_player1_id,
                match.team1_player2_id,
                match.team2_player1_id,
                match.team2_player2_id,
            ])
        for bench_history in match_round.bench_players:
            participant_ids.add(bench_history.participant_id)

    if not participant_ids:
        return {}

    participants = Participant.query.filter(Participant.id.in_(participant_ids)).all()
    return {participant.id: format_participant_label(participant) for participant in participants}




def parse_optional_int(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_winner_team(value):
    winner = parse_optional_int(value)
    return winner if winner in (1, 2) else None


GAME_SCORE_RE = re.compile(r"^(\d+)\s*-\s*(\d+)$")


def decide_game_winner(left_score, right_score, scoring_system):
    points_per_game = scoring_system["points_per_game"]
    max_points = scoring_system["max_points"]
    if left_score > max_points or right_score > max_points:
        return None
    if left_score == right_score:
        return None

    high_score = max(left_score, right_score)
    low_score = min(left_score, right_score)
    if not scoring_system["deuce_enabled"]:
        if high_score != points_per_game:
            return None
    elif high_score < points_per_game:
        return None
    elif high_score != max_points and high_score - low_score < 2:
        return None

    return 1 if left_score > right_score else 2



def parse_score_text_rows(score_text, games_per_match):
    rows = []
    for raw_line in (score_text or '').splitlines():
        line = raw_line.strip()
        if line == '':
            continue
        match = GAME_SCORE_RE.match(line)
        if match is None:
            continue
        rows.append({
            "team1": match.group(1),
            "team2": match.group(2),
        })
        if len(rows) >= games_per_match:
            break
    while len(rows) < games_per_match:
        rows.append({"team1": "", "team2": ""})
    return rows


def build_score_text_from_dropdowns(form, games_per_match):
    dropdown_field_names = {
        f"game{game_number}_{team}_score"
        for game_number in range(1, games_per_match + 1)
        for team in ("team1", "team2")
    }
    if not dropdown_field_names.intersection(form.keys()) and "score_text" in form:
        return True, form.get("score_text"), None

    lines = []
    for game_number in range(1, games_per_match + 1):
        team1_value = (form.get(f"game{game_number}_team1_score") or '').strip()
        team2_value = (form.get(f"game{game_number}_team2_score") or '').strip()
        if team1_value == '' and team2_value == '':
            continue
        if team1_value == '' or team2_value == '':
            return False, None, '片方だけ未入力のゲームがあります'
        lines.append(f"{team1_value}-{team2_value}")
    return True, "\n".join(lines), None

def parse_score_text(score_text, scoring_system):
    normalized_text = (score_text or "").strip()
    if normalized_text == "":
        return True, None, None, None, None

    team1_games = 0
    team2_games = 0
    effective_lines = []
    for raw_line in score_text.splitlines():
        line = raw_line.strip()
        if line == "":
            continue
        match = GAME_SCORE_RE.match(line)
        if match is None:
            return False, None, None, None, f'不正なスコア行があります: {line}'
        left_score, right_score = int(match.group(1)), int(match.group(2))
        winner = decide_game_winner(left_score, right_score, scoring_system)
        if winner is None:
            return False, None, None, None, f'勝敗を判定できないスコア行があります: {line}'
        if winner == 1:
            team1_games += 1
        else:
            team2_games += 1
        effective_lines.append(f"{left_score}-{right_score}")

    if not effective_lines:
        return True, None, None, None, None
    if len(effective_lines) > scoring_system["games_per_match"]:
        return False, None, None, None, f'{scoring_system["games_per_match"]}ゲーム以内で入力してください'

    winner_team = None
    if team1_games > team2_games:
        winner_team = 1
    elif team2_games > team1_games:
        winner_team = 2
    return True, "\n".join(effective_lines), team1_games, team2_games, winner_team


@app.route('/admin/match_history/<int:match_history_id>/score', methods=['POST'])
def update_match_history_score(match_history_id):
    match_history = db.session.get(MatchHistory, match_history_id)
    if match_history is None:
        flash('指定された試合履歴が見つかりません')
        return redirect(url_for('admin_match_history'))

    config = load_config()
    score_input_mode = config["score_input_mode"]

    if score_input_mode == "score":
        dropdown_valid, posted_score_text, dropdown_error = build_score_text_from_dropdowns(
            request.form,
            config["scoring_system"]["games_per_match"],
        )
        if not dropdown_valid:
            flash(dropdown_error or 'ゲーム別スコアを正しく入力してください')
            return redirect(url_for('admin_match_history'))
        valid, score_text, team1_score, team2_score, winner_team_or_error = parse_score_text(
            posted_score_text,
            config["scoring_system"],
        )
        if not valid:
            flash(winner_team_or_error or 'ゲーム別スコアを正しく入力してください')
            return redirect(url_for('admin_match_history'))
        winner_team = winner_team_or_error
        match_history.score_text = score_text
        match_history.team1_score = team1_score
        match_history.team2_score = team2_score
        match_history.winner_team = winner_team
    else:
        match_history.winner_team = parse_winner_team(request.form.get('winner_team'))

    db.session.commit()
    flash('試合結果を保存しました')
    return redirect(url_for('admin_match_history'))

@app.route('/admin/match_history')
def admin_match_history():
    rounds = (
        MatchRound.query
        .options(
            selectinload(MatchRound.matches),
            selectinload(MatchRound.bench_players),
        )
        .order_by(MatchRound.created_at.desc(), MatchRound.id.desc())
        .all()
    )
    participant_labels = get_participant_label_map(rounds)

    config = load_config()
    scoring_system = config["scoring_system"]
    return render_template(
        'match_history.html',
        rounds=rounds,
        participant_labels=participant_labels,
        score_input_mode=config["score_input_mode"],
        scoring_system=scoring_system,
        score_options=list(range(scoring_system["max_points"] + 1)),
        score_rows_by_match_id={
            match.id: parse_score_text_rows(match.score_text, scoring_system["games_per_match"])
            for match_round in rounds
            for match in match_round.matches
        },
    )

# 管理者向けトップページ
@app.route('/admin')
def admin_index():
    return render_index_view(mode='admin')

# 参加者向けビュー
@app.route('/viewer')
def viewer_index():
    return render_index_view(mode='viewer')

if __name__ == '__main__':
    ensure_database_tables()
    app.run(debug=True, host='0.0.0.0', port=5001)
