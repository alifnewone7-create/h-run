# Tg Bot – Join Request Bot (python-telegram-bot + Neon PostgreSQL)

The whole bot runs on **python-telegram-bot**. **aiogram** is used only for Premium emoji (message → `<tg-emoji>` HTML) and coloured buttons (`style` / premium icon keyboards).

When someone sends a join request to your private channel, the bot sends that user a message. The admin panel controls **Non Approve / Auto Approve**, **Text Set**, **Broadcast** and **Statistics**.

## Setup (local)

```bash
cd "Tg Bot"
python -m venv venv
# Windows: venv\Scripts\activate   |   Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
python bot.py
```

Configuration lives in `.env`:

| Key | Meaning |
|-----|---------|
| `BOT_TOKEN` | Token from @BotFather |
| `DATABASE_URL` | Neon DB URL (tables are created automatically) |
| `ADMIN_IDS` | Admin Telegram user IDs (comma separated) |

Requires Python 3.10+.

## Channel setup
1. Add the bot to your private channel as **Admin** with the **"Invite users via link"** (Add Members) permission.
2. Enable **"Request Admin Approval"** on the channel invite link.
3. Every join request now triggers the bot's message. Multiple channels are supported.

## Admin Panel – `/admin`
- **✅ Non / Approve** – `Non Approve` (message only) / `Auto Approve` (message + approve the request).
- **👋 Wlc Setting** – two messages, each fully customizable:
  - **🚀 Start Msg** – sent when a user sends `/start`.
  - **👋 Wlc Msg** – sent when a user requests to join your channel.
  - Each has **📝 Set Text** (all formatting + Premium emoji, variables `{first_name}`, `{username}`, `{channel}`), **🖼 Set Media** (photo / video / audio / GIF / voice / document, text becomes the caption, removable), **🔘 Set Button** (add multiple URL buttons; each with name + emoji/Premium emoji icon, link and color: Default / Blue / Green / Red; edit or delete any button), **👁 Preview** and **♻️ Reset**.
- **📢 Broadcast** – 24h / 7d / 30d / All Time users. Any message (text/photo/video/file/sticker) is copied with its formatting and Premium emoji, with live progress and a final report.
- **📊 Statistics** – realtime New Users, Active Users, Join Requests (24h / 7d / 30d / All Time), approved/pending, bot started, blocked, channels. Includes a 🔄 Refresh button.
- **📡 Channels** – channels where the bot is an admin.

`/cancel` – cancel the current step.

## Premium (custom) emoji
- Just type/paste Premium emoji into your message in Telegram (Set Text or Broadcast) – the bot stores them as `<tg-emoji emoji-id="...">` and sends them as Premium emoji. In a button name, the first Premium emoji becomes the button icon.
- Colored buttons use the Bot API `style` field (`primary` blue, `success` green, `danger` red); older Telegram apps show them in the default color.
- Telegram rule (Bot API, Feb 2026): bots can send custom emoji in private chats/groups **only if the bot owner (the account that created the bot in @BotFather) has an active Telegram Premium subscription** (or the bot has a username bought on Fragment).
- Without Premium, the bot automatically falls back to the regular emoji so the message is still delivered.
- The Set Text screen shows how many Premium emoji were detected; the broadcast confirmation shows the same.
- Media is stored as a Telegram `file_id`, which belongs to the current bot token – if you change the token, upload the media again.

## Notes
- Periods (24h/7d/30d) mean users active in that time (join request or `/start`).
- Telegram rule: after a join request, the bot can message the user only while the request is pending. To receive **broadcasts**, a user must press `/start` in the bot once – put the bot link (`https://t.me/<bot_username>?start=1`) in your welcome text.
- Run the bot in one place only – polling the same token from two places causes a conflict.
