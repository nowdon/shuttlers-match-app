import json

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from data.match_history import (
    update_match_score,
    get_match_history_by_id,
    get_match_round_with_matches,
    get_match_rounds_with_details,
)

from routes.helpers import (
    apply_match_history_score_update,
    apply_round_score_updates,
    clear_match_history_records,
    dump_match_history_to_json,
    get_participant_label_map,
    list_match_history_archives,
    load_match_history_archive,
    parse_score_text_rows,
    send_history_dump_email_if_enabled,
)
from models import db
from utils.config import load_config
from storage.history_archives import HistoryArchiveStorageError


history_bp = Blueprint("history", __name__)


@history_bp.route('/admin/match_history/round/<int:round_id>/score', methods=['POST'])
def update_match_history_round_score(round_id):
    match_round = get_match_round_with_matches(round_id)
    if match_round is None:
        flash('指定された試合ラウンドが見つかりません')
        return redirect(url_for('history.admin_match_history'))

    updated, error_message = apply_round_score_updates(match_round)
    if not updated:
        flash(error_message)
        return redirect(url_for('history.admin_match_history'))

    flash('ラウンドの試合結果を保存しました')
    return redirect(url_for('history.admin_match_history'))


@history_bp.route('/match/result/round/<int:round_id>/score', methods=['POST'])
def update_match_result_round_score(round_id):
    mode = request.form.get('mode', request.args.get('mode', 'admin'))
    if mode != 'admin':
        return redirect(url_for('match.match_result', mode='viewer'))

    match_round = get_match_round_with_matches(round_id)
    if match_round is None:
        flash('指定された試合ラウンドが見つかりません')
        return redirect(url_for('match.match_result', mode='admin'))

    updated, error_message = apply_round_score_updates(match_round)
    if not updated:
        flash(error_message)
        return redirect(url_for('match.match_result', mode='admin'))

    flash('ラウンドの試合結果を保存しました')
    return redirect(url_for('match.match_result', mode='admin'))


@history_bp.route('/admin/match_history/<int:match_history_id>/score', methods=['POST'])
def update_match_history_score(match_history_id):
    match_history = get_match_history_by_id(match_history_id)
    if match_history is None:
        flash('指定された試合履歴が見つかりません')
        return redirect(url_for('history.admin_match_history'))

    updated, error_message, score_update = apply_match_history_score_update(match_history, request.form)
    if not updated:
        flash(error_message)
        return redirect(url_for('history.admin_match_history'))

    update_match_score(score_update)
    db.session.expire_all()
    flash('試合結果を保存しました')
    return redirect(url_for('history.admin_match_history'))


@history_bp.route('/match/result/<int:match_history_id>/score', methods=['POST'])
def update_match_result_score(match_history_id):
    mode = request.form.get('mode', request.args.get('mode', 'admin'))
    if mode != 'admin':
        return redirect(url_for('match.match_result', mode='viewer'))

    match_history = get_match_history_by_id(match_history_id)
    if match_history is None:
        flash('指定された試合履歴が見つかりません')
        return redirect(url_for('match.match_result', mode='admin'))

    updated, error_message, score_update = apply_match_history_score_update(match_history, request.form)
    if not updated:
        flash(error_message)
        return redirect(url_for('match.match_result', mode='admin'))

    update_match_score(score_update)
    db.session.expire_all()
    flash('試合結果を保存しました')
    return redirect(url_for('match.match_result', mode='admin'))


@history_bp.route('/admin/match_history/dump', methods=['POST'])
def dump_match_history():
    try:
        archive = dump_match_history_to_json('manual_dump')
    except HistoryArchiveStorageError:
        current_app.logger.exception('Failed to dump match history')
        flash('試合履歴のJSON保存に失敗しました')
        return redirect(url_for('history.admin_match_history'))

    if not send_history_dump_email_if_enabled(archive):
        flash(f'試合履歴をJSONに保存しましたが、メール送信に失敗しました: {archive.filename}')
        return redirect(url_for('history.admin_match_history'))

    flash(f'試合履歴をJSONに保存しました: {archive.filename}')
    return redirect(url_for('history.admin_match_history'))


@history_bp.route('/admin/match_history/dump_and_clear', methods=['POST'])
def dump_and_clear_match_history():
    archive = None
    dump_error = None
    email_sent = None

    try:
        archive = dump_match_history_to_json('manual_dump_and_clear')
    except Exception as exc:
        dump_error = exc
        current_app.logger.exception('Failed to dump match history before clearing')

    try:
        clear_match_history_records()
        db.session.expire_all()
    except Exception:
        current_app.logger.exception('Failed to clear match history after dumping')
        flash('試合履歴の消去に失敗しました。DB上の履歴は保持されています')
        return redirect(url_for('history.admin_match_history'))

    if archive is not None:
        email_sent = send_history_dump_email_if_enabled(archive)

    if dump_error is not None:
        flash('DB上の試合履歴を消去しましたが、試合履歴のJSON保存に失敗しました')
    elif email_sent is False:
        flash(f'DB上の試合履歴を消去しました。JSONは保存しましたが、メール送信に失敗しました: {archive.filename}')
    elif archive is not None:
        flash(f'試合履歴をJSONに保存してからDB上の履歴を消去しました: {archive.filename}')
    else:
        flash('DB上の試合履歴を消去しました')
    return redirect(url_for('history.admin_match_history'))


@history_bp.route('/admin/match_history')
def admin_match_history():
    rounds = get_match_rounds_with_details()
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


@history_bp.route('/admin/match_history_archives')
def admin_match_history_archives():
    selected_filename = request.args.get('file')
    if selected_filename:
        return redirect(url_for('history.admin_match_history_archive_detail', filename=selected_filename))

    return render_template(
        'match_history_archives.html',
        archives=list_match_history_archives(),
        selected_filename=None,
        selected_archive=None,
        archive_error=None,
    )


@history_bp.route('/admin/match_history_archives/<path:filename>')
def admin_match_history_archive_detail(filename):
    selected_archive = None
    archive_error = None
    try:
        selected_archive = load_match_history_archive(filename)
    except (HistoryArchiveStorageError, json.JSONDecodeError, UnicodeDecodeError):
        current_app.logger.exception('Failed to read match history archive JSON')
        archive_error = '読み込みエラー'

    return render_template(
        'match_history_archives.html',
        archives=list_match_history_archives(),
        selected_filename=filename,
        selected_archive=selected_archive,
        archive_error=archive_error,
    )
