import html
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import database as db
import keyboards as kb
from utils import ADMIN, admin_only, count_entities, run_broadcast, safe_edit, send_panel, set_state, to_ptb

MODE_NAMES = {"non": "Non Approve", "auto": "Auto Approve"}


# ---------- screens ----------
async def home_text() -> str:
    mode = await db.get_setting("approve_mode", "non")
    channels = len(await db.list_channels())
    return (
        "🛠 <b>Admin Panel</b>\n\n"
        f"⚙️ Mode: <b>{MODE_NAMES[mode]}</b>\n"
        f"📡 Channels: <b>{channels}</b>\n\n"
        "Select a section below 👇"
    )


async def mode_text(mode: str) -> str:
    return (
        "✅ <b>Non / Approve</b>\n\n"
        f"Current: <b>{MODE_NAMES[mode]}</b>\n\n"
        "• <b>Non Approve</b> – the request is not approved, the user only receives the message.\n"
        "• <b>Auto Approve</b> – the user receives the message and the join request is approved automatically."
    )


def _period_block(title: str, s: dict, prefix: str) -> str:
    return (
        f"{title}\n"
        f"├ 24h: <b>{s[f'{prefix}_24h']}</b>\n"
        f"├ 7d: <b>{s[f'{prefix}_7d']}</b>\n"
        f"├ 30d: <b>{s[f'{prefix}_30d']}</b>\n"
        f"└ All Time: <b>{s[f'{prefix}_all']}</b>\n"
    )


async def stats_text() -> str:
    s = await db.get_stats()
    mode = await db.get_setting("approve_mode", "non")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return (
        "📊 <b>Statistics</b> (realtime)\n\n"
        + _period_block("👥 <b>New Users</b>", s, "new") + "\n"
        + _period_block("🔥 <b>Active Users</b>", s, "act") + "\n"
        + _period_block("📥 <b>Join Requests</b>", s, "req") + "\n"
        f"✅ Approved: <b>{s['approved']}</b>  ⏳ Pending: <b>{s['pending']}</b>\n"
        f"🤖 Bot started: <b>{s['started']}</b>  🚫 Blocked: <b>{s['blocked']}</b>\n"
        f"📡 Channels: <b>{s['channels']}</b>  ⚙️ Mode: <b>{MODE_NAMES[mode]}</b>\n\n"
        f"🕒 Updated: <code>{now}</code>"
    )


# ---------- commands ----------
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await send_panel(ctx.bot, update.effective_chat.id, await home_text(), kb.main_menu())


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await send_panel(ctx.bot, update.effective_chat.id, "❌ Cancelled.\n\n" + await home_text(), kb.main_menu())


@admin_only
async def cb_home(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await safe_edit(update.callback_query.message, await home_text(), kb.main_menu())
    await update.callback_query.answer()


# ---------- approve mode ----------
@admin_only
async def cb_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mode = await db.get_setting("approve_mode", "non")
    await safe_edit(update.callback_query.message, await mode_text(mode), kb.mode_menu(mode))
    await update.callback_query.answer()


@admin_only
async def cb_set_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    mode = cb.data.split(":")[1]
    await db.set_setting("approve_mode", mode)
    await safe_edit(cb.message, await mode_text(mode), kb.mode_menu(mode))
    await cb.answer(f"✅ {MODE_NAMES[mode]} ON")


# ---------- broadcast ----------
@admin_only
async def cb_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    counts = {p: await db.count_users(p) for p in db.PERIODS}
    await safe_edit(
        update.callback_query.message,
        "📢 <b>Broadcast</b>\n\nSelect which users should receive the message 👇",
        kb.broadcast_menu(counts),
    )
    await update.callback_query.answer()


@admin_only
async def cb_bc_period(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    period = cb.data.split(":")[1]
    set_state(ctx, "bc_wait", period=period)
    await safe_edit(
        cb.message,
        f"📢 <b>Broadcast → {db.PERIOD_LABELS[period]} Users</b>\n\n"
        "Send the message you want to broadcast (text / photo / video / file / sticker). "
        "Formatting and ✨ Premium (custom) emoji are kept as they are.",
        kb.cancel_menu(),
    )
    await cb.answer()


async def on_bc_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    period = ctx.user_data["period"]
    total = await db.count_users(period)
    set_state(ctx, "bc_confirm", from_chat=message.chat_id, msg_id=message.message_id)
    await message.reply_text(
        f"👆 This message will be sent to <b>{total}</b> users ({db.PERIOD_LABELS[period]}).\n"
        f"✨ Premium emoji: <b>{count_entities(message)}</b>\n\nConfirm?",
        reply_markup=to_ptb(kb.confirm_broadcast()),
        do_quote=True,
    )


@admin_only
async def cb_bc_go(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    data = dict(ctx.user_data)
    if data.get("state") != "bc_confirm":
        await cb.answer()
        return
    ctx.user_data.clear()
    await safe_edit(cb.message, "🚀 Broadcast started...")
    ctx.application.create_task(
        run_broadcast(ctx.bot, cb.message.chat.id, data["from_chat"], data["msg_id"], data["period"])
    )
    await cb.answer()


# ---------- statistics ----------
@admin_only
async def cb_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await safe_edit(update.callback_query.message, await stats_text(), kb.stats_menu())
    await update.callback_query.answer("🔄 Updated")


# ---------- channels ----------
@admin_only
async def cb_channels(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = await db.list_channels()
    body = "\n".join(f"• <b>{html.escape(r['title'] or 'Untitled')}</b> (<code>{r['chat_id']}</code>)" for r in rows)
    text = "📡 <b>Channels (bot admin)</b>\n\n" + (body or "No channels yet. Make the bot an admin in your channel.")
    await safe_edit(update.callback_query.message, text, kb.back_menu())
    await update.callback_query.answer()


STATES = {"bc_wait": on_bc_message}


def register_commands(app: Application) -> None:
    app.add_handler(CommandHandler("admin", cmd_admin, filters=ADMIN))
    app.add_handler(CommandHandler("cancel", cmd_cancel, filters=ADMIN))


def register_callbacks(app: Application) -> None:
    periods = "|".join(db.PERIODS)
    app.add_handlers([
        CallbackQueryHandler(cb_home, pattern=r"^adm:(home|cancel)$"),
        CallbackQueryHandler(cb_mode, pattern=r"^adm:mode$"),
        CallbackQueryHandler(cb_set_mode, pattern=r"^mode:(non|auto)$"),
        CallbackQueryHandler(cb_broadcast, pattern=r"^adm:bc$"),
        CallbackQueryHandler(cb_bc_period, pattern=rf"^bc:({periods})$"),
        CallbackQueryHandler(cb_bc_go, pattern=r"^bc:go$"),
        CallbackQueryHandler(cb_stats, pattern=r"^adm:stats$"),
        CallbackQueryHandler(cb_channels, pattern=r"^adm:channels$"),
    ])
