# utils/economy_db.py

from __future__ import annotations

import aiosqlite
import os
import re
import time
from decimal import Decimal
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

SCALES = [
    ("centillion", "cent", 10**303),
    ("googol", "googol", 10**100),
    ("vigintillion", "vig", 10**63),
    ("novemdecillion", "novemdec", 10**60),
    ("octodecillion", "octodec", 10**57),
    ("septendecillion", "septendec", 10**54),
    ("sexdecillion", "sexdec", 10**51),
    ("quindecillion", "quindec", 10**48),
    ("quattuordecillion", "quattuordec", 10**45),
    ("tredecillion", "tredec", 10**42),
    ("duodecillion", "duodec", 10**39),
    ("undecillion", "undec", 10**36),
    ("decillion", "dec", 10**33),
    ("nonillion", "non", 10**30),
    ("octillion", "oct", 10**27),
    ("septillion", "sep", 10**24),
    ("sextillion", "sex", 10**21),
    ("quintillion", "quin", 10**18),
    ("quadrillion", "quad", 10**15),
    ("trillion", "tril", 10**12),
    ("billion", "bil", 10**9),
    ("million", "mil", 10**6),
    ("thousand", "k", 10**3),
]


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

    # Handle short single-letter aliases first
    if raw.endswith("q") and not raw.endswith(("quad", "quin")):
        num_part = raw[:-1].strip()
        try:
            return int(Decimal(num_part) * Decimal(10**15))
        except Exception:
            return None

    if raw.endswith("t") and not raw.endswith("tril"):
        num_part = raw[:-1].strip()
        try:
            return int(Decimal(num_part) * Decimal(10**12))
        except Exception:
            return None

    if raw.endswith("b") and not raw.endswith("bil"):
        num_part = raw[:-1].strip()
        try:
            return int(Decimal(num_part) * Decimal(10**9))
        except Exception:
            return None

    if raw.endswith("m") and not raw.endswith("mil"):
        num_part = raw[:-1].strip()
        try:
            return int(Decimal(num_part) * Decimal(10**6))
        except Exception:
            return None

    for full_name, short_name, multiplier in SCALES:
        for suffix in (full_name, short_name):
            if raw.endswith(suffix):
                num_part = raw[:-len(suffix)].strip()
                try:
                    return int(Decimal(num_part) * Decimal(multiplier))
                except Exception:
                    return None

    try:
        val = int(raw.replace(",", ""))
        return val if val > 0 else None
    except ValueError:
        return None


def format_cash(amount: int | float) -> str:
    amount = int(amount)
    abs_amt = abs(amount)
    sign = "-" if amount < 0 else ""

    for full_name, _, multiplier in SCALES:
        if abs_amt >= multiplier:
            val = abs_amt / multiplier
            return f"🪙 **{sign}{val:.2f} {full_name}**"

    return f"🪙 **{sign}{abs_amt:,}**"


def format_cash_short(amount: int | float) -> str:
    amount = int(amount)
    abs_amt = abs(amount)
    sign = "-" if amount < 0 else ""

    for _, short_name, multiplier in SCALES:
        if abs_amt >= multiplier:
            val = abs_amt / multiplier
            return f"{sign}{val:.2f} {short_name}"

    return f"{sign}{abs_amt:,}"


class EconomyDB:
    @staticmethod
    async def init_db() -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS economy_users (
                    user_id INTEGER PRIMARY KEY,
                    wallet TEXT DEFAULT '1000',
                    bank TEXT DEFAULT '0',
                    daily_ts REAL DEFAULT 0,
                    weekly_ts REAL DEFAULT 0,
                    monthly_ts REAL DEFAULT 0,
                    work_ts REAL DEFAULT 0,
                    job_key TEXT DEFAULT NULL,
                    job_shifts INTEGER DEFAULT 0,
                    last_worked REAL DEFAULT 0,
                    loan_amount TEXT DEFAULT '0',
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
                    amount TEXT,
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
                    data = dict(row)
                    data["wallet"] = int(str(data.get("wallet") or "0"))
                    data["bank"] = int(str(data.get("bank") or "0"))
                    data["loan_amount"] = int(str(data.get("loan_amount") or "0"))
                    return data

            await db.execute("INSERT OR IGNORE INTO economy_users (user_id) VALUES (?)", (user_id,))
            await db.commit()
            async with db.execute("SELECT * FROM economy_users WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
                data = dict(row)
                data["wallet"] = int(str(data.get("wallet") or "0"))
                data["bank"] = int(str(data.get("bank") or "0"))
                data["loan_amount"] = int(str(data.get("loan_amount") or "0"))
                return data

    @staticmethod
    async def update_balance(
        user_id: int,
        *,
        wallet: int = 0,
        bank: int = 0,
        description: str | None = None,
    ) -> None:
        user = await EconomyDB.get_user(user_id)
        new_wallet = max(0, user["wallet"] + wallet)
        new_bank = max(0, user["bank"] + bank)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                UPDATE economy_users
                SET wallet = ?,
                    bank = ?
                WHERE user_id = ?
                """,
                (str(new_wallet), str(new_bank), user_id),
            )
            if description and (wallet != 0 or bank != 0):
                net_change = wallet + bank
                tx_type = "in" if net_change > 0 else "out"
                await db.execute(
                    """
                    INSERT INTO economy_transactions (user_id, type, amount, description, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, tx_type, str(abs(net_change)), description, time.time()),
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
                results = []
                for r in rows:
                    item = dict(r)
                    item["amount"] = int(str(item.get("amount") or "0"))
                    results.append(item)
                return results

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
