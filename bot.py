from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from config import EMBED_COLOR, PREFIX
from utils.core import Database, HTTP, ParameterParser, SendHelp, is_guild_owner
from utils.core.cache import BotCache
from utils.core.context import Context
from utils.core.errors import send_command_error, send_usage_help, USAGE_ERRORS

ROOT = Path(__file__).resolve().parent
COGS_DIR = ROOT / "cogs"
DB_PATH = ROOT / "timezones.sqlite3"

os.chdir(ROOT)
load_dotenv(ROOT / ".env")

commands.is_guild_owner = is_guild_owner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("timezones")

# ============================================================
# HARDCODED OWNER IDs ============================================================
OWNER_IDS = {217389696043450368, 1354629588965134376}  


class TimezonesBot(commands.Bot):

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix=PREFIX,
            intents=intents,
            help_command=None,
            owner_ids=OWNER_IDS,
            activity=discord.CustomActivity(name=".gg/timezones"),
            status=discord.Status.online,
        )

        self.owner_id = None
        self.owner_ids = OWNER_IDS

        self.color = EMBED_COLOR
        self.start_time = datetime.now(timezone.utc)
        self.done = "✅"
        self.warn = "❌"
        self.reply = "↪"
        self.db = Database(DB_PATH)
        self.cache = BotCache(self)
        self.proxied_session = HTTP()

    async def is_owner(self, user: discord.User | discord.Member) -> bool:
        if self.owner_ids:
            return user.id in self.owner_ids
        return await super().is_owner(user)

    async def get_context(
        self,
        origin: discord.Interaction | discord.Message,
        *,
        cls=Context,
    ) -> Context:
        return await super().get_context(origin, cls=cls)

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self.cache.initialize_settings_cache()
        self.before_invoke(self._before_command)
        await self._load_cogs()
        await self._sync_commands()

    async def close(self) -> None:
        await self.db.close()
        await super().close()

    @staticmethod
    async def _before_command(ctx: Context) -> None:
        ctx.parameters = {}
        if ctx.command and hasattr(ctx.command, "parameters"):
            ctx.parameters = await ParameterParser(ctx, ctx.command.parameters).parse()

    async def blacklist(self, user_id: int, type: int = 0) -> None:
        logger.warning("Blocked suspicious activity from user %s (type %s)", user_id, type)

    async def _load_cogs(self) -> None:
        if not COGS_DIR.is_dir():
            logger.warning("Cogs directory not found: %s", COGS_DIR)
            return

        loaded = 0
        for path in sorted(COGS_DIR.glob("*.py")):
            if path.name.startswith("_"):
                continue

            extension = f"cogs.{path.stem}"
            try:
                await self.load_extension(extension)
                loaded += 1
                logger.info("Loaded %s", extension)
            except Exception:
                logger.exception("Failed to load %s", extension)

        logger.info("Cog load complete (%d loaded)", loaded)

    async def _sync_commands(self) -> None:
        try:
            synced = await self.tree.sync()
            logger.info("Synced %d application command(s)", len(synced))
        except Exception:
            logger.exception("Application command sync failed")

    async def on_ready(self) -> None:
        await self.change_presence(
            activity=discord.CustomActivity(name=".gg/timezones"),
            status=discord.Status.online,
        )
        logger.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "?")
        logger.info("Configured owner IDs: %s", self.owner_ids)
        logger.info("Serving %d guild(s)", len(self.guilds))

    async def on_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.CommandNotFound):
            return

        if isinstance(error, SendHelp):
            if ctx.command:
                await send_usage_help(ctx)
            return

        if isinstance(error, USAGE_ERRORS):
            if ctx.command:
                logger.warning(
                    "Command usage error (%s): %s",
                    ctx.command.qualified_name,
                    error,
                )
                await send_usage_help(ctx)
            return

        if isinstance(error, commands.CommandOnCooldown):
            await send_command_error(ctx, error, log_level=logging.INFO)
            return

        if isinstance(error, (commands.MissingPermissions, commands.BotMissingPermissions)):
            await send_command_error(ctx, error, log_level=logging.WARNING)
            return

        if isinstance(
            error,
            (
                commands.CheckFailure,
                commands.MaxConcurrencyReached,
            ),
        ):
            await send_command_error(ctx, error, log_level=logging.WARNING)
            return

        await send_command_error(ctx, error)


def get_token() -> str:
    token = os.environ.get("DISCORD_TOKEN", "").strip()

    if not token or token in {"YOUR_BOT_TOKEN", "PASTE_YOUR_ACTUAL_72_CHARACTER_TOKEN_HERE"}:
        logger.error(
            "DISCORD_TOKEN is missing or still set to a placeholder. "
            "Export a valid bot token before starting."
        )
        sys.exit(1)

    return token


async def main() -> None:
    bot = TimezonesBot()
    token = get_token()

    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
