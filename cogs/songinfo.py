from __future__ import annotations

import asyncio
import difflib
import html
import json
import logging
import re
from typing import Any, Optional
from urllib.parse import quote, urlparse

import aiohttp
import discord
from discord.ext import commands

try:
    from config import EMBED_COLOR
except ImportError:
    EMBED_COLOR = discord.Color.from_str("#2b2d31")

logger = logging.getLogger(__name__)

API_ROOT = "https://juicewrldapi.com"
API_BASE = f"{API_ROOT}/juicewrld"
SONGS_ENDPOINT = f"{API_BASE}/songs/"
DOWNLOAD_BASE = f"{API_BASE}/files/download/"

API_TIMEOUT = 25
CATALOG_PAGE_SIZE = 100
CACHE_TTL = 3600
SELECT_TIMEOUT = 120
VIEW_TIMEOUT = 900
MAX_META_LENGTH = 90
MAX_NOTES_LENGTH = 160

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
    "file_names", "fileNames", "file_name", "fileName", "filename",
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


def display_list(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip() or default
    if isinstance(value, (list, tuple, set)):
        parts = [display(item, "") for item in value]
        joined = ", ".join(part for part in parts if part)
        return joined or default
    return display(value, default)


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
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def clean_meta_text(value: str) -> str:
    value = value.strip()
    value = re.sub(
        r"^(?:recorded|first previewed|previewed|surfaced|released|file exported)[\s,.:-]*",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()
    value = re.sub(r"^[,.:\s]+", "", value).strip()
    value = re.sub(r"[,.\s]+$", "", value).strip()
    return re.sub(r"\s+", " ", value)


def clean_era_display(era_raw: Any) -> str | None:
    if not era_raw:
        return None
    val = ""
    if isinstance(era_raw, dict):
        val = era_raw.get("description") or era_raw.get("name") or era_raw.get("title") or ""
    elif isinstance(era_raw, str):
        val = era_raw
    if not val or val == "N/A":
        return None
    val = re.sub(r"\s*\([^)]*\)", "", val)
    val = re.sub(r"\s+era$", "", val, flags=re.IGNORECASE)
    return val.strip() or None


def truncate(value: str, limit: int = MAX_META_LENGTH) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def compact_value(value: str, *, limit: int = MAX_META_LENGTH) -> str | None:
    value = display(value)
    if not value or value == "N/A":
        return None
    return truncate(value.replace("\n", ", "), limit)


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


def song_text(song: Any, *keys: str, default: Any = None) -> str:
    return display(first_value(song, *keys, default=default))


def song_list(song: Any, *keys: str) -> str:
    return display_list(first_value(song, *keys, default=None))


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


def get_cover_url(song: Any) -> str | None:
    for field in COVER_FIELDS:
        value = first_value(song, field, default=None)
        if isinstance(value, dict):
            value = get_value(value, "url", "src", "href", "image_url", "imageUrl")
        result = resolve_cover_url(value)
        if result:
            return result
    return None


def get_length(song: Any) -> str:
    value = first_value(
        song,
        "length", "duration", "duration_text", "durationText",
        default=None,
    )
    if isinstance(value, (int, float)):
        minutes, seconds = divmod(int(value), 60)
        return f"{minutes}:{seconds:02d}"
    return display(value)


def get_surfaced(song: Any) -> str:
    value = first_value(
        song,
        "surfaced", "surfaced_date", "surfacedDate",
        "surface_date", "surfaceDate", "date_leaked",
        default=None,
    )
    if value is not None:
        return display(value)
    dates = first_value(song, "dates", "important_dates", "importantDates", default=None)
    if isinstance(dates, dict):
        nested = (
            dates.get("surfaced")
            or dates.get("surface")
            or dates.get("surfaced_date")
            or dates.get("surfacedDate")
            or dates.get("date_leaked")
        )
        if nested:
            return display(nested)
    return "N/A"


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


def find_primary_file(tagged: list[dict], original: list[dict]) -> dict | None:
    for item in (*original, *tagged):
        if valid_http_url(item.get("download")):
            return item
    return None


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


def make_link_button(label: str, url: str | None) -> discord.ui.Button | None:
    url = valid_http_url(url)
    if not url:
        return None
    return discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=url)


def collect_download_files(
    tagged: list[dict],
    original: list[dict],
) -> list[dict]:
    files: list[dict] = []
    for item in original:
        files.append({**item, "kind": "Original"})
    for item in tagged:
        files.append({**item, "kind": "Tagged"})
    return files


def build_info_header(song: Any) -> str:
    title = get_song_title(song)
    lines = [f"### **{title}**"]

    aka = song_list(
        song,
        "track_titles",
        "trackTitles",
        "alternative_titles",
        "alternativeTitles",
        "alternate_titles",
        "alternateTitles",
    )
    if aka and aka != "N/A":
        parts = [p.strip() for p in aka.split(",") if p.strip() and p.strip().casefold() != title.casefold()]
        if parts:
            lines.append(f"-# Alt Name(s): **{', '.join(parts)}**")

    engineers = song_list(song, "engineers", "engineer", "engineer_names", "engineerNames")
    if engineers and engineers != "N/A":
        lines.append(f"-# Engineer(s): **{engineers}**")

    producers = song_list(song, "producers", "producer", "producer_names", "producerNames")
    if producers and producers != "N/A":
        lines.append(f"-# Producer(s): **{producers}**")

    return "\n".join(lines)


def build_info_details(
    song: Any,
    all_files: list[dict] | None = None,
    active_file: dict | None = None,
    matched_field: str | None = None,
) -> str | None:
    fields: list[str] = []

    era = clean_era_display(first_value(song, "era", "era_name", "eraName"))
    if era:
        fields.append(f"**Era**\n{era}")

    file_name = clean(first_value(song, *FILENAME_KEYS))
    if file_name and str(file_name) != "N/A":
        fields.append(f"**File Name**\n{str(file_name).strip()}")
    elif active_file and active_file.get("name"):
        fields.append(f"**File Name**\n{active_file['name']}")

    instrumentals = song_list(
        song,
        "instrumental_names",
        "instrumentalNames",
        "instrumentals",
        "instrumental",
        "instrumental_files",
        "instrumentalFiles",
    )
    if instrumentals and instrumentals != "N/A":
        fields.append(f"**Instrumentals**\n{instrumentals}")

    location = display(
        unwrap_named(
            first_value(
                song,
                "recording_locations",
                "recordingLocations",
                "recording_location",
                "recordingLocation",
                "location",
            )
        )
    )
    if location and location != "N/A":
        fields.append(f"**Recording Location**\n{location}")

    recorded = song_list(
        song,
        "record_dates",
        "recordDates",
        "recording_dates",
        "recordingDates",
        "recording_date",
        "recordingDate",
        "recorded",
        "recorded_date",
        "recordedDate",
    )
    if recorded and recorded != "N/A":
        raw_rec = clean_meta_text(recorded)
        if re.search(r"\bFile Exported\b", raw_rec, flags=re.IGNORECASE):
            match = re.search(r"^(.*?)(?:[;,]|\s)*\bFile Exported\b[\s,:.-]*(.*)$", raw_rec, flags=re.IGNORECASE)
            if match:
                rec_part = clean_meta_text(match.group(1))
                exp_part = clean_meta_text(match.group(2))
                rec_lines = []
                if rec_part:
                    rec_lines.append(f"**Recorded**\n{rec_part}")
                if exp_part:
                    rec_lines.append(f"**File Exported**\n{exp_part}")
                if rec_lines:
                    fields.append("\n".join(rec_lines))
            else:
                fields.append(f"**Recorded**\n{raw_rec}")
        else:
            fields.append(f"**Recorded**\n{raw_rec}")

    previewed = song_text(
        song,
        "preview_date",
        "previewDate",
        "first_previewed",
        "firstPreviewed",
        "first_preview_date",
        "firstPreviewDate",
    )
    if previewed and previewed != "N/A":
        fields.append(f"**First Previewed**\n{clean_meta_text(previewed)}")

    surfaced = get_surfaced(song)
    if surfaced and surfaced != "N/A":
        fields.append(f"**Surfaced**\n{clean_meta_text(surfaced)}")

    length = get_length(song)
    if length and length != "N/A":
        fields.append(f"**Length**\n{length}")

    category = display(
        unwrap_named(
            first_value(
                song,
                "category",
                "category_name",
                "categoryName",
                "status",
                "leak_type",
            )
        )
    )
    if category and category != "N/A":
        fields.append(f"**Category**\n{category}")

    bitrate = clean(first_value(song, "bitrate", "available_files", "availableFiles"))
    if bitrate and str(bitrate) != "N/A":
        fields.append(f"**Available Files**\n{str(bitrate).strip()}")
    elif all_files:
        lines = [f"{f.get('kind', 'File')}: {f.get('name', 'Audio')}" for f in all_files[:3]]
        if lines:
            fields.append(f"**Available Files**\n" + "\n".join(lines))

    additional_info = song_text(
        song,
        "additional_information",
        "additionalInformation",
        "additional_info",
        "additionalInfo",
        "info",
        "notes",
    )
    if additional_info and additional_info != "N/A":
        fields.append(f"-# {truncate(additional_info, MAX_NOTES_LENGTH)}")

    if matched_field:
        fields.append(f"-# 🔍 Matched query on: **{matched_field}**")

    return "\n\n".join(fields) if fields else None


class SongInfoFileSelect(discord.ui.Select):
    def __init__(
        self,
        *,
        song: Any,
        tagged: list[dict],
        original: list[dict],
        files: list[dict],
        selected_index: int | None = None,
        matched_field: str | None = None,
    ) -> None:
        self.song = song
        self.tagged = tagged
        self.original = original
        self.files = files
        self.matched_field = matched_field

        options: list[discord.SelectOption] = []
        for idx, item in enumerate(files[:25]):
            name = truncate(str(item.get("name", "File")), 90)
            kind = str(item.get("kind", "File"))
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=str(idx),
                    description=kind[:100],
                    default=selected_index == idx,
                )
            )

        placeholder = "Choose a file..."
        if selected_index is not None and 0 <= selected_index < len(files):
            placeholder = truncate(str(files[selected_index].get("name", "File")), 80)

        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        item = self.files[int(self.values[0])]
        download = valid_http_url(item.get("download"))
        if not download:
            await interaction.response.send_message(
                "No download link is available for that file.",
                ephemeral=True,
            )
            return

        view = SongInfoView(
            self.song,
            tagged=self.tagged,
            original=self.original,
            selected_index=int(self.values[0]),
            matched_field=self.matched_field,
        )
        await interaction.response.edit_message(view=view)


class SongInfoView(discord.ui.LayoutView):
    def __init__(
        self,
        song: dict[str, Any],
        *,
        tagged: list[dict] | None = None,
        original: list[dict] | None = None,
        selected_index: int | None = None,
        matched_field: str | None = None,
        timeout: int = VIEW_TIMEOUT,
    ):
        super().__init__(timeout=timeout)

        tagged = tagged or []
        original = original or []
        cover = get_cover_url(song)
        all_files = collect_download_files(tagged, original)

        if selected_index is not None and 0 <= selected_index < len(all_files):
            active_file = all_files[selected_index]
        else:
            active_file = find_primary_file(tagged, original)

        container = discord.ui.Container(accent_color=EMBED_COLOR)

        header_text = build_info_header(song)
        if cover:
            container.add_item(
                discord.ui.Section(
                    text_display(header_text),
                    accessory=discord.ui.Thumbnail(media=cover),
                )
            )
        else:
            container.add_item(text_display(header_text))

        container.add_item(small_separator())

        details_text = build_info_details(
            song,
            all_files=all_files,
            active_file=active_file,
            matched_field=matched_field,
        )
        if details_text:
            container.add_item(text_display(details_text))

        action_rows: list[discord.ui.ActionRow | SongInfoFileSelect] = []

        if active_file:
            download_button = make_link_button("Download", valid_http_url(active_file.get("download")))
            if download_button:
                action_rows.append(discord.ui.ActionRow(download_button))

        if len(all_files) > 1:
            action_rows.append(
                SongInfoFileSelect(
                    song=song,
                    tagged=tagged,
                    original=original,
                    files=all_files,
                    selected_index=selected_index,
                    matched_field=matched_field,
                )
            )

        if action_rows:
            container.add_item(small_separator())
            for row in action_rows:
                if isinstance(row, discord.ui.ActionRow):
                    container.add_item(row)
                else:
                    container.add_item(discord.ui.ActionRow(row))

        self.add_item(container)


class SongSelect(discord.ui.Select):
    def __init__(self, candidates: list[dict[str, Any]], author_id: int, cog: SongInfo):
        self.candidates = candidates
        self.author_id = author_id
        self.cog = cog

        options = []
        for idx, item in enumerate(candidates[:25]):
            song = item["song"]
            matched_field = item["matched_field"]
            title = get_song_title(song)[:100]
            era = display(unwrap_named(first_value(song, "era", "era_name", "eraName")))

            desc = f"Era: {era} · Matched: {matched_field}"[:100]

            options.append(
                discord.SelectOption(
                    label=title,
                    value=str(idx),
                    description=desc,
                )
            )

        super().__init__(
            placeholder="Select a song to view its info...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Only the person who ran the command can choose.", ephemeral=True)
            return

        if self.view:
            self.view.selected = True
            self.view.stop()

        await interaction.response.defer()

        chosen_entry = self.candidates[int(self.values[0])]
        view = await self.cog.resolve_song_view(
            chosen_entry["song"],
            matched_field=chosen_entry["matched_field"],
        )
        await interaction.edit_original_response(view=view)


class SongSelectView(discord.ui.LayoutView):
    def __init__(self, query: str, candidates: list[dict[str, Any]], author_id: int, cog: SongInfo):
        super().__init__(timeout=SELECT_TIMEOUT)
        self.selected = False
        self.message: Optional[discord.Message] = None

        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"# Metadata Search\n"
                f"Found **{len(candidates)}** match{'es' if len(candidates) != 1 else ''} for **{query}**.\n"
                f"-# Select a song below to inspect full metadata:"
            )
        )
        container.add_item(discord.ui.ActionRow(SongSelect(candidates, author_id, cog)))
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


class SongInfoAPI:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None
        self._storage_files: list[dict[str, str]] = []
        self._storage_timestamp: float = 0
        self._storage_lock = asyncio.Lock()

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": "Timezones-Bot/1.0"},
            )

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def request(self, endpoint: str, params: dict | None = None) -> Any:
        await self.start()
        url = f"{API_BASE}/{endpoint.lstrip('/')}"
        try:
            async with self.session.get(url, params=params) as response:
                if response.status != 200:
                    return None
                return await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError):
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

            collected: list[dict[str, str]] = []
            for base_folder in ("Original Files", "Tagged Files"):
                top_items = await self.browse_directory(base_folder)
                directories = [item for item in top_items if item.get("type") == "directory"]
                for item in top_items:
                    if item.get("type") != "directory" and item.get("path"):
                        collected.append(
                            {"name": item.get("name", ""), "path": item.get("path", "")}
                        )

                if directories:
                    tasks = [self.browse_directory(sub["path"]) for sub in directories]
                    res_list = await asyncio.gather(*tasks, return_exceptions=True)
                    for res in res_list:
                        if isinstance(res, list):
                            for sub_item in res:
                                if sub_item.get("path"):
                                    collected.append(
                                        {
                                            "name": sub_item.get("name", ""),
                                            "path": sub_item.get("path", ""),
                                        }
                                    )

            if collected:
                self._storage_files = collected
                self._storage_timestamp = now

    async def resolve_storage_path(self, query: str, era_hint: str | None = None) -> str | None:
        await self._ensure_storage_index()
        if not self._storage_files:
            return None

        clean_target = clean_match_key(query)
        if not clean_target:
            return None

        exact_match: str | None = None
        best_match: str | None = None
        best_score: float = 0.0

        for item in self._storage_files:
            path = item.get("path", "")
            name = item.get("name", "")
            base = name.rsplit(".", 1)[0] if "." in name else name
            clean_item = clean_match_key(base)

            if clean_item == clean_target:
                if era_hint and era_hint.lower() in path.lower():
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

        return exact_match or best_match


async def resolve_one_file(api: SongInfoAPI, item: Any, era_hint: str | None = None) -> dict | None:
    path = file_path_from_item(item)
    direct = direct_file_url(item)

    if isinstance(item, str):
        name = item.split("/")[-1]
    else:
        name = display(
            get_value(item, "filename", "file_name", "fileName", "name", "title", default="Song"),
            "Song",
        )

    resolved_path: str | None = None
    if path:
        if "/" in path and ("Original Files" in path or "Tagged" in path):
            resolved_path = path
        else:
            resolved_path = await api.resolve_storage_path(path, era_hint)
            if not resolved_path:
                resolved_path = path

    if not resolved_path and name:
        resolved_path = await api.resolve_storage_path(name, era_hint)

    final_path = resolved_path or path
    download = direct

    if final_path:
        download = make_download_url(final_path)

    if not download:
        return None

    display_name = (final_path.split("/")[-1] if final_path and "/" in final_path else name)[:80]
    return {"name": display_name, "download": download}


async def build_file_groups(
    api: SongInfoAPI,
    song: Any,
) -> tuple[list[dict], list[dict], int]:
    raw_items = recursive_file_objects(song)
    raw_items.extend(filename_list(song))

    era_hint = display(unwrap_named(first_value(song, "era", "era_name", "eraName")))
    if era_hint == "N/A":
        era_hint = None

    unique: list[Any] = []
    seen: set[str] = set()
    for item in raw_items:
        identity = (file_path_from_item(item) or direct_file_url(item) or str(item)).casefold()
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(item)

    tagged: list[dict] = []
    original: list[dict] = []
    for item in unique:
        resolved = await resolve_one_file(api, item, era_hint)
        if not resolved:
            continue
        text = f"{resolved['name']} {str(item).lower()}"
        if "tag" in text:
            tagged.append(resolved)
        else:
            original.append(resolved)

    if not original and not tagged:
        title = get_song_title(song)
        fallback_path = await api.resolve_storage_path(title, era_hint)
        if fallback_path:
            url = make_download_url(fallback_path)
            item_dict = {
                "name": fallback_path.split("/")[-1][:80],
                "download": url,
            }
            original.append(item_dict)

    return tagged, original, max(len(unique), len(tagged) + len(original))


class SongInfo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self.api = SongInfoAPI()
        self._catalog_cache: list[dict[str, Any]] = []
        self._cache_timestamp: float = 0
        self._index_lock = asyncio.Lock()

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                headers=HEADERS,
            )
        return self.session

    def cog_unload(self):
        async def close():
            if self.session and not self.session.closed:
                await self.session.close()
            await self.api.close()

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(close())
        except RuntimeError:
            pass

    async def _fetch_catalog_page(self, page_num: int) -> list[dict[str, Any]]:
        session = await self.get_session()
        url = f"{SONGS_ENDPOINT}?page={page_num}&page_size={CATALOG_PAGE_SIZE}"
        try:
            async with session.get(url, headers=HEADERS, timeout=15) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    data = parse_api_json(text)
                    if isinstance(data, dict):
                        return data.get("results", [])
                    if isinstance(data, list):
                        return data
        except Exception:
            pass
        return []

    async def _ensure_catalog_index(self) -> None:
        now = asyncio.get_running_loop().time()
        if self._catalog_cache and (now - self._cache_timestamp < CACHE_TTL):
            return

        async with self._index_lock:
            if self._catalog_cache and (now - self._cache_timestamp < CACHE_TTL):
                return

            session = await self.get_session()
            total_pages = 28
            try:
                async with session.get(f"{SONGS_ENDPOINT}?page=1&page_size={CATALOG_PAGE_SIZE}", headers=HEADERS) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        data = parse_api_json(text)
                        if isinstance(data, dict):
                            count = data.get("count", 0)
                            if count:
                                total_pages = (count // CATALOG_PAGE_SIZE) + 1
            except Exception:
                pass

            tasks = [self._fetch_catalog_page(p) for p in range(1, total_pages + 1)]
            pages_results = await asyncio.gather(*tasks, return_exceptions=True)

            all_songs = []
            seen_ids = set()
            for page in pages_results:
                if isinstance(page, list):
                    for item in page:
                        sid = item.get("id") or item.get("name")
                        if sid not in seen_ids:
                            seen_ids.add(sid)
                            all_songs.append(item)

            if all_songs:
                self._catalog_cache = all_songs
                self._cache_timestamp = now
                logger.info("[SongInfo] Indexed %d tracks for metadata search.", len(all_songs))

    async def get_song_details(self, song_id: Any) -> Optional[dict[str, Any]]:
        session = await self.get_session()
        for endpoint in (f"songs/{song_id}/", f"songs/{song_id}"):
            url = f"{API_BASE}/{endpoint}"
            try:
                async with session.get(url, headers=HEADERS, timeout=12) as response:
                    if response.status == 200:
                        text = await response.text()
                        data = parse_api_json(text)
                        if isinstance(data, dict):
                            song_obj = data.get("song") or data.get("data") or data
                            return song_obj if isinstance(song_obj, dict) else data
            except Exception:
                continue
        return None

    def search_metadata(self, query: str) -> list[dict[str, Any]]:
        clean_q = query.strip().lower()
        if not clean_q:
            return []

        exact_matches = []
        partial_matches = []
        fuzzy_matches = []

        for song in self._catalog_cache:
            title = str(get_song_title(song)).lower()
            aka = str(song_list(song, "track_titles", "trackTitles", "alternative_titles")).lower()
            instrumentals = str(song_list(song, "instrumental_names", "instrumentals", "instrumental_files")).lower()
            recorded = str(song_list(song, "record_dates", "recordDates", "recording_date")).lower()
            producers = str(song_list(song, "producers", "producer_names")).lower()
            engineers = str(song_list(song, "engineers", "engineer_names")).lower()
            location = str(song_text(song, "recording_locations", "location")).lower()
            era = str(song_text(song, "era")).lower()

            matched_field = None

            if clean_q in instrumentals:
                matched_field = f"Instrumental ({song_list(song, 'instrumental_names', 'instrumentals')})"
            elif clean_q in recorded:
                matched_field = f"Recorded Date ({song_list(song, 'record_dates')})"
            elif clean_q in title:
                matched_field = "Title"
            elif clean_q in aka:
                matched_field = "Alternate Name"
            elif clean_q in producers:
                matched_field = f"Producer ({song_list(song, 'producers')})"
            elif clean_q in engineers:
                matched_field = f"Engineer ({song_list(song, 'engineers')})"
            elif clean_q in location:
                matched_field = f"Location ({song_text(song, 'recording_locations')})"
            elif clean_q in era:
                matched_field = f"Era ({song_text(song, 'era')})"

            if matched_field:
                entry = {"song": song, "matched_field": matched_field}
                if clean_q == title or clean_q == instrumentals:
                    exact_matches.append(entry)
                else:
                    partial_matches.append(entry)
                continue

            score_inst = difflib.SequenceMatcher(None, clean_q, instrumentals).ratio() if len(clean_q) >= 4 else 0
            score_title = difflib.SequenceMatcher(None, clean_q, title).ratio()
            best_score = max(score_inst, score_title)

            if best_score >= 0.70:
                field_label = "Instrumental" if score_inst > score_title else "Title"
                fuzzy_matches.append((best_score, {"song": song, "matched_field": f"Fuzzy {field_label}"}))

        if exact_matches:
            return exact_matches[:25]

        fuzzy_matches.sort(key=lambda x: x[0], reverse=True)
        ordered_fuzzy = [item for _, item in fuzzy_matches]

        combined = partial_matches + [item for item in ordered_fuzzy if item not in partial_matches]
        return combined[:25]

    async def resolve_song_view(self, song: dict, matched_field: str | None = None) -> SongInfoView:
        song_id = first_value(song, "id", "song_id", "public_id")
        if song_id:
            full_song = await self.get_song_details(song_id)
            if full_song:
                song = full_song

        tagged, original, _ = await build_file_groups(self.api, song)
        return SongInfoView(
            song,
            tagged=tagged,
            original=original,
            matched_field=matched_field,
        )

    @commands.command(name="songinfo", aliases=["si", "trackinfo"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def songinfo_command(self, ctx: commands.Context, *, query: str | None = None) -> None:
        if not query:
            help_view = simple_view(
                "# Command: songinfo\n"
                "-# Search Juice WRLD songs by title, instrumental name, recording date, or credits.\n\n"
                "**Syntax** · `,songinfo <query>`\n"
                "**Examples** · `,si xr 5 gz` · `,si june 2019` · `,si gezin`"
            )
            await ctx.send(view=help_view)
            return

        searching_msg = await ctx.send(view=simple_view(f"🔍 Searching metadata for **{query}**..."))

        await self._ensure_catalog_index()
        candidates = self.search_metadata(query)

        if not candidates:
            session = await self.get_session()
            try:
                async with session.get(SONGS_ENDPOINT, params={"search": query, "page_size": 10}, headers=HEADERS) as resp:
                    if resp.status == 200:
                        data = parse_api_json(await resp.text())
                        results = data.get("results", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                        for s in results:
                            candidates.append({"song": s, "matched_field": "API Title Search"})
            except Exception:
                pass

        if not candidates:
            await searching_msg.edit(
                view=simple_view(f"❌ No songs found matching metadata query **{query}**.")
            )
            return

        if len(candidates) == 1:
            chosen_entry = candidates[0]
            view = await self.resolve_song_view(chosen_entry["song"], matched_field=chosen_entry["matched_field"])
            await searching_msg.edit(view=view)
            return

        dropdown_view = SongSelectView(
            query=query,
            candidates=candidates,
            author_id=ctx.author.id,
            cog=self,
        )
        await searching_msg.edit(view=dropdown_view)
        dropdown_view.message = searching_msg

    @songinfo_command.error
    async def songinfo_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ Try again in **{error.retry_after:.1f}s**.")
            return

        logger.exception("[SongInfo] Command error: %s", error)
        await ctx.send(f"❌ **SongInfo error:** `{error}`")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SongInfo(bot))
