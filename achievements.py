"""achievements.py — م9 + streak + rank achievements"""
from models import db, PlayerAchievement, Match, Player, ACHIEVEMENTS_DEF


def _has(pid, key):
    return PlayerAchievement.query.filter_by(player_id=pid, key=key).first() is not None


def _grant(pid, key):
    if _has(pid, key): return None
    db.session.add(PlayerAchievement(player_id=pid, key=key))
    db.session.commit()
    return ACHIEVEMENTS_DEF.get(key)


def check_achievements(player_id, guesses=0, was_bankrupt=False):
    new = []
    p   = Player.query.filter_by(id=player_id).first()
    if not p: return new

    wins = Match.query.filter_by(winner_id=player_id).count()

    # انتصارات
    for key, mn in [('first_win',1),('wins_10',10),('wins_50',50)]:
        if wins >= mn:
            r = _grant(player_id, key); r and new.append(r)

    # نقاط / رتبة
    for key, mn in [('points_500',500),('points_1000',1000)]:
        if p.points >= mn:
            r = _grant(player_id, key); r and new.append(r)

    # Diamond rank
    if p.points >= 1000:
        r = _grant(player_id, 'diamond'); r and new.append(r)

    # تخمين سريع
    if 0 < guesses <= 5:
        r = _grant(player_id, 'quick_guess'); r and new.append(r)

    # عودة من الموت
    if was_bankrupt:
        r = _grant(player_id, 'comeback'); r and new.append(r)

    # streak
    if (p.win_streak or 0) >= 5:
        r = _grant(player_id, 'streak_5'); r and new.append(r)

    return new


def get_player_achievements(player_id):
    rows = PlayerAchievement.query.filter_by(player_id=player_id).all()
    return [{'key': r.key, **ACHIEVEMENTS_DEF.get(r.key, {}),
             'earned_at': r.earned_at} for r in rows]


def get_player_title(player):
    """م14: لقب اللاعب حسب إنجازاته"""
    if (player.win_streak or 0) >= 5:
        return '🔥 Streak Master'
    wins = Match.query.filter_by(winner_id=player.id).count()
    if wins >= 50: return '🏆 Champion'
    if wins >= 10: return '⚔️ Veteran'
    if player.points >= 1000: return '💎 Diamond Lord'
    if player.points >= 500:  return '🥇 Gold Player'
    return None