import csv
import os
import json
import io
from io import TextIOWrapper
from flask import Flask, render_template, request, redirect, url_for, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask import flash
from flask import Response
from flask import send_from_directory
from flask import send_file
import qrcode
from logic import generate_matches
from itertools import zip_longest

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///participants.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

ALL_CARDS = [
    f"{suit}{rank}"
    for suit in ['♥', '♦', '♣', '♠']
    for rank in ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
] + ['JOKER_RED', 'JOKER_BLACK']

class Participant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    level = db.Column(db.String(20), nullable=False)
    weight = db.Column(db.Float, nullable=False)
    games_played = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)
    card = db.Column(db.String(10), unique=True, nullable=False)

def load_config():
    with open('config.json', 'r', encoding='utf-8') as f:
        return json.load(f)
    
config = load_config()
LEVEL_MAP = config.get("level_map", {})
GENDER_WEIGHT = config.get("gender_weight", {})

def get_match_count():
    count = session.get('match_count', 0)
    if 'draft_matches' in session:
        return count + 1  # 仮組み合わせ分も加算
    return count

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

    has_draft = 'draft_matches' in session
    has_confirmed = 'last_confirmed_matches' in session

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

    mode = request.form.get('mode', 'admin')

    if request.method == 'POST':
        # 値が送られてきたときだけcourt_countを更新
        form_value = request.form.get('court_count')
        if form_value:
            session['court_count'] = int(form_value)
        
    court_count = session.get('court_count')

    if court_count is None:
        # 最初のアクセス or リセット後はフォーム表示
        return render_template('match_form.html', mode=mode)

    participants = Participant.query.all()
    matches, bench = generate_matches(participants, court_count)
    # 試合回数はまだ更新しない
    # マッチ情報をセッションに一時保存
    session['draft_matches'] = [[p.id for p in group] for group in matches]
    session['draft_bench'] = [p.id for p in bench]

    return redirect(url_for('edit_matches', mode=mode))

@app.route('/match/edit')
def edit_matches():
    # セッションから仮組み合わせを取得
    match_ids = session.get('draft_matches', [])
    bench_ids = session.get('draft_bench', [])

    participants = {p.id: p for p in Participant.query.all()}

    matches = [[participants[pid] for pid in group] for group in match_ids]
    bench = [participants[pid] for pid in bench_ids]
    
    court_count = session.get('court_count', 1)
    match_count = get_match_count()

    mode = request.args.get('mode', 'viewer')

    return render_template(
        'match_edit.html',
        matches=matches,
        bench=bench,
        card_to_filename=card_to_filename,
        match_count=match_count,
        court_count=court_count,
        mode=mode  # ← これを渡す！
    )

@app.route('/match/swap', methods=['POST'])
def swap_players():
    raw = request.form.get('swap_ids', '')
    selected_ids = raw.split(',') if raw else []

    if len(selected_ids) != 2:
        return redirect(url_for('edit_matches'))  # 2人以外選ばれてたら無視

    id1, id2 = map(int, selected_ids)

    # 現在の状態を取得
    match_ids = session.get('draft_matches', [])
    bench_ids = session.get('draft_bench', [])

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

    session['draft_matches'] = match_ids
    session['draft_bench'] = new_bench_ids

    mode = request.form.get('mode', 'viewer')

    return redirect(url_for('edit_matches', mode=mode))

@app.route('/match/confirm', methods=['POST'])
def confirm_match():
    match_ids = session.get('draft_matches', [])
    bench_ids = session.get('draft_bench', [])

    # 対象参加者IDを集める
    confirmed_ids = [pid for group in match_ids for pid in group]

    # DBから該当参加者を取得＆games_playedを+1
    for p in Participant.query.filter(Participant.id.in_(confirmed_ids)).all():
        p.games_played += 1

    db.session.commit()

    # 今回の確定結果を保存
    session['last_confirmed_matches'] = match_ids
    session['last_confirmed_bench'] = bench_ids

    # セッションから一次保存を削除
    session.pop('draft_matches', None)
    session.pop('draft_bench', None)

    # 確定済みの組み合わせを表示用に保存（match_resultで使用）
    session['last_confirmed_matches'] = match_ids
    session['last_confirmed_bench'] = bench_ids

    # 組み合わせ回数カウントアップ
    session['match_count'] = session.get('match_count', 0) + 1

    mode = request.form.get('mode', 'viewer')

    return redirect(url_for('match_result', mode=mode))

@app.route('/update_court_count', methods=['POST'])
def update_court_count():
    new_count = int(request.form['court_count'])
    session['court_count'] = new_count

    # 参加者データ取得
    participants = Participant.query.filter_by(active=True).all()

    # 新しい組み合わせ生成
    matches, bench = generate_matches(participants, new_count)
    session['draft_matches'] = [[p.id for p in group] for group in matches]
    session['draft_bench'] = [p.id for p in bench]

    mode = request.form.get('mode', 'viewer')

    return redirect(url_for('edit_matches', mode=mode))

@app.route('/match_result')
def match_result():
    match_ids = session.get('last_confirmed_matches')
    bench_ids = session.get('last_confirmed_bench', [])  # ← benchもセッションに保存しておく

    if not match_ids:
        return redirect(url_for('viewer_index'))

    participants = {p.id: p for p in Participant.query.all()}
    matches = [[participants[pid] for pid in group] for group in match_ids]
    bench = [participants[pid] for pid in bench_ids]

    match_count = get_match_count()
    mode = request.args.get('mode', 'viewer')  # デフォルトは viewer

    return render_template(
        'match_result.html',
        matches=matches,
        bench=bench,
        card_to_filename=card_to_filename,
        match_count=match_count,
        mode=mode
    )

@app.route('/reset_match', methods=['POST'])
def reset_match():
    # セッション情報のリセット
    session.pop('court_count', None)
    session.pop('draft_matches', None)
    session.pop('draft_bench', None)
    session.pop('last_confirmed_matches', None)
    session.pop('last_confirmed_bench', None)
    session.pop('match_count', None)
    session.pop('court_count', None)

    # 参加者の試合回数リセット
    participants = Participant.query.all()
    for p in participants:
        p.games_played = 0
    db.session.commit()

    return redirect(url_for('match_form'))

@app.route('/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    #if request.args.get('key') != 'supersecret':
    #    abort(403)  # Forbidden
    if request.method == 'POST':
        # configの保存処理
        config = {
            "paypay_links": {
                "adults": request.form.get('paypay_adults'),
                "students": request.form.get('paypay_students')
            },
            "level_map": {
                "beginner": int(request.form.get('level_beginner')),
                "intermediate": int(request.form.get('level_intermediate')),
                "advanced": int(request.form.get('level_advanced'))
            },
            "gender_weight": {
                "male": float(request.form.get('weight_male')),
                "female": float(request.form.get('weight_female'))
            }
        }
        with open('config.json', 'w') as f:
            json.dump(config, f, indent=4)
        flash('設定を保存しました')
        return redirect(url_for('admin_settings'))

    # GET時: 現在のconfigを読み込み
    with open('config.json') as f:
        config = json.load(f)
    return render_template('admin_settings.html', config=config)

@app.route('/admin/reset_db', methods=['POST'])
def reset_db():
    Participant.query.delete()
    db.session.commit()
    flash('参加者データをすべて削除しました')
    return redirect(url_for('admin_settings'))

# 管理者向けトップページ
@app.route('/admin')
def admin_index():
    return render_index_view(mode='admin')

# 参加者向けビュー
@app.route('/viewer')
def viewer_index():
    return render_index_view(mode='viewer')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=5001)