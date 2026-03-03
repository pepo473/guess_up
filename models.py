from flask_sqlalchemy import SQLAlchemy

# تجهيز أداة قاعدة البيانات
db = SQLAlchemy()

# تصميم جدول اللاعبين
class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    wins = db.Column(db.Integer, default=0)        # عدد مرات الفوز
    games_played = db.Column(db.Integer, default=0) # عدد مرات اللعب

    def __repr__(self):
        return f"<Player {self.name} - Wins: {self.wins}>"