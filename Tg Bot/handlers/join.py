import html
import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, ChatJoinRequestHandler, ChatMemberHandler, ContextTypes

import database as db
from config import ADMIN_IDS
from utils import send_custom

log = logging.getLogger(__name__)

ADMIN_STATUSES = {"administrator", "creator"}


async def on_join_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request
    user = req.from_user
    await db.upsert_user(user.id, user.first_name, user.username)
    await db.upsert_channel(req.chat.id, req.chat.title, True)
    mode = await db.get_setting("approve_mode", "non")

    # message first: user_chat_id is valid only until the request is processed
    try:
        await send_custom(ctx.bot, req.user_chat_id, await db.get_msg("welcome"), user, req.chat.title)
    except TelegramError as e:
        log.warning("Message to %s failed: %s", user.id, e)

    status = "pending"
    if mode == "auto":
        try:
            await req.approve()
            status = "approved"
        except TelegramError as e:
            log.warning("Approve %s in %s failed: %s", user.id, req.chat.id, e)
    await db.add_request(user.id, req.chat.id, status)


async def on_my_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ev = update.my_chat_member
    if ev.chat.type == "private":
        await db.set_blocked(ev.chat.id, ev.new_chat_member.status == "kicked")
        return
    is_admin = ev.new_chat_member.status in ADMIN_STATUSES
    await db.upsert_channel(ev.chat.id, ev.chat.title, is_admin)
    state = "✅ is now an admin" if is_admin else "⚠️ is no longer an admin"
    for admin_id in ADMIN_IDS:
        try:
            await ctx.bot.send_message(admin_id, f"📡 Bot {state} in <b>{html.escape(ev.chat.title or '')}</b>.")
        except TelegramError:
            pass


def register(app: Application) -> None:
    app.add_handler(ChatJoinRequestHandler(on_join_request))
    app.add_handler(ChatMemberHandler(on_my_status, ChatMemberHandler.MY_CHAT_MEMBER))
