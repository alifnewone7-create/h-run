"""End-to-end handler tests verifying premium-emoji position in button names.

Bug report: premium emoji on the RIGHT of the typed text ended up on the LEFT
of the inline button. parse_button_name should only convert the emoji to the
button's premium icon when it is at the START; otherwise the emoji stays in
the text exactly where the admin typed it.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import CallbackQuery, Chat, Message, MessageEntity, Update, User
from telegram.ext import Application


# ---------- helpers ----------
def _utf16_offset(prefix: str) -> int:
    return len(prefix.encode("utf-16-le")) // 2


def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _text_msg(text: str, entities=()):
    """Build a lightweight Message-like mock that handlers only read, and whose
    reply_text / set_bot can be mocked (PTB's real Message has __slots__)."""
    m = MagicMock()
    m.text = text
    m.caption = None
    m.entities = entities
    m.caption_entities = ()
    m.chat = Chat(111, "private")
    m.chat_id = 111
    m.message_id = 1
    m.from_user = User(111, "Admin", False, username="admin")
    m.reply_text = AsyncMock(return_value=m)
    m.edit_text = AsyncMock()
    m.get_bot = MagicMock(return_value=AsyncMock())
    m.photo = ()
    for kind in ("animation", "video", "audio", "voice", "document"):
        setattr(m, kind, None)
    return m


def _cb(data: str):
    cb = MagicMock()
    cb.data = data
    cb.from_user = User(111, "Admin", False)
    cb.message = _text_msg("placeholder")
    cb.answer = AsyncMock()
    return cb


def _ctx(user_data=None):
    bot = AsyncMock()
    return SimpleNamespace(user_data=user_data or {}, bot=bot, application=MagicMock())


def _build_app():
    import handlers.admin as admin_h
    import handlers.wlc as wlc_h
    app = Application.builder().token("1:x").build()
    wlc_h.register_callbacks(app)
    admin_h.register_callbacks(app)
    return app, wlc_h, admin_h


# ---------- utils-level: parse_button_name parametric ----------
@pytest.mark.parametrize("text,emoji,offset_prefix,expected", [
    # Right side -> emoji stays in text, no icon
    ("Join 🔥", "🔥", "Join ", ("Join 🔥", None, None)),
    # Middle -> stays, no icon
    ("Join 🔥 Now", "🔥", "Join ", ("Join 🔥 Now", None, None)),
    # Start without leading space -> becomes icon
    ("🔥 Join", "🔥", "", ("Join", "9", "🔥")),
    # Start with leading whitespace -> becomes icon, rest stripped
    (" 🔥 Join", "🔥", " ", ("Join", "9", "🔥")),
    # End only (no other text before) still right side -> stays
    ("Hello 🔥", "🔥", "Hello ", ("Hello 🔥", None, None)),
])
def test_parse_button_name_positions(text, emoji, offset_prefix, expected):
    from utils import parse_button_name
    off = _utf16_offset(offset_prefix)
    ln = _utf16_len(emoji)
    ent = MessageEntity("custom_emoji", off, ln, custom_emoji_id="9")
    m = _text_msg(text, entities=(ent,))
    assert parse_button_name(m) == expected


def test_parse_button_name_no_custom_emoji():
    from utils import parse_button_name
    assert parse_button_name(_text_msg("  Join 🔥  ")) == ("Join 🔥", None, None)
    assert parse_button_name(_text_msg("Plain")) == ("Plain", None, None)


# ---------- wlc add-button flow ----------
@pytest.mark.asyncio
async def test_wlc_add_button_right_side_emoji_stays_in_text(monkeypatch):
    import handlers.wlc as wlc_h
    app, _, _ = _build_app()

    stored = {"start": {"text": "", "media": None, "buttons": [], "layout": 1}}

    async def fake_get(kind):
        return stored[kind]

    async def fake_set(kind, cfg):
        stored[kind] = cfg

    monkeypatch.setattr(wlc_h.db, "get_msg", fake_get)
    monkeypatch.setattr(wlc_h.db, "set_msg", fake_set)

    ctx = _ctx()
    # Step 1: click "Add button" -> enters btn_name state
    cb = _cb("bta:start")
    upd = MagicMock(); upd.callback_query = cb; upd.effective_user = cb.from_user; upd.effective_message = cb.message
    await wlc_h.cb_button_add(upd, ctx)
    assert ctx.user_data["state"] == "btn_name"
    assert ctx.user_data["kind"] == "start"
    assert ctx.user_data["idx"] is None

    # Step 2: send the name with premium emoji on the RIGHT
    off = _utf16_offset("Join ")
    ln = _utf16_len("🔥")
    ent = MessageEntity("custom_emoji", off, ln, custom_emoji_id="99")
    name_msg = _text_msg("Join 🔥", entities=(ent,))
    await wlc_h.on_button_name(Update(2, message=name_msg), ctx)
    assert ctx.user_data["state"] == "btn_url"
    assert ctx.user_data["name"] == "Join 🔥"
    assert ctx.user_data["icon"] is None
    assert ctx.user_data["alt"] is None

    # Step 3: send url
    url_msg = _text_msg("https://t.me/x")
    await wlc_h.on_button_url(Update(3, message=url_msg), ctx)

    assert len(stored["start"]["buttons"]) == 1
    saved = stored["start"]["buttons"][0]
    assert saved["text"] == "Join 🔥"
    assert saved["icon"] is None
    assert saved["alt"] is None
    assert saved["url"] == "https://t.me/x"


@pytest.mark.asyncio
async def test_wlc_add_button_leading_emoji_becomes_icon(monkeypatch):
    import handlers.wlc as wlc_h
    stored = {"start": {"text": "", "media": None, "buttons": [], "layout": 1}}
    monkeypatch.setattr(wlc_h.db, "get_msg", lambda k: _async_val(stored[k]))
    monkeypatch.setattr(wlc_h.db, "set_msg", lambda k, v: _async_val(stored.__setitem__(k, v)))

    ctx = _ctx(user_data={"state": "btn_name", "kind": "start", "idx": None})
    off = 0
    ln = _utf16_len("🔥")
    ent = MessageEntity("custom_emoji", off, ln, custom_emoji_id="99")
    name_msg = _text_msg("🔥 Join", entities=(ent,))
    await wlc_h.on_button_name(Update(2, message=name_msg), ctx)
    assert ctx.user_data["name"] == "Join"
    assert ctx.user_data["icon"] == "99"
    assert ctx.user_data["alt"] == "🔥"

    url_msg = _text_msg("https://t.me/x")
    await wlc_h.on_button_url(Update(3, message=url_msg), ctx)
    saved = stored["start"]["buttons"][0]
    assert saved["text"] == "Join"
    assert saved["icon"] == "99"
    assert saved["alt"] == "🔥"


def _async_val(v):
    async def _c():
        return v
    return _c()


# ---------- wlc edit-name flow ----------
@pytest.mark.asyncio
async def test_wlc_edit_name_right_side_emoji_stays(monkeypatch):
    import handlers.wlc as wlc_h
    stored = {"start": {"text": "", "media": None, "layout": 1,
                        "buttons": [{"text": "Old", "icon": "1", "alt": "⭐",
                                     "url": "https://t.me/x", "style": None}]}}
    monkeypatch.setattr(wlc_h.db, "get_msg", lambda k: _async_val(stored[k]))

    async def fake_set(k, v):
        stored[k] = v

    monkeypatch.setattr(wlc_h.db, "set_msg", fake_set)

    ctx = _ctx(user_data={"state": "btn_name", "kind": "start", "idx": 0})
    off = _utf16_offset("Join ")
    ln = _utf16_len("🔥")
    ent = MessageEntity("custom_emoji", off, ln, custom_emoji_id="99")
    name_msg = _text_msg("Join 🔥", entities=(ent,))
    await wlc_h.on_button_name(Update(2, message=name_msg), ctx)
    b = stored["start"]["buttons"][0]
    assert (b["text"], b["icon"], b["alt"]) == ("Join 🔥", None, None)


# ---------- broadcast add-button flow ----------
@pytest.mark.asyncio
async def test_broadcast_add_button_right_side_emoji_stays(monkeypatch):
    import handlers.admin as admin_h
    monkeypatch.setattr(admin_h.db, "count_users", AsyncMock(return_value=0))

    ctx = _ctx(user_data={
        "state": "bc_btn_name", "idx": None, "audience": "all",
        "from_chat": 1, "msg_id": 2, "emoji": 0, "buttons": [], "layout": 1,
    })

    off = _utf16_offset("Join ")
    ln = _utf16_len("🔥")
    ent = MessageEntity("custom_emoji", off, ln, custom_emoji_id="99")
    name_msg = _text_msg("Join 🔥", entities=(ent,))

    await admin_h.on_bc_btn_name(Update(1, message=name_msg), ctx)
    assert ctx.user_data["state"] == "bc_btn_url"
    btn = ctx.user_data["btn"]
    assert btn["text"] == "Join 🔥"
    assert btn["icon"] is None
    assert btn["alt"] is None

    url_msg = _text_msg("https://t.me/x")
    await admin_h.on_bc_btn_url(Update(2, message=url_msg), ctx)
    assert len(ctx.user_data["buttons"]) == 1
    final = ctx.user_data["buttons"][0]
    assert final["text"] == "Join 🔥"
    assert final["icon"] is None
    assert final["url"] == "https://t.me/x"


@pytest.mark.asyncio
async def test_broadcast_add_button_leading_emoji_becomes_icon(monkeypatch):
    import handlers.admin as admin_h
    monkeypatch.setattr(admin_h.db, "count_users", AsyncMock(return_value=0))

    ctx = _ctx(user_data={
        "state": "bc_btn_name", "idx": None, "audience": "all",
        "from_chat": 1, "msg_id": 2, "emoji": 1, "buttons": [], "layout": 1,
    })
    ent = MessageEntity("custom_emoji", 0, _utf16_len("🔥"), custom_emoji_id="99")
    name_msg = _text_msg("🔥 Join", entities=(ent,))
    await admin_h.on_bc_btn_name(Update(1, message=name_msg), ctx)
    btn = ctx.user_data["btn"]
    assert btn["text"] == "Join"
    assert btn["icon"] == "99"
    assert btn["alt"] == "🔥"


# ---------- broadcast preview reply_markup ----------
@pytest.mark.asyncio
async def test_broadcast_preview_markup_has_emoji_in_button_text(monkeypatch):
    """After the full bc add flow with right-side emoji, cb_bc_done should
    build a reply_markup where the button text contains '🔥' and icon is None."""
    import handlers.admin as admin_h
    from utils import build_markup, to_ptb

    buttons = [{"text": "Join 🔥", "icon": None, "alt": None,
                "url": "https://t.me/x", "style": "primary"}]
    markup = to_ptb(build_markup(buttons, per_row=1))
    inline_btn = markup.inline_keyboard[0][0]
    assert inline_btn.text == "Join 🔥"
    assert inline_btn.icon_custom_emoji_id is None

    # Leading emoji variant: icon_custom_emoji_id set, text is bare word
    buttons2 = [{"text": "Join", "icon": "99", "alt": "🔥",
                 "url": "https://t.me/x", "style": "primary"}]
    markup2 = to_ptb(build_markup(buttons2, per_row=1))
    inline_btn2 = markup2.inline_keyboard[0][0]
    assert inline_btn2.text == "Join"
    assert inline_btn2.icon_custom_emoji_id == "99"


# ---------- bot.on_state dispatch ----------
@pytest.mark.asyncio
async def test_on_state_dispatches_btn_name(monkeypatch):
    import bot as bot_mod
    called = {}

    async def fake_on_button_name(update, ctx):
        called["wlc"] = True

    async def fake_on_bc_btn_name(update, ctx):
        called["bc"] = True

    monkeypatch.setitem(bot_mod.STATES, "btn_name", fake_on_button_name)
    monkeypatch.setitem(bot_mod.STATES, "bc_btn_name", fake_on_bc_btn_name)

    ctx = _ctx(user_data={"state": "btn_name"})
    await bot_mod.on_state(Update(1, message=_text_msg("x")), ctx)
    assert called.get("wlc") is True

    ctx2 = _ctx(user_data={"state": "bc_btn_name"})
    await bot_mod.on_state(Update(2, message=_text_msg("x")), ctx2)
    assert called.get("bc") is True
