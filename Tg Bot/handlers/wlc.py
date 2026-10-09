import html

from telegram import CallbackQuery, Message, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

import database as db
import keyboards as kb
from utils import (
    MAX_BUTTONS,
    admin_only,
    button_label,
    count_tg_emoji,
    extract_media,
    html_text,
    make_button,
    move_item,
    normalize_url,
    parse_button_name,
    render,
    safe_edit,
    send_custom,
    send_panel,
    set_state,
    to_ptb,
)

KINDS = {"start": "Start Msg", "welcome": "Wlc Msg"}
DESC = {
    "start": "Sent when a user sends /start to the bot.",
    "welcome": "Sent when a user requests to join your channel.",
}
VARS_HELP = "<code>{first_name}</code>  <code>{username}</code>  <code>{channel}</code>"


def _args(cb: CallbackQuery) -> list[str]:
    return cb.data.split(":")[1:]


# ---------- screens ----------
def summary(kind: str, cfg: dict) -> str:
    media = cfg["media"]["type"].title() if cfg["media"] else "None"
    return (
        f"⚙️ <b>{KINDS[kind]}</b>\n<i>{DESC[kind]}</i>\n\n"
        f"📝 Text: <b>{'Set' if cfg['text'] else 'Empty'}</b>\n"
        f"🖼 Media: <b>{media}</b>\n"
        f"🔘 Buttons: <b>{len(cfg['buttons'])}</b>  📐 Layout: <b>{kb.LAYOUTS[cfg.get('layout', 1)]}</b>\n"
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


async def _show(target: Message, text: str, markup, edit: bool) -> None:
    if edit:
        await safe_edit(target, text, markup)
    else:
        await send_panel(target.get_bot(), target.chat.id, text, markup)


async def show_kind(target: Message, kind: str, edit: bool = True) -> None:
    await _show(target, summary(kind, await db.get_msg(kind)), kb.kind_menu(kind), edit)


async def show_buttons(target: Message, kind: str, edit: bool = True) -> None:
    cfg = await db.get_msg(kind)
    layout = cfg.get("layout", 1)
    text = (
        f"🔘 <b>{KINDS[kind]} – Buttons</b> ({len(cfg['buttons'])}/{MAX_BUTTONS})\n\n"
        f"📐 Layout: <b>{kb.LAYOUTS[layout]}</b>\n\n"
        "Tap a button to edit or delete it, change the layout, or add a new one."
    )
    await _show(target, text, kb.buttons_list(kind, [button_label(b) for b in cfg["buttons"]], layout), edit)


async def show_button(target: Message, kind: str, i: int, edit: bool = True) -> None:
    buttons = (await db.get_msg(kind))["buttons"]
    b = buttons[i]
    await _show(target, button_info(kind, i, b), kb.button_menu(kind, i, make_button(b), len(buttons)), edit)


async def _button_or_alert(cb: CallbackQuery, kind: str, i: int) -> dict | None:
    buttons = (await db.get_msg(kind))["buttons"]
    if i < len(buttons):
        return buttons[i]
    await cb.answer("Button not found", show_alert=True)
    await show_buttons(cb.message, kind)
    return None


# ---------- menus ----------
@admin_only
async def cb_wlc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await safe_edit(
        update.callback_query.message,
        "👋 <b>Wlc Setting</b>\n\nChoose which message you want to customize 👇",
        kb.wlc_menu(),
    )
    await update.callback_query.answer()


@admin_only
async def cb_kind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    ctx.user_data.clear()
    await show_kind(cb.message, _args(cb)[0])
    await cb.answer()


@admin_only
async def cb_preview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    kind = _args(cb)[0]
    await send_custom(ctx.bot, cb.message.chat.id, await db.get_msg(kind), cb.from_user, "Demo Channel")
    await cb.answer("👁 Preview sent")


@admin_only
async def cb_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    kind = _args(cb)[0]
    await db.reset_msg(kind)
    await show_kind(cb.message, kind)
    await cb.answer("♻️ Reset to default")


# ---------- text ----------
@admin_only
async def cb_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    kind = _args(cb)[0]
    cfg = await db.get_msg(kind)
    set_state(ctx, "wlc_text", kind=kind)
    await safe_edit(
        cb.message,
        f"📝 <b>{KINDS[kind]} – Set Text</b>\n\nCurrent text:\n━━━━━━━━━━━━\n"
        f"{render(cfg['text'], cb.from_user, 'Demo Channel')}\n━━━━━━━━━━━━\n\n"
        "Send the new text. All Telegram formatting and ✨ Premium emoji are supported.\n"
        f"Variables: {VARS_HELP}",
        kb.back_to(f"cfg:{kind}"),
    )
    await cb.answer()


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message.text:
        await message.reply_text("⚠️ Please send a text message.")
        return
    kind = ctx.user_data["kind"]
    cfg = await db.get_msg(kind)
    cfg["text"] = html_text(message)
    await db.set_msg(kind, cfg)
    ctx.user_data.clear()
    await message.reply_text("✅ Text saved!")
    await show_kind(message, kind, edit=False)


# ---------- media ----------
@admin_only
async def cb_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    kind = _args(cb)[0]
    cfg = await db.get_msg(kind)
    current = cfg["media"]["type"].title() if cfg["media"] else "None"
    set_state(ctx, "wlc_media", kind=kind)
    await safe_edit(
        cb.message,
        f"🖼 <b>{KINDS[kind]} – Set Media</b>\n\nCurrent media: <b>{current}</b>\n\n"
        "Send a photo, video, audio, GIF, voice or document.\n"
        "The message text is shown as the media caption. If you send the media with a caption, "
        "that caption becomes the new text.",
        kb.media_menu(kind, bool(cfg["media"])),
    )
    await cb.answer()


@admin_only
async def cb_media_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    kind = _args(cb)[0]
    cfg = await db.get_msg(kind)
    cfg["media"] = None
    await db.set_msg(kind, cfg)
    ctx.user_data.clear()
    await show_kind(cb.message, kind)
    await cb.answer("🗑 Media removed")


async def on_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    media = extract_media(message)
    if not media:
        await message.reply_text("⚠️ Please send a photo, video, audio, GIF, voice or document.")
        return
    kind = ctx.user_data["kind"]
    cfg = await db.get_msg(kind)
    cfg["media"] = media
    if message.caption:
        cfg["text"] = html_text(message)
    await db.set_msg(kind, cfg)
    ctx.user_data.clear()
    await message.reply_text(f"✅ {media['type'].title()} saved!")
    await show_kind(message, kind, edit=False)


# ---------- buttons ----------
@admin_only
async def cb_buttons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    ctx.user_data.clear()
    await show_buttons(cb.message, _args(cb)[0])
    await cb.answer()


@admin_only
async def cb_button_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    kind = _args(cb)[0]
    if len((await db.get_msg(kind))["buttons"]) >= MAX_BUTTONS:
        await cb.answer(f"Maximum {MAX_BUTTONS} buttons", show_alert=True)
        return
    set_state(ctx, "btn_name", kind=kind, idx=None)
    await safe_edit(
        cb.message,
        "➕ <b>New Button – Step 1/3</b>\n\nSend the button name.\n"
        "Emoji are supported. A ✨ Premium emoji is shown as the button icon.",
        kb.back_to(f"btn:{kind}"),
    )
    await cb.answer()


@admin_only
async def cb_button_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    kind, i = _args(cb)
    ctx.user_data.clear()
    if await _button_or_alert(cb, kind, int(i)):
        await show_button(cb.message, kind, int(i))
        await cb.answer()


@admin_only
async def cb_button_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    action = cb.data.split(":")[0]
    kind, i = _args(cb)
    if not await _button_or_alert(cb, kind, int(i)):
        return
    set_state(ctx, "btn_name" if action == "bten" else "btn_url", kind=kind, idx=int(i))
    prompt = (
        "✏️ Send the new button name (emoji and ✨ Premium emoji supported)."
        if action == "bten"
        else "🔗 Send the new button link (https://... or t.me/...)."
    )
    await safe_edit(cb.message, prompt, kb.back_to(f"bte:{kind}:{i}"))
    await cb.answer()


@admin_only
async def cb_button_color(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    kind, i = _args(cb)
    if await _button_or_alert(cb, kind, int(i)):
        await safe_edit(cb.message, "🎨 <b>Choose the button color</b>", kb.color_menu(f"btc:{kind}:{i}", f"bte:{kind}:{i}"))
        await cb.answer()


@admin_only
async def cb_button_set_color(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    kind, i, style = _args(cb)
    cfg = await db.get_msg(kind)
    if int(i) >= len(cfg["buttons"]) or style not in kb.STYLES:
        await cb.answer("Button not found", show_alert=True)
        return
    cfg["buttons"][int(i)]["style"] = None if style == "none" else style
    await db.set_msg(kind, cfg)
    await show_button(cb.message, kind, int(i))
    await cb.answer(f"Color: {kb.STYLES[style]}")


@admin_only
async def cb_button_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    kind, i = _args(cb)
    cfg = await db.get_msg(kind)
    if int(i) < len(cfg["buttons"]):
        cfg["buttons"].pop(int(i))
        await db.set_msg(kind, cfg)
    await show_buttons(cb.message, kind)
    await cb.answer("🗑 Button deleted")


@admin_only
async def cb_button_layout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    kind = _args(cb)[0]
    cfg = await db.get_msg(kind)
    cfg["layout"] = 1 if cfg.get("layout", 1) == 2 else 2
    await db.set_msg(kind, cfg)
    await show_buttons(cb.message, kind)
    await cb.answer(f"📐 Layout: {kb.LAYOUTS[cfg['layout']]}")


@admin_only
async def cb_button_move(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    kind, i = _args(cb)
    cfg = await db.get_msg(kind)
    j = move_item(cfg["buttons"], int(i), -1 if cb.data.startswith("btmu:") else 1)
    if j is None:
        await cb.answer("Can't move this button", show_alert=True)
        return
    await db.set_msg(kind, cfg)
    await show_button(cb.message, kind, j)
    await cb.answer(f"✅ Moved to #{j + 1}")


async def on_button_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message.text:
        await message.reply_text("⚠️ Please send the button name as text.")
        return
    name, icon, alt = parse_button_name(message)
    kind, idx = ctx.user_data["kind"], ctx.user_data["idx"]
    if idx is None:
        set_state(ctx, "btn_url", name=name, icon=icon, alt=alt)
        await message.reply_text(
            "🔗 <b>New Button – Step 2/3</b>\n\nSend the button link (https://... or t.me/...).",
            reply_markup=to_ptb(kb.back_to(f"btn:{kind}")),
        )
        return
    cfg = await db.get_msg(kind)
    cfg["buttons"][idx].update(text=name, icon=icon, alt=alt)
    await db.set_msg(kind, cfg)
    ctx.user_data.clear()
    await show_button(message, kind, idx, edit=False)


async def on_button_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    url = normalize_url(message.text or "")
    if not url:
        await message.reply_text("⚠️ Invalid link. Example: <code>https://t.me/yourchannel</code>")
        return
    data = dict(ctx.user_data)
    kind, idx = data["kind"], data["idx"]
    cfg = await db.get_msg(kind)
    ctx.user_data.clear()
    if idx is None:
        cfg["buttons"].append({"text": data["name"], "icon": data["icon"], "alt": data["alt"], "url": url, "style": None})
        await db.set_msg(kind, cfg)
        await message.reply_text(
            "🎨 <b>New Button – Step 3/3</b>\n\nChoose the button color:",
            reply_markup=to_ptb(kb.color_menu(f"btc:{kind}:{len(cfg['buttons']) - 1}", f"bte:{kind}:{len(cfg['buttons']) - 1}")),
        )
        return
    cfg["buttons"][idx]["url"] = url
    await db.set_msg(kind, cfg)
    await show_button(message, kind, idx, edit=False)


STATES = {
    "wlc_text": on_text,
    "wlc_media": on_media,
    "btn_name": on_button_name,
    "btn_url": on_button_url,
}


def register_callbacks(app: Application) -> None:
    app.add_handlers([
        CallbackQueryHandler(cb_wlc, pattern=r"^adm:wlc$"),
        CallbackQueryHandler(cb_kind, pattern=r"^cfg:(start|welcome)$"),
        CallbackQueryHandler(cb_preview, pattern=r"^prv:"),
        CallbackQueryHandler(cb_reset, pattern=r"^rst:"),
        CallbackQueryHandler(cb_text, pattern=r"^txt:"),
        CallbackQueryHandler(cb_media, pattern=r"^med:"),
        CallbackQueryHandler(cb_media_remove, pattern=r"^medr:"),
        CallbackQueryHandler(cb_buttons, pattern=r"^btn:"),
        CallbackQueryHandler(cb_button_add, pattern=r"^bta:"),
        CallbackQueryHandler(cb_button_edit, pattern=r"^bte:"),
        CallbackQueryHandler(cb_button_field, pattern=r"^bte[nl]:"),
        CallbackQueryHandler(cb_button_color, pattern=r"^btec:"),
        CallbackQueryHandler(cb_button_set_color, pattern=r"^btc:"),
        CallbackQueryHandler(cb_button_delete, pattern=r"^btd:"),
        CallbackQueryHandler(cb_button_move, pattern=r"^btm[ud]:(start|welcome):\d+$"),
        CallbackQueryHandler(cb_button_layout, pattern=r"^btl:(start|welcome)$"),
    ])
