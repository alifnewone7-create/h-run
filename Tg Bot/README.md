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
  - Each has **📝 Set Text** (all formatting + Premium emoji, variables `{first_name}`, `{username}`, `{channel}`), **🖼 Set Media** (photo / video / audio / GIF / voice / document, text becomes the caption, removable), **🔘 Set Button** (add multiple URL buttons; each with name + emoji/Premium emoji icon, link and color: Default / Blue / Green / Red; edit, delete or ⬆️⬇️ move any button to change the order, 📐 Layout: 1 per line or 2 per line side by side), **👁 Preview** and **♻️ Reset**.
- **📢 Broadcast** – choose 🟢 Joined Users (in the channel) / ⏳ Pending Users (request pending) / 🚪 Leaved Users (left the channel) / 👥 All Users (everyone), each with its user count. Send any message (text/photo/video/file/sticker, formatting + Premium emoji kept), then **➕ Add Button** (multiple buttons, each with name + emoji/Premium emoji icon, link and color) or **⏭ Skip** to send without buttons. Choose 📐 Layout: 1 per line or 2 per line (side by side). Tap any added button to ✏️ edit its name, 🔗 link, 🎨 color, ⬆️⬇️ move it up/down to change the order, or 🗑 delete it before sending. A preview is shown before sending, with live progress and a final report.
- **📊 Statistics** – realtime 👥 New Users (24h / 7d / 30d / All Time – everyone the bot has seen: /start, join request or channel join), ✅ Approved / ⏳ Pending (one row per user + channel – a pending request moves to approved once the user joins), 🟢 Joined / 🚪 Leaved (tracked from channel join/leave events), 👥 Channel Members (live from Telegram), bot started, blocked, channels. Includes a 🔄 Refresh button.
- **📡 Channels** – channels where the bot is an admin.

`/cancel` – cancel the current step.

## Premium (custom) emoji
- Just type/paste Premium emoji into your message in Telegram (Set Text or Broadcast) – the bot stores them as `<tg-emoji emoji-id="...">` and sends them as Premium emoji. In a button name, emoji stay exactly where you type them. A Premium emoji at the **start** of the name becomes the premium button icon – Telegram always draws button icons on the left, so a Premium emoji in the middle or at the end is kept in that position as its normal emoji.
- Colored buttons use the Bot API `style` field (`primary` blue, `success` green, `danger` red); older Telegram apps show them in the default color.
- Telegram rule (Bot API, Feb 2026): bots can send custom emoji in private chats/groups **only if the bot owner (the account that created the bot in @BotFather) has an active Telegram Premium subscription** (or the bot has a username bought on Fragment).
- Without Premium, the bot automatically falls back to the regular emoji so the message is still delivered.
- The Set Text screen shows how many Premium emoji were detected; the broadcast confirmation shows the same.
- Media is stored as a Telegram `file_id`, which belongs to the current bot token – if you change the token, upload the media again.

## Notes
- New Users periods (24h/7d/30d) count users whose **first** contact with the bot (`/start`, join request or channel join) was within that time; All Time = every user.
- Telegram rule: after a join request, the bot can message the user only while the request is pending. To receive **broadcasts**, a user must press `/start` in the bot once – put the bot link (`https://t.me/<bot_username>?start=1`) in your welcome text.
- Run the bot in one place only – polling the same token from two places causes a conflict.
