# PRD – Tg Bot (Telegram Join Request Bot)
## Problem
Aiogram 3 bot + Neon Postgres. Bot is admin in private channels; on join request, bot messages user. Admin panel: Non/Approve (Non Approve / Auto Approve), Text Set (formatted text w/ preview, vars {first_name},{username},{channel}), Broadcast (24h/7d/30d/All Time, any message type), Statistics (realtime). Admin ID 7188243734. Multiple channels. Code only – user runs locally.
## Implemented (2026-06)
- /app/Tg Bot: bot.py, config.py, database.py (asyncpg, auto schema), utils.py, keyboards.py, handlers/{admin,join,user}.py, README, .env
- Message sent before approve (Telegram user_chat_id rule); blocked tracking; Channels list; admin notify on bot admin add/remove
- Tests: tests/test_bot.py 29/29 pass (mocked Telegram, real Neon)
## Iteration 2 (2026-06)
- All bot texts + README switched to English only
- Premium/custom emoji in Text Set (stored as <tg-emoji>) and Broadcast (copy_message keeps entities); emoji count shown; auto fallback to regular emoji if Telegram rejects
- Tests: 40/40 pass
## Iteration 3 (2026-06)
- Text Set renamed to Wlc Setting: Start Msg (/start) + Wlc Msg (join request), each with Set Text, Set Media (photo/video/audio/GIF/voice/doc, removable), Set Button (multiple URL buttons, name w/ premium emoji icon, link, color style primary/success/danger, edit/delete), Preview, Reset
- Stored as JSON in settings msg:start / msg:welcome (legacy welcome_text migrated)
- Bot token changed to @automsgimh_bot
- Tests: 81/81 pass
## Backlog
- P1: Buttons side-by-side in one row; per-channel text; button reorder
- P2: Approve all pending requests button; export users CSV; persistent FSM (Redis)
