from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from random import choice
from time import time

import discord
import psutil
from discord import app_commands
from discord.ext import commands

from config import EMBED_COLOR, PING_RESPONSES
ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"__pycache__", ".git", "venv", ".venv", "node_modules"}


def _count_commands(cmds: list[commands.Command]) -> int:
    total = 0
    for cmd in cmds:
        total += 1
        if isinstance(cmd, commands.Group):
            total += _count_commands(cmd.commands)
    return total


def _count_lines() -> int:
    total_lines = 0
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for file in files:
            if not file.endswith(".py"):
                continue
            try:
                with open(
                    os.path.join(root, file),
                    encoding="utf-8",
                    errors="ignore",
                ) as handle:
                    total_lines += sum(1 for _ in handle)
            except OSError:
                pass
    return total_lines


def _format_bytes(num_bytes: int) -> str:
    if num_bytes >= 1024 ** 3:
        return f"{round(num_bytes / (1024 ** 3), 2)}GiB"
    return f"{round(num_bytes / (1024 ** 2), 2)}MiB"


def build_botinfo_view(
    bot: commands.Bot,
    *,
    total_users: int,
    total_servers: int,
    total_commands: int,
    total_lines: int,
) -> discord.ui.LayoutView:
    bot_user = bot.user
    name = bot_user.name if bot_user else "Timezones"
    avatar = (
        bot_user.display_avatar.url
        if bot_user
        else "https://cdn.discordapp.com/embed/avatars/0.png"
    )

    cpu_usage = psutil.cpu_percent(interval=None)
    mem_used_str = _format_bytes(psutil.virtual_memory().used)

    bot_created_ts = (
        discord.utils.format_dt(bot_user.created_at, style="R")
        if bot_user
        else "n/a"
    )
    start_time = getattr(bot, "start_time", datetime.now(timezone.utc))
    startup_ts = discord.utils.format_dt(start_time, style="R")
    gateway_ms = round(bot.latency * 1000)

    header_text = (
        f"# {name}\n"
        f"A Juice WRLD utility bot with moderation, leaks, snippets, covers, and radio.\n"
        f"-# Utilizing `{total_commands}` commands with `{total_lines:,}` lines of code"
    )

    info_text = (
        f"**Bot**\n"
        f"> **Users**: `{total_users:,}`\n"
        f"> **Servers**: `{total_servers:,}`\n"
        f"> **Created**: {bot_created_ts}\n\n"
        f"**System**\n"
        f"> **Latency**: `{gateway_ms}ms`\n"
        f"> **CPU**: `{cpu_usage}%`\n"
        f"> **Memory**: `{mem_used_str}`\n"
        f"> **Started**: {startup_ts}"
    )

    invite_url = (
        discord.utils.oauth_url(
            bot_user.id,
            permissions=discord.Permissions(
                send_messages=True,
                embed_links=True,
                attach_files=True,
                read_message_history=True,
                add_reactions=True,
                connect=True,
                speak=True,
                use_voice_activation=True,
                manage_messages=True,
                manage_channels=True,
                manage_roles=True,
                moderate_members=True,
                ban_members=True,
                kick_members=True,
            ),
        )
        if bot_user
        else None
    )

    class BotInfoView(discord.ui.LayoutView):
        def __init__(self) -> None:
            super().__init__(timeout=180)
            container = discord.ui.Container(accent_color=EMBED_COLOR)
            container.add_item(
                discord.ui.Section(
                    discord.ui.TextDisplay(header_text),
                    accessory=discord.ui.Thumbnail(media=avatar),
                )
            )
            container.add_item(
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
            )
            container.add_item(discord.ui.TextDisplay(info_text))
            if invite_url:
                container.add_item(
                    discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
                )
                container.add_item(
                    discord.ui.ActionRow(
                        discord.ui.Button(
                            url=invite_url,
                            style=discord.ButtonStyle.link,
                            label="Invite",
                        ),
                    )
                )
            self.add_item(container)

    return BotInfoView()


class Info(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(aliases=["latency", "p"])
    async def ping(self, ctx: commands.Context) -> None:
        started = time()
        message = await ctx.send(content="..")
        finished = time() - started

        await message.edit(
            content=(
                f"it took `{int(self.bot.latency * 1000)}ms` to ping "
                f"**{choice(PING_RESPONSES)}** (edit: `{finished:.2f}ms`)"
            )
        )

    @commands.command(name="botinfo", aliases=["bi", "about", "info"])
    async def botinfo(self, ctx: commands.Context) -> None:
        total_users = sum(g.member_count or 0 for g in self.bot.guilds)
        total_servers = len(self.bot.guilds)
        total_commands = _count_commands(list(self.bot.commands))
        total_lines = _count_lines()

        view = build_botinfo_view(
            self.bot,
            total_users=total_users,
            total_servers=total_servers,
            total_commands=total_commands,
            total_lines=total_lines,
        )
        await ctx.send(view=view, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="ping", description="Show bot latency")
    async def ping_slash(self, interaction: discord.Interaction) -> None:
        started = time()
        await interaction.response.send_message(content="..")
        finished = time() - started
        await interaction.edit_original_response(
            content=(
                f"it took `{int(self.bot.latency * 1000)}ms` to ping "
                f"**{choice(PING_RESPONSES)}** (edit: `{finished:.2f}ms`)"
            )
        )

    @app_commands.command(name="botinfo", description="Show bot information")
    async def botinfo_slash(self, interaction: discord.Interaction) -> None:
        total_users = sum(g.member_count or 0 for g in self.bot.guilds)
        total_servers = len(self.bot.guilds)
        total_commands = _count_commands(list(self.bot.commands))
        total_lines = _count_lines()

        view = build_botinfo_view(
            self.bot,
            total_users=total_users,
            total_servers=total_servers,
            total_commands=total_commands,
            total_lines=total_lines,
        )
        await interaction.response.send_message(view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Info(bot))
