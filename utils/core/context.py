from __future__ import annotations

from io import StringIO
from typing import Any, Optional, Union

import discord
from discord.ext import commands

from utils.core.helpers import ParameterParser
from utils.core.paginator import Paginator


class Context(commands.Context):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.parameters: dict[str, Any] = {}

    async def respond(
        self,
        message: str,
        *,
        emoji: str = "",
        color: int | discord.Color | None = None,
        content: Optional[str] = None,
        member: Optional[discord.Member] = None,
        delete_after: Optional[float] = None,
    ) -> discord.Message:
        if color is None:
            color = self.bot.color

        embed = discord.Embed(
            color=color,
            description=f"{emoji} {(member or self.author).mention}**:** {message}",
        )

        try:
            return await self.reply(content=content, embed=embed, delete_after=delete_after)
        except discord.HTTPException:
            return await self.send(content=content, embed=embed, delete_after=delete_after)

    async def success(
        self,
        message: str,
        member: Optional[discord.Member] = None,
        content: Optional[str] = None,
        delete_after: Optional[float] = None,
    ) -> discord.Message:
        return await self.respond(
            message,
            content=content,
            emoji=self.bot.done,
            color=self.bot.color,
            member=member,
            delete_after=delete_after,
        )

    async def error(
        self,
        message: str,
        member: Optional[discord.Member] = None,
        content: Optional[str] = None,
        delete_after: Optional[float] = None,
    ) -> discord.Message:
        return await self.respond(
            message,
            content=content,
            emoji=self.bot.warn,
            color=self.bot.color,
            member=member,
            delete_after=delete_after,
        )

    async def paginate(
        self,
        to_paginate: Union[tuple[Any, list[str]], list[discord.Embed]],
        show_index: bool = True,
    ) -> Optional[discord.Message]:
        if isinstance(to_paginate, tuple):
            embed, rows = to_paginate
            if not rows:
                return await self.error("No entries found.")

            embeds: list[discord.Embed] = []
            working = embed.copy()
            working.description = StringIO()
            count = 0
            numbered_rows = tuple(
                f"{f'`{index}` ' if show_index else ''}{row}"
                for index, row in enumerate(rows, start=1)
            )

            for row in numbered_rows:
                if count < 10:
                    working.description.write(f"\n{row}")
                    count += 1
                else:
                    working.description = working.description.getvalue()
                    embeds.append(working)
                    working = embed.copy()
                    working.description = StringIO()
                    working.description.write(row)
                    count = 1

            if count:
                embeds.append(working)

            for index, page in enumerate(embeds, start=1):
                if isinstance(page.description, StringIO):
                    page.description = page.description.getvalue()
                page.set_footer(
                    text=f"Page {index} / {len(embeds)}  ({len(rows)} entries)"
                )
                page.set_author(
                    name=str(self.author),
                    icon_url=self.author.display_avatar.url,
                )

            if len(embeds) == 1:
                return await self.reply(embed=embeds[0])

            interface = Paginator(self.bot, embeds, self, invoker=self.author.id)
            return await interface.start()

        embeds = list(to_paginate)
        if not embeds:
            return await self.error("No entries found.")

        if len(embeds) == 1:
            return await self.reply(embed=embeds[0])

        interface = Paginator(self.bot, embeds, self, invoker=self.author.id)
        return await interface.start()

    async def send_help(
        self,
        command: commands.Command | str | None = None,
    ) -> Optional[discord.Message]:
        from utils.core.errors import send_usage_help

        return await send_usage_help(self, command)

    async def can_moderate(
        self,
        user: Union[discord.Member, discord.User],
        action: str = "moderate",
    ) -> Optional[discord.Message]:
        if user == self.author:
            return await self.error(f"You can't **{action}** yourself")

        if isinstance(user, discord.Member):
            if user.id == self.guild.owner_id:
                return await self.error(f"You can't **{action}** that member")

            if (
                user.top_role.position >= self.author.top_role.position
                and self.author.id != self.guild.owner_id
            ):
                return await self.error(f"You can't **{action}** that member")

            if user.top_role.position >= self.guild.me.top_role.position:
                return await self.error(f"I can't **{action}** that member")

        return None
