from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

import discord

PREFIX: Final[str] = ","

EMBED_COLOR: Final[discord.Color] = discord.Color.from_str("#2b2d31")

EMBED_COLORS: Final[dict[str, discord.Color]] = {
    "default": EMBED_COLOR,
    "success": discord.Color.green(),
    "error": discord.Color.red(),
    "warning": discord.Color.orange(),
    "info": discord.Color.blue(),
    "purple": discord.Color.purple(),
    "gold": discord.Color.gold(),
}


@dataclass(frozen=True, slots=True)
class ModerationConfig:
    max_warnings: int = 5
    auto_mod_enabled: bool = True
    log_channel_id: int | None = None


@dataclass(frozen=True, slots=True)
class EconomyConfig:
    starting_balance: int = 100
    daily_bonus: int = 50
    work_cooldown: int = 3600


@dataclass(frozen=True, slots=True)
class LevelingConfig:
    xp_per_message: int = 15
    xp_per_voice_minute: int = 5
    level_up_channel: int | None = None


moderation = ModerationConfig()
economy = EconomyConfig()
leveling = LevelingConfig()


def get_bot_owner_id() -> int | None:
    raw = os.getenv("BOT_OWNER_ID", "").strip()
    if raw.isdigit():
        return int(raw)
    return None


PING_RESPONSES: Final[tuple[str, ...]] = (
    "i know nothing.",
    "a connection to the server",
    "i dont even know what to put here",
    "the feds",
    "said im in the club alone",
    "no one",
    "the chinese government",
    "your mom",
    "the russians",
    "6ix9ines ankle monitor",
    "trumps ears",
    "The",
    "horny asian women around your area",
    "the migos minecraft server",
    "localhost",
    "for a fat bitch to grab a cookie",
)
