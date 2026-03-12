from models import db, Player


def register_player(name, password):
    """إنشاء حساب جديد"""
    name = name.strip()
    if not name or not password:
        return None, "الاسم وكلمة السر مطلوبين"
    if len(password) < 4:
        return None, "كلمة السر لازم تكون 4 حروف على الأقل"
    if Player.query.filter_by(player_name=name).first():
        return None, "الاسم ده موجود بالفعل، جرب اسم تاني"
    player = Player(player_name=name, points=100)
    player.set_password(password)
    db.session.add(player)
    db.session.commit()
    return player, None


def login_player(name, password):
    """تسجيل دخول"""
    name = name.strip()
    player = Player.query.filter_by(player_name=name).first()
    if not player:
        return None, "الاسم ده مش موجود"
    if not player.check_password(password):
        return None, "كلمة السر غلط"
    return player, None


def get_player_by_id(player_id):
    return Player.query.filter_by(id=player_id).first()


def get_player_by_name(name):
    return Player.query.filter_by(player_name=name.strip()).first()


def is_logged_in(session):
    return 'player_id' in session