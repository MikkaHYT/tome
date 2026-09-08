# cogs/covers.py

import asyncio
import difflib
import html
import io
import json
import logging
import re
from urllib.parse import quote, unquote
from typing import Any, Optional

import aiohttp
import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION & CONSTANTS
# ============================================================

API_ROOT = "https://juicewrldapi.com"
API_BASE = f"{API_ROOT}/juicewrld"
BROWSE_ENDPOINT = f"{API_BASE}/files/browse/"
DOWNLOAD_ENDPOINT = f"{API_BASE}/files/download/"
JUICEVAULT_BASE = "https://api.juicevault.xyz"

EMBED_COLOR = discord.Color.from_str("#2b2d31")
TIMEOUT = aiohttp.ClientTimeout(total=45)
MAX_SEARCH_RESULTS = 25
MAX_ATTACHED_COVERS = 10
FUZZY_THRESHOLD = 0.60

IMAGE_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


# ============================================================
# HELPERS & SANITIZATION
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def clean_title(title: str) -> str:
    """Strips versions, metadata tags, brackets, and features[span_9](start_span)[span_9](end_span)[span_10](start_span)[span_10](end_span)."""
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


def format_era(value: Any) -> str:
    if not value:
        return "N/A"
    if isinstance(value, dict):
        name = value.get("name") or value.get("title") or value.get("value")
        if name:
            return clean_text(name)
        desc = value.get("description")
        if desc:
            return clean_text(desc)
        return "N/A"
    if isinstance(value, str):
        val = value.strip()
        return val if val else "N/A"
    return str(value)


def format_artist(song: dict[str, Any]) -> str:
    val = (
        song.get("credited_artists")
        or song.get("CreditedArtists")
        or song.get("artist")
        or song.get("artists")
    )
    if isinstance(val, list):
        items = []
        for item in val:
            if isinstance(item, dict):
                name = item.get("name") or item.get("title")
                if name:
                    items.append(str(name).strip())
            elif item:
                items.append(str(item).strip())
        if items:
            return ", ".join(items)
    elif isinstance(val, dict):
        return str(val.get("name") or val.get("title") or "Juice WRLD").strip()
    elif isinstance(val, str) and val.strip():
        return val.strip()

    return "Juice WRLD"


def format_producers(song: dict[str, Any]) -> str:
    val = (
        song.get("producers")
        or song.get("Producers")
        or song.get("producer")
        or song.get("producer_names")
        or song.get("producerNames")
    )
    if isinstance(val, list):
        items = []
        for item in val:
            if isinstance(item, dict):
                name = item.get("name") or item.get("title")
                if name:
                    items.append(str(name).strip())
            elif item:
                items.append(str(item).strip())
        if items:
            return ", ".join(items)
    elif isinstance(val, dict):
        return str(val.get("name") or val.get("title") or "N/A").strip()
    elif isinstance(val, str) and val.strip():
        return val.strip()

    return "N/A"


# ============================================================
# COMPONENTS V2: CUSTOM MEDIA GALLERY (TYPE 12)
# ============================================================

class CustomMediaGallery(discord.ui.Item):
    """Renders full-size images in a Components V2 container[span_11](start_span)[span_11](end_span)."""
    def __init__(self, *urls: str):
        super().__init__()
        self.urls = list(urls)

    def to_component_dict(self) -> dict[str, Any]:
        return {
            "type": 12,
            "items": [{"media": {"url": u}} for u in self.urls],
        }

    def to_dict(self) -> dict[str, Any]:
        return self.to_component_dict()


# ============================================================
# COMPONENTS V2: COVER RESULT VIEW
# ============================================================

class CoverResultView(discord.ui.LayoutView):
    def __init__(
        self,
        song: dict[str, Any],
        covers_data: list[tuple[io.BytesIO, str]],
        timeout: float = 300,
    ):
        super().__init__(timeout=timeout)

        title = clean_text(song.get("name") or song.get("title") or "Unknown Song")
        era = format_era(song.get("era") or song.get("Era"))
        artist = format_artist(song)
        producers = format_producers(song)

        container = discord.ui.Container(accent_color=EMBED_COLOR)

        if not covers_data:
            container.add_item(
                discord.ui.TextDisplay(
                    f"# {title}\n\n"
                    f"**Era:** {era}\n"
                    f"**Artist:** {artist}\n"
                    f"**Producer(s):** {producers}\n\n"
                    f"❌ No cover art found for this track."
                )
            )
            self.add_item(container)
            return

        header_text = (
            f"# {title}\n\n"
            f"**Era:** {era}\n"
            f"**Artist:** {artist}\n"
            f"**Producer(s):** {producers}\n"
            f"**Covers Found:** {len(covers_data)}"
        )

        container.add_item(discord.ui.TextDisplay(header_text))
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        gallery_urls = [
            f"attachment://cover_{idx}.{ext}"
            for idx, (_, ext) in enumerate(covers_data, start=1)
        ]

        container.add_item(CustomMediaGallery(*gallery_urls))
        self.add_item(container)


# ============================================================
# COMPONENTS V2: SELECTION DROPDOWN VIEW
# ============================================================

class SongSelect(discord.ui.Select):
    def __init__(self, songs: list[dict[str, Any]], author_id: int, cog: "Covers"):
        self.songs = songs
        self.author_id = author_id
        self.cog = cog

        options = []
        for idx, song in enumerate(songs[:25]):
            title = clean_text(song.get("name") or song.get("title") or "Unknown Song")[:100]
            era = format_era(song.get("era") or song.get("Era"))
            alt = clean_text(song.get("track_titles") or song.get("TrackTitles") or "")

            parts = []
            if era and era != "N/A":
                parts.append(f"Era: {era}")
            if alt:
                parts.append(f"Alt: {alt}")

            desc = " • ".join(parts)[:100] or None

            options.append(
                discord.SelectOption(
                    label=title,
                    value=str(idx),
                    description=desc,
                )
            )

        super().__init__(
            placeholder="Choose the correct song...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Only the person who ran the command can choose a song.",
                ephemeral=True,
            )
            return

        if self.view:
            self.view.selected = True
            self.view.stop()

        await interaction.response.defer()

        chosen_song = self.songs[int(self.values[0])]
        song_id = chosen_song.get("id") or chosen_song.get("song_id") or chosen_song.get("public_id")

        if song_id is not None:
            full_song = await self.cog.get_song_details(song_id)
            if full_song and isinstance(full_song, dict):
                chosen_song = full_song

        title = chosen_song.get("name") or chosen_song.get("title") or ""
        path = chosen_song.get("path") or ""

        covers_data = await self.cog.fetch_song_covers(title=title, path=path)
        view = CoverResultView(chosen_song, covers_data)

        files = []
        for idx, (buf, ext) in enumerate(covers_data, start=1):
            buf.seek(0)
            files.append(discord.File(buf, filename=f"cover_{idx}.{ext}"))

        await interaction.edit_original_response(view=view, attachments=files)


class SongSelectView(discord.ui.LayoutView):
    def __init__(self, query: str, songs: list[dict[str, Any]], author_id: int, cog: "Covers"):
        super().__init__(timeout=120)
        self.selected = False
        self.message: Optional[discord.Message] = None

        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            discord.ui.TextDisplay(
                f"# Cover Art Search\n"
                f"Found **{len(songs)}** matches for **{query}**.\n"
                f"Select the exact song from the menu below:"
            )
        )
        container.add_item(
            discord.ui.ActionRow(
                SongSelect(songs, author_id, cog)
            )
        )
        self.add_item(container)

    async def on_timeout(self):
        if self.selected:
            return

        if self.message:
            try:
                timeout_view = discord.ui.LayoutView(timeout=10)
                container = discord.ui.Container(accent_color=EMBED_COLOR)
                container.add_item(
                    discord.ui.TextDisplay("⏱️ Selection timed out. Please run the command again.")
                )
                timeout_view.add_item(container)
                await self.message.edit(view=timeout_view)
            except Exception:
                pass


# ============================================================
# COVERS COG
# ============================================================

class Covers(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self.session_lock = asyncio.Lock()

        # Cache for Cover Arts directory index
        self._covers_cache: list[dict[str, Any]] = []
        self._covers_cache_timestamp: float = 0
        self._indexing_lock = asyncio.Lock()

    async def cog_load(self):
        # Pre-warm the cover arts index in the background on startup
        asyncio.create_task(self._ensure_covers_index())

    async def get_session(self) -> aiohttp.ClientSession:
        async with self.session_lock:
            if self.session and not self.session.closed:
                return self.session

            self.session = aiohttp.ClientSession(
                timeout=TIMEOUT,
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
        params = {"path": path, "page_size": 2000}

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
            logger.warning("[Covers] Browse failed on path '%s': %s", path, exc)
            return []

    async def _ensure_covers_index(self):
        """Indexes all artist directories inside 'Cover Arts' and collects every image file[span_12](start_span)[span_12](end_span)."""
        now = asyncio.get_running_loop().time()
        if self._covers_cache and (now - self._covers_cache_timestamp < 1800):
            return

        async with self._indexing_lock:
            if self._covers_cache and (now - self._covers_cache_timestamp < 1800):
                return

            logger.info("[Cover Arts] Indexing all subfolders in 'Cover Arts'...")
            top_items = await self.browse_path("Cover Arts")
            artist_folders = [item for item in top_items if item.get("type") == "directory"]

            semaphore = asyncio.Semaphore(15)

            async def fetch_artist(artist_item):
                async with semaphore:
                    items = await self.browse_path(artist_item["path"])
                    return artist_item.get("name", "Unknown"), items

            tasks = [fetch_artist(a) for a in artist_folders]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            all_covers = []
            for res in results:
                if isinstance(res, tuple):
                    artist_name, items = res
                    if isinstance(items, list):
                        for file_item in items:
                            name = file_item.get("name", "")
                            ext = str(file_item.get("extension") or "").lower()
                            if not ext and "." in name:
                                ext = f".{name.rsplit('.', 1)[-1].lower()}"

                            if ext in IMAGE_EXTENSIONS:
                                all_covers.append({
                                    "name": name,
                                    "path": file_item.get("path", ""),
                                    "artist": artist_name,
                                    "extension": ext.lstrip("."),
                                })

            if all_covers:
                self._covers_cache = all_covers
                self._covers_cache_timestamp = now
                logger.info("[Cover Arts] Indexed %d cover art files across %d artist folders.", len(all_covers), len(artist_folders))

    async def find_covers_in_folders(self, title: str) -> list[dict[str, Any]]:
        """Scans indexed 'Cover Arts' files using exact, substring, and fuzzy matching[span_13](start_span)[span_13](end_span)[span_14](start_span)[span_14](end_span)."""
        await self._ensure_covers_index()

        target_clean = clean_title(title).strip().lower()
        target_norm = normalize(title)

        exact_matches = []
        partial_matches = []
        fuzzy_matches = []

        for item in self._covers_cache:
            raw_name = item["name"]
            base_name = raw_name.rsplit(".", 1)[0]
            name_clean = clean_title(base_name).strip().lower()
            name_norm = normalize(base_name)

            # 1. Exact Name Match
            if name_clean == target_clean or name_norm == target_norm:
                exact_matches.append(item)
                continue

            # 2. Substring Match
            if target_clean in name_clean or target_norm in name_norm:
                partial_matches.append(item)
                continue

            # 3. Fuzzy Ratio Match
            score = difflib.SequenceMatcher(None, target_norm, name_norm).ratio()
            if score >= FUZZY_THRESHOLD:
                fuzzy_matches.append((score, item))

        if exact_matches:
            return exact_matches

        fuzzy_matches.sort(key=lambda x: x[0], reverse=True)
        ordered_fuzzy = [f for _, f in fuzzy_matches]

        combined = partial_matches + [f for f in ordered_fuzzy if f not in partial_matches]
        return combined

    async def search_songs(self, query: str) -> list[dict[str, Any]]:
        session = await self.get_session()
        url = f"{API_BASE}/songs/"
        params = {"search": query, "page_size": MAX_SEARCH_RESULTS}

        try:
            async with session.get(url, params=params, headers=HEADERS, timeout=TIMEOUT) as response:
                if response.status != 200:
                    return []
                text = await response.text()
                data = parse_api_json(text)

                if isinstance(data, list):
                    return [x for x in data if isinstance(x, dict)]
                if isinstance(data, dict):
                    for key in ("results", "songs", "data", "items"):
                        items = data.get(key)
                        if isinstance(items, list):
                            return [x for x in items if isinstance(x, dict)]
                return []
        except Exception as exc:
            logger.error("[Search] Error searching songs: %s", exc)
            return []

    async def get_song_details(self, song_id: Any) -> Optional[dict[str, Any]]:
        session = await self.get_session()
        for endpoint in (f"songs/{song_id}/", f"songs/{song_id}"):
            url = f"{API_BASE}/{endpoint}"
            try:
                async with session.get(url, headers=HEADERS, timeout=15) as response:
                    if response.status == 200:
                        text = await response.text()
                        data = parse_api_json(text)
                        if isinstance(data, dict):
                            song_obj = data.get("song") or data.get("data") or data
                            if isinstance(song_obj, dict):
                                if "path" not in song_obj and "path" in data:
                                    song_obj["path"] = data["path"]
                                return song_obj
                            return data
            except Exception:
                continue
        return None

    # ========================================================
    # COVER RETRIEVAL PIPELINE (COVER ARTS FOLDERS -> JUICEVAULT -> API)
    # ========================================================

    async def fetch_song_covers(self, title: str, path: str = "") -> list[tuple[io.BytesIO, str]]:
        """Searches through every Cover Arts subfolder and downloads all matching covers[span_15](start_span)[span_15](end_span)[span_16](start_span)[span_16](end_span)."""
        session = await self.get_session()
        found_covers: list[tuple[io.BytesIO, str]] = []

        # ----------------------------------------------------
        # 1. Primary: Search every subfolder in 'Cover Arts'
        # ----------------------------------------------------
        matched_items = await self.find_covers_in_folders(title)

        if matched_items:
            logger.info("[Covers] Found %d matching artwork file(s) in 'Cover Arts' subfolders for '%s'", len(matched_items), title)

            async def download_one(cover_meta):
                dl_url = make_download_url(cover_meta["path"])
                ext = cover_meta["extension"]
                try:
                    async with session.get(dl_url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status == 200:
                            raw = await resp.read()
                            if len(raw) > 100 and raw[:5] not in {b"<!DOC", b"<html"}:
                                buf = io.BytesIO(raw)
                                buf.seek(0)
                                return (buf, ext)
                except Exception as e:
                    logger.warning("[Covers] Error downloading cover %s: %s", cover_meta["path"], e)
                return None

            download_tasks = [download_one(item) for item in matched_items[:MAX_ATTACHED_COVERS]]
            downloaded = await asyncio.gather(*download_tasks)
            valid_covers = [c for c in downloaded if c is not None]

            if valid_covers:
                return valid_covers

        # ----------------------------------------------------
        # 2. Secondary: JuiceVault Search Fallback
        # ----------------------------------------------------
        clean_title_str = clean_title(title)
        target_norm = clean_title_str.lower()
        try:
            search_url = f"{JUICEVAULT_BASE}/music/search"
            async with session.get(search_url, params={"q": clean_title_str}, headers=HEADERS, timeout=TIMEOUT) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    results = data.get("results", []) if isinstance(data, dict) else []

                    for item in results:
                        cand_title = clean_title(item.get("title", "")).strip().lower()
                        if cand_title == target_norm:
                            uuid = item.get("id")
                            if uuid:
                                cdn_url = f"{JUICEVAULT_BASE}/cdn/music/covers/{uuid}"
                                async with session.get(cdn_url, headers=HEADERS, timeout=TIMEOUT) as cdn_resp:
                                    if cdn_resp.status == 200:
                                        raw = await cdn_resp.read()
                                        if len(raw) > 100 and raw[:5] not in {b"<!DOC", b"<html"}:
                                            buf = io.BytesIO(raw)
                                            buf.seek(0)
                                            return [(buf, "webp")]
        except Exception:
            pass

        # ----------------------------------------------------
        # 3. Tertiary: juicewrldapi Embedded Cover Fallback
        # ----------------------------------------------------
        if path:
            url = f"{API_BASE}/files/cover-art/"
            try:
                async with session.get(url, params={"path": path}, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status == 200:
                        raw = await resp.read()
                        if len(raw) > 100 and raw[:5] not in {b"<!DOC", b"<html"}:
                            buf = io.BytesIO(raw)
                            buf.seek(0)
                            return [(buf, "png")]
            except Exception:
                pass

        return []

    # ========================================================
    # COMMAND
    # ========================================================

    @commands.command(
        name="cover",
        aliases=("covers",),
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def cover(self, ctx: commands.Context, *, song: str = ""):
        if not song.strip():
            view = discord.ui.LayoutView(timeout=60)
            container = discord.ui.Container(accent_color=EMBED_COLOR)
            container.add_item(
                discord.ui.TextDisplay(
                    "# Command: cover\n"
                    "**Usage:** `,cover <song>`\n\n"
                    "**Example:** `,cover on time`"
                )
            )
            view.add_item(container)
            await ctx.send(view=view)
            return

        results = await self.search_songs(song)

        if not results:
            view = discord.ui.LayoutView(timeout=60)
            container = discord.ui.Container(accent_color=EMBED_COLOR)
            container.add_item(
                discord.ui.TextDisplay(f"❌ Couldn't find any songs matching **{song}**.")
            )
            view.add_item(container)
            await ctx.send(view=view)
            return

        # Direct render if only 1 candidate exists
        if len(results) == 1:
            chosen_song = results[0]
            song_id = chosen_song.get("id") or chosen_song.get("song_id") or chosen_song.get("public_id")
            if song_id is not None:
                full_song = await self.get_song_details(song_id)
                if full_song and isinstance(full_song, dict):
                    chosen_song = full_song

            title = chosen_song.get("name") or chosen_song.get("title") or ""
            path = chosen_song.get("path") or ""

            covers_data = await self.fetch_song_covers(title, path)
            view = CoverResultView(chosen_song, covers_data)

            files = []
            for idx, (buf, ext) in enumerate(covers_data, start=1):
                buf.seek(0)
                files.append(discord.File(buf, filename=f"cover_{idx}.{ext}"))

            await ctx.send(view=view, files=files)
            return

        # Multiple matches: show dropdown selection
        view = SongSelectView(
            query=song,
            songs=results,
            author_id=ctx.author.id,
            cog=self,
        )
        message = await ctx.send(view=view)
        view.message = message

    # ========================================================
    # ERROR HANDLER
    # ========================================================

    @cover.error
    async def cover_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ Try again in **{error.retry_after:.1f}s**.")
            return

        logger.exception("Cover command error: %s", error)
        await ctx.send(f"❌ **Cover error:** `{error}`")


# ============================================================
# SETUP
# ============================================================

async def setup(bot: commands.Bot):
    await bot.add_cog(Covers(bot))