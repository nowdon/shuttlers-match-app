from routes.legacy_blueprint import LegacyEndpointBlueprint


line_bp = LegacyEndpointBlueprint("line", __name__, dependency_module="app")


@line_bp.route('/line/webhook', methods=['POST'])
def line_webhook():
    if not is_line_messaging_enabled():
        return jsonify({"status": "disabled", "message": "LINE Messaging is disabled"}), 200

    raw_body = request.get_data()
    signature = request.headers.get("X-Line-Signature")
    channel_secret = os.environ.get("LINE_CHANNEL_SECRET")
    if not verify_line_signature(raw_body, signature, channel_secret):
        return jsonify({"error": "invalid signature"}), 403

    payload = request.get_json(silent=True) or {}
    for event in payload.get("events", []):
        process_line_webhook_event(event)
    return jsonify({"status": "ok"})


@line_bp.route('/notifications/line/start/<card>')
def start_line_notification(card):
    mode = request.args.get('mode', 'viewer')
    participant = get_participant_by_card_or_404(card)
    if not is_line_messaging_enabled():
        flash("LINE通知は現在無効です", "info")
        return redirect(url_for('thanks', mode=mode, card=participant.card))
    if not participant.active:
        flash("現在参加中の方のみLINE通知登録できます", "info")
        return redirect(url_for('thanks', mode=mode, card=participant.card))

    current_session = ensure_current_match_session()

    if get_active_line_account(participant) is not None:
        subscription = get_line_notification_subscription(
            participant.id, current_session.id
        )
        if subscription is None:
            subscription = NotificationSubscription(
                session_id=current_session.id,
                participant_id=participant.id,
                channel="line",
                active=True,
            )
            db.session.add(subscription)
        elif not subscription.active:
            subscription.active = True
        db.session.commit()
        flash("今回のLINE通知を登録しました", "success")
        return redirect(url_for('thanks', mode=mode, card=participant.card))

    token = LineLinkToken(
        token=generate_line_link_token_value(),
        participant_id=participant.id,
        session_id=current_session.id,
        expires_at=utc_now() + timedelta(minutes=30),
    )
    db.session.add(token)
    db.session.commit()
    return render_template(
        'line_link_token.html',
        participant=participant,
        token=token,
        mode=mode,
        line_bot_friend_url=os.environ.get("LINE_BOT_FRIEND_URL", "").strip(),
    )


@line_bp.route('/notifications/line/unsubscribe/<card>', methods=['POST'])
def unsubscribe_line_notification(card):
    mode = request.form.get('mode', request.args.get('mode', 'viewer'))
    participant = get_participant_by_card_or_404(card)
    current_session = ensure_current_match_session()
    subscription = get_line_notification_subscription(participant.id, current_session.id)
    if subscription is not None and subscription.active:
        subscription.active = False
        db.session.commit()
    flash("今回のLINE通知を解除しました", "success")
    return redirect(url_for('thanks', mode=mode, card=participant.card))
