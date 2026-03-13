from models import db, Player
import random

STARTING_POINTS      = 100
MIN_POINTS_TO_PLAY   = 100
BANKRUPT_MODE_POINTS = 50
DAILY_REWARD         = 25    # م2
XP_PER_WIN           = 30   # م12
XP_PER_LOSS          = 10

# ── م6: Random Events ────────────────────────────────────────────────────────
RANDOM_EVENTS = {
    'double_points': {'name': '⚡ ضربة نار!',   'desc': 'النقاط مضاعفة الجولة دي!',   'multiplier': 2},
    'half_bet':      {'name': '🛡️ جولة آمنة',   'desc': 'الخسارة النصف بس!',          'multiplier': 0.5},
    'bonus_50':      {'name': '🎁 بونص!',         'desc': 'الفايز بياخد 50 نقطة إضافية!', 'bonus': 50},
    None:            {'name': '',                 'desc': '',                            'multiplier': 1},
}

def roll_random_event():
    """م6: بتشتغل مرة كل 5 مباريات تقريباً"""
    if random.random() < 0.25:
        return random.choice(['double_points', 'half_bet', 'bonus_50'])
    return None

def get_rank(points):
    """م1"""
    if points >= 1000: return {'name': 'Diamond', 'icon': '💎', 'color': '#60a5fa', 'next': None,   'next_pts': None}
    if points >= 500:  return {'name': 'Gold',    'icon': '🥇', 'color': '#fbbf24', 'next': 'Diamond','next_pts': 1000}
    if points >= 200:  return {'name': 'Silver',  'icon': '⚪', 'color': '#94a3b8', 'next': 'Gold',  'next_pts': 500}
    return                    {'name': 'Bronze',  'icon': '🟤', 'color': '#b45309', 'next': 'Silver','next_pts': 200}

def can_join_room(player, bet_points):
    return player.points >= bet_points

def is_bankrupt(player):
    return player.points <= 0

def transfer_points(winner_id, loser_id, bet, event=None):
    """م1+م4+م6: تحويل نقاط مع Streak و Events"""
    winner = Player.query.filter_by(id=winner_id).first()
    loser  = Player.query.filter_by(id=loser_id).first()
    if not winner or not loser:
        return {'winner_pts': 0, 'loser_pts': 0, 'bonus': 0}

    actual_bet   = bet
    streak_bonus = 0
    event_bonus  = 0

    # م6: Random Event
    if event == 'double_points':
        actual_bet = bet * 2
    elif event == 'half_bet':
        actual_bet = max(bet // 2, 1)
    elif event == 'bonus_50':
        event_bonus = 50

    # م4: Streak Bonus
    winner.win_streak  += 1
    winner.best_streak  = max(winner.best_streak, winner.win_streak)
    if winner.win_streak >= 5:
        streak_bonus = 50
    elif winner.win_streak >= 3:
        streak_bonus = 25
    loser.win_streak = 0   # كسر الـ streak

    # تحويل النقاط
    winner.points += actual_bet + streak_bonus + event_bonus
    loser.points  -= actual_bet
    if loser.points < 0:
        loser.points = 0

    # م12: XP
    winner.xp = (winner.xp or 0) + XP_PER_WIN
    loser.xp  = (loser.xp  or 0) + XP_PER_LOSS

    db.session.commit()
    return {
        'winner_pts':   winner.points,
        'loser_pts':    loser.points,
        'streak_bonus': streak_bonus,
        'event_bonus':  event_bonus,
        'actual_bet':   actual_bet,
        'streak':       winner.win_streak,
    }

def award_bankrupt_mode_points(winner_id):
    winner = Player.query.filter_by(id=winner_id).first()
    if not winner: return
    winner.win_streak  += 1
    winner.best_streak  = max(winner.best_streak, winner.win_streak)
    winner.points      += BANKRUPT_MODE_POINTS
    winner.xp           = (winner.xp or 0) + XP_PER_WIN
    db.session.commit()

def claim_daily_reward(player_id):
    """م2: Daily Reward"""
    from datetime import datetime, timedelta
    player = Player.query.filter_by(id=player_id).first()
    if not player: return None, 'مش موجود'
    now = datetime.utcnow()
    if player.last_daily_reward:
        diff = now - player.last_daily_reward
        if diff.total_seconds() < 86400:
            remaining = 86400 - diff.total_seconds()
            hrs  = int(remaining // 3600)
            mins = int((remaining % 3600) // 60)
            return None, f'المكافأة جاية بعد {hrs}س {mins}د'
    player.points            += DAILY_REWARD
    player.xp                 = (player.xp or 0) + 10
    player.last_daily_reward  = now
    db.session.commit()
    return DAILY_REWARD, None

def get_leaderboard(limit=20):
    return Player.query.order_by(Player.points.desc()).limit(limit).all()

def can_claim_daily(player):
    """هل ممكن يطلب المكافأة دلوقتي؟"""
    from datetime import datetime, timedelta
    if not player.last_daily_reward: return True
    return (datetime.utcnow() - player.last_daily_reward).total_seconds() >= 86400