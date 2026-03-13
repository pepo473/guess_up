"""
edit_points.py — شغّله من Terminal عشان تعدل نقاط
الاستخدام:
    python edit_points.py show              ← شوف كل اللاعبين
    python edit_points.py set  اسم  500    ← حط نقاط بالظبط
    python edit_points.py add  اسم  200    ← أضف نقاط
    python edit_points.py sub  اسم  100    ← طرح نقاط
"""

import sys, sqlite3, os

DB = os.path.join('instance', 'guess_up.db')

def connect():
    if not os.path.exists(DB):
        print(f"❌ الداتابيس مش موجودة في: {DB}")
        sys.exit(1)
    return sqlite3.connect(DB)

def show_all():
    conn = connect()
    rows = conn.execute(
        "SELECT id, player_name, points, xp, win_streak FROM players ORDER BY points DESC"
    ).fetchall()
    print(f"\n{'#':<4} {'الاسم':<20} {'النقاط':<10} {'XP':<8} {'Streak'}")
    print("─" * 55)
    for r in rows:
        print(f"{r[0]:<4} {r[1]:<20} {r[2]:<10} {r[3] or 0:<8} {r[4] or 0}")
    print(f"\nإجمالي: {len(rows)} لاعب\n")
    conn.close()

def edit(action, name, amount):
    conn  = connect()
    row   = conn.execute("SELECT id, points FROM players WHERE player_name=?", (name,)).fetchone()
    if not row:
        print(f"❌ اللاعب '{name}' مش موجود")
        show_all()
        conn.close()
        return
    old_pts = row[1]
    pid     = row[0]
    if action == 'set':
        new_pts = amount
    elif action == 'add':
        new_pts = old_pts + amount
    elif action == 'sub':
        new_pts = max(0, old_pts - amount)
    conn.execute("UPDATE players SET points=? WHERE id=?", (new_pts, pid))
    conn.commit()
    print(f"✅ {name}: {old_pts} → {new_pts} نقطة")
    conn.close()

if __name__ == '__main__':
    args = sys.argv[1:]
    if not args or args[0] == 'show':
        show_all()
    elif len(args) == 3 and args[0] in ('set','add','sub'):
        edit(args[0], args[1], int(args[2]))
    else:
        print(__doc__)