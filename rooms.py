import random
import string
from models import db, Room, Player
from points import can_join_room, BANKRUPT_MODE_POINTS


def generate_room_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))


def create_room(player1_id, bet_points, is_bankrupt_mode=False, is_bot_game=False):
    """
    إنشاء غرفة جديدة
    """
    player1 = Player.query.get(player1_id)
    if not player1:
        return None, "اللاعب مش موجود"

    # لو مش وضع مفلسين ولا لعب ضد كمبيوتر، نتأكد من النقط
    if not is_bankrupt_mode and not is_bot_game:
        if not can_join_room(player1, bet_points):
            return None, f"معندكش نقط كافية! عندك {player1.points} نقطة فقط"

    code = generate_room_code()
    # نتأكد الكود مش موجود
    while Room.query.filter_by(room_code=code).first():
        code = generate_room_code()

    room = Room(
        room_code=code,
        bet_points=bet_points,
        player1_id=player1_id,
        is_bankrupt_mode=is_bankrupt_mode,
        is_bot_game=is_bot_game
    )
    db.session.add(room)
    db.session.commit()
    return room, None


def join_room(room_code, player2_id):
    """
    لاعب ثاني يدخل الغرفة
    """
    room = Room.query.filter_by(room_code=room_code).first()
    if not room:
        return None, "الغرفة مش موجودة"

    if room.player2_id:
        return None, "الغرفة ممتلية"

    if room.player1_id == player2_id:
        return None, "مينفعش تلعب مع نفسك!"

    player2 = Player.query.get(player2_id)
    if not player2:
        return None, "اللاعب مش موجود"

    # تأكد من النقط لو مش وضع مفلسين
    if not room.is_bankrupt_mode:
        if not can_join_room(player2, room.bet_points):
            return None, f"معندكش نقط كافية! عندك {player2.points} نقطة فقط"

    room.player2_id = player2_id
    db.session.commit()
    return room, None


def get_room(room_code):
    return Room.query.filter_by(room_code=room_code).first()


def get_room_by_id(room_id):
    return Room.query.get(room_id)