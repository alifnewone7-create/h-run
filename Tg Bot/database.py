import json
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

PERIODS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}
PERIOD_LABELS = {"24h": "24 Hours", "7d": "7 Days", "30d": "30 Days", "all": "All Time"}
_INTERVALS = {"24h": "24 hours", "7d": "7 days", "30d": "30 days"}

DEFAULT_TEXT = (
    "👋 Hello <b>{first_name}</b>!\n\n"
    "Your request to join <b>{channel}</b> has been received ✅"
)
DEFAULT_MSGS = {
    "start": "👋 Hello <b>{first_name}</b>!\n\nWelcome to the bot.",
    "welcome": DEFAULT_TEXT,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     BIGINT PRIMARY KEY,
    first_name  TEXT,
    username    TEXT,
    started     BOOLEAN NOT NULL DEFAULT FALSE,
    is_blocked  BOOLEAN NOT NULL DEFAULT FALSE,
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS join_requests (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    chat_id    BIGINT NOT NULL,
    status     TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS channels (
    chat_id    BIGINT PRIMARY KEY,
    title      TEXT,
    is_admin   BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_joined ON users (joined_at);
CREATE INDEX IF NOT EXISTS idx_users_active ON users (last_active);
CREATE INDEX IF NOT EXISTS idx_requests_created ON join_requests (created_at);
"""

pool: asyncpg.Pool | None = None


def _clean_dsn(dsn: str) -> str:
    # asyncpg doesn't understand channel_binding; sslmode stays
    parts = urlsplit(dsn)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "channel_binding"]
    return urlunsplit(parts._replace(query=urlencode(query)))


async def connect(dsn: str) -> None:
    global pool
    pool = await asyncpg.create_pool(
        _clean_dsn(dsn), min_size=1, max_size=10, statement_cache_size=0
    )
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def close() -> None:
    if pool:
        await pool.close()


# ---------- users ----------
async def upsert_user(user_id: int, first_name: str | None, username: str | None, started: bool = False) -> None:
    await pool.execute(
        """
        INSERT INTO users (user_id, first_name, username, started)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_id) DO UPDATE SET
            first_name = EXCLUDED.first_name,
            username = EXCLUDED.username,
            last_active = now(),
            started = users.started OR EXCLUDED.started,
            is_blocked = CASE WHEN EXCLUDED.started THEN FALSE ELSE users.is_blocked END
        """,
        user_id, first_name, username, started,
    )


async def set_blocked(user_id: int, blocked: bool = True) -> None:
    await pool.execute("UPDATE users SET is_blocked = $2 WHERE user_id = $1", user_id, blocked)


async def count_users(period: str) -> int:
    return await pool.fetchval(
        "SELECT COUNT(*) FROM users WHERE NOT is_blocked "
        "AND ($1::interval IS NULL OR last_active >= now() - $1::interval)",
        PERIODS[period],
    )


async def get_user_ids(period: str) -> list[int]:
    rows = await pool.fetch(
        "SELECT user_id FROM users WHERE NOT is_blocked "
        "AND ($1::interval IS NULL OR last_active >= now() - $1::interval)",
        PERIODS[period],
    )
    return [r["user_id"] for r in rows]


# ---------- join requests ----------
async def add_request(user_id: int, chat_id: int, status: str) -> None:
    await pool.execute(
        "INSERT INTO join_requests (user_id, chat_id, status) VALUES ($1, $2, $3)",
        user_id, chat_id, status,
    )


# ---------- channels ----------
async def upsert_channel(chat_id: int, title: str | None, is_admin: bool) -> None:
    await pool.execute(
        """
        INSERT INTO channels (chat_id, title, is_admin) VALUES ($1, $2, $3)
        ON CONFLICT (chat_id) DO UPDATE SET
            title = EXCLUDED.title, is_admin = EXCLUDED.is_admin, updated_at = now()
        """,
        chat_id, title, is_admin,
    )


async def list_channels() -> list[asyncpg.Record]:
    return await pool.fetch("SELECT chat_id, title FROM channels WHERE is_admin ORDER BY updated_at DESC")


# ---------- settings ----------
async def get_setting(key: str, default: str) -> str:
    value = await pool.fetchval("SELECT value FROM settings WHERE key = $1", key)
    return value if value is not None else default


async def set_setting(key: str, value: str) -> None:
    await pool.execute(
        "INSERT INTO settings (key, value) VALUES ($1, $2) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        key, value,
    )


# ---------- custom messages (start / welcome) ----------
async def get_msg(kind: str) -> dict:
    raw = await pool.fetchval("SELECT value FROM settings WHERE key = $1", f"msg:{kind}")
    if raw:
        return json.loads(raw)
    text = DEFAULT_MSGS[kind]
    if kind == "welcome":
        text = await get_setting("welcome_text", text)
    return {"text": text, "media": None, "buttons": []}


async def set_msg(kind: str, cfg: dict) -> None:
    await set_setting(f"msg:{kind}", json.dumps(cfg, ensure_ascii=False))


async def reset_msg(kind: str) -> None:
    keys = [f"msg:{kind}"] + (["welcome_text"] if kind == "welcome" else [])
    await pool.execute("DELETE FROM settings WHERE key = ANY($1::text[])", keys)


# ---------- statistics ----------
def _cols(col: str, prefix: str) -> str:
    parts = [
        f"COUNT(*) FILTER (WHERE {col} >= now() - interval '{iv}') AS {prefix}_{k}"
        for k, iv in _INTERVALS.items()
    ]
    return ", ".join(parts + [f"COUNT(*) AS {prefix}_all"])


async def get_stats() -> dict:
    users = await pool.fetchrow(
        f"SELECT {_cols('joined_at', 'new')}, {_cols('last_active', 'act')}, "
        "COUNT(*) FILTER (WHERE started) AS started, "
        "COUNT(*) FILTER (WHERE is_blocked) AS blocked FROM users"
    )
    reqs = await pool.fetchrow(
        f"SELECT {_cols('created_at', 'req')}, "
        "COUNT(*) FILTER (WHERE status = 'approved') AS approved, "
        "COUNT(*) FILTER (WHERE status = 'pending') AS pending FROM join_requests"
    )
    channels = await pool.fetchval("SELECT COUNT(*) FROM channels WHERE is_admin")
    return {**dict(users), **dict(reqs), "channels": channels}
