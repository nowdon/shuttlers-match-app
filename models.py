from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


class Participant(db.Model):
    __tablename__ = 'participants'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    level = db.Column(db.String(20), nullable=False)
    weight = db.Column(db.Float, nullable=False)
    games_played = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)
    card = db.Column(db.String(10), unique=True, nullable=False)


class MatchRound(db.Model):
    __tablename__ = 'match_rounds'
    id = db.Column(db.Integer, primary_key=True)
    round_number = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    matches = db.relationship(
        'MatchHistory', backref='round', cascade='all, delete-orphan', lazy=True
    )
    bench_players = db.relationship(
        'BenchHistory', backref='round', cascade='all, delete-orphan', lazy=True
    )


class MatchHistory(db.Model):
    __tablename__ = 'match_histories'
    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey('match_rounds.id'), nullable=False)
    court_number = db.Column(db.Integer, nullable=False)
    team1_player1_id = db.Column(db.Integer, db.ForeignKey('participants.id'), nullable=False)
    team1_player2_id = db.Column(db.Integer, db.ForeignKey('participants.id'), nullable=False)
    team2_player1_id = db.Column(db.Integer, db.ForeignKey('participants.id'), nullable=False)
    team2_player2_id = db.Column(db.Integer, db.ForeignKey('participants.id'), nullable=False)
    team1_score = db.Column(db.Integer, nullable=True)
    team2_score = db.Column(db.Integer, nullable=True)
    winner_team = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class BenchHistory(db.Model):
    __tablename__ = 'bench_histories'
    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey('match_rounds.id'), nullable=False)
    participant_id = db.Column(db.Integer, db.ForeignKey('participants.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
