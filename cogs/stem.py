from __future__ import annotations

import asyncio
import difflib
import logging
import time
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp
import discord
from discord.ext import commands

from config import EMBED_COLOR

logger = logging.getLogger("juicevault")

API_BASE = "https://api.juicevault.xyz"
STEMS_LIST_ENDPOINT = f"{API_BASE}/music/stems/list"
DOWNLOAD_ENDPOINT = f"{API_BASE}/music/download"

CACHE_TTL = 300  # Cache stems for 5 minutes
SELECT_TIMEOUT = 120
VIEW_TIMEOUT = 900

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def valid_http_url(value: Any) -> str | None:
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


def resolve_cover_url(value: Any) -> str | None:
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return f"{API_BASE}{value}"
    return f"{API_BASE}/{value}"


def truncate(value: str, limit: int = 90) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def make_link_button(label: str, url: str | None) -> discord.ui.Button | None:
    url = valid_http_url(url)
    if not url:
        return None
    return discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=url)


def simple_view(content: str, *, timeout: int = 60) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=timeout)
    container = discord.ui.Container(accent_color=EMBED_COLOR)
    container.add_item(discord.ui.TextDisplay(content))
    view.add_item(container)
    return view


def text_display(content: str) -> discord.ui.TextDisplay:
    return discord.ui.TextDisplay(content=content)


def small_separator() -> discord.ui.Separator:
    return discord.ui.Separator(spacing=discord.SeparatorSpacing.small)


def build_leak_header(song: dict[str, Any]) -> str:
    title = song.get("title") or song.get("file_name") or "Unknown Track"
    lines = [f"### **{title}**"]

    alt_names = song.get("alt_names") or []
    if isinstance(alt_names, list) and alt_names:
        clean_alts = [str(a).strip() for a in alt_names if str(a).strip()]
        if clean_alts:
            lines.append(f"-# Alt Name(s): **{', '.join(clean_alts)}**")

    artist = song.get("artist")
    if artist and str(artist).strip() != "N/A":
        lines.append(f"-# Artist: **{artist}**")

    return "\n".join(lines)


def build_leak_details(song: dict[str, Any]) -> str | None:
    fields: list[str] = []

    file_name = song.get("file_name")
    if file_name:
        fields.append(f"**File Name**\n{file_name}")

    category = song.get("category")
    if category:
        fields.append(f"**Category**\n{str(category).title()}")

    length = song.get("length")
    if length:
        fields.append(f"**Length**\n{length}")

    file_size = song.get("file_size")
    if file_size:
        fields.append(f"**File Size**\n{file_size}")

    play_count = song.get("play_count")
    if play_count is not None:
        try:
            fields.append(f"**Play Count**\n{int(play_count):,}")
        except ValueError:
            fields.append(f"**Play Count**\n{play_count}")

    archive_added = song.get("archive_added_at")
    if archive_added:
        date_str = str(archive_added).split("T")[0]
        fields.append(f"**Archived**\n{date_str}")

    return "\n\n".join(fields) if fields else None


class JuiceVaultAPI:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self._cache: list[dict[str, Any]] = []
        self._last_fetched: float = 0
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers=DEFAULT_HEADERS)

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def fetch_all_stems(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._cache and (now - self._last_fetched < CACHE_TTL):
            logger.debug("Serving stems list from local memory cache (%d items)", len(self._cache))
            return self._cache

        async with self._lock:
            if self._cache and (now - self._last_fetched < CACHE_TTL):
                return self._cache

            await self.start()
            logger.info("Connecting to JuiceVault API: %s", STEMS_LIST_ENDPOINT)
            try:
                assert self.session is not None
                async with self.session.get(
                    STEMS_LIST_ENDPOINT,
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    logger.info("JuiceVault HTTP response status: %d (Content-Type: %s)", resp.status, resp.content_type)

                    if resp.status != 200:
                        body_preview = (await resp.text())[:300]
                        logger.error("JuiceVault API failed with HTTP %d. Body preview: %s", resp.status, body_preview)
                        return self._cache

                    data = await resp.json(content_type=None)
                    logger.info("JuiceVault raw response payload parsed as: %s", type(data).__name__)

                    # Extract array if nested inside an object
                    stems_list: list[dict[str, Any]] = []
                    if isinstance(data, list):
                        stems_list = data
                    elif isinstance(data, dict):
                        logger.info("Response is a dict with keys: %s", list(data.keys()))
                        for key in ("stems", "data", "items", "results", "songs", "tracks"):
                            if isinstance(data.get(key), list):
                                logger.info("Unwrapped stems list from key: %r", key)
                                stems_list = data[key]
                                break

                    if not stems_list:
                        logger.warning("Could not locate stem entries in response data: %s", str(data)[:250])
                    else:
                        self._cache = stems_list
                        self._last_fetched = now
                        logger.info("Successfully cached %d stems from JuiceVault", len(self._cache))

            except asyncio.TimeoutError:
                logger.error("Connection timed out while querying JuiceVault: %s", STEMS_LIST_ENDPOINT)
            except Exception as e:
                logger.exception("Unexpected exception occurred while loading stems: %s", e)

        return self._cache

    async def search_stems(self, query: str) -> list[dict[str, Any]]:
        stems = await self.fetch_all_stems()
        logger.info("Evaluating search query %r against %d cached stems", query, len(stems))

        if not stems:
            logger.error("Stems cache is empty. Search cannot produce results.")
            return []

        clean_query = query.strip().casefold()
        exact_matches: list[dict[str, Any]] = []
        partial_matches: list[dict[str, Any]] = []
        fuzzy_matches: list[tuple[float, dict[str, Any]]] = []

        for item in stems:
            title = str(item.get("title") or "").strip().casefold()
            filename = str(item.get("file_name") or "").strip().casefold()
            alts = [str(a).strip().casefold() for a in (item.get("alt_names") or []) if a]

            if clean_query == title or clean_query == filename or clean_query in alts:
                exact_matches.append(item)
            elif clean_query in title or clean_query in filename or any(clean_query in a for a in alts):
                partial_matches.append(item)
            else:
                score = max(
                    difflib.SequenceMatcher(None, clean_query, title).ratio(),
                    difflib.SequenceMatcher(None, clean_query, filename).ratio(),
                )
                if score >= 0.65:
                    fuzzy_matches.append((score, item))

        fuzzy_matches.sort(key=lambda x: x[0], reverse=True)
        ranked_fuzzy = [item for _, item in fuzzy_matches]

        combined = exact_matches + partial_matches + ranked_fuzzy
        seen_ids: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for song in combined:
            sid = song.get("id") or song.get("file_name")
            if sid not in seen_ids:
                seen_ids.add(sid)
                deduped.append(song)

        logger.info(
            "Search results for %r: %d found (Exact: %d, Partial: %d, Fuzzy: %d)",
            query, len(deduped), len(exact_matches), len(partial_matches), len(ranked_fuzzy)
        )
        return deduped[:25]


class LeakView(discord.ui.LayoutView):
    def __init__(self, song: dict[str, Any], *, timeout: int = VIEW_TIMEOUT) -> None:
        super().__init__(timeout=timeout)

        song_id = song.get("id")
        download_url = f"{DOWNLOAD_ENDPOINT}/{song_id}" if song_id else None
        cover_url = resolve_cover_url(song.get("cover"))

        container = discord.ui.Container(accent_color=EMBED_COLOR)

        header_text = build_leak_header(song)
        if cover_url:
            container.add_item(
                discord.ui.Section(
                    text_display(header_text),
                    accessory=discord.ui.Thumbnail(media=cover_url),
                )
            )
        else:
            container.add_item(text_display(header_text))

        container.add_item(small_separator())

        details_text = build_leak_details(song)
        if details_text:
            container.add_item(text_display(details_text))

        if download_url:
            button = make_link_button("Download", download_url)
            if button:
                container.add_item(small_separator())
                container.add_item(discord.ui.ActionRow(button))

        self.add_item(container)


class LeakSongSelect(discord.ui.Select):
    def __init__(self, candidates: list[dict[str, Any]], author_id: int) -> None:
        self.candidates = candidates
        self.author_id = author_id

        options = []
        for idx, result in enumerate(candidates[:25]):
            title = str(result.get("title") or result.get("file_name") or f"Track {idx + 1}")
            length = result.get("length")
            category = str(result.get("category", "Stem")).title()
            desc = f"{category} • {length}" if length else category

            options.append(
                discord.SelectOption(
                    label=title[:100],
                    value=str(idx),
                    description=desc[:100],
                )
            )

        super().__init__(
            placeholder="Choose the correct track...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the person who ran the command can choose.",
                ephemeral=True,
            )
            return

        chosen = self.candidates[int(self.values[0])]
        view = LeakView(chosen)
        await interaction.response.edit_message(view=view)


class LeakSongSelectView(discord.ui.LayoutView):
    def __init__(self, query: str, candidates: list[dict[str, Any]], author_id: int) -> None:
        super().__init__(timeout=SELECT_TIMEOUT)
        self.message: discord.Message | None = None

        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"# Command: leak\n"
                f"Found **{len(candidates)}** matching tracks for **{query}**.\n"
                f"Select the correct track from the dropdown below:"
            )
        )
        container.add_item(
            discord.ui.ActionRow(LeakSongSelect(candidates, author_id))
        )
        self.add_item(container)

    async def on_timeout(self) -> None:
        if not self.message:
            return
        try:
            await self.message.edit(
                view=simple_view("⏱️ Selection timed out. Please run the command again.", timeout=10)
            )
        except Exception:
            pass


class Leak(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.api = JuiceVaultAPI()

    def cog_unload(self) -> None:
        asyncio.create_task(self.api.close())

    @commands.command(name="leak", aliases=["song", "stem", "stems"])
    async def leak(self, ctx: commands.Context, *, query: str | None = None) -> None:
        if not query:
            help_view = simple_view(
                "# Command: leak\n\n"
                "**Syntax**\n"
                f"`{ctx.clean_prefix}leak <track>`\n\n"
                "**Example**\n"
                f"`{ctx.clean_prefix}leak Netflix & Pills`"
            )
            await ctx.send(view=help_view)
            return

        logger.info("[Command Invoked] %s by %s (%d) with query: %r", ctx.invoked_with, ctx.author, ctx.author.id, query)
        candidates = await self.api.search_stems(query)

        if not candidates:
            await ctx.send(view=simple_view(f"❌ Couldn't find any stems matching **{query}**."))
            return

        wanted = query.strip().casefold()
        exact_match = next(
            (
                s for s in candidates
                if str(s.get("title") or "").strip().casefold() == wanted
                or str(s.get("file_name") or "").strip().casefold() == wanted
            ),
            None,
        )

        if len(candidates) == 1 or exact_match is not None:
            song = exact_match or candidates[0]
            view = LeakView(song)
            await ctx.send(view=view)
            return

        select_view = LeakSongSelectView(
            query=query,
            candidates=candidates,
            author_id=ctx.author.id,
        )
        select_view.message = await ctx.send(view=select_view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Leak(bot))
