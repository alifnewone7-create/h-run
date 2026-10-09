import html
from datetime import datetime, timezone

from telegram import Bot, Message, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import database as db
import keyboards as kb
from utils import (
    ADMIN,
    MAX_BUTTONS,
    admin_only,
    build_markup,
    button_label,
    count_entities,
    make_button,
    normalize_url,
    parse_button_name,
    run_broadcast,
    safe_edit,
    send_panel,
    set_state,
    to_ptb,
)

MODE_NAMES = {"non": "Non Approve", "auto": "Auto Approve"}


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


async def refresh_member_counts(bot: Bot) -> None:
    for r in await db.list_channels():
        try:
            await db.set_member_count(r["chat_id"], await bot.get_chat_member_count(r["chat_id"]))
        except TelegramError:
            pass


async def stats_text(bot: Bot) -> str:
    await refresh_member_counts(bot)
    s = await db.get_stats()
    mode = await db.get_setting("approve_mode", "non")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return (
        "📊 <b>Statistics</b> (realtime)\n\n"
        + _period_block("👥 <b>New Users</b>", s, "new") + "\n"
        + _period_block("🔥 <b>Active Users</b>", s, "act") + "\n"
        + _period_block("📥 <b>Join Requests</b>", s, "req") + "\n"
        f"✅ Approved: <b>{s['approved']}</b>  ⏳ Pending: <b>{s['pending']}</b>\n"
        f"🟢 Joined: <b>{s['joined']}</b>  🚪 Leaved: <b>{s['leaved']}</b>\n"
        f"👥 Channel Members: <b>{s['channel_members']}</b>\n"
        f"🤖 Bot started: <b>{s['started']}</b>  🚫 Blocked: <b>{s['blocked']}</b>\n"
        f"📡 Channels: <b>{s['channels']}</b>  ⚙️ Mode: <b>{MODE_NAMES[mode]}</b>\n\n"
        f"🕒 Updated: <code>{now}</code>"
    )


# ---------- commands ----------
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await send_panel(ctx.bot, update.effective_chat.id, await home_text(), kb.main_menu())


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await send_panel(ctx.bot, update.effective_chat.id, "❌ Cancelled.\n\n" + await home_text(), kb.main_menu())


@admin_only
async def cb_home(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await safe_edit(update.callback_query.message, await home_text(), kb.main_menu())
    await update.callback_query.answer()


# ---------- approve mode ----------
@admin_only
async def cb_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mode = await db.get_setting("approve_mode", "non")
    await safe_edit(update.callback_query.message, await mode_text(mode), kb.mode_menu(mode))
    await update.callback_query.answer()


@admin_only
async def cb_set_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    mode = cb.data.split(":")[1]
    await db.set_setting("approve_mode", mode)
    await safe_edit(cb.message, await mode_text(mode), kb.mode_menu(mode))
    await cb.answer(f"✅ {MODE_NAMES[mode]} ON")


# ---------- broadcast ----------
@admin_only
async def cb_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    counts = {a: await db.count_users(a) for a in kb.AUDIENCES}
    lines = "\n".join(f"{label}: <b>{counts[a]}</b>" for a, label in kb.AUDIENCES.items())
    await safe_edit(
        update.callback_query.message,
        f"📢 <b>Broadcast</b>\n\n{lines}\n\nSelect which users should receive the message 👇",
        kb.broadcast_menu(counts),
    )
    await update.callback_query.answer()


@admin_only
async def cb_bc_audience(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    audience = cb.data.split(":")[1]
    ctx.user_data.clear()
    set_state(ctx, "bc_wait", audience=audience)
    await safe_edit(
        cb.message,
        f"📢 <b>Broadcast → {kb.AUDIENCES[audience]}</b> ({await db.count_users(audience)})\n\n"
        "Send the message you want to broadcast (text / photo / video / file / sticker). "
        "Formatting and ✨ Premium (custom) emoji are kept as they are.",
        kb.cancel_menu(),
    )
    await cb.answer()


def _bc_buttons_text(buttons: list[dict]) -> str:
    hint = (
        "Tap a button to edit or delete it, add a new one, or tap ✅ Done."
        if buttons else "Add buttons under the broadcast message, or skip to send it without buttons."
    )
    return f"🔘 <b>Broadcast Buttons</b> ({len(buttons)}/{MAX_BUTTONS})\n\n{hint}"


def _bc_button_text(i: int, b: dict) -> str:
    return (
        f"🔘 <b>Broadcast Button #{i + 1}</b>\n\n"
        f"Name: <b>{html.escape(button_label(b))}</b>\n"
        f"Link: {html.escape(b['url'])}\n"
        f"Color: <b>{kb.STYLES[b['style'] or 'none']}</b>\n"
        f"Premium icon: <b>{'Yes' if b['icon'] else 'No'}</b>\n\n"
        "👆 This is how the button looks."
    )


async def _show_bc(target: Message, ctx: ContextTypes.DEFAULT_TYPE, i: int | None = None, edit: bool = True) -> None:
    buttons = ctx.user_data["buttons"]
    if i is None:
        text, markup = _bc_buttons_text(buttons), kb.bc_buttons_menu([button_label(b) for b in buttons])
    else:
        text, markup = _bc_button_text(i, buttons[i]), kb.bc_button_menu(i, make_button(buttons[i]))
    if edit:
        await safe_edit(target, text, markup)
    else:
        await send_panel(target.get_bot(), target.chat.id, text, markup)


async def on_bc_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    set_state(
        ctx, "bc_buttons",
        from_chat=message.chat_id, msg_id=message.message_id, emoji=count_entities(message), buttons=[],
    )
    await message.reply_text(_bc_buttons_text([]), reply_markup=to_ptb(kb.bc_buttons_menu([])), do_quote=True)


def bc_session(fn):
    @admin_only
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if "msg_id" not in ctx.user_data:
            await update.callback_query.answer("⚠️ Broadcast session expired. Start again.", show_alert=True)
            return
        await fn(update, ctx)
    return wrapper


async def _bc_index(cb, ctx: ContextTypes.DEFAULT_TYPE, i: int) -> bool:
    if i < len(ctx.user_data["buttons"]):
        return True
    await cb.answer("Button not found", show_alert=True)
    await _show_bc(cb.message, ctx)
    return False


@bc_session
async def cb_bc_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["state"] = "bc_buttons"
    await _show_bc(update.callback_query.message, ctx)
    await update.callback_query.answer()


@bc_session
async def cb_bc_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    if len(ctx.user_data["buttons"]) >= MAX_BUTTONS:
        await cb.answer(f"Maximum {MAX_BUTTONS} buttons", show_alert=True)
        return
    set_state(ctx, "bc_btn_name", idx=None)
    await safe_edit(
        cb.message,
        "➕ <b>Broadcast Button – Step 1/3</b>\n\nSend the button name.\n"
        "Emoji are supported. A ✨ Premium emoji is shown as the button icon.",
        kb.back_to("bcb:menu"),
    )
    await cb.answer()


@bc_session
async def cb_bc_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    i = int(cb.data.split(":")[1])
    if await _bc_index(cb, ctx, i):
        ctx.user_data["state"] = "bc_buttons"
        await _show_bc(cb.message, ctx, i)
        await cb.answer()


@bc_session
async def cb_bc_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    action, i = cb.data.split(":")
    if not await _bc_index(cb, ctx, int(i)):
        return
    set_state(ctx, "bc_btn_name" if action == "bcen" else "bc_btn_url", idx=int(i))
    prompt = (
        "✏️ Send the new button name (emoji and ✨ Premium emoji supported)."
        if action == "bcen"
        else "🔗 Send the new button link (https://... or t.me/...)."
    )
    await safe_edit(cb.message, prompt, kb.back_to(f"bce:{i}"))
    await cb.answer()


@bc_session
async def cb_bc_color_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    i = int(cb.data.split(":")[1])
    if await _bc_index(cb, ctx, i):
        await safe_edit(cb.message, "🎨 <b>Choose the button color</b>", kb.color_menu(f"bcc:e:{i}", f"bce:{i}"))
        await cb.answer()


@bc_session
async def cb_bc_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    i = int(cb.data.split(":")[1])
    if await _bc_index(cb, ctx, i):
        ctx.user_data["buttons"].pop(i)
        ctx.user_data["state"] = "bc_buttons"
        await _show_bc(cb.message, ctx)
        await cb.answer("🗑 Button deleted")


async def on_bc_btn_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message.text:
        await message.reply_text("⚠️ Please send the button name as text.")
        return
    name, icon, alt = parse_button_name(message)
    idx = ctx.user_data.get("idx")
    if idx is None:
        set_state(ctx, "bc_btn_url", btn={"text": name, "icon": icon, "alt": alt, "style": None})
        await message.reply_text(
            "🔗 <b>Broadcast Button – Step 2/3</b>\n\nSend the button link (https://... or t.me/...).",
            reply_markup=to_ptb(kb.back_to("bcb:menu")),
        )
        return
    ctx.user_data["buttons"][idx].update(text=name, icon=icon, alt=alt)
    ctx.user_data["state"] = "bc_buttons"
    await _show_bc(message, ctx, idx, edit=False)


async def on_bc_btn_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    url = normalize_url(message.text or "")
    if not url:
        await message.reply_text("⚠️ Invalid link. Example: <code>https://t.me/yourchannel</code>")
        return
    buttons, idx = ctx.user_data["buttons"], ctx.user_data.get("idx")
    ctx.user_data["state"] = "bc_buttons"
    if idx is not None:
        buttons[idx]["url"] = url
        await _show_bc(message, ctx, idx, edit=False)
        return
    buttons.append({**ctx.user_data.pop("btn"), "url": url})
    await message.reply_text(
        "🎨 <b>Broadcast Button – Step 3/3</b>\n\nChoose the button color:",
        reply_markup=to_ptb(kb.color_menu(f"bcc:n:{len(buttons) - 1}", "bcb:menu")),
    )


@bc_session
async def cb_bc_color(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    _, mode, i, style = cb.data.split(":")
    if not await _bc_index(cb, ctx, int(i)) or style not in kb.STYLES:
        return
    ctx.user_data["buttons"][int(i)]["style"] = None if style == "none" else style
    await _show_bc(cb.message, ctx, int(i) if mode == "e" else None)
    await cb.answer(f"Color: {kb.STYLES[style]}")


@bc_session
async def cb_bc_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    data = ctx.user_data
    markup = None
    if data["buttons"]:
        # preview with buttons; premium icons need the bot owner's Telegram Premium
        try:
            markup = to_ptb(build_markup(data["buttons"]))
            await ctx.bot.copy_message(cb.message.chat.id, data["from_chat"], data["msg_id"], reply_markup=markup)
        except BadRequest:
            markup = to_ptb(build_markup(data["buttons"], icons=False))
            await ctx.bot.copy_message(cb.message.chat.id, data["from_chat"], data["msg_id"], reply_markup=markup)
    set_state(ctx, "bc_confirm", markup=markup)
    total = await db.count_users(data["audience"])
    text = (
        f"{'👆 Preview above. ' if markup else ''}This message will be sent to <b>{total}</b> "
        f"{kb.AUDIENCES[data['audience']]}.\n"
        f"🔘 Buttons: <b>{len(data['buttons'])}</b>\n"
        f"✨ Premium emoji: <b>{data['emoji']}</b>\n\nConfirm?"
    )
    if markup:
        await send_panel(ctx.bot, cb.message.chat.id, text, kb.confirm_broadcast())
    else:
        await safe_edit(cb.message, text, kb.confirm_broadcast())
    await cb.answer()


@admin_only
async def cb_bc_go(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    data = dict(ctx.user_data)
    if data.get("state") != "bc_confirm":
        await cb.answer("⚠️ Broadcast session expired. Start again.", show_alert=True)
        return
    ctx.user_data.clear()
    await safe_edit(cb.message, "🚀 Broadcast started...")
    ctx.application.create_task(
        run_broadcast(ctx.bot, cb.message.chat.id, data["from_chat"], data["msg_id"], data["audience"], data["markup"])
    )
    await cb.answer()


# ---------- statistics ----------
@admin_only
async def cb_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await safe_edit(update.callback_query.message, await stats_text(ctx.bot), kb.stats_menu())
    await update.callback_query.answer("🔄 Updated")


# ---------- channels ----------
@admin_only
async def cb_channels(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = await db.list_channels()
    body = "\n".join(f"• <b>{html.escape(r['title'] or 'Untitled')}</b> (<code>{r['chat_id']}</code>)" for r in rows)
    text = "📡 <b>Channels (bot admin)</b>\n\n" + (body or "No channels yet. Make the bot an admin in your channel.")
    await safe_edit(update.callback_query.message, text, kb.back_menu())
    await update.callback_query.answer()


STATES = {"bc_wait": on_bc_message, "bc_btn_name": on_bc_btn_name, "bc_btn_url": on_bc_btn_url}


def register_commands(app: Application) -> None:
    app.add_handler(CommandHandler("admin", cmd_admin, filters=ADMIN))
    app.add_handler(CommandHandler("cancel", cmd_cancel, filters=ADMIN))


def register_callbacks(app: Application) -> None:
    audiences = "|".join(kb.AUDIENCES)
    app.add_handlers([
        CallbackQueryHandler(cb_home, pattern=r"^adm:(home|cancel)$"),
        CallbackQueryHandler(cb_mode, pattern=r"^adm:mode$"),
        CallbackQueryHandler(cb_set_mode, pattern=r"^mode:(non|auto)$"),
        CallbackQueryHandler(cb_broadcast, pattern=r"^adm:bc$"),
        CallbackQueryHandler(cb_bc_audience, pattern=rf"^bc:({audiences})$"),
        CallbackQueryHandler(cb_bc_go, pattern=r"^bc:go$"),
        CallbackQueryHandler(cb_bc_menu, pattern=r"^bcb:menu$"),
        CallbackQueryHandler(cb_bc_add, pattern=r"^bcb:add$"),
        CallbackQueryHandler(cb_bc_done, pattern=r"^bcb:done$"),
        CallbackQueryHandler(cb_bc_edit, pattern=r"^bce:\d+$"),
        CallbackQueryHandler(cb_bc_field, pattern=r"^bce[nl]:\d+$"),
        CallbackQueryHandler(cb_bc_color_menu, pattern=r"^bcec:\d+$"),
        CallbackQueryHandler(cb_bc_delete, pattern=r"^bcd:\d+$"),
        CallbackQueryHandler(cb_bc_color, pattern=r"^bcc:[ne]:\d+:\w+$"),
        CallbackQueryHandler(cb_stats, pattern=r"^adm:stats$"),
        CallbackQueryHandler(cb_channels, pattern=r"^adm:channels$"),
    ])
