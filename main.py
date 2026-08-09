# -*- coding: utf-8 -*-
"""
main.py — Botni ishga tushiruvchi asosiy fayl. Barcha ConversationHandler'lar
shu yerda yig'iladi va ro'yxatdan o'tkaziladi. Hosting'da "start command"
o'zgarmaydi: baribir `python main.py` bilan ishga tushiriladi.

main.py (asl, bo'linmagan) fayldan ajratib olindi — mantiq BIR QATOR HAM
o'zgartirilmadi.
"""
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, ConversationHandler,
    ChatJoinRequestHandler, filters
)
from config import *
from database import *
from helpers import *
from handlers import *

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
            WAIT_ANIME_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_anime_name),
                CallbackQueryHandler(got_anime_name_dup_confirm, pattern="^dupanime_yes$"),
            ],
            WAIT_ANIME_GENRE: [CallbackQueryHandler(got_anime_genre_callback, pattern="^(gsel_|gconfirm)")],
            WAIT_ANIME_EPISODES: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_anime_episodes)],
            WAIT_ANIME_POSTER: [MessageHandler(filters.PHOTO | filters.VIDEO, got_anime_poster)],
            WAIT_ANIME_STATUS: [CallbackQueryHandler(got_anime_status_callback, pattern="^newanimestatus_")],
        },
        fallbacks=_esc + [CallbackQueryHandler(cancel_anime_callback, pattern="^cancel_anime$")],
        per_message=False,
    )

    # Add episode conversation
    _done_ep_cb = CallbackQueryHandler(done_episodes_callback, pattern="^done_episodes$")
    _done_ep_yes_cb = CallbackQueryHandler(done_episodes_confirm_yes, pattern="^done_ep_yes$")
    _done_ep_no_cb = CallbackQueryHandler(done_episodes_confirm_no, pattern="^done_ep_no$")
    add_episode_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📺 Qism Qo'shish$"), add_episode_start),
            MessageHandler(filters.Regex("^🏷 Anime Holati$"), anime_status_list_start),
            CallbackQueryHandler(quick_episode_select_callback, pattern="^quickep_"),
        ],
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
            WAIT_NEWSEASON_POSTER: [MessageHandler(filters.PHOTO | filters.VIDEO, got_newseason_poster)],
            WAIT_EPISODE_VIDEO: [
                _done_ep_cb,
                _done_ep_yes_cb,
                _done_ep_no_cb,
                MessageHandler(filters.VIDEO | filters.Document.ALL, got_episode_video),
            ],
        },
        fallbacks=_esc + [_done_ep_cb, CallbackQueryHandler(cancel_anime_callback, pattern="^cancel_anime$")],
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
            WAIT_EDIT_FIELD: [CallbackQueryHandler(got_edit_field_callback, pattern="^(editfield_|editseason_|editstatus|cancel_anime)")],
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
    app.add_handler(CallbackQueryHandler(search_result_callback, pattern="^srch_"))
    app.add_handler(CallbackQueryHandler(inline_menu_callback, pattern="^(anime_search|kabinet|shorts|reklama)$"))
    app.add_handler(CallbackQueryHandler(manage_channels_callback, pattern="^(add_channel|rmchan_.+)$"))
    app.add_handler(CallbackQueryHandler(channel_expiry_callback, pattern="^chexp_"))
    app.add_handler(CallbackQueryHandler(channel_send_page_callback, pattern="^chpage_"))
    app.add_handler(CallbackQueryHandler(recent_ep_page_callback, pattern="^annpage_"))
    app.add_handler(CallbackQueryHandler(channel_send_bycode_callback, pattern="^chsel_bycode$"))
    app.add_handler(CallbackQueryHandler(chsel_callback, pattern="^chsel_"))
    app.add_handler(CallbackQueryHandler(chsend_callback, pattern="^chsend_"))
    app.add_handler(CallbackQueryHandler(admin_manage_callback, pattern="^(add_admin|rmadmin_.+)$"))
    app.add_handler(CallbackQueryHandler(admin_new_episode_channel_send, pattern="^annep_"))
    app.add_handler(CallbackQueryHandler(show_season_callback, pattern="^showseason_"))
    # Restore callbacks must be registered before the catch-all episode handler.
    # Otherwise "restore_confirm"/"restore_cancel" are swallowed silently.
    app.add_handler(CallbackQueryHandler(restore_confirm_callback, pattern="^restore_(confirm|cancel)$"))
    app.add_handler(CallbackQueryHandler(episode_callback))

    app.add_handler(MessageHandler(filters.FORWARDED, got_channel_forward), group=-1)
    app.add_handler(ChatJoinRequestHandler(record_join_request))

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
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, got_episode_channel_poster))
    app.add_handler(MessageHandler(filters.Regex("^💾 Backup Olish$"), backup_db_command))
    app.add_handler(MessageHandler(filters.Regex("^♻️ Backup Tiklash$"), restore_db_start))
    app.add_handler(MessageHandler(filters.Document.ALL, got_restore_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code))

    print("✅ Bot ishga tushdi!")
    app.run_polling()

if __name__ == "__main__":
    main()

