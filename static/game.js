function setBet(val) {
    document.getElementById('bet-amount').value = val;
}

function createRoom() {
    const bet = document.getElementById('bet-amount').value;
    currentRoom = Math.floor(100000 + Math.random() * 900000).toString();
    socket.emit('join', {room: currentRoom, name: myName, bet: bet});
    showRoom(currentRoom);
}

socket.on('game_over', (data) => {
    document.getElementById('game-room').style.display = 'none';
    alert(`🎉 الفائز: ${data.winner} \n 💰 كسب رهان: ${data.bet} نقطة`);
    location.reload(); // تحديث عشان الرصيد الجديد يظهر
});