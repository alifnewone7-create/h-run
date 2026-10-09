import asyncio
import html
import logging
import re

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User

import database as db

log = logging.getLogger(__name__)

TG_EMOJI = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', re.S)
TAGS = re.compile(r"<[^>]+>")
CAPTION_LIMIT = 1024
SENDERS = {
    "photo": "send_photo",
    "video": "send_video",
    "audio": "send_audio",
    "animation": "send_animation",
    "document": "send_document",
    "voice": "send_voice",
}


# ---------- text helpers ----------
def render(template: str, user: User, channel: str) -> str:
    first = html.escape(user.first_name or "")
    values = {
        "{first_name}": first,
        "{username}": f"@{user.username}" if user.username else first,
        "{channel}": html.escape(channel or ""),
    }
    for key, val in values.items():
        template = template.replace(key, val)
    return template


def count_tg_emoji(html_text: str) -> int:
    return len(TG_EMOJI.findall(html_text or ""))


def strip_tg_emoji(html_text: str) -> str:
    return TG_EMOJI.sub(r"\1", html_text or "")


def count_entities(msg: Message) -> int:
    entities = (msg.entities or []) + (msg.caption_entities or [])
    return sum(e.type == "custom_emoji" for e in entities)


def _plain_len(html_text: str) -> int:
    plain = html.unescape(TAGS.sub("", html_text or ""))
    return len(plain.encode("utf-16-le")) // 2


# ---------- media / buttons ----------
def extract_media(msg: Message) -> dict | None:
    if msg.photo:
        return {"type": "photo", "file_id": msg.photo[-1].file_id}
    for kind in ("animation", "video", "audio", "voice", "document"):
        obj = getattr(msg, kind)
        if obj:
            return {"type": kind, "file_id": obj.file_id}
    return None


def parse_button_name(msg: Message) -> tuple[str, str | None, str | None]:
    # first premium emoji becomes the button icon, the rest stays as plain text
    text = msg.text or ""
    ce = next((e for e in msg.entities or [] if e.type == "custom_emoji"), None)
    if not ce:
        return text.strip(), None, None
    raw = text.encode("utf-16-le")
    start, end = ce.offset * 2, (ce.offset + ce.length) * 2
    alt = raw[start:end].decode("utf-16-le")
    rest = (raw[:start] + raw[end:]).decode("utf-16-le").strip()
    return rest or alt, ce.custom_emoji_id, alt


def button_label(b: dict) -> str:
    return f"{b['alt']} {b['text']}" if b.get("alt") and b["alt"] != b["text"] else b["text"]


def make_button(b: dict, icons: bool = True) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=b["text"] if icons else button_label(b),
        url=b["url"],
        style=b.get("style"),
        icon_custom_emoji_id=b.get("icon") if icons else None,
    )


def build_markup(buttons: list[dict], icons: bool = True) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[make_button(b, icons)] for b in buttons])


def strip_icons(kb: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup | None:
    if not kb:
        return kb
    rows = [[b.model_copy(update={"icon_custom_emoji_id": None}) for b in row] for row in kb.inline_keyboard]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- sending ----------
async def _deliver(bot: Bot, chat_id: int, media: dict | None, text: str, markup) -> Message:
    if not media:
        return await bot.send_message(chat_id, text, reply_markup=markup)
    send = getattr(bot, SENDERS[media["type"]])
    if _plain_len(text) <= CAPTION_LIMIT:
        return await send(chat_id, media["file_id"], caption=text or None, reply_markup=markup)
    await send(chat_id, media["file_id"])
    return await bot.send_message(chat_id, text, reply_markup=markup)


async def send_custom(bot: Bot, chat_id: int, cfg: dict, user: User, channel: str) -> Message:
    text = render(cfg["text"], user, channel)
    try:
        return await _deliver(bot, chat_id, cfg.get("media"), text, build_markup(cfg["buttons"]))
    except TelegramBadRequest as e:
        log.warning("Retrying without premium emoji (bot owner needs Telegram Premium): %s", e)
        return await _deliver(bot, chat_id, cfg.get("media"), strip_tg_emoji(text), build_markup(cfg["buttons"], False))


async def send_panel(bot: Bot, chat_id: int, text: str, kb: InlineKeyboardMarkup | None = None) -> Message:
    try:
        return await bot.send_message(chat_id, text, reply_markup=kb)
    except TelegramBadRequest:
        return await bot.send_message(chat_id, strip_tg_emoji(text), reply_markup=strip_icons(kb))


async def safe_edit(msg: Message, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    try:
        await msg.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" in str(e):
            return
        try:
            await msg.edit_text(strip_tg_emoji(text), reply_markup=strip_icons(kb))
        except TelegramBadRequest:
            await send_panel(msg.bot, msg.chat.id, text, kb)


# ---------- broadcast ----------
async def _copy(bot: Bot, uid: int, from_chat: int, msg_id: int, retries: int = 3) -> str:
    try:
        await bot.copy_message(uid, from_chat, msg_id)
        return "sent"
    except TelegramRetryAfter as e:
        if retries == 0:
            return "failed"
        await asyncio.sleep(e.retry_after + 1)
        return await _copy(bot, uid, from_chat, msg_id, retries - 1)
    except TelegramForbiddenError:
        await db.set_blocked(uid)
        return "blocked"
    except TelegramAPIError:
        return "failed"


def _report(title: str, total: int, res: dict) -> str:
    return (
        f"{title}\n\n"
        f"👥 Target: <b>{total}</b>\n"
        f"✅ Sent: <b>{res['sent']}</b>\n"
        f"🚫 Blocked: <b>{res['blocked']}</b>\n"
        f"❌ Failed: <b>{res['failed']}</b>"
    )


async def run_broadcast(bot: Bot, admin_chat: int, from_chat: int, msg_id: int, period: str) -> None:
    ids = await db.get_user_ids(period)
    total = len(ids)
    res = {"sent": 0, "blocked": 0, "failed": 0}
    label = db.PERIOD_LABELS[period]
    status = await bot.send_message(admin_chat, _report(f"📢 <b>Broadcast running</b> ({label})", total, res))
    for i, uid in enumerate(ids, 1):
        res[await _copy(bot, uid, from_chat, msg_id)] += 1
        if i % 50 == 0:
            await safe_edit(status, _report(f"📢 <b>Broadcast running</b> ({label}) {i}/{total}", total, res))
        await asyncio.sleep(0.05)
    await safe_edit(status, _report(f"✅ <b>Broadcast finished</b> ({label})", total, res))
    log.info("Broadcast %s done: %s", period, res)
