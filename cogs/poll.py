# cogs/poll.py

from __future__ import annotations

import discord
from discord.ext import commands


class UtilityPoll(commands.Cog, name="Utility"):
    """Utility poll commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(
        name="qp",
        aliases=["quickpoll", "poll"],
        description="Quickly adds thumbs up and thumbs down reactions to a message.",
        help="Creates a simple reaction poll on the message.",
        extras={"example": "qp Should we do a drop tonight?"},
    )
    async def quick_poll(self, ctx: commands.Context, *, question: str | None = None) -> None:
        try:
            await ctx.message.add_reaction("👍")
            await ctx.message.add_reaction("👎")
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UtilityPoll(bot))
