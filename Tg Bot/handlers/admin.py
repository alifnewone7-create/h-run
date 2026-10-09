import asyncio
import html
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import database as db
import keyboards as kb
from config import ADMIN_IDS
from utils import count_entities, run_broadcast, safe_edit

router = Router(name="admin")
router.message.filter(F.from_user.id.in_(ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))

MODE_NAMES = {"non": "Non Approve", "auto": "Auto Approve"}


class Broadcast(StatesGroup):
    waiting = State()
    confirm = State()


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
@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(await home_text(), reply_markup=kb.main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Cancelled.\n\n" + await home_text(), reply_markup=kb.main_menu())


@router.callback_query(F.data.in_({"adm:home", "adm:cancel"}))
async def cb_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(cb.message, await home_text(), kb.main_menu())
    await cb.answer()


# ---------- approve mode ----------
@router.callback_query(F.data == "adm:mode")
async def cb_mode(cb: CallbackQuery):
    mode = await db.get_setting("approve_mode", "non")
    await safe_edit(cb.message, await mode_text(mode), kb.mode_menu(mode))
    await cb.answer()


@router.callback_query(F.data.in_({"mode:non", "mode:auto"}))
async def cb_set_mode(cb: CallbackQuery):
    mode = cb.data.split(":")[1]
    await db.set_setting("approve_mode", mode)
    await safe_edit(cb.message, await mode_text(mode), kb.mode_menu(mode))
    await cb.answer(f"✅ {MODE_NAMES[mode]} ON")


# ---------- broadcast ----------
@router.callback_query(F.data == "adm:bc")
async def cb_broadcast(cb: CallbackQuery):
    counts = {p: await db.count_users(p) for p in db.PERIODS}
    await safe_edit(
        cb.message,
        "📢 <b>Broadcast</b>\n\nSelect which users should receive the message 👇",
        kb.broadcast_menu(counts),
    )
    await cb.answer()


@router.callback_query(F.data.in_({f"bc:{p}" for p in db.PERIODS}))
async def cb_bc_period(cb: CallbackQuery, state: FSMContext):
    period = cb.data.split(":")[1]
    await state.set_state(Broadcast.waiting)
    await state.update_data(period=period)
    await safe_edit(
        cb.message,
        f"📢 <b>Broadcast → {db.PERIOD_LABELS[period]} Users</b>\n\n"
        "Send the message you want to broadcast (text / photo / video / file / sticker). "
        "Formatting and ✨ Premium (custom) emoji are kept as they are.",
        kb.cancel_menu(),
    )
    await cb.answer()


@router.message(Broadcast.waiting)
async def on_bc_message(message: Message, state: FSMContext):
    data = await state.get_data()
    total = await db.count_users(data["period"])
    await state.update_data(from_chat=message.chat.id, msg_id=message.message_id)
    await state.set_state(Broadcast.confirm)
    await message.reply(
        f"👆 This message will be sent to <b>{total}</b> users ({db.PERIOD_LABELS[data['period']]}).\n"
        f"✨ Premium emoji: <b>{count_entities(message)}</b>\n\nConfirm?",
        reply_markup=kb.confirm_broadcast(),
    )


@router.callback_query(Broadcast.confirm, F.data == "bc:go")
async def cb_bc_go(cb: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    await safe_edit(cb.message, "🚀 Broadcast started...")
    asyncio.create_task(run_broadcast(bot, cb.message.chat.id, data["from_chat"], data["msg_id"], data["period"]))
    await cb.answer()


# ---------- statistics ----------
@router.callback_query(F.data == "adm:stats")
async def cb_stats(cb: CallbackQuery):
    await safe_edit(cb.message, await stats_text(), kb.stats_menu())
    await cb.answer("🔄 Updated")


# ---------- channels ----------
@router.callback_query(F.data == "adm:channels")
async def cb_channels(cb: CallbackQuery):
    rows = await db.list_channels()
    body = "\n".join(f"• <b>{html.escape(r['title'] or 'Untitled')}</b> (<code>{r['chat_id']}</code>)" for r in rows)
    text = "📡 <b>Channels (bot admin)</b>\n\n" + (body or "No channels yet. Make the bot an admin in your channel.")
    await safe_edit(cb.message, text, kb.back_menu())
    await cb.answer()
