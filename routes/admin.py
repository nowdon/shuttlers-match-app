import csv
import os
from io import TextIOWrapper

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from data.participants import (
    get_all_participants,
)

from routes.helpers import (
    ALL_CARDS,
    GENDER_WEIGHT,
    LEVEL_MAP,
    clear_all_data_records,
    dump_match_history_to_json,
    parse_float,
    render_index_view,
    send_history_dump_email_if_enabled,
)
from models import Participant, db
from utils.config import (
    load_config,
    normalize_consecutive_play_limit,
    normalize_score_input_mode,
    normalize_scoring_system,
    parse_bool,
    parse_positive_int,
    save_config,
)
from utils.reset import clear_match_runtime_state


admin_bp = Blueprint("admin", __name__)


@admin_bp.route('/upload', methods=['GET', 'POST'])
def upload_csv():
    used_cards = {p.card for p in get_all_participants() if p.card}
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
            return redirect(url_for('admin.admin_index'))

    return render_template('upload_csv.html')


@admin_bp.route('/download_template')
def download_template():
    return send_from_directory(
        directory='static',
        path='participants_template.csv',
        as_attachment=True
    )


@admin_bp.route('/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    current_config = load_config()
    if request.method == 'POST':
        # configの保存処理
        config = dict(current_config)
        history_dump_email_enabled = parse_bool(request.form.get('history_dump_email_enabled'))
        history_dump_email_recipient = (request.form.get('history_dump_email_recipient') or '').strip()
        if history_dump_email_enabled and not history_dump_email_recipient:
            flash('履歴ダンプのメール送信を有効にする場合は、送信先メールアドレスを入力してください')
            return render_template('admin_settings.html', config=current_config)

        config.update({
            "paypay_links": {
                "adults": request.form.get('paypay_adults'),
                "students": request.form.get('paypay_students')
            },
            "paypay_link_expirations": {
                "adults": request.form.get('paypay_expiration_adults') or "",
                "students": request.form.get('paypay_expiration_students') or "",
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
            "consecutive_play_limit": normalize_consecutive_play_limit(
                request.form.get('consecutive_play_limit')
            ),
            "scoring_system": normalize_scoring_system({
                "points_per_game": request.form.get('points_per_game'),
                "games_per_match": request.form.get('games_per_match'),
                "deuce_enabled": request.form.get('deuce_enabled'),
                "max_points": request.form.get('max_points'),
            }),
            "history_dump_email": {
                "enabled": history_dump_email_enabled,
                "recipient": history_dump_email_recipient,
            },
        })
        save_config(config)
        flash('設定を保存しました')
        return redirect(url_for('admin.admin_settings'))

    return render_template('admin_settings.html', config=current_config)


@admin_bp.route('/admin/reset_db', methods=['POST'])
def reset_db():
    dump_path = None
    dump_error = None
    email_sent = None
    state_reset_error = None

    try:
        dump_path = dump_match_history_to_json('clear_all_data')
    except Exception as exc:
        dump_error = exc
        current_app.logger.exception('Failed to dump match history before clearing all data')

    try:
        db.create_all()
        clear_all_data_records()
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Failed to clear all data')
        flash('参加者データと試合情報の削除に失敗しました')
        return redirect(url_for('admin.admin_settings'))

    try:
        clear_match_runtime_state()
    except Exception as exc:
        state_reset_error = exc
        current_app.logger.exception('Failed to clear match runtime state files after clearing all data')

    if dump_path is not None:
        email_sent = send_history_dump_email_if_enabled(dump_path)

    warnings = []
    if dump_error is not None:
        warnings.append('試合履歴のJSON保存に失敗しました')
    if state_reset_error is not None:
        warnings.append('試合状態ファイルの初期化に失敗しました')
    if email_sent is False:
        warnings.append('メール送信に失敗しました')

    if warnings:
        flash(f'参加者データと試合情報を削除しましたが、{"、".join(warnings)}')
    elif dump_path is not None:
        flash(f'参加者データと試合情報をすべて削除しました: {os.path.basename(dump_path)}')
    else:
        flash('参加者データと試合情報をすべて削除しました')
    return redirect(url_for('admin.admin_settings'))


@admin_bp.route('/admin')
def admin_index():
    return render_index_view(mode='admin')
