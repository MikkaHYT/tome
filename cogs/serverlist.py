from __future__ import annotations

import discord
from discord.ext import commands

from config import EMBED_COLOR, get_bot_owner_id
TEXT_CHUNK_LIMIT = 3600
VIEW_TIMEOUT = 180


def text_display(content: str) -> discord.ui.TextDisplay:
    return discord.ui.TextDisplay(content=content)


def small_separator() -> discord.ui.Separator:
    return discord.ui.Separator(spacing=discord.SeparatorSpacing.small)


def simple_view(content: str, *, timeout: int = 60) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=timeout)
    container = discord.ui.Container(accent_color=EMBED_COLOR)
    container.add_item(text_display(content))
    view.add_item(container)
    return view


def format_guild_entry(guild: discord.Guild) -> str:
    owner = guild.owner
    owner_text = f"{owner.mention} (`{owner.id}`)" if owner else "Unknown"
    members = guild.member_count or 0
    return (
        f"**{guild.name}**\n"
        f"`{guild.id}` · {members:,} members · Owner {owner_text}"
    )


def chunk_text(lines: list[str], limit: int = TEXT_CHUNK_LIMIT) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        piece = f"{line}\n\n"
        if current_len + len(piece) > limit and current:
            chunks.append("".join(current).rstrip())
            current = []
            current_len = 0
        current.append(piece)
        current_len += len(piece)

    if current:
        chunks.append("".join(current).rstrip())

    return chunks or [""]


class ServerListView(discord.ui.LayoutView):
    def __init__(self, pages: list[str], total: int, invoker_id: int):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.pages = pages
        self.total = total
        self.invoker_id = invoker_id
        self.page = 0
        self.message: discord.Message | None = None
        self._render()

    def _header(self) -> str:
        if len(self.pages) > 1:
            return (
                f"# Bot Servers\n"
                f"-# **{self.total}** server(s) · Page {self.page + 1}/{len(self.pages)}"
            )
        return f"# Bot Servers\n-# **{self.total}** server(s)"

    def _render(self) -> None:
        self.clear_items()

        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(text_display(self._header()))
        container.add_item(small_separator())
        container.add_item(text_display(self.pages[self.page]))
        self.add_item(container)

        if len(self.pages) > 1:
            self.add_item(
                discord.ui.ActionRow(
                    ServerListPrevious(self),
                    ServerListNext(self),
                )
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "❌ Only the person who ran the command can use these buttons.",
                ephemeral=True,
            )
            return False
        return True


class ServerListPrevious(discord.ui.Button):
    def __init__(self, view: ServerListView):
        super().__init__(label="Previous", style=discord.ButtonStyle.secondary)
        self.list_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        self.list_view.page = (self.list_view.page - 1) % len(self.list_view.pages)
        self.list_view._render()
        await interaction.response.edit_message(view=self.list_view)


class ServerListNext(discord.ui.Button):
    def __init__(self, view: ServerListView):
        super().__init__(label="Next", style=discord.ButtonStyle.secondary)
        self.list_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        self.list_view.page = (self.list_view.page + 1) % len(self.list_view.pages)
        self.list_view._render()
        await interaction.response.edit_message(view=self.list_view)


class ServerList(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="servers")
    async def servers(self, ctx: commands.Context) -> None:
        allowed_ids = {217389696043450368, 1354629588965134376}
        if ctx.author.id not in allowed_ids:
            await ctx.send(
                view=simple_view(
                    f"❌ {ctx.author.mention} · You do not have permission to use this command."
                )
            )
            return

        guilds = sorted(self.bot.guilds, key=lambda guild: guild.name.lower())

        if not guilds:
            await ctx.send(
                view=simple_view(
                    "# Bot Servers\n-# The bot is not in any servers right now."
                )
            )
            return

        pages = chunk_text([format_guild_entry(guild) for guild in guilds])
        view = ServerListView(pages, len(guilds), ctx.author.id)
        view.message = await ctx.send(view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerList(bot))
