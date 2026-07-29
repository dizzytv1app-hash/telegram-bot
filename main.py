import logging
import os
import re
import asyncio
import sqlite3
import threading
from datetime import datetime, timedelta
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, ConversationHandler,
    ChatJoinRequestHandler, filters
)

# ==================== CONFIG ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN muhit o'zgaruvchisi topilmadi. "
        "Botni ishga tushirishdan oldin BOT_TOKEN ni environment'ga o'rnating."
    )
ADMIN_ID = 6222096713
ADMIN_USERNAME = "@Reyimberganov_i"
CHANNEL_USERNAME = "@animelar_iqo"
CHANNEL_LINK = "https://t.me/animelar_iqo"
BOT_USERNAME = "Annimelar_bot"  # @ belgisiz, deep-link uchun (t.me/<BOT_USERNAME>?start=kod)

# Anime qo'shishda tanlash uchun janrlar ro'yxati (stikersiz)
GENRE_LIST = [
    "Jangari", "Sarguzasht", "Komediya", "Romantika", "Drama",
    "Fantastika", "Boshqa dunyo (Isekai)", "Ilmiy fantastika",
    "Qo'rqinchli", "Sirli", "Psixologik", "G'ayritabiiy",
    "Maktab", "Kundalik hayot", "Hentai", "Harem", "Ecchi",
    "Sport", "Jang san'ati", "Samuray",
]
GENRE_MIN_SELECT = 3
GENRE_MAX_SELECT = 4

# ==================== STATES ====================
(
    WAIT_ANIME_CODE, WAIT_ANIME_NAME, WAIT_ANIME_YEAR, WAIT_ANIME_GENRE,
    WAIT_ANIME_EPISODES, WAIT_ANIME_DESC, WAIT_ANIME_POSTER,
    WAIT_EPISODE_ANIME, WAIT_EPISODE_NUM, WAIT_EPISODE_VIDEO,
    WAIT_DELETE_CODE, WAIT_DELETE_CONFIRM, WAIT_ADD_CHANNEL,
    WAIT_EDIT_CODE, WAIT_EDIT_FIELD, WAIT_EDIT_VALUE,
    WAIT_BROADCAST_MSG, WAIT_EPM_ANIME, WAIT_EPM_ACTION, WAIT_EPM_NEWNUM,
    WAIT_NEWSEASON_EPISODES, WAIT_NEWSEASON_POSTER
) = range(22)

# ==================== BUTTON TEXTS (rejected during conversations) ====================
ADMIN_BUTTONS = {
    "➕ Anime Qo'shish", "📺 Qism Qo'shish", "📋 Animeler Ro'yxati",
    "📊 Statistika", "🗑 Anime O'chirish", "✏️ Anime Tahrirlash", "📡 Kanallar",
    "📣 Xabar Yuborish", "🔙 Asosiy Menu", "📤 Kanalga Yuborish", "👥 Adminlar",
    "🆕 Yangi Qismlar", "🛠 Qism Boshqarish", "🔗 Anime-Kanal Bog'lash",
    "🔍 Anime Izlash", "⏭ Shorts — Tez Orada!", "📢 Reklama", "📺 Animelar Kanali"
}

# Filter matching every reply-keyboard button — used as a universal conversation escape
_MENU_BTN_FILTER = filters.Regex(
    r"^(➕ Anime Qo'shish|📺 Qism Qo'shish|📋 Animeler Ro'yxati"
    r"|📊 Statistika|🗑 Anime O'chirish|✏️ Anime Tahrirlash|📡 Kanallar"
    r"|📣 Xabar Yuborish|🔙 Asosiy Menu|🔍 Anime Izlash"
    r"|📢 Reklama|📺 Animelar Kanali|📤 Kanalga Yuborish|👥 Adminlar|🆕 Yangi Qismlar|🛠 Qism Boshqarish|🔗 Anime-Kanal Bog'lash|⏭ Shorts.*)$"
)

# ==================== LOGGING ====================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def _check_menu_escape(update, context):
    """Raqam/matn so'raladigan holatlarda admin menyu tugmasini bossa,
    'faqat raqam yuboring' deb ushlab qolish o'rniga o'sha tugmaga o'tkazadi."""
    txt = update.message.text.strip() if update.message and update.message.text else ""
    if txt in ADMIN_BUTTONS:
        return await _interrupt_fallback(update, context)
    return None

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
        c.execute("ALTER TABLE animes ADD COLUMN own_channel_id TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE animes ADD COLUMN own_channel_link TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE animes ADD COLUMN own_channel_post_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
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

def get_animes_with_channel_status():
    """Barcha animelarni, ularning shaxsiy kanalga yuborilgan-yuborilmaganligi bilan qaytaradi:
    [(code, name, own_channel_post_count), ...]"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT code, name, own_channel_post_count FROM animes ORDER BY id")
    return c.fetchall()

def get_anime_channel(code):
    """Berilgan anime uchun bog'langan shaxsiy kanalni qaytaradi: (channel_id, channel_link, post_count) yoki None."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT own_channel_id, own_channel_link, own_channel_post_count FROM animes WHERE code=?", (code,))
    return c.fetchone()

def set_anime_channel(code, channel_id, channel_link):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE animes SET own_channel_id=?, own_channel_link=? WHERE code=?", (channel_id, channel_link, code))
    conn.commit()

def increment_own_channel_post_count(code):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE animes SET own_channel_post_count = own_channel_post_count + 1 WHERE code=?", (code,))
    conn.commit()

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

# ==================== HELPERS ====================
def has_pending_join_request(chat_id_str, user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM join_requests WHERE chat_id=? AND user_id=?", (chat_id_str, user_id))
    return c.fetchone() is not None

async def record_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yopiq kanalga yuborilgan qo'shilish so'rovini FAQAT yozib qo'yadi (tasdiqlamaydi) —
    admin so'rovlarni Telegram'ning o'zidan qo'lda tasdiqlaydi. Bot esa 'so'rov yuborilganini'
    obuna talabini qondirish uchun yetarli deb hisoblaydi."""
    req = update.chat_join_request
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO join_requests (chat_id, user_id, requested_at) VALUES (?, ?, ?)",
        (str(req.chat.id), req.from_user.id, datetime.now().isoformat())
    )
    conn.commit()

async def check_subscription(user_id, context):
    channels = get_required_channels()
    if not channels:
        channels = [(CHANNEL_USERNAME, CHANNEL_LINK, None, None)]
    not_joined = []
    for identifier, link, title, expires_at in channels:
        # Yopiq kanallar uchun identifier manfiy raqamli chat_id ("-100...") ko'rinishida saqlanadi
        chat_ref = int(identifier) if identifier.lstrip("-").isdigit() else identifier
        label = title or identifier
        try:
            member = await context.bot.get_chat_member(chat_ref, user_id)
            logger.info(f"[OBUNA-TEKSHIRUV] user_id={user_id} kanal={identifier} status={member.status!r}")
            if member.status not in ["member", "administrator", "creator"]:
                # A'zo emas — lekin qo'shilish so'rovi yuborgan bo'lsa, shuni yetarli deb hisoblaymiz
                # (admin so'rovni Telegram'ning o'zidan o'z vaqtida qo'lda tasdiqlaydi)
                if not has_pending_join_request(str(chat_ref), user_id):
                    not_joined.append((label, link))
        except Exception as e:
            logger.info(f"[OBUNA-TEKSHIRUV] user_id={user_id} kanal={identifier} XATO: {e!r}")
            if not has_pending_join_request(str(chat_ref), user_id):
                not_joined.append((label, link))
    return not_joined

def parse_channel_link(text):
    """Foydalanuvchi yuborgan matnni kanal havolasi sifatida tahlil qiladi.
    Qaytaradi: ("public", username) ochiq kanal uchun, yoki ("private", havola) yopiq kanal uchun.
    Noto'g'ri formatda bo'lsa None qaytaradi."""
    text = text.strip()
    if text.startswith("@"):
        uname = text[1:]
        if re.fullmatch(r"[A-Za-z0-9_]{4,}", uname):
            return ("public", uname)
        return None
    m = re.match(r"(?:https?://)?t\.me/(.+)", text, re.I)
    if not m:
        return None
    path = m.group(1).strip()
    if path.startswith("+") or path.startswith("joinchat/"):
        full_link = text if text.lower().startswith("http") else f"https://t.me/{path}"
        return ("private", full_link)
    uname = path.split("?")[0].strip("/")
    if re.fullmatch(r"[A-Za-z0-9_]{4,}", uname):
        return ("public", uname)
    return None

def _esc_md(text):
    """Foydalanuvchi kiritgan matnda Markdown maxsus belgilari bo'lsa,
    Telegram xabarni yubormay qolib ketishining oldini olish uchun ekranlaydi."""
    if text is None:
        return ""
    text = str(text)
    for ch in ("\\", "_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text

def _parse_episode_count(text):
    """Qism soni maydoniga so'z bilan yozilgan matnni ham qabul qiladi
    (masalan '24 qism 2-fasl'). Ichki hisob-kitob uchun birinchi topilgan
    raqamni ajratib oladi, ko'rsatish uchun esa yozilgan matnning o'zini saqlaydi."""
    text = text.strip()
    match = re.search(r"\d+", text)
    number = int(match.group()) if match else 0
    return number, text

def _episode_label(total_episodes, label):
    """Ko'rsatish uchun: agar admin so'z bilan yozgan bo'lsa o'shani, aks holda sonni qaytaradi."""
    if label:
        return label
    return str(total_episodes)

def _season_label(season_num, total_episodes):
    """Agar faslda jami 1 ta qism bo'lsa (film), 'N-fasl' o'rniga 'Film' deb ko'rsatadi."""
    if total_episodes == 1:
        return "🎬 Film"
    return f"{season_num}-fasl"

def main_menu_keyboard():
    keyboard = [
        [KeyboardButton("🔍 Anime Izlash")],
        [KeyboardButton("⏭ Shorts — Tez Orada!"), KeyboardButton("📢 Reklama")],
        [KeyboardButton("📺 Animelar Kanali")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def admin_menu_keyboard(user_id=None):
    keyboard = [
        [KeyboardButton("➕ Anime Qo'shish"), KeyboardButton("📺 Qism Qo'shish")],
        [KeyboardButton("📋 Animeler Ro'yxati"), KeyboardButton("📊 Statistika")],
        [KeyboardButton("🗑 Anime O'chirish"), KeyboardButton("✏️ Anime Tahrirlash")],
        [KeyboardButton("📡 Kanallar"), KeyboardButton("📣 Xabar Yuborish")],
        [KeyboardButton("📤 Kanalga Yuborish"), KeyboardButton("🆕 Yangi Qismlar")],
        [KeyboardButton("🔗 Anime-Kanal Bog'lash")],
        [KeyboardButton("🛠 Qism Boshqarish")],
    ]
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton("👥 Adminlar")])
    keyboard.append([KeyboardButton("🔙 Asosiy Menu")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def episodes_keyboard(season_id, page=0):
    episodes = get_episodes_list(season_id)
    per_page = 24
    start = page * per_page
    end = start + per_page
    page_episodes = episodes[start:end]
    total_pages = (len(episodes) - 1) // per_page + 1 if episodes else 1

    buttons = []
    row = []
    for i, ep in enumerate(page_episodes):
        row.append(InlineKeyboardButton(str(ep), callback_data=f"ep_{season_id}_{ep}"))
        if len(row) == 6:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⏮", callback_data=f"page_{season_id}_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if end < len(episodes):
        nav.append(InlineKeyboardButton("⏭", callback_data=f"page_{season_id}_{page+1}"))
    if nav:
        buttons.append(nav)

    return InlineKeyboardMarkup(buttons)

def genre_select_keyboard(selected):
    """selected — tanlangan janr indexlari to'plami"""
    buttons = []
    row = []
    for i, g in enumerate(GENRE_LIST):
        label = f"✅ {g}" if i in selected else g
        row.append(InlineKeyboardButton(label, callback_data=f"gsel_{i}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(f"➡️ Tasdiqlash ({len(selected)} ta tanlandi)", callback_data="gconfirm")])
    buttons.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_anime")])
    return InlineKeyboardMarkup(buttons)

async def _send_season_info(bot, chat_id, name, genre, desc, season_id, poster_id, total_ep, season_num=None, show_season_label=False, poster_type="photo", ep_label=None):
    episodes = get_episodes_list(season_id) if season_id else []
    title = f"🎬 *{_esc_md(name)}*"
    if show_season_label and season_num:
        title += f" — {_season_label(season_num, total_ep)}"
    ep_count_display = _episode_label(total_ep, ep_label)
    caption = (
        f"{title}\n\n"
        f"🎭 Janr: {genre}\n"
        f"📺 Jami qismlar: {ep_count_display} ta\n"
        f"✅ Yuklangan: {len(episodes)} ta\n\n"
        f"👇 Qismni tanlang:"
    )
    if desc:
        caption = caption.replace("👇 Qismni tanlang:", f"📝 {_esc_md(desc)}\n\n👇 Qismni tanlang:")

    if not poster_id:
        await bot.send_message(
            chat_id=chat_id, text=caption, parse_mode="Markdown",
            reply_markup=episodes_keyboard(season_id) if episodes else None
        )
        return

    send_fn = bot.send_video if poster_type == "video" else bot.send_photo
    media_kwarg = "video" if poster_type == "video" else "photo"

    if episodes:
        await send_fn(
            chat_id=chat_id, caption=caption,
            parse_mode="Markdown", reply_markup=episodes_keyboard(season_id),
            **{media_kwarg: poster_id}
        )
    else:
        await send_fn(
            chat_id=chat_id,
            caption=caption + "\n\n⚠️ Hali qism yuklanmagan!",
            parse_mode="Markdown",
            **{media_kwarg: poster_id}
        )

async def send_single_episode(bot, chat_id, code, season_num, ep_num):
    """Anime-kanal orqali 'Yuklab Olish' tugmasi bosilganda — faqat bitta aniq qismni yuboradi."""
    anime = get_anime_by_code(code)
    if not anime:
        await bot.send_message(chat_id=chat_id, text="❌ Bunday kodli anime topilmadi!")
        return
    name = anime[2]
    seasons = get_seasons(code)
    season = next((s for s in seasons if s[1] == season_num), None)
    if not season:
        await bot.send_message(chat_id=chat_id, text="❌ Bunday fasl topilmadi!")
        return
    sid = season[0]
    episode = get_episode(sid, ep_num)
    if not episode:
        await bot.send_message(chat_id=chat_id, text="❌ Bu qism hali yuklanmagan!")
        return
    season_label = f" — {_season_label(season_num, season[3])}" if len(seasons) > 1 else ""
    await bot.send_video(
        chat_id=chat_id,
        video=episode[0],
        caption=f"🎬 {name}{season_label} — {ep_num}-qism"
    )

async def send_anime_info(bot, chat_id, code):
    """Anime kodini kanal deep-link orqali yoki qo'lda yozilganda ko'rsatish uchun umumiy funksiya."""
    anime = get_anime_by_code(code)
    if not anime:
        await bot.send_message(chat_id=chat_id, text="❌ Bunday kodli anime topilmadi!")
        return
    _, code, name, year, genre, total_ep, desc, poster_id, added_date, *_rest = anime
    seasons = get_seasons(code)

    if not seasons:
        # eski/fasl yaratilmagan holat uchun zaxira yo'l
        await _send_season_info(bot, chat_id, name, genre, desc, None, poster_id, total_ep)
        return

    if len(seasons) == 1:
        sid, snum, s_poster, s_total, s_ptype, s_label = seasons[0]
        await _send_season_info(
            bot, chat_id, name, genre, desc, sid, s_poster, s_total,
            season_num=snum, show_season_label=False, poster_type=s_ptype, ep_label=s_label
        )
        return

    # bir nechta fasl bor — tanlash ro'yxatini ko'rsatish
    buttons = []
    for sid, snum, s_poster, s_total, s_ptype, s_label in seasons:
        added = len(get_episodes_list(sid))
        label = _episode_label(s_total, s_label)
        buttons.append([InlineKeyboardButton(f"{_season_label(snum, total_ep)} ({added}/{label})", callback_data=f"showseason_{sid}")])
    await bot.send_message(
        chat_id=chat_id,
        text=f"🎬 *{_esc_md(name)}*\n\nQaysi faslni tomosha qilmoqchisiz?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def show_season_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    season_id = int(query.data[11:])
    season = get_season_by_id(season_id)
    if not season:
        await query.answer("❌ Bu fasl endi topilmadi.", show_alert=True)
        return
    _, anime_code, season_num, poster_id, total_ep, poster_type, ep_label = season
    anime = get_anime_by_code(anime_code)
    if not anime:
        return
    name, genre, desc = anime[2], anime[4], anime[6]
    await _send_season_info(
        context.bot, query.from_user.id, name, genre, desc,
        season_id, poster_id, total_ep, season_num=season_num, show_season_label=True,
        poster_type=poster_type, ep_label=ep_label
    )

async def post_anime_to_channel(context, code, name, genre, total_episodes, poster_id, poster_type="photo", ep_label=None):
    """Admin '📤 Kanalga Yuborish' tugmasi orqali bossagina kanalga poster + ma'lumot joylash."""
    watch_url = f"https://t.me/{BOT_USERNAME}?start={code}"
    ep_count_display = _episode_label(total_episodes, ep_label)
    caption = (
        f"🎬 {_esc_md(name)}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n\n"
        f"🎞 Qismi: {ep_count_display}\n"
        f"🎭 Janri: {genre}\n"
        f"🆔 Anime kodi: {code}\n"
        f"📢 Kanal: {CHANNEL_USERNAME}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n\n"
        f"🔗 Yuklab olish: {watch_url}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Tomosha qilish", url=watch_url)]])
    try:
        if poster_type == "video":
            await context.bot.send_video(
                chat_id=CHANNEL_USERNAME, video=poster_id,
                caption=caption, reply_markup=kb
            )
        else:
            await context.bot.send_photo(
                chat_id=CHANNEL_USERNAME, photo=poster_id,
                caption=caption, reply_markup=kb
            )
        return True
    except Exception as e:
        logger.warning(f"Kanalga post yuborishda xato: {e}")
        return False

# ==================== HANDLERS ====================

async def require_subscription(update, context, pending_code=None):
    """Obunani faqat foydalanuvchi botdan haqiqatan foydalanmoqchi bo'lganda (kod yuborganda
    yoki anime linki orqali kirganda) tekshiradi. Admin uchun har doim True qaytaradi.
    Obuna bo'lmagan bo'lsa — tugmalarni ko'rsatib, False qaytaradi (chaqiruvchi to'xtashi kerak)."""
    user = update.effective_user
    if is_admin(user.id):
        return True
    not_joined = await check_subscription(user.id, context)
    if not not_joined:
        return True
    if pending_code is not None:
        context.user_data["pending_anime_code"] = pending_code
    buttons = [[InlineKeyboardButton(f"📢 {u} ga Obuna Bo'lish", url=lnk)] for u, lnk in not_joined]
    buttons.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub")])
    await update.message.reply_text(
        "🚫 Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username)

    deep_code = None
    if context.args:
        arg = context.args[0]
        if arg.isdigit():
            deep_code = int(arg)
        else:
            parts = arg.split("_")
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                deep_code = (int(parts[0]), int(parts[1]), int(parts[2]))  # (kod, fasl, qism)

    # Obuna faqat anime kodi orqali (havola bilan) kirganda tekshiriladi —
    # oddiy /start da botning asosiy menyusi darhol ko'rsatiladi.
    if deep_code is not None:
        if not await require_subscription(update, context, pending_code=deep_code):
            return

    await send_start(update, context, deep_code)

async def send_start(update, context, deep_code=None):
    if deep_code is not None:
        if isinstance(deep_code, tuple):
            code, season_num, ep_num = deep_code
            await send_single_episode(context.bot, update.effective_chat.id, code, season_num, ep_num)
        else:
            await send_anime_info(context.bot, update.effective_chat.id, deep_code)
        return

    text = (
        "👺 Assalomu aleykum botimizga xush kelibsiz.\n\n"
        "🖥 Botimizda animelerni yuklab olib, tomosha qilishingiz mumkin.\n\n"
        "‼️ Botga to'g'ri kodni yuborishingiz mumkin!"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Anime Izlash", callback_data="anime_search")],
        [InlineKeyboardButton("⚙️ Kabinet", callback_data="kabinet"), InlineKeyboardButton("🔴 Shorts", callback_data="shorts")],
        [InlineKeyboardButton("📺 Animelar Kanali", url=CHANNEL_LINK), InlineKeyboardButton("📢 Reklama", callback_data="reklama")],
    ])
    await update.message.reply_text(text, reply_markup=keyboard)

async def check_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    not_joined = await check_subscription(query.from_user.id, context)
    if not not_joined:
        await query.answer()
        await query.message.delete()
        pending_code = context.user_data.pop("pending_anime_code", None)
        if pending_code is not None:
            if isinstance(pending_code, tuple):
                code, season_num, ep_num = pending_code
                await send_single_episode(context.bot, query.from_user.id, code, season_num, ep_num)
            else:
                await send_anime_info(context.bot, query.from_user.id, pending_code)
        else:
            await send_start_from_callback(query, context)
    else:
        await query.answer(
            "❌ Hali barcha kanallarga obuna bo'lmadingiz!",
            show_alert=True
        )

async def send_start_from_callback(query, context):
    user = query.from_user
    user_is_admin = is_admin(user.id)
    text = (
        "👺 Assalomu aleykum botimizga xush kelibsiz.\n\n"
        "🖥 Botimizda animelerni yuklab olib, tomosha qilishingiz mumkin.\n\n"
        "‼️ Botga to'g'ri kodni yuborishingiz mumkin!"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Anime Izlash", callback_data="anime_search")],
        [InlineKeyboardButton("⚙️ Kabinet", callback_data="kabinet"), InlineKeyboardButton("🔴 Shorts", callback_data="shorts")],
        [InlineKeyboardButton("📺 Animelar Kanali", url=CHANNEL_LINK), InlineKeyboardButton("📢 Reklama", callback_data="reklama")],
    ])
    if user_is_admin:
        await context.bot.send_message(
            chat_id=user.id,
            text=f"👑 Admin paneliga xush kelibsiz!\n\n{text}",
            reply_markup=admin_menu_keyboard(user.id)
        )
    else:
        await context.bot.send_message(
            chat_id=user.id,
            text=text,
            reply_markup=keyboard
        )

# ==================== USER HANDLERS ====================

async def anime_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔢 Anime kodini yuboring (masalan: 1, 2, 3...)")

async def shorts_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔴 Shorts — Tez Orada! Kuting...")

async def reklama_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📩 Admin bilan bog'lanish", url="https://t.me/Reyimberganov_i")]
    ])
    await update.message.reply_text(
        "📢 Reklama berish uchun admin bilan bog'laning:",
        reply_markup=keyboard
    )

async def channel_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📺 Kanalga o'tish", url=CHANNEL_LINK)]
    ])
    await update.message.reply_text(
        f"📺 Bizning animelar kanalimiz:\n{CHANNEL_LINK}",
        reply_markup=keyboard
    )

async def kabinet_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT joined_date FROM users WHERE id=?", (user.id,))
    row = c.fetchone()
    joined = row[0] if row else "—"
    await update.message.reply_text(
        f"⚙️ *Kabinet*\n\n"
        f"👤 Ism: {user.full_name}\n"
        f"🆔 ID: {user.id}\n"
        f"📅 Ro'yxatdan o'tgan: {joined}",
        parse_mode="Markdown"
    )

# ==================== INLINE BUTTON CALLBACKS ====================

async def inline_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "anime_search":
        await query.message.reply_text("🔢 Anime kodini yuboring (masalan: 1, 2, 3...)")

    elif data == "shorts":
        await query.message.reply_text("🔴 Shorts — Tez Orada! Kuting...")

    elif data == "reklama":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📩 Admin bilan bog'lanish", url="https://t.me/Reyimberganov_i")]
        ])
        await query.message.reply_text(
            "📢 Reklama berish uchun admin bilan bog'laning:",
            reply_markup=keyboard
        )

    elif data == "kabinet":
        user = query.from_user
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT joined_date FROM users WHERE id=?", (user.id,))
        row = c.fetchone()
        joined = row[0] if row else "—"
        await query.message.reply_text(
            f"⚙️ *Kabinet*\n\n"
            f"👤 Ism: {user.full_name}\n"
            f"🆔 ID: {user.id}\n"
            f"📅 Ro'yxatdan o'tgan: {joined}",
            parse_mode="Markdown"
        )

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Handle awaiting channel input from admin
    if is_admin(update.effective_user.id) and context.user_data.get("awaiting_channel"):
        await got_add_channel(update, context)
        return

    # Handle admin qo'lda kiritayotgan kanal nomi
    if is_admin(update.effective_user.id) and context.user_data.get("awaiting_channel_title"):
        await got_channel_title(update, context)
        return

    # Handle admin "Kanalga Yuborish" uchun qo'lda kod kiritishi
    if is_admin(update.effective_user.id) and context.user_data.get("awaiting_channel_send_code"):
        await got_channel_send_code(update, context)
        return

    # Handle "Anime-Kanal Bog'lash" uchun anime kodi va kanal havolasi kiritishi
    if is_admin(update.effective_user.id) and context.user_data.get("awaiting_anime_channel_code"):
        await got_anime_channel_code(update, context)
        return
    if is_admin(update.effective_user.id) and context.user_data.get("awaiting_anime_channel_link"):
        await got_anime_channel_link(update, context)
        return

    # Handle awaiting new-admin ID input from super admin
    if update.effective_user.id == ADMIN_ID and context.user_data.get("awaiting_admin_id"):
        await got_add_admin_id(update, context)
        return

    # Admin menu buttons
    if is_admin(update.effective_user.id):
        if text == "🔙 Asosiy Menu":
            await update.message.reply_text("Asosiy menu:", reply_markup=main_menu_keyboard())
            return
        if text == "📊 Statistika":
            await show_stats(update, context)
            return
        if text == "📋 Animeler Ro'yxati":
            await show_anime_list(update, context)
            return

    if not text.isdigit():
        return

    code = int(text)
    if not await require_subscription(update, context, pending_code=code):
        return
    await send_anime_info(context.bot, update.effective_chat.id, code)

async def episode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "noop":
        return

    if data.startswith("page_"):
        _, season_id, page = data.split("_")
        await query.edit_message_reply_markup(
            reply_markup=episodes_keyboard(int(season_id), int(page))
        )
        return

    if data.startswith("ep_"):
        _, season_id, ep_num = data.split("_")
        episode = get_episode(int(season_id), int(ep_num))
        if episode:
            season = get_season_by_id(int(season_id))
            anime_name = ""
            if season:
                anime = get_anime_by_code(season[1])
                if anime:
                    anime_name = anime[2]
            await context.bot.send_video(
                chat_id=query.from_user.id,
                video=episode[0],
                caption=f"🎬 {anime_name} — {ep_num}-qism"
            )
        else:
            await query.answer("❌ Bu qism hali yuklanmagan!", show_alert=True)

# ==================== ADMIN HANDLERS ====================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ruxsat yo'q!")
        return
    await update.message.reply_text("👑 Admin paneli:", reply_markup=admin_menu_keyboard(update.effective_user.id))

# -- ADD ANIME helpers --
def _cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_anime")]])

async def cancel_anime_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.message.reply_text("❌ Bekor qilindi.", reply_markup=admin_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

# -- ADD ANIME --
async def add_anime_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        "➕ *Yangi anime qo'shish*\n\n"
        "1️⃣ Anime kodini yozing:\n_(faqat raqam, masalan: 101)_",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    await update.message.reply_text("👇", reply_markup=_cancel_kb())
    return WAIT_ANIME_CODE

async def got_anime_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _esc_result = await _check_menu_escape(update, context)
    if _esc_result is not None:
        return _esc_result
    txt = update.message.text.strip()
    if not txt.isdigit() or int(txt) <= 0:
        await update.message.reply_text(
            "⚠️ Faqat musbat *raqam* yuboring (masalan: 101):",
            parse_mode="Markdown", reply_markup=_cancel_kb()
        )
        return WAIT_ANIME_CODE
    code = int(txt)
    if get_anime_by_code(code):
        await update.message.reply_text(
            f"⚠️ *{code}* kodli anime allaqachon mavjud!\n"
            "Boshqa kod kiriting:",
            parse_mode="Markdown", reply_markup=_cancel_kb()
        )
        return WAIT_ANIME_CODE
    context.user_data["new_anime_code"] = code
    await update.message.reply_text(
        "2️⃣ Anime nomini yozing:",
        reply_markup=_cancel_kb()
    )
    return WAIT_ANIME_NAME

async def got_anime_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt in ADMIN_BUTTONS or len(txt) == 0:
        await update.message.reply_text(
            "⚠️ Tugma bosildi yoki bo'sh yuborildi.\nAnime nomini *matn* ko'rinishida yozing:",
            parse_mode="Markdown", reply_markup=_cancel_kb()
        )
        return WAIT_ANIME_NAME
    context.user_data["new_anime_name"] = txt

    matches = find_animes_by_name(txt)
    if matches:
        lines = "\n".join(f"• {code} — {_esc_md(name)}" for code, name in matches)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Ha, baribir qo'shish", callback_data="dupanime_yes"),
            InlineKeyboardButton("❌ Yo'q, bekor qilish", callback_data="cancel_anime"),
        ]])
        await update.message.reply_text(
            f"⚠️ *Diqqat!* Shu nomga o'xshash anime(lar) bazada allaqachon bor:\n\n{lines}\n\n"
            f"Baribir yangi qo'shishni davom ettirasizmi?",
            parse_mode="Markdown",
            reply_markup=kb
        )
        return WAIT_ANIME_NAME

    context.user_data["new_anime_genre_sel"] = set()
    await update.message.reply_text(
        "3️⃣ Janrlarni tanlang _(xohlagancha, kamida 1 ta)_:",
        parse_mode="Markdown",
        reply_markup=genre_select_keyboard(set())
    )
    return WAIT_ANIME_GENRE

async def got_anime_name_dup_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["new_anime_genre_sel"] = set()
    await query.edit_message_text("👍 Davom etyapmiz.")
    await query.message.reply_text(
        "3️⃣ Janrlarni tanlang _(xohlagancha, kamida 1 ta)_:",
        parse_mode="Markdown",
        reply_markup=genre_select_keyboard(set())
    )
    return WAIT_ANIME_GENRE

async def got_anime_genre_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    selected = context.user_data.setdefault("new_anime_genre_sel", set())

    if data.startswith("gsel_"):
        idx = int(data[5:])
        if idx in selected:
            selected.discard(idx)
        else:
            selected.add(idx)
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=genre_select_keyboard(selected))
        return WAIT_ANIME_GENRE

    if data == "gconfirm":
        if len(selected) < 1:
            await query.answer("⚠️ Kamida 1 ta janr tanlang!", show_alert=True)
            return WAIT_ANIME_GENRE
        await query.answer()
        genre_text = ", ".join(GENRE_LIST[i] for i in sorted(selected))
        context.user_data["new_anime_genre"] = genre_text
        await query.edit_message_text(f"✅ Tanlangan janrlar: {genre_text}")
        await query.message.reply_text(
            "4️⃣ Necha qismli:\n_(faqat raqam, masalan: 24)_",
            parse_mode="Markdown", reply_markup=_cancel_kb()
        )
        return WAIT_ANIME_EPISODES

async def got_anime_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _esc_result = await _check_menu_escape(update, context)
    if _esc_result is not None:
        return _esc_result
    txt = update.message.text.strip()
    if not txt:
        await update.message.reply_text(
            "⚠️ Bo'sh yuborildi. Qism sonini yozing (masalan: 24 yoki \"24 qism 2-fasl\"):",
            reply_markup=_cancel_kb()
        )
        return WAIT_ANIME_EPISODES
    number, label = _parse_episode_count(txt)
    context.user_data["new_anime_episodes"] = number
    context.user_data["new_anime_episodes_label"] = label
    await update.message.reply_text(
        "5️⃣ Poster rasm yoki video yuboring:\n_(rasm yoki video fayl yuborishingiz mumkin)_",
        parse_mode="Markdown", reply_markup=_cancel_kb()
    )
    return WAIT_ANIME_POSTER

async def got_anime_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo and not update.message.video:
        await update.message.reply_text(
            "⚠️ Faqat *rasm yoki video* yuboring:",
            parse_mode="Markdown", reply_markup=_cancel_kb()
        )
        return WAIT_ANIME_POSTER
    if update.message.video:
        poster_id = update.message.video.file_id
        poster_type = "video"
    else:
        poster_id = update.message.photo[-1].file_id
        poster_type = "photo"
    d = context.user_data
    ep_label = d.get("new_anime_episodes_label")
    try:
        add_anime(
            d["new_anime_code"], d["new_anime_name"], 0,
            d["new_anime_genre"], d["new_anime_episodes"], "", poster_id,
            poster_type=poster_type, total_episodes_label=ep_label
        )
        add_season(
            d["new_anime_code"], 1, poster_id, d["new_anime_episodes"],
            poster_type=poster_type, total_episodes_label=ep_label
        )
        ep_display = _episode_label(d["new_anime_episodes"], ep_label)
        await update.message.reply_text(
            f"✅ Anime muvaffaqiyatli qo'shildi!\n\n"
            f"📌 Kod: {d['new_anime_code']}\n"
            f"🎬 Nom: {d['new_anime_name']}\n"
            f"🎭 Janr: {d['new_anime_genre']}\n"
            f"📺 Qismlar: {ep_display} ta\n\n"
            f"ℹ️ Kanalga yuborish uchun 📤 Kanalga Yuborish tugmasidan foydalaning.",
            reply_markup=admin_menu_keyboard(update.effective_user.id)
        )
    except Exception as e:
        logger.warning(f"Anime qo'shishda xato: {e}")
        await update.message.reply_text(
            f"❌ Anime saqlashda xato yuz berdi:\n{e}",
            reply_markup=admin_menu_keyboard(update.effective_user.id)
        )
    context.user_data.clear()
    return ConversationHandler.END

# -- ADD EPISODE --
def _done_ep_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Hozircha tugatish", callback_data="done_episodes")]])

async def recent_episodes_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'🆕 Yangi Qismlar' — so'nggi 36 soatda qo'shilgan qismlarni ko'rsatadi (bot ichida, kanalga tegishli emas)."""
    recent = get_recent_episodes(hours=36)
    if not recent:
        await update.effective_message.reply_text("🆕 Hozircha so'nggi 36 soatda yangi qism qo'shilmagan.")
        return
    buttons = []
    seen_codes = []
    for anime_code, ep_num, added_at, name in recent:
        label = f"{name} — {ep_num}-qism"
        buttons.append([InlineKeyboardButton(label, callback_data=f"recep_{anime_code}")])
        if anime_code not in seen_codes:
            seen_codes.append(anime_code)
    await update.effective_message.reply_text(
        "🆕 *So'nggi 36 soatda qo'shilgan qismlar:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def recent_episode_open_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    code = int(query.data[6:])
    await send_anime_info(context.bot, query.from_user.id, code)

# -- ADMIN: YANGI QISMLAR (kanalga xabar yuborish uchun) --
RECENT_EP_PAGE_SIZE = 15

def _recent_ep_page_kb(grouped, page):
    start = page * RECENT_EP_PAGE_SIZE
    chunk = grouped[start:start + RECENT_EP_PAGE_SIZE]
    buttons = [
        [InlineKeyboardButton(f"🎬 {name} — {season_num}-fasl — {count} ta yangi qism", callback_data=f"annep_{code}_{season_num}")]
        for code, name, season_num, count, total_ep, ep_label in chunk
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Oldingi", callback_data=f"annpage_{page-1}"))
    if start + RECENT_EP_PAGE_SIZE < len(grouped):
        nav.append(InlineKeyboardButton("Keyingi ▶️", callback_data=f"annpage_{page+1}"))
    if nav:
        buttons.append(nav)
    return buttons

async def admin_recent_episodes_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    grouped = get_recent_episode_counts(hours=36)
    if not grouped:
        await update.message.reply_text("🆕 Hozircha so'nggi 36 soatda yangi qism qo'shilmagan.")
        return
    # Eng yangi qo'shilganlari birinchi ko'rinishi uchun teskari tartibga solamiz
    grouped = list(reversed(grouped))
    total_pages = (len(grouped) - 1) // RECENT_EP_PAGE_SIZE + 1
    await update.message.reply_text(
        f"🆕 *So'nggi 36 soatda yangi qism qo'shilgan animelar* (1/{total_pages}-sahifa)\n\n"
        "Kanalga xabar berish uchun animeni tanlang:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(_recent_ep_page_kb(grouped, 0))
    )

async def recent_ep_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    page = int(query.data.split("_")[1])
    grouped = list(reversed(get_recent_episode_counts(hours=36)))
    total_pages = (len(grouped) - 1) // RECENT_EP_PAGE_SIZE + 1
    await query.message.edit_text(
        f"🆕 *So'nggi 36 soatda yangi qism qo'shilgan animelar* ({page+1}/{total_pages}-sahifa)\n\n"
        "Kanalga xabar berish uchun animeni tanlang:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(_recent_ep_page_kb(grouped, page))
    )

async def admin_new_episode_channel_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Ruxsat yo'q!", show_alert=True)
        return
    _, code_str, season_num_str = query.data.split("_")
    code, season_num = int(code_str), int(season_num_str)
    grouped = {(c, sn): (name, count, total_ep, ep_label) for c, name, sn, count, total_ep, ep_label in get_recent_episode_counts(hours=36)}
    key = (code, season_num)
    if key not in grouped:
        await query.answer("❌ Bu anime endi ro'yxatda yo'q (36 soat o'tgan bo'lishi mumkin).", show_alert=True)
        return
    name, count, total_ep, ep_label = grouped[key]
    await query.answer()
    context.user_data["awaiting_episode_poster"] = {
        "code": code, "season_num": season_num, "count": count,
        "total_ep": total_ep, "ep_label": ep_label, "name": name,
    }
    await query.message.reply_text(
        f"🖼 *{_esc_md(name)}* — {season_num}-fasl uchun kanalga yuboriladigan rasm yoki video yuboring:",
        parse_mode="Markdown"
    )

async def got_episode_channel_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info = context.user_data.get("awaiting_episode_poster")
    if not info:
        return
    if not update.message.photo and not update.message.video:
        await update.message.reply_text("⚠️ Faqat rasm yoki video yuboring:")
        return
    if update.message.video:
        poster_id = update.message.video.file_id
        poster_type = "video"
    else:
        poster_id = update.message.photo[-1].file_id
        poster_type = "photo"
    context.user_data.pop("awaiting_episode_poster", None)
    code = info["code"]
    season_num = info["season_num"]
    count = info["count"]
    total_ep = info["total_ep"]
    ep_label = info.get("ep_label")
    name = info["name"]
    watch_url = f"https://t.me/{BOT_USERNAME}?start={code}"
    ep_display = _episode_label(total_ep, ep_label)
    caption = (
        f"🆕 *Yangi qism qo'shildi!*\n\n"
        f"🎬 {_esc_md(name)}\n"
        f"📺 Qism: {ep_display}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("💥 Tomosha qilish 💥", url=watch_url)]])
    try:
        if poster_type == "video":
            await context.bot.send_video(
                chat_id=CHANNEL_USERNAME, video=poster_id,
                caption=caption, parse_mode="Markdown", reply_markup=kb
            )
        else:
            await context.bot.send_photo(
                chat_id=CHANNEL_USERNAME, photo=poster_id,
                caption=caption, parse_mode="Markdown", reply_markup=kb
            )
        await update.message.reply_text(
            f"✅ Kanalga yuborildi: *{_esc_md(name)}* — {count} ta yangi qism.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning(f"Kanalga yangi qism xabarini yuborishda xato: {e}")
        await update.message.reply_text(f"❌ Kanalga yuborilmadi: {e}")

async def done_episodes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'✅ Hozircha tugatish' bosilganda — avval tasdiqlash so'raladi."""
    query = update.callback_query
    await query.answer()
    name = context.user_data.get("ep_anime_name", "")
    count = context.user_data.get("ep_added_count", 0)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ha", callback_data="done_ep_yes"),
        InlineKeyboardButton("❌ Yo'q", callback_data="done_ep_no"),
    ]])
    await query.message.reply_text(
        f"❗️ Rostdan ham yuklashni tugatmoqchimisiz?\n\n"
        f"🎬 Anime: {name}\n"
        f"📺 Hozircha qo'shilgan: *{count} ta* qism",
        parse_mode="Markdown",
        reply_markup=kb
    )
    return WAIT_EPISODE_VIDEO

async def done_episodes_confirm_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = context.user_data.get("ep_anime_name", "")
    count = context.user_data.get("ep_added_count", 0)
    context.user_data.clear()
    await query.edit_message_text(
        f"✅ *Qism qo'shish yakunlandi!*\n\n"
        f"🎬 Anime: {name}\n"
        f"📺 Qo'shilgan qismlar: *{count} ta*",
        parse_mode="Markdown"
    )
    await query.message.reply_text("👑 Admin paneli:", reply_markup=admin_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

async def done_episodes_confirm_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("👍 Davom eting, videolarni yuborishda davom etishingiz mumkin.")
    return WAIT_EPISODE_VIDEO

# -- QISM BOSHQARISH (o'chirish / raqam o'zgartirish) --
async def epm_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    animes = get_all_animes()
    if not animes:
        await update.message.reply_text("❌ Hali anime qo'shilmagan!")
        return ConversationHandler.END
    context.user_data.clear()
    lines = "\n".join(f"*{a[0]}* — {_esc_md(a[1])}" for a in animes)
    await update.message.reply_text(
        f"🛠 *Qism boshqarish*\n\nMavjud animeler:\n{lines}\n\nAnime kodini yuboring:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return WAIT_EPM_ANIME

async def got_epm_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _esc_result = await _check_menu_escape(update, context)
    if _esc_result is not None:
        return _esc_result
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text("⚠️ Faqat raqam (kod) yuboring!")
        return WAIT_EPM_ANIME
    code = int(txt)
    anime = get_anime_by_code(code)
    if not anime:
        await update.message.reply_text("⚠️ Bunday anime topilmadi! Kodini qayta yuboring:")
        return WAIT_EPM_ANIME
    seasons = get_seasons(code)
    if not seasons:
        await update.message.reply_text("❌ Bu animeda hali fasl/qism yo'q.")
        return ConversationHandler.END
    context.user_data["epm_code"] = code
    context.user_data["epm_name"] = anime[2]
    buttons = [
        [InlineKeyboardButton(f"{_season_label(snum, total_ep)} ({len(get_episodes_list(sid))} ta qism)", callback_data=f"epmseason_{sid}")]
        for sid, snum, poster_id, total_ep, poster_type, ep_label in seasons
    ]
    await update.message.reply_text(
        f"🛠 *{_esc_md(anime[2])}*\n\nQaysi faslni boshqarmoqchisiz?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return WAIT_EPM_ANIME

async def epm_season_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    season_id = int(query.data[10:])
    season = get_season_by_id(season_id)
    if not season:
        await query.edit_message_text("❌ Bu fasl endi topilmadi.")
        return ConversationHandler.END
    _, anime_code, season_num, poster_id, total_ep, *_rest = season
    episodes = get_episodes_list(season_id)
    if not episodes:
        await query.edit_message_text("❌ Bu faslda hali qism yo'q.")
        return ConversationHandler.END
    context.user_data["epm_season_id"] = season_id
    context.user_data["epm_season_num"] = season_num
    buttons = []
    row = []
    for ep in episodes:
        row.append(InlineKeyboardButton(str(ep), callback_data=f"epm_sel_{ep}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    name = context.user_data.get("epm_name", "")
    await query.edit_message_text(
        f"🛠 *{_esc_md(name)}* — *{season_num}-fasl*\n\nQaysi qism ustida amal bajarasiz?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return WAIT_EPM_ACTION

async def epm_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ep_num = int(query.data[8:])
    context.user_data["epm_num"] = ep_num
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 O'chirish", callback_data=f"epm_del_{ep_num}")],
        [InlineKeyboardButton("🔢 Raqamini o'zgartirish", callback_data=f"epm_ren_{ep_num}")],
        [InlineKeyboardButton("❌ Bekor qilish", callback_data="epm_cancel")],
    ])
    name = context.user_data.get("epm_name", "")
    season_num = context.user_data.get("epm_season_num")
    label = f"{name} — {season_num}-fasl" if season_num else name
    await query.edit_message_text(
        f"🛠 *{_esc_md(label)}* — *{ep_num}-qism*\n\nQaysi amalni bajarasiz?",
        parse_mode="Markdown",
        reply_markup=kb
    )
    return WAIT_EPM_ACTION

async def epm_delete_ask_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ep_num = int(query.data[8:])
    name = context.user_data.get("epm_name", "")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ha", callback_data=f"epm_delyes_{ep_num}"),
        InlineKeyboardButton("❌ Yo'q", callback_data="epm_cancel"),
    ]])
    await query.edit_message_text(
        f"❗️ Rostdan ham *{_esc_md(name)}* — *{ep_num}-qismni* o'chirmoqchimisiz?",
        parse_mode="Markdown",
        reply_markup=kb
    )
    return WAIT_EPM_ACTION

async def epm_delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ep_num = int(query.data[11:])
    season_id = context.user_data.get("epm_season_id")
    name = context.user_data.get("epm_name", "")
    delete_episode(season_id, ep_num)
    context.user_data.clear()
    await query.edit_message_text(f"✅ *{_esc_md(name)}* — *{ep_num}-qism* o'chirildi!", parse_mode="Markdown")
    await query.message.reply_text("👑 Admin paneli:", reply_markup=admin_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

async def epm_rename_ask_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ep_num = int(query.data[8:])
    context.user_data["epm_num"] = ep_num
    await query.edit_message_text(
        f"🔢 *{ep_num}-qism* uchun yangi raqamni yuboring:",
        parse_mode="Markdown"
    )
    return WAIT_EPM_NEWNUM

async def got_epm_newnum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _esc_result = await _check_menu_escape(update, context)
    if _esc_result is not None:
        return _esc_result
    txt = update.message.text.strip()
    if not txt.isdigit() or int(txt) <= 0:
        await update.message.reply_text("⚠️ Faqat musbat raqam yuboring:")
        return WAIT_EPM_NEWNUM
    new_num = int(txt)
    season_id = context.user_data.get("epm_season_id")
    old_num = context.user_data.get("epm_num")
    name = context.user_data.get("epm_name", "")
    if new_num == old_num:
        await update.message.reply_text("⚠️ Bu allaqachon shu raqam. Boshqa raqam yuboring:")
        return WAIT_EPM_NEWNUM
    existing = get_episodes_list(season_id)
    if new_num in existing:
        await update.message.reply_text(f"⚠️ *{new_num}-qism* raqami band. Boshqa raqam yuboring:", parse_mode="Markdown")
        return WAIT_EPM_NEWNUM
    update_episode_number(season_id, old_num, new_num)
    context.user_data.clear()
    await update.message.reply_text(
        f"✅ *{_esc_md(name)}*: {old_num}-qism → *{new_num}-qism* qilib o'zgartirildi!",
        parse_mode="Markdown",
        reply_markup=admin_menu_keyboard(update.effective_user.id)
    )
    return ConversationHandler.END

async def epm_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("❌ Bekor qilindi.")
    await query.message.reply_text("👑 Admin paneli:", reply_markup=admin_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

async def add_episode_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    animes = get_all_animes()
    if not animes:
        await update.message.reply_text("❌ Hali anime qo'shilmagan!")
        return ConversationHandler.END
    incomplete = []
    for code, name, year, genre, total_ep in animes:
        seasons = get_seasons(code)
        if not seasons:
            incomplete.append((code, name))
            continue
        for sid, snum, poster_id, s_total, poster_type, ep_label in seasons:
            added = len(get_episodes_list(sid))
            if added < s_total:
                incomplete.append((code, name))
                break
    if not incomplete:
        await update.message.reply_text(
            "✅ Barcha animelarning barcha fasllariga qismlar to'liq qo'shilgan!\n\n"
            "Yangi fasl qo'shish uchun baribir istalgan anime kodini yuboring:",
            reply_markup=ReplyKeyboardRemove()
        )
        await update.message.reply_text("👇", reply_markup=_cancel_kb())
        context.user_data.clear()
        return WAIT_EPISODE_ANIME
    context.user_data.clear()
    lines = "\n".join(f"*{code}* — {_esc_md(name)}" for code, name in incomplete)
    await update.message.reply_text(
        f"📺 *Qism qo'shish*\n\nQismi to'liq bo'lmagan animeler:\n{lines}\n\n1️⃣ Anime kodini yuboring:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    await update.message.reply_text("👇", reply_markup=_cancel_kb())
    return WAIT_EPISODE_ANIME

async def _show_season_picker(message, context, code, anime_name):
    seasons = get_seasons(code)
    buttons = []
    for sid, snum, poster_id, total_ep, poster_type, ep_label in seasons:
        added = len(get_episodes_list(sid))
        label = _episode_label(total_ep, ep_label)
        buttons.append([InlineKeyboardButton(f"{_season_label(snum, total_ep)} ({added}/{label})", callback_data=f"epseason_{sid}")])
    buttons.append([InlineKeyboardButton("➕ Yangi fasl qo'shish", callback_data="epseason_new")])
    await message.reply_text(
        f"✅ Anime: *{_esc_md(anime_name)}*\n\nQaysi faslga qism qo'shmoqchisiz?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return WAIT_EPISODE_NUM

async def got_episode_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _esc_result = await _check_menu_escape(update, context)
    if _esc_result is not None:
        return _esc_result
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text("⚠️ Faqat raqam (kod) yuboring!")
        return WAIT_EPISODE_ANIME
    code = int(txt)
    anime = get_anime_by_code(code)
    if not anime:
        await update.message.reply_text("⚠️ Bunday anime topilmadi! Kodini qayta yuboring:")
        return WAIT_EPISODE_ANIME
    context.user_data["ep_anime_code"] = code
    context.user_data["ep_anime_name"] = anime[2]
    return await _show_season_picker(update.message, context, code, anime[2])

def _season_upload_ready_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Qism yuklash", callback_data="ep_upload_start")],
        [InlineKeyboardButton("✅ Hozircha tugatish", callback_data="done_episodes")],
    ])

async def epseason_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    season_id = int(query.data[9:])
    season = get_season_by_id(season_id)
    if not season:
        await query.edit_message_text("❌ Bu fasl endi topilmadi.")
        return ConversationHandler.END
    _, anime_code, season_num, poster_id, total_ep, *_rest = season
    context.user_data["ep_season_id"] = season_id
    context.user_data["ep_season_num"] = season_num
    context.user_data["ep_added_count"] = 0
    existing = get_episodes_list(season_id)
    next_num = (max(existing) + 1) if existing else 1
    context.user_data["ep_next_num"] = next_num
    existing_str = ", ".join(str(e) for e in existing) if existing else "Yo'q"
    await query.edit_message_text(
        f"✅ Anime: *{_esc_md(context.user_data.get('ep_anime_name', ''))}*\n"
        f"🎬 Fasl: *{season_num}*\n"
        f"📌 Mavjud qismlar: {existing_str}\n"
        f"➡️ Keyingi qism *{next_num}*-dan boshlanadi.\n\n"
        f"Videolarni yuklashni boshlash uchun pastdagi tugmani bosing:",
        parse_mode="Markdown",
        reply_markup=_season_upload_ready_kb()
    )
    return WAIT_EPISODE_NUM

async def epseason_new_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🆕 *Yangi fasl* uchun jami qism sonini yuboring _(masalan: 24)_:",
        parse_mode="Markdown"
    )
    return WAIT_NEWSEASON_EPISODES

async def got_newseason_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _esc_result = await _check_menu_escape(update, context)
    if _esc_result is not None:
        return _esc_result
    txt = update.message.text.strip()
    if not txt:
        await update.message.reply_text("⚠️ Bo'sh yuborildi. Qism sonini yozing (masalan: 24 yoki \"24 qism 2-fasl\"):")
        return WAIT_NEWSEASON_EPISODES
    number, label = _parse_episode_count(txt)
    context.user_data["new_season_total"] = number
    context.user_data["new_season_total_label"] = label
    await update.message.reply_text("🖼 Endi shu fasl uchun poster rasm yoki video yuboring:")
    return WAIT_NEWSEASON_POSTER

async def got_newseason_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo and not update.message.video:
        await update.message.reply_text("⚠️ Faqat rasm yoki video yuboring:")
        return WAIT_NEWSEASON_POSTER
    if update.message.video:
        poster_id = update.message.video.file_id
        poster_type = "video"
    else:
        poster_id = update.message.photo[-1].file_id
        poster_type = "photo"
    code = context.user_data.get("ep_anime_code")
    total_ep = context.user_data.get("new_season_total", 0)
    ep_label = context.user_data.get("new_season_total_label")
    seasons = get_seasons(code)
    next_season_num = (max(s[1] for s in seasons) + 1) if seasons else 1
    season_id = add_season(code, next_season_num, poster_id, total_ep, poster_type=poster_type, total_episodes_label=ep_label)
    context.user_data["ep_season_id"] = season_id
    context.user_data["ep_season_num"] = next_season_num
    context.user_data["ep_added_count"] = 0
    context.user_data["ep_next_num"] = 1
    ep_display = _episode_label(total_ep, ep_label)
    await update.message.reply_text(
        f"✅ *{next_season_num}-fasl* yaratildi! ({ep_display} ta qism e'lon qilindi)\n\n"
        f"Videolarni yuklashni boshlash uchun pastdagi tugmani bosing:",
        parse_mode="Markdown",
        reply_markup=_season_upload_ready_kb()
    )
    return WAIT_EPISODE_NUM

async def got_episode_upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    next_num = context.user_data.get("ep_next_num", 1)
    await query.edit_message_text(
        f"🎬 Videolarni ketma-ket (yoki birdaniga) yuboring.\n\n"
        f"Men ularni avtomatik *{next_num}, {next_num + 1}, {next_num + 2}...* deb ketma-ket belgilab, saqlab boraman.\n\n"
        f"Yuklab bo'lgach, «✅ Hozircha tugatish» tugmasini bosing.",
        parse_mode="Markdown",
        reply_markup=_done_ep_kb()
    )
    return WAIT_EPISODE_VIDEO

async def got_episode_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.video and not update.message.document:
        await update.message.reply_text(
            "⚠️ Video fayl yuboring!",
            reply_markup=_done_ep_kb()
        )
        return WAIT_EPISODE_VIDEO

    file_id = update.message.video.file_id if update.message.video else update.message.document.file_id
    d = context.user_data
    ep_num = d.get("ep_next_num", 1)
    season_id = d.get("ep_season_id")
    season_num = d.get("ep_season_num", 1)
    add_episode(season_id, ep_num, file_id)
    d["ep_added_count"] = d.get("ep_added_count", 0) + 1
    d["ep_next_num"] = ep_num + 1
    await update.message.reply_text(
        f"✅ *{d['ep_anime_name']} — {season_num}-fasl — {ep_num}-qism* saqlandi!\n\n"
        f"Davom eting yoki «✅ Hozircha tugatish» tugmasini bosing.",
        parse_mode="Markdown",
        reply_markup=_done_ep_kb()
    )
    return WAIT_EPISODE_VIDEO

# -- DELETE ANIME --
async def delete_anime_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    animes = get_recent_animes(hours=48)
    if not animes:
        await update.message.reply_text(
            "❌ So'nggi 48 soatda qo'shilgan anime yo'q.\n"
            "Baribir o'chirmoqchi bo'lgan animening kodini bilsangiz, shuni yuborishingiz mumkin:",
            reply_markup=ReplyKeyboardRemove()
        )
        return WAIT_DELETE_CODE
    header = "🗑 O'chirmoqchi bo'lgan anime kodini yuboring:\n_(faqat so'nggi 48 soatda qo'shilganlar ko'rsatilmoqda)_\n\n"
    chunk = header
    for a in animes:
        line = f"*{a[0]}* — {_esc_md(a[1])} ({a[2]})\n"
        if len(chunk) + len(line) > 3500:
            await update.message.reply_text(chunk, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
            chunk = ""
        chunk += line
    chunk += "\n/cancel — bekor qilish"
    await update.message.reply_text(chunk, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    return WAIT_DELETE_CODE

async def got_delete_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _esc_result = await _check_menu_escape(update, context)
    if _esc_result is not None:
        return _esc_result
    if not update.message.text.isdigit():
        await update.message.reply_text("❌ Faqat raqam (kod) yuboring!")
        return WAIT_DELETE_CODE
    code = int(update.message.text)
    anime = get_anime_by_code(code)
    if not anime:
        await update.message.reply_text("❌ Bunday anime topilmadi! Kodini qayta yuboring:")
        return WAIT_DELETE_CODE
    context.user_data["delete_code"] = code
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Ha, o'chirish", callback_data="delconfirm_yes"),
         InlineKeyboardButton("❌ Yo'q", callback_data="delconfirm_no")]
    ])
    await update.message.reply_text(
        f"❗️ *Rostdan ham o'chirmoqchimisiz?*\n\n"
        f"📌 Kod: *{anime[1]}*\n"
        f"🎬 Nom: {_esc_md(anime[2])}",
        parse_mode="Markdown",
        reply_markup=kb
    )
    return WAIT_DELETE_CONFIRM

async def got_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    code = context.user_data.get("delete_code")

    if query.data == "delconfirm_no":
        context.user_data.clear()
        await query.message.edit_text("❌ Bekor qilindi, anime o'chirilmadi.")
        await query.message.reply_text("👑 Admin paneli:", reply_markup=admin_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END

    anime = get_anime_by_code(code)
    if not anime:
        context.user_data.clear()
        await query.message.edit_text("❌ Bunday anime endi topilmadi.")
        await query.message.reply_text("👑 Admin paneli:", reply_markup=admin_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END

    delete_anime(code)
    context.user_data.clear()
    await query.message.edit_text(f"✅ *{_esc_md(anime[2])}* o'chirildi!", parse_mode="Markdown")
    await query.message.reply_text("👑 Admin paneli:", reply_markup=admin_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

# -- EDIT ANIME --
def _edit_fields_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Nom", callback_data="editfield_name"),
         InlineKeyboardButton("📅 Yil", callback_data="editfield_year")],
        [InlineKeyboardButton("🎭 Janr", callback_data="editfield_genre"),
         InlineKeyboardButton("📺 Qismlar", callback_data="editfield_episodes")],
        [InlineKeyboardButton("📝 Tavsif", callback_data="editfield_desc")],
        [InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_anime")],
    ])

EDIT_FIELD_MAP = {
    "editfield_name":     ("name",           "🎬 Yangi nomni yozing:"),
    "editfield_year":     ("year",           "📅 Yangi yilni yozing:\n_(4 xonali raqam, masalan: 2023)_"),
    "editfield_genre":    ("genre",          "🎭 Yangi janrni yozing:"),
    "editfield_episodes": ("total_episodes", "📺 Yangi qismlar sonini yozing:\n_(masalan: 24 yoki \"24 qism 2-fasl\")_"),
    "editfield_desc":     ("description",    "📝 Yangi tavsifni yozing:"),
}

async def edit_anime_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data.clear()
    animes = get_recent_animes(hours=48)
    if not animes:
        await update.message.reply_text(
            "❌ So'nggi 48 soatda qo'shilgan anime yo'q.\n"
            "Baribir tahrirlamoqchi bo'lgan animening kodini bilsangiz, shuni yuborishingiz mumkin:",
            reply_markup=ReplyKeyboardRemove()
        )
        await update.message.reply_text("👇", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_anime")]]
        ))
        return WAIT_EDIT_CODE
    header = "✏️ *Anime Tahrirlash*\n_(faqat so'nggi 48 soatda qo'shilganlar ko'rsatilmoqda)_\n\nMavjud animeler:\n"
    chunk = header
    for a in animes:
        line = f"*{a[0]}* — {_esc_md(a[1])} ({a[2]})\n"
        if len(chunk) + len(line) > 3500:
            await update.message.reply_text(chunk, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
            chunk = ""
        chunk += line
    chunk += "\nTahrirlamoqchi bo'lgan anime kodini yozing:"
    await update.message.reply_text(chunk, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text("👇", reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_anime")]]
    ))
    return WAIT_EDIT_CODE

async def got_edit_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _esc_result = await _check_menu_escape(update, context)
    if _esc_result is not None:
        return _esc_result
    txt = update.message.text.strip()
    if not txt.isdigit():
        await update.message.reply_text(
            "⚠️ Faqat *raqam* yuboring:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_anime")]]))
        return WAIT_EDIT_CODE
    anime = get_anime_by_code(int(txt))
    if not anime:
        await update.message.reply_text(
            "⚠️ Bunday kodli anime topilmadi. Qaytadan kiriting:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_anime")]]))
        return WAIT_EDIT_CODE
    context.user_data["edit_code"] = int(txt)
    await update.message.reply_text(
        f"✅ Topildi!\n\n"
        f"📌 Kod: *{anime[1]}*\n"
        f"🎬 Nom: {_esc_md(anime[2])}\n"
        f"📅 Yil: {anime[3]}\n"
        f"🎭 Janr: {anime[4]}\n"
        f"📺 Qismlar: {anime[5]} ta\n"
        f"📝 Tavsif: {_esc_md(anime[6])}\n\n"
        f"Qaysi maydonni tahrirlaysiz?",
        parse_mode="Markdown",
        reply_markup=_edit_fields_kb()
    )
    return WAIT_EDIT_FIELD

async def got_edit_field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_anime")]])

    if data == "cancel_anime":
        context.user_data.clear()
        await query.message.reply_text("❌ Bekor qilindi.", reply_markup=admin_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END

    if data == "editfield_episodes":
        code = context.user_data.get("edit_code")
        seasons = get_seasons(code)
        if not seasons:
            db_field, prompt = EDIT_FIELD_MAP[data]
            context.user_data["edit_field"] = db_field
            context.user_data["edit_field_key"] = data
            await query.message.reply_text(prompt, parse_mode="Markdown", reply_markup=cancel_kb)
            return WAIT_EDIT_VALUE
        if len(seasons) == 1:
            sid, snum, poster_id, total_ep, poster_type, ep_label = seasons[0]
            context.user_data["edit_season_id"] = sid
            context.user_data["edit_field"] = "season_episodes"
            context.user_data["edit_field_key"] = data
            await query.message.reply_text(
                f"📺 *{_season_label(snum, total_ep)}* uchun yangi jami qism sonini yuboring:",
                parse_mode="Markdown", reply_markup=cancel_kb
            )
            return WAIT_EDIT_VALUE
        buttons = [
            [InlineKeyboardButton(f"{_season_label(snum, total_ep)} (hozir: {_episode_label(total_ep, ep_label)} ta)", callback_data=f"editseason_{sid}")]
            for sid, snum, poster_id, total_ep, poster_type, ep_label in seasons
        ]
        buttons.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_anime")])
        await query.message.reply_text(
            "📺 Qaysi faslning jami qism sonini o'zgartirasiz?",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return WAIT_EDIT_FIELD

    if data.startswith("editseason_"):
        sid = int(data[11:])
        context.user_data["edit_season_id"] = sid
        context.user_data["edit_field"] = "season_episodes"
        context.user_data["edit_field_key"] = "editfield_episodes"
        await query.message.reply_text(
            "📺 Yangi jami qism sonini yuboring:",
            reply_markup=cancel_kb
        )
        return WAIT_EDIT_VALUE

    db_field, prompt = EDIT_FIELD_MAP[data]
    context.user_data["edit_field"] = db_field
    context.user_data["edit_field_key"] = data
    await query.message.reply_text(
        prompt,
        parse_mode="Markdown",
        reply_markup=cancel_kb
    )
    return WAIT_EDIT_VALUE

async def got_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _esc_result = await _check_menu_escape(update, context)
    if _esc_result is not None:
        return _esc_result
    txt = update.message.text.strip()
    field = context.user_data.get("edit_field")
    field_key = context.user_data.get("edit_field_key")
    code = context.user_data.get("edit_code")
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_anime")]])

    if field == "year":
        if not txt.isdigit() or len(txt) != 4:
            await update.message.reply_text(
                "⚠️ Faqat *4 xonali yil* yuboring (masalan: 2023):",
                parse_mode="Markdown", reply_markup=cancel_kb)
            return WAIT_EDIT_VALUE
        value = int(txt)
    elif field == "total_episodes":
        if not txt or txt in ADMIN_BUTTONS:
            await update.message.reply_text(
                "⚠️ Bo'sh yuborildi. Qism sonini yozing (masalan: 24 yoki \"24 qism 2-fasl\"):",
                reply_markup=cancel_kb)
            return WAIT_EDIT_VALUE
        value, _legacy_label = _parse_episode_count(txt)
    elif field == "season_episodes":
        if not txt or txt in ADMIN_BUTTONS:
            await update.message.reply_text(
                "⚠️ Bo'sh yuborildi. Qism sonini yozing (masalan: 24 yoki \"24 qism 2-fasl\"):",
                reply_markup=cancel_kb)
            return WAIT_EDIT_VALUE
        value, value_label = _parse_episode_count(txt)
    else:
        if txt in ADMIN_BUTTONS or len(txt) == 0:
            await update.message.reply_text(
                "⚠️ Tugma bosildi yoki bo'sh yuborildi. Matn yozing:",
                reply_markup=cancel_kb)
            return WAIT_EDIT_VALUE
        value = txt

    if field == "season_episodes":
        season_id = context.user_data.get("edit_season_id")
        update_season_total_episodes(season_id, value, value_label)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ *Muvaffaqiyatli yangilandi!*\n\nKod: *{code}* — Jami qismlar → `{value_label}`",
            parse_mode="Markdown",
            reply_markup=admin_menu_keyboard(update.effective_user.id)
        )
        return ConversationHandler.END

    update_anime_field(code, field, value)
    _, label = EDIT_FIELD_MAP[field_key]
    context.user_data.clear()
    await update.message.reply_text(
        f"✅ *Muvaffaqiyatli yangilandi!*\n\nKod: *{code}* — *{field}* → `{value}`",
        parse_mode="Markdown",
        reply_markup=admin_menu_keyboard(update.effective_user.id)
    )
    return ConversationHandler.END

# -- STATS --
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    animes_this_month, users, total_animes, total_episodes = get_monthly_stats()
    month = datetime.now().strftime("%B %Y")
    await update.message.reply_text(
        f"📊 *Statistika — {month}*\n\n"
        f"👥 Jami foydalanuvchilar: {users}\n"
        f"🎬 Jami animeler: {total_animes}\n"
        f"📺 Jami qismlar: {total_episodes}\n"
        f"➕ Bu oy qo'shilgan: {animes_this_month} ta anime",
        parse_mode="Markdown"
    )

# -- BROADCAST --
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    users = get_all_users()
    await update.message.reply_text(
        f"📣 *Xabar yuborish*\n\n"
        f"👥 Foydalanuvchilar soni: *{len(users)} ta*\n\n"
        f"Yuboriladigan xabarni yozing yoki rasm+izoh yuboring:\n"
        f"_(Matn, rasm, yoki rasm+sarlavha qabul qilinadi)_",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    await update.message.reply_text(
        "👇",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_broadcast")]])
    )
    return WAIT_BROADCAST_MSG

async def cancel_broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.message.reply_text("❌ Bekor qilindi.", reply_markup=admin_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

async def got_broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = get_all_users()
    sent = 0
    failed = 0

    for uid in users:
        try:
            if update.message.photo:
                await context.bot.send_photo(
                    chat_id=uid,
                    photo=update.message.photo[-1].file_id,
                    caption=update.message.caption or "",
                )
            else:
                await context.bot.send_message(
                    chat_id=uid,
                    text=update.message.text,
                )
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # Telegramning tezlik chegarasidan (rate limit) saqlanish uchun

    await update.message.reply_text(
        f"✅ *Xabar yuborildi!*\n\n"
        f"📤 Muvaffaqiyatli: *{sent}* ta\n"
        f"❌ Xato: *{failed}* ta",
        parse_mode="Markdown",
        reply_markup=admin_menu_keyboard(update.effective_user.id)
    )
    return ConversationHandler.END

# -- ANIME LIST --
async def show_anime_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    animes, seasons_by_anime, ep_counts = get_anime_list_summary()
    if not animes:
        await update.message.reply_text("❌ Hali anime yo'q!")
        return

    lines = []
    for code, name, post_count in animes:
        post_info = f" | 📤 {post_count}-marta yuborilgan" if post_count else ""
        seasons = seasons_by_anime.get(code)
        if not seasons:
            lines.append(f"*{code}* — {_esc_md(name)} (fasl yo'q){post_info}")
            continue
        parts = []
        for sid, snum, s_total, s_label in seasons:
            added = ep_counts.get(sid, 0)
            label = _episode_label(s_total, s_label)
            parts.append(f"{_season_label(snum, s_total)}: {added}/{label}")
        lines.append(f"*{code}* — {_esc_md(name)} — " + ", ".join(parts) + post_info)

    # Telegram xabar chegarasi (4096 belgi) dan oshib ketmasligi uchun bo'laklarga bo'lib yuboramiz
    header = "📋 *Animeler ro'yxati:*\n\n"
    chunk = header
    for line in lines:
        if len(chunk) + len(line) + 1 > 3500:
            await update.message.reply_text(chunk, parse_mode="Markdown")
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        await update.message.reply_text(chunk, parse_mode="Markdown")

# -- MANAGE CHANNELS --
def _format_channels_list(channels):
    text = "📡 *Majburiy obuna kanallari:*\n\n"
    buttons = []
    if channels:
        for identifier, link, title, expires_at in channels:
            label = title or identifier
            if expires_at:
                try:
                    exp_text = f"⏳ {datetime.fromisoformat(expires_at).strftime('%d.%m.%Y')} gacha"
                except ValueError:
                    exp_text = "⏳ muddatli"
            else:
                exp_text = "♾ Doimiy"
            text += f"• {label} — {exp_text}\n"
            buttons.append([InlineKeyboardButton(f"🗑 {label} ni o'chirish", callback_data=f"rmchan_{identifier}")])
    else:
        text += "Hali kanal qo'shilmagan.\n"
    buttons.append([InlineKeyboardButton("➕ Kanal Qo'shish", callback_data="add_channel")])
    return text, buttons

async def manage_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    channels = get_required_channels()
    text, buttons = _format_channels_list(channels)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def manage_channels_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "add_channel":
        await query.message.reply_text(
            "📡 Yangi kanal havolasini yuboring.\n\n"
            "🔓 Ochiq kanal uchun: https://t.me/kanal_nomi (yoki @kanal_nomi)\n"
            "🔒 Yopiq kanal uchun: https://t.me/+XXXXXXXX"
        )
        context.user_data["awaiting_channel"] = True
        return

    if data.startswith("rmchan_"):
        identifier = data[7:]
        remove_required_channel(identifier)
        channels = get_required_channels()
        text, buttons = _format_channels_list(channels)
        await query.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def ask_channel_expiry(message, context):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 1 hafta", callback_data="chexp_7"), InlineKeyboardButton("📅 15 kun", callback_data="chexp_15")],
        [InlineKeyboardButton("📅 30 kun", callback_data="chexp_30"), InlineKeyboardButton("♾ Doimiy", callback_data="chexp_never")],
    ])
    await message.reply_text(
        "⏳ Bu kanal majburiy obunada qancha muddat tursin?\n"
        "(muddat tugagach ro'yxatdan avtomatik olib tashlanadi)",
        reply_markup=kb
    )

async def ask_channel_title(message, context, detected_title):
    context.user_data["awaiting_channel_title"] = True
    context.user_data["pending_channel"]["detected_title"] = detected_title
    hint = f" (aniqlangan nom: {detected_title})" if detected_title else ""
    await message.reply_text(
        f"✏️ Bu kanal uchun ko'rinadigan nom kiriting{hint}:"
    )

async def got_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.user_data.get("awaiting_channel"):
        return
    context.user_data["awaiting_channel"] = False
    text = update.message.text.strip()

    parsed = parse_channel_link(text)
    if not parsed:
        await update.message.reply_text(
            "❌ Havola noto'g'ri formatda!\n\n"
            "Quyidagi ko'rinishlardan birida yuboring:\n"
            "• https://t.me/kanal_nomi\n"
            "• @kanal_nomi\n"
            "• https://t.me/+XXXXXXXX (yopiq kanal uchun)"
        )
        context.user_data["awaiting_channel"] = True
        return

    kind, value = parsed
    if kind == "public":
        try:
            chat = await context.bot.get_chat(f"@{value}")
        except Exception as e:
            await update.message.reply_text(
                f"❌ Kanal topilmadi yoki botga ruxsat yo'q.\n"
                f"Botni shu kanalga *administrator* qilib qo'shganingizga ishonch hosil qiling, so'ng qaytadan yuboring.\n\n"
                f"_Xato: {_esc_md(str(e))}_",
                parse_mode="Markdown"
            )
            context.user_data["awaiting_channel"] = True
          
