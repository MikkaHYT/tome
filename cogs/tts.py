# cogs/tts.py

from __future__ import annotations

import io
import logging

import aiohttp
import discord
from discord.ext import commands

from config import EMBED_COLOR

logger = logging.getLogger(__name__)

FISH_TTS_URL = "https://api.fish.audio/v1/tts"
FISH_MODEL = "s2.1-pro-free"  # free tier model for testing/prototyping
MAX_CHARS = 1000  # keep requests reasonable / avoid abuse


def simple_view(content: str, *, timeout: int = 60) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=timeout)
    container = discord.ui.Container(accent_color=EMBED_COLOR)
    container.add_item(discord.ui.TextDisplay(content))
    view.add_item(container)
    return view


class TTS(commands.Cog):
    """Fish Audio text-to-speech command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        if self.session:
            await self.session.close()

    async def send_error(self, ctx: commands.Context, message: str) -> None:
        await ctx.send(view=simple_view(message))

    @commands.command(
        name="tts",
        description="Converts text to speech using Fish Audio and sends it as an mp3.",
        help="Generates a spoken mp3 of the given text.",
        extras={"example": "tts Hello! Welcome to Fish Audio."},
    )
    async def tts(self, ctx: commands.Context, *, text: str | None = None) -> None:
        if not text:
            help_view = simple_view(
                "# Command: tts\n\n"
                "**Syntax**\n"
                "`,tts <text>`\n\n"
                "**Example**\n"
                "`,tts Hello! Welcome to Fish Audio.`"
            )
            await ctx.send(view=help_view)
            return

        if len(text) > MAX_CHARS:
            await self.send_error(
                ctx, f"❌ That's too long. Keep it under {MAX_CHARS} characters."
            )
            return

        
        async with ctx.typing():
            try:
                assert self.session is not None
                async with self.session.post(
                    FISH_TTS_URL,
                    headers={
                        "Authorization": f"Bearer sk-fish-JlQ51EW0V2ffREBG6fRzVD0us0bUHj6RqLAbq9ew-jQ",
                        "Content-Type": "application/json",
                        "model": FISH_MODEL,
                    },
                    json={
                        "text": text,
                        "reference_id": "6ea3c15f427e402399022da1c96f0b70",
                        "format": "mp3",
                    },
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(
                            "Fish Audio TTS request failed (%s): %s",
                            resp.status,
                            body[:500],
                        )
                        await self.send_error(ctx, "❌ Failed to generate speech.")
                        return

                    data = await resp.read()
            except aiohttp.ClientError:
                logger.exception("Fish Audio TTS request failed")
                await self.send_error(ctx, "❌ Failed to generate speech.")
                return

        if not data:
            await self.send_error(ctx, "❌ Fish Audio returned no audio.")
            return

        try:
            await ctx.send(file=discord.File(io.BytesIO(data), filename="tts.mp3"))
        except discord.HTTPException:
            logger.exception("Discord rejected the tts audio file")
            await self.send_error(ctx, "❌ Discord rejected the audio file.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TTS(bot))
