"""migrate.py — يضيف columns جديدة بدون مسح بيانات"""
import sqlite3, os

def run_migrations(db_path):
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    c    = conn.cursor()

    def cols(t):
        c.execute(f"PRAGMA table_info({t})")
        return {r[1] for r in c.fetchall()}

    def tbls():
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {r[0] for r in c.fetchall()}

    T = tbls()

    # ── players ─────────────────────────────────────────────────────────────
    if 'players' in T:

        pc = cols('players')
        if 'is_deleted' not in pc:
            c.execute("ALTER TABLE players ADD COLUMN is_deleted BOOLEAN DEFAULT 0")
            print("[migrate] ✅ is_deleted column")
        if 'deleted_at' not in pc:
            c.execute("ALTER TABLE players ADD COLUMN deleted_at DATETIME")
        if 'is_banned' not in pc:
            c.execute("ALTER TABLE players ADD COLUMN is_banned BOOLEAN DEFAULT 0")
            print("[migrate] ✅ is_banned column")
        if 'ban_until' not in pc:
            c.execute("ALTER TABLE players ADD COLUMN ban_until DATETIME")
        if 'ban_reason' not in pc:
            c.execute("ALTER TABLE players ADD COLUMN ban_reason VARCHAR(200)")

        pc = cols('players')
        if 'password_hash' not in pc:
            from werkzeug.security import generate_password_hash
            h = generate_password_hash('changeme')
            c.execute(f"ALTER TABLE players ADD COLUMN password_hash VARCHAR(256) NOT NULL DEFAULT '{h}'")
            print("[migrate] ✅ password_hash — كلمة السر القديمة: changeme")
        if 'avatar' not in pc:
            c.execute("ALTER TABLE players ADD COLUMN avatar VARCHAR(200)")
        if 'xp' not in pc:
            c.execute("ALTER TABLE players ADD COLUMN xp INTEGER DEFAULT 0")
            print("[migrate] ✅ xp column")
        if 'win_streak' not in pc:
            c.execute("ALTER TABLE players ADD COLUMN win_streak INTEGER DEFAULT 0")
        if 'best_streak' not in pc:
            c.execute("ALTER TABLE players ADD COLUMN best_streak INTEGER DEFAULT 0")
        if 'last_daily_reward' not in pc:
            c.execute("ALTER TABLE players ADD COLUMN last_daily_reward DATETIME")

    # ── rooms ────────────────────────────────────────────────────────────────
    if 'rooms' in T:
        rc = cols('rooms')
        if 'is_public' not in rc:
            c.execute("ALTER TABLE rooms ADD COLUMN is_public BOOLEAN DEFAULT 0")
        if 'status' not in rc:
            c.execute("ALTER TABLE rooms ADD COLUMN status VARCHAR(20) DEFAULT 'waiting'")
        if 'random_event' not in rc:
            c.execute("ALTER TABLE rooms ADD COLUMN random_event VARCHAR(50)")
            print("[migrate] ✅ random_event column")

    # ── matches ───────────────────────────────────────────────────────────────
    if 'matches' in T:
        mc = cols('matches')
        if 'guess_log' not in mc:
            c.execute("ALTER TABLE matches ADD COLUMN guess_log TEXT")
            print("[migrate] ✅ guess_log column")
        # امسح المباريات الغلط
        c.execute("DELETE FROM matches WHERE player1_id = player2_id")
        if conn.total_changes > 0:
            print(f"[migrate] 🧹 تم حذف مباريات مكررة غلط")


    # ── friendships ─────────────────────────────────────────────────────────
    if 'friendships' not in T:
        c.execute("""CREATE TABLE IF NOT EXISTS friendships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL REFERENCES players(id),
            receiver_id INTEGER NOT NULL REFERENCES players(id),
            status VARCHAR(20) DEFAULT 'pending',
            created_at DATETIME,
            updated_at DATETIME,
            UNIQUE(sender_id, receiver_id))""")
        print("[migrate] ✅ friendships table")

    # ── notifications ────────────────────────────────────────────────────────
    if 'notifications' not in T:
        c.execute("""CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL REFERENCES players(id),
            type VARCHAR(30) NOT NULL,
            title VARCHAR(100) NOT NULL,
            body VARCHAR(255) NOT NULL,
            link VARCHAR(100),
            from_id INTEGER REFERENCES players(id),
            is_read BOOLEAN DEFAULT 0,
            created_at DATETIME)""")
        print("[migrate] ✅ notifications table")

    # ── chat_messages ────────────────────────────────────────────────────────
    if 'chat_messages' not in T:
        c.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL REFERENCES players(id),
            receiver_id INTEGER NOT NULL REFERENCES players(id),
            text VARCHAR(500) NOT NULL,
            is_read BOOLEAN DEFAULT 0,
            created_at DATETIME)""")
        print("[migrate] ✅ chat_messages table")


    # ── group_rooms ──────────────────────────────────────────────────────────
    if 'group_rooms' not in T:
        c.execute("""CREATE TABLE IF NOT EXISTS group_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code VARCHAR(10) UNIQUE NOT NULL,
            host_id INTEGER REFERENCES players(id),
            max_players INTEGER DEFAULT 4,
            bet_points INTEGER DEFAULT 100,
            status VARCHAR(20) DEFAULT 'waiting',
            winner_id INTEGER REFERENCES players(id),
            created_at DATETIME)""")
        print("[migrate] ✅ group_rooms")

    if 'group_room_players' not in T:
        c.execute("""CREATE TABLE IF NOT EXISTS group_room_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER REFERENCES group_rooms(id),
            player_id INTEGER REFERENCES players(id),
            secret INTEGER,
            is_alive BOOLEAN DEFAULT 1,
            guesses_used INTEGER DEFAULT 0,
            joined_at DATETIME,
            UNIQUE(room_id, player_id))""")
        print("[migrate] ✅ group_room_players")

    # ── daily_challenges ─────────────────────────────────────────────────────
    if 'daily_challenges' not in T:
        c.execute("""CREATE TABLE IF NOT EXISTS daily_challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_str VARCHAR(10) UNIQUE,
            target INTEGER NOT NULL,
            created_at DATETIME)""")
        print("[migrate] ✅ daily_challenges")

    if 'daily_challenge_entries' not in T:
        c.execute("""CREATE TABLE IF NOT EXISTS daily_challenge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenge_id INTEGER REFERENCES daily_challenges(id),
            player_id INTEGER REFERENCES players(id),
            guesses INTEGER DEFAULT 0,
            guess_log TEXT,
            completed BOOLEAN DEFAULT 0,
            time_secs INTEGER DEFAULT 0,
            created_at DATETIME,
            UNIQUE(challenge_id, player_id))""")
        print("[migrate] ✅ daily_challenge_entries")


    # ── rooms: mode + max_players + timer ───────────────────────────────────
    if 'rooms' in T:
        rc = cols('rooms')
        if 'max_players' not in rc:
            c.execute("ALTER TABLE rooms ADD COLUMN max_players INTEGER DEFAULT 2")
            print("[migrate] ✅ max_players")
        if 'mode' not in rc:
            c.execute("ALTER TABLE rooms ADD COLUMN mode VARCHAR(20) DEFAULT 'classic'")
            print("[migrate] ✅ mode")
        if 'timer_seconds' not in rc:
            c.execute("ALTER TABLE rooms ADD COLUMN timer_seconds INTEGER DEFAULT 0")
        if 'group_secret' not in rc:
            c.execute("ALTER TABLE rooms ADD COLUMN group_secret INTEGER")

    # ── daily_challenges ─────────────────────────────────────────────────────
    if 'daily_challenges' not in T:
        c.execute("""CREATE TABLE IF NOT EXISTS daily_challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_str VARCHAR(10) UNIQUE NOT NULL,
            secret INTEGER NOT NULL,
            created_at DATETIME)""")
        print("[migrate] ✅ daily_challenges table")

    if 'daily_entries' not in T:
        c.execute("""CREATE TABLE IF NOT EXISTS daily_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL REFERENCES players(id),
            date_str VARCHAR(10) NOT NULL,
            guesses INTEGER DEFAULT 0,
            solved BOOLEAN DEFAULT 0,
            created_at DATETIME,
            UNIQUE(player_id, date_str))""")
        print("[migrate] ✅ daily_entries table")

    conn.commit()
    conn.close()
    print("[migrate] ✅ Done")