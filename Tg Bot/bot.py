import logging

from telegram import BotCommand, BotCommandScopeChat, LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes, Defaults, MessageHandler

import database as db
from config import ADMIN_IDS, BOT_TOKEN, DATABASE_URL
from handlers import admin, join, user, wlc
from utils import ADMIN

STATES = {**admin.STATES, **wlc.STATES}


async def on_state(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    handler = STATES.get(ctx.user_data.get("state"))
    if handler:
        await handler(update, ctx)


async def set_commands(app: Application) -> None:
    bot = app.bot
    await bot.set_my_commands([BotCommand("start", "Start the bot")])
    for admin_id in ADMIN_IDS:
        try:
            await bot.set_my_commands(
                [
                    BotCommand("start", "Start the bot"),
                    BotCommand("admin", "Open admin panel"),
                    BotCommand("cancel", "Cancel current action"),
                ],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except TelegramError:
            logging.warning("Admin %s hasn't started the bot yet", admin_id)


async def post_init(app: Application) -> None:
    await db.connect(DATABASE_URL)
    await set_commands(app)
    logging.info("Bot @%s started", app.bot.username)


async def post_shutdown(app: Application) -> None:
    await db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .defaults(Defaults(parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    admin.register_commands(app)
    user.register(app)
    admin.register_callbacks(app)
    wlc.register_callbacks(app)
    app.add_handler(MessageHandler(ADMIN, on_state))
    join.register(app)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
