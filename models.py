from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# ══════════════════════════════
#  PLAYER
# ══════════════════════════════
class Player(db.Model):
    __tablename__     = 'players'
    id                = db.Column(db.Integer, primary_key=True)
    player_name       = db.Column(db.String(50), unique=True, nullable=False)
    password_hash     = db.Column(db.String(256), nullable=False)
    points            = db.Column(db.Integer, default=100)
    xp                = db.Column(db.Integer, default=0)       # م12: XP
    win_streak        = db.Column(db.Integer, default=0)       # م4: Streak
    best_streak       = db.Column(db.Integer, default=0)       # م4: أحسن streak
    last_daily_reward = db.Column(db.DateTime, nullable=True)  # م2: Daily Reward
    avatar            = db.Column(db.String(200), nullable=True)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    @property
    def rank(self):
        """م1: نظام الرتب"""
        if self.points >= 1000: return {'name': 'Diamond', 'icon': '💎', 'color': '#60a5fa'}
        if self.points >= 500:  return {'name': 'Gold',    'icon': '🥇', 'color': '#fbbf24'}
        if self.points >= 200:  return {'name': 'Silver',  'icon': '⚪', 'color': '#94a3b8'}
        return                         {'name': 'Bronze',  'icon': '🟤', 'color': '#b45309'}

    @property
    def level(self):
        """م12: مستوى XP — كل 100 XP = لفل"""
        return max(1, self.xp // 100 + 1)

    @property
    def xp_progress(self):
        """XP في اللفل الحالي"""
        return self.xp % 100

    @property
    def streak_bonus(self):
        """م4: bonus نقاط على Streak"""
        if self.win_streak >= 5: return 50
        if self.win_streak >= 3: return 25
        return 0


# ══════════════════════════════
#  ROOM
# ══════════════════════════════
class Room(db.Model):
    __tablename__    = 'rooms'
    id               = db.Column(db.Integer, primary_key=True)
    room_code        = db.Column(db.String(10), unique=True, nullable=False)
    bet_points       = db.Column(db.Integer, default=100)
    player1_id       = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=True)
    player2_id       = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=True)
    winner_id        = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=True)
    is_bankrupt_mode = db.Column(db.Boolean, default=False)
    is_bot_game      = db.Column(db.Boolean, default=False)
    is_public        = db.Column(db.Boolean, default=False)
    status           = db.Column(db.String(20), default='waiting')
    random_event     = db.Column(db.String(50), nullable=True)  # م6: Random Event
    max_players      = db.Column(db.Integer, default=2)          # Group rooms: 2-6
    mode             = db.Column(db.String(20), default='classic') # classic/speed/daily
    timer_seconds    = db.Column(db.Integer, default=0)           # Speed mode timer
    group_secret     = db.Column(db.Integer, nullable=True)       # Group rooms: رقم واحد الكل يخمنه
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

    player1 = db.relationship('Player', foreign_keys=[player1_id])
    player2 = db.relationship('Player', foreign_keys=[player2_id])
    winner  = db.relationship('Player', foreign_keys=[winner_id])


# ══════════════════════════════
#  PUNISHMENT
# ══════════════════════════════
class Punishment(db.Model):
    __tablename__   = 'punishments'
    id              = db.Column(db.Integer, primary_key=True)
    room_id         = db.Column(db.Integer, db.ForeignKey('rooms.id'),   nullable=False)
    winner_id       = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    loser_id        = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    punishment_text = db.Column(db.Text, nullable=False)
    whatsapp_number = db.Column(db.String(20), nullable=False)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    room   = db.relationship('Room',   foreign_keys=[room_id])
    winner = db.relationship('Player', foreign_keys=[winner_id])
    loser  = db.relationship('Player', foreign_keys=[loser_id])


# ══════════════════════════════
#  MATCH  (م4+م8)
# ══════════════════════════════
class Match(db.Model):
    __tablename__ = 'matches'
    id            = db.Column(db.Integer, primary_key=True)
    player1_id    = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    player2_id    = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=True)
    winner_id     = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=True)
    bet           = db.Column(db.Integer, default=0)
    is_bot        = db.Column(db.Boolean, default=False)
    guesses       = db.Column(db.Integer, default=0)
    guess_log     = db.Column(db.Text, nullable=True)   # م8: Match Replay — JSON list
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    player1 = db.relationship('Player', foreign_keys=[player1_id])
    player2 = db.relationship('Player', foreign_keys=[player2_id])
    winner  = db.relationship('Player', foreign_keys=[winner_id])


# ══════════════════════════════
#  ACHIEVEMENTS  (م9)
# ══════════════════════════════
ACHIEVEMENTS_DEF = {
    'first_win':   {'name': 'أول فوز 🏆',         'desc': 'كسبت أول مباراة',            'icon': '🏆'},
    'wins_10':     {'name': '10 انتصارات 🔥',      'desc': 'كسبت 10 مباريات',            'icon': '🔥'},
    'wins_50':     {'name': 'محترف 👑',             'desc': 'كسبت 50 مباراة',             'icon': '👑'},
    'points_500':  {'name': '500 نقطة 💰',          'desc': 'وصلت 500 نقطة',             'icon': '💰'},
    'points_1000': {'name': 'ألف نقطة 💎',          'desc': 'وصلت 1000 نقطة',            'icon': '💎'},
    'quick_guess': {'name': 'عين حديد 🎯',          'desc': 'خمنت في أقل من 5 محاولات', 'icon': '🎯'},
    'comeback':    {'name': 'عودة من الموت 💪',     'desc': 'فزت وأنت مفلس',             'icon': '💪'},
    'streak_5':    {'name': 'سلسلة نار 🔥🔥',       'desc': '5 انتصارات ورا بعض',        'icon': '🔥'},
    'diamond':     {'name': 'Diamond Player 💎',    'desc': 'وصلت رتبة Diamond',          'icon': '💎'},
    'daily_7':     {'name': 'ملتزم ⭐',             'desc': '7 أيام دخول متتالية',        'icon': '⭐'},
}

class PlayerAchievement(db.Model):
    __tablename__ = 'player_achievements'
    id        = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    key       = db.Column(db.String(50), nullable=False)
    earned_at = db.Column(db.DateTime, default=datetime.utcnow)

    player = db.relationship('Player', foreign_keys=[player_id])
    __table_args__ = (db.UniqueConstraint('player_id', 'key'),)


# ══════════════════════════════
#  FRIENDSHIP
# ══════════════════════════════
class Friendship(db.Model):
    __tablename__ = 'friendships'
    id         = db.Column(db.Integer, primary_key=True)
    sender_id  = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    receiver_id= db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    # pending / accepted / rejected
    status     = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    sender   = db.relationship('Player', foreign_keys=[sender_id])
    receiver = db.relationship('Player', foreign_keys=[receiver_id])

    __table_args__ = (db.UniqueConstraint('sender_id','receiver_id'),)

    @staticmethod
    def are_friends(pid1, pid2):
        return Friendship.query.filter(
            db.or_(
                db.and_(Friendship.sender_id==pid1,   Friendship.receiver_id==pid2),
                db.and_(Friendship.sender_id==pid2,   Friendship.receiver_id==pid1)
            ), Friendship.status=='accepted'
        ).first() is not None

    @staticmethod
    def get_friends(player_id):
        rows = Friendship.query.filter(
            db.or_(
                Friendship.sender_id==player_id,
                Friendship.receiver_id==player_id
            ), Friendship.status=='accepted'
        ).all()
        ids = []
        for r in rows:
            ids.append(r.receiver_id if r.sender_id==player_id else r.sender_id)
        return Player.query.filter(Player.id.in_(ids)).all() if ids else []


# ══════════════════════════════
#  NOTIFICATION
# ══════════════════════════════
class Notification(db.Model):
    __tablename__ = 'notifications'
    id         = db.Column(db.Integer, primary_key=True)
    player_id  = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    # friend_request / friend_accept / friend_reject /
    # room_invite / punishment / chat_msg
    type       = db.Column(db.String(30), nullable=False)
    title      = db.Column(db.String(100), nullable=False)
    body       = db.Column(db.String(255), nullable=False)
    link       = db.Column(db.String(100), nullable=True)   # URL للضغط عليها
    from_id    = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=True)
    is_read    = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    player    = db.relationship('Player', foreign_keys=[player_id])
    from_user = db.relationship('Player', foreign_keys=[from_id])


# ══════════════════════════════
#  CHAT MESSAGE (بين أصدقاء)
# ══════════════════════════════
class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'
    id         = db.Column(db.Integer, primary_key=True)
    sender_id  = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    receiver_id= db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    text       = db.Column(db.String(500), nullable=False)
    is_read    = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sender   = db.relationship('Player', foreign_keys=[sender_id])
    receiver = db.relationship('Player', foreign_keys=[receiver_id])


# ══════════════════════════════
#  GROUP ROOM  (غرف جماعية)
# ══════════════════════════════
class GroupRoom(db.Model):
    __tablename__ = 'group_rooms'
    id           = db.Column(db.Integer, primary_key=True)
    room_code    = db.Column(db.String(10), unique=True, nullable=False)
    host_id      = db.Column(db.Integer, db.ForeignKey('players.id'))
    max_players  = db.Column(db.Integer, default=4)   # 3,4,5,6
    bet_points   = db.Column(db.Integer, default=100)
    status       = db.Column(db.String(20), default='waiting')
    # waiting → secrets → playing → done
    winner_id    = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    host   = db.relationship('Player', foreign_keys=[host_id])
    winner = db.relationship('Player', foreign_keys=[winner_id])


class GroupRoomPlayer(db.Model):
    __tablename__ = 'group_room_players'
    id          = db.Column(db.Integer, primary_key=True)
    room_id     = db.Column(db.Integer, db.ForeignKey('group_rooms.id'))
    player_id   = db.Column(db.Integer, db.ForeignKey('players.id'))
    secret      = db.Column(db.Integer, nullable=True)
    is_alive    = db.Column(db.Boolean, default=True)  # لسه في اللعبة؟
    guesses_used= db.Column(db.Integer, default=0)
    joined_at   = db.Column(db.DateTime, default=datetime.utcnow)

    room   = db.relationship('GroupRoom', foreign_keys=[room_id])
    player = db.relationship('Player',    foreign_keys=[player_id])
    __table_args__ = (db.UniqueConstraint('room_id','player_id'),)


# ══════════════════════════════
#  DAILY CHALLENGE
# ══════════════════════════════
class DailyChallenge(db.Model):
    __tablename__ = 'daily_challenges'
    id           = db.Column(db.Integer, primary_key=True)
    date_str     = db.Column(db.String(10), unique=True)  # YYYY-MM-DD
    target       = db.Column(db.Integer, nullable=False)   # الرقم السري اليومي
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)


class DailyChallengeEntry(db.Model):
    __tablename__ = 'daily_challenge_entries'
    id          = db.Column(db.Integer, primary_key=True)
    challenge_id= db.Column(db.Integer, db.ForeignKey('daily_challenges.id'))
    player_id   = db.Column(db.Integer, db.ForeignKey('players.id'))
    guesses     = db.Column(db.Integer, default=0)
    guess_log   = db.Column(db.Text, nullable=True)  # JSON
    completed   = db.Column(db.Boolean, default=False)
    time_secs   = db.Column(db.Integer, default=0)   # وقت الإنجاز
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    challenge = db.relationship('DailyChallenge', foreign_keys=[challenge_id])
    player    = db.relationship('Player',         foreign_keys=[player_id])
    __table_args__ = (db.UniqueConstraint('challenge_id','player_id'),)


# ══════════════════════════════
#  DAILY CHALLENGE
# ══════════════════════════════
class DailyChallenge(db.Model):
    __tablename__ = 'daily_challenges'
    id          = db.Column(db.Integer, primary_key=True)
    date_str    = db.Column(db.String(10), unique=True, nullable=False)  # YYYY-MM-DD
    secret      = db.Column(db.Integer,   nullable=False)
    created_at  = db.Column(db.DateTime,  default=datetime.utcnow)

class DailyChallengeEntry(db.Model):
    __tablename__ = 'daily_entries'
    id          = db.Column(db.Integer, primary_key=True)
    player_id   = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    date_str    = db.Column(db.String(10), nullable=False)
    guesses     = db.Column(db.Integer, default=0)
    solved      = db.Column(db.Boolean, default=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    player = db.relationship('Player', foreign_keys=[player_id])
    __table_args__ = (db.UniqueConstraint('player_id','date_str'),)