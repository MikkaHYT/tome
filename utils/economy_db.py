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


def safe_to_int(val: Any) -> int:
    if val is None:
        return 0
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(Decimal(str(val)))
    s = str(val).strip()
    if not s:
        return 0
    try:
        return int(s)
    except ValueError:
        try:
            return int(Decimal(s))
        except Exception:
            return 0


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
        val = int(Decimal(raw.replace(",", "")))
        return val if val > 0 else None
    except Exception:
        return None


def format_cash(amount: Any) -> str:
    amount = safe_to_int(amount)
    abs_amt = abs(amount)
    sign = "-" if amount < 0 else ""

    for full_name, _, multiplier in SCALES:
        if abs_amt >= multiplier:
            val = Decimal(abs_amt) / Decimal(multiplier)
            return f"🪙 **{sign}{val:.2f} {full_name}**"

    return f"🪙 **{sign}{abs_amt:,}**"


def format_cash_short(amount: Any) -> str:
    amount = safe_to_int(amount)
    abs_amt = abs(amount)
    sign = "-" if amount < 0 else ""

    for _, short_name, multiplier in SCALES:
        if abs_amt >= multiplier:
            val = Decimal(abs_amt) / Decimal(multiplier)
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
                    loan_due REAL DEFAULT 0,
                    fish_level INTEGER DEFAULT 1,
                    fish_xp INTEGER DEFAULT 0
                )
                """
            )
            for col, col_type in [("fish_level", "INTEGER DEFAULT 1"), ("fish_xp", "INTEGER DEFAULT 0")]:
                try:
                    await db.execute(f"ALTER TABLE economy_users ADD COLUMN {col} {col_type}")
                except Exception:
                    pass

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
                """
                CREATE TABLE IF NOT EXISTS shop_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE COLLATE NOCASE,
                    emoji TEXT DEFAULT '📦',
                    buy_price TEXT DEFAULT '0',
                    sell_price TEXT DEFAULT '0',
                    can_buy INTEGER DEFAULT 1
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_inventory (
                    user_id INTEGER,
                    item_name TEXT COLLATE NOCASE,
                    quantity INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, item_name)
                )
                """
            )

            await db.execute("INSERT OR IGNORE INTO economy_state (key, value) VALUES ('treasury', 537740000000000000000.0)")
            await db.execute("INSERT OR IGNORE INTO economy_state (key, value) VALUES ('multiplier', 0.28)")
            await db.execute("INSERT OR IGNORE INTO economy_state (key, value) VALUES ('fee_rate', 0.0114)")
            await db.execute("INSERT OR IGNORE INTO economy_state (key, value) VALUES ('passive_rate', 0.0043)")
            await db.execute("INSERT OR IGNORE INTO economy_state (key, value) VALUES ('global_wins', 24934.0)")
            await db.execute("INSERT OR IGNORE INTO economy_state (key, value) VALUES ('global_losses', 79976.0)")

            default_items = [
                ("Lucky Worm Bait", "🪱", "2500", "1250", 1),
                ("Fiberglass Fishing Rod", "🎣", "25000", "12500", 1),
                ("Shiny Pearl", "🦪", "0", "15000", 0),
                ("Sunken Treasure Chest", "🪙", "0", "150000", 0),
                ("Ancient Relic", "🏺", "0", "500000", 0),
                ("Old Boot", "👢", "0", "50", 0),
            ]
            for name, emoji, buy_p, sell_p, can_b in default_items:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO shop_items (name, emoji, buy_price, sell_price, can_buy)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (name, emoji, buy_p, sell_p, can_b),
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
                    data["wallet"] = safe_to_int(data.get("wallet"))
                    data["bank"] = safe_to_int(data.get("bank"))
                    data["loan_amount"] = safe_to_int(data.get("loan_amount"))
                    data["fish_level"] = max(1, min(10, data.get("fish_level") or 1))
                    data["fish_xp"] = data.get("fish_xp") or 0
                    return data

            await db.execute("INSERT OR IGNORE INTO economy_users (user_id) VALUES (?)", (user_id,))
            await db.commit()
            async with db.execute("SELECT * FROM economy_users WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
                data = dict(row)
                data["wallet"] = safe_to_int(data.get("wallet"))
                data["bank"] = safe_to_int(data.get("bank"))
                data["loan_amount"] = safe_to_int(data.get("loan_amount"))
                data["fish_level"] = max(1, min(10, data.get("fish_level") or 1))
                data["fish_xp"] = data.get("fish_xp") or 0
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
    async def record_game(won: bool) -> None:
        key = "global_wins" if won else "global_losses"
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE economy_state SET value = value + 1 WHERE key = ?", (key,))
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
                    item["amount"] = safe_to_int(item.get("amount"))
                    results.append(item)
                return results

    @staticmethod
    async def get_econ_state() -> tuple[float, float]:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT value FROM economy_state WHERE key = 'treasury'") as cur:
                tr_row = await cur.fetchone()
                treasury = tr_row[0] if tr_row else 537740000000000000000.0
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

    @staticmethod
    async def get_overview_data(user_id: int) -> dict[str, Any]:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute("SELECT wallet, bank FROM economy_users") as cur:
                rows = await cur.fetchall()

            total_wallet = sum(safe_to_int(r["wallet"]) for r in rows)
            total_bank = sum(safe_to_int(r["bank"]) for r in rows)
            circulating = total_wallet + total_bank

            async with db.execute("SELECT key, value FROM economy_state") as cur:
                state_rows = await cur.fetchall()
                state = {r["key"]: r["value"] for r in state_rows}

            treasury = safe_to_int(state.get("treasury", 537740000000000000000))
            total_supply = circulating + treasury

            user = await EconomyDB.get_user(user_id)
            user_net = user["wallet"] + user["bank"]

            portfolio_pct = 0.0
            if total_supply > 0:
                portfolio_pct = float((Decimal(user_net) / Decimal(total_supply)) * Decimal(100))

            day_ago = time.time() - 86400
            async with db.execute(
                "SELECT amount FROM economy_transactions WHERE created_at >= ?",
                (day_ago,),
            ) as cur:
                tx_rows = await cur.fetchall()
                volume_24h = sum(safe_to_int(r["amount"]) for r in tx_rows)

            wins = int(state.get("global_wins", 24934))
            losses = int(state.get("global_losses", 79976))
            total_games = wins + losses
            win_rate = (wins / total_games * 100) if total_games > 0 else 50.0

            return {
                "total_supply": total_supply,
                "circulating": circulating,
                "treasury": treasury,
                "fee_rate": state.get("fee_rate", 0.0114),
                "passive_rate": state.get("passive_rate", 0.0043),
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "user_net": user_net,
                "portfolio_pct": portfolio_pct,
                "volume_24h": volume_24h,
            }

    @staticmethod
    async def get_fishing_stats(user_id: int) -> tuple[int, int]:
        user = await EconomyDB.get_user(user_id)
        level = max(1, min(10, user.get("fish_level") or 1))
        xp = user.get("fish_xp") or 0
        return level, xp

    @staticmethod
    async def add_fishing_progress(user_id: int, xp_gain: int) -> tuple[int, bool]:
        level, current_xp = await EconomyDB.get_fishing_stats(user_id)
        new_xp = current_xp + xp_gain
        leveled_up = False

        xp_needed = level * 300
        if new_xp >= xp_needed and level < 10:
            level += 1
            new_xp = 0
            leveled_up = True

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE economy_users SET fish_level = ?, fish_xp = ? WHERE user_id = ?",
                (level, new_xp, user_id),
            )
            await db.commit()

        return level, leveled_up

    # ==========================================
    # SHOP & INVENTORY OPERATIONS
    # ==========================================

    @staticmethod
    async def add_shop_item(name: str, emoji: str, buy_price: int, sell_price: int | None = None, can_buy: int = 1) -> None:
        sell_p = sell_price if sell_price is not None else int(buy_price * 0.70)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO shop_items (name, emoji, buy_price, sell_price, can_buy)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    emoji = excluded.emoji,
                    buy_price = excluded.buy_price,
                    sell_price = excluded.sell_price,
                    can_buy = excluded.can_buy
                """,
                (name.strip(), emoji.strip(), str(buy_price), str(sell_p), can_buy),
            )
            await db.commit()

    @staticmethod
    async def remove_shop_item(name: str) -> bool:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("DELETE FROM shop_items WHERE LOWER(name) = LOWER(?)", (name.strip(),))
            await db.commit()
            return cur.rowcount > 0

    @staticmethod
    async def get_shop_item(name: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM shop_items WHERE LOWER(name) = LOWER(?)", (name.strip(),)) as cur:
                row = await cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["buy_price"] = safe_to_int(data.get("buy_price"))
                data["sell_price"] = safe_to_int(data.get("sell_price"))
                return data

    @staticmethod
    async def get_all_shop_items() -> list[dict[str, Any]]:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM shop_items ORDER BY id ASC") as cur:
                rows = await cur.fetchall()
                results = []
                for r in rows:
                    data = dict(r)
                    data["buy_price"] = safe_to_int(data.get("buy_price"))
                    data["sell_price"] = safe_to_int(data.get("sell_price"))
                    results.append(data)
                return results

    @staticmethod
    async def add_user_inventory(user_id: int, item_name: str, quantity: int = 1, default_emoji: str = "🐟", default_sell_price: int = 0) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            # Ensure item entry exists in shop_items registry for display & sale
            await db.execute(
                """
                INSERT OR IGNORE INTO shop_items (name, emoji, buy_price, sell_price, can_buy)
                VALUES (?, ?, '0', ?, 0)
                """,
                (item_name.strip(), default_emoji, str(default_sell_price)),
            )
            await db.execute(
                """
                INSERT INTO user_inventory (user_id, item_name, quantity)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, item_name) DO UPDATE SET
                    quantity = quantity + excluded.quantity
                """,
                (user_id, item_name.strip(), quantity),
            )
            await db.commit()

    @staticmethod
    async def remove_user_inventory(user_id: int, item_name: str, quantity: int = 1) -> bool:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT quantity FROM user_inventory WHERE user_id = ? AND LOWER(item_name) = LOWER(?)",
                (user_id, item_name.strip()),
            ) as cur:
                row = await cur.fetchone()
                if not row or row["quantity"] < quantity:
                    return False

            new_qty = row["quantity"] - quantity
            if new_qty <= 0:
                await db.execute(
                    "DELETE FROM user_inventory WHERE user_id = ? AND LOWER(item_name) = LOWER(?)",
                    (user_id, item_name.strip()),
                )
            else:
                await db.execute(
                    "UPDATE user_inventory SET quantity = ? WHERE user_id = ? AND LOWER(item_name) = LOWER(?)",
                    (new_qty, user_id, item_name.strip()),
                )
            await db.commit()
            return True

    @staticmethod
    async def get_user_inventory(user_id: int) -> list[dict[str, Any]]:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT ui.item_name, ui.quantity, si.emoji, si.buy_price, si.sell_price
                FROM user_inventory ui
                LEFT JOIN shop_items si ON LOWER(ui.item_name) = LOWER(si.name)
                WHERE ui.user_id = ? AND ui.quantity > 0
                ORDER BY ui.quantity DESC
                """,
                (user_id,),
            ) as cur:
                rows = await cur.fetchall()
                results = []
                for r in rows:
                    data = dict(r)
                    data["buy_price"] = safe_to_int(data.get("buy_price"))
                    data["sell_price"] = safe_to_int(data.get("sell_price"))
                    if not data.get("emoji"):
                        data["emoji"] = "🐟"
                    results.append(data)
                return results
