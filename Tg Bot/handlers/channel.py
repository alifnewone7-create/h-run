import html
import logging

from telegram import ReplyKeyboardRemove, Update
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

import database as db
import keyboards as kb
from utils import ADMIN, admin_only, safe_edit, send_panel, to_ptb

log = logging.getLogger(__name__)

PERMISSION = "Invite Users via Link (needed to approve join requests)"


async def channel_text() -> str:
    ch = await db.get_channel()
    if not ch:
        return (
            "📡 <b>Channel</b>\n\nNo channel added yet.\n\n"
            "Tap <b>➕ Add Channel</b>, then pick your channel. The bot is added as admin with only one "
            f"permission: <b>{PERMISSION}</b>.\n\nOnly 1 channel can be added."
        )
    return (
        "📡 <b>Channel</b>\n\n"
        f"• <b>{html.escape(ch['title'] or 'Untitled')}</b> (<code>{ch['chat_id']}</code>)\n"
        f"🔐 Permission: <b>{PERMISSION}</b>\n\n"
        "Only 1 channel can be added. Delete this channel to add another one."
    )


async def show_channel(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, target=None) -> None:
    markup = kb.channel_menu(bool(await db.get_channel()))
    if target:
        await safe_edit(target, await channel_text(), markup)
    else:
        await send_panel(ctx.bot, chat_id, await channel_text(), markup)


@admin_only
async def cb_channel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await show_channel(ctx, update.effective_chat.id, update.callback_query.message)
    await update.callback_query.answer()


@admin_only
async def cb_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    if await db.get_channel():
        await cb.answer("Only 1 channel can be added. Delete the current channel first.", show_alert=True)
        return
    await cb.answer()
    await ctx.bot.send_message(
        cb.message.chat.id,
        "📡 <b>Add Channel</b>\n\nTap <b>📡 Select Channel</b> below and choose your channel.\n"
        f"The bot will be made admin with only: <b>{PERMISSION}</b>.",
        reply_markup=to_ptb(kb.channel_picker()),
    )


@admin_only
async def cb_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    ch = await db.get_channel()
    if not ch:
        await show_channel(ctx, cb.message.chat.id, cb.message)
        await cb.answer()
        return
    await safe_edit(
        cb.message,
        f"🗑 <b>Delete Channel</b>\n\nRemove <b>{html.escape(ch['title'] or 'Untitled')}</b>?\n"
        "The bot will leave the channel and stop working there.",
        kb.channel_delete_confirm(),
    )
    await cb.answer()


@admin_only
async def cb_delete_ok(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    ch = await db.get_channel()
    if ch:
        await db.delete_channel()
        try:
            await ctx.bot.leave_chat(ch["chat_id"])
        except TelegramError as e:
            log.warning("Leave %s failed: %s", ch["chat_id"], e)
    await show_channel(ctx, cb.message.chat.id, cb.message)
    await cb.answer("🗑 Channel deleted")


async def on_chat_shared(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    shared = message.chat_shared
    if shared.request_id != kb.CHANNEL_REQUEST_ID:
        return
    if await db.get_channel():
        await message.reply_text("⚠️ Only 1 channel can be added. Delete the current channel first.",
                                 reply_markup=ReplyKeyboardRemove())
        return
    try:
        member = await ctx.bot.get_chat_member(shared.chat_id, ctx.bot.id)
        is_admin = member.status == "administrator" and member.can_invite_users
    except TelegramError:
        is_admin = False
    if not is_admin:
        await message.reply_text(
            f"⚠️ The bot is not an admin with <b>{PERMISSION}</b> in that channel. Please try again.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await show_channel(ctx, message.chat_id)
        return
    await db.set_channel(shared.chat_id, shared.title)
    await message.reply_text(f"✅ Channel added: <b>{html.escape(shared.title or 'Untitled')}</b>",
                             reply_markup=ReplyKeyboardRemove())
    await show_channel(ctx, message.chat_id)


async def on_picker_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("❌ Cancelled.", reply_markup=ReplyKeyboardRemove())
    await show_channel(ctx, update.effective_chat.id)


def register_callbacks(app: Application) -> None:
    app.add_handlers([
        CallbackQueryHandler(cb_channel, pattern=r"^adm:channels$"),
        CallbackQueryHandler(cb_add, pattern=r"^ch:add$"),
        CallbackQueryHandler(cb_delete, pattern=r"^ch:del$"),
        CallbackQueryHandler(cb_delete_ok, pattern=r"^ch:delok$"),
    ])


def register_messages(app: Application) -> None:
    app.add_handlers([
        MessageHandler(ADMIN & filters.StatusUpdate.CHAT_SHARED, on_chat_shared),
        MessageHandler(ADMIN & filters.Text([kb.PICKER_CANCEL]), on_picker_cancel),
    ])
