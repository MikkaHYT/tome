# utils/economy_db.py

from __future__ import annotations

import aiosqlite
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path("timezones.sqlite3")

JOBS = {
    "dishwasher": {"title": "Dishwasher", "salary": 2500, "high_end": False},
    "cashier": {"title": "Cashier", "salary": 6000, "high_end": False},
    "barista": {"title": "Barista", "salary": 12000, "high_end": False},
    "developer": {"title": "Software Engineer", "salary": 28000, "high_end": True},
    "doctor": {"title": "Doctor", "salary": 65000, "high_end": True},
    "ceo": {"title": "Corporate Executive", "salary": 160000, "high_end": True},
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

    units = [
        ("quintillion", 10**18), ("quin", 10**18),
        ("quadrillion", 10**15), ("quad", 10**15), ("q", 10**15),
        ("trillion", 10**12), ("tril", 10**12), ("t", 10**12),
        ("billion", 10**9), ("bil", 10**9), ("b", 10**9),
        ("million", 10**6), ("mil", 10**6), ("m", 10**6),
        ("thousand", 10**3), ("k", 10**3),
    ]

    for unit_name, multiplier in units:
        if raw.endswith(unit_name):
            num_part = raw[:-len(unit_name)].strip()
            try:
                return int(float(num_part) * multiplier)
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
    if abs_amt >= 10**18:
        return f"🪙 **{sign}{abs_amt / 10**18:.2f} quintillion**"
    if abs_amt >= 10**15:
        return f"🪙 **{sign}{abs_amt / 10**15:.2f} quadrillion**"
    if abs_amt >= 10**12:
        return f"🪙 **{sign}{abs_amt / 10**12:.2f} trillion**"
    if abs_amt >= 10**9:
        return f"🪙 **{sign}{abs_amt / 10**9:.2f} billion**"
    if abs_amt >= 10**6:
        return f"🪙 **{sign}{abs_amt / 10**6:.2f} million**"
    if abs_amt >= 10**3:
        return f"🪙 **{sign}{abs_amt / 10**3:.2f} thousand**"
    return f"🪙 **{sign}{int(abs_amt):,}**"


def format_cash_short(amount: int | float) -> str:
    abs_amt = abs(float(amount))
    if abs_amt >= 10**18:
        return f"{abs_amt / 10**18:.2f} quin"
    if abs_amt >= 10**15:
        return f"{abs_amt / 10**15:.2f} quad"
    if abs_amt >= 10**12:
        return f"{abs_amt / 10**12:.2f} tril"
    if abs_amt >= 10**9:
        return f"{abs_amt / 10**9:.2f} bil"
    if abs_amt >= 10**6:
        return f"{abs_amt / 10**6:.2f} mil"
    if abs_amt >= 10**3:
        return f"{abs_amt / 10**3:.2f}k"
    return f"{int(abs_amt):,}"


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
                    daily_ts REAL DEFAULT 0,
                    weekly_ts REAL DEFAULT 0,
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
                CREATE TABLE IF NOT EXISTS economy_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    type TEXT,
                    amount REAL,
                    description TEXT,
                    created_at REAL
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
                "INSERT OR IGNORE INTO economy_state (key, value) VALUES ('treasury', 500000000.0)"
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
    async def update_balance(
        user_id: int,
        *,
        wallet: int = 0,
        bank: int = 0,
        description: str | None = None,
    ) -> None:
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
            if description and (wallet != 0 or bank != 0):
                net_change = wallet + bank
                tx_type = "in" if net_change > 0 else "out"
                await db.execute(
                    """
                    INSERT INTO economy_transactions (user_id, type, amount, description, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, tx_type, abs(net_change), description, time.time()),
                )
            await db.commit()

    @staticmethod
    async def get_recent_transactions(user_id: int, limit: int = 5) -> list[dict[str, Any]]:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM economy_transactions
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    @staticmethod
    async def get_econ_state() -> tuple[float, float]:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT value FROM economy_state WHERE key = 'treasury'") as cur:
                tr_row = await cur.fetchone()
                treasury = tr_row[0] if tr_row else 500000000.0
            async with db.execute("SELECT value FROM economy_state WHERE key = 'multiplier'") as cur:
                mp_row = await cur.fetchone()
                multiplier = mp_row[0] if mp_row else 0.28
            return treasury, multiplier

    @staticmethod
    async def modify_treasury(delta: float) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE economy_state SET value = MAX(0.0, value + ?) WHERE key = 'treasury'",
                (delta,),
            )
            await db.commit()
