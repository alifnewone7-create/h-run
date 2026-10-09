import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat

import database as db
from config import ADMIN_IDS, BOT_TOKEN, DATABASE_URL
from handlers import admin, join, user, wlc


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands([BotCommand(command="start", description="Start the bot")])
    for admin_id in ADMIN_IDS:
        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Start the bot"),
                    BotCommand(command="admin", description="Open admin panel"),
                    BotCommand(command="cancel", description="Cancel current action"),
                ],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception:
            logging.warning("Admin %s hasn't started the bot yet", admin_id)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    await db.connect(DATABASE_URL)
    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(admin.router, wlc.router, join.router, user.router)
    await set_commands(bot)
    await bot.delete_webhook(drop_pending_updates=False)
    me = await bot.get_me()
    logging.info("Bot @%s started", me.username)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
