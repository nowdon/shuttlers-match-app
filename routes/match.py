from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from data.match_history import (
    get_latest_match_round,
)
from data.participants import (
    get_active_participants,
    get_all_participants,
    get_participants_by_ids,
)

from routes.helpers import (
    get_confirmed_court_count,
    get_draft_court_count,
    get_match_count,
    card_to_filename,
    render_match_result_page,
    same_current_pair,
    send_match_confirmed_line_notifications,
    swap_pair_positions,
)
from logic import generate_matches
from models import BenchHistory, MatchHistory, MatchRound, Participant, db, utc_now
from utils.config import load_raw_config
from utils.draft_state import clear_draft_state, get_active_draft, save_draft_state
from utils.match_session import ensure_current_match_session
from utils.match_state import load_match_state, save_match_state_full
from utils.pair_optimizer import (
    INVALID_DRAFT_MESSAGE,
    get_fixed_pair_for_player,
    normalize_fixed_pairs,
    optimize_draft_pairs,
    split_editable_draft_matches_and_bench,
    validate_editable_draft,
    validate_fixed_pairs,
)
from utils.reset import reset_match_state
from utils.score import calculate_pair_score
from utils.stats import calculate_participant_win_stats


match_bp = Blueprint("match", __name__)


@match_bp.route('/match', methods=['GET', 'POST'])
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

    ensure_current_match_session()
    state = load_match_state()

    participants = get_all_participants()
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


    return redirect(url_for('match.edit_matches', mode=mode))


@match_bp.route('/match/edit')
def edit_matches():
    mode = request.args.get('mode', 'admin')
    if mode != 'admin':
        return redirect(url_for('match.match_draft', mode=mode))

    draft = get_active_draft()

    # 共有中の未確定 draft を表示元の正とする。
    if draft is None:
        return redirect(url_for('match.match_form'))

    participants = {p.id: p for p in get_all_participants()}
    if not validate_editable_draft(draft, participants):
        flash(INVALID_DRAFT_MESSAGE)
        return redirect(url_for('match.match_form', mode=mode))

    editable_parts = split_editable_draft_matches_and_bench(draft)
    if editable_parts is None:
        flash(INVALID_DRAFT_MESSAGE)
        return redirect(url_for('match.match_form', mode=mode))
    match_ids, bench_ids = editable_parts
    fixed_pairs = normalize_fixed_pairs(draft.get('fixed_pairs'), match_ids)


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
    matches = [
        [mark_bench_player(participants[pid]) for pid in group if pid in participants]
        for group in match_ids
    ]
    bench = [mark_bench_player(participants[pid]) for pid in bench_ids if pid in participants]

    court_count = get_draft_court_count(draft)
    match_count = get_match_count()

    config = load_raw_config()

    level_map = config["level_map"]
    gender_weight = config["gender_weight"]

    win_stats = calculate_participant_win_stats()

    # 各コート内を2人ずつペアにしてスコアをつける
    match_data = []  # 画面表示用
    for group in matches:  # group = [p1, p2, p3, p4]
        pairs = [group[i:i+2] for i in range(0, len(group), 2)]
        scored_pairs = [calculate_pair_score(pair, level_map, gender_weight, win_stats) for pair in pairs]
        match_data.append(scored_pairs)

    return render_template(
        'match_edit.html',
        matches=matches,
        match_data=match_data,  # 追加
        bench=bench,
        card_to_filename=card_to_filename,
        match_count=match_count,
        court_count=court_count,
        mode=mode,
        fixed_player_ids={pid for pair in fixed_pairs for pid in pair},
        fixed_pair_keys={tuple(pair) for pair in fixed_pairs},
    )


@match_bp.route('/match/optimize_pairs', methods=['POST'])
def optimize_pairs():
    mode = request.form.get('mode')
    if mode != 'admin':
        flash('管理者モードでのみ実行できます')
        return redirect(url_for('match.match_form', mode='viewer'))

    draft = get_active_draft()
    if draft is None:
        flash('編集中の組み合わせがありません')
        return redirect(url_for('match.match_form', mode=mode))

    try:
        config = load_raw_config()
        participants = {p.id: p for p in get_all_participants()}
        result = optimize_draft_pairs(
            draft,
            participants,
            config["level_map"],
            config["gender_weight"],
            calculate_participant_win_stats(),
        )
    except Exception:
        current_app.logger.exception('Failed to optimize draft pairs')
        flash('編集中の組み合わせを調整できませんでした。内容を確認してください')
        return redirect(url_for('match.match_form', mode=mode))

    if not result.success:
        flash(result.message)
        return redirect(url_for('match.match_form', mode=mode))

    save_draft_state(
        result.matches,
        result.bench,
        court_count=result.court_count,
        fixed_pairs=result.fixed_pairs,
    )
    flash(result.message)
    return redirect(url_for('match.edit_matches', mode=mode))


@match_bp.route('/match/swap', methods=['POST'])
def swap_players():
    raw = request.form.get('swap_ids', '')
    selected_ids = raw.split(',') if raw else []
    mode = request.form.get('mode', 'viewer')

    if len(selected_ids) != 2:
        return redirect(url_for('match.edit_matches', mode=mode))  # 2人以外選ばれてたら無視

    try:
        id1, id2 = map(int, selected_ids)
    except ValueError:
        return redirect(url_for('match.edit_matches', mode=mode))

    # 共有中の未確定 draft を正として現在の状態を取得
    draft = get_active_draft()
    if draft is None:
        return redirect(url_for('match.match_form', mode=mode))

    participants = {p.id: p for p in get_all_participants()}
    if not validate_editable_draft(draft, participants):
        flash(INVALID_DRAFT_MESSAGE)
        return redirect(url_for('match.match_form', mode=mode))

    match_ids, bench_ids = split_editable_draft_matches_and_bench(draft)
    if 'fixed_pairs' in draft and not validate_fixed_pairs(draft.get('fixed_pairs'), match_ids, set(participants)):
        flash('編集中の固定ペア情報が壊れています。再生成してください')
        return redirect(url_for('match.match_form', mode=mode))
    fixed_pairs = normalize_fixed_pairs(draft.get('fixed_pairs'), match_ids)

    fixed_pair_1 = get_fixed_pair_for_player(fixed_pairs, id1)
    fixed_pair_2 = get_fixed_pair_for_player(fixed_pairs, id2)
    bench_id_set = set(bench_ids)

    if same_current_pair(match_ids, id1, id2):
        selected_pair = sorted([id1, id2])
        if selected_pair in fixed_pairs:
            fixed_pairs = [pair for pair in fixed_pairs if pair != selected_pair]
            flash('固定ペアを解除しました')
        else:
            fixed_pairs = [pair for pair in fixed_pairs if id1 not in pair and id2 not in pair]
            fixed_pairs.append(selected_pair)
            fixed_pairs = normalize_fixed_pairs(fixed_pairs, match_ids)
            flash('固定ペアにしました')

        save_draft_state(
            match_ids,
            bench_ids,
            court_count=draft.get('court_count'),
            fixed_pairs=fixed_pairs,
        )
        return redirect(url_for('match.edit_matches', mode=mode))

    if (fixed_pair_1 or fixed_pair_2) and (id1 in bench_id_set or id2 in bench_id_set):
        flash('固定ペアはベンチ参加者と個別に入れ替えできません')
        save_draft_state(
            match_ids,
            bench_ids,
            court_count=draft.get('court_count'),
            fixed_pairs=fixed_pairs,
        )
        return redirect(url_for('match.edit_matches', mode=mode))

    if fixed_pair_1 or fixed_pair_2:
        swap_pair_positions(match_ids, id1, id2)
        fixed_pairs = normalize_fixed_pairs(fixed_pairs, match_ids)
        save_draft_state(
            match_ids,
            bench_ids,
            court_count=draft.get('court_count'),
            fixed_pairs=fixed_pairs,
        )
        return redirect(url_for('match.edit_matches', mode=mode))

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
        fixed_pairs=fixed_pairs,
    )

    return redirect(url_for('match.edit_matches', mode=mode))


@match_bp.route('/match/confirm', methods=['POST'])
def confirm_match():
    # 共有中の未確定 draft を確定対象の正とし、古い session draft は採用しない。
    draft = get_active_draft()
    if draft is None:
        return redirect(url_for('match.match_form'))

    participants = {p.id: p for p in get_all_participants()}
    if not validate_editable_draft(draft, participants):
        flash(INVALID_DRAFT_MESSAGE)
        return redirect(url_for('match.match_form'))

    editable_parts = split_editable_draft_matches_and_bench(draft)
    if editable_parts is None:
        flash(INVALID_DRAFT_MESSAGE)
        return redirect(url_for('match.match_form'))
    match_ids, bench_ids = editable_parts

    current_session = ensure_current_match_session()

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
    for p in get_participants_by_ids(confirmed_ids):
        p.games_played += 1

    # ワーカー切替時のセッション消失問題の調査用ログ（2025/10 対応）
    current_app.logger.debug(f"[confirm_match] Saving match_state_full: matches={match_ids}, bench={bench_ids}, count={match_count}")

    try:
        # 確定状態をファイル保存
        save_match_state_full(True, match_ids, bench_ids, match_count, court_count=draft.get('court_count'))

        # 確定済み state だけを表示の正とするため、未確定 draft を削除する
        clear_draft_state()

        current_session.status = "confirmed"
        if current_session.confirmed_at is None:
            current_session.confirmed_at = utc_now()

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    try:
        send_match_confirmed_line_notifications(current_session, match_count, match_ids, bench_ids)
    except Exception:
        current_app.logger.exception(
            "Unexpected error while sending LINE match notifications: session_id=%s match_count=%s",
            current_session.id,
            match_count,
        )

    mode = request.form.get('mode', 'viewer')
    return redirect(url_for('match.match_result', mode=mode))


@match_bp.route('/match/revert_to_draft', methods=['POST'])
def revert_match_to_draft():
    mode = request.form.get('mode', request.args.get('mode', 'admin'))
    if mode == 'viewer':
        return redirect(url_for('match.match_result', mode='viewer'))

    state = load_match_state()
    match_ids = state.get('matches', [])
    bench_ids = state.get('bench', [])
    if not (match_ids or bench_ids):
        flash('確定済み組み合わせがありません')
        return redirect(url_for('match.match_result', mode='admin'))

    save_draft_state(
        match_ids,
        bench_ids,
        court_count=state.get('court_count'),
    )

    confirmed_ids = {pid for group in match_ids for pid in group}
    if confirmed_ids:
        for participant in get_participants_by_ids(confirmed_ids):
            participant.games_played = max((participant.games_played or 0) - 1, 0)

    current_match_count = state.get('match_count', 0)
    match_round = get_latest_match_round(current_match_count)

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

    return redirect(url_for('match.edit_matches', mode='admin'))


@match_bp.route('/update_court_count', methods=['POST'])
def update_court_count():
    new_count = int(request.form['court_count'])

    # 参加者データ取得
    participants = get_active_participants()

    # 新しい組み合わせ生成
    matches, bench = generate_matches(participants, new_count)
    match_ids = [[p.id for p in group] for group in matches]
    bench_ids = [p.id for p in bench]
    save_draft_state(match_ids, bench_ids, court_count=new_count)

    mode = request.form.get('mode', 'viewer')

    return redirect(url_for('match.edit_matches', mode=mode))


@match_bp.route('/match/result')
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


@match_bp.route('/match/draft')
def match_draft():
    mode = request.args.get('mode', 'viewer')
    draft = get_active_draft()
    if draft is None:
        return redirect(url_for('match.match_result', mode=mode))

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


@match_bp.route('/match_result')
def legacy_match_result():
    mode = request.args.get('mode', 'viewer')
    return redirect(url_for('match.match_result', mode=mode))


@match_bp.route('/reset_match', methods=['POST'])
def reset_match():
    reset_match_state()
    flash('試合状態をリセットしました')
    return redirect(url_for('match.match_form'))
