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

from routes.helpers import (
    ALL_CARDS,
    PAYPAY_LINK_LABELS,
    TOKYO_TZ,
    get_tokyo_today,
    format_japanese_date,
    build_paypay_expiration_warnings,
    config,
    LEVEL_MAP,
    GENDER_WEIGHT,
    get_match_count,
    get_draft_court_count,
    get_confirmed_court_count,
    card_to_filename,
    generate_card_layout,
    render_index_view,
    get_participant_by_card_or_404,
    get_active_line_account,
    get_line_notification_subscription,
    is_line_messaging_enabled,
    has_required_line_messaging_config,
    get_line_notification_status,
    get_line_push_notification_targets,
    format_line_participant_label,
    _participant_id,
    build_personal_match_notification_message,
    build_personal_match_notification_context,
    send_match_confirmed_line_notifications,
    generate_line_link_token_value,
    reply_line_message,
    find_line_link_token,
    format_paypay_links_for_line,
    build_line_link_success_message,
    complete_line_link,
    process_line_webhook_event,
    same_current_pair,
    swap_pair_positions,
    has_valid_draft,
    get_latest_match_histories_by_court,
    render_match_result_page,
    parse_float,
    clear_all_data_records,
    serialize_datetime,
    build_participant_dump_map,
    participant_dump_fields,
    build_match_history_dump,
    HISTORY_DUMP_FILENAME_RE,
    get_match_history_dump_dir,
    get_match_history_archive_path,
    normalize_match_history_archive,
    build_match_history_archive_metadata,
    list_match_history_archives,
    load_match_history_archive,
    dump_match_history_to_json,
    build_history_dump_email_body,
    send_history_dump_email_if_enabled,
    clear_match_history_records,
    format_participant_label,
    get_participant_label_map,
    parse_optional_int,
    parse_winner_team,
    GAME_SCORE_RE,
    decide_game_winner,
    parse_score_text_rows,
    build_score_text_from_dropdowns,
    parse_score_text,
    build_match_score_form,
    apply_match_history_score_update,
    validate_round_winner_only_form,
    apply_round_score_updates,
)

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
