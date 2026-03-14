from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room as sio_join_room
from models import db, Player, Room, Punishment, Match, Friendship, Notification, ChatMessage
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
    # تحقق من الحظر والحذف
    if player.is_deleted:
        return render_template('index.html', error='❌ هذا الحساب تم حذفه', tab='login', login_name=name)
    if player.ban_active:
        from datetime import datetime as _dt
        if player.ban_until:
            until = player.ban_until.strftime('%Y/%m/%d %H:%M')
            msg = f'🚫 حسابك محظور حتى {until}'
        else:
            msg = '🚫 حسابك محظور بشكل دائم'
        if player.ban_reason:
            msg += f' — السبب: {player.ban_reason}'
        return render_template('index.html', error=msg, tab='login', login_name=name)
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
    return render_template('lobby.html', player=player,
                           bankrupt=is_bankrupt(player),
                           public_rooms=public_rooms,
                           daily_ready=daily_ready,
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
    bet       = int(request.form.get('bet_points', 100))
    is_bm     = request.form.get('bankrupt_mode') == '1'
    is_bot    = request.form.get('bot_game') == '1'
    is_public = request.form.get('is_public') == '1'

    room, err = create_room(session['player_id'], bet, is_bm, is_bot)
    if err: return jsonify({'error': err}), 400

    # م6: Random Event لكل غرفة
    room.random_event = roll_random_event()
    if is_public and not is_bot: room.is_public = True
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
            'event': None, 'event_info': {}
        }, to=request.sid)
        return

    # ── لاعبين ────────────────────────────────────────────────────────────
    player_count = len(room_players[room_code])
    emit('player_joined', {
        'player_name': player_name, 'player_count': player_count,
        'room_full': player_count >= 2
    }, room=room_code)

    if player_count >= 2:
        room_secrets.pop(room_code, None)
        plist      = list(room_players[room_code].items())
        event_info = EVENTS_MAP.get(room.random_event, {})
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
            'xp_gained': XP_PER_WIN,
            'room_code': rc,
            'no_punishment': False, 'vs_bot': False,
            'achievements': new_ach,
            'guess_log': guess_log
        }, room=rc)


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
    players  = Player.query.filter_by(is_deleted=False).order_by(Player.points.desc()).all()
    deleted  = Player.query.filter_by(is_deleted=True).order_by(Player.deleted_at.desc()).all()
    banned   = Player.query.filter_by(is_banned=True, is_deleted=False).all()
    hint     = ADMIN_SECRET[:4] + '****'
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
    p = Player.query.filter_by(id=pid).first()
    if not p: return jsonify({'error': 'مش موجود'}), 404
    p.is_deleted = True
    p.deleted_at = _dt.utcnow()
    # نعمل logout لو كان متصل
    db.session.commit()
    return jsonify({'ok': True, 'msg': f'تم حذف حساب {p.player_name}'})


@app.route('/admin/restore_player/<int:pid>', methods=['POST'])
def admin_restore_player(pid):
    if session.get('admin_auth') != ADMIN_SECRET:
        return jsonify({'error': 'مش مسموح'}), 403
    p = Player.query.filter_by(id=pid).first()
    if not p: return jsonify({'error': 'مش موجود'}), 404
    p.is_deleted = False
    p.deleted_at = None
    db.session.commit()
    return jsonify({'ok': True, 'msg': f'تم استعادة حساب {p.player_name}'})


@app.route('/admin/ban_player/<int:pid>', methods=['POST'])
def admin_ban_player(pid):
    if session.get('admin_auth') != ADMIN_SECRET:
        return jsonify({'error': 'مش مسموح'}), 403
    from datetime import datetime as _dt, timedelta
    p = Player.query.filter_by(id=pid).first()
    if not p: return jsonify({'error': 'مش موجود'}), 404

    reason   = request.form.get('reason', '').strip()
    duration = request.form.get('duration', 'permanent')  # permanent / 1h / 24h / 7d / 30d

    durations = {
        '1h':        timedelta(hours=1),
        '24h':       timedelta(hours=24),
        '3d':        timedelta(days=3),
        '7d':        timedelta(days=7),
        '30d':       timedelta(days=30),
        'permanent': None
    }

    p.is_banned  = True
    p.ban_reason = reason or None
    p.ban_until  = (_dt.utcnow() + durations[duration]) if duration != 'permanent' else None
    db.session.commit()

    until_str = p.ban_until.strftime('%Y/%m/%d %H:%M') if p.ban_until else 'دائم'
    return jsonify({'ok': True, 'msg': f'تم حظر {p.player_name} حتى {until_str}',
                    'until': until_str})


@app.route('/admin/unban_player/<int:pid>', methods=['POST'])
def admin_unban_player(pid):
    if session.get('admin_auth') != ADMIN_SECRET:
        return jsonify({'error': 'مش مسموح'}), 403
    p = Player.query.filter_by(id=pid).first()
    if not p: return jsonify({'error': 'مش موجود'}), 404
    p.is_banned  = False
    p.ban_until  = None
    p.ban_reason = None
    db.session.commit()
    return jsonify({'ok': True, 'msg': f'تم رفع الحظر عن {p.player_name}'})

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