# utils/economy_db.py

from __future__ import annotations

import aiosqlite
import os
import time
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path("timezones.sqlite3")

JOBS = {
    "dishwasher": {"title": "Dishwasher", "min_shifts": 0, "salary": 2500},
    "cashier": {"title": "Cashier", "min_shifts": 5, "salary": 6000},
    "barista": {"title": "Barista", "min_shifts": 12, "salary": 12000},
    "developer": {"title": "Software Engineer", "min_shifts": 25, "salary": 28000},
    "doctor": {"title": "Doctor", "min_shifts": 50, "salary": 65000},
    "ceo": {"title": "Corporate Executive", "min_shifts": 100, "salary": 160000},
}


def parse_bet(arg: str, user_wallet: int) -> int | None:
    if not arg:
        return None
    raw = arg.strip().lower()
    if raw in ("all", "max"):
        return user_wallet if user_wallet > 0 else None
    if raw in ("half", "50%"):
        return max(1, user_wallet // 2) if user_wallet > 0 else None
    if raw == "25%":
        return max(1, user_wallet // 4) if user_wallet > 0 else None
    if raw == "75%":
        return max(1, (user_wallet * 3) // 4) if user_wallet > 0 else None

    multipliers = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "t": 1_000_000_000_000}
    suffix = raw[-1]
    if suffix in multipliers:
        try:
            num = float(raw[:-1])
            return int(num * multipliers[suffix])
        except ValueError:
            return None
    try:
        val = int(raw.replace(",", ""))
        return val if val > 0 else None
    except ValueError:
        return None


def format_cash(amount: int | float) -> str:
    amount = float(amount)
    abs_amt = abs(amount)
    sign = "-" if amount < 0 else ""
    if abs_amt >= 1_000_000_000_000:
        return f"🪙 **{sign}{abs_amt / 1_000_000_000_000:.2f} trillion**"
    if abs_amt >= 1_000_000_000:
        return f"🪙 **{sign}{abs_amt / 1_000_000_000:.2f} billion**"
    if abs_amt >= 1_000_000:
        return f"🪙 **{sign}{abs_amt / 1_000_000:.2f} million**"
    if abs_amt >= 1_000:
        return f"🪙 **{sign}{abs_amt / 1_000:.2f} thousand**"
    return f"🪙 **{sign}{int(abs_amt):,}**"


class EconomyDB:
    @staticmethod
    async def init_db() -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS economy_users (
                    user_id INTEGER PRIMARY KEY,
                    wallet INTEGER DEFAULT 1000,
                    bank INTEGER DEFAULT 0,
                    bank_max INTEGER DEFAULT 50000,
                    daily_ts REAL DEFAULT 0,
                    monthly_ts REAL DEFAULT 0,
                    work_ts REAL DEFAULT 0,
                    job_key TEXT DEFAULT NULL,
                    job_shifts INTEGER DEFAULT 0,
                    last_worked REAL DEFAULT 0,
                    loan_amount INTEGER DEFAULT 0,
                    loan_due REAL DEFAULT 0
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS economy_state (
                    key TEXT PRIMARY KEY,
                    value REAL
                )
                """
            )
            await db.execute(
                "INSERT OR IGNORE INTO economy_state (key, value) VALUES ('treasury', 50000000.0)"
            )
            await db.execute(
                "INSERT OR IGNORE INTO economy_state (key, value) VALUES ('multiplier', 0.28)"
            )
            await db.commit()

    @staticmethod
    async def get_user(user_id: int) -> dict[str, Any]:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM economy_users WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
                if row:
                    return dict(row)
            await db.execute("INSERT OR IGNORE INTO economy_users (user_id) VALUES (?)", (user_id,))
            await db.commit()
            async with db.execute("SELECT * FROM economy_users WHERE user_id = ?", (user_id,)) as cur:
                return dict(await cur.fetchone())

    @staticmethod
    async def update_balance(user_id: int, *, wallet: int = 0, bank: int = 0) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                UPDATE economy_users
                SET wallet = MAX(0, wallet + ?),
                    bank = MAX(0, bank + ?)
                WHERE user_id = ?
                """,
                (wallet, bank, user_id),
            )
            await db.commit()

    @staticmethod
    async def get_econ_state() -> tuple[float, float]:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT value FROM economy_state WHERE key = 'treasury'") as cur:
                tr_row = await cur.fetchone()
                treasury = tr_row[0] if tr_row else 50000000.0
            async with db.execute("SELECT value FROM economy_state WHERE key = 'multiplier'") as cur:
                mp_row = await cur.fetchone()
                multiplier = mp_row[0] if mp_row else 0.28
            return treasury, multiplier

    @staticmethod
    async def modify_treasury(delta: float) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE economy_state SET value = MAX(0.0, value + ?) WHERE key = 'treasury'", (delta,))
            await db.commit()
