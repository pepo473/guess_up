"""matches.py — م4+م8: سجل المباريات + Match Replay"""
from models import db, Match
import json


def save_match(player1_id, player2_id, winner_id, bet,
               is_bot=False, guesses=0, guess_log=None):
    """
    player1_id = الفايز
    player2_id = الخاسر (None لو بوت)
    guess_log  = list of {guesser, guess, result}
    """
    log_json = json.dumps(guess_log, ensure_ascii=False) if guess_log else None
    m = Match(
        player1_id=player1_id,
        player2_id=player2_id,
        winner_id=winner_id,
        bet=bet,
        is_bot=is_bot,
        guesses=guesses,
        guess_log=log_json
    )
    db.session.add(m)
    db.session.commit()
    return m


def get_player_matches(player_id, limit=20):
    return (Match.query
            .filter(
                (Match.player1_id == player_id) |
                (Match.player2_id == player_id)
            )
            .order_by(Match.created_at.desc())
            .limit(limit).all())


def get_match_replay(match_id):
    """م8: يرجع الـ log بتاع مباراة معينة"""
    m = Match.query.filter_by(id=match_id).first()
    if not m or not m.guess_log:
        return []
    try:
        return json.loads(m.guess_log)
    except Exception:
        return []


def get_player_stats(player_id):
    total  = Match.query.filter(
        (Match.player1_id == player_id) |
        (Match.player2_id == player_id)
    ).count()
    wins   = Match.query.filter_by(winner_id=player_id).count()
    losses = total - wins
    return {
        'total':    total,
        'wins':     wins,
        'losses':   losses,
        'win_rate': min(round(wins / total * 100), 100) if total else 0
    }