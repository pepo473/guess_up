const socket = io();
let currentRoom = '';
let myTurn = false;
let myName = '';

// استدعاء الأصوات من الفولدر اللي إنت عملته ( static/sounds )
const clickSound = new Audio('/static/sounds/click.mp3');
const startSound = new Audio('/static/sounds/start.mp3');
const winSound = new Audio('/static/sounds/win.mp3');
const cheatSound = new Audio('/static/sounds/cheat.mp3');

// التأكد من جاهزية الصوت
clickSound.preload = 'auto';

function getPlayerName() {
    const name = document.getElementById('player-name').value.trim();
    if (!name) { alert("لازم تكتب اسمك الأول يا بطل!"); return null; }
    return name;
}

function createRoom() {
    clickSound.play();
    myName = getPlayerName();
    if (!myName) return;
    currentRoom = Math.floor(100000 + Math.random() * 900000).toString();
    socket.emit('join', {room: currentRoom, name: myName});
    showRoom(currentRoom);
}

function joinRoom() {
    clickSound.play();
    myName = getPlayerName();
    if (!myName) return;
    currentRoom = document.getElementById('room-code').value;
    if(currentRoom) {
        socket.emit('join', {room: currentRoom, name: myName});
        showRoom(currentRoom);
    } else { alert("اكتب كود الغرفة الأول!"); }
}

function showRoom(code) {
    document.getElementById('lobby').style.display = 'none';
    document.getElementById('game-room').style.display = 'block';
    document.getElementById('display-room-code').innerText = code;
}

socket.on('game_ready', function() {
    document.getElementById('status').style.display = 'none';
    document.getElementById('secret-number-section').style.display = 'block';
    startSound.play();
});

function submitSecret() {
    const secretVal = document.getElementById('secret-input').value;
    if(secretVal) {
        socket.emit('submit_secret', {room: currentRoom, secret: secretVal});
        document.getElementById('secret-number-section').innerHTML = '<h4>✅ تم حفظ رقمك! في انتظار اللاعب الثاني...</h4>';
    }
}

socket.on('start_guessing', function(data) {
    document.getElementById('secret-number-section').style.display = 'none';
    document.getElementById('play-section').style.display = 'block';
    updateTurnUI(data.turn, data.turn_name);
});

function updateTurnUI(turnId, turnName) {
    myTurn = (socket.id === turnId);
    document.getElementById('guesser-ui').style.display = myTurn ? 'block' : 'none';
    document.getElementById('answerer-ui').style.display = 'none';
    document.getElementById('cheat-warning-msg').style.display = 'none';
    
    if(myTurn) {
        document.getElementById('turn-message').innerText = '🔥 دورك! خمن الرقم السري';
    } else {
        document.getElementById('turn-message').innerText = `⏳ ${turnName} بيفكر وبيخمن رقمك...`;
    }
}

function sendGuess() {
    const guess = document.getElementById('guess-input').value;
    if(guess) {
        socket.emit('make_guess', {room: currentRoom, guess: guess});
        document.getElementById('guesser-ui').style.display = 'none';
        document.getElementById('turn-message').innerText = '⏳ التخمين اتبعت.. مستنيين الرد!';
    }
}

socket.on('receive_guess', function(data) {
    if(!myTurn) {
        document.getElementById('turn-message').innerText = `👀 ${data.guesser_name} خمن رقم!`;
        document.getElementById('guesser-name-display').innerText = data.guesser_name;
        document.getElementById('received-guess').innerText = data.guess;
        document.getElementById('answerer-ui').style.display = 'block';
    }
});

function sendAnswer(answer) {
    const cheatMsg = document.getElementById('cheat-warning-msg');
    cheatMsg.style.display = 'none';
    void cheatMsg.offsetWidth; 
    socket.emit('answer_guess', {room: currentRoom, answer: answer});
}

socket.on('cheat_warning', function(data) {
    const cheatMsg = document.getElementById('cheat-warning-msg');
    cheatMsg.innerText = data.msg;
    cheatMsg.style.display = 'block';
    cheatSound.play();
});

socket.on('guess_result', function(data) {
    document.getElementById('shared-result').style.display = 'block';
    document.getElementById('result-text').innerText = `🔔 حركة سابقة: ${data.previous_guesser_name} خمن (${data.guessed_number}) والرد كان: ${data.answer}`;
    updateTurnUI(data.next_turn, data.next_turn_name);
    document.getElementById('guess-input').value = '';
});

socket.on('game_over', function(data) {
    document.getElementById('play-section').style.display = 'none';
    document.getElementById('game-over-section').style.display = 'block';
    if(socket.id === data.winner) {
        document.getElementById('winner-message').innerText = '🏆 مبروك! إنت خمنت الرقم صح وفزت!';
    } else {
        document.getElementById('winner-message').innerText = `😔 للأسف ${data.winner_name} خمن رقمك صح وفاز!`;
    }
    winSound.play();
});