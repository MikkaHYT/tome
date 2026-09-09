from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import discord
from discord.ext import commands

from config import EMBED_COLOR

logger = logging.getLogger(__name__)

VIEW_TIMEOUT = 900
MAX_BUTTONS = 5
MAX_TITLE_LENGTH = 256
MAX_DESCRIPTION_LENGTH = 4000
MAX_FOOTER_LENGTH = 100
MAX_BUTTON_LABEL_LENGTH = 80

BUTTON_STYLES = {
    "link": discord.ButtonStyle.link,
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}

CHANNEL_TYPES = [
    discord.ChannelType.text,
    discord.ChannelType.news,
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def valid_http_url(value: Optional[str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    try:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None
    except Exception:
        return None
    return value


def safe_emoji(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        discord.PartialEmoji.from_str(value)
    except Exception:
        return None
    return value


def to_int_color(value) -> int:
    """Normalize an int or a discord.Colour/Color into a plain int."""
    if isinstance(value, discord.Colour):
        return value.value
    return int(value)


def parse_hex_color(value: str) -> Optional[int]:
    value = value.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        return None
    return int(value, 16)


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


def apply_url_field(new_value: str, current: Optional[str], label: str, warnings: list[str]) -> Optional[str]:
    new_value = new_value.strip()
    if not new_value:
        return None
    if valid_http_url(new_value):
        return new_value
    warnings.append(label)
    return current


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class ButtonSpec:
    style: str = "link"
    label: str = "Button"
    emoji: Optional[str] = None
    url: Optional[str] = None
    response: Optional[str] = None


@dataclass
class EmbedDraft:
    title: str = ""
    description: str = ""
    footer: str = ""
    color: int = field(default_factory=lambda: to_int_color(EMBED_COLOR))
    image_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    video_url: Optional[str] = None
    channel_id: Optional[int] = None
    buttons: list[ButtonSpec] = field(default_factory=list)

    def has_content(self) -> bool:
        return bool(self.title or self.description or self.image_url or self.video_url)


# ---------------------------------------------------------------------------
# Shared rendering
# ---------------------------------------------------------------------------

def add_preview_items(container: discord.ui.Container, draft: EmbedDraft) -> None:
    lines = []
    if draft.title:
        lines.append(f"### **{draft.title}**")
    if draft.description:
        lines.append(draft.description[:MAX_DESCRIPTION_LENGTH])
    header_text = "\n".join(lines) if lines else "*Nothing set yet -- use Edit Text below.*"

    if draft.thumbnail_url:
        container.add_item(
            discord.ui.Section(
                text_display(header_text),
                accessory=discord.ui.Thumbnail(media=draft.thumbnail_url),
            )
        )
    else:
        container.add_item(text_display(header_text))

    media_items = []
    if draft.image_url:
        media_items.append(discord.MediaGalleryItem(media=draft.image_url))
    if draft.video_url:
        media_items.append(discord.MediaGalleryItem(media=draft.video_url))
    if media_items:
        container.add_item(discord.ui.MediaGallery(*media_items))

    if draft.footer:
        container.add_item(small_separator())
        container.add_item(text_display(f"-# {draft.footer}"))


def build_custom_button(spec: ButtonSpec) -> discord.ui.Button:
    if spec.style == "link":
        return discord.ui.Button(
            label=spec.label,
            style=discord.ButtonStyle.link,
            url=spec.url,
            emoji=spec.emoji,
        )

    # Non-link buttons need a custom_id so they can be dispatched (and,
    # if desired, re-registered as a persistent view via bot.add_view).
    button: discord.ui.Button = discord.ui.Button(
        label=spec.label,
        style=BUTTON_STYLES.get(spec.style, discord.ButtonStyle.secondary),
        emoji=spec.emoji,
        custom_id=f"createembed:{uuid.uuid4().hex}",
    )

    async def callback(interaction: discord.Interaction, response: Optional[str] = spec.response) -> None:
        await interaction.response.send_message(
            response or "This button has no response configured.",
            ephemeral=True,
        )

    button.callback = callback
    return button


def check_author(interaction: discord.Interaction, author_id: int) -> bool:
    return interaction.user.id == author_id


NOT_AUTHOR_MESSAGE = "❌ Only the person who ran the command can use this."


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class EditTextModal(discord.ui.Modal, title="Edit Text"):
    def __init__(self, *, cog: "CreateEmbed", author_id: int, draft: EmbedDraft, parent_view: "CreateEmbedView") -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.author_id = author_id
        self.draft = draft
        self.parent_view = parent_view

        self.title_input = discord.ui.TextInput(
            label="Title",
            style=discord.TextStyle.short,
            required=False,
            max_length=MAX_TITLE_LENGTH,
            default=draft.title,
        )
        self.description_input = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=MAX_DESCRIPTION_LENGTH,
            default=draft.description,
        )
        self.footer_input = discord.ui.TextInput(
            label="Footer",
            style=discord.TextStyle.short,
            required=False,
            max_length=MAX_FOOTER_LENGTH,
            default=draft.footer,
        )
        self.add_item(self.title_input)
        self.add_item(self.description_input)
        self.add_item(self.footer_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.draft.title = self.title_input.value.strip()
        self.draft.description = self.description_input.value.strip()
        self.draft.footer = self.footer_input.value.strip()

        self.parent_view.stop()
        new_view = CreateEmbedView(self.cog, self.author_id, self.draft)
        await interaction.response.edit_message(view=new_view)
        new_view.message = interaction.message


class EditMediaModal(discord.ui.Modal, title="Edit Images & Video"):
    def __init__(self, *, cog: "CreateEmbed", author_id: int, draft: EmbedDraft, parent_view: "CreateEmbedView") -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.author_id = author_id
        self.draft = draft
        self.parent_view = parent_view

        self.image_input = discord.ui.TextInput(
            label="Image URL (big, main image)",
            style=discord.TextStyle.short,
            required=False,
            default=draft.image_url or "",
        )
        self.thumbnail_input = discord.ui.TextInput(
            label="Thumbnail URL (small, top-right)",
            style=discord.TextStyle.short,
            required=False,
            default=draft.thumbnail_url or "",
        )
        self.video_input = discord.ui.TextInput(
            label="Video URL",
            style=discord.TextStyle.short,
            required=False,
            default=draft.video_url or "",
        )
        self.add_item(self.image_input)
        self.add_item(self.thumbnail_input)
        self.add_item(self.video_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        warnings: list[str] = []
        self.draft.image_url = apply_url_field(self.image_input.value, self.draft.image_url, "Image URL", warnings)
        self.draft.thumbnail_url = apply_url_field(self.thumbnail_input.value, self.draft.thumbnail_url, "Thumbnail URL", warnings)
        self.draft.video_url = apply_url_field(self.video_input.value, self.draft.video_url, "Video URL", warnings)

        self.parent_view.stop()
        new_view = CreateEmbedView(self.cog, self.author_id, self.draft)
        await interaction.response.edit_message(view=new_view)
        new_view.message = interaction.message

        if warnings:
            await interaction.followup.send(
                f"⚠️ Ignored invalid link(s): {', '.join(warnings)}. Links must start with `http://` or `https://`.",
                ephemeral=True,
            )


class SetColorModal(discord.ui.Modal, title="Set Accent Color"):
    def __init__(self, *, cog: "CreateEmbed", author_id: int, draft: EmbedDraft, parent_view: "CreateEmbedView") -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.author_id = author_id
        self.draft = draft
        self.parent_view = parent_view

        self.color_input = discord.ui.TextInput(
            label="Hex color (e.g. #5865F2)",
            style=discord.TextStyle.short,
            required=True,
            max_length=7,
            default=f"#{to_int_color(draft.color):06X}",
        )
        self.add_item(self.color_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        parsed = parse_hex_color(self.color_input.value)
        if parsed is None:
            await interaction.response.send_message(
                "❌ That's not a valid hex color. Try something like `#5865F2`.",
                ephemeral=True,
            )
            return

        self.draft.color = parsed
        self.parent_view.stop()
        new_view = CreateEmbedView(self.cog, self.author_id, self.draft)
        await interaction.response.edit_message(view=new_view)
        new_view.message = interaction.message


class AddButtonModal(discord.ui.Modal, title="Add Button"):
    def __init__(self, *, cog: "CreateEmbed", author_id: int, draft: EmbedDraft, parent_view: "CreateEmbedView") -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.author_id = author_id
        self.draft = draft
        self.parent_view = parent_view

        self.label_input = discord.ui.TextInput(
            label="Button label",
            style=discord.TextStyle.short,
            required=True,
            max_length=MAX_BUTTON_LABEL_LENGTH,
        )
        self.style_input = discord.ui.TextInput(
            label="Button Style",
            style=discord.TextStyle.short,
            required=True,
            default="link",
            placeholder="link, primary, secondary, success, or danger",
        )
        self.url_input = discord.ui.TextInput(
            label="URL (only needed for 'link' style)",
            style=discord.TextStyle.short,
            required=False,
        )
        self.response_input = discord.ui.TextInput(
            label="Response text (only for non-link styles)",
            style=discord.TextStyle.paragraph,
            required=False,
        )
        self.emoji_input = discord.ui.TextInput(
            label="Emoji (optional)",
            style=discord.TextStyle.short,
            required=False,
        )
        self.add_item(self.label_input)
        self.add_item(self.style_input)
        self.add_item(self.url_input)
        self.add_item(self.response_input)
        self.add_item(self.emoji_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if len(self.draft.buttons) >= MAX_BUTTONS:
            await interaction.response.send_message(
                f"❌ You can only have up to {MAX_BUTTONS} buttons.",
                ephemeral=True,
            )
            return

        style = self.style_input.value.strip().lower()
        if style not in BUTTON_STYLES:
            await interaction.response.send_message(
                "❌ Unknown style. Use one of: `link`, `primary`, `secondary`, `success`, `danger`.",
                ephemeral=True,
            )
            return

        label = self.label_input.value.strip() or "Button"
        url = self.url_input.value.strip() or None
        response_text = self.response_input.value.strip() or None
        emoji = safe_emoji(self.emoji_input.value.strip() or None)

        if style == "link":
            if not url or not valid_http_url(url):
                await interaction.response.send_message(
                    "❌ Link buttons need a valid `http(s)://` URL.",
                    ephemeral=True,
                )
                return
        else:
            if not response_text:
                await interaction.response.send_message(
                    "❌ Non-link buttons need response text to show when someone clicks it.",
                    ephemeral=True,
                )
                return

        self.draft.buttons.append(
            ButtonSpec(
                style=style,
                label=label[:MAX_BUTTON_LABEL_LENGTH],
                emoji=emoji,
                url=url,
                response=response_text,
            )
        )

        self.parent_view.stop()
        new_view = CreateEmbedView(self.cog, self.author_id, self.draft)
        await interaction.response.edit_message(view=new_view)
        new_view.message = interaction.message


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------

class ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self, *, cog: "CreateEmbed", author_id: int, draft: EmbedDraft) -> None:
        self.cog = cog
        self.author_id = author_id
        self.draft = draft

        default_values = [discord.Object(id=draft.channel_id)] if draft.channel_id else None

        super().__init__(
            placeholder="Choose a channel to post in...",
            channel_types=CHANNEL_TYPES,
            min_values=1,
            max_values=1,
            default_values=default_values,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not check_author(interaction, self.author_id):
            await interaction.response.send_message(NOT_AUTHOR_MESSAGE, ephemeral=True)
            return

        self.draft.channel_id = self.values[0].id
        if self.view:
            self.view.stop()

        new_view = CreateEmbedView(self.cog, self.author_id, self.draft)
        await interaction.response.edit_message(view=new_view)
        new_view.message = interaction.message


class OpenModalButton(discord.ui.Button):
    """Generic button that just opens one of the editing modals above."""

    def __init__(self, *, label: str, modal_cls: type, cog: "CreateEmbed", author_id: int, draft: EmbedDraft, style: discord.ButtonStyle = discord.ButtonStyle.secondary) -> None:
        super().__init__(label=label, style=style)
        self.modal_cls = modal_cls
        self.cog = cog
        self.author_id = author_id
        self.draft = draft

    async def callback(self, interaction: discord.Interaction) -> None:
        if not check_author(interaction, self.author_id):
            await interaction.response.send_message(NOT_AUTHOR_MESSAGE, ephemeral=True)
            return

        modal = self.modal_cls(cog=self.cog, author_id=self.author_id, draft=self.draft, parent_view=self.view)
        await interaction.response.send_modal(modal)


class AddButtonButton(discord.ui.Button):
    def __init__(self, *, cog: "CreateEmbed", author_id: int, draft: EmbedDraft) -> None:
        super().__init__(label="Add Button", style=discord.ButtonStyle.secondary)
        self.cog = cog
        self.author_id = author_id
        self.draft = draft

    async def callback(self, interaction: discord.Interaction) -> None:
        if not check_author(interaction, self.author_id):
            await interaction.response.send_message(NOT_AUTHOR_MESSAGE, ephemeral=True)
            return

        if len(self.draft.buttons) >= MAX_BUTTONS:
            await interaction.response.send_message(
                f"❌ You can only have up to {MAX_BUTTONS} buttons.",
                ephemeral=True,
            )
            return

        modal = AddButtonModal(cog=self.cog, author_id=self.author_id, draft=self.draft, parent_view=self.view)
        await interaction.response.send_modal(modal)


class RemoveButtonButton(discord.ui.Button):
    def __init__(self, *, cog: "CreateEmbed", author_id: int, draft: EmbedDraft) -> None:
        super().__init__(label="Remove Last Button", style=discord.ButtonStyle.secondary)
        self.cog = cog
        self.author_id = author_id
        self.draft = draft

    async def callback(self, interaction: discord.Interaction) -> None:
        if not check_author(interaction, self.author_id):
            await interaction.response.send_message(NOT_AUTHOR_MESSAGE, ephemeral=True)
            return

        if not self.draft.buttons:
            await interaction.response.send_message("❌ There are no buttons to remove.", ephemeral=True)
            return

        self.draft.buttons.pop()
        if self.view:
            self.view.stop()

        new_view = CreateEmbedView(self.cog, self.author_id, self.draft)
        await interaction.response.edit_message(view=new_view)
        new_view.message = interaction.message


class ResetButton(discord.ui.Button):
    def __init__(self, *, cog: "CreateEmbed", author_id: int, draft: EmbedDraft) -> None:
        super().__init__(label="Reset", style=discord.ButtonStyle.secondary)
        self.cog = cog
        self.author_id = author_id
        self.draft = draft

    async def callback(self, interaction: discord.Interaction) -> None:
        if not check_author(interaction, self.author_id):
            await interaction.response.send_message(NOT_AUTHOR_MESSAGE, ephemeral=True)
            return

        fresh = EmbedDraft()
        if self.view:
            self.view.stop()

        new_view = CreateEmbedView(self.cog, self.author_id, fresh)
        await interaction.response.edit_message(view=new_view)
        new_view.message = interaction.message


class CancelButton(discord.ui.Button):
    def __init__(self, *, author_id: int) -> None:
        super().__init__(label="Cancel", style=discord.ButtonStyle.danger)
        self.author_id = author_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if not check_author(interaction, self.author_id):
            await interaction.response.send_message(NOT_AUTHOR_MESSAGE, ephemeral=True)
            return

        if self.view:
            self.view.stop()

        await interaction.response.edit_message(view=simple_view("❌ Embed builder cancelled.", timeout=10))


class PostButton(discord.ui.Button):
    def __init__(self, *, cog: "CreateEmbed", author_id: int, draft: EmbedDraft) -> None:
        super().__init__(label="Post", style=discord.ButtonStyle.success)
        self.cog = cog
        self.author_id = author_id
        self.draft = draft

    async def callback(self, interaction: discord.Interaction) -> None:
        if not check_author(interaction, self.author_id):
            await interaction.response.send_message(NOT_AUTHOR_MESSAGE, ephemeral=True)
            return

        if not self.draft.has_content():
            await interaction.response.send_message(
                "❌ Add at least a title, description, image, or video before posting.",
                ephemeral=True,
            )
            return

        if not self.draft.channel_id or not interaction.guild:
            await interaction.response.send_message(
                "❌ Pick a channel from the dropdown before posting.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel_or_thread(self.draft.channel_id)
        if channel is None:
            await interaction.response.send_message(
                "❌ That channel couldn't be found. Pick a channel again.",
                ephemeral=True,
            )
            return

        me = interaction.guild.me
        permissions = channel.permissions_for(me) if me else None
        if permissions is None or not permissions.send_messages:
            await interaction.response.send_message(
                f"❌ I don't have permission to send messages in {channel.mention}.",
                ephemeral=True,
            )
            return

        final_view = FinalEmbedView(self.draft)
        try:
            await channel.send(view=final_view)
        except discord.HTTPException:
            logger.exception("Discord rejected the built embed")
            await interaction.response.send_message(
                "❌ Discord rejected that message -- double check your image/video links.",
                ephemeral=True,
            )
            return

        if final_view.is_persistent():
            # Keeps the response-style buttons working for as long as this
            # bot process stays running. To survive a full bot restart you'd
            # need to persist the ButtonSpec data somewhere (e.g. a database)
            # and re-add a matching view on startup.
            self.cog.bot.add_view(final_view)

        if self.view:
            self.view.stop()

        await interaction.response.edit_message(
            view=simple_view(f"✅ Posted to {channel.mention}.", timeout=10)
        )


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class CreateEmbedView(discord.ui.LayoutView):
    def __init__(self, cog: "CreateEmbed", author_id: int, draft: EmbedDraft, timeout: int = VIEW_TIMEOUT) -> None:
        super().__init__(timeout=timeout)
        self.cog = cog
        self.author_id = author_id
        self.draft = draft
        self.message: Optional[discord.Message] = None

        container = discord.ui.Container(accent_color=draft.color)
        container.add_item(
            text_display(
                "# Command: createembed\n"
                "Build your message below, then hit **Post** when you're happy with it."
            )
        )
        container.add_item(small_separator())

        add_preview_items(container, draft)
        container.add_item(small_separator())

        container.add_item(
            discord.ui.ActionRow(ChannelPicker(cog=cog, author_id=author_id, draft=draft))
        )

        if draft.buttons:
            container.add_item(
                discord.ui.ActionRow(*(build_custom_button(spec) for spec in draft.buttons[:MAX_BUTTONS]))
            )

        container.add_item(
            discord.ui.ActionRow(
                OpenModalButton(label="Edit Text", modal_cls=EditTextModal, cog=cog, author_id=author_id, draft=draft, style=discord.ButtonStyle.primary),
                OpenModalButton(label="Edit Media", modal_cls=EditMediaModal, cog=cog, author_id=author_id, draft=draft),
                OpenModalButton(label="Set Color", modal_cls=SetColorModal, cog=cog, author_id=author_id, draft=draft),
                AddButtonButton(cog=cog, author_id=author_id, draft=draft),
                RemoveButtonButton(cog=cog, author_id=author_id, draft=draft),
            )
        )

        container.add_item(
            discord.ui.ActionRow(
                PostButton(cog=cog, author_id=author_id, draft=draft),
                ResetButton(cog=cog, author_id=author_id, draft=draft),
                CancelButton(author_id=author_id),
            )
        )

        self.add_item(container)

    async def on_timeout(self) -> None:
        if not self.message:
            return
        try:
            await self.message.edit(
                view=simple_view("⏱️ Embed builder timed out. Run the command again to keep editing.", timeout=10)
            )
        except Exception:
            pass


class FinalEmbedView(discord.ui.LayoutView):
    """The view actually posted to the target channel."""

    def __init__(self, draft: EmbedDraft) -> None:
        self._persistent = any(button.style != "link" for button in draft.buttons)
        super().__init__(timeout=None if self._persistent else VIEW_TIMEOUT)

        container = discord.ui.Container(accent_color=draft.color)
        add_preview_items(container, draft)

        if draft.buttons:
            container.add_item(small_separator())
            container.add_item(
                discord.ui.ActionRow(*(build_custom_button(spec) for spec in draft.buttons[:MAX_BUTTONS]))
            )

        self.add_item(container)

    def is_persistent(self) -> bool:
        return self._persistent


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class CreateEmbed(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="createembed", aliases=["embedbuilder", "buildembed", "ce"])
    @commands.has_permissions(manage_messages=True)
    async def createembed(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send(view=simple_view("❌ This command can only be used in a server."))
            return

        draft = EmbedDraft()
        view = CreateEmbedView(self, ctx.author.id, draft)
        view.message = await ctx.send(view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CreateEmbed(bot))
