import random

class BotPlayer:
    """
    لاعب الكمبيوتر - بيختار رقم سري ويرد على تخمينات اللاعب
    """

    def __init__(self):
        self.secret_number = random.randint(1, 1000)
        self.guesses_count = 0

    def respond_to_guess(self, guess):
        """
        اللاعب قال رقم، الكمبيوتر يرد:
        - أعلى: لو الرقم أقل من السري
        - أقل: لو الرقم أكبر من السري
        - ده رقمي!: لو صح
        """
        self.guesses_count += 1
        guess = int(guess)

        if guess < self.secret_number:
            return {
                'result': 'higher',
                'message': '⬆️ أعلى من كده!',
                'correct': False,
                'guesses': self.guesses_count
            }
        elif guess > self.secret_number:
            return {
                'result': 'lower',
                'message': '⬇️ أقل من كده!',
                'correct': False,
                'guesses': self.guesses_count
            }
        else:
            return {
                'result': 'correct',
                'message': f'🎉 ده رقمي! اتعرفت في {self.guesses_count} محاولة',
                'correct': True,
                'secret': self.secret_number,
                'guesses': self.guesses_count
            }

    def get_hint(self):
        """
        تلميح للاعب لو طلب
        """
        low = max(1, self.secret_number - random.randint(50, 200))
        high = min(1000, self.secret_number + random.randint(50, 200))
        return f"🔍 تلميح: الرقم بين {low} و {high}"


# dict لتخزين جلسات البوت في الذاكرة (room_id -> BotPlayer)
_bot_sessions = {}


def create_bot_session(room_id):
    bot = BotPlayer()
    _bot_sessions[room_id] = bot
    return bot.secret_number  # مش بنكشفه، بس للـ server


def get_bot_session(room_id):
    return _bot_sessions.get(room_id)


def remove_bot_session(room_id):
    if room_id in _bot_sessions:
        del _bot_sessions[room_id]