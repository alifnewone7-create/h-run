"""Offline tests for single-channel admin flow (Iteration 5)."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import ReplyKeyboardMarkup as PtbReply
from telegram import ReplyKeyboardRemove


# ---------- keyboards ----------
def test_channel_picker_only_can_invite_users():
    import keyboards as kb
    picker = kb.channel_picker()
    # Reply keyboard with 2 rows
    assert len(picker.keyboard) == 2
    select_btn = picker.keyboard[0][0]
    cancel_btn = picker.keyboard[1][0]
    assert select_btn.text == "📡 Select Channel"
    assert cancel_btn.text == kb.PICKER_CANCEL == "❌ Cancel"
    # request_chat params
    req = select_btn.request_chat
    assert req.request_id == kb.CHANNEL_REQUEST_ID
    assert req.chat_is_channel is True
    assert req.request_title is True
    # only can_invite_users is True in both admin rights
    for rights in (req.bot_administrator_rights, req.user_administrator_rights):
        d = rights.model_dump()
        assert d["can_invite_users"] is True
        for k, v in d.items():
            if k == "can_invite_users":
                continue
            # Optional fields may be None (omitted => Telegram treats as False)
            assert v in (False, None), f"{k} must be False/None but is {v}"


def test_channel_picker_to_ptb_valid_json():
    import keyboards as kb
    from utils import to_ptb
    markup = to_ptb(kb.channel_picker())
    assert isinstance(markup, PtbReply)
    assert len(markup.keyboard) == 2
    select = markup.keyboard[0][0]
    cancel = markup.keyboard[1][0]
    assert select.text == "📡 Select Channel"
    assert cancel.text == "❌ Cancel"
    assert select.request_chat is not None
    assert select.request_chat.request_id == kb.CHANNEL_REQUEST_ID
    assert select.request_chat.chat_is_channel is True
    # Serializable as JSON
    assert json.loads(json.dumps(markup.to_dict()))


def test_channel_menu_add_vs_delete():
    import keyboards as kb
    add_menu = kb.channel_menu(False)
    buttons_add = [b for row in add_menu.inline_keyboard for b in row]
    assert any(b.callback_data == "ch:add" for b in buttons_add)
    assert not any(b.callback_data == "ch:del" for b in buttons_add)

    del_menu = kb.channel_menu(True)
    buttons_del = [b for row in del_menu.inline_keyboard for b in row]
    assert any(b.callback_data == "ch:del" for b in buttons_del)
    assert not any(b.callback_data == "ch:add" for b in buttons_del)


def test_main_menu_has_channel_button():
    import keyboards as kb
    buttons = [b for row in kb.main_menu().inline_keyboard for b in row]
    assert any(b.callback_data == "adm:channels" and "Channel" in b.text for b in buttons)


# ---------- helpers ----------
def _ctx(bot=None):
    ctx = MagicMock()
    ctx.bot = bot or AsyncMock()
    ctx.bot.id = 42
    ctx.user_data = {}
    return ctx


def _admin_update(callback_data=None, chat_type="private"):
    upd = MagicMock()
    upd.effective_user = MagicMock(id=111)
    upd.effective_chat = MagicMock(id=111, type=chat_type)
    if callback_data is not None:
        cb = MagicMock()
        cb.data = callback_data
        cb.message = MagicMock(chat=MagicMock(id=111), chat_id=111)
        cb.message.edit_text = AsyncMock()
        cb.answer = AsyncMock()
        upd.callback_query = cb
        upd.effective_message = cb.message
    return upd


# ---------- cb_channel panel ----------
@pytest.mark.asyncio
async def test_cb_channel_no_channel(monkeypatch):
    from handlers import channel
    monkeypatch.setattr("database.get_channel", AsyncMock(return_value=None))
    upd = _admin_update(callback_data="adm:channels")
    ctx = _ctx()
    await channel.cb_channel(upd, ctx)
    # safe_edit will call edit_text on message
    upd.callback_query.message.edit_text.assert_awaited()
    call_args = upd.callback_query.message.edit_text.call_args
    text = call_args.args[0]
    assert "No channel added yet" in text
    markup = call_args.kwargs["reply_markup"]
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "ch:add" in datas


@pytest.mark.asyncio
async def test_cb_channel_shows_existing(monkeypatch):
    from handlers import channel
    monkeypatch.setattr(
        "database.get_channel",
        AsyncMock(return_value={"chat_id": -100123, "title": "My Chan"}),
    )
    upd = _admin_update(callback_data="adm:channels")
    ctx = _ctx()
    await channel.cb_channel(upd, ctx)
    text = upd.callback_query.message.edit_text.call_args.args[0]
    assert "My Chan" in text and "-100123" in text
    assert "Invite Users via Link" in text
    markup = upd.callback_query.message.edit_text.call_args.kwargs["reply_markup"]
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "ch:del" in datas


# ---------- cb_add ----------
@pytest.mark.asyncio
async def test_cb_add_sends_picker_when_none(monkeypatch):
    from handlers import channel
    monkeypatch.setattr("database.get_channel", AsyncMock(return_value=None))
    upd = _admin_update(callback_data="ch:add")
    ctx = _ctx()
    await channel.cb_add(upd, ctx)
    ctx.bot.send_message.assert_awaited()
    kwargs = ctx.bot.send_message.await_args.kwargs
    markup = kwargs["reply_markup"]
    assert isinstance(markup, PtbReply)
    # cancel alert not fired
    upd.callback_query.answer.assert_awaited_with()


@pytest.mark.asyncio
async def test_cb_add_alerts_when_channel_exists(monkeypatch):
    from handlers import channel
    monkeypatch.setattr(
        "database.get_channel",
        AsyncMock(return_value={"chat_id": -1, "title": "X"}),
    )
    upd = _admin_update(callback_data="ch:add")
    ctx = _ctx()
    await channel.cb_add(upd, ctx)
    # No picker sent
    ctx.bot.send_message.assert_not_called()
    # Alert raised
    args, kwargs = upd.callback_query.answer.await_args
    assert kwargs.get("show_alert") is True
    assert "Only 1 channel" in args[0]


# ---------- cb_delete / cb_delete_ok ----------
@pytest.mark.asyncio
async def test_cb_delete_shows_confirm(monkeypatch):
    from handlers import channel
    monkeypatch.setattr(
        "database.get_channel",
        AsyncMock(return_value={"chat_id": -1, "title": "C1"}),
    )
    upd = _admin_update(callback_data="ch:del")
    ctx = _ctx()
    await channel.cb_delete(upd, ctx)
    markup = upd.callback_query.message.edit_text.call_args.kwargs["reply_markup"]
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "ch:delok" in datas
    assert "adm:channels" in datas


@pytest.mark.asyncio
async def test_cb_delete_ok_removes_and_leaves(monkeypatch):
    from handlers import channel
    monkeypatch.setattr(
        "database.get_channel",
        AsyncMock(return_value={"chat_id": -99, "title": "Gone"}),
    )
    del_mock = AsyncMock()
    monkeypatch.setattr("database.delete_channel", del_mock)
    upd = _admin_update(callback_data="ch:delok")
    ctx = _ctx()
    await channel.cb_delete_ok(upd, ctx)
    del_mock.assert_awaited_once()
    ctx.bot.leave_chat.assert_awaited_with(-99)


# ---------- on_chat_shared ----------
def _shared_update(request_id=1, chat_id=-500, title="Shared"):
    upd = MagicMock()
    upd.effective_user = MagicMock(id=111)
    upd.effective_chat = MagicMock(id=111)
    msg = MagicMock(chat_id=111)
    msg.reply_text = AsyncMock()
    msg.chat_shared = MagicMock(request_id=request_id, chat_id=chat_id, title=title)
    upd.effective_message = msg
    return upd


@pytest.mark.asyncio
async def test_on_chat_shared_success(monkeypatch):
    from handlers import channel
    import keyboards as kb
    monkeypatch.setattr("database.get_channel", AsyncMock(return_value=None))
    set_mock = AsyncMock()
    monkeypatch.setattr("database.set_channel", set_mock)
    upd = _shared_update(request_id=kb.CHANNEL_REQUEST_ID, chat_id=-500, title="Hello")
    ctx = _ctx()
    member = MagicMock(status="administrator", can_invite_users=True)
    ctx.bot.get_chat_member = AsyncMock(return_value=member)
    await channel.on_chat_shared(upd, ctx)
    set_mock.assert_awaited_with(-500, "Hello")
    # reply confirms + ReplyKeyboardRemove used
    reply_kwargs = upd.effective_message.reply_text.await_args_list[0].kwargs
    assert isinstance(reply_kwargs["reply_markup"], ReplyKeyboardRemove)
    assert "✅" in upd.effective_message.reply_text.await_args_list[0].args[0]


@pytest.mark.asyncio
async def test_on_chat_shared_wrong_request_id_ignored(monkeypatch):
    from handlers import channel
    set_mock = AsyncMock()
    monkeypatch.setattr("database.set_channel", set_mock)
    monkeypatch.setattr("database.get_channel", AsyncMock(return_value=None))
    upd = _shared_update(request_id=999)
    ctx = _ctx()
    await channel.on_chat_shared(upd, ctx)
    set_mock.assert_not_called()
    upd.effective_message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_on_chat_shared_rejected_when_channel_exists(monkeypatch):
    from handlers import channel
    import keyboards as kb
    monkeypatch.setattr(
        "database.get_channel",
        AsyncMock(return_value={"chat_id": -1, "title": "existing"}),
    )
    set_mock = AsyncMock()
    monkeypatch.setattr("database.set_channel", set_mock)
    upd = _shared_update(request_id=kb.CHANNEL_REQUEST_ID)
    ctx = _ctx()
    await channel.on_chat_shared(upd, ctx)
    set_mock.assert_not_called()
    text = upd.effective_message.reply_text.await_args.args[0]
    assert "Only 1 channel" in text


@pytest.mark.asyncio
async def test_on_chat_shared_rejected_without_admin(monkeypatch):
    from handlers import channel
    import keyboards as kb
    monkeypatch.setattr("database.get_channel", AsyncMock(return_value=None))
    set_mock = AsyncMock()
    monkeypatch.setattr("database.set_channel", set_mock)
    upd = _shared_update(request_id=kb.CHANNEL_REQUEST_ID)
    ctx = _ctx()
    # Not admin
    ctx.bot.get_chat_member = AsyncMock(
        return_value=MagicMock(status="member", can_invite_users=False)
    )
    await channel.on_chat_shared(upd, ctx)
    set_mock.assert_not_called()


@pytest.mark.asyncio
async def test_on_chat_shared_rejected_without_invite_right(monkeypatch):
    from handlers import channel
    import keyboards as kb
    monkeypatch.setattr("database.get_channel", AsyncMock(return_value=None))
    set_mock = AsyncMock()
    monkeypatch.setattr("database.set_channel", set_mock)
    upd = _shared_update(request_id=kb.CHANNEL_REQUEST_ID)
    ctx = _ctx()
    ctx.bot.get_chat_member = AsyncMock(
        return_value=MagicMock(status="administrator", can_invite_users=False)
    )
    await channel.on_chat_shared(upd, ctx)
    set_mock.assert_not_called()


# ---------- picker cancel ----------
@pytest.mark.asyncio
async def test_on_picker_cancel_removes_keyboard(monkeypatch):
    from handlers import channel
    monkeypatch.setattr("database.get_channel", AsyncMock(return_value=None))
    upd = MagicMock()
    upd.effective_chat = MagicMock(id=111)
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    upd.effective_message = msg
    ctx = _ctx()
    await channel.on_picker_cancel(upd, ctx)
    kwargs = msg.reply_text.await_args.kwargs
    assert isinstance(kwargs["reply_markup"], ReplyKeyboardRemove)


# ---------- handler registration ----------
def test_handler_registration_order():
    """on_picker_cancel (text ❌ Cancel) must register before generic state MessageHandler."""
    import bot as bot_mod
    recorded = []

    class Fake:
        def add_handler(self, h):
            recorded.append(h)

        def add_handlers(self, hs):
            recorded.extend(hs)

    fake = Fake()
    # Replicate bot.main() registration order WITHOUT running polling
    from handlers import admin, channel, join, user, wlc
    from telegram.ext import MessageHandler
    from utils import ADMIN
    admin.register_commands(fake)
    user.register(fake)
    admin.register_callbacks(fake)
    wlc.register_callbacks(fake)
    channel.register_callbacks(fake)
    channel.register_messages(fake)
    fake.add_handler(MessageHandler(ADMIN, bot_mod.on_state))
    join.register(fake)

    # Find positions
    picker_cancel_idx = None
    state_idx = None
    for i, h in enumerate(recorded):
        cb = getattr(h, "callback", None)
        if cb is channel.on_picker_cancel:
            picker_cancel_idx = i
        if cb is bot_mod.on_state:
            state_idx = i
    assert picker_cancel_idx is not None, "on_picker_cancel not registered"
    assert state_idx is not None, "on_state not registered"
    assert picker_cancel_idx < state_idx


# ---------- stats_text no channels field ----------
@pytest.mark.asyncio
async def test_stats_text_no_channel_line(monkeypatch):
    from handlers import admin
    stats = {
        "new_24h": 1, "new_7d": 2, "new_30d": 3, "new_all": 4,
        "started": 5, "blocked": 0,
        "approved": 1, "pending": 2,
        "joined": 3, "leaved": 1,
        "channel_members": 42,
    }
    monkeypatch.setattr("database.get_stats", AsyncMock(return_value=stats))
    monkeypatch.setattr("database.get_setting", AsyncMock(return_value="non"))
    monkeypatch.setattr("database.get_channel", AsyncMock(return_value={"chat_id": -1, "title": "X"}))
    monkeypatch.setattr("database.set_member_count", AsyncMock())
    bot = AsyncMock()
    bot.get_chat_member_count = AsyncMock(return_value=42)
    text = await admin.stats_text(bot)
    assert "📡 Channels" not in text
    assert "Channel Members" in text
    assert "New Users" in text
    assert "Mode" in text


@pytest.mark.asyncio
async def test_home_text_channel_display(monkeypatch):
    from handlers import admin
    monkeypatch.setattr("database.get_setting", AsyncMock(return_value="non"))
    # No channel
    monkeypatch.setattr("database.get_channel", AsyncMock(return_value=None))
    text = await admin.home_text()
    assert "📡 Channel: <b>Not added</b>" in text
    # With channel
    monkeypatch.setattr(
        "database.get_channel",
        AsyncMock(return_value={"chat_id": -1, "title": "My"}),
    )
    text = await admin.home_text()
    assert "📡 Channel: <b>My</b>" in text


# ---------- stats dict shape ----------
def test_get_stats_no_channels_key():
    import database
    import inspect
    # verify "channels" key is NOT in the stats dict (channel_members ok)
    src = inspect.getsource(database.get_stats)
    assert '"channels"' not in src and "'channels'" not in src


# ---------- join: ignore unlinked chats, no notifications on my_status ----------
@pytest.mark.asyncio
async def test_on_join_request_ignores_other_chats(monkeypatch):
    from handlers import join as join_h
    monkeypatch.setattr(
        "database.get_channel",
        AsyncMock(return_value={"chat_id": -999, "title": "linked"}),
    )
    upsert = AsyncMock()
    monkeypatch.setattr("database.upsert_user", upsert)
    monkeypatch.setattr("database.add_request", AsyncMock())
    upd = MagicMock()
    upd.chat_join_request = MagicMock(
        chat=MagicMock(id=-111, title="other"),
        from_user=MagicMock(id=5, first_name="A", username=None),
        user_chat_id=5,
    )
    ctx = _ctx()
    await join_h.on_join_request(upd, ctx)
    upsert.assert_not_called()


@pytest.mark.asyncio
async def test_on_my_status_private_sets_blocked(monkeypatch):
    from handlers import join as join_h
    sb = AsyncMock()
    monkeypatch.setattr("database.set_blocked", sb)
    upd = MagicMock()
    upd.my_chat_member = MagicMock(
        chat=MagicMock(id=55, type="private"),
        new_chat_member=MagicMock(status="kicked"),
    )
    ctx = _ctx()
    await join_h.on_my_status(upd, ctx)
    sb.assert_awaited_with(55, True)
    # No admin notifications: ctx.bot.send_message never called
    ctx.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_on_my_status_demotion_unlinks_channel(monkeypatch):
    from handlers import join as join_h
    monkeypatch.setattr(
        "database.get_channel",
        AsyncMock(return_value={"chat_id": -42, "title": "T"}),
    )
    del_mock = AsyncMock()
    monkeypatch.setattr("database.delete_channel", del_mock)
    upd = MagicMock()
    upd.my_chat_member = MagicMock(
        chat=MagicMock(id=-42, type="channel"),
        new_chat_member=MagicMock(status="left"),
    )
    ctx = _ctx()
    await join_h.on_my_status(upd, ctx)
    del_mock.assert_awaited_once()
    ctx.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_on_my_status_admin_in_channel_no_message(monkeypatch):
    """When bot is promoted/added as admin in the linked channel, no notification is sent."""
    from handlers import join as join_h
    monkeypatch.setattr(
        "database.get_channel",
        AsyncMock(return_value={"chat_id": -42, "title": "T"}),
    )
    del_mock = AsyncMock()
    monkeypatch.setattr("database.delete_channel", del_mock)
    upd = MagicMock()
    upd.my_chat_member = MagicMock(
        chat=MagicMock(id=-42, type="channel"),
        new_chat_member=MagicMock(status="administrator"),
    )
    ctx = _ctx()
    await join_h.on_my_status(upd, ctx)
    del_mock.assert_not_called()
    ctx.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_on_member_ignores_other_chats(monkeypatch):
    from handlers import join as join_h
    monkeypatch.setattr(
        "database.get_channel",
        AsyncMock(return_value={"chat_id": -1, "title": "x"}),
    )
    sm = AsyncMock()
    monkeypatch.setattr("database.set_member", sm)
    monkeypatch.setattr("database.upsert_user", AsyncMock())
    upd = MagicMock()
    upd.chat_member = MagicMock(
        chat=MagicMock(id=-999),
        old_chat_member=MagicMock(status="left", is_member=False),
        new_chat_member=MagicMock(status="member", is_member=True, user=MagicMock(id=7, is_bot=False, first_name="n", username=None)),
    )
    ctx = _ctx()
    await join_h.on_member(upd, ctx)
    sm.assert_not_called()
