import html
import re

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import database as db
import keyboards as kb
from config import ADMIN_IDS
from utils import (
    button_label,
    count_tg_emoji,
    extract_media,
    make_button,
    parse_button_name,
    render,
    safe_edit,
    send_custom,
    send_panel,
)

router = Router(name="wlc")
router.message.filter(F.from_user.id.in_(ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))

KINDS = {"start": "Start Msg", "welcome": "Wlc Msg"}
DESC = {
    "start": "Sent when a user sends /start to the bot.",
    "welcome": "Sent when a user requests to join your channel.",
}
VARS_HELP = "<code>{first_name}</code>  <code>{username}</code>  <code>{channel}</code>"
MAX_BUTTONS = 20
URL_RE = re.compile(r"^(https?|tg)://\S+$")


class Wlc(StatesGroup):
    text = State()
    media = State()
    btn_name = State()
    btn_url = State()


def _args(cb: CallbackQuery) -> list[str]:
    return cb.data.split(":")[1:]


def normalize_url(raw: str) -> str | None:
    url = raw.strip()
    if url.startswith(("t.me/", "www.")):
        url = "https://" + url
    return url if URL_RE.match(url) else None


# ---------- screens ----------
def summary(kind: str, cfg: dict) -> str:
    media = cfg["media"]["type"].title() if cfg["media"] else "None"
    return (
        f"⚙️ <b>{KINDS[kind]}</b>\n<i>{DESC[kind]}</i>\n\n"
        f"📝 Text: <b>{'Set' if cfg['text'] else 'Empty'}</b>\n"
        f"🖼 Media: <b>{media}</b>\n"
        f"🔘 Buttons: <b>{len(cfg['buttons'])}</b>\n"
        f"✨ Premium emoji: <b>{count_tg_emoji(cfg['text'])}</b>"
    )


def button_info(kind: str, i: int, b: dict) -> str:
    return (
        f"🔘 <b>{KINDS[kind]} – Button #{i + 1}</b>\n\n"
        f"Name: <b>{html.escape(button_label(b))}</b>\n"
        f"Link: {html.escape(b['url'])}\n"
        f"Color: <b>{kb.STYLES[b.get('style') or 'none']}</b>\n"
        f"Premium icon: <b>{'Yes' if b.get('icon') else 'No'}</b>\n\n"
        "👆 This is how the button looks."
    )


async def show_kind(target: Message, kind: str, edit: bool = True) -> None:
    cfg = await db.get_msg(kind)
    if edit:
        await safe_edit(target, summary(kind, cfg), kb.kind_menu(kind))
    else:
        await send_panel(target.bot, target.chat.id, summary(kind, cfg), kb.kind_menu(kind))


async def show_buttons(target: Message, kind: str, edit: bool = True) -> None:
    cfg = await db.get_msg(kind)
    text = (
        f"🔘 <b>{KINDS[kind]} – Buttons</b> ({len(cfg['buttons'])}/{MAX_BUTTONS})\n\n"
        "Tap a button to edit or delete it, or add a new one."
    )
    markup = kb.buttons_list(kind, [button_label(b) for b in cfg["buttons"]])
    if edit:
        await safe_edit(target, text, markup)
    else:
        await send_panel(target.bot, target.chat.id, text, markup)


async def show_button(target: Message, kind: str, i: int, edit: bool = True) -> None:
    b = (await db.get_msg(kind))["buttons"][i]
    markup = kb.button_menu(kind, i, make_button(b))
    if edit:
        await safe_edit(target, button_info(kind, i, b), markup)
    else:
        await send_panel(target.bot, target.chat.id, button_info(kind, i, b), markup)


async def _button_or_alert(cb: CallbackQuery, kind: str, i: int) -> dict | None:
    buttons = (await db.get_msg(kind))["buttons"]
    if i < len(buttons):
        return buttons[i]
    await cb.answer("Button not found", show_alert=True)
    await show_buttons(cb.message, kind)
    return None


# ---------- menus ----------
@router.callback_query(F.data == "adm:wlc")
async def cb_wlc(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(cb.message, "👋 <b>Wlc Setting</b>\n\nChoose which message you want to customize 👇", kb.wlc_menu())
    await cb.answer()


@router.callback_query(F.data.in_({"cfg:start", "cfg:welcome"}))
async def cb_kind(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_kind(cb.message, _args(cb)[0])
    await cb.answer()


@router.callback_query(F.data.startswith("prv:"))
async def cb_preview(cb: CallbackQuery, bot: Bot):
    kind = _args(cb)[0]
    await send_custom(bot, cb.message.chat.id, await db.get_msg(kind), cb.from_user, "Demo Channel")
    await cb.answer("👁 Preview sent")


@router.callback_query(F.data.startswith("rst:"))
async def cb_reset(cb: CallbackQuery):
    kind = _args(cb)[0]
    await db.reset_msg(kind)
    await show_kind(cb.message, kind)
    await cb.answer("♻️ Reset to default")


# ---------- text ----------
@router.callback_query(F.data.startswith("txt:"))
async def cb_text(cb: CallbackQuery, state: FSMContext):
    kind = _args(cb)[0]
    cfg = await db.get_msg(kind)
    await state.set_state(Wlc.text)
    await state.update_data(kind=kind)
    await safe_edit(
        cb.message,
        f"📝 <b>{KINDS[kind]} – Set Text</b>\n\nCurrent text:\n━━━━━━━━━━━━\n"
        f"{render(cfg['text'], cb.from_user, 'Demo Channel')}\n━━━━━━━━━━━━\n\n"
        "Send the new text. All Telegram formatting and ✨ Premium emoji are supported.\n"
        f"Variables: {VARS_HELP}",
        kb.back_to(f"cfg:{kind}"),
    )
    await cb.answer()


@router.message(Wlc.text)
async def on_text(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("⚠️ Please send a text message.")
        return
    kind = (await state.get_data())["kind"]
    cfg = await db.get_msg(kind)
    cfg["text"] = message.html_text
    await db.set_msg(kind, cfg)
    await state.clear()
    await message.answer("✅ Text saved!")
    await show_kind(message, kind, edit=False)


# ---------- media ----------
@router.callback_query(F.data.startswith("med:"))
async def cb_media(cb: CallbackQuery, state: FSMContext):
    kind = _args(cb)[0]
    cfg = await db.get_msg(kind)
    current = cfg["media"]["type"].title() if cfg["media"] else "None"
    await state.set_state(Wlc.media)
    await state.update_data(kind=kind)
    await safe_edit(
        cb.message,
        f"🖼 <b>{KINDS[kind]} – Set Media</b>\n\nCurrent media: <b>{current}</b>\n\n"
        "Send a photo, video, audio, GIF, voice or document.\n"
        "The message text is shown as the media caption. If you send the media with a caption, "
        "that caption becomes the new text.",
        kb.media_menu(kind, bool(cfg["media"])),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("medr:"))
async def cb_media_remove(cb: CallbackQuery, state: FSMContext):
    kind = _args(cb)[0]
    cfg = await db.get_msg(kind)
    cfg["media"] = None
    await db.set_msg(kind, cfg)
    await state.clear()
    await show_kind(cb.message, kind)
    await cb.answer("🗑 Media removed")


@router.message(Wlc.media)
async def on_media(message: Message, state: FSMContext):
    media = extract_media(message)
    if not media:
        await message.answer("⚠️ Please send a photo, video, audio, GIF, voice or document.")
        return
    kind = (await state.get_data())["kind"]
    cfg = await db.get_msg(kind)
    cfg["media"] = media
    if message.caption:
        cfg["text"] = message.html_text
    await db.set_msg(kind, cfg)
    await state.clear()
    await message.answer(f"✅ {media['type'].title()} saved!")
    await show_kind(message, kind, edit=False)


# ---------- buttons ----------
@router.callback_query(F.data.startswith("btn:"))
async def cb_buttons(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_buttons(cb.message, _args(cb)[0])
    await cb.answer()


@router.callback_query(F.data.startswith("bta:"))
async def cb_button_add(cb: CallbackQuery, state: FSMContext):
    kind = _args(cb)[0]
    if len((await db.get_msg(kind))["buttons"]) >= MAX_BUTTONS:
        await cb.answer(f"Maximum {MAX_BUTTONS} buttons", show_alert=True)
        return
    await state.set_state(Wlc.btn_name)
    await state.update_data(kind=kind, idx=None)
    await safe_edit(
        cb.message,
        "➕ <b>New Button – Step 1/3</b>\n\nSend the button name.\n"
        "Emoji are supported. A ✨ Premium emoji is shown as the button icon.",
        kb.back_to(f"btn:{kind}"),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("bte:"))
async def cb_button_edit(cb: CallbackQuery, state: FSMContext):
    kind, i = _args(cb)
    await state.clear()
    if await _button_or_alert(cb, kind, int(i)):
        await show_button(cb.message, kind, int(i))
        await cb.answer()


@router.callback_query(F.data.startswith("bten:") | F.data.startswith("btel:"))
async def cb_button_field(cb: CallbackQuery, state: FSMContext):
    action = cb.data.split(":")[0]
    kind, i = _args(cb)
    if not await _button_or_alert(cb, kind, int(i)):
        return
    await state.set_state(Wlc.btn_name if action == "bten" else Wlc.btn_url)
    await state.update_data(kind=kind, idx=int(i))
    prompt = (
        "✏️ Send the new button name (emoji and ✨ Premium emoji supported)."
        if action == "bten"
        else "🔗 Send the new button link (https://... or t.me/...)."
    )
    await safe_edit(cb.message, prompt, kb.back_to(f"bte:{kind}:{i}"))
    await cb.answer()


@router.callback_query(F.data.startswith("btec:"))
async def cb_button_color(cb: CallbackQuery):
    kind, i = _args(cb)
    if await _button_or_alert(cb, kind, int(i)):
        await safe_edit(cb.message, "🎨 <b>Choose the button color</b>", kb.color_menu(kind, int(i)))
        await cb.answer()


@router.callback_query(F.data.startswith("btc:"))
async def cb_button_set_color(cb: CallbackQuery):
    kind, i, style = _args(cb)
    cfg = await db.get_msg(kind)
    if int(i) >= len(cfg["buttons"]) or style not in kb.STYLES:
        await cb.answer("Button not found", show_alert=True)
        return
    cfg["buttons"][int(i)]["style"] = None if style == "none" else style
    await db.set_msg(kind, cfg)
    await show_button(cb.message, kind, int(i))
    await cb.answer(f"Color: {kb.STYLES[style]}")


@router.callback_query(F.data.startswith("btd:"))
async def cb_button_delete(cb: CallbackQuery):
    kind, i = _args(cb)
    cfg = await db.get_msg(kind)
    if int(i) < len(cfg["buttons"]):
        cfg["buttons"].pop(int(i))
        await db.set_msg(kind, cfg)
    await show_buttons(cb.message, kind)
    await cb.answer("🗑 Button deleted")


@router.message(Wlc.btn_name)
async def on_button_name(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("⚠️ Please send the button name as text.")
        return
    name, icon, alt = parse_button_name(message)
    data = await state.get_data()
    kind, idx = data["kind"], data["idx"]
    if idx is None:
        await state.update_data(name=name, icon=icon, alt=alt)
        await state.set_state(Wlc.btn_url)
        await message.answer(
            "🔗 <b>New Button – Step 2/3</b>\n\nSend the button link (https://... or t.me/...).",
            reply_markup=kb.back_to(f"btn:{kind}"),
        )
        return
    cfg = await db.get_msg(kind)
    cfg["buttons"][idx].update(text=name, icon=icon, alt=alt)
    await db.set_msg(kind, cfg)
    await state.clear()
    await show_button(message, kind, idx, edit=False)


@router.message(Wlc.btn_url)
async def on_button_url(message: Message, state: FSMContext):
    url = normalize_url(message.text or "")
    if not url:
        await message.answer("⚠️ Invalid link. Example: <code>https://t.me/yourchannel</code>")
        return
    data = await state.get_data()
    kind, idx = data["kind"], data["idx"]
    cfg = await db.get_msg(kind)
    await state.clear()
    if idx is None:
        cfg["buttons"].append({"text": data["name"], "icon": data["icon"], "alt": data["alt"], "url": url, "style": None})
        await db.set_msg(kind, cfg)
        await message.answer(
            "🎨 <b>New Button – Step 3/3</b>\n\nChoose the button color:",
            reply_markup=kb.color_menu(kind, len(cfg["buttons"]) - 1),
        )
        return
    cfg["buttons"][idx]["url"] = url
    await db.set_msg(kind, cfg)
    await show_button(message, kind, idx, edit=False)
