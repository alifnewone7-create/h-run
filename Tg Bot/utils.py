import asyncio
import html
import logging
import re
from datetime import timedelta
from functools import wraps

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.types import MessageEntity as AioEntity
from aiogram.utils.text_decorations import html_decoration
from telegram import Bot, Message, Update, User
from telegram import InlineKeyboardMarkup as PtbMarkup
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import ContextTypes, filters

import database as db
from config import ADMIN_IDS

log = logging.getLogger(__name__)

ADMIN = filters.User(user_id=ADMIN_IDS)
TG_EMOJI = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', re.S)
TAGS = re.compile(r"<[^>]+>")
URL_RE = re.compile(r"^(https?|tg)://\S+$")
MAX_BUTTONS = 20
CAPTION_LIMIT = 1024
SENDERS = {
    "photo": "send_photo",
    "video": "send_video",
    "audio": "send_audio",
    "animation": "send_animation",
    "document": "send_document",
    "voice": "send_voice",
}


# ---------- admin / state ----------
def admin_only(fn):
    @wraps(fn)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user and update.effective_user.id in ADMIN_IDS:
            return await fn(update, ctx)
    return wrapper


def set_state(ctx: ContextTypes.DEFAULT_TYPE, state: str, **data) -> None:
    ctx.user_data.update(data, state=state)


# ---------- aiogram bridge (premium emoji + coloured buttons) ----------
def html_text(msg: Message) -> str:
    # aiogram turns entities (incl. premium emoji) into HTML with <tg-emoji>
    if msg.text is not None:
        text, entities = msg.text, msg.entities
    else:
        text, entities = msg.caption or "", msg.caption_entities
    return html_decoration.unparse(text, [AioEntity.model_validate(e.to_dict()) for e in entities])


def to_ptb(kb: InlineKeyboardMarkup | None) -> PtbMarkup | None:
    return PtbMarkup.de_json(kb.model_dump(exclude_none=True)) if kb else None


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
    return sum(e.type == "custom_emoji" for e in (*msg.entities, *msg.caption_entities))


def _plain_len(html_text: str) -> int:
    plain = html.unescape(TAGS.sub("", html_text or ""))
    return len(plain.encode("utf-16-le")) // 2


# ---------- media / buttons ----------
def normalize_url(raw: str) -> str | None:
    url = raw.strip()
    if url.startswith(("t.me/", "www.")):
        url = "https://" + url
    return url if URL_RE.match(url) else None


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
    ce = next((e for e in msg.entities if e.type == "custom_emoji"), None)
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


def build_markup(buttons: list[dict], icons: bool = True, per_row: int = 1) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    btns = [make_button(b, icons) for b in buttons]
    return InlineKeyboardMarkup(inline_keyboard=[btns[i:i + per_row] for i in range(0, len(btns), per_row)])


def strip_icons(kb: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup | None:
    if not kb:
        return kb
    rows = [[b.model_copy(update={"icon_custom_emoji_id": None}) for b in row] for row in kb.inline_keyboard]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- sending ----------
async def _deliver(bot: Bot, chat_id: int, media: dict | None, text: str, kb) -> Message:
    markup = to_ptb(kb)
    if not media:
        return await bot.send_message(chat_id, text, reply_markup=markup)
    send = getattr(bot, SENDERS[media["type"]])
    if _plain_len(text) <= CAPTION_LIMIT:
        return await send(chat_id, media["file_id"], caption=text or None, reply_markup=markup)
    await send(chat_id, media["file_id"])
    return await bot.send_message(chat_id, text, reply_markup=markup)


async def send_custom(bot: Bot, chat_id: int, cfg: dict, user: User, channel: str) -> Message:
    text = render(cfg["text"], user, channel)
    layout = cfg.get("layout", 1)
    try:
        return await _deliver(bot, chat_id, cfg.get("media"), text, build_markup(cfg["buttons"], per_row=layout))
    except BadRequest as e:
        log.warning("Retrying without premium emoji (bot owner needs Telegram Premium): %s", e)
        return await _deliver(bot, chat_id, cfg.get("media"), strip_tg_emoji(text), build_markup(cfg["buttons"], False, layout))


async def send_panel(bot: Bot, chat_id: int, text: str, kb: InlineKeyboardMarkup | None = None) -> Message:
    try:
        return await bot.send_message(chat_id, text, reply_markup=to_ptb(kb))
    except BadRequest:
        return await bot.send_message(chat_id, strip_tg_emoji(text), reply_markup=to_ptb(strip_icons(kb)))


async def safe_edit(msg: Message, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    try:
        await msg.edit_text(text, reply_markup=to_ptb(kb))
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        try:
            await msg.edit_text(strip_tg_emoji(text), reply_markup=to_ptb(strip_icons(kb)))
        except BadRequest:
            await send_panel(msg.get_bot(), msg.chat.id, text, kb)


# ---------- broadcast ----------
async def _copy(bot: Bot, uid: int, from_chat: int, msg_id: int, markup=None, retries: int = 3) -> str:
    try:
        await bot.copy_message(uid, from_chat, msg_id, reply_markup=markup)
        return "sent"
    except RetryAfter as e:
        if retries == 0:
            return "failed"
        wait = e.retry_after
        await asyncio.sleep((wait.total_seconds() if isinstance(wait, timedelta) else wait) + 1)
        return await _copy(bot, uid, from_chat, msg_id, markup, retries - 1)
    except Forbidden:
        await db.set_blocked(uid)
        return "blocked"
    except TelegramError:
        return "failed"


def _report(title: str, total: int, res: dict) -> str:
    return (
        f"{title}\n\n"
        f"👥 Target: <b>{total}</b>\n"
        f"✅ Sent: <b>{res['sent']}</b>\n"
        f"🚫 Blocked: <b>{res['blocked']}</b>\n"
        f"❌ Failed: <b>{res['failed']}</b>"
    )


async def run_broadcast(
    bot: Bot, admin_chat: int, from_chat: int, msg_id: int, audience: str, markup: PtbMarkup | None = None
) -> None:
    ids = await db.get_user_ids(audience)
    total = len(ids)
    res = {"sent": 0, "blocked": 0, "failed": 0}
    label = f"{db.AUDIENCE_LABELS[audience]} Users"
    status = await bot.send_message(admin_chat, _report(f"📢 <b>Broadcast running</b> ({label})", total, res))
    for i, uid in enumerate(ids, 1):
        res[await _copy(bot, uid, from_chat, msg_id, markup)] += 1
        if i % 50 == 0:
            await safe_edit(status, _report(f"📢 <b>Broadcast running</b> ({label}) {i}/{total}", total, res))
        await asyncio.sleep(0.05)
    await safe_edit(status, _report(f"✅ <b>Broadcast finished</b> ({label})", total, res))
    log.info("Broadcast %s done: %s", audience, res)
