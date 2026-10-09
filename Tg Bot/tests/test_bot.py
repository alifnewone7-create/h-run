"""Backend tests for Telegram bot (aiogram 3 + Neon Postgres).

Covers:
- Code compiles / handlers import
- database.py functions against Neon DB
- Handler logic via Dispatcher.feed_update with a mocked Bot session (no real Telegram calls)
- utils.render, send_custom (text + media + caption-overflow + fallback), run_broadcast
- New Wlc Setting flows (start / welcome): text, media, buttons (add/edit/color/delete),
  preview, reset, URL normalization, out-of-range handling, legacy welcome_text migration
"""
import asyncio
import json
import os
import pathlib
import py_compile

import pytest
import pytest_asyncio

# --- Compile check (collection-time) ---------------------------------------
BOT_DIR = pathlib.Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "path",
    [
        "bot.py", "database.py", "utils.py", "keyboards.py", "config.py",
        "handlers/admin.py", "handlers/join.py", "handlers/user.py", "handlers/wlc.py",
    ],
)
def test_py_compile(path):
    py_compile.compile(str(BOT_DIR / path), doraise=True)


def test_handlers_import():
    import handlers.admin as a
    import handlers.join as j
    import handlers.user as u
    import handlers.wlc as w
    assert a.router.name == "admin"
    assert j.router.name == "join"
    assert u.router.name == "user"
    assert w.router.name == "wlc"


# --- Mocked aiogram session ------------------------------------------------
from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.methods import (  # noqa: E402
    AnswerCallbackQuery,
    ApproveChatJoinRequest,
    CopyMessage,
    EditMessageText,
    SendAnimation,
    SendAudio,
    SendDocument,
    SendMessage,
    SendPhoto,
    SendVideo,
    SendVoice,
)
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    Chat,
    ChatJoinRequest,
    Message,
    MessageEntity,
    MessageId,
    PhotoSize,
    Update,
    User,
    Video,
    Audio,
    Animation,
    Document,
    Voice,
)


class MockedSession(BaseSession):
    """Records every API call; returns plausible objects, raises on demand."""

    def __init__(self):
        super().__init__()
        self.requests = []
        self.raise_forbidden_for = set()
        self.bad_request_on_tg_emoji = False  # for SendMessage retry test
        self._msg_counter = 1000

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):  # pragma: no cover
        if False:
            yield b""

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)

        if isinstance(method, SendMessage):
            if method.chat_id in self.raise_forbidden_for:
                raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
            if self.bad_request_on_tg_emoji and "<tg-emoji" in (method.text or ""):
                raise TelegramBadRequest(method=method, message="Bad Request: CUSTOM_EMOJI_INVALID")
            self._msg_counter += 1
            msg = Message(
                message_id=self._msg_counter,
                date=0,
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
                from_user=User(id=bot.id, is_bot=True, first_name="Bot"),
            )
            return msg.as_(bot)
        if isinstance(method, (SendPhoto, SendVideo, SendAudio, SendAnimation, SendDocument, SendVoice)):
            if self.bad_request_on_tg_emoji and "<tg-emoji" in (method.caption or ""):
                raise TelegramBadRequest(method=method, message="Bad Request: CUSTOM_EMOJI_INVALID")
            self._msg_counter += 1
            return MessageId(message_id=self._msg_counter)
        if isinstance(method, EditMessageText):
            return True
        if isinstance(method, AnswerCallbackQuery):
            return True
        if isinstance(method, ApproveChatJoinRequest):
            return True
        if isinstance(method, CopyMessage):
            if method.chat_id in self.raise_forbidden_for:
                raise TelegramForbiddenError(method=method, message="Forbidden")
            self._msg_counter += 1
            return MessageId(message_id=self._msg_counter)
        return True

    def calls_of(self, *cls):
        return [m for m in self.requests if isinstance(m, cls)]


@pytest_asyncio.fixture
async def mocked_bot():
    session = MockedSession()
    bot = Bot(
        "123456:TESTTOKEN",
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    bot._me = User(id=777777, is_bot=True, first_name="MockBot", username="mockbot")
    yield bot, session
    await bot.session.close()


@pytest_asyncio.fixture(scope="session")
async def dp():
    import handlers.admin as admin_h
    import handlers.join as join_h
    import handlers.user as user_h
    import handlers.wlc as wlc_h

    d = Dispatcher(storage=MemoryStorage())
    # Order: admin -> wlc -> join -> user (matches bot.py)
    d.include_routers(admin_h.router, wlc_h.router, join_h.router, user_h.router)
    return d


# --- Database connection ----------------------------------------------------
import database as db  # noqa: E402
from config import ADMIN_IDS, DATABASE_URL  # noqa: E402

TEST_USER_IDS = [999000001, 999000002, 999000003]
TEST_CHAT_ID = -1009990001001
NON_ADMIN_ID = 111222333
ADMIN_ID = next(iter(ADMIN_IDS))

# Keys this suite is allowed to touch; everything else is snapshotted & restored.
_WLC_KEYS = ("msg:start", "msg:welcome")
_TEST_SETTING_KEYS = _WLC_KEYS + ("TEST_setting_key_999",)


async def _cleanup_rows():
    assert db.pool is not None
    uids = TEST_USER_IDS + [NON_ADMIN_ID, 999000777, 999000888]
    await db.pool.execute("DELETE FROM users WHERE user_id = ANY($1::bigint[])", uids)
    await db.pool.execute("DELETE FROM join_requests WHERE user_id = ANY($1::bigint[])", uids)
    await db.pool.execute("DELETE FROM channels WHERE chat_id = $1", TEST_CHAT_ID)


async def _delete_test_settings():
    """Delete only settings this suite could have written."""
    await db.pool.execute(
        "DELETE FROM settings WHERE key = ANY($1::text[])", list(_TEST_SETTING_KEYS)
    )


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _db_connect():
    await db.connect(DATABASE_URL)

    # Full snapshot of ALL settings so we can restore the user's live DB verbatim.
    snapshot = await db.pool.fetch("SELECT key, value FROM settings")
    snapshot_map = {r["key"]: r["value"] for r in snapshot}

    await _cleanup_rows()
    # Ensure clean slate for test keys (they will be restored or deleted at teardown).
    await _delete_test_settings()

    yield

    await _cleanup_rows()
    # Delete any settings we touched that were NOT in the original snapshot
    await db.pool.execute(
        "DELETE FROM settings WHERE key = ANY($1::text[]) AND key <> ALL($2::text[])",
        list(_TEST_SETTING_KEYS),
        list(snapshot_map.keys()),
    )
    # Restore original values for keys we may have overwritten
    for k, v in snapshot_map.items():
        await db.set_setting(k, v)
    await db.close()


@pytest_asyncio.fixture(autouse=True)
async def _per_test_cleanup():
    yield
    await _cleanup_rows()
    # Clear msg:start / msg:welcome between tests to avoid cross-test leakage
    await _delete_test_settings()


# ===========================================================================
# Database tests
# ===========================================================================
class TestDatabase:
    @pytest.mark.asyncio
    async def test_schema_exists(self):
        rows = await db.pool.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "AND tablename IN ('users','join_requests','channels','settings')"
        )
        assert {r["tablename"] for r in rows} == {"users", "join_requests", "channels", "settings"}

    @pytest.mark.asyncio
    async def test_upsert_user_started_and_block_logic(self):
        uid = TEST_USER_IDS[0]
        await db.upsert_user(uid, "Alice", "alice", started=False)
        row = await db.pool.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)
        assert row["first_name"] == "Alice"
        assert row["started"] is False
        assert row["is_blocked"] is False

        await db.set_blocked(uid, True)
        await db.upsert_user(uid, "Alice2", "alice2", started=False)
        row = await db.pool.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)
        assert row["is_blocked"] is True
        assert row["started"] is False

        await db.upsert_user(uid, "Alice3", "alice3", started=True)
        row = await db.pool.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)
        assert row["is_blocked"] is False
        assert row["started"] is True

        await db.upsert_user(uid, "Alice3", "alice3", started=False)
        row = await db.pool.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)
        assert row["started"] is True

    @pytest.mark.asyncio
    async def test_add_request_and_counts(self):
        for uid in TEST_USER_IDS:
            await db.upsert_user(uid, f"U{uid}", None, started=False)
        await db.add_request(TEST_USER_IDS[0], TEST_CHAT_ID, "approved")
        await db.add_request(TEST_USER_IDS[1], TEST_CHAT_ID, "pending")

        n = await db.count_users("all")
        assert n >= 3
        ids_all = await db.get_user_ids("all")
        for uid in TEST_USER_IDS:
            assert uid in ids_all

        await db.set_blocked(TEST_USER_IDS[0], True)
        ids_all2 = await db.get_user_ids("all")
        assert TEST_USER_IDS[0] not in ids_all2

        ids_24h = await db.get_user_ids("24h")
        assert TEST_USER_IDS[1] in ids_24h

    @pytest.mark.asyncio
    async def test_upsert_channel_and_list(self):
        await db.upsert_channel(TEST_CHAT_ID, "Test Channel", True)
        chans = await db.list_channels()
        assert any(c["chat_id"] == TEST_CHAT_ID and c["title"] == "Test Channel" for c in chans)

        await db.upsert_channel(TEST_CHAT_ID, "Test Channel", False)
        chans = await db.list_channels()
        assert not any(c["chat_id"] == TEST_CHAT_ID for c in chans)

    @pytest.mark.asyncio
    async def test_settings_default_and_set(self):
        key = "TEST_setting_key_999"
        val = await db.get_setting(key, "DEFAULT_X")
        assert val == "DEFAULT_X"
        await db.set_setting(key, "hello")
        assert await db.get_setting(key, "DEFAULT_X") == "hello"
        await db.pool.execute("DELETE FROM settings WHERE key=$1", key)

    @pytest.mark.asyncio
    async def test_get_stats_shape(self):
        s = await db.get_stats()
        expected = {
            "new_24h", "new_7d", "new_30d", "new_all",
            "act_24h", "act_7d", "act_30d", "act_all",
            "req_24h", "req_7d", "req_30d", "req_all",
            "approved", "pending", "started", "blocked", "channels",
        }
        assert expected.issubset(set(s.keys()))
        for k in expected:
            assert isinstance(s[k], int), f"{k} is not int: {type(s[k])}"


# ===========================================================================
# database.get_msg / set_msg / reset_msg + migration
# ===========================================================================
class TestMsgStorage:
    @pytest.mark.asyncio
    async def test_default_start_msg(self):
        await _delete_test_settings()
        cfg = await db.get_msg("start")
        assert cfg["text"] == db.DEFAULT_MSGS["start"]
        assert cfg["media"] is None
        assert cfg["buttons"] == []

    @pytest.mark.asyncio
    async def test_welcome_legacy_migration(self):
        """If msg:welcome is missing, legacy welcome_text must be returned."""
        await _delete_test_settings()
        legacy = "LEGACY <b>{first_name}</b> hi"
        await db.set_setting("welcome_text", legacy)
        try:
            cfg = await db.get_msg("welcome")
            assert cfg["text"] == legacy
            assert cfg["media"] is None
            assert cfg["buttons"] == []
        finally:
            await db.pool.execute("DELETE FROM settings WHERE key='welcome_text'")

    @pytest.mark.asyncio
    async def test_set_and_get_msg_roundtrip(self):
        cfg = {
            "text": 'Hi <tg-emoji emoji-id="42">🔥</tg-emoji>',
            "media": {"type": "photo", "file_id": "abc"},
            "buttons": [{"text": "Go", "icon": None, "alt": None, "url": "https://t.me/x", "style": "primary"}],
        }
        await db.set_msg("start", cfg)
        got = await db.get_msg("start")
        assert got == cfg

    @pytest.mark.asyncio
    async def test_reset_welcome_deletes_legacy(self):
        await db.set_setting("welcome_text", "LEGACY VALUE")
        await db.set_msg("welcome", {"text": "x", "media": None, "buttons": []})
        await db.reset_msg("welcome")
        # Both legacy and new key must be gone -> get_msg returns DEFAULT
        row = await db.pool.fetchrow(
            "SELECT value FROM settings WHERE key IN ('welcome_text','msg:welcome')"
        )
        assert row is None
        cfg = await db.get_msg("welcome")
        assert cfg["text"] == db.DEFAULT_MSGS["welcome"]


# ===========================================================================
# utils tests
# ===========================================================================
class TestUtils:
    def _u(self, uid=1, first="<Alice>", username="al"):
        return User(id=uid, is_bot=False, first_name=first, username=username)

    def test_render_escapes_and_replaces(self):
        from utils import render
        out = render("Hi {first_name} / {username} / {channel}",
                     self._u(first="<Al>", username="bob"), "<Chan>")
        assert "&lt;Al&gt;" in out
        assert "&lt;Chan&gt;" in out
        assert "@bob" in out

    def test_render_no_username_falls_back_to_first(self):
        from utils import render
        out = render("{username}", self._u(first="Zoe", username=None), "c")
        assert out == "Zoe"

    def test_count_and_strip_tg_emoji(self):
        from utils import count_tg_emoji, strip_tg_emoji
        t = 'Hi <tg-emoji emoji-id="1">🔥</tg-emoji> and <tg-emoji emoji-id="2">⭐</tg-emoji>!'
        assert count_tg_emoji(t) == 2
        assert strip_tg_emoji(t) == "Hi 🔥 and ⭐!"

    def test_count_entities_counts_custom_emoji(self):
        from utils import count_entities
        ents = [
            MessageEntity(type="custom_emoji", offset=0, length=1, custom_emoji_id="1"),
            MessageEntity(type="bold", offset=1, length=2),
            MessageEntity(type="custom_emoji", offset=3, length=1, custom_emoji_id="2"),
        ]
        m = Message(message_id=1, date=0, chat=Chat(id=1, type="private"), text="x" * 10, entities=ents)
        assert count_entities(m) == 2

    def test_parse_button_name_plain(self):
        from utils import parse_button_name
        m = Message(message_id=1, date=0, chat=Chat(id=1, type="private"),
                    from_user=User(id=1, is_bot=False, first_name="A"), text="  Hello  ")
        name, icon, alt = parse_button_name(m)
        assert name == "Hello"
        assert icon is None and alt is None

    def test_parse_button_name_strips_premium_icon(self):
        from utils import parse_button_name
        prefix = "🔥 Go"
        offset = 0
        length = len("🔥".encode("utf-16-le")) // 2
        m = Message(
            message_id=1, date=0, chat=Chat(id=1, type="private"),
            from_user=User(id=1, is_bot=False, first_name="A"),
            text=prefix,
            entities=[MessageEntity(type="custom_emoji", offset=offset, length=length, custom_emoji_id="555")],
        )
        name, icon, alt = parse_button_name(m)
        assert name == "Go"
        assert icon == "555"
        assert alt == "🔥"

    def test_parse_button_name_only_emoji_keeps_alt(self):
        from utils import parse_button_name
        length = len("🔥".encode("utf-16-le")) // 2
        m = Message(
            message_id=1, date=0, chat=Chat(id=1, type="private"),
            from_user=User(id=1, is_bot=False, first_name="A"),
            text="🔥",
            entities=[MessageEntity(type="custom_emoji", offset=0, length=length, custom_emoji_id="9")],
        )
        name, icon, alt = parse_button_name(m)
        assert name == "🔥"
        assert icon == "9"
        assert alt == "🔥"

    def test_extract_media_photo(self):
        from utils import extract_media
        m = Message(
            message_id=1, date=0, chat=Chat(id=1, type="private"),
            photo=[PhotoSize(file_id="smallid", file_unique_id="u1", width=10, height=10),
                   PhotoSize(file_id="bigid", file_unique_id="u2", width=100, height=100)],
        )
        assert extract_media(m) == {"type": "photo", "file_id": "bigid"}

    def test_extract_media_video(self):
        from utils import extract_media
        m = Message(
            message_id=1, date=0, chat=Chat(id=1, type="private"),
            video=Video(file_id="vid", file_unique_id="vu", width=1, height=1, duration=1),
        )
        assert extract_media(m) == {"type": "video", "file_id": "vid"}

    def test_extract_media_none(self):
        from utils import extract_media
        m = Message(message_id=1, date=0, chat=Chat(id=1, type="private"), text="hi")
        assert extract_media(m) is None

    def test_build_markup_and_strip_icons(self):
        from utils import build_markup, strip_icons
        btns = [{"text": "A", "alt": None, "icon": "111", "url": "https://t.me/x", "style": "primary"}]
        kb = build_markup(btns)
        assert kb.inline_keyboard[0][0].style == "primary"
        assert kb.inline_keyboard[0][0].icon_custom_emoji_id == "111"
        stripped = strip_icons(kb)
        assert stripped.inline_keyboard[0][0].icon_custom_emoji_id is None

    def test_build_markup_empty_returns_none(self):
        from utils import build_markup
        assert build_markup([]) is None


# ===========================================================================
# send_custom (delivery)
# ===========================================================================
class TestSendCustom:
    @pytest.mark.asyncio
    async def test_send_custom_text_only(self, mocked_bot):
        bot, session = mocked_bot
        from utils import send_custom
        cfg = {"text": "Hello", "media": None, "buttons": []}
        user = User(id=1, is_bot=False, first_name="Alice", username="al")
        await send_custom(bot, 999, cfg, user, "Chan")
        sends = session.calls_of(SendMessage)
        assert len(sends) == 1
        assert sends[0].text == "Hello"
        assert sends[0].reply_markup is None

    @pytest.mark.asyncio
    async def test_send_custom_with_buttons(self, mocked_bot):
        bot, session = mocked_bot
        from utils import send_custom
        cfg = {
            "text": "Hi {first_name}",
            "media": None,
            "buttons": [{"text": "Join", "alt": None, "icon": None, "url": "https://t.me/x", "style": "success"}],
        }
        user = User(id=1, is_bot=False, first_name="Bob", username="b")
        await send_custom(bot, 999, cfg, user, "Chan")
        sends = session.calls_of(SendMessage)
        assert "Bob" in sends[0].text
        mkup = sends[0].reply_markup
        assert mkup.inline_keyboard[0][0].style == "success"
        assert mkup.inline_keyboard[0][0].url == "https://t.me/x"

    @pytest.mark.asyncio
    async def test_send_custom_photo_with_caption(self, mocked_bot):
        bot, session = mocked_bot
        from utils import send_custom
        cfg = {"text": "Caption", "media": {"type": "photo", "file_id": "pid"}, "buttons": []}
        user = User(id=1, is_bot=False, first_name="A", username=None)
        await send_custom(bot, 999, cfg, user, "C")
        photos = session.calls_of(SendPhoto)
        assert len(photos) == 1
        assert photos[0].photo == "pid"
        assert photos[0].caption == "Caption"
        assert session.calls_of(SendMessage) == []

    @pytest.mark.asyncio
    async def test_send_custom_caption_over_limit_sends_separate_text(self, mocked_bot):
        bot, session = mocked_bot
        from utils import send_custom
        long_text = "x" * 1200  # > 1024
        cfg = {"text": long_text, "media": {"type": "video", "file_id": "vid"}, "buttons": []}
        user = User(id=1, is_bot=False, first_name="A", username=None)
        await send_custom(bot, 999, cfg, user, "C")
        videos = session.calls_of(SendVideo)
        texts = session.calls_of(SendMessage)
        assert len(videos) == 1
        assert videos[0].caption is None
        assert len(texts) == 1
        assert texts[0].text == long_text

    @pytest.mark.asyncio
    async def test_send_custom_retries_on_tg_emoji_bad_request(self, mocked_bot):
        bot, session = mocked_bot
        from utils import send_custom
        session.bad_request_on_tg_emoji = True
        cfg = {
            "text": 'Hi <tg-emoji emoji-id="5">🔥</tg-emoji>',
            "media": None,
            "buttons": [{"text": "A", "alt": "🔥", "icon": "5", "url": "https://t.me/x", "style": None}],
        }
        user = User(id=1, is_bot=False, first_name="A", username=None)
        await send_custom(bot, 999, cfg, user, "C")
        sends = session.calls_of(SendMessage)
        assert len(sends) == 2
        assert "<tg-emoji" in sends[0].text
        assert "<tg-emoji" not in sends[1].text
        # icons removed in fallback -> button text should be label (alt + text)
        kb2 = sends[1].reply_markup
        assert kb2.inline_keyboard[0][0].icon_custom_emoji_id is None


# ===========================================================================
# Handler helpers
# ===========================================================================
def _user(uid=NON_ADMIN_ID, first="Bob", username="bob"):
    return User(id=uid, is_bot=False, first_name=first, username=username)


def _private_chat(uid):
    return Chat(id=uid, type="private")


def _msg(uid, text, chat=None, mid=1, entities=None):
    return Message(
        message_id=mid, date=0, chat=chat or _private_chat(uid),
        from_user=_user(uid), text=text, entities=entities,
    )


def _callback(data, uid=ADMIN_ID, mid=100):
    msg = Message(
        message_id=mid, date=0, chat=_private_chat(uid),
        from_user=User(id=777777, is_bot=True, first_name="MockBot"),
        text="prev",
    )
    return CallbackQuery(id=f"cb-{data}", from_user=_user(uid), chat_instance="ci", data=data, message=msg)


# ===========================================================================
# Join handler
# ===========================================================================
class TestJoinHandler:
    @pytest.mark.asyncio
    async def test_join_auto_mode_sends_then_approves(self, mocked_bot, dp):
        bot, session = mocked_bot
        await db.set_setting("approve_mode", "auto")
        await db.set_msg("welcome", {
            "text": "Hi {first_name} welcome to {channel}", "media": None, "buttons": [],
        })

        uid = TEST_USER_IDS[0]
        req = ChatJoinRequest(
            chat=Chat(id=TEST_CHAT_ID, type="channel", title="MyChan"),
            from_user=_user(uid, first="Carol", username="carol"),
            user_chat_id=uid, date=0, bio=None, invite_link=None,
        )
        await dp.feed_update(bot, Update(update_id=1, chat_join_request=req))

        sends = session.calls_of(SendMessage)
        approves = session.calls_of(ApproveChatJoinRequest)
        assert len(sends) == 1
        assert "Carol" in sends[0].text and "MyChan" in sends[0].text
        assert len(approves) == 1
        assert session.requests.index(sends[0]) < session.requests.index(approves[0])

        row = await db.pool.fetchrow(
            "SELECT status FROM join_requests WHERE user_id=$1 ORDER BY id DESC LIMIT 1", uid
        )
        assert row["status"] == "approved"

    @pytest.mark.asyncio
    async def test_join_non_mode_no_approve(self, mocked_bot, dp):
        bot, session = mocked_bot
        await db.set_setting("approve_mode", "non")

        uid = TEST_USER_IDS[1]
        req = ChatJoinRequest(
            chat=Chat(id=TEST_CHAT_ID, type="channel", title="C2"),
            from_user=_user(uid, first="Dan", username=None),
            user_chat_id=uid, date=0, bio=None, invite_link=None,
        )
        await dp.feed_update(bot, Update(update_id=2, chat_join_request=req))

        assert len(session.calls_of(SendMessage)) == 1
        assert len(session.calls_of(ApproveChatJoinRequest)) == 0

        row = await db.pool.fetchrow(
            "SELECT status FROM join_requests WHERE user_id=$1 ORDER BY id DESC LIMIT 1", uid
        )
        assert row["status"] == "pending"

    @pytest.mark.asyncio
    async def test_join_uses_legacy_welcome_text(self, mocked_bot, dp):
        bot, session = mocked_bot
        await db.set_setting("approve_mode", "non")
        await _delete_test_settings()
        await db.set_setting("welcome_text", "Legacy hi {first_name}")
        try:
            uid = TEST_USER_IDS[2]
            req = ChatJoinRequest(
                chat=Chat(id=TEST_CHAT_ID, type="channel", title="LegacyChan"),
                from_user=_user(uid, first="Zoe", username="z"),
                user_chat_id=uid, date=0, bio=None, invite_link=None,
            )
            await dp.feed_update(bot, Update(update_id=3, chat_join_request=req))
            sends = session.calls_of(SendMessage)
            assert "Legacy hi Zoe" in sends[-1].text
        finally:
            await db.pool.execute("DELETE FROM settings WHERE key='welcome_text'")


# ===========================================================================
# Admin basics
# ===========================================================================
class TestAdminHandlers:
    @pytest.mark.asyncio
    async def test_non_admin_cannot_open_admin(self, mocked_bot, dp):
        bot, session = mocked_bot
        m = _msg(NON_ADMIN_ID, "/admin")
        await dp.feed_update(bot, Update(update_id=10, message=m))
        assert session.calls_of(SendMessage) == []

    @pytest.mark.asyncio
    async def test_admin_home_callback(self, mocked_bot, dp):
        bot, session = mocked_bot
        await dp.feed_update(bot, Update(update_id=11, callback_query=_callback("adm:home")))
        edits = session.calls_of(EditMessageText)
        assert edits and "Admin Panel" in edits[0].text

    @pytest.mark.asyncio
    async def test_main_menu_has_wlc_setting(self):
        import keyboards as kb
        rows = kb.main_menu().inline_keyboard
        found = False
        for row in rows:
            for b in row:
                if b.callback_data == "adm:wlc":
                    assert "Wlc Setting" in b.text
                    found = True
        assert found, "adm:wlc button missing in main_menu"
        # And no 'Text Set' leftover
        all_text = " ".join(b.text for row in rows for b in row)
        assert "Text Set" not in all_text

    @pytest.mark.asyncio
    async def test_mode_set_auto_then_non(self, mocked_bot, dp):
        bot, session = mocked_bot
        for mode_cb, expected in [("mode:auto", "auto"), ("mode:non", "non")]:
            session.requests.clear()
            await dp.feed_update(bot, Update(update_id=20, callback_query=_callback(mode_cb)))
            assert await db.get_setting("approve_mode", "non") == expected

    @pytest.mark.asyncio
    async def test_broadcast_flow_triggers_run_broadcast(self, mocked_bot, dp, monkeypatch):
        bot, session = mocked_bot
        uid = TEST_USER_IDS[2]
        await db.upsert_user(uid, "BC", "bc", started=True)
        called = {}

        async def fake_run(bot_, admin_chat, from_chat, msg_id, period):
            called["args"] = (admin_chat, from_chat, msg_id, period)

        import handlers.admin as admin_h
        monkeypatch.setattr(admin_h, "run_broadcast", fake_run)

        await dp.feed_update(bot, Update(update_id=50, callback_query=_callback("adm:bc")))
        await dp.feed_update(bot, Update(update_id=51, callback_query=_callback("bc:24h")))
        bc_msg = Message(message_id=200, date=0, chat=_private_chat(ADMIN_ID),
                         from_user=_user(ADMIN_ID), text="Broadcast content")
        await dp.feed_update(bot, Update(update_id=52, message=bc_msg))
        await dp.feed_update(bot, Update(update_id=53, callback_query=_callback("bc:go")))

        for _ in range(20):
            if called:
                break
            await asyncio.sleep(0.05)
        assert called, "run_broadcast was not invoked"
        _, from_chat, msg_id, period = called["args"]
        assert period == "24h"
        assert from_chat == ADMIN_ID
        assert msg_id == 200

    @pytest.mark.asyncio
    async def test_stats_callback(self, mocked_bot, dp):
        bot, session = mocked_bot
        await dp.feed_update(bot, Update(update_id=60, callback_query=_callback("adm:stats")))
        edits = session.calls_of(EditMessageText)
        assert edits and "Statistics" in edits[0].text

    @pytest.mark.asyncio
    async def test_channels_callback(self, mocked_bot, dp):
        bot, session = mocked_bot
        await db.upsert_channel(TEST_CHAT_ID, "ChTest", True)
        await dp.feed_update(bot, Update(update_id=61, callback_query=_callback("adm:channels")))
        edits = session.calls_of(EditMessageText)
        assert edits and "ChTest" in edits[0].text


# ===========================================================================
# User /start handler
# ===========================================================================
class TestUserHandler:
    @pytest.mark.asyncio
    async def test_start_sets_started_and_sends_start_msg(self, mocked_bot, dp):
        bot, session = mocked_bot
        uid = 999000777
        # Admin hint only triggers for ADMIN_IDS; test non-admin flow here
        m = _msg(uid, "/start", mid=1)
        # Register as non-admin to avoid admin hint path
        if uid in ADMIN_IDS:  # pragma: no cover
            pytest.skip("test uid collides with admin")
        await dp.feed_update(bot, Update(update_id=70, message=m))
        row = await db.pool.fetchrow("SELECT started FROM users WHERE user_id=$1", uid)
        assert row is not None and row["started"] is True
        sends = session.calls_of(SendMessage)
        assert sends, "/start should reply with Start Msg"
        # Should use configured start msg (default)
        assert "{first_name}" not in sends[0].text  # template was rendered

    @pytest.mark.asyncio
    async def test_start_for_admin_includes_admin_hint(self, mocked_bot, dp):
        bot, session = mocked_bot
        m = _msg(ADMIN_ID, "/start", mid=1)
        await dp.feed_update(bot, Update(update_id=71, message=m))
        sends = session.calls_of(SendMessage)
        # 2 messages: start_msg + "/admin" hint
        assert len(sends) >= 2
        assert any("/admin" in s.text for s in sends)


# ===========================================================================
# run_broadcast
# ===========================================================================
class TestRunBroadcast:
    @pytest.mark.asyncio
    async def test_run_broadcast_counts(self, mocked_bot):
        bot, session = mocked_bot
        from utils import run_broadcast
        for uid in TEST_USER_IDS:
            await db.upsert_user(uid, "BC", "bc", started=True)
            await db.set_blocked(uid, False)
        forbidden_uid = TEST_USER_IDS[0]
        session.raise_forbidden_for = {forbidden_uid}

        await run_broadcast(bot, admin_chat=ADMIN_ID, from_chat=ADMIN_ID, msg_id=1, period="all")

        copies = session.calls_of(CopyMessage)
        assert len(copies) >= 3
        row = await db.pool.fetchrow("SELECT is_blocked FROM users WHERE user_id=$1", forbidden_uid)
        assert row["is_blocked"] is True


# ===========================================================================
# Wlc Setting flows (NEW)
# ===========================================================================
class TestWlcMenu:
    @pytest.mark.asyncio
    async def test_adm_wlc_opens_wlc_menu(self, mocked_bot, dp):
        bot, session = mocked_bot
        await dp.feed_update(bot, Update(update_id=200, callback_query=_callback("adm:wlc")))
        edits = session.calls_of(EditMessageText)
        assert edits and "Wlc Setting" in edits[-1].text
        mkup = edits[-1].reply_markup
        all_data = {b.callback_data for row in mkup.inline_keyboard for b in row}
        assert "cfg:start" in all_data
        assert "cfg:welcome" in all_data

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind,label", [("start", "Start Msg"), ("welcome", "Wlc Msg")])
    async def test_cfg_kind_shows_summary(self, mocked_bot, dp, kind, label):
        bot, session = mocked_bot
        await dp.feed_update(bot, Update(update_id=210, callback_query=_callback(f"cfg:{kind}")))
        edits = session.calls_of(EditMessageText)
        assert edits
        body = edits[-1].text
        assert label in body
        assert "Text:" in body and "Media:" in body and "Buttons:" in body
        datas = {b.callback_data for row in edits[-1].reply_markup.inline_keyboard for b in row}
        assert {f"txt:{kind}", f"med:{kind}", f"btn:{kind}", f"prv:{kind}", f"rst:{kind}"}.issubset(datas)


class TestWlcText:
    @pytest.mark.asyncio
    async def test_text_flow_saves_tg_emoji_html(self, mocked_bot, dp):
        bot, session = mocked_bot
        kind = "start"
        await dp.feed_update(bot, Update(update_id=220, callback_query=_callback(f"txt:{kind}")))
        # Admin sends text with a custom emoji entity
        text = "Welcome 🔥 user"
        offset = len("Welcome ".encode("utf-16-le")) // 2
        length = len("🔥".encode("utf-16-le")) // 2
        m = Message(
            message_id=55, date=0, chat=_private_chat(ADMIN_ID),
            from_user=_user(ADMIN_ID, first="Admin", username="adm"),
            text=text,
            entities=[MessageEntity(type="custom_emoji", offset=offset, length=length, custom_emoji_id="999888")],
        )
        await dp.feed_update(bot, Update(update_id=221, message=m))

        raw = await db.pool.fetchval("SELECT value FROM settings WHERE key='msg:start'")
        assert raw, "msg:start was not saved"
        cfg = json.loads(raw)
        assert '<tg-emoji emoji-id="999888">' in cfg["text"]
        # Confirmation reply
        sends = session.calls_of(SendMessage)
        assert any("Text saved" in s.text for s in sends)

    @pytest.mark.asyncio
    async def test_text_non_text_message_rejected(self, mocked_bot, dp):
        bot, session = mocked_bot
        await dp.feed_update(bot, Update(update_id=230, callback_query=_callback("txt:welcome")))
        # Send a photo while in text state -> rejected
        m = Message(
            message_id=56, date=0, chat=_private_chat(ADMIN_ID),
            from_user=_user(ADMIN_ID), text=None,
            photo=[PhotoSize(file_id="x", file_unique_id="y", width=1, height=1)],
        )
        await dp.feed_update(bot, Update(update_id=231, message=m))
        sends = session.calls_of(SendMessage)
        assert any("send a text" in s.text.lower() for s in sends)


class TestWlcMedia:
    @pytest.mark.asyncio
    async def test_media_flow_saves_photo_with_caption(self, mocked_bot, dp):
        bot, session = mocked_bot
        kind = "welcome"
        await dp.feed_update(bot, Update(update_id=240, callback_query=_callback(f"med:{kind}")))
        m = Message(
            message_id=57, date=0, chat=_private_chat(ADMIN_ID),
            from_user=_user(ADMIN_ID),
            caption="New caption <b>bold</b>",
            photo=[PhotoSize(file_id="small", file_unique_id="s", width=1, height=1),
                   PhotoSize(file_id="big", file_unique_id="b", width=10, height=10)],
        )
        await dp.feed_update(bot, Update(update_id=241, message=m))

        cfg = await db.get_msg(kind)
        assert cfg["media"] == {"type": "photo", "file_id": "big"}
        # caption without entities -> html_text equals caption
        assert "New caption" in cfg["text"]

    @pytest.mark.asyncio
    async def test_media_remove(self, mocked_bot, dp):
        bot, session = mocked_bot
        kind = "welcome"
        await db.set_msg(kind, {"text": "T", "media": {"type": "video", "file_id": "v"}, "buttons": []})
        await dp.feed_update(bot, Update(update_id=250, callback_query=_callback(f"medr:{kind}")))
        cfg = await db.get_msg(kind)
        assert cfg["media"] is None

    @pytest.mark.asyncio
    async def test_media_non_media_message_rejected(self, mocked_bot, dp):
        bot, session = mocked_bot
        await dp.feed_update(bot, Update(update_id=260, callback_query=_callback("med:start")))
        m = _msg(ADMIN_ID, "just text", mid=58)
        await dp.feed_update(bot, Update(update_id=261, message=m))
        sends = session.calls_of(SendMessage)
        assert any("photo, video, audio" in s.text.lower() for s in sends)


class TestWlcButtons:
    @pytest.mark.asyncio
    async def test_add_button_full_flow(self, mocked_bot, dp):
        bot, session = mocked_bot
        kind = "start"
        await dp.feed_update(bot, Update(update_id=300, callback_query=_callback(f"bta:{kind}")))

        # Step 1: name with premium emoji prefix -> icon
        offset = 0
        length = len("🔥".encode("utf-16-le")) // 2
        name_msg = Message(
            message_id=60, date=0, chat=_private_chat(ADMIN_ID),
            from_user=_user(ADMIN_ID), text="🔥 Join",
            entities=[MessageEntity(type="custom_emoji", offset=offset, length=length, custom_emoji_id="icon-5")],
        )
        await dp.feed_update(bot, Update(update_id=301, message=name_msg))

        # Step 2: URL (t.me/x normalized)
        url_msg = _msg(ADMIN_ID, "t.me/foo", mid=61)
        await dp.feed_update(bot, Update(update_id=302, message=url_msg))

        cfg = await db.get_msg(kind)
        assert len(cfg["buttons"]) == 1
        b = cfg["buttons"][0]
        assert b["text"] == "Join"
        assert b["icon"] == "icon-5"
        assert b["alt"] == "🔥"
        assert b["url"] == "https://t.me/foo"
        assert b["style"] is None  # not chosen yet

        # Step 3: choose color 'success'
        await dp.feed_update(bot, Update(update_id=303, callback_query=_callback(f"btc:{kind}:0:success")))
        cfg = await db.get_msg(kind)
        assert cfg["buttons"][0]["style"] == "success"

    @pytest.mark.asyncio
    async def test_add_button_invalid_url_rejected(self, mocked_bot, dp):
        bot, session = mocked_bot
        kind = "welcome"
        await dp.feed_update(bot, Update(update_id=310, callback_query=_callback(f"bta:{kind}")))
        name_msg = _msg(ADMIN_ID, "Click", mid=62)
        await dp.feed_update(bot, Update(update_id=311, message=name_msg))
        bad_url = _msg(ADMIN_ID, "ftp://nope", mid=63)
        await dp.feed_update(bot, Update(update_id=312, message=bad_url))
        sends = session.calls_of(SendMessage)
        assert any("Invalid link" in s.text for s in sends)
        cfg = await db.get_msg(kind)
        assert cfg["buttons"] == []

    @pytest.mark.asyncio
    async def test_set_color_none_saves_null(self, mocked_bot, dp):
        bot, _ = mocked_bot
        kind = "start"
        await db.set_msg(kind, {"text": "T", "media": None, "buttons": [
            {"text": "A", "icon": None, "alt": None, "url": "https://t.me/x", "style": "danger"},
        ]})
        await dp.feed_update(bot, Update(update_id=320, callback_query=_callback(f"btc:{kind}:0:none")))
        cfg = await db.get_msg(kind)
        assert cfg["buttons"][0]["style"] is None

    @pytest.mark.asyncio
    async def test_edit_button_name(self, mocked_bot, dp):
        bot, _ = mocked_bot
        kind = "start"
        await db.set_msg(kind, {"text": "T", "media": None, "buttons": [
            {"text": "Old", "icon": None, "alt": None, "url": "https://t.me/x", "style": None},
        ]})
        await dp.feed_update(bot, Update(update_id=330, callback_query=_callback(f"bten:{kind}:0")))
        name_msg = _msg(ADMIN_ID, "NewName", mid=70)
        await dp.feed_update(bot, Update(update_id=331, message=name_msg))
        cfg = await db.get_msg(kind)
        assert cfg["buttons"][0]["text"] == "NewName"
        assert cfg["buttons"][0]["icon"] is None

    @pytest.mark.asyncio
    async def test_edit_button_url(self, mocked_bot, dp):
        bot, session = mocked_bot
        kind = "start"
        await db.set_msg(kind, {"text": "T", "media": None, "buttons": [
            {"text": "A", "icon": None, "alt": None, "url": "https://t.me/old", "style": None},
        ]})
        await dp.feed_update(bot, Update(update_id=340, callback_query=_callback(f"btel:{kind}:0")))
        # invalid first
        await dp.feed_update(bot, Update(update_id=341, message=_msg(ADMIN_ID, "nope", mid=71)))
        cfg = await db.get_msg(kind)
        assert cfg["buttons"][0]["url"] == "https://t.me/old"
        # valid second
        await dp.feed_update(bot, Update(update_id=342, message=_msg(ADMIN_ID, "https://example.com/x", mid=72)))
        cfg = await db.get_msg(kind)
        assert cfg["buttons"][0]["url"] == "https://example.com/x"

    @pytest.mark.asyncio
    async def test_delete_button(self, mocked_bot, dp):
        bot, _ = mocked_bot
        kind = "welcome"
        await db.set_msg(kind, {"text": "T", "media": None, "buttons": [
            {"text": "A", "icon": None, "alt": None, "url": "https://t.me/a", "style": None},
            {"text": "B", "icon": None, "alt": None, "url": "https://t.me/b", "style": None},
        ]})
        await dp.feed_update(bot, Update(update_id=350, callback_query=_callback(f"btd:{kind}:0")))
        cfg = await db.get_msg(kind)
        assert len(cfg["buttons"]) == 1
        assert cfg["buttons"][0]["text"] == "B"

    @pytest.mark.asyncio
    async def test_out_of_range_button_shows_alert(self, mocked_bot, dp):
        bot, session = mocked_bot
        kind = "start"
        await db.set_msg(kind, {"text": "T", "media": None, "buttons": []})
        # bte with i=5 -> no buttons
        await dp.feed_update(bot, Update(update_id=360, callback_query=_callback(f"bte:{kind}:5")))
        answers = session.calls_of(AnswerCallbackQuery)
        assert any(a.show_alert and "not found" in (a.text or "").lower() for a in answers)

        # btc out-of-range
        session.requests.clear()
        await dp.feed_update(bot, Update(update_id=361, callback_query=_callback(f"btc:{kind}:9:primary")))
        answers = session.calls_of(AnswerCallbackQuery)
        assert any(a.show_alert for a in answers)


class TestWlcPreview:
    @pytest.mark.asyncio
    async def test_preview_text_only(self, mocked_bot, dp):
        bot, session = mocked_bot
        kind = "start"
        await db.set_msg(kind, {
            "text": "Preview {first_name}",
            "media": None,
            "buttons": [{"text": "Go", "icon": None, "alt": None, "url": "https://t.me/x", "style": "primary"}],
        })
        await dp.feed_update(bot, Update(update_id=400, callback_query=_callback(f"prv:{kind}")))
        sends = session.calls_of(SendMessage)
        preview = [s for s in sends if "Preview" in s.text]
        assert preview, "preview message not sent"
        assert preview[0].reply_markup is not None
        assert preview[0].reply_markup.inline_keyboard[0][0].style == "primary"

    @pytest.mark.asyncio
    async def test_preview_photo_media(self, mocked_bot, dp):
        bot, session = mocked_bot
        kind = "welcome"
        await db.set_msg(kind, {
            "text": "Hi",
            "media": {"type": "photo", "file_id": "pfid"},
            "buttons": [],
        })
        await dp.feed_update(bot, Update(update_id=410, callback_query=_callback(f"prv:{kind}")))
        photos = session.calls_of(SendPhoto)
        assert photos and photos[0].photo == "pfid"
        assert photos[0].caption == "Hi"


class TestWlcReset:
    @pytest.mark.asyncio
    async def test_reset_start(self, mocked_bot, dp):
        bot, _ = mocked_bot
        await db.set_msg("start", {"text": "X", "media": None, "buttons": []})
        await dp.feed_update(bot, Update(update_id=420, callback_query=_callback("rst:start")))
        cfg = await db.get_msg("start")
        assert cfg["text"] == db.DEFAULT_MSGS["start"]

    @pytest.mark.asyncio
    async def test_reset_welcome_removes_legacy_too(self, mocked_bot, dp):
        bot, _ = mocked_bot
        await db.set_setting("welcome_text", "LEGACY")
        await db.set_msg("welcome", {"text": "X", "media": None, "buttons": []})
        try:
            await dp.feed_update(bot, Update(update_id=430, callback_query=_callback("rst:welcome")))
            cfg = await db.get_msg("welcome")
            assert cfg["text"] == db.DEFAULT_MSGS["welcome"]
            legacy = await db.pool.fetchval("SELECT value FROM settings WHERE key='welcome_text'")
            assert legacy is None
        finally:
            await db.pool.execute("DELETE FROM settings WHERE key='welcome_text'")


class TestWlcUrlNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("t.me/foo", "https://t.me/foo"),
        ("www.example.com/x", "https://www.example.com/x"),
        ("https://example.com", "https://example.com"),
        ("http://foo.bar/baz", "http://foo.bar/baz"),
        ("tg://resolve?domain=x", "tg://resolve?domain=x"),
    ])
    def test_normalize_valid(self, raw, expected):
        from handlers.wlc import normalize_url
        assert normalize_url(raw) == expected

    @pytest.mark.parametrize("raw", ["", "nope", "ftp://x", "ht tp://x", "javascript:alert(1)"])
    def test_normalize_invalid(self, raw):
        from handlers.wlc import normalize_url
        assert normalize_url(raw) is None


# ===========================================================================
# English-only language audit
# ===========================================================================
class TestEnglishOnlyLanguage:
    FILES = [
        "utils.py", "keyboards.py",
        "handlers/admin.py", "handlers/join.py", "handlers/user.py", "handlers/wlc.py",
        "database.py",
    ]
    BANGLISH = [
        "korun", "koro", "korbo", "korchi", "korbe",
        "pathan", "pathao", "pathate",
        "hobe", "hoye", "hocche",
        "likhun", "likho",
        "dekhun", "dekho",
        "chalu", "bondho",
        "boshai", "shob", "amader", "apnar",
        "rakhun", "rakho",
        "shuru", "shesh",
    ]

    def test_no_banglish_in_source(self):
        import re as _re
        offenders = []
        for rel in self.FILES:
            p = BOT_DIR / rel
            content = p.read_text(encoding="utf-8").lower()
            for word in self.BANGLISH:
                if _re.search(r"\b" + _re.escape(word) + r"\b", content):
                    offenders.append(f"{rel}: '{word}'")
        assert not offenders, "Banglish words found: " + "; ".join(offenders)

    def test_default_text_is_english(self):
        text = db.DEFAULT_TEXT.lower()
        for word in self.BANGLISH:
            assert word not in text, f"DEFAULT_TEXT contains '{word}'"
        for kind, t in db.DEFAULT_MSGS.items():
            low = t.lower()
            for word in self.BANGLISH:
                assert word not in low, f"DEFAULT_MSGS[{kind}] contains '{word}'"
