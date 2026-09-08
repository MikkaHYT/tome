from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    note TEXT NOT NULL,
    note_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id, note_id)
);

CREATE TABLE IF NOT EXISTS taken_roles (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id, role_id)
);

CREATE TABLE IF NOT EXISTS temporary_bans (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    unban_on TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS hard_banned (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS moderation_history (
    guild_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    reason TEXT,
    created_on TEXT NOT NULL,
    case_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, case_id)
);

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id INTEGER PRIMARY KEY,
    mute_role_id INTEGER,
    jail_role_id INTEGER,
    jail_channel_id INTEGER
);

CREATE TABLE IF NOT EXISTS muted_user (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    unmute_on TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS jailed_user (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    unjail_on TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS sticky_roles (
    guild_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);

CREATE TABLE IF NOT EXISTS welcome_settings (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS leave_settings (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS boost_settings (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS unboost_settings (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);
"""


def _parse_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return value


def _normalize_row(row: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(_parse_value(value) for value in row)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _convert(self, statement: str) -> str:
        statement = statement.replace(
            "ON DUPLICATE KEY UPDATE mute_role_id = VALUES(mute_role_id), jail_role_id = VALUES(jail_role_id), jail_channel_id = VALUES(jail_channel_id)",
            (
                "ON CONFLICT(guild_id) DO UPDATE SET "
                "mute_role_id = excluded.mute_role_id, "
                "jail_role_id = excluded.jail_role_id, "
                "jail_channel_id = excluded.jail_channel_id"
            ),
        )
        return statement.replace("%s", "?")

    def _prepare_params(self, params: tuple[Any, ...]) -> tuple[Any, ...]:
        prepared = []
        for value in params:
            if isinstance(value, datetime):
                prepared.append(value.isoformat())
            else:
                prepared.append(value)
        return tuple(prepared)

    async def execute(
        self,
        statement: str,
        *params: Any,
        one_row: bool = False,
        one_value: bool = False,
        as_list: bool = False,
    ) -> Any:
        if self._conn is None:
            raise RuntimeError("Database is not connected")

        statement = self._convert(statement).strip()
        params = self._prepare_params(params)

        if ";" in statement and not statement.upper().startswith("SELECT"):
            parts = [part.strip() for part in statement.split(";") if part.strip()]
            if len(parts) > 1:
                offset = 0
                for part in parts:
                    count = part.count("?")
                    chunk = params[offset : offset + count]
                    offset += count
                    await self._conn.execute(part, chunk)
                await self._conn.commit()
                return ()

        cursor = await self._conn.execute(statement, params)

        if statement.upper().startswith(("INSERT", "UPDATE", "DELETE")):
            await self._conn.commit()
            return ()

        rows = await cursor.fetchall()
        if not rows:
            return ()

        rows = [_normalize_row(row) for row in rows]

        if one_value:
            return rows[0][0]

        if one_row:
            return rows[0]

        if as_list:
            return tuple(row[0] for row in rows)

        return rows

    async def fetchrow(self, statement: str, *params: Any) -> tuple[Any, ...] | None:
        result = await self.execute(statement, *params, one_row=True)
        return result if result else None

    async def fetchval(self, statement: str, *params: Any) -> Any:
        return await self.execute(statement, *params, one_value=True)

    async def fetch(self, statement: str, *params: Any) -> tuple[Any, ...]:
        result = await self.execute(statement, *params, as_list=True)
        return result if result else ()
