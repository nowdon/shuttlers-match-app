import csv
import os
import json
import io
import logging
import urllib.error
import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from io import TextIOWrapper
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from models import (
    db,
    BenchHistory,
    LineAccount,
    LineLinkToken,
    MatchHistory,
    MatchRound,
    MatchSession,
    MatchNotification,
    NotificationDeliveryLog,
    NotificationSubscription,
    Participant,
    utc_now,
)
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import selectinload
import qrcode
from logic import generate_matches
from utils.config import (
    load_config,
    load_raw_config,
    normalize_consecutive_play_limit,
    normalize_score_input_mode,
    normalize_scoring_system,
    parse_bool,
    parse_positive_int,
    save_config,
)
from utils.match_state import load_match_state, save_match_state_full
from utils.draft_state import clear_draft_state, get_active_draft, save_draft_state
from utils.score import calculate_pair_score
from utils.pair_optimizer import (
    get_current_pair,
    get_fixed_pair_for_player,
    INVALID_DRAFT_MESSAGE,
    normalize_fixed_pairs,
    optimize_draft_pairs,
    split_editable_draft_matches_and_bench,
    validate_editable_draft,
    validate_fixed_pairs,
)
from utils.stats import calculate_participant_win_stats
from utils.reset import clear_match_runtime_state, reset_match_state
from utils.match_session import ensure_current_match_session
from utils.line_push import push_line_message, send_line_reply, verify_line_signature
from utils.mail_sender import send_email_with_attachment
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

PAYPAY_LINK_LABELS = {
    "adults": "社会人用",
    "students": "学生用",
}
TOKYO_TZ = ZoneInfo("Asia/Tokyo")


def get_tokyo_today():
    return datetime.now(TOKYO_TZ).date()


def format_japanese_date(value):
    return f"{value.year}年{value.month}月{value.day}日"


def build_paypay_expiration_warnings(config, today=None):
    paypay_links = config.get("paypay_links", {})
    expirations = config.get("paypay_link_expirations", {})
    if not isinstance(paypay_links, dict):
        paypay_links = {}
    if not isinstance(expirations, dict):
        expirations = {}

    today = today or get_tokyo_today()
    warnings = []
    for key, label in PAYPAY_LINK_LABELS.items():
        url = (paypay_links.get(key) or "").strip()
        if not url:
            continue

        expiration_text = (expirations.get(key) or "").strip()
        if not expiration_text:
            warnings.append({
                "level": "warning",
                "message": f"⚠️ {label}PayPayリンクの有効期限が設定されていません",
            })
            continue

        try:
            expiration_date = datetime.strptime(expiration_text, "%Y-%m-%d").date()
        except ValueError:
            warnings.append({
                "level": "warning",
                "message": f"⚠️ {label}PayPayリンクの有効期限が設定されていません",
            })
            continue

        days_until_expiration = (expiration_date - today).days
        formatted_date = format_japanese_date(expiration_date)
        if days_until_expiration == 1:
            message = f"⚠️ {label}PayPayリンクは明日（{formatted_date}）期限切れになります"
        elif days_until_expiration == 0:
            message = f"⚠️ {label}PayPayリンクは本日（{formatted_date}）期限切れになります"
        elif days_until_expiration < 0:
            message = f"🚨 {label}PayPayリンクは期限切れです（{formatted_date}）"
        else:
            continue
        warnings.append({"level": "expired" if days_until_expiration < 0 else "warning", "message": message})
    return warnings

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

def generate_card_layout(participants):
    suits = ['♥', '♦', '♣', '♠']
    columns = {suit: [] for suit in suits}

    participants_dict = {p.card: p for p in participants}

    card_map = {}
    for card in ALL_CARDS:
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
        max_rows=max_rows,
        paypay_expiration_warnings=build_paypay_expiration_warnings(load_config()) if mode == 'admin' else []
    )




def get_participant_by_card_or_404(card):
    participant = Participant.query.filter_by(card=card).first()
    if participant is None:
        abort(404)
    return participant


def get_active_line_account(participant):
    account = participant.line_account
    if account is not None and account.active:
        return account
    return None


def get_line_notification_subscription(participant_id, session_id):
    return NotificationSubscription.query.filter_by(
        session_id=session_id,
        participant_id=participant_id,
        channel="line",
    ).first()



def is_line_messaging_enabled():
    """Return True only when LINE Messaging is explicitly enabled."""
    return os.environ.get("LINE_MESSAGING_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
    }


def has_required_line_messaging_config():
    """Return True when required LINE Messaging API settings are present."""
    return bool(
        os.environ.get("LINE_CHANNEL_SECRET")
        and os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    )

def get_line_notification_status(participant, current_session):
    has_active_line_account = get_active_line_account(participant) is not None
    current_subscription = get_line_notification_subscription(
        participant.id, current_session.id
    )
    current_subscription_active = (
        current_subscription is not None and current_subscription.active
    )
    has_past_subscription = (
        NotificationSubscription.query.filter(
            NotificationSubscription.participant_id == participant.id,
            NotificationSubscription.channel == "line",
            NotificationSubscription.session_id != current_session.id,
        ).first()
        is not None
    )

    if not has_active_line_account:
        state = "unlinked"
    elif current_subscription_active:
        state = "subscribed"
    elif has_past_subscription:
        state = "linked_past_session_only"
    else:
        state = "linked_unsubscribed"

    return {
        "state": state,
        "has_active_line_account": has_active_line_account,
        "current_subscription_active": current_subscription_active,
        "has_past_subscription": has_past_subscription,
    }


def get_line_push_notification_targets(match_session):
    """Return active participants subscribed to LINE notifications for this session."""
    if match_session is None or match_session.id is None:
        return []
    return (
        db.session.query(Participant, LineAccount)
        .join(
            NotificationSubscription,
            NotificationSubscription.participant_id == Participant.id,
        )
        .join(LineAccount, LineAccount.participant_id == Participant.id)
        .filter(
            NotificationSubscription.session_id == match_session.id,
            NotificationSubscription.channel == "line",
            NotificationSubscription.active.is_(True),
            LineAccount.active.is_(True),
            Participant.active.is_(True),
        )
        .all()
    )


def format_line_participant_label(participant):
    if participant is None:
        return "不明な参加者"

    card = (participant.card or "").strip()
    if card in ("JOKER_RED", "JOKER_BLACK"):
        card = "JK"

    if card:
        return f"{card} {participant.name}"
    return participant.name


def _participant_id(participant_or_id):
    return getattr(participant_or_id, "id", participant_or_id)


def build_personal_match_notification_message(
    participant, matches, bench, match_count, result_url
):
    participant_id = _participant_id(participant)
    for court_index, match in enumerate(matches, start=1):
        court_number = getattr(match, "court_number", court_index)
        if hasattr(match, "team1_player1_id"):
            team1 = [match.team1_player1, match.team1_player2]
            team2 = [match.team2_player1, match.team2_player2]
        else:
            team1 = list(match[:2])
            team2 = list(match[2:4])

        match_participant_ids = [_participant_id(player) for player in team1 + team2]
        if participant_id not in match_participant_ids:
            continue

        team1_text = "・".join(format_line_participant_label(player) for player in team1)
        team2_text = "・".join(format_line_participant_label(player) for player in team2)
        return (
            f"第{match_count}回目\n\n"
            f"あなたは {court_number}コートです\n\n"
            f"{team1_text}\n"
            "vs\n"
            f"{team2_text}\n\n"
            "結果はこちら\n"
            f"{result_url}"
        )

    bench_participant_ids = {_participant_id(bench_player) for bench_player in bench}
    if participant_id in bench_participant_ids:
        return (
            f"第{match_count}回目\n\n"
            "今回は待機です。\n"
            "次の組み合わせまでお待ちください。\n\n"
            "結果はこちら\n"
            f"{result_url}"
        )

    return None


def build_personal_match_notification_context(matches, bench):
    participant_ids = {pid for match in matches for pid in match}
    participant_ids.update(bench)
    participants_by_id = {
        participant.id: participant
        for participant in Participant.query.filter(Participant.id.in_(participant_ids)).all()
    }
    message_matches = [
        [participants_by_id.get(participant_id) for participant_id in match]
        for match in matches
    ]
    message_bench = [participants_by_id.get(participant_id) for participant_id in bench]
    return message_matches, message_bench


def send_match_confirmed_line_notifications(match_session, match_count, matches=None, bench=None):
    """Send confirmed-match LINE notifications once per match count and channel."""
    if match_session is None:
        return False
    if not is_line_messaging_enabled():
        return False
    if not has_required_line_messaging_config():
        app.logger.warning(
            "LINE Messaging is enabled but required environment variables are missing; skipping notifications"
        )
        return False

    try:
        existing_notification = MatchNotification.query.filter_by(
            session_id=match_session.id,
            match_count=match_count,
            channel="line",
        ).first()
    except TypeError:
        # Some focused route tests replace db.session with a minimal fake that
        # cannot back Flask-SQLAlchemy model queries. In that case, skip only
        # notification side effects and keep the confirmation route behavior under test.
        return False
    if existing_notification is not None:
        return False

    notification = MatchNotification(
        session_id=match_session.id,
        match_count=match_count,
        channel="line",
        status="pending",
    )
    targets = get_line_push_notification_targets(match_session)
    db.session.add(notification)
    db.session.flush()

    delivery_logs = []
    for participant, _line_account in targets:
        delivery_log = NotificationDeliveryLog(
            session_id=match_session.id,
            participant_id=participant.id,
            match_count=match_count,
            channel="line",
            status="pending",
            sent_at=utc_now(),
        )
        db.session.add(delivery_log)
        delivery_logs.append((delivery_log, participant))

    if not delivery_logs:
        notification.status = "completed"
        notification.sent_at = utc_now()
        db.session.commit()
        return True

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False

    if matches is None or bench is None:
        state = load_match_state()
        matches = state.get("matches", [])
        bench = state.get("bench", [])
    message_matches, message_bench = build_personal_match_notification_context(matches, bench)
    result_url = url_for("match_result", _external=True)
    logs_by_participant_id = {
        log.participant_id: log for log, _participant in delivery_logs
    }
    for participant, line_account in targets:
        delivery_log = logs_by_participant_id[participant.id]
        delivery_log.sent_at = utc_now()
        message = build_personal_match_notification_message(
            participant, message_matches, message_bench, match_count, result_url
        )
        if message is None:
            delivery_log.status = "skipped"
            delivery_log.error_message = "participant not found in confirmed matches or bench"
            continue
        try:
            push_line_message(line_account.line_user_id, message)
            delivery_log.status = "success"
            delivery_log.error_message = None
        except Exception as error:  # Keep confirmation successful even when notification fails.
            delivery_log.status = "failed"
            delivery_log.error_message = str(error)
            app.logger.warning(
                "Failed to send LINE push notification: session_id=%s match_count=%s participant_id=%s error=%s",
                match_session.id,
                match_count,
                participant.id,
                error,
            )

    notification.status = "completed"
    notification.sent_at = utc_now()
    db.session.commit()
    return True

def generate_line_link_token_value():
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(10):
        token = "".join(secrets.choice(alphabet) for _ in range(6))
        if LineLinkToken.query.filter_by(token=token).first() is None:
            return token
    return secrets.token_urlsafe(8)[:12].upper()






def reply_line_message(reply_token, message_text):
    if not reply_token:
        return
    try:
        send_line_reply(reply_token, message_text)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        app.logger.warning("Failed to send LINE reply: %s", error)


def find_line_link_token(token_value):
    return (
        LineLinkToken.query.options(
            selectinload(LineLinkToken.participant),
            selectinload(LineLinkToken.session),
        )
        .filter_by(token=token_value)
        .first()
    )


def format_paypay_links_for_line(paypay_links):
    lines = [
        "続けて参加費のお支払いをお願いします。",
        "社会人：600円",
        "学生：300円",
    ]
    missing_links_message = "PayPayリンクが未設定のため、現地でお支払いください。"
    if not isinstance(paypay_links, dict):
        return "\n\n" + "\n".join(lines + ["", missing_links_message])
    labels = {
        "adults": "社会人の方はこちら（600円）",
        "students": "学生の方はこちら（300円）",
    }
    link_lines = []
    for key in ("adults", "students"):
        url = (paypay_links.get(key) or "").strip()
        if url:
            link_lines.extend([labels[key], url])
    for key, url_value in paypay_links.items():
        if key in labels:
            continue
        url = (url_value or "").strip() if isinstance(url_value, str) else ""
        if url:
            link_lines.extend([f"{key}はこちら", url])
    if link_lines:
        return "\n\n" + "\n".join(lines + ["", "PayPayはこちら:"] + link_lines)
    return "\n\n" + "\n".join(lines + ["", missing_links_message])


def build_line_link_success_message():
    message = "LINE通知登録が完了しました🏸\n組み合わせが確定したらLINEでお知らせします。"
    try:
        paypay_text = format_paypay_links_for_line(
            load_config().get("paypay_links", {})
        )
    except (OSError, json.JSONDecodeError, TypeError, AttributeError) as error:
        app.logger.warning("Failed to load PayPay links for LINE reply: %s", error)
        paypay_text = ""
    return message + paypay_text


def complete_line_link(token_value, line_user_id):
    now = utc_now()
    link_token = find_line_link_token(token_value)
    if link_token is None:
        return False, "連携コードが見つかりません。コードを確認してください。"
    if link_token.used_at is not None:
        return False, "この連携コードはすでに使用されています。"
    expires_at = link_token.expires_at
    if expires_at.tzinfo is None:
        now = now.replace(tzinfo=None)
    if expires_at < now:
        return False, "連携コードの有効期限が切れています。もう一度登録を開始してください。"
    if link_token.participant is None or link_token.session is None:
        return False, "連携コードが無効です。もう一度登録を開始してください。"
    if not link_token.participant.active:
        return False, "現在参加中ではないため、LINE通知登録はできません。"

    conflicting_account = LineAccount.query.filter(
        LineAccount.line_user_id == line_user_id,
        LineAccount.participant_id != link_token.participant_id,
    ).first()
    if conflicting_account is not None:
        return False, "このLINEアカウントは別の参加者に連携済みです。"

    account = LineAccount.query.filter_by(
        participant_id=link_token.participant_id
    ).first()
    if account is None:
        account = LineAccount(
            participant_id=link_token.participant_id,
            line_user_id=line_user_id,
            active=True,
        )
        db.session.add(account)
    else:
        account.line_user_id = line_user_id
        account.active = True

    subscription = get_line_notification_subscription(
        link_token.participant_id, link_token.session_id
    )
    if subscription is None:
        subscription = NotificationSubscription(
            session_id=link_token.session_id,
            participant_id=link_token.participant_id,
            channel="line",
            active=True,
        )
        db.session.add(subscription)
    else:
        subscription.active = True

    link_token.used_at = now
    db.session.commit()
    return True, build_line_link_success_message()


def process_line_webhook_event(event):
    if event.get("type") != "message":
        return
    message = event.get("message") or {}
    if message.get("type") != "text":
        return
    line_user_id = (event.get("source") or {}).get("userId")
    if not line_user_id:
        return

    token_value = (message.get("text") or "").strip()
    success, reply_message = complete_line_link(token_value, line_user_id)
    reply_line_message(event.get("replyToken"), reply_message)












def same_current_pair(match_ids, id1, id2):
    position_1 = get_current_pair(match_ids, id1)
    position_2 = get_current_pair(match_ids, id2)
    return (
        position_1 is not None
        and position_2 is not None
        and position_1[0] == position_2[0]
        and position_1[1] == position_2[1]
        and len(position_1[2]) == 2
    )


def swap_pair_positions(match_ids, id1, id2):
    position_1 = get_current_pair(match_ids, id1)
    position_2 = get_current_pair(match_ids, id2)
    if position_1 is None or position_2 is None:
        return False

    match_index_1, start_1, pair_1 = position_1
    match_index_2, start_2, pair_2 = position_2
    if len(pair_1) != 2 or len(pair_2) != 2:
        return False

    match_ids[match_index_1][start_1:start_1 + 2] = pair_2
    match_ids[match_index_2][start_2:start_2 + 2] = pair_1
    return True






def has_valid_draft(matches, bench):
    return (
        isinstance(matches, list)
        and isinstance(bench, list)
        and bool(matches or bench)
    )







def get_latest_match_histories_by_court(match_count):
    """Return MatchHistory rows for the latest persisted round by court number."""
    match_round = (
        MatchRound.query
        .options(selectinload(MatchRound.matches))
        .filter_by(round_number=match_count)
        .order_by(MatchRound.id.desc())
        .first()
    )
    if match_round is None:
        return {}
    return {match.court_number: match for match in match_round.matches}


def render_match_result_page(match_ids, bench_ids, match_count, mode, *, is_draft, has_draft, has_confirmed):
    participants = {p.id: p for p in Participant.query.all()}

    matches = [[participants[pid] for pid in group] for group in match_ids]
    bench = [participants[pid] for pid in bench_ids] if bench_ids else []
    config = load_config()
    scoring_system = config["scoring_system"]
    match_histories_by_court = {}
    if mode == 'admin' and not is_draft and has_confirmed:
        match_histories_by_court = get_latest_match_histories_by_court(match_count)

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
        match_histories_by_court=match_histories_by_court,
        score_input_mode=config["score_input_mode"],
        scoring_system=scoring_system,
        score_options=list(range(scoring_system["max_points"] + 1)),
        score_rows_by_match_id={
            match.id: parse_score_text_rows(match.score_text, scoring_system["games_per_match"])
            for match in match_histories_by_court.values()
        },
    )








def parse_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default




def clear_all_data_records():
    # Bulk delete does not trigger SQLAlchemy relationship cascades, so delete
    # notification rows explicitly from foreign-key children to parents.
    MatchNotification.query.delete()
    NotificationDeliveryLog.query.delete()
    NotificationSubscription.query.delete()
    LineLinkToken.query.delete()
    LineAccount.query.delete()
    MatchSession.query.delete()
    BenchHistory.query.delete()
    MatchHistory.query.delete()
    MatchRound.query.delete()
    Participant.query.delete()




def serialize_datetime(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def build_participant_dump_map(rounds):
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
    return {
        participant.id: {
            "name": participant.name,
            "card": participant.card,
        }
        for participant in participants
    }


def participant_dump_fields(participant_map, participant_id, prefix=None):
    participant = participant_map.get(participant_id, {})
    data = {
        "id": participant_id,
        "name": participant.get("name", "不明な参加者"),
        "card": participant.get("card"),
    }
    if prefix is None:
        return {
            "participant_id": data["id"],
            "name": data["name"],
            "card": data["card"],
        }
    return {
        f"{prefix}_id": data["id"],
        f"{prefix}_name": data["name"],
        f"{prefix}_card": data["card"],
    }


def build_match_history_dump(reason):
    rounds = (
        MatchRound.query
        .options(
            selectinload(MatchRound.matches),
            selectinload(MatchRound.bench_players),
        )
        .order_by(MatchRound.created_at.asc(), MatchRound.id.asc())
        .all()
    )
    participant_map = build_participant_dump_map(rounds)

    return {
        "schema_version": 1,
        "dumped_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "rounds": [
            {
                "id": match_round.id,
                "round_number": match_round.round_number,
                "created_at": serialize_datetime(match_round.created_at),
                "matches": [
                    {
                        "id": match.id,
                        "court_number": match.court_number,
                        **participant_dump_fields(participant_map, match.team1_player1_id, "team1_player1"),
                        **participant_dump_fields(participant_map, match.team1_player2_id, "team1_player2"),
                        **participant_dump_fields(participant_map, match.team2_player1_id, "team2_player1"),
                        **participant_dump_fields(participant_map, match.team2_player2_id, "team2_player2"),
                        "score_text": match.score_text,
                        "team1_score": match.team1_score,
                        "team2_score": match.team2_score,
                        "winner_team": match.winner_team,
                        "created_at": serialize_datetime(match.created_at),
                    }
                    for match in sorted(match_round.matches, key=lambda item: (item.court_number, item.id))
                ],
                "bench": [
                    {
                        **participant_dump_fields(participant_map, bench_history.participant_id),
                        "created_at": serialize_datetime(bench_history.created_at),
                    }
                    for bench_history in sorted(match_round.bench_players, key=lambda item: item.id)
                ],
            }
            for match_round in rounds
        ],
    }




HISTORY_DUMP_FILENAME_RE = re.compile(r"^match_history_[A-Za-z0-9_]+_\d{8}_\d{6}_\d{6}\.json$")


def get_match_history_dump_dir():
    return os.path.join(app.instance_path, 'history_dumps')


def get_match_history_archive_path(filename):
    if not HISTORY_DUMP_FILENAME_RE.fullmatch(filename or ""):
        abort(404)

    dump_dir = os.path.realpath(get_match_history_dump_dir())
    archive_path = os.path.realpath(os.path.join(dump_dir, filename))
    if os.path.dirname(archive_path) != dump_dir or not os.path.isfile(archive_path):
        abort(404)
    return archive_path


def normalize_match_history_archive(data):
    if not isinstance(data, dict):
        data = {}

    normalized_rounds = []
    rounds = data.get("rounds")
    if not isinstance(rounds, list):
        rounds = []

    for round_data in rounds:
        if not isinstance(round_data, dict):
            continue

        matches = round_data.get("matches")
        if not isinstance(matches, list):
            matches = []

        bench = round_data.get("bench")
        if not isinstance(bench, list):
            bench = []

        normalized_rounds.append({
            "round_number": round_data.get("round_number"),
            "created_at": round_data.get("created_at"),
            "matches": [match for match in matches if isinstance(match, dict)],
            "bench": [bench_item for bench_item in bench if isinstance(bench_item, dict)],
        })

    return {
        "schema_version": data.get("schema_version"),
        "dumped_at": data.get("dumped_at"),
        "reason": data.get("reason"),
        "rounds": normalized_rounds,
    }


def build_match_history_archive_metadata(filename, path):
    stat_result = os.stat(path)
    metadata = {
        "filename": filename,
        "size": stat_result.st_size,
        "modified_at": datetime.fromtimestamp(stat_result.st_mtime, timezone.utc),
        "dumped_at": None,
        "reason": None,
        "round_count": 0,
        "match_count": 0,
        "bench_count": 0,
        "status": "ok",
        "error_message": None,
    }

    try:
        with open(path, encoding='utf-8') as archive_file:
            archive = normalize_match_history_archive(json.load(archive_file))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        app.logger.exception('Failed to read match history archive JSON: %s', filename)
        metadata["status"] = "error"
        metadata["error_message"] = "読み込みエラー"
        return metadata

    metadata["dumped_at"] = archive["dumped_at"]
    metadata["reason"] = archive["reason"]
    metadata["round_count"] = len(archive["rounds"])
    metadata["match_count"] = sum(len(round_data["matches"]) for round_data in archive["rounds"])
    metadata["bench_count"] = sum(len(round_data["bench"]) for round_data in archive["rounds"])
    return metadata


def list_match_history_archives():
    dump_dir = get_match_history_dump_dir()
    if not os.path.isdir(dump_dir):
        return []

    archives = []
    for filename in os.listdir(dump_dir):
        if not HISTORY_DUMP_FILENAME_RE.fullmatch(filename):
            continue
        try:
            path = get_match_history_archive_path(filename)
        except Exception:
            continue
        archives.append(build_match_history_archive_metadata(filename, path))

    return sorted(
        archives,
        key=lambda archive: (archive["dumped_at"] or archive["modified_at"].isoformat()),
        reverse=True,
    )


def load_match_history_archive(filename):
    archive_path = get_match_history_archive_path(filename)
    with open(archive_path, encoding='utf-8') as archive_file:
        return normalize_match_history_archive(json.load(archive_file))

def dump_match_history_to_json(reason):
    dump_dir = get_match_history_dump_dir()
    os.makedirs(dump_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
    dump_path = os.path.join(dump_dir, f'match_history_{reason}_{timestamp}.json')
    dump_data = build_match_history_dump(reason)
    with open(dump_path, 'w', encoding='utf-8') as dump_file:
        json.dump(dump_data, dump_file, indent=2, ensure_ascii=False)
    return dump_path


def build_history_dump_email_body(dump_data, attachment_name):
    rounds = dump_data.get("rounds") if isinstance(dump_data, dict) else []
    if not isinstance(rounds, list):
        rounds = []
    round_count = len(rounds)
    match_count = sum(len(round_data.get("matches", [])) for round_data in rounds if isinstance(round_data, dict))
    bench_count = sum(len(round_data.get("bench", [])) for round_data in rounds if isinstance(round_data, dict))
    return "\n".join([
        "shuttlers-match-app の試合履歴ダンプを添付します。",
        f"ダンプ作成日時: {dump_data.get('dumped_at')}",
        f"ダンプ理由: {dump_data.get('reason')}",
        f"添付ファイル名: {attachment_name}",
        f"ラウンド数: {round_count}",
        f"試合数: {match_count}",
        f"待機履歴数: {bench_count}",
    ])


def send_history_dump_email_if_enabled(dump_path):
    email_config = load_config().get("history_dump_email", {})
    if not email_config.get("enabled"):
        return True

    recipient = (email_config.get("recipient") or "").strip()
    if not recipient:
        return True

    try:
        with open(dump_path, encoding='utf-8') as dump_file:
            dump_data = json.load(dump_file)
        attachment_name = os.path.basename(dump_path)
        dumped_at = dump_data.get("dumped_at") or datetime.now(timezone.utc).isoformat()
        subject_date = dumped_at[:10]
        send_email_with_attachment(
            recipient=recipient,
            subject=f"shuttlers-match-app 試合履歴ダンプ {subject_date}",
            body=build_history_dump_email_body(dump_data, attachment_name),
            attachment_path=dump_path,
        )
    except Exception:
        app.logger.exception('Failed to send match history dump email')
        return False
    return True


def clear_match_history_records():
    BenchHistory.query.delete()
    MatchHistory.query.delete()
    MatchRound.query.delete()


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


def build_match_score_form(form, match_history_id):
    """Return score fields for one match from a round-level score form."""
    prefix = f"match_{match_history_id}_"
    return {
        key.removeprefix(prefix): value
        for key, value in form.items()
        if key.startswith(prefix)
    }


def apply_match_history_score_update(match_history, form):
    """Apply score form values to a MatchHistory row without committing."""
    config = load_config()
    score_input_mode = config["score_input_mode"]

    if score_input_mode == "score":
        dropdown_valid, posted_score_text, dropdown_error = build_score_text_from_dropdowns(
            form,
            config["scoring_system"]["games_per_match"],
        )
        if not dropdown_valid:
            return False, dropdown_error or 'ゲーム別スコアを正しく入力してください'
        valid, score_text, team1_score, team2_score, winner_team_or_error = parse_score_text(
            posted_score_text,
            config["scoring_system"],
        )
        if not valid:
            return False, winner_team_or_error or 'ゲーム別スコアを正しく入力してください'
        winner_team = winner_team_or_error
        match_history.score_text = score_text
        match_history.team1_score = team1_score
        match_history.team2_score = team2_score
        match_history.winner_team = winner_team
    else:
        match_history.winner_team = parse_winner_team(form.get('winner_team'))

    return True, None


def validate_round_winner_only_form(form):
    """Validate winner_only round-level fields before applying any updates."""
    winner_team = form.get("winner_team")
    if winner_team in ("", "1", "2"):
        return True, None
    return False, "勝者の入力値が不正です"


def apply_round_score_updates(match_round):
    """Apply all posted score updates for a MatchRound atomically."""
    config = load_config()
    matches = sorted(match_round.matches, key=lambda match: match.court_number)
    match_forms = [
        (match_history, build_match_score_form(request.form, match_history.id))
        for match_history in matches
    ]
    if config["score_input_mode"] == "winner_only":
        for _match_history, match_form in match_forms:
            valid, error_message = validate_round_winner_only_form(match_form)
            if not valid:
                db.session.rollback()
                return False, error_message

    for match_history, match_form in match_forms:
        updated, error_message = apply_match_history_score_update(
            match_history,
            match_form,
        )
        if not updated:
            db.session.rollback()
            return False, error_message
    db.session.commit()
    return True, None



















# 管理者向けトップページ

# 参加者向けビュー

from routes.participant import (
    participant_bp,
    qrcode_image,
    register,
    root_redirect,
    thanks,
    participant_view,
    viewer_index,
)
from routes.line import (
    line_bp,
    line_webhook,
    start_line_notification,
    unsubscribe_line_notification,
)
from routes.admin import (
    admin_bp,
    admin_index,
    admin_settings,
    download_template,
    reset_db,
    upload_csv,
)
from routes.match import (
    match_bp,
    confirm_match,
    edit_matches,
    legacy_match_result,
    match_draft,
    match_form,
    match_result,
    optimize_pairs,
    reset_match,
    revert_match_to_draft,
    swap_players,
    update_court_count,
)
from routes.history import (
    history_bp,
    admin_match_history,
    admin_match_history_archive_detail,
    admin_match_history_archives,
    dump_and_clear_match_history,
    dump_match_history,
    update_match_history_round_score,
    update_match_history_score,
    update_match_result_round_score,
    update_match_result_score,
)

app.register_blueprint(participant_bp)
app.register_blueprint(line_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(match_bp)
app.register_blueprint(history_bp)


if __name__ == '__main__':
    ensure_database_tables()
    app.run(debug=True, host='0.0.0.0', port=5001)
