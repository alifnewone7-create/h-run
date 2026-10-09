import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

AUDIENCE_LABELS = {"joined": "Joined", "pending": "Pending", "leaved": "Leaved", "all": "All"}
_AUDIENCES = {
    "joined": "SELECT user_id FROM members WHERE status = 'joined'",
    "pending": "SELECT user_id FROM join_requests WHERE status = 'pending'",
    "leaved": "SELECT user_id FROM members WHERE status = 'left'",
    "all": "SELECT user_id FROM users UNION SELECT user_id FROM members UNION SELECT user_id FROM join_requests",
}
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
CREATE TABLE IF NOT EXISTS members (
    user_id    BIGINT NOT NULL,
    chat_id    BIGINT NOT NULL,
    status     TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, chat_id)
);
ALTER TABLE join_requests ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE channels ADD COLUMN IF NOT EXISTS member_count INTEGER NOT NULL DEFAULT 0;
DELETE FROM join_requests a USING join_requests b
    WHERE a.user_id = b.user_id AND a.chat_id = b.chat_id
    AND (b.status = 'approved', b.id) > (a.status = 'approved', a.id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_requests_user_chat ON join_requests (user_id, chat_id);
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


def _audience_sql(audience: str) -> str:
    return (
        f"SELECT DISTINCT t.user_id FROM ({_AUDIENCES[audience]}) t "
        "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.user_id = t.user_id AND u.is_blocked)"
    )


async def count_users(audience: str) -> int:
    return await pool.fetchval(f"SELECT COUNT(*) FROM ({_audience_sql(audience)}) a")


async def get_user_ids(audience: str) -> list[int]:
    return [r["user_id"] for r in await pool.fetch(_audience_sql(audience))]


# ---------- join requests ----------
async def add_request(user_id: int, chat_id: int, status: str) -> None:
    # one row per user + channel: a new request resets it, approval updates it
    await pool.execute(
        """
        INSERT INTO join_requests (user_id, chat_id, status) VALUES ($1, $2, $3)
        ON CONFLICT (user_id, chat_id) DO UPDATE SET
            status = EXCLUDED.status, created_at = now(), updated_at = now()
        """,
        user_id, chat_id, status,
    )


async def approve_request(user_id: int, chat_id: int) -> None:
    await pool.execute(
        "UPDATE join_requests SET status = 'approved', updated_at = now() "
        "WHERE user_id = $1 AND chat_id = $2 AND status = 'pending'",
        user_id, chat_id,
    )


# ---------- members (joined / left) ----------
async def set_member(user_id: int, chat_id: int, status: str) -> None:
    await pool.execute(
        """
        INSERT INTO members (user_id, chat_id, status) VALUES ($1, $2, $3)
        ON CONFLICT (user_id, chat_id) DO UPDATE SET status = EXCLUDED.status, updated_at = now()
        """,
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


async def set_member_count(chat_id: int, count: int) -> None:
    await pool.execute("UPDATE channels SET member_count = $2 WHERE chat_id = $1", chat_id, count)


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
    members = await pool.fetchrow(
        "SELECT COUNT(*) FILTER (WHERE status = 'joined') AS joined, "
        "COUNT(*) FILTER (WHERE status = 'left') AS leaved FROM members"
    )
    channels = await pool.fetchrow(
        "SELECT COUNT(*) AS channels, COALESCE(SUM(member_count), 0) AS channel_members "
        "FROM channels WHERE is_admin"
    )
    return {**dict(users), **dict(reqs), **dict(members), **dict(channels)}
