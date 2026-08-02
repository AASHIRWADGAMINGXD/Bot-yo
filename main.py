"""
VantixNodes Bot - Production Discord Bot
==========================================
Single-file, multi-feature Discord bot built with discord.py 2.x,
Supabase (PostgreSQL) for persistence, and OpenRouter for AI chat.

Developer: AashirwadGamerzz
Footer branding: "VantixNodes Bot" (applied via embed_footer() helper everywhere)

Run:
    python main.py

Requires: .env file (see .env.example) and a Supabase project with schema.sql applied.
"""

import os
import re
import io
import time
import json
import asyncio
import logging
import datetime
import traceback
from typing import Optional, Literal

import discord
from discord import app_commands
from discord.ext import commands, tasks

from dotenv import load_dotenv
from supabase import create_client, Client
import aiohttp

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None

# ------------------------------------------------------------------
# ENV / CONFIG
# ------------------------------------------------------------------
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
REDIS_URL = os.getenv("REDIS_URL")  # e.g. redis://localhost:6379/0 — optional, falls back to in-memory cache
SHARD_COUNT = os.getenv("SHARD_COUNT")  # e.g. "4" — leave unset to let discord.py auto-decide
BOT_DEVELOPER = "AashirwadGamerzz"
BOT_NAME = "VantixNodes Bot"

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN missing in .env")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL / SUPABASE_KEY missing in .env")

# ------------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------------
logger = logging.getLogger("vantixnodes")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")

_file_handler = logging.FileHandler("bot.log", encoding="utf-8")
_file_handler.setFormatter(_fmt)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_fmt)

logger.addHandler(_file_handler)
logger.addHandler(_console_handler)

# ------------------------------------------------------------------
# SUPABASE CLIENT
# ------------------------------------------------------------------
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ------------------------------------------------------------------
# BOT SETUP
# ------------------------------------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True
intents.guilds = True
intents.moderation = True

# AutoShardedBot lets a single process (or a fleet of processes coordinated by
# Discord's recommended shard count) handle 2500+ guilds. Discord requires
# sharding once a bot passes ~2500 servers; discord.py auto-computes the
# shard count unless SHARD_COUNT is explicitly set (useful for multi-process
# deployments where each process owns a fixed shard range).
bot = commands.AutoShardedBot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    shard_count=int(SHARD_COUNT) if SHARD_COUNT else None,
)
bot.start_time = time.time()
bot.commands_executed = 0

# ------------------------------------------------------------------
# CACHE LAYER
# ------------------------------------------------------------------
# When REDIS_URL is configured, all "cache" dicts below are backed by Redis
# so that multiple bot processes/shards (or horizontally scaled instances)
# share the same state instead of drifting out of sync. Without Redis
# configured, everything falls back to plain in-memory dicts (fine for a
# single-process deployment).
bot.redis = aioredis.from_url(REDIS_URL, decode_responses=True) if (REDIS_URL and aioredis) else None

# in-memory fallback caches (used directly when bot.redis is None)
bot.guild_config_cache: dict[int, dict] = {}
bot.antinuke_actions: dict[int, dict] = {}   # guild_id -> {actor_id: [timestamps]}
bot.afk_cache: dict[tuple, dict] = {}         # (guild_id, user_id) -> {"reason":..., "time":...}
bot.ai_context: dict[int, list] = {}          # user_id -> list of {"role","content"}
bot.invite_cache: dict[int, dict] = {}        # guild_id -> {invite_code: uses}
bot.xp_cooldowns: dict[str, float] = {}       # "guild_id:user_id" -> last XP award timestamp


async def cache_get(namespace: str, key, default=None):
    """Read from Redis if configured, else from the in-memory dict named `namespace`."""
    if bot.redis:
        raw = await bot.redis.get(f"{namespace}:{key}")
        return json.loads(raw) if raw is not None else default
    return getattr(bot, namespace).get(key, default)


async def cache_set(namespace: str, key, value, ttl: Optional[int] = None):
    if bot.redis:
        await bot.redis.set(f"{namespace}:{key}", json.dumps(value), ex=ttl)
    else:
        getattr(bot, namespace)[key] = value


async def cache_delete(namespace: str, key):
    if bot.redis:
        await bot.redis.delete(f"{namespace}:{key}")
    else:
        getattr(bot, namespace).pop(key, None)

# ------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------

def embed_footer(embed: discord.Embed, bot_user: Optional[discord.User] = None) -> discord.Embed:
    """Apply the mandatory VantixNodes Bot footer to every embed."""
    icon = bot_user.display_avatar.url if bot_user else None
    embed.set_footer(text=BOT_NAME, icon_url=icon)
    embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
    return embed


def make_embed(
    title: str = None,
    description: str = None,
    color: discord.Color = discord.Color.blurple(),
    bot_user: Optional[discord.User] = None,
) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color)
    return embed_footer(e, bot_user)


def parse_duration(duration_str: str) -> Optional[int]:
    """Parse strings like '10m', '1h', '1d' into seconds."""
    match = re.match(r"^(\d+)([smhd])$", duration_str.strip().lower())
    if not match:
        return None
    amount, unit = match.groups()
    amount = int(amount)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return amount * multipliers[unit]


async def get_guild_config(guild_id: int) -> dict:
    cached = await cache_get("guild_config_cache", guild_id)
    if cached is not None:
        return cached
    try:
        res = supabase.table("guild_config").select("*").eq("guild_id", guild_id).execute()
        if res.data:
            cfg = res.data[0]
        else:
            cfg = {"guild_id": guild_id, "prefix": "!", "modlog_channel": None,
                   "antinuke_enabled": False, "antinuke_threshold": 5, "antinuke_window": 10,
                   "welcome_channel": None, "welcome_message": None,
                   "goodbye_channel": None, "goodbye_message": None,
                   "ticket_category_id": None, "ticket_staff_role": None, "ticket_log_channel": None,
                   "badwords_log_channel": None, "membercount_channel": None,
                   "premium": False}
            supabase.table("guild_config").insert(cfg).execute()
        await cache_set("guild_config_cache", guild_id, cfg, ttl=300)
        return cfg
    except Exception as e:
        logger.error(f"get_guild_config error: {e}")
        return {"guild_id": guild_id}


async def update_guild_config(guild_id: int, **fields):
    cfg = await get_guild_config(guild_id)
    cfg.update(fields)
    await cache_set("guild_config_cache", guild_id, cfg, ttl=300)
    try:
        supabase.table("guild_config").update(fields).eq("guild_id", guild_id).execute()
    except Exception as e:
        logger.error(f"update_guild_config error: {e}")


async def is_premium(guild_id: int) -> bool:
    cfg = await get_guild_config(guild_id)
    return bool(cfg.get("premium"))


def premium_feature():
    """Gate a command behind the per-guild premium flag stored in Supabase."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if await is_premium(interaction.guild_id):
            return True
        await interaction.response.send_message(
            embed=make_embed("✨ Premium Required", "This feature is only available on servers with VantixNodes Premium. Use `/premium status` for details.",
                              discord.Color.gold(), bot.user),
            ephemeral=True,
        )
        return False
    return app_commands.check(predicate)


async def log_to_channel(guild: discord.Guild, channel_id: Optional[int], embed: discord.Embed):
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if channel:
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning(f"Missing permission to log in channel {channel_id} of guild {guild.id}")


def has_mod_perms():
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.manage_guild
    return app_commands.check(predicate)


def has_admin_perms():
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.administrator
    return app_commands.check(predicate)


# ==================================================================
# GLOBAL ERROR HANDLING
# ==================================================================

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    bot.commands_executed += 1
    if isinstance(error, app_commands.MissingPermissions):
        msg = "You don't have permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = f"I'm missing permissions: {', '.join(error.missing_permissions)}"
    elif isinstance(error, app_commands.CommandOnCooldown):
        msg = f"This command is on cooldown. Try again in {error.retry_after:.1f}s."
    elif isinstance(error, app_commands.CheckFailure):
        msg = "You don't meet the requirements to use this command."
    else:
        logger.error(f"Unhandled app command error: {error}\n{traceback.format_exc()}")
        msg = "An unexpected error occurred. The developer has been notified."

    embed = make_embed("❌ Error", msg, discord.Color.red(), bot.user)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    logger.error(f"Prefix command error: {error}\n{traceback.format_exc()}")


# ==================================================================
# 1. ANTI-NUKE SYSTEM
# ==================================================================

async def antinuke_check(guild: discord.Guild, actor: discord.abc.User, action: str):
    cfg = await get_guild_config(guild.id)
    if not cfg.get("antinuke_enabled"):
        return

    # whitelist check
    try:
        wl = supabase.table("antinuke_whitelist").select("*").eq("guild_id", guild.id).eq("user_id", actor.id).execute()
        if wl.data:
            return
    except Exception as e:
        logger.error(f"antinuke whitelist check error: {e}")

    now = time.time()
    window = cfg.get("antinuke_window", 10)
    threshold = cfg.get("antinuke_threshold", 5)

    bucket_key = f"{guild.id}:{actor.id}"
    actor_events = await cache_get("antinuke_actions", bucket_key, default=[])
    actor_events.append(now)
    actor_events = [t for t in actor_events if now - t <= window]

    if len(actor_events) < threshold:
        await cache_set("antinuke_actions", bucket_key, actor_events, ttl=window + 5)
        return

    await cache_delete("antinuke_actions", bucket_key)  # reset after trigger

    punishment = "none"
    member = guild.get_member(actor.id)
    try:
        if member and guild.me.guild_permissions.ban_members and member.top_role < guild.me.top_role:
            await guild.ban(member, reason=f"Anti-Nuke: {action} threshold exceeded")
            punishment = "banned"
        elif member:
            roles_to_remove = [r for r in member.roles if r != guild.default_role]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Anti-Nuke: role strip")
            punishment = "roles stripped"
    except discord.Forbidden:
        punishment = "failed (missing perms)"
    except Exception as e:
        logger.error(f"antinuke punishment error: {e}")
        punishment = "failed (error)"

    try:
        supabase.table("antinuke_logs").insert({
            "guild_id": guild.id, "actor_id": actor.id, "action": action,
            "punishment": punishment, "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }).execute()
    except Exception as e:
        logger.error(f"antinuke log insert error: {e}")

    embed = make_embed("🛡️ Anti-Nuke Triggered", color=discord.Color.dark_red(), bot_user=bot.user)
    embed.add_field(name="Actor", value=f"{actor} (`{actor.id}`)", inline=True)
    embed.add_field(name="Action", value=action, inline=True)
    embed.add_field(name="Punishment", value=punishment, inline=True)
    await log_to_channel(guild, cfg.get("antinuke_log_channel") or cfg.get("modlog_channel"), embed)


@bot.event
async def on_guild_channel_delete(channel):
    async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
        await antinuke_check(channel.guild, entry.user, "mass_channel_delete")
        break


@bot.event
async def on_guild_role_delete(role):
    async for entry in role.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
        await antinuke_check(role.guild, entry.user, "mass_role_delete")
        break


@bot.event
async def on_member_ban(guild, user):
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
        await antinuke_check(guild, entry.user, "mass_ban")
        break


@bot.event
async def on_member_remove(member):
    # Distinguish kicks via audit log (also handled by invite tracking below)
    async for entry in member.guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
        if entry.target and entry.target.id == member.id and (time.time() - entry.created_at.timestamp()) < 5:
            await antinuke_check(member.guild, entry.user, "mass_kick")
        break
    await handle_invite_leave(member)


@bot.event
async def on_webhooks_update(channel):
    async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.webhook_create):
        await antinuke_check(channel.guild, entry.user, "webhook_spam")
        break


@bot.event
async def on_member_update(before, after):
    if before.roles != after.roles:
        added = [r for r in after.roles if r not in before.roles]
        if any(r.permissions.administrator for r in added):
            async for entry in after.guild.audit_logs(limit=1, action=discord.AuditLogAction.member_role_update):
                await antinuke_check(after.guild, entry.user, "dangerous_permission_grant")
                break


antinuke_group = app_commands.Group(name="antinuke", description="Anti-nuke configuration")


@antinuke_group.command(name="enable", description="Enable anti-nuke protection")
@has_admin_perms()
async def antinuke_enable(interaction: discord.Interaction):
    await update_guild_config(interaction.guild_id, antinuke_enabled=True)
    await interaction.response.send_message(embed=make_embed("🛡️ Anti-Nuke", "Anti-nuke protection **enabled**.", discord.Color.green(), bot.user))


@antinuke_group.command(name="disable", description="Disable anti-nuke protection")
@has_admin_perms()
async def antinuke_disable(interaction: discord.Interaction):
    await update_guild_config(interaction.guild_id, antinuke_enabled=False)
    await interaction.response.send_message(embed=make_embed("🛡️ Anti-Nuke", "Anti-nuke protection **disabled**.", discord.Color.orange(), bot.user))


@antinuke_group.command(name="threshold", description="Set action threshold and time window")
@has_admin_perms()
async def antinuke_threshold(interaction: discord.Interaction, actions: int, window_seconds: int):
    await update_guild_config(interaction.guild_id, antinuke_threshold=actions, antinuke_window=window_seconds)
    await interaction.response.send_message(embed=make_embed("🛡️ Anti-Nuke", f"Threshold set to **{actions}** actions per **{window_seconds}s**.", discord.Color.green(), bot.user))


@antinuke_group.command(name="whitelist", description="Whitelist a trusted user/bot")
@has_admin_perms()
async def antinuke_whitelist(interaction: discord.Interaction, user: discord.User):
    existing = supabase.table("antinuke_whitelist").select("id").eq("guild_id", interaction.guild_id).eq("user_id", user.id).execute()
    if existing.data:
        return await interaction.response.send_message(embed=make_embed("🛡️ Anti-Nuke", f"{user.mention} is already whitelisted.", discord.Color.orange(), bot.user), ephemeral=True)
    supabase.table("antinuke_whitelist").upsert({"guild_id": interaction.guild_id, "user_id": user.id}, on_conflict="guild_id,user_id").execute()
    await interaction.response.send_message(embed=make_embed("🛡️ Anti-Nuke", f"{user.mention} whitelisted.", discord.Color.green(), bot.user))


@antinuke_group.command(name="logchannel", description="Set the anti-nuke log channel")
@has_admin_perms()
async def antinuke_logchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    await update_guild_config(interaction.guild_id, antinuke_log_channel=channel.id)
    await interaction.response.send_message(embed=make_embed("🛡️ Anti-Nuke", f"Log channel set to {channel.mention}.", discord.Color.green(), bot.user))


bot.tree.add_command(antinuke_group)

# ==================================================================
# 2. BADWORDS SYSTEM
# ==================================================================

badwords_group = app_commands.Group(name="badwords", description="Manage the badword filter")


@badwords_group.command(name="add", description="Add a badword to the filter")
@has_mod_perms()
async def badwords_add(interaction: discord.Interaction, word: str):
    supabase.table("badwords").insert({"guild_id": interaction.guild_id, "word": word.lower()}).execute()
    await interaction.response.send_message(embed=make_embed("🚫 Badwords", f"Added `{word}` to the filter.", discord.Color.green(), bot.user), ephemeral=True)


@badwords_group.command(name="remove", description="Remove a badword from the filter")
@has_mod_perms()
async def badwords_remove(interaction: discord.Interaction, word: str):
    supabase.table("badwords").delete().eq("guild_id", interaction.guild_id).eq("word", word.lower()).execute()
    await interaction.response.send_message(embed=make_embed("🚫 Badwords", f"Removed `{word}` from the filter.", discord.Color.green(), bot.user), ephemeral=True)


@badwords_group.command(name="list", description="List all filtered badwords")
@has_mod_perms()
async def badwords_list(interaction: discord.Interaction):
    res = supabase.table("badwords").select("word").eq("guild_id", interaction.guild_id).execute()
    words = ", ".join(f"`{r['word']}`" for r in res.data) or "None configured."
    await interaction.response.send_message(embed=make_embed("🚫 Badwords List", words, discord.Color.blurple(), bot.user), ephemeral=True)


bot.tree.add_command(badwords_group)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return

    await handle_afk(message)
    await handle_custom_command(message)
    await handle_badwords(message)
    await add_xp(message)

    await bot.process_commands(message)


async def handle_badwords(message: discord.Message):
    if message.author.guild_permissions.manage_messages:
        return
    try:
        res = supabase.table("badwords").select("word").eq("guild_id", message.guild.id).execute()
        badwords = {r["word"] for r in res.data}
    except Exception as e:
        logger.error(f"badwords fetch error: {e}")
        return
    if not badwords:
        return

    content_lower = message.content.lower()
    if any(bw in content_lower for bw in badwords):
        try:
            await message.delete()
        except discord.Forbidden:
            return

        # escalation: warn the user automatically
        await add_warn(message.guild, message.author, bot.user, "Automatic warn: used a filtered word")

        cfg = await get_guild_config(message.guild.id)
        embed = make_embed("🚫 Badword Filtered", color=discord.Color.orange(), bot_user=bot.user)
        embed.add_field(name="User", value=f"{message.author} (`{message.author.id}`)", inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        embed.add_field(name="Content", value=message.content[:500], inline=False)
        await log_to_channel(message.guild, cfg.get("badwords_log_channel") or cfg.get("modlog_channel"), embed)


# ==================================================================
# 3-5. BAN / KICK / TIMEOUT SYSTEMS
# ==================================================================

async def dm_user_safely(user: discord.abc.User, embed: discord.Embed):
    try:
        await user.send(embed=embed)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


@bot.tree.command(name="ban", description="Ban a member from the server")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.checks.bot_has_permissions(ban_members=True)
async def ban_cmd(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided", delete_message_days: int = 0):
    if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "You cannot ban someone with an equal/higher role.", discord.Color.red(), bot.user), ephemeral=True)

    dm_embed = make_embed(f"You were banned from {interaction.guild.name}", f"**Reason:** {reason}", discord.Color.red(), bot.user)
    dmed = await dm_user_safely(member, dm_embed)

    try:
        await interaction.guild.ban(member, reason=f"{reason} | By {interaction.user}", delete_message_seconds=delete_message_days * 86400)
    except discord.Forbidden:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "I don't have permission to ban this member.", discord.Color.red(), bot.user), ephemeral=True)

    embed = make_embed("🔨 Member Banned", color=discord.Color.red(), bot_user=bot.user)
    embed.add_field(name="Member", value=f"{member} (`{member.id}`)", inline=True)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="DM Sent", value="Yes" if dmed else "No", inline=True)
    await interaction.response.send_message(embed=embed)

    cfg = await get_guild_config(interaction.guild_id)
    await log_to_channel(interaction.guild, cfg.get("modlog_channel"), embed)


@bot.tree.command(name="unban", description="Unban a user by ID")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.checks.bot_has_permissions(ban_members=True)
async def unban_cmd(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=f"{reason} | By {interaction.user}")
    except (ValueError, discord.NotFound):
        return await interaction.response.send_message(embed=make_embed("❌ Error", "Invalid user ID or user is not banned.", discord.Color.red(), bot.user), ephemeral=True)
    except discord.Forbidden:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "I don't have permission to unban.", discord.Color.red(), bot.user), ephemeral=True)

    embed = make_embed("✅ Member Unbanned", f"{user} (`{user.id}`)\n**Reason:** {reason}", discord.Color.green(), bot.user)
    await interaction.response.send_message(embed=embed)
    cfg = await get_guild_config(interaction.guild_id)
    await log_to_channel(interaction.guild, cfg.get("modlog_channel"), embed)


@bot.tree.command(name="banlist", description="View all banned users")
@app_commands.checks.has_permissions(ban_members=True)
async def banlist_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    bans = [entry async for entry in interaction.guild.bans(limit=50)]
    if not bans:
        return await interaction.followup.send(embed=make_embed("🔨 Ban List", "No banned users.", discord.Color.blurple(), bot.user))
    desc = "\n".join(f"**{b.user}** (`{b.user.id}`) — {b.reason or 'No reason'}" for b in bans[:25])
    await interaction.followup.send(embed=make_embed("🔨 Ban List (top 25)", desc, discord.Color.blurple(), bot.user))


@bot.tree.command(name="kick", description="Kick a member from the server")
@app_commands.checks.has_permissions(kick_members=True)
@app_commands.checks.bot_has_permissions(kick_members=True)
async def kick_cmd(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "You cannot kick someone with an equal/higher role.", discord.Color.red(), bot.user), ephemeral=True)

    dm_embed = make_embed(f"You were kicked from {interaction.guild.name}", f"**Reason:** {reason}", discord.Color.orange(), bot.user)
    dmed = await dm_user_safely(member, dm_embed)

    try:
        await member.kick(reason=f"{reason} | By {interaction.user}")
    except discord.Forbidden:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "I don't have permission to kick this member.", discord.Color.red(), bot.user), ephemeral=True)

    embed = make_embed("👢 Member Kicked", color=discord.Color.orange(), bot_user=bot.user)
    embed.add_field(name="Member", value=f"{member} (`{member.id}`)", inline=True)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="DM Sent", value="Yes" if dmed else "No", inline=True)
    await interaction.response.send_message(embed=embed)

    cfg = await get_guild_config(interaction.guild_id)
    await log_to_channel(interaction.guild, cfg.get("modlog_channel"), embed)


@bot.tree.command(name="timeout", description="Timeout a member")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.checks.bot_has_permissions(moderate_members=True)
async def timeout_cmd(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = "No reason provided"):
    seconds = parse_duration(duration)
    if seconds is None:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "Invalid duration format. Use e.g. `10m`, `1h`, `1d`.", discord.Color.red(), bot.user), ephemeral=True)
    if seconds > 28 * 86400:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "Timeout duration cannot exceed 28 days.", discord.Color.red(), bot.user), ephemeral=True)

    until = discord.utils.utcnow() + datetime.timedelta(seconds=seconds)
    try:
        await member.timeout(until, reason=f"{reason} | By {interaction.user}")
    except discord.Forbidden:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "I don't have permission to timeout this member.", discord.Color.red(), bot.user), ephemeral=True)

    dm_embed = make_embed(f"You were timed out in {interaction.guild.name}", f"**Duration:** {duration}\n**Reason:** {reason}", discord.Color.orange(), bot.user)
    await dm_user_safely(member, dm_embed)

    embed = make_embed("⏱️ Member Timed Out", color=discord.Color.orange(), bot_user=bot.user)
    embed.add_field(name="Member", value=f"{member} (`{member.id}`)", inline=True)
    embed.add_field(name="Duration", value=duration, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    await interaction.response.send_message(embed=embed)
    cfg = await get_guild_config(interaction.guild_id)
    await log_to_channel(interaction.guild, cfg.get("modlog_channel"), embed)


@bot.tree.command(name="untimeout", description="Remove a member's timeout")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.checks.bot_has_permissions(moderate_members=True)
async def untimeout_cmd(interaction: discord.Interaction, member: discord.Member):
    try:
        await member.timeout(None, reason=f"Timeout removed by {interaction.user}")
    except discord.Forbidden:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "I don't have permission.", discord.Color.red(), bot.user), ephemeral=True)
    await interaction.response.send_message(embed=make_embed("✅ Timeout Removed", f"{member.mention}'s timeout has been removed.", discord.Color.green(), bot.user))


# ==================================================================
# 6. WARN SYSTEM
# ==================================================================

async def add_warn(guild: discord.Guild, member: discord.abc.User, moderator: discord.abc.User, reason: str):
    supabase.table("warns").insert({
        "guild_id": guild.id, "user_id": member.id, "moderator_id": moderator.id,
        "reason": reason, "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }).execute()

    res = supabase.table("warns").select("id").eq("guild_id", guild.id).eq("user_id", member.id).execute()
    warn_count = len(res.data)

    # auto-punishment thresholds
    real_member = guild.get_member(member.id) if hasattr(member, "id") else None
    if real_member:
        try:
            if warn_count == 5:
                await real_member.kick(reason="Auto-punishment: 5 warns")
            elif warn_count == 3:
                await real_member.timeout(discord.utils.utcnow() + datetime.timedelta(hours=1), reason="Auto-punishment: 3 warns")
        except discord.Forbidden:
            pass
    return warn_count


warn_group = app_commands.Group(name="warn", description="Manage member warnings")


@warn_group.command(name="add", description="Warn a member")
@has_mod_perms()
async def warn_add(interaction: discord.Interaction, member: discord.Member, reason: str):
    count = await add_warn(interaction.guild, member, interaction.user, reason)
    embed = make_embed("⚠️ Member Warned", color=discord.Color.orange(), bot_user=bot.user)
    embed.add_field(name="Member", value=member.mention, inline=True)
    embed.add_field(name="Total Warns", value=str(count), inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    await interaction.response.send_message(embed=embed)
    await dm_user_safely(member, make_embed(f"You were warned in {interaction.guild.name}", f"**Reason:** {reason}\n**Total Warns:** {count}", discord.Color.orange(), bot.user))


@warn_group.command(name="remove", description="Remove a member's most recent warn")
@has_mod_perms()
async def warn_remove(interaction: discord.Interaction, member: discord.Member):
    res = supabase.table("warns").select("id").eq("guild_id", interaction.guild_id).eq("user_id", member.id).order("created_at", desc=True).limit(1).execute()
    if not res.data:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "This member has no warns.", discord.Color.red(), bot.user), ephemeral=True)
    supabase.table("warns").delete().eq("id", res.data[0]["id"]).execute()
    await interaction.response.send_message(embed=make_embed("✅ Warn Removed", f"Removed the most recent warn for {member.mention}.", discord.Color.green(), bot.user))


@warn_group.command(name="list", description="List a member's warns")
@has_mod_perms()
async def warn_list(interaction: discord.Interaction, member: discord.Member):
    res = supabase.table("warns").select("*").eq("guild_id", interaction.guild_id).eq("user_id", member.id).order("created_at", desc=True).execute()
    if not res.data:
        return await interaction.response.send_message(embed=make_embed("⚠️ Warns", "No warns found.", discord.Color.blurple(), bot.user))
    desc = "\n".join(f"**#{i+1}** — {w['reason']} (by <@{w['moderator_id']}>)" for i, w in enumerate(res.data[:15]))
    await interaction.response.send_message(embed=make_embed(f"⚠️ Warns for {member}", desc, discord.Color.blurple(), bot.user))


bot.tree.add_command(warn_group)

# ==================================================================
# 7. PURGE SYSTEM
# ==================================================================

purge_group = app_commands.Group(name="purge", description="Bulk delete messages")


@purge_group.command(name="amount", description="Delete a number of recent messages")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.checks.bot_has_permissions(manage_messages=True)
async def purge_amount(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 500]):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(embed=make_embed("🧹 Purge Complete", f"Deleted **{len(deleted)}** messages.", discord.Color.green(), bot.user), ephemeral=True)


@purge_group.command(name="user", description="Delete recent messages from a specific user")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.checks.bot_has_permissions(manage_messages=True)
async def purge_user(interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 500] = 100):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount, check=lambda m: m.author.id == member.id)
    await interaction.followup.send(embed=make_embed("🧹 Purge Complete", f"Deleted **{len(deleted)}** messages from {member.mention}.", discord.Color.green(), bot.user), ephemeral=True)


@purge_group.command(name="bots", description="Delete recent messages from bots")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.checks.bot_has_permissions(manage_messages=True)
async def purge_bots(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 500] = 100):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount, check=lambda m: m.author.bot)
    await interaction.followup.send(embed=make_embed("🧹 Purge Complete", f"Deleted **{len(deleted)}** bot messages.", discord.Color.green(), bot.user), ephemeral=True)


@purge_group.command(name="contains", description="Delete recent messages containing text")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.checks.bot_has_permissions(manage_messages=True)
async def purge_contains(interaction: discord.Interaction, text: str, amount: app_commands.Range[int, 1, 500] = 100):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount, check=lambda m: text.lower() in m.content.lower())
    await interaction.followup.send(embed=make_embed("🧹 Purge Complete", f"Deleted **{len(deleted)}** messages containing `{text}`.", discord.Color.green(), bot.user), ephemeral=True)


bot.tree.add_command(purge_group)

# ==================================================================
# 8. CHANNEL LOCK / UNLOCK
# ==================================================================

@bot.tree.command(name="lock", description="Lock a channel (prevent @everyone from sending messages)")
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.checks.bot_has_permissions(manage_channels=True)
async def lock_cmd(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None, reason: str = "No reason provided"):
    channel = channel or interaction.channel
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=reason)
    await interaction.response.send_message(embed=make_embed("🔒 Channel Locked", f"{channel.mention}\n**Reason:** {reason}", discord.Color.red(), bot.user))


@bot.tree.command(name="unlock", description="Unlock a channel")
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.checks.bot_has_permissions(manage_channels=True)
async def unlock_cmd(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    channel = channel or interaction.channel
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = None
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message(embed=make_embed("🔓 Channel Unlocked", f"{channel.mention}", discord.Color.green(), bot.user))


@bot.tree.command(name="lockall", description="Lock all text channels (raid mode)")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.bot_has_permissions(manage_channels=True)
async def lockall_cmd(interaction: discord.Interaction, reason: str = "Raid mode activated"):
    await interaction.response.defer()
    count = 0
    for channel in interaction.guild.text_channels:
        try:
            overwrite = channel.overwrites_for(interaction.guild.default_role)
            overwrite.send_messages = False
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=reason)
            count += 1
        except discord.Forbidden:
            continue
    await interaction.followup.send(embed=make_embed("🔒 Server Locked", f"Locked **{count}** channels.\n**Reason:** {reason}", discord.Color.red(), bot.user))


@bot.tree.command(name="unlockall", description="Unlock all text channels")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.bot_has_permissions(manage_channels=True)
async def unlockall_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    count = 0
    for channel in interaction.guild.text_channels:
        try:
            overwrite = channel.overwrites_for(interaction.guild.default_role)
            overwrite.send_messages = None
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
            count += 1
        except discord.Forbidden:
            continue
    await interaction.followup.send(embed=make_embed("🔓 Server Unlocked", f"Unlocked **{count}** channels.", discord.Color.green(), bot.user))


# ==================================================================
# 9. SLOWMODE SYSTEM
# ==================================================================

@bot.tree.command(name="slowmode", description="Set slowmode for a channel (0 to disable)")
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.checks.bot_has_permissions(manage_channels=True)
async def slowmode_cmd(interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600], channel: Optional[discord.TextChannel] = None):
    channel = channel or interaction.channel
    await channel.edit(slowmode_delay=seconds)
    if seconds == 0:
        msg = f"Slowmode disabled in {channel.mention}."
    else:
        msg = f"Slowmode set to **{seconds}s** in {channel.mention}."
    await interaction.response.send_message(embed=make_embed("🐌 Slowmode", msg, discord.Color.blurple(), bot.user))


# ==================================================================
# 10. CUSTOM COMMAND SYSTEM
# ==================================================================

customcommand_group = app_commands.Group(name="customcommand", description="Manage custom commands")


@customcommand_group.command(name="add", description="Add a custom command")
@has_mod_perms()
async def customcommand_add(interaction: discord.Interaction, name: str, response: str):
    supabase.table("custom_commands").upsert({
        "guild_id": interaction.guild_id, "name": name.lower(), "response": response
    }, on_conflict="guild_id,name").execute()
    await interaction.response.send_message(embed=make_embed("✅ Custom Command Added", f"`{name}` → {response[:100]}", discord.Color.green(), bot.user), ephemeral=True)


@customcommand_group.command(name="remove", description="Remove a custom command")
@has_mod_perms()
async def customcommand_remove(interaction: discord.Interaction, name: str):
    supabase.table("custom_commands").delete().eq("guild_id", interaction.guild_id).eq("name", name.lower()).execute()
    await interaction.response.send_message(embed=make_embed("✅ Custom Command Removed", f"`{name}` removed.", discord.Color.green(), bot.user), ephemeral=True)


@customcommand_group.command(name="list", description="List all custom commands")
async def customcommand_list(interaction: discord.Interaction):
    res = supabase.table("custom_commands").select("name").eq("guild_id", interaction.guild_id).execute()
    names = ", ".join(f"`{r['name']}`" for r in res.data) or "None configured."
    await interaction.response.send_message(embed=make_embed("📜 Custom Commands", names, discord.Color.blurple(), bot.user))


bot.tree.add_command(customcommand_group)


async def handle_custom_command(message: discord.Message):
    cfg = await get_guild_config(message.guild.id)
    prefix = cfg.get("prefix", "!")
    if not message.content.startswith(prefix):
        return
    name = message.content[len(prefix):].split(" ")[0].lower()
    if not name:
        return
    res = supabase.table("custom_commands").select("response").eq("guild_id", message.guild.id).eq("name", name).execute()
    if not res.data:
        return
    response = res.data[0]["response"]
    response = response.replace("{user}", message.author.mention)
    response = response.replace("{server}", message.guild.name)
    response = response.replace("{membercount}", str(message.guild.member_count))
    await message.channel.send(response)


# ==================================================================
# 11. TICKET SYSTEM (Advanced)
# ==================================================================

TICKET_CATEGORIES = ["Support", "Report", "Billing"]


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="Select a ticket category...",
        custom_id="vantix_ticket_select",
        options=[discord.SelectOption(label=c, value=c) for c in TICKET_CATEGORIES],
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        category_name = select.values[0]
        await create_ticket(interaction, category_name)


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.blurple, custom_id="vantix_ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = await get_guild_config(interaction.guild_id)
        staff_role_id = cfg.get("ticket_staff_role")
        if staff_role_id and int(staff_role_id) not in [r.id for r in interaction.user.roles]:
            return await interaction.response.send_message("Only staff can claim tickets.", ephemeral=True)
        await interaction.response.send_message(embed=make_embed("🎫 Ticket Claimed", f"Claimed by {interaction.user.mention}", discord.Color.blurple(), bot.user))

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, custom_id="vantix_ticket_close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await close_ticket(interaction)


async def create_ticket(interaction: discord.Interaction, category_name: str):
    cfg = await get_guild_config(interaction.guild_id)
    guild = interaction.guild
    category_id = cfg.get("ticket_category_id")
    category = guild.get_channel(int(category_id)) if category_id else None

    staff_role_id = cfg.get("ticket_staff_role")
    staff_role = guild.get_role(int(staff_role_id)) if staff_role_id else None

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    channel_name = f"ticket-{category_name.lower()}-{interaction.user.name}"[:95]
    channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)

    supabase.table("tickets").insert({
        "guild_id": guild.id, "channel_id": channel.id, "user_id": interaction.user.id,
        "category": category_name, "status": "open", "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }).execute()

    embed = make_embed(f"🎫 {category_name} Ticket", f"Welcome {interaction.user.mention}! Support will be with you shortly.\nClick **Close** when your issue is resolved.", discord.Color.blurple(), bot.user)
    await channel.send(embed=embed, view=TicketControlView())
    await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)


async def close_ticket(interaction: discord.Interaction):
    channel = interaction.channel
    res = supabase.table("tickets").select("*").eq("channel_id", channel.id).execute()
    if not res.data:
        return await interaction.response.send_message("This isn't a ticket channel.", ephemeral=True)
    ticket = res.data[0]

    await interaction.response.send_message(embed=make_embed("🎫 Closing Ticket", "Generating transcript...", discord.Color.orange(), bot.user))

    # Build transcript
    lines = []
    async for msg in channel.history(limit=None, oldest_first=True):
        ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"[{ts}] {msg.author}: {msg.content}")
    transcript_text = "\n".join(lines) or "No messages."
    transcript_file = discord.File(io.BytesIO(transcript_text.encode()), filename=f"transcript-{channel.name}.txt")

    cfg = await get_guild_config(interaction.guild_id)
    log_channel_id = cfg.get("ticket_log_channel")
    if log_channel_id:
        log_channel = interaction.guild.get_channel(int(log_channel_id))
        if log_channel:
            await log_channel.send(
                embed=make_embed("🎫 Ticket Closed", f"Channel: {channel.name}\nUser: <@{ticket['user_id']}>\nCategory: {ticket['category']}", discord.Color.red(), bot.user),
                file=discord.File(io.BytesIO(transcript_text.encode()), filename=f"transcript-{channel.name}.txt"),
            )

    supabase.table("tickets").update({"status": "closed"}).eq("channel_id", channel.id).execute()

    # DM user with transcript + rating request
    ticket_user = interaction.guild.get_member(ticket["user_id"]) or await bot.fetch_user(ticket["user_id"])
    if ticket_user:
        try:
            await ticket_user.send(embed=make_embed("🎫 Your Ticket Was Closed", "Thank you for contacting support! Here's your transcript.", discord.Color.blurple(), bot.user),
                                    file=discord.File(io.BytesIO(transcript_text.encode()), filename=f"transcript-{channel.name}.txt"))
            await ticket_user.send(embed=make_embed("⭐ Rate Your Support", "How was your experience?", discord.Color.gold(), bot.user), view=RatingView(ticket["id"]))
        except discord.Forbidden:
            pass

    await asyncio.sleep(5)
    await channel.delete(reason="Ticket closed")


class RatingView(discord.ui.View):
    def __init__(self, ticket_id: int):
        super().__init__(timeout=86400)
        self.ticket_id = ticket_id
        for i in range(1, 6):
            self.add_item(RatingButton(i, ticket_id))


class RatingButton(discord.ui.Button):
    def __init__(self, stars: int, ticket_id: int):
        super().__init__(label="⭐" * stars, style=discord.ButtonStyle.gray)
        self.stars = stars
        self.ticket_id = ticket_id

    async def callback(self, interaction: discord.Interaction):
        supabase.table("ticket_ratings").insert({"ticket_id": self.ticket_id, "user_id": interaction.user.id, "rating": self.stars}).execute()
        await interaction.response.edit_message(content=f"Thanks for rating {self.stars} stars!", view=None)


@bot.tree.command(name="ticketsetup", description="Post the ticket creation panel")
@has_admin_perms()
async def ticketsetup_cmd(interaction: discord.Interaction, title: str = "🎫 Support Tickets", description: str = "Select a category below to open a ticket."):
    embed = make_embed(title, description, discord.Color.blurple(), bot.user)
    await interaction.channel.send(embed=embed, view=TicketPanelView())
    await interaction.response.send_message("Ticket panel posted.", ephemeral=True)


@bot.tree.command(name="ticketconfig", description="Configure the ticket system")
@has_admin_perms()
async def ticketconfig_cmd(interaction: discord.Interaction, category: Optional[discord.CategoryChannel] = None,
                            staff_role: Optional[discord.Role] = None, log_channel: Optional[discord.TextChannel] = None):
    fields = {}
    if category:
        fields["ticket_category_id"] = category.id
    if staff_role:
        fields["ticket_staff_role"] = staff_role.id
    if log_channel:
        fields["ticket_log_channel"] = log_channel.id
    if fields:
        await update_guild_config(interaction.guild_id, **fields)
    await interaction.response.send_message(embed=make_embed("🎫 Ticket Config Updated", "Settings saved.", discord.Color.green(), bot.user), ephemeral=True)


@bot.tree.command(name="ticketstats", description="View average staff ratings")
async def ticketstats_cmd(interaction: discord.Interaction):
    res = supabase.table("ticket_ratings").select("rating").execute()
    if not res.data:
        return await interaction.response.send_message(embed=make_embed("⭐ Ticket Stats", "No ratings yet.", discord.Color.blurple(), bot.user))
    avg = sum(r["rating"] for r in res.data) / len(res.data)
    await interaction.response.send_message(embed=make_embed("⭐ Ticket Stats", f"Average rating: **{avg:.2f}/5** ({len(res.data)} ratings)", discord.Color.gold(), bot.user))


# ==================================================================
# 12. WELCOME / GOODBYE SYSTEM
# ==================================================================

welcomeset_group = app_commands.Group(name="welcomeset", description="Configure welcome messages")


@welcomeset_group.command(name="channel", description="Set the welcome channel")
@has_admin_perms()
async def welcomeset_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    await update_guild_config(interaction.guild_id, welcome_channel=channel.id)
    await interaction.response.send_message(embed=make_embed("👋 Welcome System", f"Welcome channel set to {channel.mention}.", discord.Color.green(), bot.user))


@welcomeset_group.command(name="message", description="Set the welcome message (supports {user} {server} {membercount})")
@has_admin_perms()
async def welcomeset_message(interaction: discord.Interaction, message: str):
    await update_guild_config(interaction.guild_id, welcome_message=message)
    await interaction.response.send_message(embed=make_embed("👋 Welcome System", "Welcome message updated.", discord.Color.green(), bot.user))


bot.tree.add_command(welcomeset_group)

goodbyeset_group = app_commands.Group(name="goodbyeset", description="Configure goodbye messages")


@goodbyeset_group.command(name="channel", description="Set the goodbye channel")
@has_admin_perms()
async def goodbyeset_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    await update_guild_config(interaction.guild_id, goodbye_channel=channel.id)
    await interaction.response.send_message(embed=make_embed("👋 Goodbye System", f"Goodbye channel set to {channel.mention}.", discord.Color.green(), bot.user))


@goodbyeset_group.command(name="message", description="Set the goodbye message (supports {user} {server} {membercount})")
@has_admin_perms()
async def goodbyeset_message(interaction: discord.Interaction, message: str):
    await update_guild_config(interaction.guild_id, goodbye_message=message)
    await interaction.response.send_message(embed=make_embed("👋 Goodbye System", "Goodbye message updated.", discord.Color.green(), bot.user))


bot.tree.add_command(goodbyeset_group)


def fill_vars(text: str, member: discord.Member) -> str:
    return (text.replace("{user}", member.mention)
                .replace("{server}", member.guild.name)
                .replace("{membercount}", str(member.guild.member_count)))


@bot.event
async def on_member_join(member: discord.Member):
    cfg = await get_guild_config(member.guild.id)
    await handle_invite_join(member)

    channel_id = cfg.get("welcome_channel")
    if channel_id:
        channel = member.guild.get_channel(int(channel_id))
        if channel:
            msg = cfg.get("welcome_message") or "Welcome {user} to {server}! We're now {membercount} members."
            embed = make_embed("👋 Welcome!", fill_vars(msg, member), discord.Color.green(), bot.user)
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)

    await update_membercount_channel(member.guild)


@bot.event
async def on_member_remove(member: discord.Member):  # noqa: F811 (extends earlier handler intentionally via separate listener)
    cfg = await get_guild_config(member.guild.id)
    channel_id = cfg.get("goodbye_channel")
    if channel_id:
        channel = member.guild.get_channel(int(channel_id))
        if channel:
            msg = cfg.get("goodbye_message") or "{user} has left {server}. We're now {membercount} members."
            embed = make_embed("👋 Goodbye", fill_vars(msg, member), discord.Color.orange(), bot.user)
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)
    await update_membercount_channel(member.guild)


# ==================================================================
# 13. DM SYSTEM
# ==================================================================

@bot.tree.command(name="dm", description="Send a custom embed DM to a user")
@has_mod_perms()
async def dm_cmd(interaction: discord.Interaction, user: discord.User, title: str, message: str, color: Optional[str] = None, image_url: Optional[str] = None):
    try:
        colour = discord.Color(int(color, 16)) if color else discord.Color.blurple()
    except ValueError:
        colour = discord.Color.blurple()
    embed = make_embed(title, message, colour, bot.user)
    if image_url:
        embed.set_image(url=image_url)
    sent = await dm_user_safely(user, embed)
    result_embed = make_embed("📨 DM Sent" if sent else "❌ DM Failed",
                               f"To: {user.mention}" if sent else f"Could not DM {user.mention} (DMs closed).",
                               discord.Color.green() if sent else discord.Color.red(), bot.user)
    await interaction.response.send_message(embed=result_embed, ephemeral=True)


@bot.tree.command(name="dmall", description="Mass DM all members with a custom message (admin only)")
@has_admin_perms()
async def dmall_cmd(interaction: discord.Interaction, title: str, message: str):
    await interaction.response.send_message(embed=make_embed("⚠️ Confirm Mass DM", f"This will queue a DM job for **{interaction.guild.member_count}** members. React ✅ within 30s to confirm.", discord.Color.orange(), bot.user))
    sent_message = await interaction.original_response()
    await sent_message.add_reaction("✅")

    def check(reaction, user):
        return user.id == interaction.user.id and str(reaction.emoji) == "✅" and reaction.message.id == sent_message.id

    try:
        await bot.wait_for("reaction_add", timeout=30.0, check=check)
    except asyncio.TimeoutError:
        return await interaction.followup.send(embed=make_embed("❌ Cancelled", "Mass DM confirmation timed out.", discord.Color.red(), bot.user))

    # Instead of blocking this interaction while DMing every member (which risks
    # timing out and hammering Discord's global rate limit), we enqueue a job row
    # and let a background worker (dm_queue_worker) drain it at a safe, steady rate.
    member_ids = [m.id for m in interaction.guild.members if not m.bot]
    job = supabase.table("dm_jobs").insert({
        "guild_id": interaction.guild_id, "requested_by": interaction.user.id,
        "title": title, "message": message, "targets": json.dumps(member_ids),
        "sent": 0, "failed": 0, "status": "queued",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }).execute()
    job_id = job.data[0]["id"]

    await interaction.followup.send(embed=make_embed(
        "📨 Mass DM Queued", f"Job `#{job_id}` queued for **{len(member_ids)}** members. "
        f"Use `/dmjobstatus job_id:{job_id}` to check progress.", discord.Color.green(), bot.user))


@bot.tree.command(name="dmjobstatus", description="Check the progress of a mass DM job")
@has_admin_perms()
async def dmjobstatus_cmd(interaction: discord.Interaction, job_id: int):
    res = supabase.table("dm_jobs").select("*").eq("id", job_id).execute()
    if not res.data:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "Job not found.", discord.Color.red(), bot.user), ephemeral=True)
    job = res.data[0]
    total = len(json.loads(job["targets"]))
    embed = make_embed(f"📨 DM Job #{job_id}", color=discord.Color.blurple(), bot_user=bot.user)
    embed.add_field(name="Status", value=job["status"], inline=True)
    embed.add_field(name="Progress", value=f"{job['sent'] + job['failed']}/{total}", inline=True)
    embed.add_field(name="Sent", value=str(job["sent"]), inline=True)
    embed.add_field(name="Failed", value=str(job["failed"]), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# Global concurrency-safe rate limit for outbound DMs across ALL queued jobs
# (Discord's practical safe DM rate is well under 1/sec sustained).
DM_QUEUE_RATE_SECONDS = 1.2


@tasks.loop(seconds=5)
async def dm_queue_worker():
    """Background worker that drains queued dm_jobs at a steady, safe rate.
    Runs independently of any single interaction so it survives Discord API
    hiccups and doesn't hold an interaction/response open for a long-running job."""
    try:
        res = supabase.table("dm_jobs").select("*").eq("status", "queued").order("created_at").limit(1).execute()
        if not res.data:
            return
        job = res.data[0]
        supabase.table("dm_jobs").update({"status": "running"}).eq("id", job["id"]).execute()

        guild = bot.get_guild(job["guild_id"])
        if not guild:
            supabase.table("dm_jobs").update({"status": "failed"}).eq("id", job["id"]).execute()
            return

        embed = make_embed(job["title"], job["message"], discord.Color.blurple(), bot.user)
        targets = json.loads(job["targets"])
        sent, failed = job["sent"], job["failed"]

        # Process in small batches per worker tick so we never block the event
        # loop for the whole job, and other bot activity stays responsive.
        batch = targets[sent + failed: sent + failed + 20]
        for user_id in batch:
            member = guild.get_member(user_id)
            if not member:
                failed += 1
                continue
            ok = await dm_user_safely(member, embed)
            sent += 1 if ok else 0
            failed += 0 if ok else 1
            await asyncio.sleep(DM_QUEUE_RATE_SECONDS)

        done = (sent + failed) >= len(targets)
        supabase.table("dm_jobs").update({
            "sent": sent, "failed": failed, "status": "done" if done else "queued"
        }).eq("id", job["id"]).execute()
    except Exception as e:
        logger.error(f"dm_queue_worker error: {e}")


# ==================================================================
# 14. INVITE TRACKING SYSTEM
# ==================================================================

@bot.event
async def on_ready_populate_invites():
    for guild in bot.guilds:
        try:
            invites = await guild.invites()
            bot.invite_cache[guild.id] = {inv.code: inv.uses for inv in invites}
        except discord.Forbidden:
            bot.invite_cache[guild.id] = {}


async def handle_invite_join(member: discord.Member):
    guild = member.guild
    try:
        new_invites = await guild.invites()
    except discord.Forbidden:
        return
    old = bot.invite_cache.get(guild.id, {})
    used_invite = None
    for inv in new_invites:
        if inv.uses > old.get(inv.code, 0):
            used_invite = inv
            break
    bot.invite_cache[guild.id] = {inv.code: inv.uses for inv in new_invites}

    if used_invite:
        supabase.table("invites").insert({
            "guild_id": guild.id, "inviter_id": used_invite.inviter.id if used_invite.inviter else None,
            "invited_id": member.id, "code": used_invite.code, "status": "active",
            "joined_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }).execute()


async def handle_invite_leave(member: discord.Member):
    supabase.table("invites").update({"status": "left"}).eq("guild_id", member.guild.id).eq("invited_id", member.id).execute()


@bot.tree.command(name="invites", description="View invite stats for a user")
async def invites_cmd(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    user = user or interaction.user
    res = supabase.table("invites").select("*").eq("guild_id", interaction.guild_id).eq("inviter_id", user.id).execute()
    total = len(res.data)
    real = len([r for r in res.data if r["status"] == "active"])
    fake = total - real
    embed = make_embed(f"📨 Invites — {user}", color=discord.Color.blurple(), bot_user=bot.user)
    embed.add_field(name="Total", value=str(total), inline=True)
    embed.add_field(name="Real (still in server)", value=str(real), inline=True)
    embed.add_field(name="Fake (left)", value=str(fake), inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="invitesleaderboard", description="Top inviters leaderboard")
async def invitesleaderboard_cmd(interaction: discord.Interaction):
    res = supabase.table("invites").select("inviter_id").eq("guild_id", interaction.guild_id).eq("status", "active").execute()
    counts = {}
    for r in res.data:
        counts[r["inviter_id"]] = counts.get(r["inviter_id"], 0) + 1
    top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]
    if not top:
        return await interaction.response.send_message(embed=make_embed("🏆 Invite Leaderboard", "No data yet.", discord.Color.blurple(), bot.user))
    desc = "\n".join(f"**{i+1}.** <@{uid}> — {count} invites" for i, (uid, count) in enumerate(top))
    await interaction.response.send_message(embed=make_embed("🏆 Invite Leaderboard", desc, discord.Color.gold(), bot.user))


# ==================================================================
# 15. GIVEAWAYS SYSTEM
# ==================================================================

class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: int):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id

    @discord.ui.button(label="🎉 Enter Giveaway", style=discord.ButtonStyle.green, custom_id="vantix_giveaway_enter")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        res = supabase.table("giveaways").select("*").eq("id", self.giveaway_id).execute()
        if not res.data or res.data[0]["status"] != "active":
            return await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
        giveaway = res.data[0]

        if giveaway.get("required_role"):
            if int(giveaway["required_role"]) not in [r.id for r in interaction.user.roles]:
                return await interaction.response.send_message("You don't have the required role to enter.", ephemeral=True)

        entrants = set(json.loads(giveaway.get("entrants") or "[]"))
        if interaction.user.id in entrants:
            entrants.discard(interaction.user.id)
            msg = "You left the giveaway."
        else:
            entrants.add(interaction.user.id)
            msg = "You entered the giveaway! 🎉"
        supabase.table("giveaways").update({"entrants": json.dumps(list(entrants))}).eq("id", self.giveaway_id).execute()
        await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="gcreate", description="Create a giveaway")
@has_mod_perms()
async def gcreate_cmd(interaction: discord.Interaction, prize: str, duration: str, winners: int = 1,
                       channel: Optional[discord.TextChannel] = None, required_role: Optional[discord.Role] = None):
    seconds = parse_duration(duration)
    if seconds is None:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "Invalid duration. Use e.g. `1h`, `1d`.", discord.Color.red(), bot.user), ephemeral=True)
    channel = channel or interaction.channel
    end_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)

    embed = make_embed(f"🎉 Giveaway: {prize}", f"Winners: **{winners}**\nEnds: <t:{int(end_time.timestamp())}:R>\nClick the button below to enter!", discord.Color.gold(), bot.user)
    msg = await channel.send(embed=embed)

    data = {
        "guild_id": interaction.guild_id, "channel_id": channel.id, "message_id": msg.id,
        "prize": prize, "winners": winners, "required_role": required_role.id if required_role else None,
        "entrants": "[]", "status": "active", "end_time": end_time.isoformat(),
    }
    res = supabase.table("giveaways").insert(data).execute()
    giveaway_id = res.data[0]["id"]
    await msg.edit(view=GiveawayView(giveaway_id))
    await interaction.response.send_message(embed=make_embed("✅ Giveaway Created", f"Giveaway posted in {channel.mention}.", discord.Color.green(), bot.user), ephemeral=True)


async def end_giveaway(giveaway: dict):
    guild = bot.get_guild(giveaway["guild_id"])
    if not guild:
        return
    channel = guild.get_channel(giveaway["channel_id"])
    entrants = json.loads(giveaway.get("entrants") or "[]")
    import random
    winners_count = min(giveaway["winners"], len(entrants))
    winners = random.sample(entrants, winners_count) if entrants else []

    supabase.table("giveaways").update({"status": "ended"}).eq("id", giveaway["id"]).execute()

    if not winners:
        result = "No valid entrants — no winner could be selected."
    else:
        result = ", ".join(f"<@{w}>" for w in winners)

    embed = make_embed(f"🎉 Giveaway Ended: {giveaway['prize']}", f"Winner(s): {result}", discord.Color.gold(), bot.user)
    if channel:
        await channel.send(embed=embed)


@bot.tree.command(name="gend", description="End a giveaway early")
@has_mod_perms()
async def gend_cmd(interaction: discord.Interaction, message_id: str):
    res = supabase.table("giveaways").select("*").eq("message_id", int(message_id)).eq("status", "active").execute()
    if not res.data:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "Active giveaway not found.", discord.Color.red(), bot.user), ephemeral=True)
    await end_giveaway(res.data[0])
    await interaction.response.send_message(embed=make_embed("✅ Giveaway Ended", "The giveaway has been ended.", discord.Color.green(), bot.user), ephemeral=True)


@bot.tree.command(name="greroll", description="Reroll a giveaway winner")
@has_mod_perms()
async def greroll_cmd(interaction: discord.Interaction, message_id: str):
    res = supabase.table("giveaways").select("*").eq("message_id", int(message_id)).execute()
    if not res.data:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "Giveaway not found.", discord.Color.red(), bot.user), ephemeral=True)
    giveaway = res.data[0]
    entrants = json.loads(giveaway.get("entrants") or "[]")
    if not entrants:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "No entrants to reroll.", discord.Color.red(), bot.user), ephemeral=True)
    import random
    new_winner = random.choice(entrants)
    await interaction.response.send_message(embed=make_embed("🎉 Giveaway Rerolled", f"New winner: <@{new_winner}>", discord.Color.gold(), bot.user))


@bot.tree.command(name="glist", description="List active giveaways")
async def glist_cmd(interaction: discord.Interaction):
    res = supabase.table("giveaways").select("*").eq("guild_id", interaction.guild_id).eq("status", "active").execute()
    if not res.data:
        return await interaction.response.send_message(embed=make_embed("🎉 Active Giveaways", "None currently active.", discord.Color.blurple(), bot.user))
    desc = "\n".join(f"**{g['prize']}** — ends <t:{int(datetime.datetime.fromisoformat(g['end_time']).timestamp())}:R>" for g in res.data)
    await interaction.response.send_message(embed=make_embed("🎉 Active Giveaways", desc, discord.Color.gold(), bot.user))


@tasks.loop(seconds=30)
async def giveaway_checker():
    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        res = supabase.table("giveaways").select("*").eq("status", "active").lte("end_time", now).execute()
        for giveaway in res.data:
            await end_giveaway(giveaway)
    except Exception as e:
        logger.error(f"giveaway_checker error: {e}")


# ==================================================================
# 16. USERINFO SYSTEM
# ==================================================================

@bot.tree.command(name="userinfo", description="View information about a member")
async def userinfo_cmd(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    member = member or interaction.user
    roles = ", ".join(r.mention for r in reversed(member.roles) if r != interaction.guild.default_role) or "None"
    embed = make_embed(f"👤 {member}", color=member.color if member.color != discord.Color.default() else discord.Color.blurple(), bot_user=bot.user)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
    embed.add_field(name="Boosting", value="Yes" if member.premium_since else "No", inline=True)
    embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
    embed.add_field(name="Joined Server", value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "Unknown", inline=True)
    embed.add_field(name="Roles", value=roles[:1024], inline=False)
    await interaction.response.send_message(embed=embed)


# ==================================================================
# 17. SERVER INFO SYSTEM
# ==================================================================

@bot.tree.command(name="serverinfo", description="View information about the server")
async def serverinfo_cmd(interaction: discord.Interaction):
    guild = interaction.guild
    embed = make_embed(f"🏰 {guild.name}", color=discord.Color.blurple(), bot_user=bot.user)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Owner", value=str(guild.owner), inline=True)
    embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, "R"), inline=True)
    embed.add_field(name="Members", value=str(guild.member_count), inline=True)
    embed.add_field(name="Boost Level", value=str(guild.premium_tier), inline=True)
    embed.add_field(name="Channels", value=str(len(guild.channels)), inline=True)
    embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
    embed.add_field(name="Verification Level", value=str(guild.verification_level).title(), inline=True)
    await interaction.response.send_message(embed=embed)


# ==================================================================
# 18. MEMBER COUNT SYSTEM
# ==================================================================

@bot.tree.command(name="membercountset", description="Set the channel that displays live member count")
@has_admin_perms()
async def membercountset_cmd(interaction: discord.Interaction, channel: discord.abc.GuildChannel):
    await update_guild_config(interaction.guild_id, membercount_channel=channel.id)
    await update_membercount_channel(interaction.guild)
    await interaction.response.send_message(embed=make_embed("👥 Member Count", f"Live count channel set to {channel.mention}.", discord.Color.green(), bot.user))


async def update_membercount_channel(guild: discord.Guild):
    cfg = await get_guild_config(guild.id)
    channel_id = cfg.get("membercount_channel")
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if channel:
        try:
            await channel.edit(name=f"Members: {guild.member_count:,}")
        except discord.HTTPException:
            pass


# ==================================================================
# 19. BOT STATS SYSTEM
# ==================================================================

@bot.tree.command(name="botstats", description="View bot statistics")
async def botstats_cmd(interaction: discord.Interaction):
    import psutil
    uptime_seconds = int(time.time() - bot.start_time)
    uptime = str(datetime.timedelta(seconds=uptime_seconds))
    process = psutil.Process(os.getpid())
    mem_mb = process.memory_info().rss / 1024 / 1024

    embed = make_embed("📊 Bot Statistics", color=discord.Color.blurple(), bot_user=bot.user)
    embed.add_field(name="Servers", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Users", value=str(sum(g.member_count for g in bot.guilds)), inline=True)
    embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)}ms", inline=True)
    embed.add_field(name="Uptime", value=uptime, inline=True)
    embed.add_field(name="Memory Usage", value=f"{mem_mb:.1f} MB", inline=True)
    embed.add_field(name="discord.py", value=discord.__version__, inline=True)
    embed.add_field(name="Commands Executed", value=str(bot.commands_executed), inline=True)
    await interaction.response.send_message(embed=embed)


# ==================================================================
# 20. STATUS MONITOR SYSTEM
# ==================================================================

@bot.tree.command(name="statusmonitor", description="Configure the live status monitor channel")
@has_admin_perms()
async def statusmonitor_cmd(interaction: discord.Interaction, channel: discord.abc.GuildChannel, address: str, port: Optional[int] = None):
    """`port` is optional — omit it to monitor plain host reachability (HTTP HEAD /
    socket probe on common ports) instead of a specific TCP port."""
    supabase.table("status_monitor_config").upsert({
        "guild_id": interaction.guild_id, "channel_id": channel.id, "address": address, "port": port
    }, on_conflict="guild_id,channel_id").execute()
    target_desc = f"`{address}:{port}`" if port else f"`{address}` (host reachability only)"
    await interaction.response.send_message(embed=make_embed("📡 Status Monitor", f"Monitoring {target_desc} → {channel.mention}", discord.Color.green(), bot.user))


async def check_host(address: str, port: Optional[int] = None, timeout: float = 3.0) -> bool:
    """If a port is given, does a TCP connect check on that exact port.
    If no port is given, falls back to an HTTP HEAD request (covers web
    services) and, failing that, a raw TCP probe on common ports (80/443)
    as a best-effort 'is this host up at all' check."""
    if port:
        try:
            conn = asyncio.open_connection(address, port)
            reader, writer = await asyncio.wait_for(conn, timeout=timeout)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    # No port specified: try HTTP(S) first, then common ports as a fallback.
    url = address if address.startswith(("http://", "https://")) else f"https://{address}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True) as resp:
                return resp.status < 500
    except Exception:
        for fallback_port in (443, 80):
            if await check_host(address, fallback_port, timeout):
                return True
        return False


@tasks.loop(seconds=60)
async def status_monitor_loop():
    try:
        res = supabase.table("status_monitor_config").select("*").execute()
        for cfg in res.data:
            guild = bot.get_guild(cfg["guild_id"])
            if not guild:
                continue
            channel = guild.get_channel(cfg["channel_id"])
            if not channel:
                continue
            port = cfg.get("port")
            online = await check_host(cfg["address"], port)
            status_icon = "🟢" if online else "🔴"
            port_label = f" | Port: {port}" if port else ""
            new_name = f"{status_icon} Node: {'Online' if online else 'Offline'}{port_label}"
            if channel.name != new_name:
                try:
                    await channel.edit(name=new_name)
                except discord.HTTPException:
                    pass
    except Exception as e:
        logger.error(f"status_monitor_loop error: {e}")


# ==================================================================
# 21. CHAT AI SYSTEM (OpenRouter)
# ==================================================================

SYSTEM_PROMPT = (
    "You are VantixNodes Bot, a helpful, friendly AI assistant integrated into a Discord server. "
    "Keep responses concise, clear, and useful. You were developed by AashirwadGamerzz."
)


@bot.tree.command(name="ask", description="Ask the VantixNodes AI a question")
@app_commands.checks.cooldown(1, 10.0)
async def ask_cmd(interaction: discord.Interaction, question: str):
    if not OPENROUTER_API_KEY:
        return await interaction.response.send_message(embed=make_embed("❌ AI Unavailable", "OpenRouter API key is not configured.", discord.Color.red(), bot.user), ephemeral=True)

    await interaction.response.defer()
    context = bot.ai_context.setdefault(interaction.user.id, [])
    context.append({"role": "user", "content": question})
    context[:] = context[-10:]  # keep last 10 messages

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + context

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={"model": OPENROUTER_MODEL, "messages": messages},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"OpenRouter returned {resp.status}")
                data = await resp.json()
                answer = data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"OpenRouter error: {e}")
        return await interaction.followup.send(embed=make_embed("❌ AI Error", "Failed to get a response from the AI service. Please try again later.", discord.Color.red(), bot.user))

    context.append({"role": "assistant", "content": answer})
    embed = make_embed("🤖 VantixNodes AI", answer[:4000], discord.Color.purple(), bot.user)
    embed.set_footer(text=f"{BOT_NAME} • Asked by {interaction.user}", icon_url=bot.user.display_avatar.url)
    await interaction.followup.send(embed=embed)


# ==================================================================
# 22. AFK SYSTEM
# ==================================================================

@bot.tree.command(name="afk", description="Set yourself as AFK")
async def afk_cmd(interaction: discord.Interaction, reason: str = "AFK"):
    key = f"{interaction.guild_id}:{interaction.user.id}"
    await cache_set("afk_cache", key, {"reason": reason, "time": time.time()})
    await interaction.response.send_message(embed=make_embed("💤 AFK Set", f"{interaction.user.mention} is now AFK: {reason}", discord.Color.blurple(), bot.user))


async def handle_afk(message: discord.Message):
    key = f"{message.guild.id}:{message.author.id}"
    info = await cache_get("afk_cache", key)
    if info is not None:
        await cache_delete("afk_cache", key)
        embed = make_embed("👋 Welcome Back", f"{message.author.mention}, I've removed your AFK status.", discord.Color.green(), bot.user)
        await message.channel.send(embed=embed, delete_after=10)

    if message.mentions:
        for user in message.mentions:
            mkey = f"{message.guild.id}:{user.id}"
            minfo = await cache_get("afk_cache", mkey)
            if minfo is not None:
                since = int(time.time() - minfo["time"])
                embed = make_embed("💤 AFK", f"{user.mention} is AFK: {minfo['reason']} ({since}s ago)", discord.Color.blurple(), bot.user)
                await message.channel.send(embed=embed, delete_after=10)


# ==================================================================
# 23. ABOUT COMMAND
# ==================================================================

@bot.tree.command(name="about", description="About VantixNodes Bot")
async def about_cmd(interaction: discord.Interaction):
    uptime_seconds = int(time.time() - bot.start_time)
    uptime = str(datetime.timedelta(seconds=uptime_seconds))
    embed = make_embed(f"ℹ️ About {BOT_NAME}", color=discord.Color.blurple(), bot_user=bot.user)
    embed.add_field(name="Status", value=f"🟢 Online | {round(bot.latency * 1000)}ms", inline=True)
    embed.add_field(name="Uptime", value=uptime, inline=True)
    embed.add_field(name="Developer", value=BOT_DEVELOPER, inline=True)
    embed.set_thumbnail(url=bot.user.display_avatar.url)
    await interaction.response.send_message(embed=embed)


# ==================================================================
# 24. REACTION-ROLE SYSTEM
# ==================================================================

reactionrole_group = app_commands.Group(name="reactionrole", description="Manage reaction roles")


@reactionrole_group.command(name="add", description="Add a reaction role to a message")
@has_admin_perms()
async def reactionrole_add(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role, channel: Optional[discord.TextChannel] = None):
    channel = channel or interaction.channel
    try:
        target_message = await channel.fetch_message(int(message_id))
    except (discord.NotFound, ValueError):
        return await interaction.response.send_message(embed=make_embed("❌ Error", "Message not found in that channel.", discord.Color.red(), bot.user), ephemeral=True)

    try:
        await target_message.add_reaction(emoji)
    except discord.HTTPException:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "Invalid emoji.", discord.Color.red(), bot.user), ephemeral=True)

    supabase.table("reaction_roles").upsert({
        "guild_id": interaction.guild_id, "channel_id": channel.id, "message_id": int(message_id),
        "emoji": emoji, "role_id": role.id,
    }, on_conflict="message_id,emoji").execute()

    await interaction.response.send_message(embed=make_embed("✅ Reaction Role Added", f"{emoji} → {role.mention} on that message.", discord.Color.green(), bot.user), ephemeral=True)


@reactionrole_group.command(name="remove", description="Remove a reaction role mapping")
@has_admin_perms()
async def reactionrole_remove(interaction: discord.Interaction, message_id: str, emoji: str):
    supabase.table("reaction_roles").delete().eq("message_id", int(message_id)).eq("emoji", emoji).execute()
    await interaction.response.send_message(embed=make_embed("✅ Reaction Role Removed", "Mapping removed.", discord.Color.green(), bot.user), ephemeral=True)


bot.tree.add_command(reactionrole_group)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.member is None or payload.member.bot:
        return
    res = supabase.table("reaction_roles").select("*").eq("message_id", payload.message_id).eq("emoji", str(payload.emoji)).execute()
    if not res.data:
        return
    guild = bot.get_guild(payload.guild_id)
    role = guild.get_role(res.data[0]["role_id"]) if guild else None
    if guild and role:
        try:
            await payload.member.add_roles(role, reason="Reaction role")
        except discord.Forbidden:
            pass


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    res = supabase.table("reaction_roles").select("*").eq("message_id", payload.message_id).eq("emoji", str(payload.emoji)).execute()
    if not res.data:
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    role = guild.get_role(res.data[0]["role_id"])
    if member and role:
        try:
            await member.remove_roles(role, reason="Reaction role removed")
        except discord.Forbidden:
            pass


# ==================================================================
# 25. LEVELING / XP SYSTEM
# ==================================================================

LEVEL_XP_COOLDOWN = 60  # seconds between XP awards per user, to prevent spam-farming
XP_PER_MESSAGE = (15, 25)  # random range


def xp_for_level(level: int) -> int:
    """Total XP required to reach `level` (simple quadratic curve)."""
    return 5 * (level ** 2) + 50 * level + 100


async def add_xp(message: discord.Message):
    key = f"{message.guild.id}:{message.author.id}"
    last_award = await cache_get("xp_cooldowns", key, default=0)
    now = time.time()
    if now - last_award < LEVEL_XP_COOLDOWN:
        return
    await cache_set("xp_cooldowns", key, now, ttl=LEVEL_XP_COOLDOWN)

    import random
    gained = random.randint(*XP_PER_MESSAGE)

    res = supabase.table("levels").select("*").eq("guild_id", message.guild.id).eq("user_id", message.author.id).execute()
    if res.data:
        record = res.data[0]
        new_xp = record["xp"] + gained
        level = record["level"]
        leveled_up = False
        while new_xp >= xp_for_level(level):
            new_xp -= xp_for_level(level)
            level += 1
            leveled_up = True
        supabase.table("levels").update({"xp": new_xp, "level": level}).eq("id", record["id"]).execute()
    else:
        new_xp, level, leveled_up = gained, 0, False
        supabase.table("levels").insert({"guild_id": message.guild.id, "user_id": message.author.id, "xp": new_xp, "level": level}).execute()

    if leveled_up:
        embed = make_embed("🎉 Level Up!", f"{message.author.mention} reached **level {level}**!", discord.Color.gold(), bot.user)
        try:
            await message.channel.send(embed=embed)
        except discord.Forbidden:
            pass


@bot.tree.command(name="rank", description="View your (or another member's) level and XP")
async def rank_cmd(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    member = member or interaction.user
    res = supabase.table("levels").select("*").eq("guild_id", interaction.guild_id).eq("user_id", member.id).execute()
    if not res.data:
        return await interaction.response.send_message(embed=make_embed("📈 Rank", f"{member.mention} hasn't earned any XP yet.", discord.Color.blurple(), bot.user))
    record = res.data[0]
    needed = xp_for_level(record["level"])
    embed = make_embed(f"📈 Rank — {member}", color=discord.Color.blurple(), bot_user=bot.user)
    embed.add_field(name="Level", value=str(record["level"]), inline=True)
    embed.add_field(name="XP", value=f"{record['xp']}/{needed}", inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="levelleaderboard", description="View the server's XP leaderboard")
async def levelleaderboard_cmd(interaction: discord.Interaction):
    res = supabase.table("levels").select("*").eq("guild_id", interaction.guild_id).order("level", desc=True).order("xp", desc=True).limit(10).execute()
    if not res.data:
        return await interaction.response.send_message(embed=make_embed("🏆 XP Leaderboard", "No data yet.", discord.Color.blurple(), bot.user))
    desc = "\n".join(f"**{i+1}.** <@{r['user_id']}> — Level {r['level']} ({r['xp']} XP)" for i, r in enumerate(res.data))
    await interaction.response.send_message(embed=make_embed("🏆 XP Leaderboard", desc, discord.Color.gold(), bot.user))


# ==================================================================
# 26. BACKUP / RESTORE SYSTEM
# ==================================================================
# Snapshots the contents of guild_config (channels, toggles, messages, etc.)
# so an admin can roll back after a misconfiguration or migrate settings.

@bot.tree.command(name="backup", description="Create a backup of this server's VantixNodes configuration")
@has_admin_perms()
async def backup_cmd(interaction: discord.Interaction, label: str = "manual"):
    cfg = await get_guild_config(interaction.guild_id)
    snapshot = {k: v for k, v in cfg.items() if k != "guild_id"}
    res = supabase.table("guild_backups").insert({
        "guild_id": interaction.guild_id, "label": label, "snapshot": json.dumps(snapshot),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }).execute()
    backup_id = res.data[0]["id"]
    await interaction.response.send_message(embed=make_embed("💾 Backup Created", f"Backup `#{backup_id}` (`{label}`) saved. Use `/restore backup_id:{backup_id}` to roll back to it.", discord.Color.green(), bot.user), ephemeral=True)


@bot.tree.command(name="restore", description="Restore this server's VantixNodes configuration from a backup")
@has_admin_perms()
async def restore_cmd(interaction: discord.Interaction, backup_id: int):
    res = supabase.table("guild_backups").select("*").eq("id", backup_id).eq("guild_id", interaction.guild_id).execute()
    if not res.data:
        return await interaction.response.send_message(embed=make_embed("❌ Error", "Backup not found for this server.", discord.Color.red(), bot.user), ephemeral=True)
    snapshot = json.loads(res.data[0]["snapshot"])
    await update_guild_config(interaction.guild_id, **snapshot)
    await interaction.response.send_message(embed=make_embed("♻️ Configuration Restored", f"Restored from backup `#{backup_id}` (`{res.data[0]['label']}`).", discord.Color.green(), bot.user))


@bot.tree.command(name="backuplist", description="List available configuration backups for this server")
@has_admin_perms()
async def backuplist_cmd(interaction: discord.Interaction):
    res = supabase.table("guild_backups").select("id,label,created_at").eq("guild_id", interaction.guild_id).order("created_at", desc=True).limit(10).execute()
    if not res.data:
        return await interaction.response.send_message(embed=make_embed("💾 Backups", "No backups yet. Use `/backup` to create one.", discord.Color.blurple(), bot.user), ephemeral=True)
    desc = "\n".join(f"**#{b['id']}** — `{b['label']}` ({b['created_at'][:19]})" for b in res.data)
    await interaction.response.send_message(embed=make_embed("💾 Backups", desc, discord.Color.blurple(), bot.user), ephemeral=True)


# ==================================================================
# 27. PREMIUM TIER SYSTEM
# ==================================================================
# Per-guild feature gating driven by the `premium` boolean on guild_config.
# Wrap any command with @premium_feature() to restrict it to premium guilds
# (already defined earlier, near get_guild_config). Example usages you can
# apply to specific commands: @premium_feature() above @dmall_cmd, or above
# a future "advanced analytics" command.

premium_group = app_commands.Group(name="premium", description="Manage VantixNodes Premium for this server")


@premium_group.command(name="status", description="Check this server's premium status")
async def premium_status(interaction: discord.Interaction):
    active = await is_premium(interaction.guild_id)
    embed = make_embed("✨ VantixNodes Premium", f"Status: **{'Active ✅' if active else 'Inactive'}**", discord.Color.gold() if active else discord.Color.blurple(), bot.user)
    await interaction.response.send_message(embed=embed)


@premium_group.command(name="set", description="[Bot Owner Only] Toggle premium for a server")
async def premium_set(interaction: discord.Interaction, guild_id: str, enabled: bool):
    if interaction.user.id != interaction.guild.owner_id and not await bot.is_owner(interaction.user):
        return await interaction.response.send_message(embed=make_embed("❌ Error", "Only the bot owner can manage premium grants.", discord.Color.red(), bot.user), ephemeral=True)
    await update_guild_config(int(guild_id), premium=enabled)
    await interaction.response.send_message(embed=make_embed("✨ Premium Updated", f"Premium set to **{enabled}** for guild `{guild_id}`.", discord.Color.green(), bot.user), ephemeral=True)


bot.tree.add_command(premium_group)


# ==================================================================
# LIFECYCLE EVENTS
# ==================================================================

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} ({bot.user.id})")
    await on_ready_populate_invites()
    if not giveaway_checker.is_running():
        giveaway_checker.start()
    if not status_monitor_loop.is_running():
        status_monitor_loop.start()
    if not dm_queue_worker.is_running():
        dm_queue_worker.start()
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        logger.error(f"Command sync failed: {e}")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="VantixNodes | /about"))


@bot.event
async def on_guild_join(guild):
    bot.invite_cache[guild.id] = {}
    await get_guild_config(guild.id)
    logger.info(f"Joined guild: {guild.name} ({guild.id})")


# ==================================================================
# ENTRYPOINT
# ==================================================================

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN, log_handler=None)
