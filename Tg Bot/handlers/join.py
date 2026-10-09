import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, ChatJoinRequestHandler, ChatMemberHandler, ContextTypes

import database as db
from utils import send_custom

log = logging.getLogger(__name__)

ADMIN_STATUSES = {"administrator", "creator"}
MEMBER_STATUSES = {"member", "administrator", "creator"}


async def is_linked(chat_id: int) -> bool:
    ch = await db.get_channel()
    return bool(ch) and ch["chat_id"] == chat_id


async def on_join_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request
    if not await is_linked(req.chat.id):
        return
    user = req.from_user
    await db.upsert_user(user.id, user.first_name, user.username)
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


def is_member(m) -> bool:
    return m.status in MEMBER_STATUSES or (m.status == "restricted" and m.is_member)


async def on_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ev = update.chat_member
    user = ev.new_chat_member.user
    was, now = is_member(ev.old_chat_member), is_member(ev.new_chat_member)
    if user.is_bot or was == now or not await is_linked(ev.chat.id):
        return
    await db.upsert_user(user.id, user.first_name, user.username)
    await db.set_member(user.id, ev.chat.id, "joined" if now else "left")
    if now:
        await db.approve_request(user.id, ev.chat.id)


async def on_my_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ev = update.my_chat_member
    if ev.chat.type == "private":
        await db.set_blocked(ev.chat.id, ev.new_chat_member.status == "kicked")
        return
    # bot removed / demoted in the linked channel -> unlink it (no notification)
    if ev.new_chat_member.status not in ADMIN_STATUSES and await is_linked(ev.chat.id):
        await db.delete_channel()


def register(app: Application) -> None:
    app.add_handler(ChatJoinRequestHandler(on_join_request))
    app.add_handler(ChatMemberHandler(on_my_status, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(on_member, ChatMemberHandler.CHAT_MEMBER))
