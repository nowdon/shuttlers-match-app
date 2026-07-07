from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy


def utc_now():
    return datetime.now(timezone.utc)


db = SQLAlchemy()


class Participant(db.Model):
    __tablename__ = "participants"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    level = db.Column(db.String(20), nullable=False)
    weight = db.Column(db.Float, nullable=False)
    games_played = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)
    card = db.Column(db.String(10), unique=True, nullable=False)

    line_account = db.relationship(
        "LineAccount",
        back_populates="participant",
        uselist=False,
        cascade="all, delete-orphan",
    )
    notification_subscriptions = db.relationship(
        "NotificationSubscription",
        back_populates="participant",
        cascade="all, delete-orphan",
        lazy=True,
    )
    line_link_tokens = db.relationship(
        "LineLinkToken",
        back_populates="participant",
        cascade="all, delete-orphan",
        lazy=True,
    )
    notification_delivery_logs = db.relationship(
        "NotificationDeliveryLog",
        back_populates="participant",
        cascade="all, delete-orphan",
        lazy=True,
    )


class MatchRound(db.Model):
    __tablename__ = "match_rounds"
    id = db.Column(db.Integer, primary_key=True)
    round_number = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    matches = db.relationship(
        "MatchHistory", backref="round", cascade="all, delete-orphan", lazy=True
    )
    bench_players = db.relationship(
        "BenchHistory", backref="round", cascade="all, delete-orphan", lazy=True
    )


class MatchHistory(db.Model):
    __tablename__ = "match_histories"
    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey("match_rounds.id"), nullable=False)
    court_number = db.Column(db.Integer, nullable=False)
    team1_player1_id = db.Column(
        db.Integer, db.ForeignKey("participants.id"), nullable=False
    )
    team1_player2_id = db.Column(
        db.Integer, db.ForeignKey("participants.id"), nullable=False
    )
    team2_player1_id = db.Column(
        db.Integer, db.ForeignKey("participants.id"), nullable=False
    )
    team2_player2_id = db.Column(
        db.Integer, db.ForeignKey("participants.id"), nullable=False
    )
    team1_score = db.Column(db.Integer, nullable=True)
    team2_score = db.Column(db.Integer, nullable=True)
    score_text = db.Column(db.Text, nullable=True)
    winner_team = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)


class BenchHistory(db.Model):
    __tablename__ = "bench_histories"
    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey("match_rounds.id"), nullable=False)
    participant_id = db.Column(
        db.Integer, db.ForeignKey("participants.id"), nullable=False
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)


class MatchSession(db.Model):
    __tablename__ = "match_sessions"
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(20), nullable=False, default="draft")
    match_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    confirmed_at = db.Column(db.DateTime, nullable=True)
    notification_sent_at = db.Column(db.DateTime, nullable=True)

    subscriptions = db.relationship(
        "NotificationSubscription",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy=True,
    )
    link_tokens = db.relationship(
        "LineLinkToken",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy=True,
    )
    delivery_logs = db.relationship(
        "NotificationDeliveryLog",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy=True,
    )


class LineAccount(db.Model):
    __tablename__ = "line_accounts"
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(
        db.Integer, db.ForeignKey("participants.id"), nullable=False, unique=True
    )
    line_user_id = db.Column(db.String(128), nullable=False, unique=True)
    display_name = db.Column(db.String(100), nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    participant = db.relationship("Participant", back_populates="line_account")


class NotificationSubscription(db.Model):
    __tablename__ = "notification_subscriptions"
    __table_args__ = (
        db.UniqueConstraint(
            "session_id",
            "participant_id",
            "channel",
            name="uq_notification_subscription",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer, db.ForeignKey("match_sessions.id"), nullable=False
    )
    participant_id = db.Column(
        db.Integer, db.ForeignKey("participants.id"), nullable=False
    )
    channel = db.Column(db.String(20), nullable=False, default="line")
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    session = db.relationship("MatchSession", back_populates="subscriptions")
    participant = db.relationship(
        "Participant", back_populates="notification_subscriptions"
    )


class LineLinkToken(db.Model):
    __tablename__ = "line_link_tokens"
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(20), nullable=False, unique=True)
    participant_id = db.Column(
        db.Integer, db.ForeignKey("participants.id"), nullable=False
    )
    session_id = db.Column(
        db.Integer, db.ForeignKey("match_sessions.id"), nullable=False
    )
    used_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    participant = db.relationship("Participant", back_populates="line_link_tokens")
    session = db.relationship("MatchSession", back_populates="link_tokens")


class NotificationDeliveryLog(db.Model):
    __tablename__ = "notification_delivery_logs"
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer, db.ForeignKey("match_sessions.id"), nullable=False
    )
    participant_id = db.Column(
        db.Integer, db.ForeignKey("participants.id"), nullable=False
    )
    channel = db.Column(db.String(20), nullable=False, default="line")
    status = db.Column(db.String(20), nullable=False)
    error_message = db.Column(db.Text, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    session = db.relationship("MatchSession", back_populates="delivery_logs")
    participant = db.relationship(
        "Participant", back_populates="notification_delivery_logs"
    )
