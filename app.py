from flask import Flask, render_template, request
from flask_socketio import SocketIO, join_room, emit
import rooms
from models import db, Player  # استدعاء الداتا بيز وجدول اللاعبين

app = Flask(__name__)
app.config['SECRET_KEY'] = 'mysecret'

# ----------------- إعدادات قاعدة البيانات -----------------
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ربط الداتا بيز بالتطبيق
db.init_app(app)

# إنشاء ملف database.db لو مش موجود
with app.app_context():
    db.create_all()
# ----------------------------------------------------------

socketio = SocketIO(app)

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)
    is_ready = rooms.add_player(room, request.sid, data['name'])
    if is_ready:
        socketio.emit('game_ready', room=room)

@socketio.on('submit_secret')
def on_submit_secret(data):
    room = data['room']
    turn_id, turn_name = rooms.set_secret(room, request.sid, int(data['secret']))
    if turn_id:
        socketio.emit('start_guessing', {'turn': turn_id, 'turn_name': turn_name}, room=room)

@socketio.on('make_guess')
def on_make_guess(data):
    room = data['room']
    guess = int(data['guess'])
    rooms.save_guess(room, guess)
    guesser_name = rooms.get_guesser_name(room, request.sid)
    socketio.emit('receive_guess', {'guess': guess, 'guesser_name': guesser_name}, room=room)

@socketio.on('answer_guess')
def on_answer_guess(data):
    room = data['room']
    answer = data['answer']
    
    if rooms.check_cheat(room, request.sid, answer):
        emit('cheat_warning', {'msg': 'انت غشاش اوي يلا 😂 العب صح!'}, to=request.sid)
        return
        
    if answer == 'أيوة ده رقمي':
        winner_id, winner_name = rooms.get_winner_info(room)
        socketio.emit('game_over', {'winner': winner_id, 'winner_name': winner_name}, room=room)
    else:
        next_turn, next_turn_name, prev_name, guessed_num = rooms.process_answer(room)
        socketio.emit('guess_result', {
            'answer': answer, 'next_turn': next_turn, 'next_turn_name': next_turn_name,
            'previous_guesser_name': prev_name, 'guessed_number': guessed_num
        }, room=room)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)