from __future__ import annotations

import asyncio
import difflib
import logging
import os
import random
import re
import tempfile
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

try:
    from config import EMBED_COLOR
except ImportError:
    EMBED_COLOR = discord.Color.from_str("#2b2d31")

logger = logging.getLogger("MakeSnip")
logger.setLevel(logging.INFO)

API_ROOT = "https://juicewrldapi.com"
API_BASE = f"{API_ROOT}/juicewrld"
SONGS_ENDPOINT = f"{API_BASE}/songs/"
BROWSE_ENDPOINT = f"{API_BASE}/files/browse/"
DOWNLOAD_BASE = f"{API_BASE}/files/download/"

API_TIMEOUT = 25
SELECT_TIMEOUT = 120
MAX_RESULTS = 25
SNIPPET_DURATION = 30
MAX_DISCORD_FILE_SIZE = 25 * 1024 * 1024

COVER_FIELDS = (
    "image_url", "imageUrl", "imageURL", "image",
    "cover_url", "coverUrl", "coverURL",
    "cover_art_url", "coverArtUrl",
    "artwork_url", "artworkUrl",
    "thumbnail_url", "thumbnailUrl", "thumbnail",
)

FILE_PATH_KEYS = (
    "path", "file_path", "filePath", "filepath",
    "download_path", "downloadPath",
)

FILE_URL_KEYS = (
    "download_url", "downloadUrl", "stream_url", "streamUrl",
    "play_url", "playUrl", "audio_url", "audioUrl",
    "url", "href", "link",
)

FILENAME_KEYS = (
    "file_names", "fileNames", "file_name", "fileName", "filename", "files",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


def clean(value: Any) -> Any:
    if value is None:
        return None
    if callable(value):
        try:
            value = value()
        except TypeError:
            return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    return value


def get_value(obj: Any, *names: str, default: Any = None) -> Any:
    if obj is None:
        return default
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        value = clean(value)
        if value is not None:
            return value
    return default


def recursive_find(obj: Any, names: tuple[str, ...] | list[str]) -> Any:
    wanted = {str(name).casefold() for name in names}
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).casefold() in wanted:
                value = clean(value)
                if value is not None:
                    return value
        for value in obj.values():
            found = recursive_find(value, names)
            if found is not None:
                return found
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            found = recursive_find(item, names)
            if found is not None:
                return found
    return None


def first_value(obj: Any, *names: str, default: Any = None) -> Any:
    value = get_value(obj, *names, default=None)
    if value is not None:
        return value
    found = recursive_find(obj, names)
    return found if found is not None else default


def display(value: Any, default: str = "N/A") -> str:
    value = clean(value)
    if value is None:
        return default
    if isinstance(value, (list, tuple, set)):
        parts = [display(item, "") for item in value]
        joined = ", ".join(part for part in parts if part)
        return joined or default
    if isinstance(value, dict):
        for key in ("name", "title", "value", "text", "label"):
            if key in value:
                result = display(value[key], "")
                if result:
                    return result
        return default
    return str(value)


def unwrap_named(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("name") or value.get("title") or value.get("value")
    return value


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


def normalize_path(path: Any) -> str | None:
    if not path:
        return None
    path = str(path).strip()
    if not path or path.startswith(("http://", "https://")):
        return None
    while path.startswith("/"):
        path = path[1:]
    return path


def make_download_url(path: str) -> str | None:
    normalized = normalize_path(path)
    if not normalized:
        return None
    return f"{DOWNLOAD_BASE}?path={quote(normalized, safe='')}&channel=comp"


def clean_match_key(value: str) -> str:
    value = re.sub(r"\.[a-zA-Z0-9]+$", "", value)
    value = re.sub(r"[_\-.]+", " ", value)
    value = re.sub(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}", "", value)
    return re.sub(r"\s+", " ", value).strip().lower()


def song_text(song: Any, *keys: str, default: Any = None) -> str:
    return display(first_value(song, *keys, default=default))


def get_song_title(song: Any) -> str:
    return song_text(
        song,
        "name", "title", "song_title", "songTitle",
        default="Unknown Song",
    )


def resolve_cover_url(value: Any) -> str | None:
    direct = valid_http_url(value)
    if direct:
        return direct
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return f"https://juicewrldapi.com{value}"
    if value.startswith(("images/", "media/")):
        return f"https://juicewrldapi.com/{value}"
    return None


def get_cover_metadata_url(song: Any) -> str | None:
    for field in COVER_FIELDS:
        value = first_value(song, field, default=None)
        if isinstance(value, dict):
            value = get_value(value, "url", "src", "href", "image_url", "imageUrl")
        result = resolve_cover_url(value)
        if result:
            return result
    return None


def filename_list(song: Any) -> list[str]:
    value = first_value(song, *FILENAME_KEYS, default=None)
    if value is None:
        return []
    if isinstance(value, str):
        return [line.strip() for line in value.split("\n") if line.strip()]
    if isinstance(value, (list, tuple, set)):
        output: list[str] = []
        for item in value:
            if isinstance(item, dict):
                item = get_value(
                    item,
                    *FILE_PATH_KEYS,
                    "filename", "file_name", "fileName", "name",
                    "url", "download_url", "downloadUrl",
                    default=None,
                )
            if item:
                output.append(str(item).strip())
        return output
    return []


def file_path_from_item(item: Any) -> str | None:
    if isinstance(item, str):
        if item.startswith(("http://", "https://")):
            return None
        return normalize_path(item)
    if isinstance(item, dict):
        return normalize_path(get_value(item, *FILE_PATH_KEYS, default=None))
    return None


def direct_file_url(item: Any) -> str | None:
    if isinstance(item, str):
        return valid_http_url(item)
    if isinstance(item, dict):
        return valid_http_url(get_value(item, *FILE_URL_KEYS, default=None))
    return None


def recursive_file_objects(obj: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    file_keys = {
        "path", "file_path", "filepath", "download_path", "download_url",
        "downloadurl", "stream_url", "streamurl", "play_url", "playurl",
        "audio_url", "audiourl",
    }
    if isinstance(obj, dict):
        keys = {str(key).casefold() for key in obj}
        if keys.intersection(file_keys):
            found.append(obj)
        for value in obj.values():
            found.extend(recursive_file_objects(value))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            found.extend(recursive_file_objects(item))
    return found


def parse_duration_seconds(song: Any) -> int:
    value = first_value(song, "length", "duration", "duration_text", "durationText", default=None)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and ":" in value:
        parts = value.strip().split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except ValueError:
            pass
    return 180


def simple_view(content: str, *, timeout: int = 60) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=timeout)
    container = discord.ui.Container(accent_color=EMBED_COLOR)
    container.add_item(discord.ui.TextDisplay(content))
    view.add_item(container)
    return view


class JuiceWRLDAPI:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self._storage_files: list[dict[str, str]] = []
        self._storage_timestamp: float = 0
        self._storage_lock = asyncio.Lock()

        self._cover_root_items: list[dict[str, Any]] = []
        self._cover_timestamp: float = 0
        self._cover_lock = asyncio.Lock()

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
            self.session = aiohttp.ClientSession(timeout=timeout, headers=HEADERS)

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def request(self, endpoint: str, params: dict | None = None) -> Any:
        await self.start()
        url = endpoint if endpoint.startswith("http") else f"{API_BASE}/{endpoint.lstrip('/')}"
        try:
            async with self.session.get(url, params=params) as response:
                if response.status != 200:
                    logger.warning("[API] Request to '%s' failed with status %d", url, response.status)
                    return None
                return await response.json(content_type=None)
        except Exception as e:
            logger.warning("[API] Request to '%s' failed: %s", url, e)
            return None

    async def browse_directory(self, path: str) -> list[dict]:
        data = await self.request(
            "files/browse/",
            params={"path": path, "channel": "comp", "page_size": 2000},
        )
        if isinstance(data, dict):
            return data.get("items", [])
        return []

    async def _ensure_storage_index(self) -> None:
        now = asyncio.get_running_loop().time()
        if self._storage_files and (now - self._storage_timestamp < 1800):
            return

        async with self._storage_lock:
            if self._storage_files and (now - self._storage_timestamp < 1800):
                return

            logger.info("[Storage] Indexing 'Original Files' and 'Tagged Files' archives...")
            collected: list[dict[str, str]] = []
            for base_folder in ("Original Files", "Tagged Files"):
                top_items = await self.browse_directory(base_folder)
                directories = [item for item in top_items if item.get("type") == "directory"]
                for item in top_items:
                    if item.get("type") != "directory" and item.get("path"):
                        collected.append({"name": item.get("name", ""), "path": item.get("path", "")})

                if directories:
                    tasks = [self.browse_directory(sub["path"]) for sub in directories]
                    res_list = await asyncio.gather(*tasks, return_exceptions=True)
                    for res in res_list:
                        if isinstance(res, list):
                            for sub_item in res:
                                if sub_item.get("path"):
                                    collected.append({"name": sub_item.get("name", ""), "path": sub_item.get("path", "")})

            if collected:
                self._storage_files = collected
                self._storage_timestamp = now
                logger.info("[Storage] Indexed %d total audio files across leak directories.", len(collected))

    async def resolve_storage_path(self, query: str, era_hint: str | None = None) -> str | None:
        await self._ensure_storage_index()
        if not self._storage_files:
            logger.warning("[Storage] Storage index is empty; cannot resolve path for '%s'", query)
            return None

        clean_target = clean_match_key(query)
        if not clean_target:
            return None

        exact_match = None
        best_match = None
        best_score = 0.0

        for item in self._storage_files:
            path = item.get("path", "")
            name = item.get("name", "")
            base = name.rsplit(".", 1)[0] if "." in name else name
            clean_item = clean_match_key(base)

            if clean_item == clean_target:
                if era_hint and era_hint.lower() in path.lower():
                    logger.info("[Storage] Exact era-matched file found: '%s'", path)
                    return path
                if not exact_match:
                    exact_match = path

            score = difflib.SequenceMatcher(None, clean_target, clean_item).ratio()
            if era_hint and era_hint.lower() in path.lower():
                score += 0.15

            if score > best_score:
                best_score = score
                if score >= 0.70:
                    best_match = path

        resolved = exact_match or best_match
        logger.info("[Storage] Resolved query '%s' -> '%s' (score: %.2f)", query, resolved, best_score if resolved else 0.0)
        return resolved

    async def resolve_leak_audio_url(self, song: Any) -> str | None:
        raw_items = recursive_file_objects(song)
        raw_items.extend(filename_list(song))

        era_hint = display(unwrap_named(first_value(song, "era", "era_name", "eraName")))
        if era_hint == "N/A":
            era_hint = None

        logger.info("[AudioResolver] Inspecting song metadata objects for audio links/paths...")
        for item in raw_items:
            direct = direct_file_url(item)
            if direct:
                logger.info("[AudioResolver] Found direct audio URL in song item: %s", direct)
                return direct

            path = file_path_from_item(item)
            if path:
                if "/" in path and ("Original Files" in path or "Tagged" in path):
                    url = make_download_url(path)
                    logger.info("[AudioResolver] Using existing categorized path: %s", url)
                    return url
                resolved = await self.resolve_storage_path(path, era_hint)
                if resolved:
                    url = make_download_url(resolved)
                    logger.info("[AudioResolver] Resolved metadata path to: %s", url)
                    return url

        title = get_song_title(song)
        logger.info("[AudioResolver] No direct link in metadata; falling back to storage index search for '%s'...", title)
        fallback_path = await self.resolve_storage_path(title, era_hint)
        if fallback_path:
            url = make_download_url(fallback_path)
            logger.info("[AudioResolver] Fallback matched storage path: %s", url)
            return url

        browse_results = await self.browse_directory(f"?search={quote(title)}")
        for item in browse_results:
            p = item.get("path")
            if p and any(p.lower().endswith(ext) for ext in (".mp3", ".wav", ".m4a", ".flac")):
                url = make_download_url(p)
                logger.info("[AudioResolver] Matched via live directory search: %s", url)
                return url

        logger.warning("[AudioResolver] Failed to resolve any audio file for '%s'", title)
        return None

    async def _ensure_cover_index(self) -> None:
        now = asyncio.get_running_loop().time()
        if self._cover_root_items and (now - self._cover_timestamp < 1800):
            return

        async with self._cover_lock:
            if self._cover_root_items and (now - self._cover_timestamp < 1800):
                return

            logger.info("[CoverArts] Indexing 'Cover Arts' root directories...")
            items = await self.browse_directory("Cover Arts")
            if items:
                self._cover_root_items = items
                self._cover_timestamp = now
                logger.info("[CoverArts] Indexed %d items/folders in 'Cover Arts'.", len(items))

    async def resolve_cover_arts_path(self, song: Any) -> str | None:
        meta_cover = get_cover_metadata_url(song)
        if meta_cover:
            logger.info("[CoverResolver] Found direct cover in song metadata: %s", meta_cover)
            return meta_cover

        await self._ensure_cover_index()
        title = get_song_title(song)
        clean_title = clean_match_key(title)
        era_hint = display(unwrap_named(first_value(song, "era", "era_name", "eraName"))).lower()

        logger.info("[CoverResolver] Searching 'Cover Arts' directory for cover matching '%s'...", title)
        matching_dirs = []
        for item in self._cover_root_items:
            path = item.get("path", "")
            name = item.get("name", "")
            is_dir = item.get("type") == "directory"
            clean_name = clean_match_key(name)

            if not is_dir and any(path.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp")):
                if clean_name == clean_title or clean_title in clean_name:
                    logger.info("[CoverResolver] Direct image file matched in Cover Arts: %s", path)
                    return make_download_url(path)

            if is_dir:
                score = difflib.SequenceMatcher(None, clean_title, clean_name).ratio()
                if clean_title in clean_name or clean_name in clean_title:
                    score += 0.25
                if era_hint != "n/a" and era_hint in path.lower():
                    score += 0.15
                if score >= 0.50:
                    matching_dirs.append((score, path))

        matching_dirs.sort(key=lambda x: x[0], reverse=True)

        for _, folder_path in matching_dirs[:5]:
            sub_items = await self.browse_directory(folder_path)
            for sub in sub_items:
                sub_path = sub.get("path", "")
                sub_name = clean_match_key(sub.get("name", ""))
                if any(sub_path.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp")):
                    if clean_title in sub_name or sub_name in clean_title:
                        logger.info("[CoverResolver] Found matching artwork inside '%s': %s", folder_path, sub_path)
                        return make_download_url(sub_path)

        if matching_dirs:
            folder_path = matching_dirs[0][1]
            sub_items = await self.browse_directory(folder_path)
            for sub in sub_items:
                sub_path = sub.get("path", "")
                if any(sub_path.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp")):
                    logger.info("[CoverResolver] Using first artwork from folder '%s': %s", folder_path, sub_path)
                    return make_download_url(sub_path)

        logger.warning("[CoverResolver] No cover art found in Cover Arts archive for '%s'", title)
        return None

    async def search_songs(self, query: str) -> list[dict]:
        data = await self.request(
            SONGS_ENDPOINT,
            params={
                "search": query,
                "page": 1,
                "page_size": MAX_RESULTS,
                "limit": MAX_RESULTS,
            },
        )
        if isinstance(data, list):
            return data[:MAX_RESULTS]
        if isinstance(data, dict):
            for key in ("results", "songs", "data", "items"):
                val = data.get(key)
                if isinstance(val, list):
                    return val[:MAX_RESULTS]
        return []

    async def get_song(self, song_id: Any) -> Any:
        for endpoint in (f"songs/{song_id}/", f"songs/{song_id}"):
            data = await self.request(endpoint)
            if data:
                return data
        return None

    async def download_to_file(self, url: str, destination: str) -> bool:
        await self.start()
        logger.info("[Downloader] Downloading stream from: %s", url)
        try:
            async with self.session.get(url, timeout=40) as resp:
                if resp.status != 200:
                    logger.warning("[Downloader] Download returned HTTP status %d", resp.status)
                    return False
                with open(destination, "wb") as f:
                    while chunk := await resp.content.read(65536):
                        f.write(chunk)
            size = os.path.getsize(destination)
            logger.info("[Downloader] Download completed successfully (%d bytes -> %s)", size, destination)
            return size > 500
        except Exception as e:
            logger.exception("[Downloader] Exception during download of '%s': %s", url, e)
            return False


async def get_audio_duration_ffprobe(audio_path: str) -> float:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        dur = float(stdout.decode().strip())
        logger.info("[FFprobe] Audio duration probed: %.2f seconds", dur)
        return dur
    except Exception as e:
        logger.warning("[FFprobe] Failed to probe duration (%s); defaulting to 180s", e)
        return 180.0


async def render_snippet_video(
    audio_path: str,
    cover_path: str | None,
    start_offset: int,
    output_path: str,
) -> None:
    has_valid_cover = cover_path and os.path.exists(cover_path) and os.path.getsize(cover_path) > 500
    if has_valid_cover:
        logger.info("[FFmpeg] Encoding video snippet with background image: %s", cover_path)
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_offset),
            "-t", str(SNIPPET_DURATION),
            "-i", audio_path,
            "-loop", "1",
            "-i", cover_path,
            "-c:v", "libx264",
            "-tune", "stillimage",
            "-preset", "ultrafast",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-vf", "scale=720:720:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=720:720:(ow-iw)/2:(oh-ih)/2:color=black",
            "-r", "30",
            "-t", str(SNIPPET_DURATION),
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ]
    else:
        logger.info("[FFmpeg] No cover art found; encoding video snippet with solid color background")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_offset),
            "-t", str(SNIPPET_DURATION),
            "-i", audio_path,
            "-f", "lavfi",
            "-i", "color=c=0x14121d:s=720x720:r=30",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-t", str(SNIPPET_DURATION),
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ]

    logger.info("[FFmpeg] Executing command: %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
    except asyncio.TimeoutError:
        proc.kill()
        logger.error("[FFmpeg] Video rendering timed out after 60 seconds")
        raise TimeoutError("FFmpeg encoding timed out.")

    if proc.returncode != 0:
        logger.error("[FFmpeg] Process exited with error code %d: %s", proc.returncode, stderr.decode(errors="replace"))
        raise RuntimeError("FFmpeg failed to encode video snippet.")

    logger.info("[FFmpeg] Snippet generated at: %s (%d bytes)", output_path, os.path.getsize(output_path))


class MakeSnipSelect(discord.ui.Select):
    def __init__(self, candidates: list[dict], author_id: int, cog: MakeSnip) -> None:
        self.candidates = candidates
        self.author_id = author_id
        self.cog = cog

        options = []
        for idx, result in enumerate(candidates[:MAX_RESULTS]):
            title = get_song_title(result)
            era = display(unwrap_named(first_value(result, "era", "era_name", "eraName")))
            desc = f"Era: {era}"[:100] if era != "N/A" else None
            options.append(
                discord.SelectOption(
                    label=title[:100],
                    value=str(idx),
                    description=desc,
                )
            )

        super().__init__(
            placeholder="Select the exact track to create snippet...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Only the command runner can choose.", ephemeral=True)
            return

        if self.view:
            self.view.selected = True
            self.view.stop()

        await interaction.response.defer()
        chosen = self.candidates[int(self.values[0])]
        await self.cog.process_and_send_snippet(interaction, chosen)


class MakeSnipSelectView(discord.ui.LayoutView):
    def __init__(self, query: str, candidates: list[dict], author_id: int, cog: MakeSnip) -> None:
        super().__init__(timeout=SELECT_TIMEOUT)
        self.selected = False
        self.message: Optional[discord.Message] = None

        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            discord.ui.TextDisplay(
                f"# Create Snippet\n"
                f"Found **{len(candidates)}** matches for **{query}**.\n"
                f"-# Choose the exact track below:"
            )
        )
        container.add_item(discord.ui.ActionRow(MakeSnipSelect(candidates, author_id, cog)))
        self.add_item(container)

    async def on_timeout(self) -> None:
        if self.selected or not self.message:
            return
        try:
            await self.message.edit(view=simple_view("⏱️ Selection timed out. Run the command again.", timeout=10))
        except Exception:
            pass


class MakeSnip(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.api = JuiceWRLDAPI()

    def cog_unload(self) -> None:
        asyncio.create_task(self.api.close())

    async def process_and_send_snippet(
        self,
        ctx_or_interaction: commands.Context | discord.Interaction,
        song: dict,
    ) -> None:
        title = get_song_title(song)
        era = display(unwrap_named(first_value(song, "era", "era_name", "eraName")))
        logger.info("[MakeSnip] Starting snippet pipeline for '%s' (Era: %s)", title, era)

        song_id = first_value(song, "id", "song_id", "songId", "public_id", "publicId")
        if song_id is not None:
            logger.info("[MakeSnip] Fetching full song details for ID %s...", song_id)
            full_song = await self.api.get_song(song_id)
            if full_song:
                wrapped = full_song.get("song") or full_song.get("data") if isinstance(full_song, dict) else None
                song = wrapped if isinstance(wrapped, dict) else full_song

        progress_view = simple_view(f"🔍 Resolving audio files for **{title}**...")
        if isinstance(ctx_or_interaction, discord.Interaction):
            status_msg = await ctx_or_interaction.edit_original_response(view=progress_view)
        else:
            status_msg = await ctx_or_interaction.send(view=progress_view)

        audio_url = await self.api.resolve_leak_audio_url(song)
        if not audio_url:
            logger.error("[MakeSnip] Audio resolution failed for '%s'", title)
            error_view = simple_view(f"❌ No playable audio file could be resolved for **{title}**.")
            if isinstance(ctx_or_interaction, discord.Interaction):
                await ctx_or_interaction.edit_original_response(view=error_view)
            else:
                await status_msg.edit(view=error_view)
            return

        logger.info("[MakeSnip] Resolving cover art...")
        cover_url = await self.api.resolve_cover_arts_path(song)

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_audio = os.path.join(tmp_dir, "track_audio.mp3")
            temp_cover = os.path.join(tmp_dir, "cover_art.png")
            clean_name = re.sub(r"[^\w\s-]", "", title).strip() or "snippet"
            out_video = os.path.join(tmp_dir, f"{clean_name}_snippet.mp4")

            render_view = simple_view(f"🎬 Downloading media and generating 30s snippet for **{title}**...")
            if isinstance(ctx_or_interaction, discord.Interaction):
                await ctx_or_interaction.edit_original_response(view=render_view)
            else:
                await status_msg.edit(view=render_view)

            audio_ok = await self.api.download_to_file(audio_url, temp_audio)
            if not audio_ok:
                logger.error("[MakeSnip] Failed to download audio from '%s'", audio_url)
                fail_view = simple_view("❌ Failed to download the song file from the server.")
                if isinstance(ctx_or_interaction, discord.Interaction):
                    await ctx_or_interaction.edit_original_response(view=fail_view)
                else:
                    await status_msg.edit(view=fail_view)
                return

            cover_ok = False
            if cover_url:
                cover_ok = await self.api.download_to_file(cover_url, temp_cover)

            actual_duration = await get_audio_duration_ffprobe(temp_audio)
            if actual_duration <= 0:
                actual_duration = parse_duration_seconds(song)

            if actual_duration > SNIPPET_DURATION + 15:
                start_sec = random.randint(15, int(actual_duration) - SNIPPET_DURATION - 5)
            elif actual_duration > SNIPPET_DURATION:
                start_sec = random.randint(0, int(actual_duration) - SNIPPET_DURATION)
            else:
                start_sec = 0

            logger.info("[MakeSnip] Chosen snippet time window: %ds - %ds (Total: %.2fs)", start_sec, start_sec + SNIPPET_DURATION, actual_duration)

            try:
                await render_snippet_video(
                    audio_path=temp_audio,
                    cover_path=temp_cover if cover_ok else None,
                    start_offset=start_sec,
                    output_path=out_video,
                )
            except Exception as e:
                logger.exception("[MakeSnip] FFmpeg rendering threw exception: %s", e)
                fail_view = simple_view("❌ Failed to render video snippet with FFmpeg.")
                if isinstance(ctx_or_interaction, discord.Interaction):
                    await ctx_or_interaction.edit_original_response(view=fail_view)
                else:
                    await status_msg.edit(view=fail_view)
                return

            if not os.path.exists(out_video) or os.path.getsize(out_video) == 0:
                logger.error("[MakeSnip] Output video does not exist or is empty")
                fail_view = simple_view("❌ Output video file was empty or missing.")
                if isinstance(ctx_or_interaction, discord.Interaction):
                    await ctx_or_interaction.edit_original_response(view=fail_view)
                else:
                    await status_msg.edit(view=fail_view)
                return

            video_size = os.path.getsize(out_video)
            if video_size > MAX_DISCORD_FILE_SIZE:
                logger.error("[MakeSnip] Rendered video exceeds 25MB (%d bytes)", video_size)
                fail_view = simple_view(f"❌ Video snippet exceeds Discord's 25MB limit ({video_size / (1024 * 1024):.1f} MB).")
                if isinstance(ctx_or_interaction, discord.Interaction):
                    await ctx_or_interaction.edit_original_response(view=fail_view)
                else:
                    await status_msg.edit(view=fail_view)
                return

            def fmt_time(s: int) -> str:
                m, sec = divmod(s, 60)
                return f"{m}:{sec:02d}"

            end_sec = start_sec + SNIPPET_DURATION
            embed = discord.Embed(
                title=f"🎬 {title}",
                description=f"**Era:** {era}\n**Snippet:** `{fmt_time(start_sec)} - {fmt_time(end_sec)}`",
                color=EMBED_COLOR,
            )
            if cover_url:
                embed.set_thumbnail(url=cover_url)

            btn_view = discord.ui.View()
            if audio_url:
                btn_view.add_item(
                    discord.ui.Button(
                        label="Full Track",
                        style=discord.ButtonStyle.link,
                        url=audio_url,
                        emoji="🎵",
                    )
                )

            channel = (
                ctx_or_interaction.channel
                if isinstance(ctx_or_interaction, discord.Interaction)
                else ctx_or_interaction.channel
            )

            file = discord.File(out_video, filename=f"{clean_name}_snippet.mp4")

            try:
                if isinstance(ctx_or_interaction, discord.Interaction):
                    await ctx_or_interaction.delete_original_response()
                else:
                    await status_msg.delete()
            except Exception:
                pass

            await channel.send(embed=embed, file=file, view=btn_view if btn_view.children else None)
            logger.info("[MakeSnip] Snippet video and playable media embed delivered successfully.")

    @commands.command(name="makesnip", aliases=["snipgen", "gensnip", "clipsnip"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def makesnip_command(self, ctx: commands.Context, *, query: str | None = None) -> None:
        if not query:
            help_view = simple_view(
                "# Command: makesnip\n\n"
                "**Syntax**\n"
                "`,makesnip <song>`\n\n"
                "**Example**\n"
                "`,makesnip rental`"
            )
            await ctx.send(view=help_view)
            return

        logger.info("[MakeSnip] Command invoked by %s (%d) with query: '%s'", ctx.author, ctx.author.id, query)
        searching_msg = await ctx.send(view=simple_view(f"🔍 Searching song for **{query}**..."))

        results = await self.api.search_songs(query)
        if not results:
            logger.warning("[MakeSnip] No songs found matching query '%s'", query)
            await searching_msg.edit(view=simple_view(f"❌ Couldn't find a song matching **{query}**."))
            return

        wanted = clean_match_key(query)
        exact_match = next(
            (r for r in results if clean_match_key(get_song_title(r)) == wanted),
            None,
        )

        if len(results) == 1 or exact_match is not None:
            chosen = exact_match or results[0]
            logger.info("[MakeSnip] Match selected: '%s'", get_song_title(chosen))
            await self.process_and_send_snippet(ctx, chosen)
            return

        logger.info("[MakeSnip] Found %d candidate songs; presenting select dropdown", len(results))
        dropdown_view = MakeSnipSelectView(
            query=query,
            candidates=results[:MAX_RESULTS],
            author_id=ctx.author.id,
            cog=self,
        )
        await searching_msg.edit(view=dropdown_view)
        dropdown_view.message = searching_msg

    @makesnip_command.error
    async def makesnip_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏱️ Try again in **{error.retry_after:.1f}s**.")
            return
        logger.exception("[MakeSnip] Unhandled command error: %s", error)
        await ctx.send(f"❌ **MakeSnip error:** `{error}`")

    @app_commands.command(name="makesnip", description="Create a 30-second video snippet of a Juice WRLD track")
    @app_commands.describe(query="Song title to search for")
    async def makesnip_slash(
        self,
        interaction: discord.Interaction,
        query: str | None = None,
    ) -> None:
        if not query:
            help_view = simple_view(
                "# Command: makesnip\n\n"
                "**Syntax**\n"
                "`,makesnip <song>`\n\n"
                "**Example**\n"
                "`,makesnip rental`"
            )
            await interaction.response.send_message(view=help_view)
            return

        await interaction.response.send_message(
            view=simple_view(f"🔍 Searching song for **{query}**...")
        )

        results = await self.api.search_songs(query)
        if not results:
            await interaction.edit_original_response(
                view=simple_view(f"❌ Couldn't find a song matching **{query}**.")
            )
            return

        wanted = clean_match_key(query)
        exact_match = next(
            (r for r in results if clean_match_key(get_song_title(r)) == wanted),
            None,
        )

        if len(results) == 1 or exact_match is not None:
            chosen = exact_match or results[0]
            await self.process_and_send_snippet(interaction, chosen)
            return

        dropdown_view = MakeSnipSelectView(
            query=query,
            candidates=results[:MAX_RESULTS],
            author_id=interaction.user.id,
            cog=self,
        )
        msg = await interaction.edit_original_response(view=dropdown_view)
        dropdown_view.message = msg

    @makesnip_slash.error
    async def makesnip_slash_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        logger.exception("[MakeSnip] Slash command error: %s", error)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"❌ **MakeSnip error:** `{error}`", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"❌ **MakeSnip error:** `{error}`", ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MakeSnip(bot))
