import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

import database as db
from config import ADMIN_IDS
from utils import send_custom

log = logging.getLogger(__name__)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = message.from_user
    await db.upsert_user(user.id, user.first_name, user.username, started=True)
    try:
        await send_custom(ctx.bot, message.chat_id, await db.get_msg("start"), user, "")
    except TelegramError as e:
        log.warning("Start message to %s failed: %s", user.id, e)
    if user.id in ADMIN_IDS:
        await message.reply_text("🛠 Use /admin to open the admin panel.")


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
