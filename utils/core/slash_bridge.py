"""
Slash command bridge.

Automatically registers an ``app_commands.Command`` for every prefix
command in every cog so that the bot works with ``/`` commands alongside
the regular ``,`` prefix commands.

Each bridged app command builds a synthetic ``Context`` from the
interaction (``Context.from_interaction``) and then invokes the original
prefix command callback untouched. This keeps a single source of truth
for behaviour while giving full slash-command coverage.
"""

from __future__ import annotations

import inspect
import logging
import re
import types
from typing import Any, Callable, Optional, Union, get_args, get_origin, Literal

import discord
from discord import app_commands
from discord.ext import commands

from utils.core.context import Context
from utils.core.converters import (
    AttachmentConverter,
    HexConverter,
    MemberConverter,
    MessageConverter,
    RoleConverter,
    Timespan,
    UserConverter,
)
from utils.core.errors import user_message_for_error
from utils.core.helpers import SendHelp

logger = logging.getLogger("timezones.slash_bridge")

NAME_RE = re.compile(r"^[\w-]{1,32}$")

# Prefix converter -> native app command parameter type
CONVERTER_APP_TYPES: dict[type, type] = {
    MemberConverter: discord.Member,
    UserConverter: discord.User,
    RoleConverter: discord.Role,
    commands.MemberConverter: discord.Member,
    commands.UserConverter: discord.User,
    commands.RoleConverter: discord.Role,
    commands.TextChannelConverter: discord.TextChannel,
    commands.GuildChannelConverter: discord.abc.GuildChannel,
}

# Prefix converters that stay raw strings in the picker and are converted
# manually right before the original callback runs.
STRING_CONVERTERS: dict[type, type] = {
    Timespan: Timespan,
    HexConverter: HexConverter,
    MessageConverter: MessageConverter,
    AttachmentConverter: AttachmentConverter,
}

NATIVE_APP_TYPES: tuple[type, ...] = (
    discord.Member,
    discord.User,
    discord.Role,
    discord.TextChannel,
    discord.VoiceChannel,
    discord.Thread,
    discord.CategoryChannel,
    discord.StageChannel,
    discord.Attachment,
    discord.abc.GuildChannel,
    discord.Object,
)

BASE_TYPES: tuple[type, ...] = (str, int, float, bool)

# Some prefix groups have a main command that also takes parameters.
# Discord cannot attach options to a group, so those mains are exposed as
# a subcommand. The key is the fully qualified prefix path (e.g. "role" or
# "history remove") and the value is the subcommand name used instead.
GROUP_MAIN_RENAMES: dict[str, str] = {
    "role": "update",
    "history": "view",
    "history remove": "case",
    "lockdown": "lock",
    "lockdown remove": "channel",
    "unlock": "remove",
    "untimeout": "remove",
    "unmute": "remove",
    "purge": "standard",
    "notes": "view",
}

# Raised internally to abort an invocation (e.g. a custom converter failed).
_STOP = object()


def _parameters_config(prefix_cmd: commands.Command) -> dict[str, Any]:
    """Return the ``parameters={...}`` flag configuration for a command."""
    config = getattr(prefix_cmd, "parameters", None)
    if not config:
        config = prefix_cmd.__original_kwargs__.get("parameters")
    return config or {}


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Return ``(base_annotation, is_optional)`` for ``Optional``/``| None``."""
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def _is_greedy(annotation: Any) -> bool:
    return isinstance(annotation, commands.Greedy)


def _app_type_for(annotation: Any) -> Any:
    if annotation in CONVERTER_APP_TYPES:
        return CONVERTER_APP_TYPES[annotation]
    if annotation in STRING_CONVERTERS:
        return str
    if annotation in NATIVE_APP_TYPES:
        return annotation
    if annotation in BASE_TYPES:
        return annotation
    return str


def _union_app_type(annotation: Any) -> Any:
    """Pick the widest supported type for ``Union`` annotations."""
    args = [a for a in get_args(annotation) if a is not type(None)]
    mapped = {_app_type_for(a) for a in args}
    if discord.Member in mapped:
        return discord.Member
    if discord.User in mapped:
        return discord.User
    if discord.Role in mapped:
        return discord.Role
    if len(mapped) == 1:
        return mapped.pop()
    return str


def _resolve_param(param: inspect.Parameter) -> dict[str, Any]:
    """Translate a prefix command parameter into a slash parameter spec."""
    annotation = param.annotation
    if annotation is param.empty:
        annotation = str

    is_greedy = _is_greedy(annotation)
    if is_greedy:
        annotation = annotation.converter

    base, optional = _unwrap_optional(annotation)

    literal_args: tuple[Any, ...] | None = None
    if get_origin(base) is Literal:
        literal_args = tuple(get_args(base))
        base = type(literal_args[0]) if literal_args else str
    elif get_origin(base) in (Union, types.UnionType):
        base = _union_app_type(base)

    if base not in STRING_CONVERTERS:
        base = _app_type_for(base)

    required = param.default is param.empty and not optional

    return {
        "name": param.name,
        "annotation": base,
        "optional": optional,
        "required": required,
        "kind": param.kind,
        "literal_args": literal_args,
        "converter": STRING_CONVERTERS.get(base),
        "is_greedy": is_greedy,
        "is_var": param.kind is inspect.Parameter.VAR_POSITIONAL,
        "description": None,
    }


def _render_annotation(spec: dict[str, Any]) -> str:
    if spec["literal_args"]:
        rendered = f"Literal[{', '.join(repr(a) for a in spec['literal_args'])}]"
    else:
        rendered = _type_repr(spec["annotation"])
    if spec["optional"]:
        return f"Optional[{rendered}]"
    return rendered


def _type_repr(typ: Any) -> str:
    if typ is str:
        return "str"
    if typ is int:
        return "int"
    if typ is float:
        return "float"
    if typ is bool:
        return "bool"
    if typ is discord.Member:
        return "discord.Member"
    if typ is discord.User:
        return "discord.User"
    if typ is discord.Role:
        return "discord.Role"
    if typ is discord.TextChannel:
        return "discord.TextChannel"
    if typ is discord.VoiceChannel:
        return "discord.VoiceChannel"
    if typ is discord.Thread:
        return "discord.Thread"
    if typ is discord.CategoryChannel:
        return "discord.CategoryChannel"
    if typ is discord.StageChannel:
        return "discord.StageChannel"
    if typ is discord.Attachment:
        return "discord.Attachment"
    if typ is discord.abc.GuildChannel:
        return "discord.abc.GuildChannel"
    if typ is discord.Object:
        return "discord.Object"
    return "str"


def _generate_callback(
    bot: commands.Bot,
    cog: commands.Cog,
    prefix_cmd: commands.Command,
    param_specs: list[dict[str, Any]],
    flag_specs: list[dict[str, Any]],
) -> Callable[..., Any]:
    """Build a callback whose signature matches the slash command options."""
    specs = list(param_specs)
    specs.extend(flag_specs)

    required = [s for s in specs if s["required"]]
    optional = [s for s in specs if not s["required"]]

    signature: list[str] = []
    for spec in (*required, *optional):
        ann = _render_annotation(spec)
        if spec["required"]:
            signature.append(f"{spec['name']}: {ann}")
        else:
            signature.append(f"{spec['name']}: {ann} = None")

    signature_code = ", ".join(signature)
    kwargs_code = ", ".join(f"'{spec['name']}': {spec['name']}" for spec in specs)

    code = (
        "async def __bridged_callback(interaction: discord.Interaction"
        + (f", {signature_code}" if signature_code else "")
        + ") -> None:\n"
        + f"    await _bridged_invoke(_BOT, _PREFIX_CMD, _COG, interaction, **{{{kwargs_code}}})"
    )

    namespace: dict[str, Any] = {
        "discord": discord,
        "Optional": Optional,
        "Union": Union,
        "Literal": Literal,
        "commands": commands,
        "_BOT": bot,
        "_PREFIX_CMD": prefix_cmd,
        "_COG": cog,
        "_bridged_invoke": _bridged_invoke,
    }
    exec(code, namespace)  # noqa: S102 - generated from a fixed template
    return namespace["__bridged_callback"]


async def _convert_flag(config: dict[str, Any], ctx: Context, raw: Any) -> Any:
    if raw is None:
        return None
    if config.get("require_value", True) is False:
        return True
    converter = config.get("converter", str)
    if converter is int:
        return int(raw)
    if isinstance(converter, type) and issubclass(converter, commands.Converter):
        return await converter().convert(ctx, raw)
    if callable(converter):
        return converter(raw)
    return raw


async def _resolve_value(
    ctx: Context,
    prefix_cmd: commands.Command,
    param: inspect.Parameter,
    value: Any,
    interaction: discord.Interaction,
) -> Any:
    if value is None:
        default = param.default
        if default is commands.Author:
            return interaction.user
        if default is not param.empty:
            return default
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            return ()
        if _is_greedy(param.annotation):
            return []
        return None

    annotation = param.annotation
    if _is_greedy(annotation):
        annotation = annotation.converter
    base, _ = _unwrap_optional(annotation)
    if get_origin(base) in (Union, types.UnionType):
        base = None
    converter_cls = STRING_CONVERTERS.get(base) if base is not None else None

    if converter_cls is not None:
        try:
            return await converter_cls().convert(ctx, value)
        except SendHelp:
            await ctx.send_help(prefix_cmd.qualified_name)
            return _STOP
        except commands.BadArgument as error:
            await ctx.error(str(error) or "One of the arguments was invalid.")
            return _STOP

    if _is_greedy(param.annotation):
        return [value]
    if param.kind is inspect.Parameter.VAR_POSITIONAL:
        return (value,)
    return value


async def _bridged_invoke(
    bot: commands.Bot,
    prefix_cmd: commands.Command,
    cog: commands.Cog,
    interaction: discord.Interaction,
    **values: Any,
) -> None:
    ctx = await Context.from_interaction(interaction)
    ctx.command = prefix_cmd

    # Flags from the prefix command's custom "--flag" system become plain
    # optional options here and are handed to the callback via ctx.parameters.
    ctx.parameters = {}
    params_config = _parameters_config(prefix_cmd)
    signature_names = {name for name in prefix_cmd.clean_params if name not in ("self", "ctx")}
    for flag, config in params_config.items():
        if flag in signature_names:
            continue
        ctx.parameters[flag] = await _convert_flag(config, ctx, values.get(flag))

    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    for name, param in prefix_cmd.clean_params.items():
        if name in ("self", "ctx"):
            continue
        value = await _resolve_value(ctx, prefix_cmd, param, values.get(name), interaction)
        if value is _STOP:
            return
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            # ``_resolve_value`` returns a tuple for var-positional params,
            # which must be unpacked into the positional arguments.
            args.extend(value)
        elif param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD:
            args.append(value)
        else:
            kwargs[name] = value

    try:
        await prefix_cmd.callback(cog, ctx, *args, **kwargs)
    except Exception as error:
        handler = getattr(prefix_cmd, "on_error", None)
        if handler is None:
            raise
        wrapped = (
            error
            if isinstance(error, commands.CommandError)
            else commands.CommandInvokeError(prefix_cmd, error)
        )
        if hasattr(handler, "__get__"):
            handler = handler.__get__(cog, type(cog))
        await handler(ctx, wrapped)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _bucket_key(bucket_type: Any) -> Callable[[discord.Interaction], Any]:
    if bucket_type is commands.BucketType.user:
        return lambda i: i.user.id
    if bucket_type is commands.BucketType.guild:
        return lambda i: (i.guild or i.user).id
    if bucket_type is commands.BucketType.channel:
        return lambda i: i.channel_id
    if bucket_type is commands.BucketType.member:
        return lambda i: (i.guild_id, i.user.id)
    if bucket_type is commands.BucketType.category:
        return lambda i: getattr(i.channel, "category_id", None) or i.channel_id
    if bucket_type is commands.BucketType.role:
        return lambda i: (getattr(i.user, "top_role", None) or i.user).id
    return lambda i: None


def _make_prefix_checks_check(checks: list[Any]) -> Callable[[discord.Interaction], Any]:
    async def app_check(interaction: discord.Interaction) -> bool:
        ctx = await Context.from_interaction(interaction)
        for check in checks:
            predicate = getattr(check, "predicate", check)
            try:
                result = predicate(ctx)
                if inspect.isawaitable(result):
                    result = await result
            except commands.CommandError as error:
                raise app_commands.CheckFailure(
                    str(error) or "You can't use this command."
                ) from None
            if not result:
                raise app_commands.CheckFailure("You can't use this command.")
        return True

    return app_check


def _make_cog_check_app(cog: commands.Cog) -> Callable[[discord.Interaction], Any]:
    async def app_check(interaction: discord.Interaction) -> bool:
        ctx = await Context.from_interaction(interaction)
        try:
            result = await cog.cog_check(ctx)
        except commands.CommandError as error:
            raise app_commands.CheckFailure(
                str(error) or "You can't use this command."
            ) from None
        if not result:
            raise app_commands.CheckFailure("You can't use this command.")
        return True

    return app_check


def _apply_checks(
    bot: commands.Bot,
    cog: commands.Cog,
    prefix_cmd: commands.Command,
    app_cmd: app_commands.Command,
) -> None:
    user_perms: dict[str, bool] = {}
    runtime_checks: list[Any] = []

    for check in prefix_cmd.checks:
        predicate = getattr(check, "predicate", check)
        qualname = getattr(predicate, "__qualname__", "") or ""
        outer = qualname.split(".", 1)[0]

        try:
            closure = inspect.getclosurevars(predicate)
        except TypeError:
            closure = None
        perms = closure.nonlocals.get("perms") if closure else None

        if outer == "has_permissions" and isinstance(perms, dict):
            user_perms.update(perms)
        elif outer == "guild_only":
            app_cmd.guild_only = True
        else:
            runtime_checks.append(check)

    if user_perms:
        app_cmd.default_permissions = discord.Permissions(**user_perms)
    if runtime_checks:
        app_cmd.add_check(_make_prefix_checks_check(runtime_checks))

    # Prefix cooldowns translate to app command cooldowns. Applying the
    # returned decorator to the app command registers the real predicate.
    buckets = getattr(prefix_cmd, "_buckets", None)
    if buckets is not None and getattr(buckets, "valid", False):
        cooldown = getattr(buckets, "_cooldown", None)
        if cooldown is not None:
            app_commands.checks.cooldown(
                cooldown.rate,
                cooldown.per,
                key=_bucket_key(getattr(buckets, "type", None)),
            )(app_cmd)

    # Cog-wide checks (e.g. Moderation requires the bot to have admin).
    cog_check = getattr(type(cog), "cog_check", None)
    if cog_check is not None and cog_check is not commands.Cog.cog_check:
        app_cmd.add_check(_make_cog_check_app(cog))


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def _build_leaf(
    bot: commands.Bot,
    cog: commands.Cog,
    prefix_cmd: commands.Command,
    *,
    parent: app_commands.Group | None = None,
    slash_name: str | None = None,
) -> app_commands.Command:
    name = slash_name or prefix_cmd.name

    param_specs: list[dict[str, Any]] = []
    for param_name, param in prefix_cmd.clean_params.items():
        if param_name in ("self", "ctx"):
            continue
        spec = _resolve_param(param)
        spec["name"] = param_name
        param_specs.append(spec)

    flag_specs: list[dict[str, Any]] = []
    params_config = _parameters_config(prefix_cmd)
    signature_names = {spec["name"] for spec in param_specs}
    for flag, config in params_config.items():
        if flag in signature_names:
            continue
        require_value = config.get("require_value", True)
        flag_specs.append(
            {
                "name": flag,
                "annotation": bool if not require_value else str,
                "optional": True,
                "required": False,
                "kind": inspect.Parameter.KEYWORD_ONLY,
                "literal_args": None,
                "converter": None,
                "is_greedy": False,
                "is_var": False,
                "description": config.get("description"),
            }
        )

    callback = _generate_callback(bot, cog, prefix_cmd, param_specs, flag_specs)
    description = (prefix_cmd.short_doc or "…").splitlines()[0][:100]

    app_cmd = app_commands.Command(
        name=name,
        description=description,
        callback=callback,
        parent=parent,
        extras={"bridged": True, "prefix_command": prefix_cmd.qualified_name},
    )
    _apply_checks(bot, cog, prefix_cmd, app_cmd)
    return app_cmd


def _build_group_main(
    bot: commands.Bot,
    cog: commands.Cog,
    prefix_cmd: commands.Group,
    group: app_commands.Group,
) -> app_commands.Command | None:
    has_own_params = any(
        name not in ("self", "ctx") for name in prefix_cmd.clean_params
    ) or bool(_parameters_config(prefix_cmd))
    if not has_own_params:
        return None

    parts = [parent.name for parent in getattr(prefix_cmd, "parents", ())]
    qualified = " ".join([*parts, prefix_cmd.name])

    slash_name = GROUP_MAIN_RENAMES.get(qualified)
    if slash_name is None:
        logger.warning(
            "Group %r has its own parameters but no slash name mapping; "
            "skipping its main command",
            qualified,
        )
        return None

    return _build_leaf(bot, cog, prefix_cmd, parent=group, slash_name=slash_name)


def _build_command(
    bot: commands.Bot,
    cog: commands.Cog,
    prefix_cmd: commands.Command,
    *,
    parent: app_commands.Group | None = None,
) -> app_commands.Command | app_commands.Group | None:
    if isinstance(prefix_cmd, commands.Group) and prefix_cmd.commands:
        # NOTE: Group.__init__ auto-registers itself into ``parent`` when
        # ``parent=`` is passed, so it must be constructed without it and
        # added via ``Group.add_command`` below instead.
        group = app_commands.Group(
            name=prefix_cmd.name,
            description=(prefix_cmd.short_doc or "…").splitlines()[0][:100],
        )
        main = _build_group_main(bot, cog, prefix_cmd, group)
        if main is not None:
            group.add_command(main)
        for child in prefix_cmd.commands:
            built = _build_command(bot, cog, child, parent=group)
            if built is not None:
                group.add_command(built)
        return group

    return _build_leaf(bot, cog, prefix_cmd, parent=parent)


def _already_registered(bot: commands.Bot, name: str) -> bool:
    return bot.tree.get_command(name) is not None


def bridge_bot(bot: commands.Bot) -> None:
    """Register slash commands for every prefix command on every cog."""
    bridged = 0
    skipped = 0

    for cog in bot.cogs.values():
        for prefix_cmd in cog.get_commands():
            if not NAME_RE.match(prefix_cmd.name):
                logger.warning(
                    "Skipping %s.%s: name is not a valid slash command name",
                    cog.qualified_name,
                    prefix_cmd.name,
                )
                skipped += 1
                continue
            if _already_registered(bot, prefix_cmd.name):
                skipped += 1
                continue

            built = _build_command(bot, cog, prefix_cmd)
            if built is None:
                skipped += 1
                continue

            try:
                bot.tree.add_command(built)
            except Exception:
                logger.exception(
                    "Failed to register /%s from %s.%s",
                    built.name,
                    cog.qualified_name,
                    prefix_cmd.name,
                )
                skipped += 1
                continue

            bridged += 1
            logger.info(
                "Bridged %s.%s -> /%s",
                cog.qualified_name,
                prefix_cmd.name,
                built.name,
            )

    logger.info(
        "Slash bridge complete: %d bridged, %d skipped",
        bridged,
        skipped,
    )


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def _error_message(error: Any) -> str:
    if isinstance(error, app_commands.CommandOnCooldown):
        return f"Slow down — try again in **{error.retry_after:.1f}s**."
    if isinstance(error, app_commands.CheckFailure):
        return str(error) or "You can't use this command."
    return user_message_for_error(error)


async def handle_application_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    """Global handler for application command errors."""
    original = error.original if isinstance(error, app_commands.CommandInvokeError) else error

    if isinstance(original, (app_commands.CheckFailure, app_commands.CommandOnCooldown)):
        logger.info(
            "Application command check failure (%s): %s",
            getattr(interaction.command, "name", "?"),
            original,
        )
    else:
        logger.exception(
            "Application command error (%s): %s",
            getattr(interaction.command, "name", "?"),
            original,
        )

    embed = discord.Embed(
        description=_error_message(original),
        color=getattr(interaction.client, "color", discord.Color.red()),
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass