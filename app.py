from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room as sio_join_room
from models import db, Player, Room, Punishment
from auth import register_player, login_player, get_player_by_id, is_logged_in
from rooms import create_room, join_room, get_room
from points import transfer_points, award_bankrupt_mode_points, is_bankrupt
from bot import create_bot_session, get_bot_session, remove_bot_session
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'guess_up_secret_2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///guess_up.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)

# ─── in-memory state ────────────────────────────────────────────────────────
room_secrets  = {}   # room_code -> {player_id: secret}
room_players  = {}   # room_code -> {player_id: player_name}
sid_to_room   = {}   # sid -> room_code
sid_to_player = {}   # sid -> player_id

# ────────────────────────────────────────────────────────────────────────────
# HTTP ROUTES
# ────────────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if is_logged_in(session):
        return redirect(url_for('lobby'))
    return render_template('index.html')


@app.route('/register', methods=['POST'])
def register():
    name     = request.form.get('name', '').strip()
    password = request.form.get('password', '')
    player, error = register_player(name, password)
    if error:
        return render_template('index.html', error=error, tab='register',
                               reg_name=name)
    session['player_id']   = player.id
    session['player_name'] = player.player_name
    return redirect(url_for('lobby'))


@app.route('/login', methods=['POST'])
def login():
    name     = request.form.get('name', '').strip()
    password = request.form.get('password', '')
    player, error = login_player(name, password)
    if error:
        return render_template('index.html', error=error, tab='login',
                               login_name=name)
    session['player_id']   = player.id
    session['player_name'] = player.player_name
    return redirect(url_for('lobby'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/lobby')
def lobby():
    if not is_logged_in(session):
        return redirect(url_for('index'))
    player = get_player_by_id(session['player_id'])
    if not player:
        session.clear()
        return redirect(url_for('index'))
    bankrupt = is_bankrupt(player)
    return render_template('lobby.html', player=player, bankrupt=bankrupt)


@app.route('/create_room', methods=['POST'])
def create_room_route():
    if not is_logged_in(session):
        return jsonify({'error': 'مش مسجل دخول'}), 401
    bet    = int(request.form.get('bet_points', 100))
    is_bm  = request.form.get('bankrupt_mode') == '1'
    is_bot = request.form.get('bot_game') == '1'

    room, error = create_room(session['player_id'], bet, is_bm, is_bot)
    if error:
        return jsonify({'error': error}), 400
    if is_bot:
        create_bot_session(room.room_code)
    return jsonify({'room_code': room.room_code})


@app.route('/join_room_route', methods=['POST'])
def join_room_route():
    if not is_logged_in(session):
        return jsonify({'error': 'مش مسجل دخول'}), 401
    code = request.form.get('room_code', '').strip().upper()
    room, error = join_room(code, session['player_id'])
    if error:
        return jsonify({'error': error}), 400
    return jsonify({'room_code': room.room_code})


@app.route('/room/<room_code>')
def game_room(room_code):
    if not is_logged_in(session):
        return redirect(url_for('index'))
    room = get_room(room_code)
    if not room:
        return redirect(url_for('lobby'))
    player = get_player_by_id(session['player_id'])
    if not player:
        return redirect(url_for('index'))
    return render_template('room.html', room=room, player=player)


@app.route('/punishment/<room_code>', methods=['POST'])
def punishment(room_code):
    if not is_logged_in(session):
        return jsonify({'error': 'مش مسجل دخول'}), 401
    room = get_room(room_code)
    if not room or room.winner_id != session['player_id']:
        return jsonify({'error': 'مش مسموح'}), 403

    text     = request.form.get('punishment_text', '').strip()
    whatsapp = request.form.get('whatsapp', '').strip()
    if not text or not whatsapp:
        return jsonify({'error': 'اكتب العقاب ورقم الواتساب'}), 400

    loser_id = room.player1_id if room.winner_id == room.player2_id else room.player2_id
    p = Punishment(
        room_id=room.id,
        winner_id=room.winner_id,
        loser_id=loser_id,
        punishment_text=text,
        whatsapp_number=whatsapp
    )
    db.session.add(p)
    db.session.commit()

    socketio.emit('punishment_received', {'text': text, 'whatsapp': whatsapp},
                  room=room_code)
    return jsonify({'ok': True})


@app.route('/leaderboard')
def leaderboard():
    from points import get_leaderboard
    players = get_leaderboard(20)
    return render_template('leaderboard.html', players=players)


# ────────────────────────────────────────────────────────────────────────────
# SOCKET.IO EVENTS
# ────────────────────────────────────────────────────────────────────────────

@socketio.on('connect')
def on_connect():
    pid = session.get('player_id')
    if pid:
        sid_to_player[request.sid] = pid


@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    pid = sid_to_player.pop(sid, None)
    rc  = sid_to_room.pop(sid, None)
    if pid and rc and rc in room_players:
        room_players[rc].pop(pid, None)


@socketio.on('join_game_room')
def on_join(data):
    room_code   = data.get('room_code')
    player_id   = session.get('player_id')
    player_name = session.get('player_name')

    if not room_code or not player_id:
        return

    sio_join_room(room_code)
    sid_to_room[request.sid] = room_code

    if room_code not in room_players:
        room_players[room_code] = {}
    room_players[room_code][player_id] = player_name

    room = get_room(room_code)
    if not room:
        return

    # ── لعب ضد البوت ──────────────────────────────────────────────────────
    if room.is_bot_game:
        if not get_bot_session(room_code):
            create_bot_session(room_code)
        emit('player_joined', {'player_count': 1, 'room_full': True,
                               'opponent_name': '🤖 الكمبيوتر'}, to=request.sid)
        emit('game_started', {
            'message': '🤖 الكمبيوتر اختار رقم من 1 لـ 1000 — ابدأ تخمن!',
            'vs_bot': True,
            'opponent_name': '🤖 الكمبيوتر'
        }, to=request.sid)
        return

    # ── لعب بين لاعبين ────────────────────────────────────────────────────
    player_count = len(room_players[room_code])

    # ابعت لكل اللي في الغرفة اسم اللي دخل
    emit('player_joined', {
        'player_name':  player_name,
        'player_count': player_count,
        'room_full':    player_count >= 2,
        'all_players':  room_players[room_code]   # {id: name}
    }, room=room_code)

    if player_count >= 2:
        room_secrets.pop(room_code, None)
        # كل لاعب يعرف اسم خصمه
        players_list = list(room_players[room_code].items())  # [(id, name), ...]
        for sid, p_id in sid_to_player.items():
            if p_id in room_players.get(room_code, {}):
                # الخصم هو اللي مش هو
                opp_name = next(
                    (n for i, n in players_list if i != p_id),
                    'الخصم'
                )
                socketio.emit('room_ready', {
                    'message': '✅ اللاعبين جاهزين! اختار رقمك السري',
                    'opponent_name': opp_name
                }, to=sid)


@socketio.on('set_secret')
def on_set_secret(data):
    room_code = data.get('room_code')
    secret    = data.get('secret')
    player_id = session.get('player_id')

    if not room_code or secret is None or not player_id:
        return
    if room_code not in room_secrets:
        room_secrets[room_code] = {}
    room_secrets[room_code][player_id] = int(secret)

    if len(room_secrets[room_code]) >= 2:
        emit('game_started', {
            'message': '🎮 اللعبة بدأت! اللاعب الأول يبدأ التخمين!'
        }, room=room_code)
    else:
        emit('secret_set', {'message': '✅ تمام! استنى صاحبك يحط رقمه...'}, to=request.sid)


@socketio.on('make_guess')
def on_guess(data):
    room_code   = data.get('room_code')
    guess       = data.get('guess')
    guesser_id  = session.get('player_id')
    guesser_name = session.get('player_name')

    if not room_code or guess is None or not guesser_id:
        return
    room = get_room(room_code)
    if not room:
        return

    # ── ضد البوت ──────────────────────────────────────────────────────────
    if room.is_bot_game:
        bot = get_bot_session(room_code)
        if not bot:
            emit('error_msg', {'message': '⚠️ مشكلة في البوت، عمل refresh'}, to=request.sid)
            return
        result = bot.respond_to_guess(int(guess))
        emit('guess_result', {'guesser': guesser_name, 'guess': int(guess), **result},
             to=request.sid)
        if result['correct']:
            room.winner_id = guesser_id
            db.session.commit()
            remove_bot_session(room_code)
            room_players.pop(room_code, None)
            emit('game_over', {
                'winner': guesser_name, 'winner_id': guesser_id,
                'loser_id': None,
                'message': f'🏆 كسبت في {result["guesses"]} محاولة!',
                'vs_bot': True, 'no_punishment': True,
                'winner_new_points': None, 'loser_new_points': None
            }, to=request.sid)
        return

    # ── بين لاعبين ────────────────────────────────────────────────────────
    opponent_id = room.player2_id if guesser_id == room.player1_id else room.player1_id
    if not opponent_id:
        emit('error_msg', {'message': '⚠️ اللاعب الثاني لسه مدخلش'}, to=request.sid)
        return

    secret = room_secrets.get(room_code, {}).get(opponent_id)
    if secret is None:
        emit('error_msg', {'message': '⚠️ الخصم لسه محطش رقمه'}, to=request.sid)
        return

    guess_num = int(guess)
    if guess_num < 1 or guess_num > 1000:
        emit('error_msg', {'message': '⚠️ الرقم لازم بين 1 و 1000'}, to=request.sid)
        return

    if guess_num < secret:
        result = {'result': 'higher', 'message': '⬆️ أعلى من كده!', 'correct': False}
    elif guess_num > secret:
        result = {'result': 'lower',  'message': '⬇️ أقل من كده!',  'correct': False}
    else:
        result = {'result': 'correct','message': '🎉 صح! ده الرقم!', 'correct': True}

    emit('guess_result', {'guesser': guesser_name, 'guess': guess_num, **result},
         room=room_code)

    if result['correct']:
        bet = room.bet_points
        if room.is_bankrupt_mode:
            award_bankrupt_mode_points(guesser_id)
        else:
            transfer_points(guesser_id, opponent_id, bet)

        room.winner_id = guesser_id
        db.session.commit()

        wp = get_player_by_id(guesser_id)
        lp = get_player_by_id(opponent_id)

        room_secrets.pop(room_code, None)
        room_players.pop(room_code, None)

        emit('game_over', {
            'winner': guesser_name, 'winner_id': guesser_id,
            'loser_id': opponent_id,
            'message': f'🏆 {guesser_name} كسب {bet} نقطة!',
            'winner_new_points': wp.points, 'loser_new_points': lp.points,
            'bet': bet, 'room_code': room_code,
            'no_punishment': False, 'vs_bot': False
        }, room=room_code)


# ────────────────────────────────────────────────────────────────────────────
with app.app_context():
    # ── Migration أولاً: يضيف columns ناقصة بدون مسح بيانات ──
    from migrate import run_migrations
    db_path = os.path.join(app.instance_path, 'guess_up.db')
    run_migrations(db_path)
    # ── ثم إنشاء الجداول الجديدة لو مش موجودة ──
    db.create_all()

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)