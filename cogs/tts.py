# cogs/tts.py

from __future__ import annotations

import io
import logging

import discord
from discord.ext import commands
from elevenlabs.client import ElevenLabs

from config import EMBED_COLOR

logger = logging.getLogger(__name__)

MODEL_ID = "eleven_v3"
OUTPUT_FORMAT = "mp3_44100_128"
MAX_CHARS = 1000  # keep requests reasonable / avoid abuse 


def simple_view(content: str, *, timeout: int = 60) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=timeout)
    container = discord.ui.Container(accent_color=EMBED_COLOR)
    container.add_item(discord.ui.TextDisplay(content))
    view.add_item(container)
    return view


class TTS(commands.Cog):
    """ElevenLabs text-to-speech command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.client = ElevenLabs(api_key="sk_369e4e780fc704eca99a335905cfc1be77c07f784a14b1c7")

    async def send_error(self, ctx: commands.Context, message: str) -> None:
        await ctx.send(view=simple_view(message))

    @commands.command(
        name="tts",
        description="Converts text to speech using ElevenLabs and sends it as an mp3.",
        help="Generates a spoken mp3 of the given text.",
        extras={"example": "tts The first move is what sets everything in motion."},
    )
    async def tts(self, ctx: commands.Context, *, text: str | None = None) -> None:
        if not text:
            help_view = simple_view(
                "# Command: tts\n\n"
                "**Syntax**\n"
                "`,tts <text>`\n\n"
                "**Example**\n"
                "`,tts The first move is what sets everything in motion.`"
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
                audio = await self.bot.loop.run_in_executor(
                    None,
                    lambda: self.client.text_to_speech.convert(
                        text=text,
                        voice_id="YDCfZMLWcUmsGvqHq0rS",
                        model_id=MODEL_ID,
                        output_format=OUTPUT_FORMAT,
                    ),
                )
                # convert() returns a generator of audio chunks - collect them
                buffer = io.BytesIO()
                for chunk in audio:
                    if chunk:
                        buffer.write(chunk)
                buffer.seek(0)
            except Exception:
                logger.exception("ElevenLabs TTS request failed")
                await self.send_error(ctx, "❌ Failed to generate speech.")
                return

        if buffer.getbuffer().nbytes == 0:
            await self.send_error(ctx, "❌ ElevenLabs returned no audio.")
            return

        try:
            await ctx.send(file=discord.File(buffer, filename="tts.mp3"))
        except discord.HTTPException:
            logger.exception("Discord rejected the tts audio file")
            await self.send_error(ctx, "❌ Discord rejected the audio file.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TTS(bot))
