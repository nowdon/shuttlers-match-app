from routes.legacy_blueprint import LegacyEndpointBlueprint


participant_bp = LegacyEndpointBlueprint("participant", __name__, dependency_module="app")


@participant_bp.route('/')
def root_redirect():
    return redirect(url_for('viewer_index'))


@participant_bp.route('/register', methods=['GET', 'POST'])
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

        return redirect(url_for('thanks', mode=mode, card=card))

    card = request.args.get('card')
    return render_template('register.html', card=card, mode=mode)


@participant_bp.route('/qrcode/<user_type>')
def qrcode_image(user_type):
    config = load_raw_config()
    url = config.get("paypay_links", {}).get(user_type)
    if not url:
        return "Invalid user type", 400

    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')


@participant_bp.route('/thanks')
def thanks():
    mode = request.args.get('mode', 'viewer')
    card = request.args.get('card')
    config = load_config()
    paypay_links = config.get("paypay_links", {})
    participant = Participant.query.filter_by(card=card).first() if card else None
    line_notification_status = None
    if participant is not None:
        if is_line_messaging_enabled():
            if participant.active:
                current_session = ensure_current_match_session()
                line_notification_status = get_line_notification_status(
                    participant, current_session
                )
            else:
                line_notification_status = {"state": "inactive"}
    return render_template(
        'thanks.html',
        paypay_links=paypay_links,
        mode=mode,
        participant=participant,
        line_notification_status=line_notification_status,
    )


@participant_bp.route('/participant/<card>', methods=['GET', 'POST'])
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


@participant_bp.route('/viewer')
def viewer_index():
    return render_index_view(mode='viewer')
