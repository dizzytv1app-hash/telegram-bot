# -*- coding: utf-8 -*-
"""
config.py — Botning barcha sozlamalari, holatlar (states), tugma matnlari va logging.
Bu fayl main.py dan ajratib olindi — mantiq BIR QATOR HAM o'zgartirilmadi,
faqat joylashuvi o'zgardi. Barcha qiymatlar va funksiyalar original bilan bir xil.

MUHIM: _check_menu_escape() funksiyasi bu yerdan handlers.py ga ko'chirildi,
chunki u _interrupt_fallback() ga bog'liq (aylanma import bo'lmasligi uchun).
"""
import logging
import os
import shutil
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

ANIME_STATUSES = {
    "ongoing": "🟢 Davom etmoqda",
    "finished": "🔵 Tugagan",
    "soon": "🟡 Tez orada",
    "paused": "⏸ Tanaffusda",
}

# ==================== STATES ====================
(
    WAIT_ANIME_CODE, WAIT_ANIME_NAME, WAIT_ANIME_YEAR, WAIT_ANIME_GENRE,
    WAIT_ANIME_EPISODES, WAIT_ANIME_DESC, WAIT_ANIME_POSTER,
    WAIT_EPISODE_ANIME, WAIT_EPISODE_NUM, WAIT_EPISODE_VIDEO,
    WAIT_DELETE_CODE, WAIT_DELETE_CONFIRM, WAIT_ADD_CHANNEL,
    WAIT_EDIT_CODE, WAIT_EDIT_FIELD, WAIT_EDIT_VALUE,
    WAIT_BROADCAST_MSG, WAIT_EPM_ANIME, WAIT_EPM_ACTION, WAIT_EPM_NEWNUM,
    WAIT_NEWSEASON_EPISODES, WAIT_NEWSEASON_POSTER, WAIT_ANIME_STATUS
) = range(23)

# ==================== BUTTON TEXTS (rejected during conversations) ====================
ADMIN_BUTTONS = {
    "➕ Anime Qo'shish", "📺 Qism Qo'shish", "📋 Animeler Ro'yxati",
    "📊 Statistika", "🗑 Anime O'chirish", "✏️ Anime Tahrirlash", "📡 Kanallar",
    "📣 Xabar Yuborish", "🔙 Asosiy Menu", "📤 Kanalga Yuborish", "👥 Adminlar",
    "🆕 Yangi Qismlar", "🛠 Qism Boshqarish", "💾 Backup Olish", "♻️ Backup Tiklash", "🏷 Anime Holati",
    "🔍 Anime Izlash", "⏭ Shorts — Tez Orada!", "📢 Reklama", "📺 Animelar Kanali"
}

# Filter matching every reply-keyboard button — used as a universal conversation escape
_MENU_BTN_FILTER = filters.Regex(
    r"^(➕ Anime Qo'shish|📺 Qism Qo'shish|📋 Animeler Ro'yxati"
    r"|📊 Statistika|🗑 Anime O'chirish|✏️ Anime Tahrirlash|📡 Kanallar"
    r"|📣 Xabar Yuborish|🔙 Asosiy Menu|🔍 Anime Izlash"
    r"|📢 Reklama|📺 Animelar Kanali|📤 Kanalga Yuborish|👥 Adminlar|🆕 Yangi Qismlar|🛠 Qism Boshqarish|💾 Backup Olish|♻️ Backup Tiklash|🏷 Anime Holati|⏭ Shorts.*)$"
)

# ==================== LOGGING ====================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# Telegram HTTP request URLs contain the bot token; keep those URLs out of logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


