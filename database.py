# -*- coding: utf-8 -*-
"""
database.py — Barcha SQLite bilan ishlash funksiyalari (jadval yaratish,
qo'shish, o'chirish, yangilash, o'qish). main.py dan ajratib olindi —
mantiq BIR QATOR HAM o'zgartirilmadi.
"""
import sqlite3
from datetime import datetime, timedelta
from config import *
# ==================== DATABASE ====================
# Butun bot davomida bitta umumiy ulanishdan foydalaniladi — har bir so'rov uchun
# alohida sqlite3.connect()/close() ochish ortiqcha xarajat va sekinlikka olib kelardi.
_DB_CONN = None

def get_db():
    global _DB_CONN
    if _DB_CONN is None:
        _DB_CONN = sqlite3.connect("anime.db", check_same_thread=False)
        _DB_CONN.execute("PRAGMA journal_mode=WAL")       # bir vaqtda o'qish va yozishga ruxsat beradi
        _DB_CONN.execute("PRAGMA synchronous=NORMAL")     # WAL bilan xavfsiz, sezilarli tezroq yozish
        _DB_CONN.execute("PRAGMA cache_size=-10000")      # ~10 MB keshni xotirada saqlaydi (ko'p o'qishlarni tezlashtiradi)
    return _DB_CONN

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""
        CREATE TABLE IF NOT EXISTS animes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code INTEGER UNIQUE,
            name TEXT,
            year INTEGER,
            genre TEXT,
            total_episodes INTEGER,
            description TEXT,
            poster_file_id TEXT,
            added_date TEXT
        )
    """)
    try:
        c.execute("ALTER TABLE animes ADD COLUMN channel_post_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # ustun allaqachon mavjud
    try:
        c.execute("ALTER TABLE animes ADD COLUMN episode_thumb_file_id TEXT")
    except sqlite3.OperationalError:
        pass  # ustun allaqachon mavjud
    c.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anime_code INTEGER,
            episode_num INTEGER,
            video_file_id TEXT,
            FOREIGN KEY(anime_code) REFERENCES animes(code)
        )
    """)
    try:
        c.execute("ALTER TABLE episodes ADD COLUMN added_at REAL")
    except sqlite3.OperationalError:
        pass  # ustun allaqachon mavjud
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            joined_date TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS required_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            link TEXT
        )
    """)
    try:
        c.execute("ALTER TABLE required_channels ADD COLUMN title TEXT")
    except sqlite3.OperationalError:
        pass  # ustun allaqachon mavjud
    try:
        c.execute("ALTER TABLE required_channels ADD COLUMN expires_at TEXT")
    except sqlite3.OperationalError:
        pass  # ustun allaqachon mavjud
    c.execute("""
        CREATE TABLE IF NOT EXISTS join_requests (
            chat_id TEXT,
            user_id INTEGER,
            requested_at TEXT,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER,
            added_date TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anime_code INTEGER,
            season_num INTEGER,
            poster_file_id TEXT,
            total_episodes INTEGER,
            added_date TEXT
        )
    """)
    try:
        c.execute("ALTER TABLE seasons ADD COLUMN poster_type TEXT DEFAULT 'photo'")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE seasons ADD COLUMN total_episodes_label TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE animes ADD COLUMN poster_type TEXT DEFAULT 'photo'")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE animes ADD COLUMN total_episodes_label TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE animes ADD COLUMN added_at REAL")
    except sqlite3.OperationalError:
        pass  # ustun allaqachon mavjud
    try:
        c.execute("ALTER TABLE animes ADD COLUMN status TEXT")
    except sqlite3.OperationalError:
        pass  # ustun allaqachon mavjud
    # Eski (vaqt belgisi yo'q) animelarga hozirgi vaqtni belgilaymiz — shunda ular darhol
    # 48 soatlik ro'yxatlarga (O'chirish/Tahrirlash/Kanalga Yuborish) kirib keladi.
    # Bu faqat vaqt belgisi ALI yo'q qatorlarga tegadi, shuning uchun xavfsiz bir martalik amal.
    c.execute("UPDATE animes SET added_at=? WHERE added_at IS NULL", (datetime.now().timestamp(),))
    try:
        c.execute("ALTER TABLE episodes ADD COLUMN season_id INTEGER")
    except sqlite3.OperationalError:
        pass  # ustun allaqachon mavjud

    # Tezlik uchun indekslar (anime/qism ko'payishi bilan sekinlashmasligi uchun)
    c.execute("CREATE INDEX IF NOT EXISTS idx_episodes_season ON episodes(season_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_episodes_season_num ON episodes(season_id, episode_num)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_seasons_anime ON seasons(anime_code)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_animes_code ON animes(code)")

    # Migratsiya: har bir anime uchun 1-fasl yozuvi yo'q bo'lsa, avtomatik yaratamiz
    # (eski, fasl tushunchasi bo'lmagan davrda qo'shilgan animelar/qismlar uchun)
    c.execute("SELECT code, total_episodes, poster_file_id FROM animes")
    for code, total_ep, poster_id in c.fetchall():
        c.execute("SELECT id FROM seasons WHERE anime_code=? AND season_num=1", (code,))
        if not c.fetchone():
            c.execute(
                "INSERT INTO seasons (anime_code, season_num, poster_file_id, total_episodes, added_date) VALUES (?, 1, ?, ?, ?)",
                (code, poster_id, total_ep, datetime.now().strftime("%Y-%m-%d"))
            )
            season1_id = c.lastrowid
            c.execute(
                "UPDATE episodes SET season_id=? WHERE anime_code=? AND season_id IS NULL",
                (season1_id, code)
            )
    conn.commit()

def get_next_code():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT MAX(code) FROM animes")
    result = c.fetchone()[0]
    return (result or 0) + 1

def add_anime(code, name, year, genre, total_episodes, description, poster_file_id, poster_type="photo", total_episodes_label=None):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now()
    c.execute("""
        INSERT INTO animes (code, name, year, genre, total_episodes, description, poster_file_id, added_date, poster_type, total_episodes_label, added_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (code, name, year, genre, total_episodes, description, poster_file_id, now.strftime("%Y-%m-%d"), poster_type, total_episodes_label, now.timestamp()))
    conn.commit()

ALLOWED_ANIME_FIELDS = {"name", "year", "genre", "total_episodes", "description"}

def update_anime_field(code, field, value):
    if field not in ALLOWED_ANIME_FIELDS:
        raise ValueError(f"Ruxsat etilmagan ustun: {field}")
    conn = get_db()
    c = conn.cursor()
    c.execute(f"UPDATE animes SET {field}=? WHERE code=?", (value, code))
    conn.commit()

def set_anime_status(code, status_key):
    if status_key not in ANIME_STATUSES:
        raise ValueError(f"Noma'lum holat: {status_key}")
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE animes SET status=? WHERE code=?", (status_key, code))
    conn.commit()

def get_anime_status(code):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT status FROM animes WHERE code=?", (code,))
    row = c.fetchone()
    return row[0] if row else None

def get_animes_by_status(status_key):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT code, name FROM animes WHERE status=? ORDER BY id", (status_key,))
    return c.fetchall()

def add_episode(season_id, episode_num, video_file_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO episodes (season_id, episode_num, video_file_id, added_at)
        VALUES (?, ?, ?, ?)
    """, (season_id, episode_num, video_file_id, datetime.now().timestamp()))
    conn.commit()

def get_recent_episodes(hours=36):
    """So'nggi `hours` soat ichida qo'shilgan qismlarni anime/fasl nomi bilan qaytaradi."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT s.anime_code, e.episode_num, e.added_at, a.name, s.season_num
        FROM episodes e
        JOIN seasons s ON e.season_id = s.id
        JOIN animes a ON s.anime_code = a.code
        WHERE e.added_at IS NOT NULL
        ORDER BY e.added_at DESC
    """)
    rows = c.fetchall()
    cutoff = datetime.now().timestamp() - hours * 3600
    return [r for r in rows if r[2] and r[2] >= cutoff]

def get_recent_episode_counts(hours=36):
    """So'nggi `hours` soat ichida har bir anime/fasl uchun qancha qism qo'shilganini qaytaradi.
    Natija: [(anime_code, anime_name, season_num, qo'shilgan_soni, jami_e'lon_qilingan_qismlar, label), ...]"""
    recent = get_recent_episodes(hours)
    counts = {}
    for anime_code, ep_num, added_at, name, season_num in recent:
        key = (anime_code, season_num)
        if key not in counts:
            counts[key] = {"name": name, "count": 0}
        counts[key]["count"] += 1
    result = []
    for (code, season_num), info in counts.items():
        season = get_season(code, season_num)
        total_ep = season[3] if season else 0
        ep_label = season[5] if season and len(season) > 5 else None
        result.append((code, info["name"], season_num, info["count"], total_ep, ep_label))
    return result

def get_anime_by_code(code):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM animes WHERE code=?", (code,))
    result = c.fetchone()
    return result

def get_anime_list_summary():
    """Barcha animelar, fasllar va yuklangan qismlar sonini atigi 3 ta SQL so'rov bilan
    qaytaradi (har bir anime/fasl uchun alohida so'rov ochish o'rniga)."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT code, name, channel_post_count FROM animes ORDER BY id")
    animes = c.fetchall()

    c.execute("SELECT id, anime_code, season_num, total_episodes, total_episodes_label FROM seasons ORDER BY anime_code, season_num")
    seasons_by_anime = {}
    for sid, anime_code, snum, s_total, s_label in c.fetchall():
        seasons_by_anime.setdefault(anime_code, []).append((sid, snum, s_total, s_label))

    c.execute("SELECT season_id, COUNT(*) FROM episodes GROUP BY season_id")
    ep_counts = {sid: cnt for sid, cnt in c.fetchall()}

    return animes, seasons_by_anime, ep_counts

def get_all_animes():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT code, name, year, genre, total_episodes FROM animes ORDER BY id")
    result = c.fetchall()
    return result

def get_recent_animes(hours=48):
    """So'nggi `hours` soat ichida qo'shilgan animelarni qaytaradi (added_at bo'yicha,
    eng yangisi birinchi). Eski (added_at=NULL yoki muddati o'tgan) animeler kirmaydi —
    ular bot ichida kod orqali ishlashda davom etadi, faqat bu ro'yxatlarda ko'rinmaydi."""
    conn = get_db()
    c = conn.cursor()
    cutoff = datetime.now().timestamp() - hours * 3600
    c.execute(
        "SELECT code, name, year, genre, total_episodes FROM animes "
        "WHERE added_at IS NOT NULL AND added_at >= ? ORDER BY added_at ASC",
        (cutoff,)
    )
    return c.fetchall()

def find_animes_by_name(name):
    """Nomi bo'yicha (harf katta-kichikligiga qaramasdan) o'xshash animelarni topadi."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT code, name FROM animes WHERE LOWER(name) LIKE ?", (f"%{name.lower()}%",))
    result = c.fetchall()
    return result

def increment_channel_post_count(code):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE animes SET channel_post_count = COALESCE(channel_post_count, 0) + 1 WHERE code=?", (code,))
    conn.commit()

def get_channel_post_count(code):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT channel_post_count FROM animes WHERE code=?", (code,))
    row = c.fetchone()
    return row[0] if row and row[0] else 0

# -- FASLLAR (SEASONS) --
def add_season(anime_code, season_num, poster_file_id, total_episodes, poster_type="photo", total_episodes_label=None):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO seasons (anime_code, season_num, poster_file_id, total_episodes, added_date, poster_type, total_episodes_label) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (anime_code, season_num, poster_file_id, total_episodes, datetime.now().strftime("%Y-%m-%d"), poster_type, total_episodes_label)
    )
    conn.commit()
    season_id = c.lastrowid
    return season_id

def update_season_total_episodes(season_id, total_episodes, total_episodes_label=None):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE seasons SET total_episodes=?, total_episodes_label=? WHERE id=?", (total_episodes, total_episodes_label, season_id))
    conn.commit()

def update_season_poster(season_id, poster_file_id, poster_type="photo"):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE seasons SET poster_file_id=?, poster_type=? WHERE id=?", (poster_file_id, poster_type, season_id))
    conn.commit()

def get_seasons(anime_code):
    """Berilgan anime uchun barcha fasllarni qaytaradi:
    [(id, season_num, poster_file_id, total_episodes, poster_type, total_episodes_label), ...]"""
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT id, season_num, poster_file_id, total_episodes, poster_type, total_episodes_label FROM seasons WHERE anime_code=? ORDER BY season_num",
        (anime_code,)
    )
    result = c.fetchall()
    return result

def get_season(anime_code, season_num):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT id, season_num, poster_file_id, total_episodes, poster_type, total_episodes_label FROM seasons WHERE anime_code=? AND season_num=?",
        (anime_code, season_num)
    )
    result = c.fetchone()
    return result

def get_season_by_id(season_id):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT id, anime_code, season_num, poster_file_id, total_episodes, poster_type, total_episodes_label FROM seasons WHERE id=?",
        (season_id,)
    )
    result = c.fetchone()
    return result

def get_episode(season_id, episode_num):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT video_file_id FROM episodes WHERE season_id=? AND episode_num=?",
              (season_id, episode_num))
    result = c.fetchone()
    return result

def get_episodes_list(season_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT episode_num FROM episodes WHERE season_id=? ORDER BY episode_num",
              (season_id,))
    result = c.fetchall()
    return [r[0] for r in result]

def delete_anime(code):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM seasons WHERE anime_code=?", (code,))
    season_ids = [r[0] for r in c.fetchall()]
    c.execute("DELETE FROM animes WHERE code=?", (code,))
    c.execute("DELETE FROM seasons WHERE anime_code=?", (code,))
    for sid in season_ids:
        c.execute("DELETE FROM episodes WHERE season_id=?", (sid,))
    conn.commit()

def delete_episode(season_id, episode_num):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM episodes WHERE season_id=? AND episode_num=?", (season_id, episode_num))
    conn.commit()

def update_episode_number(season_id, old_num, new_num):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE episodes SET episode_num=? WHERE season_id=? AND episode_num=?",
        (new_num, season_id, old_num)
    )
    conn.commit()

def get_monthly_stats():
    conn = get_db()
    c = conn.cursor()
    month = datetime.now().strftime("%Y-%m")
    c.execute("SELECT COUNT(*) FROM animes WHERE added_date LIKE ?", (f"{month}%",))
    animes_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users")
    users_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM animes")
    total_animes = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM episodes")
    total_episodes = c.fetchone()[0]
    return animes_count, users_count, total_animes, total_episodes

def add_required_channel(identifier, link, title=None, expires_at=None):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO required_channels (username, link, title, expires_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(username) DO UPDATE SET link=excluded.link, title=excluded.title, expires_at=excluded.expires_at",
        (identifier, link, title, expires_at)
    )
    conn.commit()

def remove_required_channel(identifier):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM required_channels WHERE username=?", (identifier,))
    conn.commit()

def cleanup_expired_channels():
    """Muddati o'tgan (1 hafta/15 kun/30 kun) kanallarni majburiy obuna ro'yxatidan avtomatik olib tashlaydi."""
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM required_channels WHERE expires_at IS NOT NULL AND expires_at <= ?", (datetime.now().isoformat(),))
    conn.commit()

def get_required_channels():
    cleanup_expired_channels()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, link, title, expires_at FROM required_channels")
    result = c.fetchall()
    return result

def register_user(user_id, username):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO users (id, username, joined_date)
        VALUES (?, ?, ?)
    """, (user_id, username or "", datetime.now().strftime("%Y-%m-%d")))
    conn.commit()

def get_all_users():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM users")
    rows = c.fetchall()
    return [r[0] for r in rows]

def add_admin(user_id, added_by):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO admins (user_id, added_by, added_date) VALUES (?, ?, ?)",
        (user_id, added_by, datetime.now().strftime("%Y-%m-%d"))
    )
    conn.commit()

def remove_admin(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    conn.commit()

def get_all_admins():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id, added_date FROM admins")
    rows = c.fetchall()
    return rows

def is_admin(user_id):
    """Asosiy admin yoki qo'shilgan sub-admin bo'lsa True qaytaradi."""
    if user_id == ADMIN_ID:
        return True
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
    result = c.fetchone()
    return result is not None


