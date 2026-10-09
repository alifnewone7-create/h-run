import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.types import Message

import database as db
from config import ADMIN_IDS
from utils import send_custom

log = logging.getLogger(__name__)
router = Router(name="user")


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    user = message.from_user
    await db.upsert_user(user.id, user.first_name, user.username, started=True)
    try:
        await send_custom(bot, message.chat.id, await db.get_msg("start"), user, "")
    except TelegramAPIError as e:
        log.warning("Start message to %s failed: %s", user.id, e)
    if user.id in ADMIN_IDS:
        await message.answer("🛠 Use /admin to open the admin panel.")
