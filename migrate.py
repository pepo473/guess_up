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

    conn.commit()
    conn.close()
    print("[migrate] ✅ Done")