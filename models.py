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