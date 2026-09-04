from routes.legacy_blueprint import LegacyEndpointBlueprint


history_bp = LegacyEndpointBlueprint("history", __name__, dependency_module="app")


@history_bp.route('/admin/match_history/round/<int:round_id>/score', methods=['POST'])
def update_match_history_round_score(round_id):
    match_round = (
        MatchRound.query
        .options(selectinload(MatchRound.matches))
        .filter_by(id=round_id)
        .first()
    )
    if match_round is None:
        flash('指定された試合ラウンドが見つかりません')
        return redirect(url_for('admin_match_history'))

    updated, error_message = apply_round_score_updates(match_round)
    if not updated:
        flash(error_message)
        return redirect(url_for('admin_match_history'))

    flash('ラウンドの試合結果を保存しました')
    return redirect(url_for('admin_match_history'))


@history_bp.route('/match/result/round/<int:round_id>/score', methods=['POST'])
def update_match_result_round_score(round_id):
    mode = request.form.get('mode', request.args.get('mode', 'admin'))
    if mode != 'admin':
        return redirect(url_for('match_result', mode='viewer'))

    match_round = (
        MatchRound.query
        .options(selectinload(MatchRound.matches))
        .filter_by(id=round_id)
        .first()
    )
    if match_round is None:
        flash('指定された試合ラウンドが見つかりません')
        return redirect(url_for('match_result', mode='admin'))

    updated, error_message = apply_round_score_updates(match_round)
    if not updated:
        flash(error_message)
        return redirect(url_for('match_result', mode='admin'))

    flash('ラウンドの試合結果を保存しました')
    return redirect(url_for('match_result', mode='admin'))


@history_bp.route('/admin/match_history/<int:match_history_id>/score', methods=['POST'])
def update_match_history_score(match_history_id):
    match_history = db.session.get(MatchHistory, match_history_id)
    if match_history is None:
        flash('指定された試合履歴が見つかりません')
        return redirect(url_for('admin_match_history'))

    updated, error_message = apply_match_history_score_update(match_history, request.form)
    if not updated:
        flash(error_message)
        return redirect(url_for('admin_match_history'))

    db.session.commit()
    flash('試合結果を保存しました')
    return redirect(url_for('admin_match_history'))


@history_bp.route('/match/result/<int:match_history_id>/score', methods=['POST'])
def update_match_result_score(match_history_id):
    mode = request.form.get('mode', request.args.get('mode', 'admin'))
    if mode != 'admin':
        return redirect(url_for('match_result', mode='viewer'))

    match_history = db.session.get(MatchHistory, match_history_id)
    if match_history is None:
        flash('指定された試合履歴が見つかりません')
        return redirect(url_for('match_result', mode='admin'))

    updated, error_message = apply_match_history_score_update(match_history, request.form)
    if not updated:
        flash(error_message)
        return redirect(url_for('match_result', mode='admin'))

    db.session.commit()
    flash('試合結果を保存しました')
    return redirect(url_for('match_result', mode='admin'))


@history_bp.route('/admin/match_history/dump', methods=['POST'])
def dump_match_history():
    try:
        dump_path = dump_match_history_to_json('manual_dump')
    except OSError:
        app.logger.exception('Failed to dump match history')
        flash('試合履歴のJSON保存に失敗しました')
        return redirect(url_for('admin_match_history'))

    if not send_history_dump_email_if_enabled(dump_path):
        flash(f'試合履歴をJSONに保存しましたが、メール送信に失敗しました: {os.path.basename(dump_path)}')
        return redirect(url_for('admin_match_history'))

    flash(f'試合履歴をJSONに保存しました: {os.path.basename(dump_path)}')
    return redirect(url_for('admin_match_history'))


@history_bp.route('/admin/match_history/dump_and_clear', methods=['POST'])
def dump_and_clear_match_history():
    dump_path = None
    dump_error = None
    email_sent = None

    try:
        dump_path = dump_match_history_to_json('manual_dump_and_clear')
    except Exception as exc:
        dump_error = exc
        app.logger.exception('Failed to dump match history before clearing')

    try:
        clear_match_history_records()
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception('Failed to clear match history after dumping')
        flash('試合履歴の消去に失敗しました。DB上の履歴は保持されています')
        return redirect(url_for('admin_match_history'))

    if dump_path is not None:
        email_sent = send_history_dump_email_if_enabled(dump_path)

    if dump_error is not None:
        flash('DB上の試合履歴を消去しましたが、試合履歴のJSON保存に失敗しました')
    elif email_sent is False:
        flash(f'DB上の試合履歴を消去しました。JSONは保存しましたが、メール送信に失敗しました: {os.path.basename(dump_path)}')
    elif dump_path is not None:
        flash(f'試合履歴をJSONに保存してからDB上の履歴を消去しました: {os.path.basename(dump_path)}')
    else:
        flash('DB上の試合履歴を消去しました')
    return redirect(url_for('admin_match_history'))


@history_bp.route('/admin/match_history')
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


@history_bp.route('/admin/match_history_archives')
def admin_match_history_archives():
    selected_filename = request.args.get('file')
    if selected_filename:
        return redirect(url_for('admin_match_history_archive_detail', filename=selected_filename))

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
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        app.logger.exception('Failed to read match history archive JSON')
        archive_error = '読み込みエラー'

    return render_template(
        'match_history_archives.html',
        archives=list_match_history_archives(),
        selected_filename=filename,
        selected_archive=selected_archive,
        archive_error=archive_error,
    )
