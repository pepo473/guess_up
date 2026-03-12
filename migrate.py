"""
migrate.py
يشتغل أوتوماتيك من app.py عند كل تشغيل.
بيضيف أي columns ناقصة من غير ما يمسح البيانات القديمة.
"""
import sqlite3
import os


def run_migrations(db_path):
    """
    يفحص الـ DB ويضيف الـ columns اللي ناقصة
    """
    if not os.path.exists(db_path):
        return  # DB جديدة - SQLAlchemy هيعملها من الأول

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # ── اعرف الـ columns الموجودة في جدول players ──────────────────────────
    cursor.execute("PRAGMA table_info(players)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    # ── ضيف password_hash لو مش موجود ────────────────────────────────────
    if 'password_hash' not in existing_cols:
        print("[migrate] إضافة عمود password_hash لجدول players...")
        # قيمة افتراضية للحسابات القديمة (hash لكلمة 'changeme')
        from werkzeug.security import generate_password_hash
        default_hash = generate_password_hash('changeme')
        cursor.execute(
            f"ALTER TABLE players ADD COLUMN password_hash VARCHAR(256) NOT NULL DEFAULT '{default_hash}'"
        )
        conn.commit()
        print("[migrate] ✅ تم! كل الحسابات القديمة كلمة سرها: changeme")

    # ── ضيف أي columns تانية لو محتاجها مستقبلاً هنا ────────────────────

    conn.close()