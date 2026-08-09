# -*- coding: utf-8 -*-
"""
helpers.py — Yordamchi funksiyalar: obuna tekshirish, klaviaturalar,
anime/fasl ma'lumotlarini foydalanuvchiga yuborish. main.py dan ajratib
olindi — mantiq BIR QATOR HAM o'zgartirilmadi.
"""
import re
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
)
from telegram.ext import ContextTypes
from config import *
from database import *
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
        [KeyboardButton("🛠 Qism Boshqarish")],
        [KeyboardButton("💾 Backup Olish"), KeyboardButton("♻️ Backup Tiklash")],
        [KeyboardButton("🏷 Anime Holati")],
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

async def _send_poster_or_text(
    bot,
    chat_id,
    caption,
    poster_id,
    poster_type="photo",
    reply_markup=None,
    log_context="",
):
    """Yaroqsiz Telegram file_id botni to'xtatib qo'ymasligi uchun fallback."""
    if not poster_id:
        await bot.send_message(
            chat_id=chat_id,
            text=caption,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )
        return

    media_kwarg = "video" if poster_type == "video" else "photo"
    send_fn = bot.send_video if poster_type == "video" else bot.send_photo
    try:
        await send_fn(
            chat_id=chat_id,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=reply_markup,
            **{media_kwarg: poster_id},
        )
    except Exception as e:
        logger.warning("Media yuborilmadi%s: %s", f" ({log_context})" if log_context else "", e)
        fallback_text = (
            f"{caption}\n\n"
            "⚠️ Poster fayli topilmadi, lekin anime ma'lumotlari saqlandi."
        )
        await bot.send_message(
            chat_id=chat_id,
            text=fallback_text,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )

async def _send_season_info(bot, chat_id, name, genre, desc, season_id, poster_id, total_ep, season_num=None, show_season_label=False, poster_type="photo", ep_label=None, status_key=None):
    episodes = get_episodes_list(season_id) if season_id else []
    title = f"🎬 *{_esc_md(name)}*"
    if show_season_label and season_num:
        title += f" — {_season_label(season_num, total_ep)}"
    ep_count_display = _episode_label(total_ep, ep_label)
    status_line = f"🏷 Holati: {ANIME_STATUSES[status_key]}\n" if status_key in ANIME_STATUSES else ""
    caption = (
        f"{title}\n\n"
        f"{status_line}"
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

    reply_markup = episodes_keyboard(season_id) if episodes else None
    final_caption = caption if episodes else caption + "\n\n⚠️ Hali qism yuklanmagan!"
    await _send_poster_or_text(
        bot,
        chat_id,
        final_caption,
        poster_id,
        poster_type=poster_type,
        reply_markup=reply_markup,
        log_context=f"anime={name}, season={season_num or '-'}",
    )

async def send_anime_info(bot, chat_id, code):
    """Anime kodini kanal deep-link orqali yoki qo'lda yozilganda ko'rsatish uchun umumiy funksiya."""
    anime = get_anime_by_code(code)
    if not anime:
        await bot.send_message(chat_id=chat_id, text="❌ Bunday kodli anime topilmadi!")
        return
    _, code, name, year, genre, total_ep, desc, poster_id, added_date, *_rest = anime
    status_key = get_anime_status(code)
    seasons = get_seasons(code)

    if not seasons:
        # eski/fasl yaratilmagan holat uchun zaxira yo'l
        await _send_season_info(bot, chat_id, name, genre, desc, None, poster_id, total_ep, status_key=status_key)
        return

    if len(seasons) == 1:
        sid, snum, s_poster, s_total, s_ptype, s_label = seasons[0]
        await _send_season_info(
            bot, chat_id, name, genre, desc, sid, s_poster, s_total,
            season_num=snum, show_season_label=False, poster_type=s_ptype, ep_label=s_label,
            status_key=status_key
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
    status_key = get_anime_status(anime_code)
    await _send_season_info(
        context.bot, query.from_user.id, name, genre, desc,
        season_id, poster_id, total_ep, season_num=season_num, show_season_label=True,
        poster_type=poster_type, ep_label=ep_label, status_key=status_key
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


