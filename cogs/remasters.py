from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import discord
from discord.ext import commands

try:
    from config import EMBED_COLOR
except ImportError:
    EMBED_COLOR = discord.Color.from_str("#2b2d31")

logger = logging.getLogger("juicevault.remasters")

JUICEVAULT_BASE = "https://api.juicevault.xyz"
DOWNLOAD_ENDPOINT = f"{JUICEVAULT_BASE}/music/download"

CACHE_TTL = 1800
SELECT_TIMEOUT = 120
VIEW_TIMEOUT = 900
MAX_META_LENGTH = 90


def get_remasters_file_path() -> Path:
    parent_path = Path(__file__).resolve().parent.parent / "remasters.json"
    if parent_path.is_file():
        return parent_path
    return Path.cwd() / "remasters.json"


def clean_match_key(value: str) -> str:
    if not value:
        return ""
    val = str(value)
    val = re.sub(r"\.[a-zA-Z0-9]+$", "", val)
    val = val.replace("&", " and ")
    val = re.sub(r"[_\-./\\+]+", " ", val)
    val = re.sub(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}", "", val)
    val = re.sub(r"\b(?:remasters?|remake|edit|session|stem|stems)\b", "", val, flags=re.IGNORECASE)
    val = re.sub(r"[^\w\s]", "", val)
    val = re.sub(r"\s+", " ", val).strip().lower()
    return val if val else str(value).strip().lower()


def truncate(value: str, limit: int = MAX_META_LENGTH) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


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


def resolve_vault_cover(path: str | None) -> str | None:
    if not path or not isinstance(path, str):
        return None
    path = path.strip()
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("//"):
        return f"https:{path}"
    if path.startswith("/"):
        return f"{JUICEVAULT_BASE}{path}"
    return f"{JUICEVAULT_BASE}/{path}"


def format_iso_date(iso_str: str | None) -> str | None:
    if not iso_str or not isinstance(iso_str, str):
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%B %d, %Y")
    except Exception:
        return iso_str.split("T")[0]


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


def make_link_button(label: str, url: str | None) -> discord.ui.Button | None:
    url = valid_http_url(url)
    if not url:
        return None
    return discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=url)


def build_remaster_header(remaster: dict[str, Any]) -> str:
    title = remaster.get("title") or remaster.get("file_name") or "Unknown Remaster"
    lines = [f"### **{title}**"]

    alt_names = remaster.get("alt_names") or []
    if isinstance(alt_names, str):
        alt_names = [a.strip() for a in alt_names.split(",") if a.strip()]
    if alt_names:
        clean_alts = [a for a in alt_names if a.lower() != title.lower()]
        if clean_alts:
            lines.append(f"-# Alt Name(s): **{', '.join(clean_alts)}**")

    artist = remaster.get("artist")
    if artist:
        lines.append(f"-# Artist: **{artist}**")

    category = remaster.get("category")
    if category:
        lines.append(f"-# Category: **{str(category).title()}**")

    return "\n".join(lines)


def build_remaster_details(remaster: dict[str, Any]) -> str:
    fields = []

    file_name = remaster.get("file_name")
    if file_name:
        fields.append(f"**File Name**\n{file_name}")

    length = remaster.get("length")
    if length:
        fields.append(f"**Length**\n{length}")

    file_size = remaster.get("file_size")
    if file_size:
        fields.append(f"**File Size**\n{file_size}")

    if remaster.get("is_session_edit"):
        fields.append("**Type**\nStudio Session Edit")

    added_at = format_iso_date(remaster.get("archive_added_at"))
    if added_at:
        fields.append(f"**Vault Archive Date**\n{added_at}")

    play_count = remaster.get("play_count")
    if play_count is not None:
        fields.append(f"**Vault Plays**\n{play_count:,}")

    if file_name:
        fields.append(f"**Available Files**\nRemaster: {file_name}")

    return "\n\n".join(fields)


class RemasterView(discord.ui.LayoutView):
    def __init__(self, remaster: dict[str, Any], timeout: int = VIEW_TIMEOUT) -> None:
        super().__init__(timeout=timeout)

        container = discord.ui.Container(accent_color=EMBED_COLOR)
        header_text = build_remaster_header(remaster)
        cover_url = resolve_vault_cover(remaster.get("cover"))

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

        details_text = build_remaster_details(remaster)
        if details_text:
            container.add_item(text_display(details_text))

        remaster_id = remaster.get("id")
        action_rows: list[discord.ui.ActionRow] = []

        if remaster_id:
            dl_url = f"{DOWNLOAD_ENDPOINT}/{remaster_id}"
            download_btn = make_link_button("Download", dl_url)
            if download_btn:
                action_rows.append(discord.ui.ActionRow(download_btn))

        if action_rows:
            container.add_item(small_separator())
            for row in action_rows:
                container.add_item(row)

        self.add_item(container)


class RemasterSelect(discord.ui.Select):
    def __init__(self, candidates: list[dict[str, Any]], author_id: int, cog: Remaster) -> None:
        self.candidates = candidates
        self.author_id = author_id
        self.cog = cog

        options = []
        for idx, item in enumerate(candidates[:25]):
            title = str(item.get("title") or item.get("file_name") or "Remaster")[:100]
            size = item.get("file_size") or ""
            length = item.get("length") or ""
            desc = f"{length} • {size}" if length and size else (length or size or "JuiceVault Remaster")

            options.append(
                discord.SelectOption(
                    label=title,
                    value=str(idx),
                    description=desc[:100],
                )
            )

        super().__init__(
            placeholder="Choose the correct remaster...",
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

        if self.view:
            self.view.selected = True
            self.view.stop()

        await interaction.response.defer()
        chosen = self.candidates[int(self.values[0])]
        logger.info("[RemasterSelect] Selected: %s (ID: %s)", chosen.get("title"), chosen.get("id"))

        try:
            view = RemasterView(chosen)
            await interaction.edit_original_response(view=view)
        except discord.HTTPException:
            logger.exception("[RemasterSelect] Layout view rejected.")
            await self.cog.edit_with_error(interaction, "❌ Discord rejected the remaster card.")
        except Exception:
            logger.exception("[RemasterSelect] Failed to render selected remaster.")
            await self.cog.edit_with_error(interaction, "❌ Failed to build the remaster card.")


class RemasterSelectView(discord.ui.LayoutView):
    def __init__(self, query: str, candidates: list[dict[str, Any]], author_id: int, cog: Remaster) -> None:
        super().__init__(timeout=SELECT_TIMEOUT)
        self.selected = False
        self.message: Optional[discord.Message] = None

        container = discord.ui.Container(accent_color=EMBED_COLOR)
        container.add_item(
            text_display(
                f"# Command: remaster\n"
                f"Found **{len(candidates)}** matching remasters for **{query}**.\n"
                f"-# Select the correct track from the dropdown below:"
            )
        )
        container.add_item(discord.ui.ActionRow(RemasterSelect(candidates, author_id, cog)))
        self.add_item(container)

    async def on_timeout(self) -> None:
        if self.selected or not self.message:
            return
        try:
            await self.message.edit(
                view=simple_view("⏱️ Selection timed out. Please run the command again.", timeout=10)
            )
        except Exception:
            pass


class JuiceVaultRemasterManager:
    def __init__(self) -> None:
        self._remasters_cache: list[dict[str, Any]] = []
        self._cache_timestamp: float = 0
        self._cache_lock = asyncio.Lock()

    @staticmethod
    def _read_file_sync(path: Path) -> Any:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def extract_results(data: Any) -> list[dict[str, Any]]:
        if not data:
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("remasters", "stems", "data", "songs", "results", "items", "files"):
                val = data.get(key)
                if isinstance(val, list):
                    return [item for item in val if isinstance(item, dict)]
            for val in data.values():
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    return val
        return []

    async def _ensure_remasters_index(self, force: bool = False) -> list[dict[str, Any]]:
        now = asyncio.get_running_loop().time()
        if not force and self._remasters_cache and (now - self._cache_timestamp < CACHE_TTL):
            return self._remasters_cache

        async with self._cache_lock:
            if not force and self._remasters_cache and (now - self._cache_timestamp < CACHE_TTL):
                return self._remasters_cache

            path = get_remasters_file_path()
            logger.info("[JuiceVault] Loading remasters from disk: %s", path)

            if not path.is_file():
                logger.error("[JuiceVault] Local remasters.json file does not exist at: %s", path)
                return self._remasters_cache

            try:
                data = await asyncio.to_thread(self._read_file_sync, path)
                remasters = self.extract_results(data)
                if remasters:
                    self._remasters_cache = remasters
                    self._cache_timestamp = now
                    logger.info("[JuiceVault] Successfully loaded %d remasters from %s", len(remasters), path.name)
                else:
                    logger.warning("[JuiceVault] %s parsed successfully but yielded 0 items.", path.name)
            except Exception as e:
                logger.exception("[JuiceVault] Failed to parse %s: %s", path, e)

        return self._remasters_cache

    async def search_remasters(self, query: str) -> list[dict[str, Any]]:
        remasters = await self._ensure_remasters_index()
        if not remasters:
            remasters = await self._ensure_remasters_index(force=True)
            if not remasters:
                return []

        clean_q = clean_match_key(query)
        raw_q = query.strip().lower()
        if not clean_q and not raw_q:
            return []

        q_tokens = [tok for tok in clean_q.split() if tok]

        exact_matches: list[dict[str, Any]] = []
        word_boundary_matches: list[dict[str, Any]] = []
        token_matches: list[dict[str, Any]] = []
        partial_matches: list[dict[str, Any]] = []
        fuzzy_matches: list[tuple[float, dict[str, Any]]] = []

        for remaster in remasters:
            title = remaster.get("title") or ""
            file_name = remaster.get("file_name") or ""
            clean_title = clean_match_key(title)
            clean_file = clean_match_key(file_name)

            alt_names = remaster.get("alt_names") or []
            if isinstance(alt_names, str):
                alt_names = [a.strip() for a in alt_names.split(",") if a.strip()]
            clean_alts = [clean_match_key(a) for a in alt_names if clean_match_key(a)]

            all_clean = [k for k in (clean_title, clean_file, *clean_alts) if k]
            all_raw = [str(k).lower() for k in (title, file_name, *alt_names) if k]

            if clean_q in all_clean or raw_q in all_raw:
                exact_matches.append(remaster)
                continue

            matched_wb = False
            for k in all_clean:
                if re.search(rf"\b{re.escape(clean_q)}\b", k):
                    word_boundary_matches.append(remaster)
                    matched_wb = True
                    break
            if matched_wb:
                continue

            if q_tokens and all(any(tok in k for k in all_clean) for tok in q_tokens):
                token_matches.append(remaster)
                continue

            matched_partial = False
            for k in all_clean:
                if (len(clean_q) >= 2 and clean_q in k) or (len(k) >= 2 and k in clean_q):
                    partial_matches.append(remaster)
                    matched_partial = True
                    break
            if matched_partial:
                continue

            best_score = 0.0
            for k in all_clean:
                score = difflib.SequenceMatcher(None, clean_q, k).ratio()
                if score > best_score:
                    best_score = score

            if best_score >= 0.60:
                fuzzy_matches.append((best_score, remaster))

        if exact_matches:
            return exact_matches[:25]

        fuzzy_matches.sort(key=lambda x: x[0], reverse=True)
        ordered_fuzzy = [item for _, item in fuzzy_matches]

        combined: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for pool in (word_boundary_matches, token_matches, partial_matches, ordered_fuzzy):
            for s in pool:
                s_id = str(s.get("id") or s.get("file_name") or id(s))
                if s_id not in seen_ids:
                    seen_ids.add(s_id)
                    combined.append(s)

        return combined[:25]


class Remaster(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.manager = JuiceVaultRemasterManager()

    async def send_error(self, ctx: commands.Context, message: str) -> None:
        await ctx.send(view=simple_view(message))

    async def edit_with_error(self, interaction: discord.Interaction, message: str) -> None:
        try:
            await interaction.edit_original_response(view=simple_view(message))
        except Exception:
            pass

    @commands.command(name="remaster", aliases=["remasters", "vaultremaster", "remasterdownload"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def remaster_command(self, ctx: commands.Context, *, query: str | None = None) -> None:
        if not query:
            help_view = simple_view(
                "# Command: remaster\n\n"
                "**Syntax**\n"
                "`,remaster <song>`\n\n"
                "**Example**\n"
                "`,remaster 20`\n"
                "`,remaster netflix & pills`"
            )
            await ctx.send(view=help_view)
            return

        searching_msg = await ctx.send(view=simple_view(f"🔍 Searching JuiceVault remasters for **{query}**..."))

        results = await self.manager.search_remasters(query)
        if not results:
            await searching_msg.edit(
                view=simple_view(f"❌ Couldn't find any remasters matching **{query}**.")
            )
            return

        clean_q = clean_match_key(query)
        exact_match = next(
            (
                s for s in results
                if clean_match_key(s.get("title", "")) == clean_q
                or clean_match_key(s.get("file_name", "")) == clean_q
            ),
            None,
        )

        if len(results) == 1 or exact_match is not None:
            chosen = exact_match or results[0]
            try:
                view = RemasterView(chosen)
                await searching_msg.edit(view=view)
            except discord.HTTPException:
                logger.exception("[Command:remaster] Discord layout rejected.")
                await searching_msg.edit(view=simple_view("❌ Discord rejected the remaster card."))
            except Exception:
                logger.exception("[Command:remaster] Failed to build direct remaster card.")
                await searching_msg.edit(view=simple_view("❌ Failed to build the remaster card."))
            return

        dropdown_view = RemasterSelectView(
            query=query,
            candidates=results,
            author_id=ctx.author.id,
            cog=self,
        )
        await searching_msg.edit(view=dropdown_view)
        dropdown_view.message = searching_msg

    @remaster_command.error
    async def remaster_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏱️ Try again in **{error.retry_after:.1f}s**.")
            return

        logger.exception("[Command:remaster] Unhandled command error: %s", error)
        await ctx.send(f"❌ **Remaster error:** `{error}`")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Remaster(bot))
