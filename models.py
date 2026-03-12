from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class Player(db.Model):
    __tablename__ = 'players'
    id            = db.Column(db.Integer, primary_key=True)
    player_name   = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    points        = db.Column(db.Integer, default=100)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<Player {self.player_name} - {self.points} pts>'


class Room(db.Model):
    __tablename__ = 'rooms'
    id               = db.Column(db.Integer, primary_key=True)
    room_code        = db.Column(db.String(10), unique=True, nullable=False)
    bet_points       = db.Column(db.Integer, default=100)
    player1_id       = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=True)
    player2_id       = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=True)
    winner_id        = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=True)
    is_bankrupt_mode = db.Column(db.Boolean, default=False)
    is_bot_game      = db.Column(db.Boolean, default=False)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

    player1 = db.relationship('Player', foreign_keys=[player1_id])
    player2 = db.relationship('Player', foreign_keys=[player2_id])
    winner  = db.relationship('Player', foreign_keys=[winner_id])


class Punishment(db.Model):
    __tablename__ = 'punishments'
    id               = db.Column(db.Integer, primary_key=True)
    room_id          = db.Column(db.Integer, db.ForeignKey('rooms.id'),    nullable=False)
    winner_id        = db.Column(db.Integer, db.ForeignKey('players.id'),  nullable=False)
    loser_id         = db.Column(db.Integer, db.ForeignKey('players.id'),  nullable=False)
    punishment_text  = db.Column(db.Text,    nullable=False)
    whatsapp_number  = db.Column(db.String(20), nullable=False)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

    room   = db.relationship('Room',   foreign_keys=[room_id])
    winner = db.relationship('Player', foreign_keys=[winner_id])
    loser  = db.relationship('Player', foreign_keys=[loser_id])