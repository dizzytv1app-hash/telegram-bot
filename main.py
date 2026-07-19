import logging
import os
import asyncio
import sqlite3
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, ConversationHandler,
    filters
)

# ==================== CONFIG ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8947239424:AAFqwmLp5ICjDsQ9VeCvteGXW26C_J2P-XQ")
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
    "🆕 Yangi Qismlar", "🛠 Qism Boshqarish",
    "🔍 Anime Izlash", "⏭ Shorts — Tez Orada!", "📢 Reklama", "📺 Animelar Kanali"
}

# Filter matching every reply-keyboard button — used as a universal conversation escape
_MENU_BTN_FILTER = filters.Regex(
    r"^(➕ Anime Qo'shish|📺 Qism Qo'shish|📋 Animeler Ro'yxati"
    r"|📊 Statistika|🗑 Anime O'chirish|✏️ Anime Tahrirlash|📡 Kanallar"
    r"|📣 Xabar Yuborish|🔙 Asosiy Menu|🔍 Anime Izlash"
    r"|📢 Reklama|📺 Animelar Kanali|📤 Kanalga Yuborish|👥 Adminlar|🆕 Yangi Qismlar|🛠 Qism Boshqarish|⏭ Shorts.*)$"
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
def init_db():
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
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
        c.execute("ALTER TABLE episodes ADD COLUMN season_id INTEGER")
    except sqlite3.OperationalError:
        pass  # ustun allaqachon mavjud

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
    conn.close()

def get_next_code():
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("SELECT MAX(code) FROM animes")
    result = c.fetchone()[0]
    conn.close()
    return (result or 0) + 1

def add_anime(code, name, year, genre, total_episodes, description, poster_file_id):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO animes (code, name, year, genre, total_episodes, description, poster_file_id, added_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (code, name, year, genre, total_episodes, description, poster_file_id, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()

def update_anime_field(code, field, value):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute(f"UPDATE animes SET {field}=? WHERE code=?", (value, code))
    conn.commit()
    conn.close()

def add_episode(season_id, episode_num, video_file_id):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO episodes (season_id, episode_num, video_file_id, added_at)
        VALUES (?, ?, ?, ?)
    """, (season_id, episode_num, video_file_id, datetime.now().timestamp()))
    conn.commit()
    conn.close()

def get_recent_episodes(hours=36):
    """So'nggi `hours` soat ichida qo'shilgan qismlarni anime/fasl nomi bilan qaytaradi."""
    conn = sqlite3.connect("anime.db")
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
    conn.close()
    cutoff = datetime.now().timestamp() - hours * 3600
    return [r for r in rows if r[2] and r[2] >= cutoff]

def get_recent_episode_counts(hours=36):
    """So'nggi `hours` soat ichida har bir anime/fasl uchun qancha qism qo'shilganini qaytaradi.
    Natija: [(anime_code, anime_name, season_num, qo'shilgan_soni, jami_e'lon_qilingan_qismlar), ...]"""
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
        result.append((code, info["name"], season_num, info["count"], total_ep))
    return result

def get_anime_by_code(code):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("SELECT * FROM animes WHERE code=?", (code,))
    result = c.fetchone()
    conn.close()
    return result

def get_all_animes():
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("SELECT code, name, year, genre, total_episodes FROM animes ORDER BY code")
    result = c.fetchall()
    conn.close()
    return result

def increment_channel_post_count(code):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("UPDATE animes SET channel_post_count = COALESCE(channel_post_count, 0) + 1 WHERE code=?", (code,))
    conn.commit()
    conn.close()

def get_channel_post_count(code):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("SELECT channel_post_count FROM animes WHERE code=?", (code,))
    row = c.fetchone()
    conn.close()
    return row[0] if row and row[0] else 0

# -- FASLLAR (SEASONS) --
def add_season(anime_code, season_num, poster_file_id, total_episodes):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute(
        "INSERT INTO seasons (anime_code, season_num, poster_file_id, total_episodes, added_date) VALUES (?, ?, ?, ?, ?)",
        (anime_code, season_num, poster_file_id, total_episodes, datetime.now().strftime("%Y-%m-%d"))
    )
    conn.commit()
    season_id = c.lastrowid
    conn.close()
    return season_id

def get_seasons(anime_code):
    """Berilgan anime uchun barcha fasllarni qaytaradi: [(id, season_num, poster_file_id, total_episodes), ...]"""
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute(
        "SELECT id, season_num, poster_file_id, total_episodes FROM seasons WHERE anime_code=? ORDER BY season_num",
        (anime_code,)
    )
    result = c.fetchall()
    conn.close()
    return result

def get_season(anime_code, season_num):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute(
        "SELECT id, season_num, poster_file_id, total_episodes FROM seasons WHERE anime_code=? AND season_num=?",
        (anime_code, season_num)
    )
    result = c.fetchone()
    conn.close()
    return result

def get_season_by_id(season_id):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute(
        "SELECT id, anime_code, season_num, poster_file_id, total_episodes FROM seasons WHERE id=?",
        (season_id,)
    )
    result = c.fetchone()
    conn.close()
    return result

def get_episode(season_id, episode_num):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("SELECT video_file_id FROM episodes WHERE season_id=? AND episode_num=?",
              (season_id, episode_num))
    result = c.fetchone()
    conn.close()
    return result

def get_episodes_list(season_id):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("SELECT episode_num FROM episodes WHERE season_id=? ORDER BY episode_num",
              (season_id,))
    result = c.fetchall()
    conn.close()
    return [r[0] for r in result]

def delete_anime(code):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("SELECT id FROM seasons WHERE anime_code=?", (code,))
    season_ids = [r[0] for r in c.fetchall()]
    c.execute("DELETE FROM animes WHERE code=?", (code,))
    c.execute("DELETE FROM seasons WHERE anime_code=?", (code,))
    for sid in season_ids:
        c.execute("DELETE FROM episodes WHERE season_id=?", (sid,))
    conn.commit()
    conn.close()

def delete_episode(season_id, episode_num):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("DELETE FROM episodes WHERE season_id=? AND episode_num=?", (season_id, episode_num))
    conn.commit()
    conn.close()

def update_episode_number(season_id, old_num, new_num):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute(
        "UPDATE episodes SET episode_num=? WHERE season_id=? AND episode_num=?",
        (new_num, season_id, old_num)
    )
    conn.commit()
    conn.close()

def get_monthly_stats():
    conn = sqlite3.connect("anime.db")
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
    conn.close()
    return animes_count, users_count, total_animes, total_episodes

def add_required_channel(username, link):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO required_channels (username, link) VALUES (?, ?)", (username, link))
    conn.commit()
    conn.close()

def remove_required_channel(username):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("DELETE FROM required_channels WHERE username=?", (username,))
    conn.commit()
    conn.close()

def get_required_channels():
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("SELECT username, link FROM required_channels")
    result = c.fetchall()
    conn.close()
    return result

def register_user(user_id, username):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO users (id, username, joined_date)
        VALUES (?, ?, ?)
    """, (user_id, username or "", datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("SELECT id FROM users")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def add_admin(user_id, added_by):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO admins (user_id, added_by, added_date) VALUES (?, ?, ?)",
        (user_id, added_by, datetime.now().strftime("%Y-%m-%d"))
    )
    conn.commit()
    conn.close()

def remove_admin(user_id):
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_all_admins():
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("SELECT user_id, added_date FROM admins")
    rows = c.fetchall()
    conn.close()
    return rows

def is_admin(user_id):
    """Asosiy admin yoki qo'shilgan sub-admin bo'lsa True qaytaradi."""
    if user_id == ADMIN_ID:
        return True
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result is not None

# ==================== HELPERS ====================
async def check_subscription(user_id, context):
    channels = get_required_channels()
    if not channels:
        channels = [(CHANNEL_USERNAME, CHANNEL_LINK)]
    not_joined = []
    for username, link in channels:
        try:
            member = await context.bot.get_chat_member(username, user_id)
            if member.status not in ["member", "administrator", "creator"]:
                not_joined.append((username, link))
        except:
            not_joined.append((username, link))
    return not_joined

def _esc_md(text):
    """Foydalanuvchi kiritgan matnda Markdown maxsus belgilari bo'lsa,
    Telegram xabarni yubormay qolib ketishining oldini olish uchun ekranlaydi."""
    if text is None:
        return ""
    text = str(text)
    for ch in ("\\", "_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text

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

async def _send_season_info(bot, chat_id, name, genre, desc, season_id, poster_id, total_ep, season_num=None, show_season_label=False):
    episodes = get_episodes_list(season_id) if season_id else []
    title = f"🎬 *{_esc_md(name)}*"
    if show_season_label and season_num:
        title += f" — {season_num}-fasl"
    caption = (
        f"{title}\n\n"
        f"🎭 Janr: {genre}\n"
        f"📺 Jami qismlar: {total_ep} ta\n"
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

    if episodes:
        await bot.send_photo(
            chat_id=chat_id, photo=poster_id, caption=caption,
            parse_mode="Markdown", reply_markup=episodes_keyboard(season_id)
        )
    else:
        await bot.send_photo(
            chat_id=chat_id, photo=poster_id,
            caption=caption + "\n\n⚠️ Hali qism yuklanmagan!",
            parse_mode="Markdown"
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
        sid, snum, s_poster, s_total = seasons[0]
        await _send_season_info(bot, chat_id, name, genre, desc, sid, s_poster, s_total, season_num=snum, show_season_label=False)
        return

    # bir nechta fasl bor — tanlash ro'yxatini ko'rsatish
    buttons = []
    for sid, snum, s_poster, s_total in seasons:
        added = len(get_episodes_list(sid))
        buttons.append([InlineKeyboardButton(f"{snum}-fasl ({added}/{s_total})", callback_data=f"showseason_{sid}")])
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
    _, anime_code, season_num, poster_id, total_ep = season
    anime = get_anime_by_code(anime_code)
    if not anime:
        return
    name, genre, desc = anime[2], anime[4], anime[6]
    await _send_season_info(
        context.bot, query.from_user.id, name, genre, desc,
        season_id, poster_id, total_ep, season_num=season_num, show_season_label=True
    )

async def post_anime_to_channel(context, code, name, genre, total_episodes, poster_id):
    """Admin '📤 Kanalga Yuborish' tugmasi orqali bossagina kanalga poster + ma'lumot joylash."""
    watch_url = f"https://t.me/{BOT_USERNAME}?start={code}"
    caption = (
        f"{name}\n"
        f"────────────────\n\n"
        f"➤Qismi: {total_episodes}\n"
        f"➤Janri: {genre}\n"
        f"➤Anime kodi: {code}\n"
        f"➤Kanal: {CHANNEL_USERNAME}\n"
        f"────────────────\n\n"
        f"Animeni yuklab olish silkasi:\n{watch_url}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎬 Tomosha qilish", url=watch_url)]])
    try:
        await context.bot.send_photo(
            chat_id=CHANNEL_USERNAME, photo=poster_id,
            caption=caption, reply_markup=kb
        )
        return True
    except Exception as e:
        logger.warning(f"Kanalga post yuborishda xato: {e}")
        return False

# ==================== HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username)

    deep_code = None
    if context.args and context.args[0].isdigit():
        deep_code = int(context.args[0])

    if not is_admin(user.id):
        not_joined = await check_subscription(user.id, context)
        if not_joined:
            if deep_code is not None:
                context.user_data["pending_anime_code"] = deep_code
            buttons = [[InlineKeyboardButton(f"📢 {u} ga Obuna Bo'lish", url=lnk)] for u, lnk in not_joined]
            buttons.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub")])
            await update.message.reply_text(
                "🚫 Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:\n\n" +
                "\n".join(f"• {lnk}" for _, lnk in not_joined),
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

    await send_start(update, context, deep_code)

async def send_start(update, context, deep_code=None):
    if deep_code is not None:
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
    await query.answer()
    not_joined = await check_subscription(query.from_user.id, context)
    if not not_joined:
        await query.message.delete()
        pending_code = context.user_data.pop("pending_anime_code", None)
        if pending_code is not None:
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
    conn = sqlite3.connect("anime.db")
    c = conn.cursor()
    c.execute("SELECT joined_date FROM users WHERE id=?", (user.id,))
    row = c.fetchone()
    conn.close()
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
        conn = sqlite3.connect("anime.db")
        c = conn.cursor()
        c.execute("SELECT joined_date FROM users WHERE id=?", (user.id,))
        row = c.fetchone()
        conn.close()
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
    context.user_data["new_anime_genre_sel"] = set()
    await update.message.reply_text(
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
    if not txt.isdigit() or int(txt) <= 0:
        await update.message.reply_text(
            "⚠️ Faqat musbat *raqam* yuboring (masalan: 24):",
            parse_mode="Markdown", reply_markup=_cancel_kb()
        )
        return WAIT_ANIME_EPISODES
    context.user_data["new_anime_episodes"] = int(txt)
    await update.message.reply_text(
        "5️⃣ Poster rasmini yuboring:\n_(rasm faylini yuboring)_",
        parse_mode="Markdown", reply_markup=_cancel_kb()
    )
    return WAIT_ANIME_POSTER

async def got_anime_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text(
            "⚠️ Faqat *rasm (foto)* yuboring:",
            parse_mode="Markdown", reply_markup=_cancel_kb()
        )
        return WAIT_ANIME_POSTER
    poster_id = update.message.photo[-1].file_id
    d = context.user_data
    try:
        add_anime(
            d["new_anime_code"], d["new_anime_name"], 0,
            d["new_anime_genre"], d["new_anime_episodes"], "", poster_id
        )
        add_season(d["new_anime_code"], 1, poster_id, d["new_anime_episodes"])
        await update.message.reply_text(
            f"✅ Anime muvaffaqiyatli qo'shildi!\n\n"
            f"📌 Kod: {d['new_anime_code']}\n"
            f"🎬 Nom: {d['new_anime_name']}\n"
            f"🎭 Janr: {d['new_anime_genre']}\n"
            f"📺 Qismlar: {d['new_anime_episodes']} ta\n\n"
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
async def admin_recent_episodes_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    grouped = get_recent_episode_counts(hours=36)
    if not grouped:
        await update.message.reply_text("🆕 Hozircha so'nggi 36 soatda yangi qism qo'shilmagan.")
        return
    buttons = [
        [InlineKeyboardButton(f"🎬 {name} — {season_num}-fasl — {count} ta yangi qism", callback_data=f"annep_{code}_{season_num}")]
        for code, name, season_num, count, total_ep in grouped
    ]
    await update.message.reply_text(
        "🆕 *So'nggi 36 soatda yangi qism qo'shilgan animelar:*\n\n"
        "Kanalga xabar berish uchun animeni tanlang:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def admin_new_episode_channel_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Ruxsat yo'q!", show_alert=True)
        return
    _, code_str, season_num_str = query.data.split("_")
    code, season_num = int(code_str), int(season_num_str)
    grouped = {(c, sn): (name, count, total_ep) for c, name, sn, count, total_ep in get_recent_episode_counts(hours=36)}
    key = (code, season_num)
    if key not in grouped:
        await query.answer("❌ Bu anime endi ro'yxatda yo'q (36 soat o'tgan bo'lishi mumkin).", show_alert=True)
        return
    name, count, total_ep = grouped[key]
    await query.answer("⏳ Yuborilmoqda...")
    watch_url = f"https://t.me/{BOT_USERNAME}?start={code}"
    text = (
        f"🆕 *{_esc_md(name)}* — {season_num}-fasl ga {count} ta yangi qism qo'shildi!\n\n"
        f"📺 Jami qismlar: {total_ep} ta\n"
        f"➤Anime kodi: {code}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎬 Tomosha qilish", url=watch_url)]])
    try:
        await context.bot.send_message(chat_id=CHANNEL_USERNAME, text=text, parse_mode="Markdown", reply_markup=kb)
        await query.message.reply_text(f"✅ Kanalga yuborildi: *{_esc_md(name)}* — {count} ta yangi qism.", parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Kanalga yangi qism xabarini yuborishda xato: {e}")
        await query.message.reply_text(f"❌ Kanalga yuborilmadi: {e}")

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
        [InlineKeyboardButton(f"{snum}-fasl ({len(get_episodes_list(sid))} ta qism)", callback_data=f"epmseason_{sid}")]
        for sid, snum, poster_id, total_ep in seasons
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
    _, anime_code, season_num, poster_id, total_ep = season
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
    context.user_data.clear()
    lines = "\n".join(f"*{code}* — {_esc_md(name)}" for code, name, year, genre, total_ep in animes)
    await update.message.reply_text(
        f"📺 *Qism qo'shish*\n\nMavjud animeler:\n{lines}\n\n1️⃣ Anime kodini yuboring:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return WAIT_EPISODE_ANIME

async def _show_season_picker(message, context, code, anime_name):
    seasons = get_seasons(code)
    buttons = []
    for sid, snum, poster_id, total_ep in seasons:
        added = len(get_episodes_list(sid))
        buttons.append([InlineKeyboardButton(f"{snum}-fasl ({added}/{total_ep})", callback_data=f"epseason_{sid}")])
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
    _, anime_code, season_num, poster_id, total_ep = season
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
    if not txt.isdigit() or int(txt) <= 0:
        await update.message.reply_text("⚠️ Faqat musbat raqam yuboring:")
        return WAIT_NEWSEASON_EPISODES
    context.user_data["new_season_total"] = int(txt)
    await update.message.reply_text("🖼 Endi shu fasl uchun poster rasmini yuboring:")
    return WAIT_NEWSEASON_POSTER

async def got_newseason_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("⚠️ Faqat rasm (foto) yuboring:")
        return WAIT_NEWSEASON_POSTER
    poster_id = update.message.photo[-1].file_id
    code = context.user_data.get("ep_anime_code")
    total_ep = context.user_data.get("new_season_total", 0)
    seasons = get_seasons(code)
    next_season_num = (max(s[1] for s in seasons) + 1) if seasons else 1
    season_id = add_season(code, next_season_num, poster_id, total_ep)
    context.user_data["ep_season_id"] = season_id
    context.user_data["ep_season_num"] = next_season_num
    context.user_data["ep_added_count"] = 0
    context.user_data["ep_next_num"] = 1
    await update.message.reply_text(
        f"✅ *{next_season_num}-fasl* yaratildi! ({total_ep} ta qism e'lon qilindi)\n\n"
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
    animes = get_all_animes()
    if not animes:
        await update.message.reply_text("❌ Hali anime yo'q!")
        return ConversationHandler.END
    text = "🗑 O'chirmoqchi bo'lgan anime kodini yuboring:\n\n"
    for a in animes:
        text += f"*{a[0]}* — {_esc_md(a[1])} ({a[2]})\n"
    text += "\n/cancel — bekor qilish"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
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
        f"📌 Kod: *{anime[0]}*\n"
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
    "editfield_episodes": ("total_episodes", "📺 Yangi qismlar sonini yozing:\n_(musbat raqam)_"),
    "editfield_desc":     ("description",    "📝 Yangi tavsifni yozing:"),
}

async def edit_anime_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data.clear()
    animes = get_all_animes()
    if not animes:
        await update.message.reply_text("❌ Hali anime yo'q!")
        return ConversationHandler.END
    lines = "\n".join(f"*{a[0]}* — {_esc_md(a[1])} ({a[2]})" for a in animes)
    await update.message.reply_text(
        f"✏️ *Anime Tahrirlash*\n\nMavjud animeler:\n{lines}\n\nTahrirlamoqchi bo'lgan anime kodini yozing:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
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
        f"📌 Kod: *{anime[0]}*\n"
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
    if data == "cancel_anime":
        context.user_data.clear()
        await query.message.reply_text("❌ Bekor qilindi.", reply_markup=admin_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    db_field, prompt = EDIT_FIELD_MAP[data]
    context.user_data["edit_field"] = db_field
    context.user_data["edit_field_key"] = data
    await query.message.reply_text(
        prompt,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_anime")]])
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
        if not txt.isdigit() or int(txt) <= 0:
            await update.message.reply_text(
                "⚠️ Faqat musbat *raqam* yuboring:",
                parse_mode="Markdown", reply_markup=cancel_kb)
            return WAIT_EDIT_VALUE
        value = int(txt)
    else:
        if txt in ADMIN_BUTTONS or len(txt) == 0:
            await update.message.reply_text(
                "⚠️ Tugma bosildi yoki bo'sh yuborildi. Matn yozing:",
                reply_markup=cancel_kb)
            return WAIT_EDIT_VALUE
        value = txt

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
    animes = get_all_animes()
    if not animes:
        await update.message.reply_text("❌ Hali anime yo'q!")
        return
    text = "📋 *Animeler ro'yxati:*\n\n"
    for a in animes:
        episodes = get_episodes_list(a[0])
        text += f"*{a[0]}* — {_esc_md(a[1])} ({a[2]}) | {len(episodes)}/{a[4]} qism\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# -- MANAGE CHANNELS --
async def manage_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    channels = get_required_channels()
    text = "📡 *Majburiy obuna kanallari:*\n\n"
    buttons = []
    if channels:
        for username, link in channels:
            text += f"• {username} — {link}\n"
            buttons.append([InlineKeyboardButton(f"🗑 {username} ni o'chirish", callback_data=f"rmchan_{username}")])
    else:
        text += "Hali kanal qo'shilmagan.\n"
    buttons.append([InlineKeyboardButton("➕ Kanal Qo'shish", callback_data="add_channel")])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def manage_channels_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "add_channel":
        await query.message.reply_text(
            "📡 Yangi kanal username'ini yuboring.\n\nMasalan: @mening_kanalim"
        )
        context.user_data["awaiting_channel"] = True
        return

    if data.startswith("rmchan_"):
        username = data[7:]
        remove_required_channel(username)
        channels = get_required_channels()
        text = "📡 *Majburiy obuna kanallari:*\n\n"
        buttons = []
        if channels:
            for u, lnk in channels:
                text += f"• {u} — {lnk}\n"
                buttons.append([InlineKeyboardButton(f"🗑 {u} ni o'chirish", callback_data=f"rmchan_{u}")])
        else:
            text += "Hali kanal qo'shilmagan.\n"
        buttons.append([InlineKeyboardButton("➕ Kanal Qo'shish", callback_data="add_channel")])
        await query.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def got_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.user_data.get("awaiting_channel"):
        return
    context.user_data["awaiting_channel"] = False
    text = update.message.text.strip()
    if not text.startswith("@"):
        await update.message.reply_text("❌ Username @ bilan boshlanishi kerak! Masalan: @kanal")
        return
    username = text
    link = f"https://t.me/{username.lstrip('@')}"
    add_required_channel(username, link)
    channels = get_required_channels()
    info = "📡 *Majburiy obuna kanallari:*\n\n"
    btns = []
    for u, lnk in channels:
        info += f"• {u} — {lnk}\n"
        btns.append([InlineKeyboardButton(f"🗑 {u} ni o'chirish", callback_data=f"rmchan_{u}")])
    btns.append([InlineKeyboardButton("➕ Kanal Qo'shish", callback_data="add_channel")])
    await update.message.reply_text(
        f"✅ *{username}* qo'shildi!\n\n" + info,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns)
    )

# -- KANALGA QO'LDA YUBORISH --
async def channel_send_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    animes = get_all_animes()
    if not animes:
        await update.message.reply_text("❌ Hali anime qo'shilmagan!")
        return
    buttons = [
        [InlineKeyboardButton(f"{a[0]} — {a[1]}", callback_data=f"chsel_{a[0]}")]
        for a in animes
    ]
    await update.message.reply_text(
        "📤 *Kanalga yuborish*\n\nQaysi animeni kanalga yubormoqchisiz?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def _do_channel_send(query, context, code, name, genre, season):
    sid, season_num, poster_id, total_ep = season
    await query.answer("⏳ Yuborilmoqda...")
    ok = await post_anime_to_channel(context, code, name, genre, total_ep, poster_id)
    if ok:
        increment_channel_post_count(code)
        count = get_channel_post_count(code)
        await query.message.reply_text(
            f"✅ *{_esc_md(name)}* — {season_num}-fasl kanalga yuborildi!\n"
            f"📊 Bu anime posteri kanalga jami *{count}-marta* joylandi.",
            parse_mode="Markdown"
        )
    else:
        await query.message.reply_text(
            f"❌ *{_esc_md(name)}* kanalga yuborilmadi. Bot kanalda admin ekanligini tekshiring.",
            parse_mode="Markdown"
        )

async def chsel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    code = int(query.data[6:])
    anime = get_anime_by_code(code)
    if not anime:
        await query.answer("❌ Bunday anime topilmadi!", show_alert=True)
        return
    name, genre = anime[2], anime[4]
    seasons = get_seasons(code)
    if not seasons:
        await query.answer()
        await query.edit_message_text("❌ Bu animeda hali fasl yo'q.")
        return
    if len(seasons) == 1:
        await _do_channel_send(query, context, code, name, genre, seasons[0])
        return
    await query.answer()
    buttons = [
        [InlineKeyboardButton(f"{snum}-fasl", callback_data=f"chsend_{sid}")]
        for sid, snum, poster_id, total_ep in seasons
    ]
    await query.edit_message_text(
        f"📤 *{_esc_md(name)}*\n\nQaysi faslni kanalga yubormoqchisiz?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def chsend_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    season_id = int(query.data[7:])
    season = get_season_by_id(season_id)
    if not season:
        await query.answer("❌ Bu fasl topilmadi!", show_alert=True)
        return
    _, code, season_num, poster_id, total_ep = season
    anime = get_anime_by_code(code)
    if not anime:
        await query.answer("❌ Bunday anime topilmadi!", show_alert=True)
        return
    name, genre = anime[2], anime[4]
    await _do_channel_send(query, context, code, name, genre, (season_id, season_num, poster_id, total_ep))

# -- ADMINLIK BOSHQARUVI (faqat asosiy admin) --
def _admins_kb():
    admins = get_all_admins()
    buttons = [
        [InlineKeyboardButton(f"🗑 {uid}", callback_data=f"rmadmin_{uid}")]
        for uid, _ in admins
    ]
    buttons.append([InlineKeyboardButton("➕ Admin Qo'shish", callback_data="add_admin")])
    return buttons, admins

async def admins_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    buttons, admins = _admins_kb()
    text = "👥 *Adminlar ro'yxati:*\n\n"
    if admins:
        for uid, date in admins:
            text += f"• `{uid}` — {date}\n"
    else:
        text += "Hali qo'shimcha admin yo'q.\n"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def admin_manage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID:
        await query.answer("❌ Ruxsat yo'q!", show_alert=True)
        return
    await query.answer()
    data = query.data

    if data == "add_admin":
        context.user_data["awaiting_admin_id"] = True
        await query.message.reply_text(
            "🆔 Yangi adminning Telegram ID raqamini yuboring:\n"
            "_(ID ni bilish uchun @userinfobot dan foydalanishi mumkin)_",
            parse_mode="Markdown"
        )
        return

    if data.startswith("rmadmin_"):
        uid = int(data[8:])
        remove_admin(uid)
        buttons, admins = _admins_kb()
        text = "👥 *Adminlar ro'yxati:*\n\n"
        if admins:
            for a_uid, date in admins:
                text += f"• `{a_uid}` — {date}\n"
        else:
            text += "Hali qo'shimcha admin yo'q.\n"
        await query.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def got_add_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.user_data.get("awaiting_admin_id"):
        return
    context.user_data["awaiting_admin_id"] = False
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Faqat raqamli Telegram ID yuboring!")
        return
    new_admin_id = int(text)
    add_admin(new_admin_id, update.effective_user.id)
    try:
        await context.bot.send_message(
            chat_id=new_admin_id,
            text="👑 Sizga admin huquqi berildi! /admin buyrug'ini yuboring."
        )
    except Exception:
        pass
    buttons, admins = _admins_kb()
    text_out = f"✅ *{new_admin_id}* admin qilib qo'shildi!\n\n👥 *Adminlar ro'yxati:*\n\n"
    for a_uid, date in admins:
        text_out += f"• `{a_uid}` — {date}\n"
    await update.message.reply_text(text_out, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("awaiting_channel", None)
    await update.message.reply_text(
        "❌ Bekor qilindi.",
        reply_markup=admin_menu_keyboard(update.effective_user.id) if is_admin(update.effective_user.id) else ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# ==================== UNIVERSAL CONVERSATION ESCAPE FALLBACKS ====================
async def _start_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await start(update, context)
    return ConversationHandler.END

async def _admin_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await admin_command(update, context)
    return ConversationHandler.END

async def _interrupt_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    txt = update.message.text if update.message else ""
    direct_map = {
        "📋 Animeler Ro'yxati": show_anime_list,
        "📊 Statistika":        show_stats,
        "📡 Kanallar":          manage_channels,
        "🔙 Asosiy Menu":       start,
        "🔍 Anime Izlash":      anime_search,
        "📢 Reklama":           reklama_info,
        "📺 Animelar Kanali":   channel_info,
        "📤 Kanalga Yuborish":  channel_send_list,
        "👥 Adminlar":          admins_panel,
        "🆕 Yangi Qismlar":     admin_recent_episodes_panel,
    }
    direct = direct_map.get(txt)
    if direct:
        await direct(update, context)
    else:
        user_is_admin = is_admin(update.effective_user.id)
        await update.message.reply_text(
            "⚠️ Oldingi jarayon bekor qilindi.\nIltimos, tugmani qayta bosing.",
            reply_markup=admin_menu_keyboard(update.effective_user.id) if user_is_admin else ReplyKeyboardRemove()
        )
    return ConversationHandler.END

# ==================== MAIN ====================
def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Shared escape fallbacks — added to every conversation
    _esc = [
        CommandHandler("start", _start_fallback),
        CommandHandler("admin", _admin_fallback),
        CommandHandler("cancel", cancel),
        MessageHandler(_MENU_BTN_FILTER, _interrupt_fallback),
    ]

    # Add anime conversation
    add_anime_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Anime Qo'shish$"), add_anime_start)],
        states={
            WAIT_ANIME_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_anime_code)],
            WAIT_ANIME_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_anime_name)],
            WAIT_ANIME_GENRE: [CallbackQueryHandler(got_anime_genre_callback, pattern="^(gsel_|gconfirm)")],
            WAIT_ANIME_EPISODES: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_anime_episodes)],
            WAIT_ANIME_POSTER: [MessageHandler(filters.PHOTO, got_anime_poster)],
        },
        fallbacks=_esc + [CallbackQueryHandler(cancel_anime_callback, pattern="^cancel_anime$")],
        per_message=False,
    )

    # Add episode conversation
    _done_ep_cb = CallbackQueryHandler(done_episodes_callback, pattern="^done_episodes$")
    _done_ep_yes_cb = CallbackQueryHandler(done_episodes_confirm_yes, pattern="^done_ep_yes$")
    _done_ep_no_cb = CallbackQueryHandler(done_episodes_confirm_no, pattern="^done_ep_no$")
    add_episode_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📺 Qism Qo'shish$"), add_episode_start)],
        states={
            WAIT_EPISODE_ANIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_episode_anime)],
            WAIT_EPISODE_NUM: [
                _done_ep_cb,
                _done_ep_yes_cb,
                _done_ep_no_cb,
                CallbackQueryHandler(epseason_select_callback, pattern="^epseason_\\d"),
                CallbackQueryHandler(epseason_new_callback, pattern="^epseason_new$"),
                CallbackQueryHandler(got_episode_upload_start, pattern="^ep_upload_start$"),
            ],
            WAIT_NEWSEASON_EPISODES: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_newseason_episodes)],
            WAIT_NEWSEASON_POSTER: [MessageHandler(filters.PHOTO, got_newseason_poster)],
            WAIT_EPISODE_VIDEO: [
                _done_ep_cb,
                _done_ep_yes_cb,
                _done_ep_no_cb,
                MessageHandler(filters.VIDEO | filters.Document.ALL, got_episode_video),
            ],
        },
        fallbacks=_esc + [_done_ep_cb],
        per_message=False,
    )

    # Episode management conversation (delete / renumber)
    epm_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🛠 Qism Boshqarish$"), epm_start)],
        states={
            WAIT_EPM_ANIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_epm_anime),
                CallbackQueryHandler(epm_season_select_callback, pattern="^epmseason_"),
            ],
            WAIT_EPM_ACTION: [
                CallbackQueryHandler(epm_select_callback, pattern="^epm_sel_"),
                CallbackQueryHandler(epm_delete_ask_callback, pattern="^epm_del_"),
                CallbackQueryHandler(epm_delete_confirm_callback, pattern="^epm_delyes_"),
                CallbackQueryHandler(epm_rename_ask_callback, pattern="^epm_ren_"),
                CallbackQueryHandler(epm_cancel_callback, pattern="^epm_cancel$"),
            ],
            WAIT_EPM_NEWNUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_epm_newnum)],
        },
        fallbacks=_esc,
        per_message=False,
    )

    # Delete anime conversation
    delete_anime_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🗑 Anime O'chirish$"), delete_anime_start)],
        states={
            WAIT_DELETE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_delete_code)],
            WAIT_DELETE_CONFIRM: [CallbackQueryHandler(got_delete_confirm, pattern="^delconfirm_(yes|no)$")],
        },
        fallbacks=_esc,
        per_message=False,
    )

    # Edit anime conversation
    edit_anime_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✏️ Anime Tahrirlash$"), edit_anime_start)],
        states={
            WAIT_EDIT_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_edit_code)],
            WAIT_EDIT_FIELD: [CallbackQueryHandler(got_edit_field_callback, pattern="^(editfield_|cancel_anime)")],
            WAIT_EDIT_VALUE: [
                CallbackQueryHandler(cancel_anime_callback, pattern="^cancel_anime$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_edit_value),
            ],
        },
        fallbacks=_esc + [CallbackQueryHandler(cancel_anime_callback, pattern="^cancel_anime$")],
        per_message=False,
    )

    # Broadcast conversation
    broadcast_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📣 Xabar Yuborish$"), broadcast_start)],
        states={
            WAIT_BROADCAST_MSG: [
                CallbackQueryHandler(cancel_broadcast_callback, pattern="^cancel_broadcast$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_broadcast_msg),
                MessageHandler(filters.PHOTO, got_broadcast_msg),
            ],
        },
        fallbacks=_esc + [CallbackQueryHandler(cancel_broadcast_callback, pattern="^cancel_broadcast$")],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(add_anime_conv)
    app.add_handler(add_episode_conv)
    app.add_handler(epm_conv)
    app.add_handler(delete_anime_conv)
    app.add_handler(edit_anime_conv)
    app.add_handler(broadcast_conv)

    app.add_handler(CallbackQueryHandler(check_sub_callback, pattern="^check_sub$"))
    app.add_handler(CallbackQueryHandler(inline_menu_callback, pattern="^(anime_search|kabinet|shorts|reklama)$"))
    app.add_handler(CallbackQueryHandler(manage_channels_callback, pattern="^(add_channel|rmchan_.+)$"))
    app.add_handler(CallbackQueryHandler(chsel_callback, pattern="^chsel_"))
    app.add_handler(CallbackQueryHandler(chsend_callback, pattern="^chsend_"))
    app.add_handler(CallbackQueryHandler(admin_manage_callback, pattern="^(add_admin|rmadmin_.+)$"))
    app.add_handler(CallbackQueryHandler(admin_new_episode_channel_send, pattern="^annep_"))
    app.add_handler(CallbackQueryHandler(show_season_callback, pattern="^showseason_"))
    app.add_handler(CallbackQueryHandler(episode_callback))

    app.add_handler(MessageHandler(filters.Regex("^🔍 Anime Izlash$"), anime_search))
    app.add_handler(MessageHandler(filters.Regex("^⏭ Shorts"), shorts_info))
    app.add_handler(MessageHandler(filters.Regex("^📢 Reklama$"), reklama_info))
    app.add_handler(MessageHandler(filters.Regex("^📺 Animelar Kanali$"), channel_info))
    app.add_handler(MessageHandler(filters.Regex("^📊 Statistika$"), show_stats))
    app.add_handler(MessageHandler(filters.Regex("^📋 Animeler Ro'yxati$"), show_anime_list))
    app.add_handler(MessageHandler(filters.Regex("^📡 Kanallar$"), manage_channels))
    app.add_handler(MessageHandler(filters.Regex("^📤 Kanalga Yuborish$"), channel_send_list))
    app.add_handler(MessageHandler(filters.Regex("^👥 Adminlar$"), admins_panel))
    app.add_handler(MessageHandler(filters.Regex("^🆕 Yangi Qismlar$"), admin_recent_episodes_panel))
    app.add_handler(MessageHandler(filters.Regex("^🔙 Asosiy Menu$"), start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code))

    print("✅ Bot ishga tushdi!")
    app.run_polling()

if __name__ == "__main__":
    main()
