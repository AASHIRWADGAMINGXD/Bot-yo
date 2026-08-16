"""
VantixNodes — Discord Server Management Bot
=============================================
Single-file, production-grade Discord bot built with discord.py 2.x
(slash commands via app_commands) and Supabase (PostgreSQL) as the
persistence layer.

Sections (top to bottom):
    1. Imports & Config
    2. Logging setup
    3. Supabase client + DB helper functions
    4. Keep-alive web server (optional)
    5. Bot class & lifecycle
    6. Anti-Nuke system
    7. AutoMod system (heat/spam filter)
    8. Audit / event logging
    9. Moderation slash commands
    10. Welcome system
    11. Giveaway system
    12. Server stats
    13. AFK system
    14. Node monitoring
    15. Ticket system
    16. Status/presence command
    17. Run block

Author: VantixNodes dev team
"""

# ============================================================
# 1. IMPORTS & CONFIG
# ============================================================
import os
import re
import io
import time
import asyncio
import logging
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

try:
    from supabase import create_client, Client
except ImportError:
    raise SystemExit(
        "Missing dependency 'supabase'. Run: pip install supabase"
    )

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BOT_OWNER_ID = os.getenv("BOT_OWNER_ID")
PORT = os.getenv("PORT")  # optional keep-alive web server port

if not DISCORD_BOT_TOKEN:
    raise SystemExit("DISCORD_BOT_TOKEN is missing from your .env file.")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise SystemExit("SUPABASE_URL / SUPABASE_KEY missing from your .env file.")
if not BOT_OWNER_ID:
    raise SystemExit("BOT_OWNER_ID is missing from your .env file.")

BOT_OWNER_ID = int(BOT_OWNER_ID)

# ============================================================
# 2. LOGGING SETUP
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("VantixNodes")

# ============================================================
# 3. SUPABASE CLIENT + DB HELPER FUNCTIONS
# ============================================================
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


async def db_call(fn, *args, **kwargs):
    """
    Runs a blocking supabase-py call in a background thread so it never
    blocks the event loop, and wraps it with consistent error handling.
    Returns None on failure and logs the exception instead of raising,
    so a single DB hiccup never crashes a command.
    """
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))
    except Exception:
        log.error("Supabase call failed:\n%s", traceback.format_exc())
        return None


async def get_guild_config(guild_id: int) -> dict:
    res = await db_call(
        lambda: supabase.table("guild_config").select("*").eq("guild_id", str(guild_id)).execute()
    )
    if res and res.data:
        return res.data[0]
    # create a default row
    default = {"guild_id": str(guild_id)}
    await db_call(lambda: supabase.table("guild_config").insert(default).execute())
    return default


async def upsert_guild_config(guild_id: int, patch: dict):
    patch = dict(patch)
    patch["guild_id"] = str(guild_id)
    await db_call(
        lambda: supabase.table("guild_config").upsert(patch, on_conflict="guild_id").execute()
    )


async def next_case_id(guild_id: int) -> int:
    res = await db_call(
        lambda: supabase.table("mod_cases")
        .select("case_id")
        .eq("guild_id", str(guild_id))
        .order("case_id", desc=True)
        .limit(1)
        .execute()
    )
    if res and res.data:
        return int(res.data[0]["case_id"]) + 1
    return 1


async def log_mod_case(guild_id: int, moderator_id: int, target_id: int, action: str, reason: str) -> int:
    case_id = await next_case_id(guild_id)
    row = {
        "case_id": case_id,
        "guild_id": str(guild_id),
        "moderator_id": str(moderator_id),
        "target_id": str(target_id),
        "action": action,
        "reason": reason or "No reason provided",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await db_call(lambda: supabase.table("mod_cases").insert(row).execute())
    return case_id


# ============================================================
# 4. KEEP-ALIVE WEB SERVER (OPTIONAL)
# ============================================================
async def start_keepalive_server():
    """Starts a minimal aiohttp web server exposing GET /health for
    external uptime monitors (UptimeRobot, etc). Only runs if PORT is set."""
    if not PORT:
        return
    from aiohttp import web

    async def health(_request):
        return web.json_response({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})

    app = web.Application()
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(PORT))
    await site.start()
    log.info("Keep-alive web server listening on port %s (/health)", PORT)


# ============================================================
# 5. BOT CLASS & LIFECYCLE
# ============================================================
INTENTS = discord.Intents.default()
INTENTS.members = True
INTENTS.message_content = True
INTENTS.guilds = True
INTENTS.moderation = True


class VantixNodes(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!vx-unused!", intents=INTENTS, help_command=None)
        # in-memory runtime state (per-process, not persisted)
        self.antinuke_hits: dict[int, list[float]] = {}   # guild_id -> [timestamps]
        self.automod_heat: dict[tuple, dict] = {}         # (guild_id, user_id) -> {"heat": float, "last": ts}
        self.afk_cache: dict[tuple, dict] = {}            # (guild_id, user_id) -> {"reason":..., "since":...}
        self.ticket_panels: dict[int, int] = {}           # channel_id -> message_id

    async def setup_hook(self):
        # background loops
        self.node_monitor_loop.start()
        self.server_stats_loop.start()
        self.giveaway_check_loop.start()
        # sync slash commands
        await self.tree.sync()
        log.info("Slash commands synced.")
        # optional keep-alive server
        asyncio.create_task(start_keepalive_server())

    async def on_ready(self):
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        # restore last-set presence from DB
        res = await db_call(lambda: supabase.table("guild_config").select("*").limit(1).execute())
        try:
            row = await db_call(
                lambda: supabase.table("bot_state").select("*").eq("key", "presence").execute()
            )
            if row and row.data:
                status_str = row.data[0].get("value", "online")
                await self.change_presence(status=_status_from_str(status_str))
        except Exception:
            pass

    async def on_error(self, event_method, *args, **kwargs):
        log.error("Unhandled error in %s:\n%s", event_method, traceback.format_exc())

    # ---- background loops defined here, bodies implemented in later sections ----
    @tasks.loop(seconds=60)
    async def node_monitor_loop(self):
        await run_node_monitor(self)

    @tasks.loop(seconds=60)
    async def server_stats_loop(self):
        await run_server_stats(self)

    @tasks.loop(seconds=30)
    async def giveaway_check_loop(self):
        await run_giveaway_check(self)

    @node_monitor_loop.before_loop
    @server_stats_loop.before_loop
    @giveaway_check_loop.before_loop
    async def _before_loops(self):
        await self.wait_until_ready()


def _status_from_str(s: str) -> discord.Status:
    return {
        "online": discord.Status.online,
        "idle": discord.Status.idle,
        "dnd": discord.Status.dnd,
        "invisible": discord.Status.invisible,
    }.get(s, discord.Status.online)


bot = VantixNodes()


# ------------------------------------------------------------
# Permission helpers
# ------------------------------------------------------------
def is_owner_or_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id == BOT_OWNER_ID:
            return True
        if isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator:
            return True
        raise app_commands.CheckFailure("You need Administrator permission to use this command.")
    return app_commands.check(predicate)


async def get_log_channel(guild: discord.Guild, key: str) -> Optional[discord.TextChannel]:
    cfg = await get_guild_config(guild.id)
    chan_id = cfg.get(key)
    if not chan_id:
        return None
    ch = guild.get_channel(int(chan_id))
    return ch if isinstance(ch, discord.TextChannel) else None


async def send_log_embed(guild: discord.Guild, key: str, embed: discord.Embed):
    ch = await get_log_channel(guild, key)
    if ch:
        try:
            await ch.send(embed=embed)
        except discord.HTTPException:
            pass


# ============================================================
# 6. ANTI-NUKE SYSTEM
# ============================================================
DANGEROUS_PERMS = ("administrator", "ban_members", "manage_roles", "manage_channels", "manage_guild", "kick_members")


async def is_whitelisted(guild_id: int, user_id: int) -> bool:
    res = await db_call(
        lambda: supabase.table("whitelist")
        .select("*")
        .eq("guild_id", str(guild_id))
        .eq("user_id", str(user_id))
        .execute()
    )
    return bool(res and res.data)


async def antinuke_register_hit(bot_: VantixNodes, guild: discord.Guild, actor: discord.abc.User, action: str):
    """Registers a suspicious action and triggers punishment if the
    configured threshold (actions within window) is exceeded."""
    if actor.id == BOT_OWNER_ID or actor.bot and actor.id == bot_.user.id:
        return
    if await is_whitelisted(guild.id, actor.id):
        return

    cfg = await get_guild_config(guild.id)
    threshold = int(cfg.get("antinuke_threshold", 5))
    window = int(cfg.get("antinuke_window_seconds", 10))

    key = f"{guild.id}:{actor.id}"
    now = time.time()
    hits = bot_.antinuke_hits.setdefault(key, [])
    hits.append(now)
    # drop stale hits outside the window
    bot_.antinuke_hits[key] = [t for t in hits if now - t <= window]

    if len(bot_.antinuke_hits[key]) >= threshold:
        bot_.antinuke_hits[key] = []  # reset after triggering
        await antinuke_punish(guild, actor, action)


async def antinuke_punish(guild: discord.Guild, actor: discord.abc.User, action: str):
    member = guild.get_member(actor.id)
    reason = f"VantixNodes Anti-Nuke: exceeded action threshold ({action})"

    try:
        if member:
            # strip all roles first (least destructive, reversible)
            removable = [r for r in member.roles if r != guild.default_role and r < guild.me.top_role]
            if removable:
                await member.remove_roles(*removable, reason=reason)
            try:
                await guild.ban(member, reason=reason, delete_message_seconds=0)
            except discord.Forbidden:
                pass
    except discord.HTTPException:
        pass

    embed = discord.Embed(
        title="🛡️ Anti-Nuke Triggered",
        description=f"Action taken against **{actor}** (`{actor.id}`)\nTrigger: `{action}`",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    await send_log_embed(guild, "audit_log_channel", embed)

    try:
        if guild.owner:
            await guild.owner.send(
                f"🚨 VantixNodes Anti-Nuke fired in **{guild.name}** against {actor} ({actor.id}) — reason: {action}"
            )
    except discord.HTTPException:
        pass


@bot.event
async def on_audit_log_entry_create(entry: discord.AuditLogEntry):
    guild = entry.guild
    actor = entry.user
    if actor is None or actor.bot and actor.id == bot.user.id:
        return

    action_map = {
        discord.AuditLogAction.ban: "mass_ban",
        discord.AuditLogAction.kick: "mass_kick",
        discord.AuditLogAction.channel_delete: "mass_channel_delete",
        discord.AuditLogAction.role_delete: "mass_role_delete",
    }
    if entry.action in action_map:
        await antinuke_register_hit(bot, guild, actor, action_map[entry.action])

    if entry.action == discord.AuditLogAction.role_update:
        after = getattr(entry.after, "permissions", None)
        if after and any(getattr(after, p, False) for p in DANGEROUS_PERMS):
            await antinuke_register_hit(bot, guild, actor, "dangerous_permission_grant")


# ============================================================
# 7. AUTOMOD SYSTEM (heat / spam filter)
# ============================================================
INVITE_RE = re.compile(r"(discord\.gg|discordapp\.com/invite)/\S+", re.I)
LINK_RE = re.compile(r"https?://\S+", re.I)
ZALGO_RE = re.compile(r"[\u0300-\u036f]{3,}")

HEAT_DECAY_PER_SEC = 0.5
HEAT_PER_MESSAGE = 3.0
HEAT_MUTE_THRESHOLD = 12.0


async def automod_bypass(member: discord.Member, cfg: dict) -> bool:
    if member.guild_permissions.administrator:
        return True
    bypass_roles = cfg.get("automod_bypass_roles") or []
    return any(str(r.id) in bypass_roles for r in member.roles)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    # AFK auto-clear + mention notice
    await handle_afk_logic(message)

    cfg = await get_guild_config(message.guild.id)
    if cfg.get("automod_enabled", True) and isinstance(message.author, discord.Member):
        if not await automod_bypass(message.author, cfg):
            await run_automod_checks(message, cfg)

    await bot.process_commands(message)


async def run_automod_checks(message: discord.Message, cfg: dict):
    content = message.content or ""
    violations = []

    if cfg.get("block_invites", True) and INVITE_RE.search(content):
        violations.append("invite_link")
    if cfg.get("block_links", False) and LINK_RE.search(content):
        violations.append("link")
    if cfg.get("block_mass_mentions", True) and len(message.mentions) >= int(cfg.get("mass_mention_limit", 5)):
        violations.append("mass_mentions")
    if cfg.get("block_caps", True) and len(content) > 12:
        letters = [c for c in content if c.isalpha()]
        if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.7:
            violations.append("caps_abuse")
    if cfg.get("block_zalgo", True) and ZALGO_RE.search(content):
        violations.append("zalgo")

    blacklist = cfg.get("word_blacklist") or []
    if blacklist and any(w.lower() in content.lower() for w in blacklist):
        violations.append("blacklisted_word")

    if violations:
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        await bump_heat(message, weight=1.5 * len(violations))
        embed = discord.Embed(
            title="🚫 AutoMod Action",
            description=f"Deleted message from {message.author.mention} in {message.channel.mention}\nReason: `{', '.join(violations)}`",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        await send_log_embed(message.guild, "mod_log_channel", embed)
        return

    await bump_heat(message, weight=1.0)


async def bump_heat(message: discord.Message, weight: float = 1.0):
    key = (message.guild.id, message.author.id)
    state = bot.automod_heat.get(key, {"heat": 0.0, "last": time.time()})
    now = time.time()
    elapsed = now - state["last"]
    state["heat"] = max(0.0, state["heat"] - elapsed * HEAT_DECAY_PER_SEC)
    state["heat"] += HEAT_PER_MESSAGE * weight
    state["last"] = now
    bot.automod_heat[key] = state

    if state["heat"] >= HEAT_MUTE_THRESHOLD:
        state["heat"] = 0.0
        member = message.guild.get_member(message.author.id)
        if member:
            try:
                await member.timeout(timedelta(minutes=10), reason="VantixNodes AutoMod: spam heat threshold exceeded")
                embed = discord.Embed(
                    title="🔇 Auto-Timeout",
                    description=f"{member.mention} was timed out for 10 minutes (spam heat threshold).",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                )
                await send_log_embed(message.guild, "mod_log_channel", embed)
            except discord.Forbidden:
                pass


# ============================================================
# 8. AUDIT / EVENT LOGGING
# ============================================================
@bot.event
async def on_member_join(member: discord.Member):
    cfg = await get_guild_config(member.guild.id)
    embed = discord.Embed(
        title="📥 Member Joined",
        description=f"{member.mention} ({member})",
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    await send_log_embed(member.guild, "join_leave_log_channel", embed)
    await send_welcome_message(member, cfg)


@bot.event
async def on_member_remove(member: discord.Member):
    embed = discord.Embed(
        title="📤 Member Left",
        description=f"{member} ({member.id})",
        color=discord.Color.dark_grey(),
        timestamp=datetime.now(timezone.utc),
    )
    await send_log_embed(member.guild, "join_leave_log_channel", embed)


@bot.event
async def on_message_delete(message: discord.Message):
    if not message.guild or message.author.bot:
        return
    embed = discord.Embed(
        title="🗑️ Message Deleted",
        description=f"**Author:** {message.author.mention}\n**Channel:** {message.channel.mention}",
        color=discord.Color.dark_red(),
        timestamp=datetime.now(timezone.utc),
    )
    if message.content:
        embed.add_field(name="Content", value=message.content[:1000], inline=False)
    await send_log_embed(message.guild, "message_log_channel", embed)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if not before.guild or before.author.bot or before.content == after.content:
        return
    embed = discord.Embed(
        title="✏️ Message Edited",
        description=f"**Author:** {before.author.mention}\n**Channel:** {before.channel.mention}",
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Before", value=(before.content or "*empty*")[:500], inline=False)
    embed.add_field(name="After", value=(after.content or "*empty*")[:500], inline=False)
    await send_log_embed(before.guild, "message_log_channel", embed)


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    embed = discord.Embed(
        title="🗑️ Channel Deleted",
        description=f"`#{channel.name}` ({channel.id})",
        color=discord.Color.dark_red(),
        timestamp=datetime.now(timezone.utc),
    )
    await send_log_embed(channel.guild, "audit_log_channel", embed)


@bot.event
async def on_guild_role_delete(role: discord.Role):
    embed = discord.Embed(
        title="🗑️ Role Deleted",
        description=f"`{role.name}` ({role.id})",
        color=discord.Color.dark_red(),
        timestamp=datetime.now(timezone.utc),
    )
    await send_log_embed(role.guild, "audit_log_channel", embed)


# ============================================================
# 9. MODERATION SLASH COMMANDS
# ============================================================
mod_group = app_commands.Group(name="role", description="Role management commands")


@bot.tree.command(name="kick", description="Kick a member from the server.")
@app_commands.checks.has_permissions(kick_members=True)
async def kick_cmd(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = None):
    await interaction.response.defer(ephemeral=True)
    try:
        await member.kick(reason=reason or "No reason provided")
    except discord.Forbidden:
        await interaction.followup.send("I don't have permission to kick that member.", ephemeral=True)
        return
    case_id = await log_mod_case(interaction.guild_id, interaction.user.id, member.id, "kick", reason)
    await interaction.followup.send(f"✅ Kicked {member.mention} — Case #{case_id}", ephemeral=True)
    embed = discord.Embed(title="👢 Member Kicked", color=discord.Color.orange(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Target", value=f"{member} ({member.id})")
    embed.add_field(name="Moderator", value=interaction.user.mention)
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
    embed.set_footer(text=f"Case #{case_id}")
    await send_log_embed(interaction.guild, "mod_log_channel", embed)


@bot.tree.command(name="ban", description="Ban a member from the server.")
@app_commands.checks.has_permissions(ban_members=True)
async def ban_cmd(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = None,
                   delete_message_days: Optional[int] = 0):
    await interaction.response.defer(ephemeral=True)
    try:
        await member.ban(reason=reason or "No reason provided",
                          delete_message_seconds=(delete_message_days or 0) * 86400)
    except discord.Forbidden:
        await interaction.followup.send("I don't have permission to ban that member.", ephemeral=True)
        return
    case_id = await log_mod_case(interaction.guild_id, interaction.user.id, member.id, "ban", reason)
    await interaction.followup.send(f"✅ Banned {member.mention} — Case #{case_id}", ephemeral=True)
    embed = discord.Embed(title="🔨 Member Banned", color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Target", value=f"{member} ({member.id})")
    embed.add_field(name="Moderator", value=interaction.user.mention)
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
    embed.set_footer(text=f"Case #{case_id}")
    await send_log_embed(interaction.guild, "mod_log_channel", embed)


@bot.tree.command(name="unban", description="Unban a user by ID.")
@app_commands.checks.has_permissions(ban_members=True)
async def unban_cmd(interaction: discord.Interaction, user_id: str, reason: Optional[str] = None):
    await interaction.response.defer(ephemeral=True)
    try:
        user = discord.Object(id=int(user_id))
        await interaction.guild.unban(user, reason=reason or "No reason provided")
    except (discord.NotFound, ValueError):
        await interaction.followup.send("That user isn't banned or the ID is invalid.", ephemeral=True)
        return
    except discord.Forbidden:
        await interaction.followup.send("I don't have permission to unban.", ephemeral=True)
        return
    case_id = await log_mod_case(interaction.guild_id, interaction.user.id, int(user_id), "unban", reason)
    await interaction.followup.send(f"✅ Unbanned `{user_id}` — Case #{case_id}", ephemeral=True)


@bot.tree.command(name="timeout", description="Timeout (mute) a member for a duration in minutes.")
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout_cmd(interaction: discord.Interaction, member: discord.Member, minutes: int,
                       reason: Optional[str] = None):
    await interaction.response.defer(ephemeral=True)
    try:
        await member.timeout(timedelta(minutes=minutes), reason=reason or "No reason provided")
    except discord.Forbidden:
        await interaction.followup.send("I don't have permission to timeout that member.", ephemeral=True)
        return
    case_id = await log_mod_case(interaction.guild_id, interaction.user.id, member.id, "timeout", reason)
    await interaction.followup.send(f"✅ Timed out {member.mention} for {minutes}m — Case #{case_id}", ephemeral=True)


@bot.tree.command(name="warn", description="Warn a member.")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn_cmd(interaction: discord.Interaction, member: discord.Member, reason: str):
    await interaction.response.defer(ephemeral=True)
    row = {
        "guild_id": str(interaction.guild_id),
        "user_id": str(member.id),
        "moderator_id": str(interaction.user.id),
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await db_call(lambda: supabase.table("warnings").insert(row).execute())
    case_id = await log_mod_case(interaction.guild_id, interaction.user.id, member.id, "warn", reason)
    await interaction.followup.send(f"✅ Warned {member.mention} — Case #{case_id}", ephemeral=True)
    try:
        await member.send(f"You were warned in **{interaction.guild.name}**: {reason}")
    except discord.HTTPException:
        pass


@bot.tree.command(name="warnings", description="View a member's warning history.")
async def warnings_cmd(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    res = await db_call(
        lambda: supabase.table("warnings").select("*")
        .eq("guild_id", str(interaction.guild_id)).eq("user_id", str(member.id))
        .order("timestamp", desc=True).execute()
    )
    rows = res.data if res and res.data else []
    if not rows:
        await interaction.followup.send(f"{member.mention} has no warnings.", ephemeral=True)
        return
    embed = discord.Embed(title=f"Warnings for {member}", color=discord.Color.yellow())
    for w in rows[:10]:
        embed.add_field(name=w["timestamp"][:19], value=w["reason"], inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="lock", description="Lock the current channel (deny @everyone send messages).")
@app_commands.checks.has_permissions(manage_channels=True)
async def lock_cmd(interaction: discord.Interaction):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message("🔒 Channel locked.")


@bot.tree.command(name="unlock", description="Unlock the current channel.")
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock_cmd(interaction: discord.Interaction):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = None
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message("🔓 Channel unlocked.")


@mod_group.command(name="add", description="Add a role to a member.")
@app_commands.checks.has_permissions(manage_roles=True)
async def role_add_cmd(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    try:
        await member.add_roles(role, reason=f"By {interaction.user}")
        await interaction.response.send_message(f"✅ Added {role.mention} to {member.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("I can't manage that role (hierarchy).", ephemeral=True)


@mod_group.command(name="remove", description="Remove a role from a member.")
@app_commands.checks.has_permissions(manage_roles=True)
async def role_remove_cmd(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    try:
        await member.remove_roles(role, reason=f"By {interaction.user}")
        await interaction.response.send_message(f"✅ Removed {role.mention} from {member.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("I can't manage that role (hierarchy).", ephemeral=True)


bot.tree.add_command(mod_group)


@bot.tree.command(name="purge", description="Bulk delete messages.")
@app_commands.checks.has_permissions(manage_messages=True)
async def purge_cmd(interaction: discord.Interaction, count: app_commands.Range[int, 1, 100],
                     member: Optional[discord.Member] = None, contains: Optional[str] = None):
    await interaction.response.defer(ephemeral=True)

    def check(m: discord.Message) -> bool:
        if member and m.author.id != member.id:
            return False
        if contains and contains.lower() not in (m.content or "").lower():
            return False
        return True

    try:
        deleted = await interaction.channel.purge(limit=count, check=check)
    except discord.Forbidden:
        await interaction.followup.send("I don't have permission to delete messages here.", ephemeral=True)
        return
    await interaction.followup.send(f"🧹 Deleted {len(deleted)} messages.", ephemeral=True)


@kick_cmd.error
@ban_cmd.error
@unban_cmd.error
@timeout_cmd.error
@warn_cmd.error
@lock_cmd.error
@unlock_cmd.error
@purge_cmd.error
async def moderation_error_handler(interaction: discord.Interaction, error: app_commands.AppCommandError):
    msg = "You don't have permission to do that." if isinstance(error, app_commands.MissingPermissions) else \
          f"Something went wrong: {error}"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)
    log.warning("Command error: %s", error)


# ============================================================
# 10. WELCOME SYSTEM
# ============================================================
async def send_welcome_message(member: discord.Member, cfg: dict):
    if not cfg.get("welcome_enabled", False):
        return
    chan_id = cfg.get("welcome_channel_id")
    if not chan_id:
        return
    channel = member.guild.get_channel(int(chan_id))
    if not isinstance(channel, discord.TextChannel):
        return

    title = (cfg.get("welcome_title") or "Welcome {user}!").format(
        user=member.display_name, server=member.guild.name, membercount=member.guild.member_count
    )
    description = (cfg.get("welcome_description") or "Glad to have you here, {user}!").format(
        user=member.mention, server=member.guild.name, membercount=member.guild.member_count
    )
    color = cfg.get("welcome_color")
    embed = discord.Embed(title=title, description=description,
                           color=int(color, 16) if color else discord.Color.blurple())
    if cfg.get("welcome_image_url"):
        embed.set_image(url=cfg["welcome_image_url"])
    embed.set_thumbnail(url=member.display_avatar.url)
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


welcome_group = app_commands.Group(name="welcome", description="Configure the welcome system")


@welcome_group.command(name="setup", description="Configure the welcome message.")
@is_owner_or_admin()
async def welcome_setup(interaction: discord.Interaction, channel: discord.TextChannel,
                         title: str = "Welcome {user}!",
                         description: str = "Glad to have you here, {user}! You're member #{membercount}.",
                         image_url: Optional[str] = None, color_hex: Optional[str] = "5865F2"):
    await upsert_guild_config(interaction.guild_id, {
        "welcome_enabled": True,
        "welcome_channel_id": str(channel.id),
        "welcome_title": title,
        "welcome_description": description,
        "welcome_image_url": image_url,
        "welcome_color": color_hex,
    })
    await interaction.response.send_message(f"✅ Welcome messages will be sent in {channel.mention}.", ephemeral=True)


@welcome_group.command(name="disable", description="Disable the welcome system.")
@is_owner_or_admin()
async def welcome_disable(interaction: discord.Interaction):
    await upsert_guild_config(interaction.guild_id, {"welcome_enabled": False})
    await interaction.response.send_message("✅ Welcome messages disabled.", ephemeral=True)


bot.tree.add_command(welcome_group)


# ============================================================
# 11. GIVEAWAY SYSTEM
# ============================================================
class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: str):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id

    @discord.ui.button(label="🎉 Enter", style=discord.ButtonStyle.green, custom_id="giveaway_enter")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        res = await db_call(
            lambda: supabase.table("giveaways").select("*").eq("giveaway_id", self.giveaway_id).execute()
        )
        if not res or not res.data:
            await interaction.response.send_message("This giveaway no longer exists.", ephemeral=True)
            return
        row = res.data[0]
        entrants = set(row.get("entrants") or [])
        if str(interaction.user.id) in entrants:
            entrants.discard(str(interaction.user.id))
            msg = "❌ You left the giveaway."
        else:
            entrants.add(str(interaction.user.id))
            msg = "✅ You entered the giveaway! Good luck."
        await db_call(
            lambda: supabase.table("giveaways").update({"entrants": list(entrants)})
            .eq("giveaway_id", self.giveaway_id).execute()
        )
        await interaction.response.send_message(msg, ephemeral=True)


giveaway_group = app_commands.Group(name="giveaway", description="Run server giveaways")


@giveaway_group.command(name="start", description="Start a giveaway.")
@is_owner_or_admin()
async def giveaway_start(interaction: discord.Interaction, prize: str, duration_minutes: int, winners: int = 1):
    end_time = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
    embed = discord.Embed(
        title="🎉 Giveaway!",
        description=f"**Prize:** {prize}\n**Winners:** {winners}\n**Ends:** <t:{int(end_time.timestamp())}:R>",
        color=discord.Color.magenta(),
    )
    giveaway_id = f"{interaction.guild_id}-{int(time.time())}"
    view = GiveawayView(giveaway_id)
    await interaction.response.send_message(embed=embed, view=view)
    message = await interaction.original_response()

    row = {
        "giveaway_id": giveaway_id,
        "guild_id": str(interaction.guild_id),
        "channel_id": str(interaction.channel_id),
        "message_id": str(message.id),
        "prize": prize,
        "end_time": end_time.isoformat(),
        "winners_count": winners,
        "entrants": [],
        "status": "active",
    }
    await db_call(lambda: supabase.table("giveaways").insert(row).execute())


@giveaway_group.command(name="end", description="End a giveaway early by message ID.")
@is_owner_or_admin()
async def giveaway_end(interaction: discord.Interaction, message_id: str):
    await interaction.response.defer(ephemeral=True)
    await finish_giveaway_by_message_id(message_id)
    await interaction.followup.send("✅ Giveaway ended.", ephemeral=True)


@giveaway_group.command(name="reroll", description="Reroll winners for a finished giveaway.")
@is_owner_or_admin()
async def giveaway_reroll(interaction: discord.Interaction, message_id: str):
    await interaction.response.defer()
    res = await db_call(lambda: supabase.table("giveaways").select("*").eq("message_id", message_id).execute())
    if not res or not res.data:
        await interaction.followup.send("Giveaway not found.")
        return
    row = res.data[0]
    winners = pick_giveaway_winners(row)
    if not winners:
        await interaction.followup.send("No entrants to reroll from.")
        return
    await interaction.followup.send(f"🎉 New winner(s): {', '.join(f'<@{w}>' for w in winners)}")


bot.tree.add_command(giveaway_group)


def pick_giveaway_winners(row: dict) -> list[str]:
    import random
    entrants = list(row.get("entrants") or [])
    count = min(int(row.get("winners_count", 1)), len(entrants))
    return random.sample(entrants, count) if count else []


async def finish_giveaway_by_message_id(message_id: str):
    res = await db_call(lambda: supabase.table("giveaways").select("*").eq("message_id", message_id).execute())
    if not res or not res.data:
        return
    row = res.data[0]
    if row.get("status") != "active":
        return
    winners = pick_giveaway_winners(row)
    guild = bot.get_guild(int(row["guild_id"]))
    channel = guild.get_channel(int(row["channel_id"])) if guild else None
    if channel:
        if winners:
            await channel.send(f"🎉 Giveaway for **{row['prize']}** has ended! Winner(s): "
                                f"{', '.join(f'<@{w}>' for w in winners)}")
        else:
            await channel.send(f"🎉 Giveaway for **{row['prize']}** ended with no entrants.")
    await db_call(lambda: supabase.table("giveaways").update({"status": "ended"}).eq("giveaway_id", row["giveaway_id"]).execute())


async def run_giveaway_check(bot_: VantixNodes):
    res = await db_call(lambda: supabase.table("giveaways").select("*").eq("status", "active").execute())
    if not res or not res.data:
        return
    now = datetime.now(timezone.utc)
    for row in res.data:
        try:
            end_time = datetime.fromisoformat(row["end_time"])
        except (ValueError, KeyError):
            continue
        if now >= end_time:
            await finish_giveaway_by_message_id(row["message_id"])


# ============================================================
# 12. SERVER STATS
# ============================================================
async def run_server_stats(bot_: VantixNodes):
    for guild in bot_.guilds:
        cfg = await get_guild_config(guild.id)
        chan_id = cfg.get("stats_channel_id")
        if not chan_id:
            continue
        channel = guild.get_channel(int(chan_id))
        if not channel:
            continue
        online = sum(1 for m in guild.members if m.status != discord.Status.offline)
        try:
            if isinstance(channel, discord.VoiceChannel):
                await channel.edit(name=f"👥 Members: {guild.member_count}")
            else:
                await channel.send(
                    f"📊 **{guild.name} Stats** — Members: {guild.member_count} | "
                    f"Online: {online} | Boosts: {guild.premium_subscription_count}"
                )
        except discord.HTTPException:
            pass


stats_group = app_commands.Group(name="stats", description="Live server stats")


@stats_group.command(name="setup", description="Set the channel used for live server stats.")
@is_owner_or_admin()
async def stats_setup(interaction: discord.Interaction, channel: discord.abc.GuildChannel):
    await upsert_guild_config(interaction.guild_id, {"stats_channel_id": str(channel.id)})
    await interaction.response.send_message(f"✅ Server stats will update in {channel.mention}.", ephemeral=True)


bot.tree.add_command(stats_group)


# ============================================================
# 13. AFK SYSTEM
# ============================================================
@bot.tree.command(name="afk", description="Set yourself as AFK.")
async def afk_cmd(interaction: discord.Interaction, reason: Optional[str] = "AFK"):
    key = (interaction.guild_id, interaction.user.id)
    bot.afk_cache[key] = {"reason": reason, "since": datetime.now(timezone.utc).isoformat()}
    await db_call(lambda: supabase.table("afk_status").upsert({
        "guild_id": str(interaction.guild_id), "user_id": str(interaction.user.id),
        "reason": reason, "since": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="guild_id,user_id").execute())
    await interaction.response.send_message(f"💤 You're now AFK: {reason}", ephemeral=True)


async def handle_afk_logic(message: discord.Message):
    key = (message.guild.id, message.author.id)
    if key in bot.afk_cache:
        del bot.afk_cache[key]
        await db_call(lambda: supabase.table("afk_status").delete()
                      .eq("guild_id", str(message.guild.id)).eq("user_id", str(message.author.id)).execute())
        try:
            await message.channel.send(f"👋 Welcome back, {message.author.mention}! I've removed your AFK status.",
                                        delete_after=8)
        except discord.HTTPException:
            pass

    for user in message.mentions:
        akey = (message.guild.id, user.id)
        if akey in bot.afk_cache:
            info = bot.afk_cache[akey]
            try:
                await message.channel.send(f"💤 {user.display_name} is AFK: {info['reason']}", delete_after=8)
            except discord.HTTPException:
                pass


# ============================================================
# 14. NODE MONITORING
# ============================================================
node_group = app_commands.Group(name="node", description="Monitor external servers/services")


@node_group.command(name="add", description="Add a node to monitor.")
@is_owner_or_admin()
async def node_add(interaction: discord.Interaction, name: str, address: str):
    row = {"guild_id": str(interaction.guild_id), "name": name, "address": address, "status": "unknown"}
    await db_call(lambda: supabase.table("monitored_nodes").insert(row).execute())
    await interaction.response.send_message(f"✅ Added node **{name}** (`{address}`).", ephemeral=True)


@node_group.command(name="remove", description="Remove a monitored node by name.")
@is_owner_or_admin()
async def node_remove(interaction: discord.Interaction, name: str):
    await db_call(lambda: supabase.table("monitored_nodes").delete()
                  .eq("guild_id", str(interaction.guild_id)).eq("name", name).execute())
    await interaction.response.send_message(f"✅ Removed node **{name}**.", ephemeral=True)


@node_group.command(name="list", description="List monitored nodes and their status.")
async def node_list(interaction: discord.Interaction):
    res = await db_call(lambda: supabase.table("monitored_nodes").select("*")
                         .eq("guild_id", str(interaction.guild_id)).execute())
    rows = res.data if res and res.data else []
    if not rows:
        await interaction.response.send_message("No nodes are being monitored.", ephemeral=True)
        return
    embed = discord.Embed(title="🖥️ Monitored Nodes", color=discord.Color.blue())
    for n in rows:
        emoji = "🟢" if n.get("status") == "online" else ("🔴" if n.get("status") == "offline" else "⚪")
        embed.add_field(name=f"{emoji} {n['name']}", value=f"`{n['address']}`\nLast checked: {n.get('last_checked', 'never')}",
                         inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(node_group)


async def check_node(address: str) -> bool:
    """TCP/host reachability check using an ICMP-free approach (HTTP HEAD or TCP connect)."""
    host = address
    port = 80
    if ":" in address:
        host, port_str = address.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            port = 80
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=5)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def run_node_monitor(bot_: VantixNodes):
    res = await db_call(lambda: supabase.table("monitored_nodes").select("*").execute())
    if not res or not res.data:
        return
    for n in res.data:
        online = await check_node(n["address"])
        await db_call(lambda n=n, online=online: supabase.table("monitored_nodes").update({
            "status": "online" if online else "offline",
            "last_checked": datetime.now(timezone.utc).isoformat(),
        }).eq("node_id", n["node_id"]).execute())


# ============================================================
# 15. TICKET SYSTEM
# ============================================================
class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 Open Ticket", style=discord.ButtonStyle.blurple, custom_id="ticket_open")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        cfg = await get_guild_config(guild.id)
        category_id = cfg.get("ticket_category_id")
        category = guild.get_channel(int(category_id)) if category_id else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        channel = await guild.create_text_channel(
            name=f"ticket-{interaction.user.name}"[:90], overwrites=overwrites, category=category,
            reason=f"Ticket opened by {interaction.user}",
        )
        row = {
            "guild_id": str(guild.id), "channel_id": str(channel.id), "opener_id": str(interaction.user.id),
            "status": "open", "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db_call(lambda: supabase.table("tickets").insert(row).execute())

        embed = discord.Embed(title="🎫 Support Ticket",
                               description=f"{interaction.user.mention}, a staff member will be with you shortly.",
                               color=discord.Color.blurple())
        await channel.send(embed=embed, view=TicketControlView())
        await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.green, custom_id="ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await db_call(lambda: supabase.table("tickets").update({"claimer_id": str(interaction.user.id)})
                      .eq("channel_id", str(interaction.channel_id)).execute())
        await interaction.response.send_message(f"✅ Ticket claimed by {interaction.user.mention}")

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, custom_id="ticket_close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 Closing ticket and saving transcript...")
        await close_ticket(interaction.channel, interaction.user)


async def close_ticket(channel: discord.TextChannel, closer: discord.abc.User):
    lines = []
    async for msg in channel.history(limit=None, oldest_first=True):
        lines.append(f"[{msg.created_at:%Y-%m-%d %H:%M}] {msg.author}: {msg.content}")
    transcript = "\n".join(lines) or "(no messages)"

    res = await db_call(lambda: supabase.table("tickets").select("*").eq("channel_id", str(channel.id)).execute())
    opener_id = None
    if res and res.data:
        opener_id = res.data[0].get("opener_id")
        await db_call(lambda: supabase.table("tickets").update({
            "status": "closed", "closed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("channel_id", str(channel.id)).execute())

    if opener_id:
        opener = channel.guild.get_member(int(opener_id))
        if opener:
            try:
                file = discord.File(io.BytesIO(transcript.encode()), filename=f"transcript-{channel.name}.txt")
                await opener.send(f"Your ticket in **{channel.guild.name}** was closed.", file=file)
            except discord.HTTPException:
                pass

    await asyncio.sleep(3)
    try:
        await channel.delete(reason=f"Ticket closed by {closer}")
    except discord.HTTPException:
        pass


ticket_group = app_commands.Group(name="ticket", description="Ticket system commands")


@ticket_group.command(name="panel", description="Deploy the ticket-opening panel.")
@is_owner_or_admin()
async def ticket_panel(interaction: discord.Interaction, category: Optional[discord.CategoryChannel] = None):
    if category:
        await upsert_guild_config(interaction.guild_id, {"ticket_category_id": str(category.id)})
    embed = discord.Embed(title="🎫 Need Help?",
                           description="Click the button below to open a support ticket.",
                           color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, view=TicketPanelView())


@ticket_group.command(name="claim", description="Claim the current ticket.")
async def ticket_claim(interaction: discord.Interaction):
    await db_call(lambda: supabase.table("tickets").update({"claimer_id": str(interaction.user.id)})
                  .eq("channel_id", str(interaction.channel_id)).execute())
    await interaction.response.send_message(f"✅ Claimed by {interaction.user.mention}")


@ticket_group.command(name="close", description="Close the current ticket.")
async def ticket_close(interaction: discord.Interaction):
    await interaction.response.send_message("🔒 Closing ticket...")
    await close_ticket(interaction.channel, interaction.user)


@ticket_group.command(name="add", description="Add a user to this ticket.")
async def ticket_add(interaction: discord.Interaction, member: discord.Member):
    await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
    await interaction.response.send_message(f"✅ Added {member.mention} to this ticket.")


@ticket_group.command(name="remove", description="Remove a user from this ticket.")
async def ticket_remove(interaction: discord.Interaction, member: discord.Member):
    await interaction.channel.set_permissions(member, overwrite=None)
    await interaction.response.send_message(f"✅ Removed {member.mention} from this ticket.")


bot.tree.add_command(ticket_group)


# ============================================================
# 16. STATUS / PRESENCE COMMAND
# ============================================================
@bot.tree.command(name="status", description="Change the bot's presence (owner/admin only).")
@is_owner_or_admin()
async def status_cmd(interaction: discord.Interaction, state: Literal["online", "idle", "dnd", "invisible"]):
    await bot.change_presence(status=_status_from_str(state))
    await db_call(lambda: supabase.table("bot_state").upsert(
        {"key": "presence", "value": state}, on_conflict="key"
    ).execute())
    await interaction.response.send_message(f"✅ Presence set to `{state}`.", ephemeral=True)


# ============================================================
# 16b. WHITELIST / ANTI-NUKE CONFIG COMMANDS
# ============================================================
antinuke_group = app_commands.Group(name="antinuke", description="Configure anti-nuke protection")


@antinuke_group.command(name="config", description="Set the anti-nuke action threshold and time window.")
@is_owner_or_admin()
async def antinuke_config(interaction: discord.Interaction, threshold: int, window_seconds: int):
    await upsert_guild_config(interaction.guild_id, {
        "antinuke_threshold": threshold, "antinuke_window_seconds": window_seconds,
    })
    await interaction.response.send_message(
        f"✅ Anti-nuke set to trigger after **{threshold}** actions within **{window_seconds}s**.", ephemeral=True)


@antinuke_group.command(name="whitelist", description="Whitelist a user from anti-nuke checks.")
@is_owner_or_admin()
async def antinuke_whitelist(interaction: discord.Interaction, member: discord.Member):
    await db_call(lambda: supabase.table("whitelist").insert({
        "guild_id": str(interaction.guild_id), "user_id": str(member.id), "type": "antinuke",
    }).execute())
    await interaction.response.send_message(f"✅ {member.mention} is now whitelisted.", ephemeral=True)


bot.tree.add_command(antinuke_group)


log_group = app_commands.Group(name="logging", description="Configure log channels")


@log_group.command(name="set", description="Set a log channel.")
@is_owner_or_admin()
async def log_set(interaction: discord.Interaction,
                   kind: Literal["mod_log", "message_log", "join_leave_log", "audit_log"],
                   channel: discord.TextChannel):
    key = f"{kind}_channel" if kind != "audit_log" else "audit_log_channel"
    key = {"mod_log": "mod_log_channel", "message_log": "message_log_channel",
           "join_leave_log": "join_leave_log_channel", "audit_log": "audit_log_channel"}[kind]
    await upsert_guild_config(interaction.guild_id, {key: str(channel.id)})
    await interaction.response.send_message(f"✅ `{kind}` set to {channel.mention}.", ephemeral=True)


bot.tree.add_command(log_group)


automod_group = app_commands.Group(name="automod", description="Configure AutoMod")


@automod_group.command(name="toggle", description="Enable or disable AutoMod.")
@is_owner_or_admin()
async def automod_toggle(interaction: discord.Interaction, enabled: bool):
    await upsert_guild_config(interaction.guild_id, {"automod_enabled": enabled})
    await interaction.response.send_message(f"✅ AutoMod {'enabled' if enabled else 'disabled'}.", ephemeral=True)


@automod_group.command(name="blacklist_add", description="Add a word to the blacklist.")
@is_owner_or_admin()
async def automod_blacklist_add(interaction: discord.Interaction, word: str):
    cfg = await get_guild_config(interaction.guild_id)
    words = cfg.get("word_blacklist") or []
    if word.lower() not in [w.lower() for w in words]:
        words.append(word)
    await upsert_guild_config(interaction.guild_id, {"word_blacklist": words})
    await interaction.response.send_message(f"✅ Added `{word}` to the blacklist.", ephemeral=True)


bot.tree.add_command(automod_group)


# ============================================================
# 17. RUN BLOCK
# ============================================================
if __name__ == "__main__":
    try:
        bot.run(DISCORD_BOT_TOKEN, log_handler=None)
    except discord.LoginFailure:
        log.critical("Invalid DISCORD_BOT_TOKEN — check your .env file.")
    except Exception:
        log.critical("Fatal error on startup:\n%s", traceback.format_exc())
