from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord
from discord.ext import commands
from durations_nlp import Duration

from utils.core import regex as regex_module
from utils.core.helpers import SendHelp

if TYPE_CHECKING:
    from utils.core.context import Context


class MemberConverter(commands.MemberConverter):
    async def convert(self, ctx: Context, argument: str) -> discord.Member:
        if (
            argument.isdigit()
            and ctx.bot.cache.is_ratelimited(f"rl:invalid_id{ctx.guild.id}-{argument}")
        ):
            raise commands.MemberNotFound(argument)

        try:
            return await super().convert(ctx, argument)
        except commands.MemberNotFound:
            if argument.isdigit():
                await ctx.bot.cache.ratelimited(
                    f"rl:invalid_id{ctx.guild.id}-{argument}",
                    1,
                    86400,
                )
            raise


class UserConverter(commands.UserConverter):
    async def convert(self, ctx: Context, argument: str) -> discord.User:
        if (
            argument.isdigit()
            and ctx.bot.cache.is_ratelimited(f"rl:invalid_id{ctx.guild.id}-{argument}")
        ):
            raise commands.UserNotFound(argument)

        try:
            return await super().convert(ctx, argument)
        except commands.UserNotFound:
            if argument.isdigit():
                await ctx.bot.cache.ratelimited(
                    f"rl:invalid_id{ctx.guild.id}-{argument}",
                    1,
                    86400,
                )
            raise


class RoleConverter(commands.RoleConverter):
    async def convert(self, ctx: Context, argument: str) -> discord.Role:
        return await super().convert(ctx, argument)


class MessageConverter(commands.MessageConverter):
    async def convert(self, ctx: Context, argument: str) -> discord.Message:
        return await super().convert(ctx, argument)


class Timespan(commands.Converter):
    async def convert(self, ctx: Context, argument: str) -> Duration:
        ret = Duration(argument)
        if not ret.seconds and argument not in {
            "0",
            "0 seconds",
            "0s",
            "0h",
            "0w",
            "0m",
            "0 hours",
            "0 weeks",
            "0 months",
        }:
            raise SendHelp()
        return ret


class AttachmentConverter(commands.Converter):
    async def convert(self, ctx: Context, argument: Optional[str] = None) -> str:
        if argument and (links := regex_module.link.findall(argument)):
            return links[0]

        if not argument:
            async for message in ctx.channel.history(limit=50):
                if message.attachments:
                    return message.attachments[0].url

        await ctx.send_help(ctx.command.qualified_name)
        raise SendHelp()


class HexConverter(commands.Converter):
    async def convert(self, ctx: Context, argument: Optional[str] = None) -> int:
        if argument is None:
            raise SendHelp()

        try:
            return int(argument)
        except ValueError:
            try:
                return int(argument.strip("#"), 16)
            except ValueError:
                raise SendHelp()
