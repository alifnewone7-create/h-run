import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatJoinRequest, ChatMemberUpdated

import database as db
from config import ADMIN_IDS
from utils import send_custom

log = logging.getLogger(__name__)
router = Router(name="join")

ADMIN_STATUSES = {"administrator", "creator"}


@router.chat_join_request()
async def on_join_request(req: ChatJoinRequest, bot: Bot):
    user = req.from_user
    await db.upsert_user(user.id, user.first_name, user.username)
    await db.upsert_channel(req.chat.id, req.chat.title, True)
    mode = await db.get_setting("approve_mode", "non")

    # message first: user_chat_id is valid only until the request is processed
    try:
        await send_custom(bot, req.user_chat_id, await db.get_msg("welcome"), user, req.chat.title)
    except TelegramAPIError as e:
        log.warning("Message to %s failed: %s", user.id, e)

    status = "pending"
    if mode == "auto":
        try:
            await req.approve()
            status = "approved"
        except TelegramAPIError as e:
            log.warning("Approve %s in %s failed: %s", user.id, req.chat.id, e)
    await db.add_request(user.id, req.chat.id, status)


@router.my_chat_member(F.chat.type.in_({"channel", "supergroup", "group"}))
async def on_bot_status(ev: ChatMemberUpdated, bot: Bot):
    is_admin = ev.new_chat_member.status in ADMIN_STATUSES
    await db.upsert_channel(ev.chat.id, ev.chat.title, is_admin)
    state = "✅ is now an admin" if is_admin else "⚠️ is no longer an admin"
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"📡 Bot {state} in <b>{html.escape(ev.chat.title or '')}</b>.")
        except TelegramAPIError:
            pass


@router.my_chat_member(F.chat.type == "private")
async def on_private_status(ev: ChatMemberUpdated):
    await db.set_blocked(ev.chat.id, ev.new_chat_member.status == "kicked")
