from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room as sio_join_room
from models import db, Player, Room, Punishment, Match, Friendship, Notification, ChatMessage, DailyChallenge, DailyChallengeEntry, GroupRoom, GroupRoomPlayer
from auth import register_player, login_player, get_player_by_id, is_logged_in
from rooms import create_room, join_room, get_room
from points import (transfer_points, award_bankrupt_mode_points, is_bankrupt, get_rank,
                    get_leaderboard, claim_daily_reward, can_claim_daily,
                    roll_random_event, RANDOM_EVENTS as EVENTS_MAP, XP_PER_WIN, XP_PER_LOSS)
from bot import create_bot_session, get_bot_session, remove_bot_session
from matches import save_match, get_player_matches, get_player_stats, get_match_replay
from achievements import check_achievements, get_player_achievements, get_player_title
import os, uuid, json
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['SECRET_KEY']                     = 'guess_up_secret_2024'
app.config['SQLALCHEMY_DATABASE_URI']        = 'sqlite:///guess_up.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER']                  = os.path.join('static', 'avatars')
app.config['MAX_CONTENT_LENGTH']             = 2 * 1024 * 1024
ALLOWED_EXT = {'png','jpg','jpeg','gif','webp'}

db.init_app(app)
socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)


# ════════════════════════════════════════════════════════
# DAILY CHALLENGE HELPERS
# ════════════════════════════════════════════════════════

def get_today_str():
    from datetime import datetime as _dt
    return _dt.utcnow().strftime('%Y-%m-%d')

def get_or_create_daily():
    """يجيب أو ينشئ تحدي اليوم"""
    today = get_today_str()
    ch = DailyChallenge.query.filter_by(date_str=today).first()
    if not ch:
        import random as _r
        ch = DailyChallenge(date_str=today, target=_r.randint(1,1000))
        db.session.add(ch)
        db.session.commit()
    return ch

@app.context_processor
def inject_player():
    def get_player():
        pid=session.get('player_id')
        return Player.query.filter_by(id=pid).first() if pid else None
    return dict(get_player=get_player)

# ── in-memory ────────────────────────────────────────────────────────────────
room_secrets     = {}   # room_code -> {player_id: secret}
room_players     = {}   # room_code -> {player_id: name}
room_guesses     = {}   # room_code -> {player_id: count}
room_guess_logs  = {}   # room_code -> [entries]   م8
room_spectators  = {}   # room_code -> {sid: name} م7
room_timers      = {}   # room_code -> {player_id: remaining_seconds}  Speed mode
group_scores     = {}   # room_code -> {player_id: guesses_count}      Group rooms
sid_to_room      = {}
sid_to_player    = {}
player_status    = {}   # player_id -> 'online'/'in_game'/'waiting'  م5

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def allowed_file(fn):
    return '.' in fn and fn.rsplit('.',1)[1].lower() in ALLOWED_EXT



# ════════════════════════════════════════════════════════
# HELPERS — Notifications
# ════════════════════════════════════════════════════════

def send_notification(player_id, ntype, title, body, from_id=None, link=None):
    """يحفظ notification ويبعته real-time لو اللاعب متصل"""
    from datetime import datetime as _dt
    n = Notification(
        player_id=player_id, type=ntype,
        title=title, body=body,
        from_id=from_id, link=link,
        created_at=_dt.utcnow()
    )
    db.session.add(n)
    db.session.commit()
    # بعته real-time عبر SocketIO
    socketio.emit('new_notification', {
        'id':    n.id,
        'type':  ntype,
        'title': title,
        'body':  body,
        'link':  link or '',
        'from_id': from_id
    }, room=f'user_{player_id}')
    return n

def finish_game(room_code, winner_id, loser_id, bet, guesses=0, is_bot=False):
    room = get_room(room_code)
    if not room: return [], {}

    was_bankrupt = is_bankrupt(get_player_by_id(winner_id)) if winner_id else False
    room.winner_id = winner_id
    room.status    = 'done'
    db.session.commit()

    transfer_info = {}
    if not is_bot:
        if room.is_bankrupt_mode:
            award_bankrupt_mode_points(winner_id)
        else:
            transfer_info = transfer_points(winner_id, loser_id, bet,
                                            event=room.random_event)
        # Speed mode bonus — نضيف نقاط إضافية للفايز
        mode_bonus = 0
        if hasattr(room, 'mode'):
            if room.mode == 'speed_20': mode_bonus = 20
            elif room.mode == 'speed_10': mode_bonus = 50
            elif room.mode == 'double':
                mode_bonus = bet  # ضعف الرهان مضاف
            if mode_bonus > 0:
                from models import Player as _P
                w = _P.query.filter_by(id=winner_id).first()
                if w:
                    w.points += mode_bonus
                    db.session.commit()
                    transfer_info['mode_bonus'] = mode_bonus

    # م8: حفظ الـ log
    log = room_guess_logs.pop(room_code, [])
    save_match(winner_id, loser_id, winner_id, bet, is_bot, guesses, log)

    new_ach = check_achievements(winner_id, guesses, was_bankrupt)

    # م5: تحديث status
    player_status.pop(winner_id, None)
    if loser_id: player_status.pop(loser_id, None)

    # تنظيف
    room_secrets.pop(room_code, None)
    room_players.pop(room_code, None)
    room_guesses.pop(room_code, None)
    room_spectators.pop(room_code, None)

    # ── notification للخاسر بالعقاب لو الفائز كتبه لاحقاً ──
    # (الـ punishment route هيتكلم بعدين)
    return new_ach or [], transfer_info


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    if is_logged_in(session): return redirect(url_for('lobby'))
    return render_template('index.html')


@app.route('/register', methods=['POST'])
def register():
    name, pw = request.form.get('name','').strip(), request.form.get('password','')
    player, err = register_player(name, pw)
    if err: return render_template('index.html', error=err, tab='register', reg_name=name)
    session['player_id'] = player.id
    session['player_name'] = player.player_name
    return redirect(url_for('lobby'))


@app.route('/login', methods=['POST'])
def login():
    name, pw = request.form.get('name','').strip(), request.form.get('password','')
    player, err = login_player(name, pw)
    if err: return render_template('index.html', error=err, tab='login', login_name=name)
    # تحقق من الحظر والحذف بـ raw SQL عشان الـ columns ممكن تكون مش موجودة لسه
    try:
        import sqlite3 as _sq
        from datetime import datetime as _dt
        conn = _sq.connect(os.path.join(app.instance_path, 'guess_up.db'))
        row  = conn.execute(
            "SELECT is_deleted, is_banned, ban_until, ban_reason FROM players WHERE id=?",
            (player.id,)
        ).fetchone()
        conn.close()
        if row:
            is_deleted, is_banned, ban_until, ban_reason = row
            if is_deleted:
                return render_template('index.html', error='❌ هذا الحساب تم حذفه',
                                       tab='login', login_name=name)
            if is_banned:
                if ban_until:
                    try:
                        until_dt = _dt.strptime(ban_until, '%Y-%m-%d %H:%M:%S')
                        if _dt.utcnow() < until_dt:
                            msg = f'🚫 حسابك محظور حتى {until_dt.strftime("%Y/%m/%d %H:%M")}'
                            if ban_reason: msg += f' — السبب: {ban_reason}'
                            return render_template('index.html', error=msg,
                                                   tab='login', login_name=name)
                        # الحظر انتهى — ارفعه تلقائياً
                        c2 = _sq.connect(os.path.join(app.instance_path, 'guess_up.db'))
                        c2.execute("UPDATE players SET is_banned=0 WHERE id=?", (player.id,))
                        c2.commit(); c2.close()
                    except Exception:
                        pass
                else:
                    # حظر دائم
                    msg = '🚫 حسابك محظور بشكل دائم'
                    if ban_reason: msg += f' — السبب: {ban_reason}'
                    return render_template('index.html', error=msg,
                                           tab='login', login_name=name)
    except Exception:
        pass  # لو في أي خطأ نسمح بالدخول عادي
    session['player_id'] = player.id
    session['player_name'] = player.player_name
    return redirect(url_for('lobby'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/lobby')
def lobby():
    if not is_logged_in(session): return redirect(url_for('index'))
    player = get_player_by_id(session['player_id'])
    if not player: session.clear(); return redirect(url_for('index'))
    public_rooms = (Room.query
                    .filter_by(is_public=True, status='waiting')
                    .filter(Room.player2_id == None, Room.is_bot_game == False)
                    .order_by(Room.created_at.desc()).limit(10).all())
    daily_ready  = can_claim_daily(player)
    # التحدي اليومي
    from datetime import datetime as _dt
    today_str = _dt.utcnow().strftime('%Y-%m-%d')
    daily_ch  = DailyChallenge.query.filter_by(date_str=today_str).first()
    daily_entry = None
    if daily_ch:
        daily_entry = DailyChallengeEntry.query.filter_by(
            challenge_id=daily_ch.id, player_id=player.id).first()
    return render_template('lobby.html', player=player,
                           bankrupt=is_bankrupt(player),
                           public_rooms=public_rooms,
                           daily_ready=daily_ready,
                           daily_entry=daily_entry,
                           title=get_player_title(player))


# م2: Daily Reward
@app.route('/daily_reward', methods=['POST'])
def daily_reward():
    if not is_logged_in(session): return jsonify({'error':'مش مسجل دخول'}), 401
    pts, err = claim_daily_reward(session['player_id'])
    if err: return jsonify({'error': err}), 400
    return jsonify({'ok': True, 'points': pts})


@app.route('/create_room', methods=['POST'])
def create_room_route():
    if not is_logged_in(session): return jsonify({'error':'مش مسجل دخول'}), 401
    bet         = int(request.form.get('bet_points', 100))
    is_bm       = request.form.get('bankrupt_mode') == '1'
    is_bot      = request.form.get('bot_game') == '1'
    is_public   = request.form.get('is_public') == '1'
    max_players = int(request.form.get('max_players', 2))
    mode = request.form.get('mode', 'classic')
    # تحديد وقت التايمر حسب المود
    MODE_TIMERS = {
        'speed_20': 20,
        'speed_10': 10,
    }
    timer_secs = MODE_TIMERS.get(mode, 0)

    room, err = create_room(session['player_id'], bet, is_bm, is_bot)
    if err: return jsonify({'error': err}), 400

    # م6: Random Event لكل غرفة
    room.random_event    = roll_random_event()
    room.max_players     = max(2, min(6, max_players))
    room.mode            = mode
    room.timer_seconds   = timer_secs
    if is_public and not is_bot: room.is_public = True
    # Group room: رقم سري واحد للكل
    if room.max_players > 2:
        import random as _r
        room.group_secret = _r.randint(1, 1000)
    db.session.commit()

    if is_bot: create_bot_session(room.room_code)
    player_status[session['player_id']] = 'waiting'  # م5

    # ── بعت notification لأصدقاء اللاعب لو الغرفة عامة ──
    if is_public and not is_bot:
        creator = get_player_by_id(session['player_id'])
        friends = Friendship.get_friends(session['player_id'])
        for fr in friends:
            send_notification(
                player_id=fr.id,
                ntype='room_invite',
                title=f'🎮 {creator.player_name} عامل غرفة!',
                body=f'صاحبك {creator.player_name} عامل غرفة برهان {room.bet_points} نقطة — خش العب معاه!',
                from_id=session['player_id'],
                link=f'/room/{room.room_code}'
            )

    return jsonify({
        'room_code': room.room_code,
        'event':     room.random_event,
        'event_info': EVENTS_MAP.get(room.random_event, {})
    })


@app.route('/join_room_route', methods=['POST'])
def join_room_route():
    if not is_logged_in(session): return jsonify({'error':'مش مسجل دخول'}), 401
    code = request.form.get('room_code','').strip().upper()
    pid  = session['player_id']

    room, err = join_room(code, pid)

    if err:
        # الغرفة ممتلئة؟ → دخول كـ spectator مش error!
        existing_room = get_room(code)
        if existing_room and existing_room.player2_id and err == 'الغرفة ممتلية':
            return jsonify({'room_code': existing_room.room_code, 'spectator': True})
        return jsonify({'error': err}), 400

    player_status[pid] = 'in_game'
    return jsonify({'room_code': room.room_code, 'spectator': False})


@app.route('/room/<room_code>')
def game_room(room_code):
    if not is_logged_in(session): return redirect(url_for('index'))
    room   = get_room(room_code)
    if not room: return redirect(url_for('lobby'))
    player = get_player_by_id(session['player_id'])
    if not player: return redirect(url_for('index'))
    event_info = EVENTS_MAP.get(room.random_event, {})
    return render_template('room.html', room=room, player=player,
                           event_info=event_info)


@app.route('/punishment/<room_code>', methods=['POST'])
def punishment(room_code):
    if not is_logged_in(session): return jsonify({'error':'مش مسجل دخول'}), 401
    room = get_room(room_code)
    if not room or room.winner_id != session['player_id']:
        return jsonify({'error':'مش مسموح'}), 403
    text     = request.form.get('punishment_text','').strip()
    whatsapp = request.form.get('whatsapp','').strip()
    if not text or not whatsapp: return jsonify({'error':'اكتب العقاب والواتساب'}), 400
    loser_id = room.player1_id if room.winner_id == room.player2_id else room.player2_id
    db.session.add(Punishment(room_id=room.id, winner_id=room.winner_id,
                              loser_id=loser_id, punishment_text=text, whatsapp_number=whatsapp))
    db.session.commit()
    socketio.emit('punishment_received', {'text': text, 'whatsapp': whatsapp}, room=room_code)
    # ── notification للخاسر يوصله العقاب حتى لو خرج ──
    winner = get_player_by_id(room.winner_id)
    send_notification(
        player_id=loser_id,
        ntype='punishment',
        title=f'😈 عقاب من {winner.player_name if winner else "الفائز"}',
        body=text[:100],
        from_id=room.winner_id,
        link=f'/room/{room_code}'
    )
    return jsonify({'ok': True})


@app.route('/leaderboard')
def leaderboard():
    all_time = get_leaderboard(20)
    week_ago = datetime.utcnow() - timedelta(days=7)
    today    = datetime.utcnow() - timedelta(hours=24)

    def weekly_board(since, lim=20):
        import sqlite3 as _sq
        try:
            db_path = os.path.join(app.instance_path, 'guess_up.db')
            conn = _sq.connect(db_path)
            # استثني المحذوفين والمحظورين من الـ leaderboard
            raw = conn.execute("""
                SELECT m.winner_id,
                       SUM(m.bet) as earned,
                       COUNT(m.id) as wins
                FROM matches m
                JOIN players p ON p.id = m.winner_id
                WHERE m.created_at >= ?
                  AND m.winner_id IS NOT NULL
                  AND (p.is_deleted = 0 OR p.is_deleted IS NULL)
                  AND (p.is_banned  = 0 OR p.is_banned  IS NULL)
                GROUP BY m.winner_id
                ORDER BY earned DESC
                LIMIT ?
            """, (since.strftime('%Y-%m-%d %H:%M:%S'), lim)).fetchall()
            conn.close()
            result = []
            for r in raw:
                p = get_player_by_id(r[0])
                if p: result.append({'player': p, 'earned': r[1], 'wins': r[2]})
            return result
        except Exception:
            # fallback
            rows = (db.session.query(Match.winner_id,
                        db.func.sum(Match.bet).label('earned'),
                        db.func.count(Match.id).label('wins'))
                    .filter(Match.created_at >= since, Match.winner_id != None)
                    .group_by(Match.winner_id)
                    .order_by(db.func.sum(Match.bet).desc()).limit(lim).all())
            return [{'player': get_player_by_id(r.winner_id),
                     'earned': r.earned, 'wins': r.wins}
                    for r in rows if get_player_by_id(r.winner_id)]

    return render_template('leaderboard.html',
                           players=all_time,
                           weekly=weekly_board(week_ago),
                           today=weekly_board(today))


@app.route('/player/<int:pid>')
def profile(pid):
    if not is_logged_in(session): return redirect(url_for('index'))
    target = get_player_by_id(pid)
    if not target: return redirect(url_for('leaderboard'))
    matches = get_player_matches(pid, 15)
    # حالة الصداقة بين الزائر والـ target
    me_id = session.get('player_id')
    friendship_status = 'none'
    if me_id and me_id != pid:
        fr = Friendship.query.filter(
            db.or_(
                db.and_(Friendship.sender_id==me_id,  Friendship.receiver_id==pid),
                db.and_(Friendship.sender_id==pid,    Friendship.receiver_id==me_id)
            )
        ).first()
        if fr:
            if fr.status == 'accepted':
                friendship_status = 'accepted'
            elif fr.status == 'pending' and fr.sender_id == me_id:
                friendship_status = 'pending_sent'
            elif fr.status == 'pending' and fr.receiver_id == me_id:
                friendship_status = 'pending_received'

    return render_template('profile.html',
        target            = target,
        matches           = matches,
        stats             = get_player_stats(pid),
        achievements      = get_player_achievements(pid),
        title             = get_player_title(target),
        is_me             = (me_id == pid),
        friendship_status = friendship_status)


# م8: Match Replay API
@app.route('/api/match/<int:mid>/replay')
def match_replay(mid):
    if not is_logged_in(session): return jsonify({'error':'مش مسجل دخول'}), 401
    return jsonify({'log': get_match_replay(mid)})


@app.route('/upload_avatar', methods=['POST'])
def upload_avatar():
    if not is_logged_in(session): return jsonify({'error':'مش مسجل دخول'}), 401
    f = request.files.get('avatar')
    if not f or not allowed_file(f.filename): return jsonify({'error':'ملف مش صالح'}), 400
    ext      = f.filename.rsplit('.',1)[1].lower()
    filename = f"avatar_{session['player_id']}_{uuid.uuid4().hex[:8]}.{ext}"
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    f.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    p = get_player_by_id(session['player_id'])
    p.avatar = filename; db.session.commit()
    return jsonify({'ok': True, 'avatar': filename})


# ══════════════════════════════════════════════════════════════════════════════
# SOCKET.IO
# ══════════════════════════════════════════════════════════════════════════════

@socketio.on('connect')
def on_connect():
    pid = session.get('player_id')
    if pid:
        sid_to_player[request.sid] = pid
        player_status[pid] = 'online'  # م5


@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    pid = sid_to_player.pop(sid, None)
    rc  = sid_to_room.pop(sid, None)
    player_status.pop(pid, None)

    # هل كان متفرج؟
    if rc and pid in room_spectators.get(rc, {}):
        room_spectators[rc].pop(pid, None)
        _broadcast_spectators(rc)
        return

    if not pid or not rc: return
    room_players.get(rc, {}).pop(pid, None)
    room = get_room(rc)
    if not room or room.status == 'done': return

    remaining = room_players.get(rc, {})
    if remaining:
        opp_id   = next(iter(remaining))
        opp_name = remaining[opp_id]
        opp_p    = get_player_by_id(opp_id)
        finish_game(rc, opp_id, pid, room.bet_points)
        socketio.emit('game_over', {
            'winner': opp_name, 'winner_id': opp_id, 'loser_id': pid,
            'message': f'🏆 {opp_name} كسب! الخصم غادر اللعبة',
            'winner_new_points': opp_p.points if opp_p else 0,
            'loser_new_points': 0, 'bet': room.bet_points, 'room_code': rc,
            'no_punishment': False, 'vs_bot': False,
            'opponent_left': True, 'achievements': [],
            'streak': getattr(opp_p, 'win_streak', 0),
            'streak_bonus': 0, 'guess_log': []
        }, room=rc)


def _broadcast_spectators(rc):
    count = len(room_spectators.get(rc, {}))
    socketio.emit('spectators_update', {'count': count}, room=rc)


@socketio.on('join_game_room')
def on_join(data):
    room_code   = data.get('room_code')
    player_id   = session.get('player_id')
    player_name = session.get('player_name')
    if not room_code or not player_id: return

    room = get_room(room_code)
    if not room: return

    # م7: لو الغرفة ممتلئة وهو مش لاعب → Spectator
    is_player = (room.player1_id == player_id or room.player2_id == player_id)
    is_full   = room.player1_id and room.player2_id

    if is_full and not is_player:
        sio_join_room(room_code)
        sid_to_room[request.sid] = room_code
        if room_code not in room_spectators:
            room_spectators[room_code] = {}
        room_spectators[room_code][player_id] = player_name
        emit('spectator_mode', {
            'message': '👁 أنت بتتفرج على المباراة',
            'count': len(room_spectators[room_code])
        }, to=request.sid)
        _broadcast_spectators(room_code)
        return

    sio_join_room(room_code)
    sid_to_room[request.sid] = room_code
    player_status[player_id] = 'in_game'

    if room_code not in room_players:
        room_players[room_code]    = {}
        room_guesses[room_code]    = {}
        room_guess_logs[room_code] = []

    room_players[room_code][player_id] = player_name
    room_guesses[room_code][player_id] = 0

    # ── بوت ──────────────────────────────────────────────────────────────
    if room.is_bot_game:
        if not get_bot_session(room_code): create_bot_session(room_code)
        emit('player_joined', {'player_count':1,'room_full':True,
                               'opponent_name':'🤖 الكمبيوتر'}, to=request.sid)
        emit('game_started', {
            'message': '🤖 الكمبيوتر اختار رقم — ابدأ تخمن!',
            'vs_bot': True, 'opponent_name': '🤖 الكمبيوتر',
            'event': None, 'event_info': {},
            'timer_seconds': getattr(room, 'timer_seconds', 0),
            'mode': getattr(room, 'mode', 'classic')
        }, to=request.sid)
        return

    # ── لاعبين ────────────────────────────────────────────────────────────
    player_count = len(room_players[room_code])
    max_p        = room.max_players or 2
    is_group     = max_p > 2

    emit('player_joined', {
        'player_name': player_name, 'player_count': player_count,
        'room_full': player_count >= max_p,
        'max_players': max_p, 'is_group': is_group
    }, room=room_code)

    if player_count >= max_p:
        event_info = EVENTS_MAP.get(room.random_event, {})

        if is_group:
            # ── Group Room: كل الناس بتخمن رقم واحد ──
            room.status = 'playing'
            db.session.commit()
            group_scores[room_code] = {pid: 0 for pid in room_players[room_code]}
            players_list = [{'id': pid, 'name': name}
                           for pid, name in room_players[room_code].items()]
            socketio.emit('group_game_started', {
                'message':     f'🎮 اللعبة بدأت! {max_p} لاعبين',
                'players':      players_list,
                'timer':        room.timer_seconds,
                'event':        room.random_event,
                'event_info':   event_info,
                'mode':         room.mode or 'classic'
            }, room=room_code)
        else:
            # ── Classic 1v1 ──
            room_secrets.pop(room_code, None)
            plist = list(room_players[room_code].items())
            for s, p in sid_to_player.items():
                if p in room_players.get(room_code, {}):
                    opp = next((n for i,n in plist if i != p), 'الخصم')
                    socketio.emit('room_ready', {
                        'message':      '✅ اللاعبين جاهزين! اختار رقمك السري',
                        'opponent_name': opp,
                        'event':         room.random_event,
                        'event_info':    event_info
                    }, to=s)


@socketio.on('set_secret')
def on_set_secret(data):
    rc, secret, pid = data.get('room_code'), data.get('secret'), session.get('player_id')
    if not rc or secret is None or not pid: return
    if rc not in room_secrets: room_secrets[rc] = {}
    room_secrets[rc][pid] = int(secret)
    if len(room_secrets[rc]) >= 2:
        room = get_room(rc)
        if room: room.status = 'playing'; db.session.commit()
        emit('game_started', {'message':'🎮 اللعبة بدأت!'}, room=rc)
    else:
        emit('secret_set', {'message':'✅ تمام! استنى صاحبك...'}, to=request.sid)


@socketio.on('make_guess')
def on_guess(data):
    rc, guess, gid, gname = (
        data.get('room_code'), data.get('guess'),
        session.get('player_id'), session.get('player_name')
    )
    if not rc or guess is None or not gid: return
    room = get_room(rc)
    if not room: return

    room_guesses.setdefault(rc, {})
    room_guesses[rc][gid] = room_guesses[rc].get(gid, 0) + 1
    total_g = room_guesses[rc][gid]

    # ── بوت ──────────────────────────────────────────────────────────────
    if room.is_bot_game:
        bot = get_bot_session(rc)
        if not bot:
            emit('error_msg', {'message':'⚠️ مشكلة في البوت'}, to=request.sid); return
        result = bot.respond_to_guess(int(guess))

        # م8: log
        room_guess_logs.setdefault(rc, [])
        room_guess_logs[rc].append({'guesser': gname, 'guess': int(guess),
                                    'result': result.get('result','')})

        emit('guess_result', {'guesser':gname,'guess':int(guess),**result,
                              'total_guesses': total_g}, to=request.sid)
        if result['correct']:
            new_ach, ti = finish_game(rc, gid, None, 0, total_g, is_bot=True)
            wp = get_player_by_id(gid)
            emit('game_over', {
                'winner':gname,'winner_id':gid,'loser_id':None,
                'message':f'🏆 كسبت في {total_g} محاولة!',
                'vs_bot':True,'no_punishment':True,
                'winner_new_points': wp.points if wp else None,
                'loser_new_points': None,
                'streak': getattr(wp,'win_streak',0),
                'streak_bonus': 0,
                'xp_gained': XP_PER_WIN,
                'achievements': new_ach,
                'guess_log': room_guess_logs.get(rc,[])
            }, to=request.sid)
        return

    # ── لاعبين ────────────────────────────────────────────────────────────
    opp_id = room.player2_id if gid == room.player1_id else room.player1_id
    if not opp_id:
        emit('error_msg',{'message':'⚠️ اللاعب الثاني لسه مدخلش'},to=request.sid); return
    secret = room_secrets.get(rc,{}).get(opp_id)
    if secret is None:
        emit('error_msg',{'message':'⚠️ الخصم لسه محطش رقمه'},to=request.sid); return

    g = int(guess)
    if g < 1 or g > 1000:
        emit('error_msg',{'message':'⚠️ الرقم بين 1 و 1000'},to=request.sid); return

    if   g < secret: res = {'result':'higher','message':'⬆️ أعلى من كده!','correct':False}
    elif g > secret: res = {'result':'lower', 'message':'⬇️ أقل من كده!', 'correct':False}
    else:            res = {'result':'correct','message':'🎉 صح! ده الرقم!','correct':True}

    # م8: log
    room_guess_logs.setdefault(rc,[])
    room_guess_logs[rc].append({'guesser':gname,'guess':g,'result':res['result']})

    emit('guess_result', {'guesser':gname,'guess':g,**res,
                          'total_guesses':total_g}, room=rc)

    if res['correct']:
        bet        = room.bet_points
        guess_log  = list(room_guess_logs.get(rc,[]))
        new_ach, ti = finish_game(rc, gid, opp_id, bet, total_g)
        wp = get_player_by_id(gid)
        lp = get_player_by_id(opp_id)

        streak_bonus = ti.get('streak_bonus',0)
        event_bonus  = ti.get('event_bonus',0)
        actual_bet   = ti.get('actual_bet', bet)
        streak       = ti.get('streak', getattr(wp,'win_streak',0))

        emit('game_over', {
            'winner':gname,'winner_id':gid,'loser_id':opp_id,
            'message':f'🏆 {gname} كسب {actual_bet} نقطة!',
            'winner_new_points': wp.points if wp else 0,
            'loser_new_points':  lp.points if lp else 0,
            'bet': bet, 'actual_bet': actual_bet,
            'streak': streak, 'streak_bonus': streak_bonus,
            'event_bonus': event_bonus,
            'mode_bonus': ti.get('mode_bonus', 0),
            'xp_gained': XP_PER_WIN,
            'room_code': rc,
            'no_punishment': False, 'vs_bot': False,
            'achievements': new_ach,
            'guess_log': guess_log
        }, room=rc)




@socketio.on('group_guess')
def on_group_guess(data):
    """تخمين في Group Room — كل الناس بتخمن رقم واحد"""
    rc    = data.get('room_code')
    guess = data.get('guess')
    gid   = session.get('player_id')
    gname = session.get('player_name')
    if not rc or guess is None or not gid: return

    room = get_room(rc)
    if not room or room.status == 'done': return

    secret = room.group_secret
    if secret is None: return

    # عدّ محاولات هذا اللاعب
    if rc not in group_scores: group_scores[rc] = {}
    group_scores[rc][gid] = group_scores[rc].get(gid, 0) + 1
    total_g = group_scores[rc][gid]

    g = int(guess)
    if   g < secret: result = {'result':'higher','message':'⬆️ أعلى!','correct':False}
    elif g > secret: result = {'result':'lower', 'message':'⬇️ أقل!', 'correct':False}
    else:            result = {'result':'correct','message':'✅ صح!','correct':True}

    # broadcast لكل الغرفة
    socketio.emit('group_guess_result', {
        'guesser':      gname,
        'guesser_id':   gid,
        'guess':        g,
        'result':       result['result'],
        'message':      result['message'],
        'total_guesses':total_g
    }, room=rc)

    if result['correct']:
        # الفائز بياخد نقاط من كل البقية
        room.winner_id = gid
        room.status    = 'done'
        db.session.commit()

        # تحويل النقاط
        bet     = room.bet_points
        players = list(room_players.get(rc, {}).items())
        for pid, pname in players:
            if pid != gid:
                p = Player.query.get(pid)
                if p and p.points >= bet:
                    p.points -= bet
        winner = Player.query.get(gid)
        total_pot = bet * (len(players) - 1)
        if winner:
            winner.points += total_pot
            winner.xp = (winner.xp or 0) + 30
        db.session.commit()

        # جيب scores النهائية
        final_scores = []
        for pid, pname in players:
            final_scores.append({
                'id': pid, 'name': pname,
                'guesses': group_scores.get(rc,{}).get(pid, 0),
                'won': pid == gid
            })
        final_scores.sort(key=lambda x: (0 if x['won'] else 1, x['guesses']))

        socketio.emit('group_game_over', {
            'winner_id':   gid,
            'winner_name': gname,
            'secret':      secret,
            'total_guesses':total_g,
            'pot':         total_pot,
            'scores':      final_scores,
            'winner_pts':  winner.points if winner else 0
        }, room=rc)

        # تنظيف
        room_players.pop(rc, None)
        room_guesses.pop(rc, None)
        group_scores.pop(rc, None)

# م5: Chat
@socketio.on('chat_message')
def on_chat(data):
    rc  = data.get('room_code')
    msg = data.get('message','').strip()[:200]
    if not rc or not msg: return
    emit('chat_message', {
        'sender': session.get('player_name','؟'), 'message': msg
    }, room=rc)



# ══ Admin Panel ══════════════════════════════════════════════
ADMIN_SECRET = 'guessup_admin_2024'   # ← غيّره لو حابب

@app.route('/admin')
def admin_panel():
    if session.get('admin_auth') != ADMIN_SECRET:
        return redirect(url_for('admin_login'))
    import sqlite3 as _sq
    db_path = os.path.join(app.instance_path, 'guess_up.db')
    conn    = _sq.connect(db_path)
    conn.row_factory = _sq.Row

    # تأكد إن الـ columns موجودة — لو لأ أضفهم فوراً
    existing = [r[1] for r in conn.execute("PRAGMA table_info(players)")]
    if 'is_deleted' not in existing:
        conn.execute("ALTER TABLE players ADD COLUMN is_deleted BOOLEAN DEFAULT 0")
        conn.commit()
    if 'is_banned' not in existing:
        conn.execute("ALTER TABLE players ADD COLUMN is_banned BOOLEAN DEFAULT 0")
        conn.commit()
    if 'ban_until' not in existing:
        conn.execute("ALTER TABLE players ADD COLUMN ban_until DATETIME")
        conn.commit()
    if 'ban_reason' not in existing:
        conn.execute("ALTER TABLE players ADD COLUMN ban_reason VARCHAR(200)")
        conn.commit()
    if 'deleted_at' not in existing:
        conn.execute("ALTER TABLE players ADD COLUMN deleted_at DATETIME")
        conn.commit()

    # جيب اللاعبين بـ raw SQL
    raw_active  = conn.execute(
        "SELECT * FROM players WHERE is_deleted=0 OR is_deleted IS NULL ORDER BY points DESC"
    ).fetchall()
    raw_deleted = conn.execute(
        "SELECT * FROM players WHERE is_deleted=1 ORDER BY deleted_at DESC"
    ).fetchall()
    raw_banned  = conn.execute(
        "SELECT * FROM players WHERE is_banned=1 AND (is_deleted=0 OR is_deleted IS NULL)"
    ).fetchall()
    conn.close()

    # حوّل لـ Player objects عشان الـ template يشتغل
    def row_to_player(row):
        p = Player.query.get(row['id'])
        return p

    players = [p for p in (row_to_player(r) for r in raw_active)  if p]
    deleted = [p for p in (row_to_player(r) for r in raw_deleted) if p]
    banned  = [p for p in (row_to_player(r) for r in raw_banned)  if p]

    hint = ADMIN_SECRET[:4] + '****'
    return render_template('admin.html', players=players, deleted=deleted,
                           banned=banned, secret_hint=hint)

@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    err = ''
    if request.method == 'POST':
        if request.form.get('secret') == ADMIN_SECRET:
            session['admin_auth'] = ADMIN_SECRET
            return redirect(url_for('admin_panel'))
        err = '❌ باسورد غلط'
    return f"""
    <html><head><meta charset="utf-8">
    <style>
        body{{font-family:'Cairo',sans-serif;background:#0f0f1a;color:#fff;
             display:flex;align-items:center;justify-content:center;height:100vh;margin:0;direction:rtl}}
        .box{{background:#1e1b2e;border:1px solid #2d2b42;border-radius:16px;
              padding:32px;width:300px;text-align:center}}
        h2{{margin-bottom:20px;font-size:1.3rem}}
        input{{width:100%;padding:10px;margin-bottom:12px;box-sizing:border-box;
               background:#2d2b42;border:1px solid #3d3b52;border-radius:8px;
               color:#fff;font-family:'Cairo',sans-serif;font-size:1rem;text-align:center}}
        button{{width:100%;padding:10px;background:#7c3aed;color:#fff;border:none;
                border-radius:8px;font-size:1rem;cursor:pointer;font-family:'Cairo',sans-serif}}
        .err{{color:#fca5a5;margin-bottom:10px;font-size:.9rem}}
    </style></head>
    <body><div class="box">
        <h2>⚡ Admin Panel</h2>
        <div class="err">{err}</div>
        <form method="POST">
            <input type="password" name="secret" placeholder="الباسورد السري" autofocus>
            <button type="submit">دخول</button>
        </form>
    </div></body></html>
    """

@app.route('/admin/edit_points', methods=['POST'])
def admin_edit_points():
    if session.get('admin_auth') != ADMIN_SECRET:
        return jsonify({'error': 'مش مسموح'}), 403
    pid    = int(request.form.get('player_id', 0))
    action = request.form.get('action', 'add')
    amount = int(request.form.get('amount', 0))
    p = Player.query.filter_by(id=pid).first()
    if not p:
        return jsonify({'error': 'لاعب مش موجود'}), 404
    old = p.points
    if action == 'add':
        p.points += amount
        msg = f'✅ +{amount} نقطة لـ {p.player_name} ({old} → {p.points})'
    elif action == 'sub':
        p.points = max(0, p.points - amount)
        msg = f'✅ -{amount} نقطة من {p.player_name} ({old} → {p.points})'
    elif action == 'set':
        p.points = amount
        msg = f'✅ نقاط {p.player_name} = {amount}'
    db.session.commit()
    return jsonify({'ok': True, 'new_points': p.points, 'msg': msg})

@app.route('/admin/run_migrate')
def admin_run_migrate():
    if session.get('admin_auth') != ADMIN_SECRET:
        return jsonify({'error': 'مش مسموح'}), 403
    try:
        from migrate import run_migrations
        run_migrations(os.path.join(app.instance_path, 'guess_up.db'))
        # تأكيد إن الـ columns موجودة
        import sqlite3
        conn = sqlite3.connect(os.path.join(app.instance_path, 'guess_up.db'))
        c = conn.execute("PRAGMA table_info(players)")
        cols = [r[1] for r in c.fetchall()]
        conn.close()
        return jsonify({'ok': True, 'columns': cols})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_auth', None)
    return redirect(url_for('index'))



# ════════════════════════════════════════════════════════
# ADMIN — حذف وحظر الحسابات
# ════════════════════════════════════════════════════════

@app.route('/admin/delete_player/<int:pid>', methods=['POST'])
def admin_delete_player(pid):
    if session.get('admin_auth') != ADMIN_SECRET:
        return jsonify({'error': 'مش مسموح'}), 403
    from datetime import datetime as _dt
    import sqlite3 as _sq
    p = Player.query.filter_by(id=pid).first()
    if not p: return jsonify({'error': 'مش موجود'}), 404
    name = p.player_name
    try:
        # حاول SQLAlchemy الأول
        p.is_deleted = True
        p.deleted_at = _dt.utcnow()
        db.session.commit()
    except Exception:
        db.session.rollback()
    # raw SQL كـ backup مضمون
    try:
        conn = _sq.connect(os.path.join(app.instance_path, 'guess_up.db'))
        now  = _dt.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("UPDATE players SET is_deleted=1, deleted_at=? WHERE id=?", (now, pid))
        conn.commit(); conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': True, 'msg': f'تم حذف حساب {name}'})


@app.route('/admin/restore_player/<int:pid>', methods=['POST'])
def admin_restore_player(pid):
    if session.get('admin_auth') != ADMIN_SECRET:
        return jsonify({'error': 'مش مسموح'}), 403
    import sqlite3 as _sq
    p = Player.query.filter_by(id=pid).first()
    if not p: return jsonify({'error': 'مش موجود'}), 404
    name = p.player_name
    try:
        p.is_deleted = False; p.deleted_at = None; db.session.commit()
    except Exception: db.session.rollback()
    try:
        conn = _sq.connect(os.path.join(app.instance_path, 'guess_up.db'))
        conn.execute("UPDATE players SET is_deleted=0, deleted_at=NULL WHERE id=?", (pid,))
        conn.commit(); conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': True, 'msg': f'تم استعادة حساب {name}'})


@app.route('/admin/ban_player/<int:pid>', methods=['POST'])
def admin_ban_player(pid):
    if session.get('admin_auth') != ADMIN_SECRET:
        return jsonify({'error': 'مش مسموح'}), 403
    from datetime import datetime as _dt, timedelta
    import sqlite3 as _sq
    p = Player.query.filter_by(id=pid).first()
    if not p: return jsonify({'error': 'مش موجود'}), 404
    name     = p.player_name
    reason   = request.form.get('reason', '').strip()
    duration = request.form.get('duration', 'permanent')
    durations = {
        '1h': timedelta(hours=1), '24h': timedelta(hours=24),
        '3d': timedelta(days=3),  '7d':  timedelta(days=7),
        '30d':timedelta(days=30), 'permanent': None
    }
    ban_until = (_dt.utcnow() + durations[duration]) if duration != 'permanent' else None
    until_str = ban_until.strftime('%Y/%m/%d %H:%M') if ban_until else 'دائم'
    until_db  = ban_until.strftime('%Y-%m-%d %H:%M:%S') if ban_until else None

    try:
        p.is_banned = True; p.ban_reason = reason or None; p.ban_until = ban_until
        db.session.commit()
    except Exception: db.session.rollback()
    try:
        conn = _sq.connect(os.path.join(app.instance_path, 'guess_up.db'))
        conn.execute("UPDATE players SET is_banned=1, ban_until=?, ban_reason=? WHERE id=?",
                     (until_db, reason or None, pid))
        conn.commit(); conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': True, 'msg': f'تم حظر {name} حتى {until_str}', 'until': until_str})


@app.route('/admin/unban_player/<int:pid>', methods=['POST'])
def admin_unban_player(pid):
    if session.get('admin_auth') != ADMIN_SECRET:
        return jsonify({'error': 'مش مسموح'}), 403
    import sqlite3 as _sq
    p = Player.query.filter_by(id=pid).first()
    if not p: return jsonify({'error': 'مش موجود'}), 404
    name = p.player_name
    try:
        p.is_banned = False; p.ban_until = None; p.ban_reason = None
        db.session.commit()
    except Exception: db.session.rollback()
    try:
        conn = _sq.connect(os.path.join(app.instance_path, 'guess_up.db'))
        conn.execute("UPDATE players SET is_banned=0, ban_until=NULL, ban_reason=NULL WHERE id=?", (pid,))
        conn.commit(); conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': True, 'msg': f'تم رفع الحظر عن {name}'})


# ════════════════════════════════════════════════════════
# DAILY CHALLENGE ROUTES
# ════════════════════════════════════════════════════════

@app.route('/daily')
def daily_challenge():
    if not is_logged_in(session): return redirect(url_for('index'))
    ch  = get_or_create_daily()
    me  = get_player_by_id(session['player_id'])
    entry = DailyChallengeEntry.query.filter_by(
        challenge_id=ch.id, player_id=me.id).first()
    top = (DailyChallengeEntry.query
           .filter_by(challenge_id=ch.id, completed=True)
           .order_by(DailyChallengeEntry.guesses.asc(),
                     DailyChallengeEntry.time_secs.asc())
           .limit(20).all())
    return render_template('daily.html', ch=ch, me=me, entry=entry, top=top)

@app.route('/api/daily/guess', methods=['POST'])
def daily_guess():
    if not is_logged_in(session): return jsonify({'error':'مش مسجل'}), 401
    from datetime import datetime as _dt
    guess = int(request.form.get('guess', 0))
    pid   = session['player_id']
    ch    = get_or_create_daily()

    entry = DailyChallengeEntry.query.filter_by(
        challenge_id=ch.id, player_id=pid).first()
    if not entry:
        entry = DailyChallengeEntry(
            challenge_id=ch.id, player_id=pid,
            guesses=0, completed=False, created_at=_dt.utcnow())
        db.session.add(entry)
        db.session.flush()

    if entry.completed:
        return jsonify({'error':'حليت التحدي ده قبل كده!'}), 400

    entry.guesses += 1
    db.session.commit()

    if guess == ch.target:
        entry.completed = True
        # مكافأة نقاط حسب عدد المحاولات
        bonus = max(10, 100 - (entry.guesses-1)*10)
        p = Player.query.get(pid)
        if p:
            p.points += bonus
            p.xp = (p.xp or 0) + 20
        db.session.commit()
        return jsonify({'result':'correct','message':f'🎉 صح! في {entry.guesses} محاولة!',
                       'guesses':entry.guesses,'bonus':bonus})
    elif guess < ch.secret:
        return jsonify({'result':'higher','message':'⬆️ أعلى من كده!','guesses':entry.guesses})
    else:
        return jsonify({'result':'lower', 'message':'⬇️ أقل من كده!', 'guesses':entry.guesses})

# ════════════════════════════════════════════════════════
# FRIENDS & NOTIFICATIONS ROUTES
# ════════════════════════════════════════════════════════

@app.route('/friends')
def friends_page():
    if not is_logged_in(session): return redirect(url_for('index'))
    me      = get_player_by_id(session['player_id'])
    friends = Friendship.get_friends(me.id)

    # طلبات الصداقة الواردة
    incoming = Friendship.query.filter_by(
        receiver_id=me.id, status='pending'
    ).all()

    # طلبات الصداقة الصادرة
    outgoing = Friendship.query.filter_by(
        sender_id=me.id, status='pending'
    ).all()

    return render_template('friends.html',
        me=me, friends=friends,
        incoming=incoming, outgoing=outgoing
    )


@app.route('/friends/search')
def friends_search():
    if not is_logged_in(session): return jsonify([])
    q   = request.args.get('q','').strip()
    me  = session['player_id']
    if len(q) < 2: return jsonify([])
    players = Player.query.filter(
        Player.player_name.ilike(f'%{q}%'),
        Player.id != me
    ).limit(10).all()
    result = []
    for p in players:
        # حالة الصداقة
        fr = Friendship.query.filter(
            db.or_(
                db.and_(Friendship.sender_id==me,   Friendship.receiver_id==p.id),
                db.and_(Friendship.sender_id==p.id, Friendship.receiver_id==me)
            )
        ).first()
        status = fr.status if fr else 'none'
        is_sender = fr.sender_id == me if fr else False
        result.append({
            'id':        p.id,
            'name':      p.player_name,
            'points':    p.points,
            'avatar':    p.avatar,
            'status':    status,
            'is_sender': is_sender
        })
    return jsonify(result)


@app.route('/friends/request/<int:pid>', methods=['POST'])
def friend_request(pid):
    if not is_logged_in(session): return jsonify({'error':'مش مسجل'}), 401
    me = session['player_id']
    if me == pid: return jsonify({'error':'مش ممكن تبعت لنفسك'}), 400

    # تأكد مش موجود أصلاً
    exists = Friendship.query.filter(
        db.or_(
            db.and_(Friendship.sender_id==me,  Friendship.receiver_id==pid),
            db.and_(Friendship.sender_id==pid, Friendship.receiver_id==me)
        )
    ).first()
    if exists: return jsonify({'error':'طلب موجود بالفعل'}), 400

    fr = Friendship(sender_id=me, receiver_id=pid, status='pending')
    db.session.add(fr)
    db.session.commit()

    sender = get_player_by_id(me)
    send_notification(
        player_id=pid, ntype='friend_request',
        title='طلب صداقة جديد 👋',
        body=f'{sender.player_name} بعتلك طلب صداقة',
        from_id=me, link='/friends'
    )
    return jsonify({'ok': True})


@app.route('/friends/accept/<int:pid>', methods=['POST'])
def friend_accept(pid):
    if not is_logged_in(session): return jsonify({'error':'مش مسجل'}), 401
    me = session['player_id']
    fr = Friendship.query.filter_by(sender_id=pid, receiver_id=me, status='pending').first()
    if not fr: return jsonify({'error':'مفيش طلب'}), 404
    from datetime import datetime as _dt
    fr.status = 'accepted'
    fr.updated_at = _dt.utcnow()
    db.session.commit()

    me_player = get_player_by_id(me)
    send_notification(
        player_id=pid, ntype='friend_accept',
        title='✅ قبل طلب صداقتك!',
        body=f'{me_player.player_name} قبل طلب صداقتك',
        from_id=me, link='/friends'
    )
    return jsonify({'ok': True})


@app.route('/friends/reject/<int:pid>', methods=['POST'])
def friend_reject(pid):
    if not is_logged_in(session): return jsonify({'error':'مش مسجل'}), 401
    me = session['player_id']
    fr = Friendship.query.filter(
        db.or_(
            db.and_(Friendship.sender_id==pid, Friendship.receiver_id==me),
            db.and_(Friendship.sender_id==me,  Friendship.receiver_id==pid)
        )
    ).first()
    if fr:
        db.session.delete(fr)
        db.session.commit()
    return jsonify({'ok': True})


@app.route('/friends/remove/<int:pid>', methods=['POST'])
def friend_remove(pid):
    if not is_logged_in(session): return jsonify({'error':'مش مسجل'}), 401
    me = session['player_id']
    fr = Friendship.query.filter(
        db.or_(
            db.and_(Friendship.sender_id==me,  Friendship.receiver_id==pid),
            db.and_(Friendship.sender_id==pid, Friendship.receiver_id==me)
        )
    ).first()
    if fr:
        db.session.delete(fr)
        db.session.commit()
    return jsonify({'ok': True})


# ── Notifications ────────────────────────────────────────────────────────────
@app.route('/notifications')
def notifications_page():
    if not is_logged_in(session): return redirect(url_for('index'))
    me    = session['player_id']
    notifs = Notification.query.filter_by(player_id=me)        .order_by(Notification.created_at.desc()).limit(50).all()
    # اعمل كلهم مقروءين
    Notification.query.filter_by(player_id=me, is_read=False)        .update({'is_read': True})
    db.session.commit()
    return render_template('notifications.html', notifs=notifs)


@app.route('/api/notifications/count')
def notif_count():
    if not is_logged_in(session): return jsonify({'count': 0})
    c = Notification.query.filter_by(
        player_id=session['player_id'], is_read=False
    ).count()
    return jsonify({'count': c})


@app.route('/api/notifications/mark_read', methods=['POST'])
def notif_mark_read():
    if not is_logged_in(session): return jsonify({'ok': False})
    Notification.query.filter_by(
        player_id=session['player_id'], is_read=False
    ).update({'is_read': True})
    db.session.commit()
    return jsonify({'ok': True})


# ── Chat ─────────────────────────────────────────────────────────────────────
@app.route('/chat/<int:friend_id>')
def chat_page(friend_id):
    if not is_logged_in(session): return redirect(url_for('index'))
    me = session['player_id']
    if not Friendship.are_friends(me, friend_id):
        return redirect(url_for('friends_page'))
    friend = get_player_by_id(friend_id)
    if not friend: return redirect(url_for('friends_page'))

    msgs = ChatMessage.query.filter(
        db.or_(
            db.and_(ChatMessage.sender_id==me,        ChatMessage.receiver_id==friend_id),
            db.and_(ChatMessage.sender_id==friend_id, ChatMessage.receiver_id==me)
        )
    ).order_by(ChatMessage.created_at.asc()).limit(100).all()

    # اعمل الرسايل مقروءة
    ChatMessage.query.filter_by(sender_id=friend_id, receiver_id=me, is_read=False)        .update({'is_read': True})
    db.session.commit()

    me_player = get_player_by_id(me)
    return render_template('chat.html', friend=friend, me=me_player, msgs=msgs)


@app.route('/api/chat/send', methods=['POST'])
def chat_send():
    if not is_logged_in(session): return jsonify({'error':'مش مسجل'}), 401
    me        = session['player_id']
    friend_id = int(request.form.get('friend_id', 0))
    text      = request.form.get('text','').strip()[:500]

    if not text or not friend_id: return jsonify({'error':'بيانات ناقصة'}), 400
    if not Friendship.are_friends(me, friend_id):
        return jsonify({'error':'مش أصدقاء'}), 403

    msg = ChatMessage(sender_id=me, receiver_id=friend_id, text=text)
    db.session.add(msg)
    db.session.commit()

    sender = get_player_by_id(me)
    # Real-time للمستلم
    socketio.emit('new_chat_msg', {
        'msg_id':    msg.id,
        'from_id':   me,
        'from_name': sender.player_name,
        'text':      text,
        'time':      msg.created_at.strftime('%H:%M')
    }, room=f'user_{friend_id}')

    # notification للمستلم لو مش في صفحة الشات
    send_notification(
        player_id=friend_id, ntype='chat_msg',
        title=f'💬 {sender.player_name}',
        body=text[:80],
        from_id=me, link=f'/chat/{me}'
    )
    return jsonify({'ok': True, 'msg_id': msg.id,
                    'time': msg.created_at.strftime('%H:%M')})


# ── SocketIO: Personal Room ──────────────────────────────────────────────────
@socketio.on('join_personal_room')
def on_join_personal(data):
    """كل لاعب يدخل room خاص بـ user_ID عشان يستقبل notifications"""
    pid = session.get('player_id')
    if pid:
        sio_join_room(f'user_{pid}')

# ══════════════════════════════════════════════════════════════════════════════
with app.app_context():
    from migrate import run_migrations
    run_migrations(os.path.join(app.instance_path, 'guess_up.db'))
    db.create_all()

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)