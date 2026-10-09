"""Offline tests for the bot (python-telegram-bot + aiogram for premium emoji / coloured buttons)."""
import pathlib
import py_compile
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, InlineKeyboardMarkup, Message, MessageEntity, PhotoSize, User
from telegram.error import BadRequest

BOT_DIR = pathlib.Path(__file__).resolve().parent.parent
FILES = [
    "bot.py", "database.py", "utils.py", "keyboards.py", "config.py",
    "handlers/admin.py", "handlers/join.py", "handlers/user.py", "handlers/wlc.py",
]


def _msg(text=None, entities=(), caption=None, caption_entities=(), photo=()):
    return Message(
        1, datetime.now(timezone.utc), Chat(111, "private"), from_user=User(111, "Bob", False),
        text=text, entities=entities, caption=caption, caption_entities=caption_entities, photo=photo,
    )


@pytest.mark.parametrize("path", FILES)
def test_py_compile(path):
    py_compile.compile(str(BOT_DIR / path), doraise=True)


def test_no_aiogram_runtime():
    # aiogram is only used for premium emoji + coloured buttons
    for path in ["bot.py", "handlers/admin.py", "handlers/join.py", "handlers/user.py", "handlers/wlc.py"]:
        assert "aiogram" not in (BOT_DIR / path).read_text(encoding="utf-8"), path


def test_bot_states_registered():
    import bot
    assert set(bot.STATES) == {"bc_wait", "bc_btn_name", "bc_btn_url", "wlc_text", "wlc_media", "btn_name", "btn_url"}


def test_keyboards_coloured_and_converted():
    import keyboards as kb
    from utils import to_ptb
    markup = to_ptb(kb.main_menu())
    assert isinstance(markup, InlineKeyboardMarkup)
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert all(b.style in {"primary", "success", "danger"} for b in buttons)
    assert any(b.callback_data == "adm:wlc" for b in buttons)
    assert to_ptb(kb.back_menu()).inline_keyboard[0][0].style == "danger"


def test_mode_menu_highlights_current():
    import keyboards as kb
    row = kb.mode_menu("auto").inline_keyboard[0]
    assert [b.style for b in row] == ["primary", "success"]


def test_html_text_keeps_premium_emoji():
    from utils import count_entities, html_text
    ent = MessageEntity("custom_emoji", 0, 2, custom_emoji_id="555")
    bold = MessageEntity("bold", 3, 2)
    m = _msg(text="🔥 Hi", entities=(ent, bold))
    assert html_text(m) == '<tg-emoji emoji-id="555">🔥</tg-emoji> <b>Hi</b>'
    assert count_entities(m) == 1


def test_html_text_caption():
    from utils import html_text
    m = _msg(caption="Cap", caption_entities=(MessageEntity("italic", 0, 3),))
    assert html_text(m) == "<i>Cap</i>"


def test_parse_button_name_premium_icon():
    from utils import parse_button_name
    m = _msg(text="🔥 Join", entities=(MessageEntity("custom_emoji", 0, 2, custom_emoji_id="9"),))
    assert parse_button_name(m) == ("Join", "9", "🔥")
    assert parse_button_name(_msg(text=" Plain ")) == ("Plain", None, None)


def test_extract_media_photo():
    from utils import extract_media
    m = _msg(photo=(PhotoSize("a", "ua", 1, 1), PhotoSize("b", "ub", 2, 2)))
    assert extract_media(m) == {"type": "photo", "file_id": "b"}
    assert extract_media(_msg(text="x")) is None


def test_custom_buttons_keep_style_and_icon():
    from utils import build_markup, strip_icons, to_ptb
    btns = [{"text": "A", "alt": None, "icon": "111", "url": "https://t.me/x", "style": "success"}]
    b = to_ptb(build_markup(btns)).inline_keyboard[0][0]
    assert (b.style, b.icon_custom_emoji_id, b.url) == ("success", "111", "https://t.me/x")
    assert to_ptb(strip_icons(build_markup(btns))).inline_keyboard[0][0].icon_custom_emoji_id is None
    assert build_markup([]) is None


def test_render_and_strip():
    from utils import count_tg_emoji, render, strip_tg_emoji
    assert render("Hi {first_name} {username} {channel}", User(1, "<B>", False), "C&") == "Hi &lt;B&gt; &lt;B&gt; C&amp;"
    t = '<tg-emoji emoji-id="1">🔥</tg-emoji> x'
    assert count_tg_emoji(t) == 1 and strip_tg_emoji(t) == "🔥 x"


async def test_send_custom_text_with_buttons():
    from utils import send_custom
    bot = AsyncMock()
    cfg = {"text": "Hi {first_name}", "media": None,
           "buttons": [{"text": "Go", "alt": None, "icon": None, "url": "https://t.me/x", "style": "primary"}]}
    await send_custom(bot, 5, cfg, User(1, "Bob", False), "")
    args, kwargs = bot.send_message.call_args
    assert args == (5, "Hi Bob")
    assert kwargs["reply_markup"].inline_keyboard[0][0].style == "primary"


async def test_send_custom_photo_and_fallback():
    from utils import send_custom
    bot = AsyncMock()
    bot.send_photo.side_effect = [BadRequest("custom emoji"), None]
    cfg = {"text": '<tg-emoji emoji-id="1">🔥</tg-emoji> Hi', "media": {"type": "photo", "file_id": "F"},
           "buttons": [{"text": "Go", "alt": "⭐", "icon": "7", "url": "https://t.me/x", "style": None}]}
    await send_custom(bot, 5, cfg, User(1, "Bob", False), "")
    args, kwargs = bot.send_photo.call_args
    assert args == (5, "F") and kwargs["caption"] == "🔥 Hi"
    assert kwargs["reply_markup"].inline_keyboard[0][0].text == "⭐ Go"


@pytest.mark.parametrize("raw,expected", [
    ("t.me/abc", "https://t.me/abc"),
    ("https://example.com", "https://example.com"),
    ("tg://resolve?domain=x", "tg://resolve?domain=x"),
    ("not a link", None),
])
def test_normalize_url(raw, expected):
    from utils import normalize_url
    assert normalize_url(raw) == expected


async def test_member_join_and_leave(monkeypatch):
    from telegram import ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, Update

    import handlers.join as j
    calls = []
    monkeypatch.setattr(j.db, "set_member", AsyncMock(side_effect=lambda *a: calls.append(("member", *a))))
    monkeypatch.setattr(j.db, "approve_request", AsyncMock(side_effect=lambda *a: calls.append(("approve", *a))))
    user, chat = User(5, "Bob", False), Chat(-100, "channel")
    left, member = ChatMemberLeft(user), ChatMemberMember(user)

    def upd(old, new):
        return Update(1, chat_member=ChatMemberUpdated(chat, user, datetime.now(timezone.utc), old, new))

    await j.on_member(upd(left, member), None)
    await j.on_member(upd(member, left), None)
    assert calls == [("member", 5, -100, "joined"), ("approve", 5, -100), ("member", 5, -100, "left")]


def test_broadcast_menus():
    import keyboards as kb
    rows = kb.broadcast_menu({"joined": 3, "pending": 2, "leaved": 1, "all": 6}).inline_keyboard
    texts = [b.text for row in rows for b in row]
    assert texts[:4] == ["🟢 Joined Users (3)", "⏳ Pending Users (2)", "🚪 Leaved Users (1)", "👥 All Users (6)"]
    assert [b.callback_data for row in rows for b in row][:4] == ["bc:joined", "bc:pending", "bc:leaved", "bc:all"]
    skip = [b.callback_data for row in kb.bc_buttons_menu(False).inline_keyboard for b in row]
    assert skip == ["bcb:add", "bcb:done", "adm:cancel"]
    done = [b.text for row in kb.bc_buttons_menu(True).inline_keyboard for b in row]
    assert "✅ Done" in done and "🗑 Remove Last" in done
    assert [b.callback_data for b in kb.color_menu("bcc", "bcb:menu").inline_keyboard[0]] == ["bcc:none", "bcc:primary"]


async def test_run_broadcast_with_buttons(monkeypatch):
    import utils
    monkeypatch.setattr(utils.db, "get_user_ids", AsyncMock(return_value=[1, 2]))
    monkeypatch.setattr(utils.asyncio, "sleep", AsyncMock())
    bot = AsyncMock()
    markup = utils.to_ptb(utils.build_markup([{"text": "Go", "url": "https://t.me/x", "style": "success"}]))
    await utils.run_broadcast(bot, 9, 9, 5, "joined", markup)
    assert bot.copy_message.call_count == 2
    assert bot.copy_message.call_args.kwargs["reply_markup"] is markup
    assert "Joined Users" in bot.send_message.call_args.args[1]
