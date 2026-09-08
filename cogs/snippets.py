from __future__ import annotations

import asyncio
import difflib
import html
import json
import logging
import re
from typing import Any, Optional
from urllib.parse import quote, unquote

import aiohttp
import discord
from discord.ext import commands

from config import EMBED_COLOR
from utils.core.errors import USAGE_ERRORS, send_command_error, send_usage_help
from utils.core.helpers import SendHelp

logger = logging.getLogger(__name__)

API_ROOT = "https://juicewrldapi.com"
API_BASE = f"{API_ROOT}/juicewrld"
BROWSE_ENDPOINT = f"{API_BASE}/files/browse/"
DOWNLOAD_ENDPOINT = f"{API_BASE}/files/download/"

REQUEST_TIMEOUT = 30
FUZZY_THRESHOLD = 0.55
SELECT_TIMEOUT = 120
MAX_GALLERY_ITEMS = 10

VIDEO_EXTENSIONS = (
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def clean_title(title: str) -> str:
    clean = re.sub(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}", "", title)
    clean = re.sub(r"\s+(?:feat|ft|with|prod)\.?\s+.*$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"[()\[\]{}]", "", clean)
    return re.sub(r"\s+", " ", clean).strip() or title


def normalize(value: str) -> str:
    if not value:
        return ""
    val = clean_title(html.unescape(unquote(str(value)))).lower()
    return re.sub(r"[^\w\s]", " ", val).strip()


def make_download_url(path: str) -> str:
    clean_p = path.strip().lstrip("/")
    return f"{DOWNLOAD_ENDPOINT}?path={quote(clean_p, safe='')}"


def parse_api_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    pre_match = re.search(r"<pre[^>]*>(.*?)</pre>", text, re.DOTALL | re.IGNORECASE)
    if pre_match:
        inner = html.unescape(pre_match.group(1).strip())
        try:
            return json.loads(inner)
        except Exception:
            pass

    return None


def format_inline_fields(*pairs: tuple[str, str], separator: str = "  ·  ") -> str | None:
    parts = [
        f"**{label}** · {value}"
        for label, value in pairs
        if value
    ]
    return separator.join(parts) if parts else None


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


class SnippetView(discord.ui.LayoutView):
    def __init__(self, song_title: str, era_name: str, files: list[dict[str, Any]]):
        super().__init__(timeout=300)

        container = discord.ui.Container(accent_color=EMBED_COLOR)
        title = clean_text(song_title) or "Unknown Song"
        era = clean_text(era_name) or "Unknown Era"

        header_lines = [f"# {title}"]
        overview = format_inline_fields(("Era", era))
        if overview:
            header_lines.append(overview)

        container.add_item(text_display("\n".join(header_lines)))
        container.add_item(small_separator())

        if not files:
            container.add_item(
                text_display("❌ No video snippets found in this folder.")
            )
            self.add_item(container)
            return

        count = len(files)
        summary = format_inline_fields(
            ("Snippets", f"{count} video{'s' if count != 1 else ''}"),
        )
        container.add_item(text_display(summary or f"🎬 **{count}** snippet video(s)"))

        shown = files[:MAX_GALLERY_ITEMS]
        gallery_items = [
            discord.MediaGalleryItem(media=item["download_url"], description=item.get("name"))
            for item in shown
            if item.get("download_url")
        ]

        if gallery_items:
            container.add_item(small_separator())
            container.add_item(discord.ui.MediaGallery(*gallery_items))

        if count > MAX_GALLERY_ITEMS:
            container.add_item(
                text_display(f"-# Showing {MAX_GALLERY_ITEMS} of {count} snippets.")
            )

        self.add_item(container)


class SongSelect(discord.ui.Select):
    def __init__(self, candidate_folders: list[dict[str, Any]], author_id: int, cog: "Snippets"):
        self.candidate_folders = candidate_folders
        self.author_id = author_id
        self.cog = cog

        options = []
        for idx, item in enumerate(candidate_folders[:25]):
            raw_name = clean_text(item.get("name"))
            era = clean_text(item.get("era", "Unknown Era"))
            count = item.get("item_count", 0)

            label = raw_name[:100]
            desc = f"Era · {era} · {count} item{'s' if count != 1 else ''}"[:100]

            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(idx),
                    description=desc,
                )
            )

        super().__init__(
            placeholder="Choose the correct snippet folder...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the person who ran the command can choose.",
                ephemeral=True,
            )
            return

        if self.view:
            self.view.selected = True
            self.view.stop()

        await interaction.response.defer()

        chosen = self.candidate_folders[int(self.values[0])]
        folder_path = chosen.get("path", "")
        folder_name = chosen.get("name", "Unknown Song")
        era_name = chosen.get("era", "")

        files = await self.cog.fetch_folder_snippets(folder_path)
        view = SnippetView(song_title=folder_name, era_name=era_name, files=files)
        await interaction.edit_original_response(view=view)


class SongSelectView(discord.ui.LayoutView):
    def __init__(self, query: str, candidate_folders: list[dict[str, Any]], author_id: int, cog: "Snippets"):
        super().__init__(timeout=SELECT_TIMEOUT)
        self.selected = False
        self.message: Optional[discord.Message] = None

        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"# Command: snip\n"
                f"Found **{len(candidate_folders)}** matches for **{query}**.\n"
                f"-# Pick the correct folder below."
            )
        )
        container.add_item(discord.ui.ActionRow(SongSelect(candidate_folders, author_id, cog)))
        self.add_item(container)

    async def on_timeout(self):
        if self.selected or not self.message:
            return
        try:
            await self.message.edit(
                view=simple_view("⏱️ Selection timed out. Please run the command again.", timeout=10)
            )
        except Exception:
            pass


class Snippets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self.session_lock = asyncio.Lock()

        self._eras_cache: list[dict[str, Any]] = []
        self._song_folders_cache: list[dict[str, Any]] = []
        self._cache_timestamp: float = 0

    async def get_session(self) -> aiohttp.ClientSession:
        async with self.session_lock:
            if self.session and not self.session.closed:
                return self.session

            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                headers=HEADERS,
            )
            return self.session

    def cog_unload(self):
        async def close():
            if self.session and not self.session.closed:
                await self.session.close()

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(close())
        except RuntimeError:
            pass

    async def browse_path(self, path: str) -> list[dict[str, Any]]:
        session = await self.get_session()
        params = {"path": path}

        try:
            async with session.get(BROWSE_ENDPOINT, params=params, headers=HEADERS) as resp:
                if resp.status != 200:
                    return []
                text = await resp.text()
                data = parse_api_json(text)
                if isinstance(data, dict):
                    return data.get("items", [])
                return []
        except Exception as exc:
            logger.warning("[Snippets] Browse failed on path '%s': %s", path, exc)
            return []

    async def _ensure_folder_index(self):
        now = asyncio.get_running_loop().time()
        if self._song_folders_cache and (now - self._cache_timestamp < 900):
            return

        if not self._eras_cache:
            top_items = await self.browse_path("Snippets")
            self._eras_cache = [
                item for item in top_items
                if item.get("type") == "directory"
            ]

        tasks = [self.browse_path(era["path"]) for era in self._eras_cache]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_song_folders = []
        for era_item, era_result in zip(self._eras_cache, results):
            if isinstance(era_result, list):
                for item in era_result:
                    if item.get("type") == "directory":
                        all_song_folders.append({
                            "name": item.get("name", ""),
                            "path": item.get("path", ""),
                            "era": era_item.get("name", "Unknown Era"),
                            "item_count": item.get("item_count", 0),
                        })

        if all_song_folders:
            self._song_folders_cache = all_song_folders
            self._cache_timestamp = now
            logger.info(
                "[Snippets] Indexed %d song snippet folders across %d eras.",
                len(all_song_folders),
                len(self._eras_cache),
            )

    async def find_matching_folders(self, query: str) -> list[dict[str, Any]]:
        await self._ensure_folder_index()

        target_clean = clean_title(query).strip().lower()
        target_norm = normalize(query)

        exact_matches = []
        partial_matches = []
        fuzzy_matches = []

        for folder in self._song_folders_cache:
            name_raw = folder["name"]
            name_clean = clean_title(name_raw).strip().lower()
            name_norm = normalize(name_raw)

            if name_clean == target_clean or name_norm == target_norm:
                exact_matches.append(folder)
                continue

            if target_clean in name_clean or target_norm in name_norm:
                partial_matches.append(folder)
                continue

            score = difflib.SequenceMatcher(None, target_norm, name_norm).ratio()
            if score >= FUZZY_THRESHOLD:
                fuzzy_matches.append((score, folder))

        if exact_matches:
            return exact_matches

        fuzzy_matches.sort(key=lambda x: x[0], reverse=True)
        ordered_fuzzy = [folder for _, folder in fuzzy_matches]

        combined = partial_matches + [f for f in ordered_fuzzy if f not in partial_matches]
        return combined[:25]

    async def fetch_folder_snippets(self, folder_path: str) -> list[dict[str, Any]]:
        items = await self.browse_path(folder_path)
        snippets = []

        for item in items:
            name = item.get("name", "")
            ext = str(item.get("extension") or "").lower()
            if not ext and "." in name:
                ext = f".{name.rsplit('.', 1)[-1].lower()}"

            if ext in VIDEO_EXTENSIONS:
                snippets.append({
                    "name": name,
                    "path": item.get("path", ""),
                    "size_human": item.get("size_human", ""),
                    "download_url": make_download_url(item.get("path", "")),
                })

        return snippets

    @commands.command(name="snip", aliases=["snippets", "snippet"])
    @commands.cooldown(1, 4, commands.BucketType.user)
    async def snip(self, ctx: commands.Context, *, song: str = ""):
        if not song.strip():
            await ctx.send(
                view=simple_view(
                    "# Command: snip\n"
                    "-# Browse Juice WRLD video snippets by song.\n\n"
                    "**Syntax** · `,snip <song>`\n"
                    "**Example** · `,snip wraith`"
                )
            )
            return

        searching_msg = await ctx.send(
            view=simple_view(f"🔍 Searching snippets for **{song}**...")
        )

        matching_folders = await self.find_matching_folders(song)

        if not matching_folders:
            await searching_msg.edit(
                view=simple_view(f"❌ No snippet folders found matching **{song}**.")
            )
            return

        if len(matching_folders) == 1:
            chosen = matching_folders[0]
            files = await self.fetch_folder_snippets(chosen["path"])
            result_view = SnippetView(
                song_title=chosen["name"],
                era_name=chosen["era"],
                files=files,
            )
            await searching_msg.edit(view=result_view)
            return

        dropdown_view = SongSelectView(
            query=song,
            candidate_folders=matching_folders,
            author_id=ctx.author.id,
            cog=self,
        )
        await searching_msg.edit(view=dropdown_view)
        dropdown_view.message = searching_msg

    @snip.error
    async def snip_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, (SendHelp, *USAGE_ERRORS)):
            if ctx.command:
                return await send_usage_help(ctx)

        await send_command_error(
            ctx,
            error,
            log_level=logging.INFO if isinstance(error, commands.CommandOnCooldown) else logging.ERROR,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Snippets(bot))
