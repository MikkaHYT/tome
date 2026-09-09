from __future__ import annotations

import inspect
import os
import time
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import EMBED_COLOR, PREFIX

DISPLAY_PREFIX = PREFIX if isinstance(PREFIX, str) else PREFIX[0]

# Predefined master categories with keywords for automatic grouping
CATEGORY_CONFIG = {
    "juice_wrld": {
        "label": "Juice WRLD",
        "description": "Leaks, stems, remasters, snippets, sessions, & audio",
        "emoji": "🎵",
        "keywords": (
            "juice", "wrld", "leak", "stem", "remaster", "instrumental",
            "session", "snippet", "snip", "makesnip", "songinfo", "cover",
            "radio", "media", "track", "music", "discog", "si"
        ),
    },
    "economy": {
        "label": "Economy",
        "description": "Wallet, bank, jobs, loans, daily rewards, & leaderboards",
        "emoji": "🪙",
        "keywords": (
            "economy", "bank", "shop", "work", "job", "wallet",
            "balance", "loan", "pay", "daily", "coin"
        ),
    },
    "gambling": {
        "label": "Gambling",
        "description": "Blackjack, mines, slots, crash, roulette, & games",
        "emoji": "🎲",
        "keywords": (
            "gambling", "gamble", "casino", "blackjack", "slot",
            "slots", "mine", "mines", "crash", "roulette", "bet", "roll"
        ),
    },
    "moderation": {
        "label": "Moderation",
        "description": "Ban, kick, mute, jail, and server security tools",
        "emoji": "🛡️",
        "keywords": (
            "moderation", "mod", "admin", "jail", "punish",
            "warn", "kick", "ban", "mute", "lock", "security"
        ),
    },
    "utility": {
        "label": "Utility & Tools",
        "description": "Embed builder, server tools, bot latency, & general utilities",
        "emoji": "🛠️",
        "keywords": (
            "utility", "util", "tools", "tool", "createembed", "embed",
            "serverlist", "server", "info", "information", "bot",
            "stats", "ping", "misc", "general", "config", "settings"
        ),
    },
}


@dataclass(slots=True)
class HelpCategory:
    key: str
    label: str
    description: str
    commands: list[commands.Command]
    emoji: str | None = None


def resolve_category_key(cog_name: str) -> str:
    normalized = cog_name.lower().replace("_", " ").replace("-", " ").strip()
    
    for cat_key, meta in CATEGORY_CONFIG.items():
        for kw in meta["keywords"]:
            if kw in normalized.split() or normalized == kw:
                return cat_key
            if len(kw) >= 4 and kw in normalized:
                return cat_key

    # Fallback to Utility & Tools so stray 1-command cogs don't clutter the dropdown
    return "utility"


def _visible_commands(cog: commands.Cog) -> list[commands.Command]:
    return [
        command
        for command in cog.get_commands()
        if not command.hidden and command.name not in {"help", "helpme", "h"}
    ]


def build_category_map(bot: commands.Bot) -> dict[str, HelpCategory]:
    grouped_commands: dict[str, list[commands.Command]] = {
        key: [] for key in CATEGORY_CONFIG
    }

    seen_command_names: set[str] = set()

    for cog_name, cog in bot.cogs.items():
        if cog_name.lower() == "help":
            continue
        if "jishaku" in (getattr(cog, "__module__", "") or "").lower():
            continue

        command_list = _visible_commands(cog)
        if not command_list:
            continue

        target_category = resolve_category_key(cog_name)

        for cmd in command_list:
            if cmd.qualified_name not in seen_command_names:
                seen_command_names.add(cmd.qualified_name)
                grouped_commands[target_category].append(cmd)

    category_map: dict[str, HelpCategory] = {}

    for key, meta in CATEGORY_CONFIG.items():
        cmds = grouped_commands.get(key, [])
        if cmds:
            category_map[key] = HelpCategory(
                key=key,
                label=meta["label"],
                description=meta["description"],
                commands=sorted(cmds, key=lambda c: c.name.lower()),
                emoji=meta["emoji"],
            )

    return category_map


def _command_description(command: commands.Command) -> str:
    text = command.description or command.help or "No description available."
    return text.split("\n")[0].strip()


def _command_permissions(command: commands.Command) -> str:
    found: list[str] = []
    current: commands.Command | None = command
    while current is not None:
        for check in getattr(current, "checks", []):
            predicate = getattr(check, "predicate", check)
            try:
                closure = inspect.getclosurevars(predicate)
            except TypeError:
                continue
            perms = closure.nonlocals.get("perms")
            if isinstance(perms, dict):
                found.extend(
                    perm.replace("_", " ").title()
                    for perm, enabled in perms.items()
                    if enabled
                )
        extra = current.extras.get("permissions")
        if extra:
            if isinstance(extra, str):
                found.append(extra)
            else:
                found.extend(extra)
        current = getattr(current, "parent", None)
    return ", ".join(sorted(set(found))) if found else "n/a"


def _format_params(command: commands.Command) -> str:
    tokens = []
    for name, param in command.clean_params.items():
        if name in {"self", "ctx"}:
            continue
        label = name.replace("_", " ")
        tokens.append(f"({label})" if param.default is param.empty else f"[{label}]")
    return ", ".join(tokens) if tokens else "n/a"


def _command_example(command: commands.Command, prefix: str) -> str:
    example = command.extras.get("example")
    if not example:
        example = getattr(command, "__original_kwargs__", {}).get("example")
    if example:
        example = str(example).strip()
        return example if example.startswith(prefix) else f"{prefix}{example}"
    help_text = command.help or ""
    for line in help_text.splitlines():
        if line.lower().startswith("example:"):
            example = line.split(":", 1)[1].strip()
            return example if example.startswith(prefix) else f"{prefix}{example}"
    return f"{prefix}{command.qualified_name}"


def _usage_syntax(command: commands.Command, prefix: str) -> str:
    tokens = []
    for name, param in command.clean_params.items():
        if name in {"self", "ctx"}:
            continue
        if param.default is param.empty:
            tokens.append(f"<{name}>")
        else:
            tokens.append(f"[{name}]")
    base = f"{prefix}{command.qualified_name}"
    return f"{base} {' '.join(tokens)}".strip() if tokens else base


def _select_options(category_map: dict[str, HelpCategory], current: str = "home") -> list[discord.SelectOption]:
    options = [
        discord.SelectOption(
            label="Home",
            description="Overview and bot information",
            emoji="🏠",
            value="home",
            default=(current == "home"),
        )
    ]
    for category in category_map.values():
        options.append(
            discord.SelectOption(
                label=category.label[:100],
                description=f"{len(category.commands)} command(s) · {category.description}"[:100],
                value=category.key,
                emoji=category.emoji,
                default=(current == category.key),
            )
        )
    return options[:25]


def build_home_view(
    bot_user: discord.ClientUser | None,
    category_map: dict[str, HelpCategory],
    select_id: str,
) -> discord.ui.LayoutView:
    name = bot_user.name if bot_user else "Timezones"
    avatar = (
        bot_user.display_avatar.url
        if bot_user
        else "https://cdn.discordapp.com/embed/avatars/0.png"
    )
    total_commands = sum(len(c.commands) for c in category_map.values())

    header = (
        f"# {name} Help\n"
        f"> Select a category from the dropdown menu to view its commands.\n"
        f"-# **{total_commands} commands** across **{len(category_map)} modules**"
    )

    overview_lines = [
        f"### **Modules Overview**",
        "> `(param)` = required · `[param]` = optional · `[n]` = subcommands",
        "",
    ]
    for category in category_map.values():
        overview_lines.append(
            f"**{category.emoji} {category.label}** · `{len(category.commands)}`\n"
            f"-# {category.description}"
        )

    overview_text = "\n".join(overview_lines)

    class HomeView(discord.ui.LayoutView):
        def __init__(self) -> None:
            super().__init__(timeout=180)
            container = discord.ui.Container(accent_color=EMBED_COLOR)
            
            container.add_item(
                discord.ui.Section(
                    discord.ui.TextDisplay(header),
                    accessory=discord.ui.Thumbnail(media=avatar),
                )
            )
            container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(discord.ui.TextDisplay(overview_text))
            container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(
                discord.ui.ActionRow(
                    discord.ui.Select(
                        placeholder="Choose a category...",
                        min_values=1,
                        max_values=1,
                        options=_select_options(category_map, current="home"),
                        custom_id=select_id,
                    )
                )
            )
            self.add_item(container)

    return HomeView()


def build_category_view(
    category: HelpCategory,
    category_map: dict[str, HelpCategory],
    bot_user: discord.ClientUser | None,
    select_id: str,
) -> discord.ui.LayoutView:
    avatar = (
        bot_user.display_avatar.url
        if bot_user
        else "https://cdn.discordapp.com/embed/avatars/0.png"
    )

    header = (
        f"# {category.emoji or '📁'} {category.label}\n"
        f"> {category.description}\n"
        f"-# Showing **{len(category.commands)}** available command(s)"
    )

    command_blocks: list[str] = []
    for cmd in category.commands:
        desc = _command_description(cmd)
        sub_tag = f" `[{len(cmd.commands)}]`" if isinstance(cmd, commands.Group) else ""
        cmd_title = f"**`{DISPLAY_PREFIX}{cmd.name}`**{sub_tag}"

        if desc and desc != "No description available.":
            command_blocks.append(f"{cmd_title}\n-# {desc}")
        else:
            command_blocks.append(f"{cmd_title}")

    # Chunk command blocks to avoid Discord text display component limits
    text_chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for block in command_blocks:
        if current_len + len(block) + 2 > 2500:
            text_chunks.append("\n\n".join(current_chunk))
            current_chunk = [block]
            current_len = len(block)
        else:
            current_chunk.append(block)
            current_len += len(block) + 2

    if current_chunk:
        text_chunks.append("\n\n".join(current_chunk))

    footer_tip = f"-# 💡 Tip: Type `{DISPLAY_PREFIX}help <command>` for detailed syntax, aliases, and examples."

    class CategoryView(discord.ui.LayoutView):
        def __init__(self) -> None:
            super().__init__(timeout=180)
            container = discord.ui.Container(accent_color=EMBED_COLOR)

            container.add_item(
                discord.ui.Section(
                    discord.ui.TextDisplay(header),
                    accessory=discord.ui.Thumbnail(media=avatar),
                )
            )
            container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

            for chunk in text_chunks:
                container.add_item(discord.ui.TextDisplay(chunk))

            container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(discord.ui.TextDisplay(footer_tip))
            container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(
                discord.ui.ActionRow(
                    discord.ui.Select(
                        placeholder="Switch category...",
                        min_values=1,
                        max_values=1,
                        options=_select_options(category_map, current=category.key),
                        custom_id=select_id,
                    )
                )
            )
            self.add_item(container)

    return CategoryView()


def build_command_embed(
    command: commands.Command,
    prefix: str,
    author: discord.abc.User,
    bot: commands.Bot,
    *,
    page: int,
    total: int,
) -> discord.Embed:
    if isinstance(command, commands.Group):
        title = f"Group: {command.qualified_name}"
    else:
        title = f"Command: {command.qualified_name}"

    embed = discord.Embed(
        title=title,
        description=_command_description(command),
        color=EMBED_COLOR,
    )
    embed.set_author(
        name=f"{bot.user.name} help" if bot.user else "help",
        icon_url=bot.user.display_avatar.url if bot.user else None,
    )
    embed.add_field(
        name="Aliases",
        value=", ".join(f"`{a}`" for a in command.aliases) if command.aliases else "None",
        inline=True,
    )
    embed.add_field(name="Parameters", value=_format_params(command), inline=True)
    embed.add_field(
        name="Permissions",
        value=_command_permissions(command),
        inline=True,
    )
    embed.add_field(
        name="Usage",
        value=(
            f"```ansi\n"
            f"\u001b[0;35mSyntax:\u001b[0m  {_usage_syntax(command, prefix)}\n"
            f"\u001b[0;35mExample:\u001b[0m {_command_example(command, prefix)}\n"
            f"```"
        ),
        inline=False,
    )
    embed.set_footer(
        text=f"Page {page}/{total} ({total} entries) · Module: {resolve_category_key(command.cog_name or '').replace('_', ' ').title()}",
        icon_url=author.display_avatar.url,
    )
    return embed


def build_command_pages(
    command: commands.Command,
    prefix: str,
    author: discord.abc.User,
    bot: commands.Bot,
) -> list[discord.Embed]:
    items: list[commands.Command] = []

    def collect(cmd: commands.Command) -> None:
        items.append(cmd)
        if isinstance(cmd, commands.Group):
            for child in sorted(cmd.commands, key=lambda c: c.name):
                collect(child)

    collect(command)
    total = len(items)
    return [
        build_command_embed(item, prefix, author, bot, page=index, total=total)
        for index, item in enumerate(items, start=1)
    ]


async def send_command_help(
    ctx: commands.Context,
    command: commands.Command | str | None = None,
) -> discord.Message | None:
    if command is None:
        target = ctx.command
    elif isinstance(command, str):
        target = ctx.bot.get_command(command)
    else:
        target = command

    if not target or target.hidden:
        view = discord.ui.LayoutView(timeout=60)
        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            discord.ui.TextDisplay("❌ No help is available for that command.")
        )
        view.add_item(container)
        return await ctx.send(view=view)

    pages = build_command_pages(target, ctx.clean_prefix, ctx.author, ctx.bot)
    if not pages:
        return None

    if len(pages) == 1:
        return await ctx.send(embed=pages[0])

    paginator = HelpPaginator(ctx, pages)
    return await paginator.start()


class HelpSkipModal(discord.ui.Modal, title="Skip to page"):
    page_number = discord.ui.TextInput(
        label="Page number",
        placeholder="1",
        max_length=4,
    )

    def __init__(self, view: "HelpPaginator") -> None:
        super().__init__()
        self.help_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.page_number.value).strip()
        if not raw.isdigit():
            await interaction.response.send_message(
                "Enter a valid page number.",
                ephemeral=True,
            )
            return
        page = int(raw) - 1
        if not (0 <= page < len(self.help_view.pages)):
            await interaction.response.send_message(
                f"Choose a page between 1 and {len(self.help_view.pages)}.",
                ephemeral=True,
            )
            return
        self.help_view.page = page
        await interaction.response.edit_message(
            embed=self.help_view.pages[self.help_view.page],
            view=self.help_view,
        )


class HelpPaginator(discord.ui.View):
    def __init__(
        self,
        ctx: commands.Context,
        pages: list[discord.Embed],
    ) -> None:
        super().__init__(timeout=180)
        self.ctx = ctx
        self.pages = pages
        self.page = 0
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "This help menu belongs to someone else.",
                ephemeral=True,
            )
            return False
        return True

    async def _edit(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=self.pages[self.page],
            view=self,
        )

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.primary)
    async def previous(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.page = (self.page - 1) % len(self.pages)
        await self._edit(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.page = (self.page + 1) % len(self.pages)
        await self._edit(interaction)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(HelpSkipModal(self))

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.message.delete()
        self.stop()

    async def start(self) -> discord.Message:
        self.message = await self.ctx.send(
            embed=self.pages[self.page],
            view=self,
        )
        return self.message


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._sessions: dict[str, dict] = {}
        self._session_cleanup.start()

    def cog_unload(self) -> None:
        self._session_cleanup.cancel()

    @tasks.loop(minutes=10)
    async def _session_cleanup(self) -> None:
        cutoff = time.monotonic() - 300
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if session.get("created_at", 0) < cutoff
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)

    @commands.command(name="help", aliases=["h"])
    async def help_command(
        self,
        ctx: commands.Context,
        *,
        command: Optional[str] = None,
    ) -> None:
        if command:
            target = self.bot.get_command(command.lower())
            if not target or target.hidden:
                view = discord.ui.LayoutView(timeout=60)
                container = discord.ui.Container(accent_color=EMBED_COLOR)
                container.add_item(
                    discord.ui.TextDisplay(
                        f"❌ {ctx.author.mention}: No command named **{command}** was found."
                    )
                )
                view.add_item(container)
                await ctx.send(view=view)
                return

            await send_command_help(ctx, target)
            return

        category_map = build_category_map(self.bot)
        if not category_map:
            view = discord.ui.LayoutView(timeout=60)
            container = discord.ui.Container(accent_color=EMBED_COLOR)
            container.add_item(
                discord.ui.TextDisplay("No commands are currently loaded.")
            )
            view.add_item(container)
            await ctx.send(view=view)
            return

        select_id = os.urandom(16).hex()
        self._sessions[select_id] = {
            "user_id": ctx.author.id,
            "category_map": category_map,
            "created_at": time.monotonic(),
        }
        view = build_home_view(self.bot.user, category_map, select_id)
        await ctx.send(view=view)

    @app_commands.command(name="help", description="Show bot help and command categories")
    @app_commands.describe(command="Optional command name for detailed help")
    async def help_slash(
        self,
        interaction: discord.Interaction,
        command: str | None = None,
    ) -> None:
        if command:
            target = self.bot.get_command(command.lower())
            if not target or target.hidden:
                view = discord.ui.LayoutView(timeout=60)
                container = discord.ui.Container(accent_color=EMBED_COLOR)
                container.add_item(
                    discord.ui.TextDisplay(
                        f"❌ No command named **{command}** was found."
                    )
                )
                view.add_item(container)
                await interaction.response.send_message(view=view)
                return

            await interaction.response.defer()
            pages = build_command_pages(target, DISPLAY_PREFIX, interaction.user, self.bot)
            if not pages:
                await interaction.followup.send("No help available for that command.")
                return
            if len(pages) == 1:
                await interaction.followup.send(embed=pages[0])
            else:
                await interaction.followup.send(embed=pages[0])
            return

        category_map = build_category_map(self.bot)
        if not category_map:
            view = discord.ui.LayoutView(timeout=60)
            container = discord.ui.Container(accent_color=EMBED_COLOR)
            container.add_item(
                discord.ui.TextDisplay("No commands are currently loaded.")
            )
            view.add_item(container)
            await interaction.response.send_message(view=view)
            return

        select_id = os.urandom(16).hex()
        self._sessions[select_id] = {
            "user_id": interaction.user.id,
            "category_map": category_map,
            "created_at": time.monotonic(),
        }
        view = build_home_view(self.bot.user, category_map, select_id)
        await interaction.response.send_message(view=view)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return

        data = interaction.data or {}
        if data.get("component_type") != 3:
            return

        custom_id = data.get("custom_id", "")
        session = self._sessions.get(custom_id)
        if not session:
            return

        if interaction.user.id != session["user_id"]:
            await interaction.response.send_message(
                "This help menu belongs to someone else.",
                ephemeral=True,
            )
            return

        category_map = session["category_map"]
        selected = (data.get("values") or [None])[0]

        new_select_id = os.urandom(16).hex()
        self._sessions[new_select_id] = {
            "user_id": session["user_id"],
            "category_map": category_map,
            "created_at": time.monotonic(),
        }
        del self._sessions[custom_id]

        if selected == "home":
            view = build_home_view(self.bot.user, category_map, new_select_id)
            await interaction.response.edit_message(view=view)
            return

        category = category_map.get(selected)
        if not category:
            await interaction.response.defer()
            return

        view = build_category_view(category, category_map, self.bot.user, new_select_id)
        await interaction.response.edit_message(view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
