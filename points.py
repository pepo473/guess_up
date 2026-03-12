from models import db, Player

STARTING_POINTS      = 100
MIN_POINTS_TO_PLAY   = 100
BANKRUPT_MODE_POINTS = 50


def can_join_room(player, bet_points):
    return player.points >= bet_points


def is_bankrupt(player):
    return player.points <= 0


def transfer_points(winner_id, loser_id, amount):
    winner = Player.query.filter_by(id=winner_id).first()
    loser  = Player.query.filter_by(id=loser_id).first()
    if not winner or not loser:
        return False
    winner.points += amount
    loser.points  -= amount
    if loser.points < 0:
        loser.points = 0
    db.session.commit()
    return True


def award_bankrupt_mode_points(winner_id):
    winner = Player.query.filter_by(id=winner_id).first()
    if not winner:
        return False
    winner.points += BANKRUPT_MODE_POINTS
    db.session.commit()
    return True


def get_leaderboard(limit=10):
    return Player.query.order_by(Player.points.desc()).limit(limit).all()