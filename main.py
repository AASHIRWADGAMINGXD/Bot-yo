import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import json
import asyncio
import aiohttp
import datetime
import random
import string
import re
import io
import socket
import time
import pytz
import qrcode
import firebase_admin
from firebase_admin import credentials, db
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
#  KEEP-ALIVE (Flask — for Render / UptimeRobot)
# ─────────────────────────────────────────────
from flask import Flask
from threading import Thread

_flask_app = Flask(__name__)

@_flask_app.route("/")
def _health():
    return "Vantix Management V1 is online!", 200

def _run_flask():
    port = int(os.getenv("PORT", 8080))
    _flask_app.run(host="0.0.0.0", port=port)

Thread(target=_run_flask, daemon=True).start()

# ─────────────────────────────────────────────
#  FIREBASE SETUP
# ─────────────────────────────────────────────
firebase_config = {
    "type": "service_account",
    "project_id": "infinite-chats-web-app",
    "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID", ""),
    "private_key": os.getenv("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n"),
    "client_email": os.getenv("FIREBASE_CLIENT_EMAIL", ""),
    "client_id": os.getenv("FIREBASE_CLIENT_ID", ""),
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": os.getenv("FIREBASE_CLIENT_CERT_URL", ""),
    "universe_domain": "googleapis.com"
}

try:
    cred = credentials.Certificate(firebase_config)
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://infinite-chats-web-app-default-rtdb.firebaseio.com'
    })
    firebase_ok = True
except Exception as e:
    print(f"[Firebase] Warning: {e}. Using in-memory fallback.")
    firebase_ok = False

# ─────────────────────────────────────────────
#  DATABASE HELPERS  (Firebase or in-memory)
# ─────────────────────────────────────────────
_mem: dict = {}

def db_get(path: str):
    if firebase_ok:
        try:
            ref = db.reference(path)
            return ref.get()
        except Exception:
            pass
    keys = path.strip("/").split("/")
    node = _mem
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return None
        node = node[k]
    return node

def db_set(path: str, value):
    if firebase_ok:
        try:
            ref = db.reference(path)
            ref.set(value)
            return
        except Exception:
            pass
    keys = path.strip("/").split("/")
    node = _mem
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value

def db_push(path: str, value) -> str:
    uid = "".join(random.choices(string.ascii_lowercase + string.digits, k=16))
    db_set(f"{path}/{uid}", value)
    return uid

def db_delete(path: str):
    db_set(path, None)

# ─────────────────────────────────────────────
#  BOT SETUP
# ─────────────────────────────────────────────
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
tree = bot.tree

BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0"))
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")

# ─────────────────────────────────────────────
#  PERMISSION HELPERS
# ─────────────────────────────────────────────
def is_bot_owner(user: discord.User | discord.Member) -> bool:
    return user.id == BOT_OWNER_ID

def is_super_admin(user: discord.User | discord.Member) -> bool:
    if is_bot_owner(user): return True
    admins = db_get("superadmins") or {}
    return str(user.id) in admins

def is_extra_owner(guild: discord.Guild, user: discord.Member) -> bool:
    if is_super_admin(user): return True
    data = db_get(f"guilds/{guild.id}/extraowners") or {}
    return str(user.id) in data

def has_mod_perm(member: discord.Member) -> bool:
    if is_extra_owner(member.guild, member): return True
    return member.guild_permissions.manage_messages or member.guild_permissions.administrator

def has_admin_perm(member: discord.Member) -> bool:
    if is_extra_owner(member.guild, member): return True
    return member.guild_permissions.administrator

# ─────────────────────────────────────────────
#  EMBED HELPERS
# ─────────────────────────────────────────────
BRAND_COLOR = 0x5865F2

def ok_embed(title: str, desc: str = "") -> discord.Embed:
    e = discord.Embed(title=f"✅ {title}", description=desc, color=0x57F287)
    e.set_footer(text="Vantix Management V1")
    return e

def err_embed(title: str, desc: str = "") -> discord.Embed:
    e = discord.Embed(title=f"❌ {title}", description=desc, color=0xED4245)
    e.set_footer(text="Vantix Management V1")
    return e

def info_embed(title: str, desc: str = "") -> discord.Embed:
    e = discord.Embed(title=f"ℹ️ {title}", description=desc, color=BRAND_COLOR)
    e.set_footer(text="Vantix Management V1")
    return e

# ═══════════════════════════════════════════════════════════
#   BOT OWNER COMMANDS
# ═══════════════════════════════════════════════════════════

@tree.command(name="superadmin", description="Manage bot super admins")
@app_commands.describe(action="add / remove / list", user="Target user")
@app_commands.choices(action=[
    app_commands.Choice(name="add", value="add"),
    app_commands.Choice(name="remove", value="remove"),
    app_commands.Choice(name="list", value="list"),
])
async def superadmin(interaction: discord.Interaction, action: str, user: discord.User = None):
    if not is_bot_owner(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission", "Only the bot owner can use this."), ephemeral=True)
    admins = db_get("superadmins") or {}
    if action == "add":
        if not user:
            return await interaction.response.send_message(embed=err_embed("Missing User"), ephemeral=True)
        admins[str(user.id)] = {"name": str(user), "added": datetime.datetime.now(datetime.UTC).isoformat()}
        db_set("superadmins", admins)
        await interaction.response.send_message(embed=ok_embed("Super Admin Added", f"{user.mention} is now a super admin."))
    elif action == "remove":
        if not user:
            return await interaction.response.send_message(embed=err_embed("Missing User"), ephemeral=True)
        admins.pop(str(user.id), None)
        db_set("superadmins", admins)
        await interaction.response.send_message(embed=ok_embed("Super Admin Removed", f"{user.mention} removed."))
    else:
        if not admins:
            return await interaction.response.send_message(embed=info_embed("Super Admins", "No super admins configured."))
        lines = "\n".join(f"<@{uid}> — {v.get('name','?')}" for uid, v in admins.items())
        await interaction.response.send_message(embed=info_embed("Super Admins", lines))

@tree.command(name="botconfig", description="Configure bot-wide settings")
@app_commands.describe(setting="Setting name", value="Setting value")
async def botconfig(interaction: discord.Interaction, setting: str, value: str):
    if not is_bot_owner(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    db_set(f"botconfig/{setting}", value)
    await interaction.response.send_message(embed=ok_embed("Bot Config Updated", f"`{setting}` = `{value}`"))

@tree.command(name="extraowner", description="Manage server extra owners")
@app_commands.describe(action="add / remove / list", user="Target user")
@app_commands.choices(action=[
    app_commands.Choice(name="add", value="add"),
    app_commands.Choice(name="remove", value="remove"),
    app_commands.Choice(name="list", value="list"),
])
async def extraowner(interaction: discord.Interaction, action: str, user: discord.Member = None):
    if not is_super_admin(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    gid = interaction.guild.id
    data = db_get(f"guilds/{gid}/extraowners") or {}
    if action == "add":
        if not user:
            return await interaction.response.send_message(embed=err_embed("Missing User"), ephemeral=True)
        data[str(user.id)] = {"name": str(user)}
        db_set(f"guilds/{gid}/extraowners", data)
        await interaction.response.send_message(embed=ok_embed("Extra Owner Added", f"{user.mention} is now an extra owner."))
    elif action == "remove":
        if not user:
            return await interaction.response.send_message(embed=err_embed("Missing User"), ephemeral=True)
        data.pop(str(user.id), None)
        db_set(f"guilds/{gid}/extraowners", data)
        await interaction.response.send_message(embed=ok_embed("Extra Owner Removed", f"{user.mention} removed."))
    else:
        if not data:
            return await interaction.response.send_message(embed=info_embed("Extra Owners", "None configured."))
        lines = "\n".join(f"<@{uid}>" for uid in data)
        await interaction.response.send_message(embed=info_embed("Extra Owners", lines))

# ═══════════════════════════════════════════════════════════
#   SECURITY & PROTECTION
# ═══════════════════════════════════════════════════════════

@tree.command(name="antinuke", description="Anti-nuke protection system")
@app_commands.describe(
    action="enable / disable / config / whitelist / logs",
    option="For config/whitelist: setting or user mention",
    value="Value for config"
)
@app_commands.choices(action=[
    app_commands.Choice(name="enable", value="enable"),
    app_commands.Choice(name="disable", value="disable"),
    app_commands.Choice(name="config", value="config"),
    app_commands.Choice(name="whitelist", value="whitelist"),
    app_commands.Choice(name="logs", value="logs"),
])
async def antinuke(interaction: discord.Interaction, action: str, option: str = None, value: str = None):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    gid = interaction.guild.id
    path = f"guilds/{gid}/antinuke"
    if action == "enable":
        db_set(f"{path}/enabled", True)
        await interaction.response.send_message(embed=ok_embed("Anti-Nuke Enabled", "Anti-nuke protection is now active."))
    elif action == "disable":
        db_set(f"{path}/enabled", False)
        await interaction.response.send_message(embed=ok_embed("Anti-Nuke Disabled", "Anti-nuke protection deactivated."))
    elif action == "config":
        if not option or not value:
            cfg = db_get(path) or {}
            lines = "\n".join(f"`{k}`: {v}" for k, v in cfg.items()) or "Default config"
            return await interaction.response.send_message(embed=info_embed("Anti-Nuke Config", lines))
        db_set(f"{path}/{option}", value)
        await interaction.response.send_message(embed=ok_embed("Config Updated", f"`{option}` = `{value}`"))
    elif action == "whitelist":
        if not option:
            wl = db_get(f"{path}/whitelist") or {}
            lines = "\n".join(f"<@{uid}>" for uid in wl) or "Empty whitelist"
            return await interaction.response.send_message(embed=info_embed("Whitelist", lines))
        uid = option.strip("<@!>")
        wl = db_get(f"{path}/whitelist") or {}
        if uid in wl:
            wl.pop(uid)
            db_set(f"{path}/whitelist", wl)
            await interaction.response.send_message(embed=ok_embed("Whitelist Updated", f"<@{uid}> removed from whitelist."))
        else:
            wl[uid] = True
            db_set(f"{path}/whitelist", wl)
            await interaction.response.send_message(embed=ok_embed("Whitelist Updated", f"<@{uid}> added to whitelist."))
    elif action == "logs":
        logs = db_get(f"{path}/logs") or {}
        if not logs:
            return await interaction.response.send_message(embed=info_embed("Security Logs", "No logs found."))
        lines = []
        for k, v in list(logs.items())[-10:]:
            lines.append(f"`{v.get('time','?')}` — {v.get('event','?')} by <@{v.get('user','?')}>")
        await interaction.response.send_message(embed=info_embed("Security Logs (Last 10)", "\n".join(lines)))

@tree.command(name="antispam", description="Anti-spam system")
@app_commands.describe(action="enable / disable / config", option="Config key", value="Config value")
@app_commands.choices(action=[
    app_commands.Choice(name="enable", value="enable"),
    app_commands.Choice(name="disable", value="disable"),
    app_commands.Choice(name="config", value="config"),
])
async def antispam(interaction: discord.Interaction, action: str, option: str = None, value: str = None):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    gid = interaction.guild.id
    path = f"guilds/{gid}/antispam"
    if action == "enable":
        db_set(f"{path}/enabled", True)
        await interaction.response.send_message(embed=ok_embed("Anti-Spam Enabled"))
    elif action == "disable":
        db_set(f"{path}/enabled", False)
        await interaction.response.send_message(embed=ok_embed("Anti-Spam Disabled"))
    elif action == "config":
        if option and value:
            db_set(f"{path}/{option}", value)
            return await interaction.response.send_message(embed=ok_embed("Config Updated", f"`{option}` = `{value}`"))
        cfg = db_get(path) or {}
        lines = "\n".join(f"`{k}`: {v}" for k, v in cfg.items()) or "Default config"
        await interaction.response.send_message(embed=info_embed("Anti-Spam Config", lines))

@tree.command(name="badwords", description="Bad words filter management")
@app_commands.describe(action="add / remove / list", word="The word to add or remove")
@app_commands.choices(action=[
    app_commands.Choice(name="add", value="add"),
    app_commands.Choice(name="remove", value="remove"),
    app_commands.Choice(name="list", value="list"),
])
async def badwords(interaction: discord.Interaction, action: str, word: str = None):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    gid = interaction.guild.id
    path = f"guilds/{gid}/badwords"
    words = db_get(path) or []
    if action == "add":
        if not word:
            return await interaction.response.send_message(embed=err_embed("Missing Word"), ephemeral=True)
        if word.lower() not in words:
            words.append(word.lower())
            db_set(path, words)
        await interaction.response.send_message(embed=ok_embed("Bad Word Added", f"`{word}` added to filter."))
    elif action == "remove":
        if not word:
            return await interaction.response.send_message(embed=err_embed("Missing Word"), ephemeral=True)
        if word.lower() in words:
            words.remove(word.lower())
            db_set(path, words)
        await interaction.response.send_message(embed=ok_embed("Bad Word Removed", f"`{word}` removed."))
    else:
        if not words:
            return await interaction.response.send_message(embed=info_embed("Bad Words", "No bad words configured."))
        await interaction.response.send_message(embed=info_embed("Bad Words", ", ".join(f"`{w}`" for w in words)), ephemeral=True)

# ═══════════════════════════════════════════════════════════
#   MODERATION COMMANDS
# ═══════════════════════════════════════════════════════════

@tree.command(name="ban", description="Ban a user from the server")
@app_commands.describe(user="User to ban", reason="Reason for ban")
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    if user.top_role >= interaction.user.top_role and not is_extra_owner(interaction.guild, interaction.user):
        return await interaction.response.send_message(embed=err_embed("Cannot Ban", "Target has equal or higher role."), ephemeral=True)
    try:
        await user.ban(reason=f"{reason} | By: {interaction.user}")
        await interaction.response.send_message(embed=ok_embed("User Banned", f"{user.mention} was banned.\n**Reason:** {reason}"))
    except discord.Forbidden:
        await interaction.response.send_message(embed=err_embed("Failed", "I don't have permission to ban this user."), ephemeral=True)

@tree.command(name="kick", description="Kick a user from the server")
@app_commands.describe(user="User to kick", reason="Reason for kick")
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
    if not has_mod_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    try:
        await user.kick(reason=f"{reason} | By: {interaction.user}")
        await interaction.response.send_message(embed=ok_embed("User Kicked", f"{user.mention} was kicked.\n**Reason:** {reason}"))
    except discord.Forbidden:
        await interaction.response.send_message(embed=err_embed("Failed", "Cannot kick this user."), ephemeral=True)

@tree.command(name="timeout", description="Timeout (mute) a user temporarily")
@app_commands.describe(user="User to timeout", minutes="Duration in minutes", reason="Reason")
async def timeout_cmd(interaction: discord.Interaction, user: discord.Member, minutes: int, reason: str = "No reason provided"):
    if not has_mod_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
    try:
        await user.timeout(until, reason=reason)
        await interaction.response.send_message(embed=ok_embed("User Timed Out", f"{user.mention} timed out for {minutes}m.\n**Reason:** {reason}"))
    except discord.Forbidden:
        await interaction.response.send_message(embed=err_embed("Failed", "Cannot timeout this user."), ephemeral=True)

@tree.command(name="warn", description="Warn a user for rule violations")
@app_commands.describe(user="User to warn", reason="Reason")
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not has_mod_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    gid = interaction.guild.id
    uid = user.id
    path = f"guilds/{gid}/warnings/{uid}"
    entry = {"reason": reason, "by": str(interaction.user), "time": datetime.datetime.now(datetime.UTC).isoformat()}
    db_push(path, entry)
    warns = db_get(path) or {}
    count = len(warns)
    await interaction.response.send_message(embed=ok_embed("User Warned", f"{user.mention} warned. Total: **{count}**\n**Reason:** {reason}"))
    try:
        await user.send(embed=discord.Embed(title="⚠️ You have been warned", description=f"**Server:** {interaction.guild.name}\n**Reason:** {reason}", color=0xFEE75C))
    except Exception:
        pass

@tree.command(name="warnings", description="View user warnings")
@app_commands.describe(user="User to check")
async def warnings(interaction: discord.Interaction, user: discord.Member):
    if not has_mod_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    gid = interaction.guild.id
    path = f"guilds/{gid}/warnings/{user.id}"
    warns = db_get(path) or {}
    if not warns:
        return await interaction.response.send_message(embed=info_embed("No Warnings", f"{user.mention} has no warnings."))
    lines = []
    for i, (k, v) in enumerate(warns.items(), 1):
        lines.append(f"**{i}.** {v.get('reason','?')} — by {v.get('by','?')}")
    await interaction.response.send_message(embed=info_embed(f"Warnings for {user.display_name}", "\n".join(lines)))

@tree.command(name="clearwarns", description="Clear user warnings")
@app_commands.describe(user="User to clear warnings for")
async def clearwarns(interaction: discord.Interaction, user: discord.Member):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    db_delete(f"guilds/{interaction.guild.id}/warnings/{user.id}")
    await interaction.response.send_message(embed=ok_embed("Warnings Cleared", f"All warnings for {user.mention} cleared."))

@tree.command(name="purge", description="Delete multiple messages at once")
@app_commands.describe(amount="Number of messages to delete (1-100)")
async def purge(interaction: discord.Interaction, amount: int):
    if not has_mod_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    if amount < 1 or amount > 100:
        return await interaction.response.send_message(embed=err_embed("Invalid Amount", "Must be between 1 and 100."), ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(embed=ok_embed("Purge Complete", f"Deleted {len(deleted)} messages."), ephemeral=True)

@tree.command(name="lock", description="Lock a channel")
@app_commands.describe(channel="Channel to lock (default: current)")
async def lock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not has_mod_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    ch = channel or interaction.channel
    overwrite = ch.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await ch.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message(embed=ok_embed("Channel Locked", f"{ch.mention} is now locked."))

@tree.command(name="unlock", description="Unlock a channel")
@app_commands.describe(channel="Channel to unlock (default: current)")
async def unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not has_mod_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    ch = channel or interaction.channel
    overwrite = ch.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = None
    await ch.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message(embed=ok_embed("Channel Unlocked", f"{ch.mention} is now unlocked."))

@tree.command(name="slowmode", description="Set channel slowmode")
@app_commands.describe(seconds="Slowmode delay in seconds (0 to disable)", channel="Target channel")
async def slowmode(interaction: discord.Interaction, seconds: int, channel: discord.TextChannel = None):
    if not has_mod_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    ch = channel or interaction.channel
    await ch.edit(slowmode_delay=seconds)
    msg = f"{ch.mention} slowmode set to {seconds}s." if seconds > 0 else f"{ch.mention} slowmode disabled."
    await interaction.response.send_message(embed=ok_embed("Slowmode Updated", msg))

# ═══════════════════════════════════════════════════════════
#   TICKET SYSTEM
# ═══════════════════════════════════════════════════════════

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id="ticket_close_btn", emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ch = interaction.channel
        gid = interaction.guild.id
        ticket_data = db_get(f"guilds/{gid}/tickets/open/{ch.id}")
        if not ticket_data:
            return await interaction.response.send_message(embed=err_embed("Not a Ticket", "This channel is not a ticket."), ephemeral=True)
        await interaction.response.send_message(embed=info_embed("Ticket Closing", "This ticket will be archived."))
        await asyncio.sleep(3)
        db_delete(f"guilds/{gid}/tickets/open/{ch.id}")
        stats = db_get(f"guilds/{gid}/tickets/stats") or {"closed": 0, "opened": 0}
        stats["closed"] = int(stats.get("closed", 0)) + 1
        db_set(f"guilds/{gid}/tickets/stats", stats)
        try:
            await ch.delete(reason="Ticket closed")
        except Exception:
            pass

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.blurple, custom_id="ticket_open_btn", emoji="🎫")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        gid = guild.id
        config = db_get(f"guilds/{gid}/tickets/config") or {}
        category_id = config.get("category_id")
        category = None
        if category_id:
            category = guild.get_channel(int(category_id))
        member = interaction.user
        ticket_name = f"ticket-{member.name}-{random.randint(1000,9999)}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }
        try:
            channel = await guild.create_text_channel(ticket_name, category=category, overwrites=overwrites)
        except discord.Forbidden:
            return await interaction.response.send_message(embed=err_embed("Failed", "Missing permissions to create channel."), ephemeral=True)
        db_set(f"guilds/{gid}/tickets/open/{channel.id}", {
            "owner": str(member.id), "created": datetime.datetime.now(datetime.UTC).isoformat(), "claimed_by": None
        })
        stats = db_get(f"guilds/{gid}/tickets/stats") or {"closed": 0, "opened": 0}
        stats["opened"] = int(stats.get("opened", 0)) + 1
        db_set(f"guilds/{gid}/tickets/stats", stats)
        view = TicketCloseView()
        embed = discord.Embed(title="🎫 Ticket Created", description=f"Hello {member.mention}! Support will be with you shortly.\nClick the button below to close this ticket.", color=BRAND_COLOR)
        await channel.send(member.mention, embed=embed, view=view)
        await interaction.response.send_message(embed=ok_embed("Ticket Opened", f"Your ticket: {channel.mention}"), ephemeral=True)

@tree.command(name="ticket", description="Ticket system management")
@app_commands.describe(
    action="setup/panel/panels/editpanel/deletepanel/closeall/add/remove/close/claim/transcript/stats/addtype/listtypes/edittype/deletetype/config",
    option="Optional: user, panel name, or type name",
    value="Optional: value for the action"
)
async def ticket(interaction: discord.Interaction, action: str, option: str = None, value: str = None):
    gid = interaction.guild.id
    if action == "setup":
        if not has_admin_perm(interaction.user):
            return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
        config = db_get(f"guilds/{gid}/tickets/config") or {}
        embed = info_embed("Ticket System Setup",
            "**Current Config:**\n" +
            f"Category: {config.get('category_id', 'Not set')}\n" +
            f"Log Channel: {config.get('log_channel', 'Not set')}\n\n" +
            "Use `/ticket config` to set values."
        )
        await interaction.response.send_message(embed=embed)

    elif action == "panel":
        if not has_admin_perm(interaction.user):
            return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
        panel_name = option or "Support"
        view = TicketPanelView()
        embed = discord.Embed(title=f"🎫 {panel_name}", description="Click the button below to open a support ticket.", color=BRAND_COLOR)
        await interaction.channel.send(embed=embed, view=view)
        db_set(f"guilds/{gid}/tickets/panels/{panel_name}", {"channel_id": str(interaction.channel.id)})
        await interaction.response.send_message(embed=ok_embed("Panel Created", f"Ticket panel '{panel_name}' sent."), ephemeral=True)

    elif action == "panels":
        panels = db_get(f"guilds/{gid}/tickets/panels") or {}
        if not panels:
            return await interaction.response.send_message(embed=info_embed("Ticket Panels", "No panels configured."))
        lines = "\n".join(f"**{n}** — <#{v.get('channel_id','?')}>" for n, v in panels.items())
        await interaction.response.send_message(embed=info_embed("Ticket Panels", lines))

    elif action == "editpanel":
        if not has_admin_perm(interaction.user):
            return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
        if not option:
            return await interaction.response.send_message(embed=err_embed("Missing Panel Name"), ephemeral=True)
        db_set(f"guilds/{gid}/tickets/panels/{option}/updated", value or "true")
        await interaction.response.send_message(embed=ok_embed("Panel Updated", f"Panel `{option}` updated."))

    elif action == "deletepanel":
        if not has_admin_perm(interaction.user):
            return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
        if not option:
            return await interaction.response.send_message(embed=err_embed("Missing Panel Name"), ephemeral=True)
        db_delete(f"guilds/{gid}/tickets/panels/{option}")
        await interaction.response.send_message(embed=ok_embed("Panel Deleted", f"Panel `{option}` deleted."))

    elif action == "closeall":
        if not has_admin_perm(interaction.user):
            return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
        open_tickets = db_get(f"guilds/{gid}/tickets/open") or {}
        count = 0
        for ch_id in list(open_tickets.keys()):
            ch = interaction.guild.get_channel(int(ch_id))
            if ch:
                try:
                    await ch.delete(reason="Close all tickets")
                    count += 1
                except Exception:
                    pass
        db_delete(f"guilds/{gid}/tickets/open")
        await interaction.response.send_message(embed=ok_embed("All Tickets Closed", f"Closed {count} tickets."))

    elif action == "add":
        ch = interaction.channel
        ticket_data = db_get(f"guilds/{gid}/tickets/open/{ch.id}")
        if not ticket_data:
            return await interaction.response.send_message(embed=err_embed("Not a Ticket"), ephemeral=True)
        if not option:
            return await interaction.response.send_message(embed=err_embed("Missing User"), ephemeral=True)
        uid = option.strip("<@!>")
        member = interaction.guild.get_member(int(uid)) if uid.isdigit() else None
        if not member:
            return await interaction.response.send_message(embed=err_embed("User Not Found"), ephemeral=True)
        await ch.set_permissions(member, read_messages=True, send_messages=True)
        await interaction.response.send_message(embed=ok_embed("User Added", f"{member.mention} added to ticket."))

    elif action == "remove":
        ch = interaction.channel
        ticket_data = db_get(f"guilds/{gid}/tickets/open/{ch.id}")
        if not ticket_data:
            return await interaction.response.send_message(embed=err_embed("Not a Ticket"), ephemeral=True)
        if not option:
            return await interaction.response.send_message(embed=err_embed("Missing User"), ephemeral=True)
        uid = option.strip("<@!>")
        member = interaction.guild.get_member(int(uid)) if uid.isdigit() else None
        if not member:
            return await interaction.response.send_message(embed=err_embed("User Not Found"), ephemeral=True)
        await ch.set_permissions(member, overwrite=None)
        await interaction.response.send_message(embed=ok_embed("User Removed", f"{member.mention} removed from ticket."))

    elif action == "close":
        ch = interaction.channel
        ticket_data = db_get(f"guilds/{gid}/tickets/open/{ch.id}")
        if not ticket_data:
            return await interaction.response.send_message(embed=err_embed("Not a Ticket"), ephemeral=True)
        await interaction.response.send_message(embed=info_embed("Closing Ticket", "Deleting channel..."))
        db_delete(f"guilds/{gid}/tickets/open/{ch.id}")
        await asyncio.sleep(2)
        await ch.delete(reason=f"Ticket closed by {interaction.user}")

    elif action == "claim":
        ch = interaction.channel
        ticket_data = db_get(f"guilds/{gid}/tickets/open/{ch.id}")
        if not ticket_data:
            return await interaction.response.send_message(embed=err_embed("Not a Ticket"), ephemeral=True)
        ticket_data["claimed_by"] = str(interaction.user.id)
        db_set(f"guilds/{gid}/tickets/open/{ch.id}", ticket_data)
        await interaction.response.send_message(embed=ok_embed("Ticket Claimed", f"{interaction.user.mention} claimed this ticket."))

    elif action == "transcript":
        ch = interaction.channel
        ticket_data = db_get(f"guilds/{gid}/tickets/open/{ch.id}")
        if not ticket_data:
            return await interaction.response.send_message(embed=err_embed("Not a Ticket"), ephemeral=True)
        await interaction.response.defer()
        messages = []
        async for msg in ch.history(limit=500, oldest_first=True):
            messages.append(f"[{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {msg.author}: {msg.content}")
        transcript = "\n".join(messages)
        buf = io.BytesIO(transcript.encode())
        file = discord.File(buf, filename=f"transcript-{ch.name}.txt")
        await interaction.followup.send(embed=ok_embed("Transcript Generated"), file=file)

    elif action == "stats":
        stats = db_get(f"guilds/{gid}/tickets/stats") or {"opened": 0, "closed": 0}
        open_count = len(db_get(f"guilds/{gid}/tickets/open") or {})
        await interaction.response.send_message(embed=info_embed("Ticket Stats",
            f"**Total Opened:** {stats.get('opened',0)}\n"
            f"**Total Closed:** {stats.get('closed',0)}\n"
            f"**Currently Open:** {open_count}"
        ))

    elif action == "addtype":
        if not has_admin_perm(interaction.user):
            return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
        if not option:
            return await interaction.response.send_message(embed=err_embed("Missing Type Name"), ephemeral=True)
        db_set(f"guilds/{gid}/tickets/types/{option}", {"label": option, "desc": value or ""})
        await interaction.response.send_message(embed=ok_embed("Type Added", f"Ticket type `{option}` added."))

    elif action == "listtypes":
        types = db_get(f"guilds/{gid}/tickets/types") or {}
        if not types:
            return await interaction.response.send_message(embed=info_embed("Ticket Types", "No types configured."))
        lines = "\n".join(f"**{n}** — {v.get('desc','')}" for n, v in types.items())
        await interaction.response.send_message(embed=info_embed("Ticket Types", lines))

    elif action == "edittype":
        if not has_admin_perm(interaction.user):
            return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
        if not option:
            return await interaction.response.send_message(embed=err_embed("Missing Type Name"), ephemeral=True)
        db_set(f"guilds/{gid}/tickets/types/{option}/desc", value or "")
        await interaction.response.send_message(embed=ok_embed("Type Updated", f"Type `{option}` updated."))

    elif action == "deletetype":
        if not has_admin_perm(interaction.user):
            return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
        if not option:
            return await interaction.response.send_message(embed=err_embed("Missing Type Name"), ephemeral=True)
        db_delete(f"guilds/{gid}/tickets/types/{option}")
        await interaction.response.send_message(embed=ok_embed("Type Deleted", f"Type `{option}` deleted."))

    elif action == "config":
        if not has_admin_perm(interaction.user):
            return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
        if option and value:
            db_set(f"guilds/{gid}/tickets/config/{option}", value)
            return await interaction.response.send_message(embed=ok_embed("Config Updated", f"`{option}` = `{value}`"))
        config = db_get(f"guilds/{gid}/tickets/config") or {}
        lines = "\n".join(f"`{k}`: {v}" for k, v in config.items()) or "No config set. Use `/ticket config key value`"
        await interaction.response.send_message(embed=info_embed("Ticket Config", lines))

    else:
        await interaction.response.send_message(embed=err_embed("Unknown Action", f"Unknown action: `{action}`"), ephemeral=True)

# ═══════════════════════════════════════════════════════════
#   WELCOME & GOODBYE
# ═══════════════════════════════════════════════════════════

def build_welcome_msg(template: str, member: discord.Member) -> str:
    return (template
        .replace("{user}", member.mention)
        .replace("{username}", str(member))
        .replace("{server}", member.guild.name)
        .replace("{count}", str(member.guild.member_count))
    )

@tree.command(name="welcome", description="Configure welcome messages")
@app_commands.describe(
    action="setup / test / disable",
    channel="Channel for welcome messages",
    message="Welcome message template"
)
@app_commands.choices(action=[
    app_commands.Choice(name="setup", value="setup"),
    app_commands.Choice(name="test", value="test"),
    app_commands.Choice(name="disable", value="disable"),
])
async def welcome(interaction: discord.Interaction, action: str, channel: discord.TextChannel = None, message: str = None):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    gid = interaction.guild.id
    if action == "setup":
        if not channel:
            return await interaction.response.send_message(embed=err_embed("Missing Channel"), ephemeral=True)
        db_set(f"guilds/{gid}/welcome", {
            "channel": str(channel.id),
            "message": message or "Welcome to {server}, {user}! You are member #{count}.",
            "enabled": True
        })
        await interaction.response.send_message(embed=ok_embed("Welcome Setup", f"Welcome messages will be sent to {channel.mention}."))
    elif action == "test":
        config = db_get(f"guilds/{gid}/welcome") or {}
        if not config.get("enabled"):
            return await interaction.response.send_message(embed=err_embed("Not Configured"), ephemeral=True)
        ch = interaction.guild.get_channel(int(config.get("channel", 0)))
        if not ch:
            return await interaction.response.send_message(embed=err_embed("Channel Not Found"), ephemeral=True)
        msg = build_welcome_msg(config.get("message", "Welcome {user}!"), interaction.user)
        await ch.send(msg)
        await interaction.response.send_message(embed=ok_embed("Test Sent"), ephemeral=True)
    elif action == "disable":
        db_set(f"guilds/{gid}/welcome/enabled", False)
        await interaction.response.send_message(embed=ok_embed("Welcome Disabled"))

@tree.command(name="goodbye", description="Configure goodbye messages")
@app_commands.describe(
    action="setup / test / disable",
    channel="Channel for goodbye messages",
    message="Goodbye message template"
)
@app_commands.choices(action=[
    app_commands.Choice(name="setup", value="setup"),
    app_commands.Choice(name="test", value="test"),
    app_commands.Choice(name="disable", value="disable"),
])
async def goodbye(interaction: discord.Interaction, action: str, channel: discord.TextChannel = None, message: str = None):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    gid = interaction.guild.id
    if action == "setup":
        if not channel:
            return await interaction.response.send_message(embed=err_embed("Missing Channel"), ephemeral=True)
        db_set(f"guilds/{gid}/goodbye", {
            "channel": str(channel.id),
            "message": message or "Goodbye {username}! We'll miss you.",
            "enabled": True
        })
        await interaction.response.send_message(embed=ok_embed("Goodbye Setup", f"Goodbye messages set to {channel.mention}."))
    elif action == "test":
        config = db_get(f"guilds/{gid}/goodbye") or {}
        if not config.get("enabled"):
            return await interaction.response.send_message(embed=err_embed("Not Configured"), ephemeral=True)
        ch = interaction.guild.get_channel(int(config.get("channel", 0)))
        if not ch:
            return await interaction.response.send_message(embed=err_embed("Channel Not Found"), ephemeral=True)
        msg = build_welcome_msg(config.get("message", "Goodbye {username}!"), interaction.user)
        await ch.send(msg)
        await interaction.response.send_message(embed=ok_embed("Test Sent"), ephemeral=True)
    elif action == "disable":
        db_set(f"guilds/{gid}/goodbye/enabled", False)
        await interaction.response.send_message(embed=ok_embed("Goodbye Disabled"))

# ═══════════════════════════════════════════════════════════
#   DM SYSTEM
# ═══════════════════════════════════════════════════════════

@tree.command(name="dm", description="Send DMs to users")
@app_commands.describe(
    target="user / role / everyone",
    message="Message to send",
    user="Specific user (if target=user)",
    role="Role to DM (if target=role)"
)
@app_commands.choices(target=[
    app_commands.Choice(name="user", value="user"),
    app_commands.Choice(name="role", value="role"),
    app_commands.Choice(name="everyone", value="everyone"),
])
async def dm_cmd(interaction: discord.Interaction, target: str, message: str, user: discord.Member = None, role: discord.Role = None):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    gid = interaction.guild.id
    sent = 0
    failed = 0
    members = []
    if target == "user":
        if not user:
            return await interaction.followup.send(embed=err_embed("Missing User"), ephemeral=True)
        members = [user]
    elif target == "role":
        if not role:
            return await interaction.followup.send(embed=err_embed("Missing Role"), ephemeral=True)
        members = [m for m in interaction.guild.members if role in m.roles and not m.bot]
    elif target == "everyone":
        members = [m for m in interaction.guild.members if not m.bot]
    embed = discord.Embed(title=f"📩 Message from {interaction.guild.name}", description=message, color=BRAND_COLOR)
    for m in members:
        try:
            await m.send(embed=embed)
            sent += 1
            await asyncio.sleep(0.5)
        except Exception:
            failed += 1
    log_entry = {"by": str(interaction.user), "target": target, "sent": sent, "failed": failed, "time": datetime.datetime.now(datetime.UTC).isoformat()}
    db_push(f"guilds/{gid}/dmlogs", log_entry)
    await interaction.followup.send(embed=ok_embed("DMs Sent", f"Sent: {sent} | Failed: {failed}"), ephemeral=True)

@tree.command(name="dmlogs", description="View DM logs and history")
async def dmlogs(interaction: discord.Interaction):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    logs = db_get(f"guilds/{interaction.guild.id}/dmlogs") or {}
    if not logs:
        return await interaction.response.send_message(embed=info_embed("DM Logs", "No logs found."))
    lines = []
    for k, v in list(logs.items())[-10:]:
        lines.append(f"`{v.get('time','?')[:10]}` — **{v.get('target','?')}** by {v.get('by','?')} | Sent: {v.get('sent',0)}")
    await interaction.response.send_message(embed=info_embed("DM Logs (Last 10)", "\n".join(lines)))

# ═══════════════════════════════════════════════════════════
#   INVITE TRACKER
# ═══════════════════════════════════════════════════════════

invite_cache: dict = {}

@bot.event
async def on_invite_create(invite: discord.Invite):
    if invite.guild:
        gid = invite.guild.id
        if gid not in invite_cache:
            invite_cache[gid] = {}
        invite_cache[gid][invite.code] = invite.uses or 0

@bot.event
async def on_invite_delete(invite: discord.Invite):
    if invite.guild:
        gid = invite.guild.id
        if gid in invite_cache and invite.code in invite_cache[gid]:
            del invite_cache[gid][invite.code]

@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    gid = guild.id

    # Welcome message
    config = db_get(f"guilds/{gid}/welcome") or {}
    if config.get("enabled"):
        ch = guild.get_channel(int(config.get("channel", 0) or 0))
        if ch:
            msg = build_welcome_msg(config.get("message", "Welcome {user}!"), member)
            try:
                await ch.send(msg)
            except Exception:
                pass

    # Auto-role
    autorole_id = db_get(f"guilds/{gid}/autorole")
    if autorole_id:
        role = guild.get_role(int(autorole_id))
        if role:
            try:
                await member.add_roles(role, reason="Auto-role")
            except Exception:
                pass

    # Invite tracking
    try:
        current_invites = await guild.invites()
        cached = invite_cache.get(gid, {})
        for inv in current_invites:
            prev = cached.get(inv.code, 0)
            if inv.uses > prev:
                inviter_id = str(inv.inviter.id) if inv.inviter else "unknown"
                data = db_get(f"guilds/{gid}/invites/{inviter_id}") or {"uses": 0, "members": []}
                data["uses"] = int(data.get("uses", 0)) + 1
                data.setdefault("members", []).append(str(member.id))
                db_set(f"guilds/{gid}/invites/{inviter_id}", data)
                cached[inv.code] = inv.uses
                break
        invite_cache[gid] = {i.code: i.uses for i in current_invites}
    except Exception:
        pass

    # Sticky roles
    sticky = db_get(f"guilds/{gid}/stickyroles/enabled")
    if sticky:
        saved_roles = db_get(f"guilds/{gid}/stickyroles/users/{member.id}") or []
        for rid in saved_roles:
            role = guild.get_role(int(rid))
            if role:
                try:
                    await member.add_roles(role, reason="Sticky role restore")
                except Exception:
                    pass

@bot.event
async def on_member_remove(member: discord.Member):
    guild = member.guild
    gid = guild.id

    # Goodbye message
    config = db_get(f"guilds/{gid}/goodbye") or {}
    if config.get("enabled"):
        ch = guild.get_channel(int(config.get("channel", 0) or 0))
        if ch:
            msg = build_welcome_msg(config.get("message", "Goodbye {username}!"), member)
            try:
                await ch.send(msg)
            except Exception:
                pass

    # Save sticky roles
    sticky = db_get(f"guilds/{gid}/stickyroles/enabled")
    if sticky:
        role_ids = [str(r.id) for r in member.roles if r != guild.default_role]
        db_set(f"guilds/{gid}/stickyroles/users/{member.id}", role_ids)

@tree.command(name="invites", description="Check invite stats")
@app_commands.describe(user="User to check (default: yourself)")
async def invites(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    data = db_get(f"guilds/{interaction.guild.id}/invites/{target.id}") or {"uses": 0}
    await interaction.response.send_message(embed=info_embed(f"Invites for {target.display_name}", f"**Total Invites:** {data.get('uses', 0)}"))

@tree.command(name="inviteleaderboard", description="View top inviters")
async def inviteleaderboard(interaction: discord.Interaction):
    data = db_get(f"guilds/{interaction.guild.id}/invites") or {}
    if not data:
        return await interaction.response.send_message(embed=info_embed("Invite Leaderboard", "No invite data yet."))
    sorted_data = sorted(data.items(), key=lambda x: int(x[1].get("uses", 0)) if isinstance(x[1], dict) else 0, reverse=True)
    lines = []
    for i, (uid, v) in enumerate(sorted_data[:10], 1):
        count = v.get("uses", 0) if isinstance(v, dict) else 0
        lines.append(f"**#{i}** <@{uid}> — {count} invites")
    await interaction.response.send_message(embed=info_embed("🏆 Invite Leaderboard", "\n".join(lines) or "No data"))

@tree.command(name="resetinvites", description="Reset user invite count (Admin)")
@app_commands.describe(user="User to reset")
async def resetinvites(interaction: discord.Interaction, user: discord.Member):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    db_delete(f"guilds/{interaction.guild.id}/invites/{user.id}")
    await interaction.response.send_message(embed=ok_embed("Invites Reset", f"{user.mention}'s invites reset."))

@tree.command(name="give_invites", description="Give invites to a user (Admin only)")
@app_commands.describe(user="User to give invites", number="Number of invites")
async def give_invites(interaction: discord.Interaction, user: discord.Member, number: int):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    path = f"guilds/{interaction.guild.id}/invites/{user.id}"
    data = db_get(path) or {"uses": 0}
    data["uses"] = int(data.get("uses", 0)) + number
    db_set(path, data)
    await interaction.response.send_message(embed=ok_embed("Invites Given", f"Gave {number} invites to {user.mention}. Total: {data['uses']}"))

# ═══════════════════════════════════════════════════════════
#   UTILITY & TOOLS
# ═══════════════════════════════════════════════════════════

@tree.command(name="giveaway", description="Giveaway system")
@app_commands.describe(
    action="start / end / reroll",
    prize="Prize description (for start)",
    duration="Duration in minutes (for start)",
    winners="Number of winners (for start)",
    channel="Channel for giveaway"
)
@app_commands.choices(action=[
    app_commands.Choice(name="start", value="start"),
    app_commands.Choice(name="end", value="end"),
    app_commands.Choice(name="reroll", value="reroll"),
])
async def giveaway(interaction: discord.Interaction, action: str, prize: str = None, duration: int = None, winners: int = 1, channel: discord.TextChannel = None):
    if not has_mod_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    gid = interaction.guild.id
    if action == "start":
        if not prize or not duration:
            return await interaction.response.send_message(embed=err_embed("Missing Args", "Provide `prize` and `duration`."), ephemeral=True)
        ch = channel or interaction.channel
        end_time = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=duration)
        embed = discord.Embed(
            title="🎉 GIVEAWAY",
            description=f"**Prize:** {prize}\n**Winners:** {winners}\n**Ends:** <t:{int(end_time.timestamp())}:R>\n\nReact with 🎉 to enter!",
            color=0xF1C40F
        )
        embed.set_footer(text="Vantix Giveaways")
        msg = await ch.send(embed=embed)
        await msg.add_reaction("🎉")
        db_set(f"guilds/{gid}/giveaways/{msg.id}", {
            "prize": prize, "winners": winners,
            "end": end_time.isoformat(), "channel": str(ch.id),
            "active": True
        })
        await interaction.response.send_message(embed=ok_embed("Giveaway Started", f"In {ch.mention}"), ephemeral=True)

    elif action == "end":
        giveaways = db_get(f"guilds/{gid}/giveaways") or {}
        active = [(mid, g) for mid, g in giveaways.items() if g.get("active")]
        if not active:
            return await interaction.response.send_message(embed=err_embed("No Active Giveaways"))
        mid, ga = active[-1]
        ch = interaction.guild.get_channel(int(ga["channel"]))
        try:
            msg = await ch.fetch_message(int(mid))
            reaction = discord.utils.get(msg.reactions, emoji="🎉")
            users = [u async for u in reaction.users() if not u.bot]
            if not users:
                await ch.send(embed=err_embed("No Entries", "No one entered the giveaway."))
            else:
                ws = random.sample(users, min(int(ga["winners"]), len(users)))
                mentions = ", ".join(w.mention for w in ws)
                await ch.send(embed=ok_embed("🎉 Giveaway Ended!", f"**Prize:** {ga['prize']}\n**Winners:** {mentions}"))
        except Exception as e:
            await interaction.response.send_message(embed=err_embed("Error", str(e)), ephemeral=True)
            return
        db_set(f"guilds/{gid}/giveaways/{mid}/active", False)
        await interaction.response.send_message(embed=ok_embed("Giveaway Ended"), ephemeral=True)

    elif action == "reroll":
        giveaways = db_get(f"guilds/{gid}/giveaways") or {}
        ended = [(mid, g) for mid, g in giveaways.items() if not g.get("active")]
        if not ended:
            return await interaction.response.send_message(embed=err_embed("No Ended Giveaways"))
        mid, ga = ended[-1]
        ch = interaction.guild.get_channel(int(ga["channel"]))
        try:
            msg = await ch.fetch_message(int(mid))
            reaction = discord.utils.get(msg.reactions, emoji="🎉")
            users = [u async for u in reaction.users() if not u.bot]
            if not users:
                await ch.send(embed=err_embed("No Entries"))
            else:
                winner = random.choice(users)
                await ch.send(embed=ok_embed("🎉 Reroll!", f"New winner: {winner.mention} | Prize: {ga['prize']}"))
        except Exception as e:
            await interaction.response.send_message(embed=err_embed("Error", str(e)), ephemeral=True)
            return
        await interaction.response.send_message(embed=ok_embed("Rerolled"), ephemeral=True)

@tree.command(name="weather", description="Get weather information")
@app_commands.describe(city="City name")
async def weather(interaction: discord.Interaction, city: str):
    if not WEATHER_API_KEY:
        return await interaction.response.send_message(embed=err_embed("Not Configured", "WEATHER_API_KEY not set in .env"), ephemeral=True)
    await interaction.response.defer()
    url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return await interaction.followup.send(embed=err_embed("City Not Found", f"Could not find weather for `{city}`."))
            data = await resp.json()
    desc = data["weather"][0]["description"].title()
    temp = data["main"]["temp"]
    feels = data["main"]["feels_like"]
    humidity = data["main"]["humidity"]
    wind = data["wind"]["speed"]
    embed = discord.Embed(title=f"🌤️ Weather — {data['name']}, {data['sys']['country']}", color=BRAND_COLOR)
    embed.add_field(name="Condition", value=desc)
    embed.add_field(name="Temperature", value=f"{temp}°C (feels {feels}°C)")
    embed.add_field(name="Humidity", value=f"{humidity}%")
    embed.add_field(name="Wind Speed", value=f"{wind} m/s")
    embed.set_footer(text="Vantix Management V1")
    await interaction.followup.send(embed=embed)

@tree.command(name="qrcode", description="Generate a QR code")
@app_commands.describe(content="Content to encode in the QR code")
async def qrcode_cmd(interaction: discord.Interaction, content: str):
    await interaction.response.defer()
    img = qrcode.make(content)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    file = discord.File(buf, filename="qrcode.png")
    embed = discord.Embed(title="QR Code Generated", color=BRAND_COLOR)
    embed.set_image(url="attachment://qrcode.png")
    await interaction.followup.send(embed=embed, file=file)

_reminders: list = []

@tree.command(name="remindme", description="Set a reminder")
@app_commands.describe(minutes="Remind in how many minutes", reminder="What to remind you about")
async def remindme(interaction: discord.Interaction, minutes: int, reminder: str):
    await interaction.response.send_message(embed=ok_embed("Reminder Set", f"I'll remind you in {minutes} minute(s): **{reminder}**"))
    await asyncio.sleep(minutes * 60)
    try:
        await interaction.user.send(embed=info_embed("⏰ Reminder", reminder))
    except Exception:
        pass

@tree.command(name="poll", description="Create a poll")
@app_commands.describe(question="Poll question", option1="Option 1", option2="Option 2", option3="Option 3 (optional)", option4="Option 4 (optional)")
async def poll(interaction: discord.Interaction, question: str, option1: str, option2: str, option3: str = None, option4: str = None):
    options = [o for o in [option1, option2, option3, option4] if o]
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    desc = "\n".join(f"{emojis[i]} {opt}" for i, opt in enumerate(options))
    embed = discord.Embed(title=f"📊 {question}", description=desc, color=BRAND_COLOR)
    embed.set_footer(text=f"Poll by {interaction.user} | Vantix Management V1")
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    for i in range(len(options)):
        await msg.add_reaction(emojis[i])

@tree.command(name="afk", description="Set your AFK status")
@app_commands.describe(reason="AFK reason")
async def afk(interaction: discord.Interaction, reason: str = "AFK"):
    gid = interaction.guild.id
    db_set(f"guilds/{gid}/afk/{interaction.user.id}", {"reason": reason, "time": datetime.datetime.now(datetime.UTC).isoformat()})
    await interaction.response.send_message(embed=ok_embed("AFK Set", f"You are now AFK: **{reason}**"))

# ═══════════════════════════════════════════════════════════
#   INFORMATION COMMANDS
# ═══════════════════════════════════════════════════════════

@tree.command(name="serverinfo", description="View server information")
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=g.name, color=BRAND_COLOR)
    embed.set_thumbnail(url=g.icon.url if g.icon else None)
    embed.add_field(name="Owner", value=f"<@{g.owner_id}>")
    embed.add_field(name="Members", value=str(g.member_count))
    embed.add_field(name="Channels", value=str(len(g.channels)))
    embed.add_field(name="Roles", value=str(len(g.roles)))
    embed.add_field(name="Created", value=g.created_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Boosts", value=str(g.premium_subscription_count))
    embed.set_footer(text="Vantix Management V1")
    await interaction.response.send_message(embed=embed)

@tree.command(name="userinfo", description="View user information")
@app_commands.describe(user="User to inspect")
async def userinfo(interaction: discord.Interaction, user: discord.Member = None):
    u = user or interaction.user
    embed = discord.Embed(title=str(u), color=u.color)
    embed.set_thumbnail(url=u.display_avatar.url)
    embed.add_field(name="ID", value=str(u.id))
    embed.add_field(name="Joined Server", value=u.joined_at.strftime("%Y-%m-%d") if u.joined_at else "?")
    embed.add_field(name="Account Created", value=u.created_at.strftime("%Y-%m-%d"))
    roles = [r.mention for r in reversed(u.roles) if r != interaction.guild.default_role]
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles[:10]) or "None", inline=False)
    embed.set_footer(text="Vantix Management V1")
    await interaction.response.send_message(embed=embed)

@tree.command(name="roleinfo", description="View role information")
@app_commands.describe(role="Role to inspect")
async def roleinfo(interaction: discord.Interaction, role: discord.Role):
    embed = discord.Embed(title=f"Role: {role.name}", color=role.color)
    embed.add_field(name="ID", value=str(role.id))
    embed.add_field(name="Members", value=str(len(role.members)))
    embed.add_field(name="Color", value=str(role.color))
    embed.add_field(name="Mentionable", value=str(role.mentionable))
    embed.add_field(name="Hoisted", value=str(role.hoist))
    embed.add_field(name="Created", value=role.created_at.strftime("%Y-%m-%d"))
    embed.set_footer(text="Vantix Management V1")
    await interaction.response.send_message(embed=embed)

@tree.command(name="avatar", description="View user avatar")
@app_commands.describe(user="User to get avatar of")
async def avatar(interaction: discord.Interaction, user: discord.Member = None):
    u = user or interaction.user
    embed = discord.Embed(title=f"{u.display_name}'s Avatar", color=BRAND_COLOR)
    embed.set_image(url=u.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@tree.command(name="banner", description="View user banner")
@app_commands.describe(user="User to get banner of")
async def banner(interaction: discord.Interaction, user: discord.Member = None):
    u = user or interaction.user
    fetched = await bot.fetch_user(u.id)
    if not fetched.banner:
        return await interaction.response.send_message(embed=err_embed("No Banner", f"{u.mention} has no banner set."))
    embed = discord.Embed(title=f"{u.display_name}'s Banner", color=BRAND_COLOR)
    embed.set_image(url=fetched.banner.url)
    await interaction.response.send_message(embed=embed)

@tree.command(name="membercount", description="View member count")
async def membercount(interaction: discord.Interaction):
    g = interaction.guild
    humans = sum(1 for m in g.members if not m.bot)
    bots = sum(1 for m in g.members if m.bot)
    embed = info_embed("Member Count",
        f"**Total:** {g.member_count}\n**Humans:** {humans}\n**Bots:** {bots}"
    )
    await interaction.response.send_message(embed=embed)

@tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    lat = round(bot.latency * 1000)
    await interaction.response.send_message(embed=info_embed("🏓 Pong!", f"Latency: **{lat}ms**"))

@tree.command(name="stats", description="View bot statistics")
async def stats(interaction: discord.Interaction):
    guilds = len(bot.guilds)
    users = sum(g.member_count for g in bot.guilds)
    lat = round(bot.latency * 1000)
    embed = info_embed("Bot Statistics",
        f"**Servers:** {guilds}\n**Total Users:** {users}\n**Latency:** {lat}ms\n**Version:** Vantix V1"
    )
    await interaction.response.send_message(embed=embed)

@tree.command(name="help", description="Show the help menu")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="Vantix Management V1 — Help", color=BRAND_COLOR)
    embed.set_thumbnail(url=bot.user.display_avatar.url if bot.user else None)
    categories = {
        "👑 Bot Owner": "/superadmin, /botconfig, /extraowner",
        "🛡️ Security": "/antinuke, /antispam, /badwords",
        "🔨 Moderation": "/ban, /kick, /timeout, /warn, /warnings, /clearwarns, /purge, /lock, /unlock, /slowmode",
        "🎫 Tickets": "/ticket setup/panel/panels/close/claim/transcript/stats...",
        "👋 Welcome/Goodbye": "/welcome, /goodbye",
        "📩 DM System": "/dm, /dmlogs",
        "📨 Invites": "/invites, /inviteleaderboard, /resetinvites, /give_invites",
        "🛠️ Utility": "/giveaway, /weather, /qrcode, /remindme, /poll, /afk",
        "ℹ️ Info": "/serverinfo, /userinfo, /roleinfo, /avatar, /banner, /membercount, /ping, /stats",
        "⚙️ Server Mgmt": "/autorole, /stickyroles, /addrole, /removerole, /verifyconfig, /verify, /serverstats",
        "📢 Announcement": "/webhook",
        "📡 Status": "/status_setup, /monitor_add",
    }
    for cat, cmds in categories.items():
        embed.add_field(name=cat, value=cmds, inline=False)
    embed.set_footer(text="Vantix Management V1 | Use / to see commands")
    await interaction.response.send_message(embed=embed)

# ═══════════════════════════════════════════════════════════
#   SERVER MANAGEMENT
# ═══════════════════════════════════════════════════════════

@tree.command(name="autorole", description="Auto-assign roles to new members")
@app_commands.describe(action="set / remove", role="Role to auto-assign")
@app_commands.choices(action=[
    app_commands.Choice(name="set", value="set"),
    app_commands.Choice(name="remove", value="remove"),
])
async def autorole(interaction: discord.Interaction, action: str, role: discord.Role = None):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    gid = interaction.guild.id
    if action == "set":
        if not role:
            return await interaction.response.send_message(embed=err_embed("Missing Role"), ephemeral=True)
        db_set(f"guilds/{gid}/autorole", str(role.id))
        await interaction.response.send_message(embed=ok_embed("Auto-Role Set", f"New members will receive {role.mention}"))
    elif action == "remove":
        db_delete(f"guilds/{gid}/autorole")
        await interaction.response.send_message(embed=ok_embed("Auto-Role Removed"))

@tree.command(name="stickyroles", description="Restore roles on rejoin")
@app_commands.describe(action="enable / disable")
@app_commands.choices(action=[
    app_commands.Choice(name="enable", value="enable"),
    app_commands.Choice(name="disable", value="disable"),
])
async def stickyroles(interaction: discord.Interaction, action: str):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    enabled = action == "enable"
    db_set(f"guilds/{interaction.guild.id}/stickyroles/enabled", enabled)
    await interaction.response.send_message(embed=ok_embed(f"Sticky Roles {'Enabled' if enabled else 'Disabled'}"))

@tree.command(name="addrole", description="Add role to user")
@app_commands.describe(user="Target user", role="Role to add")
async def addrole(interaction: discord.Interaction, user: discord.Member, role: discord.Role):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    try:
        await user.add_roles(role)
        await interaction.response.send_message(embed=ok_embed("Role Added", f"{role.mention} added to {user.mention}"))
    except discord.Forbidden:
        await interaction.response.send_message(embed=err_embed("Failed", "Missing permissions."), ephemeral=True)

@tree.command(name="removerole", description="Remove role from user")
@app_commands.describe(user="Target user", role="Role to remove")
async def removerole(interaction: discord.Interaction, user: discord.Member, role: discord.Role):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    try:
        await user.remove_roles(role)
        await interaction.response.send_message(embed=ok_embed("Role Removed", f"{role.mention} removed from {user.mention}"))
    except discord.Forbidden:
        await interaction.response.send_message(embed=err_embed("Failed", "Missing permissions."), ephemeral=True)

@tree.command(name="verifyconfig", description="Setup verification system")
@app_commands.describe(role="Role to give on verify", channel="Verification channel")
async def verifyconfig(interaction: discord.Interaction, role: discord.Role, channel: discord.TextChannel = None):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    gid = interaction.guild.id
    ch = channel or interaction.channel
    db_set(f"guilds/{gid}/verify", {"role": str(role.id), "channel": str(ch.id), "enabled": True})
    embed = discord.Embed(title="✅ Verification", description="React with ✅ or click below to verify yourself.", color=0x57F287)
    view = discord.ui.View(timeout=None)
    btn = discord.ui.Button(label="Verify Me", style=discord.ButtonStyle.green, custom_id="verify_btn")
    view.add_item(btn)
    msg = await ch.send(embed=embed, view=view)
    db_set(f"guilds/{gid}/verify/msg_id", str(msg.id))
    await interaction.response.send_message(embed=ok_embed("Verification Setup", f"Verify panel sent to {ch.mention}."))

@tree.command(name="verify", description="Verify yourself")
async def verify(interaction: discord.Interaction):
    gid = interaction.guild.id
    config = db_get(f"guilds/{gid}/verify") or {}
    if not config.get("enabled"):
        return await interaction.response.send_message(embed=err_embed("Not Configured", "Verification not set up."), ephemeral=True)
    role = interaction.guild.get_role(int(config.get("role", 0) or 0))
    if not role:
        return await interaction.response.send_message(embed=err_embed("Role Not Found"), ephemeral=True)
    try:
        await interaction.user.add_roles(role, reason="Verification")
        await interaction.response.send_message(embed=ok_embed("Verified!", f"You've been given {role.mention}"), ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(embed=err_embed("Failed", "I can't assign the role."), ephemeral=True)

@tree.command(name="serverstats", description="Server statistics channels")
@app_commands.describe(action="setup / remove")
@app_commands.choices(action=[
    app_commands.Choice(name="setup", value="setup"),
    app_commands.Choice(name="remove", value="remove"),
])
async def serverstats(interaction: discord.Interaction, action: str):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    gid = interaction.guild.id
    guild = interaction.guild
    if action == "setup":
        cat = await guild.create_category("📊 Server Stats")
        members_ch = await guild.create_voice_channel(f"👥 Members: {guild.member_count}", category=cat)
        bots_ch = await guild.create_voice_channel(f"🤖 Bots: {sum(1 for m in guild.members if m.bot)}", category=cat)
        for ch in [members_ch, bots_ch]:
            await ch.set_permissions(guild.default_role, connect=False)
        db_set(f"guilds/{gid}/serverstats", {
            "enabled": True,
            "members_ch": str(members_ch.id),
            "bots_ch": str(bots_ch.id)
        })
        await interaction.response.send_message(embed=ok_embed("Server Stats Setup", "Stats channels created."))
    elif action == "remove":
        config = db_get(f"guilds/{gid}/serverstats") or {}
        for key in ["members_ch", "bots_ch"]:
            ch_id = config.get(key)
            if ch_id:
                ch = guild.get_channel(int(ch_id))
                if ch:
                    try:
                        await ch.delete()
                    except Exception:
                        pass
        db_delete(f"guilds/{gid}/serverstats")
        await interaction.response.send_message(embed=ok_embed("Server Stats Removed"))

# ═══════════════════════════════════════════════════════════
#   ANNOUNCEMENT — WEBHOOK
# ═══════════════════════════════════════════════════════════

@tree.command(name="webhook", description="Send announcement via webhook")
@app_commands.describe(
    title="Announcement title",
    message="Announcement message",
    channel="Target channel",
    embed="Send as embed? yes/no",
    color="Hex color (e.g. ff0000)"
)
async def webhook_cmd(interaction: discord.Interaction, title: str, message: str, channel: discord.TextChannel = None, embed: str = "yes", color: str = "5865F2"):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    ch = channel or interaction.channel
    webhooks = await ch.webhooks()
    hook = None
    for wh in webhooks:
        if wh.name == "Vantix Announcements":
            hook = wh
            break
    if not hook:
        hook = await ch.create_webhook(name="Vantix Announcements")
    try:
        color_int = int(color.lstrip("#"), 16)
    except Exception:
        color_int = BRAND_COLOR
    if embed.lower() == "yes":
        e = discord.Embed(title=title, description=message, color=color_int)
        e.set_footer(text="Vantix Announcements")
        await hook.send(embed=e, username="Vantix Announcements", avatar_url=bot.user.display_avatar.url if bot.user else None)
    else:
        await hook.send(f"**{title}**\n{message}", username="Vantix Announcements", avatar_url=bot.user.display_avatar.url if bot.user else None)
    await interaction.response.send_message(embed=ok_embed("Announcement Sent", f"Posted to {ch.mention}"), ephemeral=True)

# ═══════════════════════════════════════════════════════════
#   STATUS MONITOR
# ═══════════════════════════════════════════════════════════

_status_messages: dict = {}

@tree.command(name="status_setup", description="Set up live status channel (run in target channel)")
async def status_setup(interaction: discord.Interaction):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    gid = interaction.guild.id
    embed = discord.Embed(title="📡 Service Status", description="Loading status...", color=BRAND_COLOR)
    msg = await interaction.channel.send(embed=embed)
    db_set(f"guilds/{gid}/statusmonitor/channel", str(interaction.channel.id))
    db_set(f"guilds/{gid}/statusmonitor/message", str(msg.id))
    _status_messages[gid] = {"channel": interaction.channel.id, "message": msg.id}
    await interaction.response.send_message(embed=ok_embed("Status Setup", "Status monitor configured. Add services with `/monitor_add`."), ephemeral=True)

@tree.command(name="monitor_add", description="Add a service to monitor")
@app_commands.describe(
    name="Service name",
    protocol="tcp or http",
    address="IP or hostname",
    port="Port number (for TCP)"
)
@app_commands.choices(protocol=[
    app_commands.Choice(name="tcp", value="tcp"),
    app_commands.Choice(name="http", value="http"),
])
async def monitor_add(interaction: discord.Interaction, name: str, protocol: str, address: str, port: int = 80):
    if not has_admin_perm(interaction.user):
        return await interaction.response.send_message(embed=err_embed("No Permission"), ephemeral=True)
    gid = interaction.guild.id
    services = db_get(f"guilds/{gid}/statusmonitor/services") or {}
    services[name] = {"protocol": protocol, "address": address, "port": port}
    db_set(f"guilds/{gid}/statusmonitor/services", services)
    await interaction.response.send_message(embed=ok_embed("Service Added", f"`{name}` ({protocol}://{address}:{port}) added to monitor."))

async def check_tcp(address: str, port: int) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((address, port))
        sock.close()
        return result == 0
    except Exception:
        return False

async def check_http(address: str) -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(address, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                return resp.status < 500
    except Exception:
        return False

@tasks.loop(seconds=60)
async def update_status():
    for guild in bot.guilds:
        gid = guild.id
        config = db_get(f"guilds/{gid}/statusmonitor") or {}
        if not config.get("channel") or not config.get("message"):
            continue
        services = config.get("services") or {}
        if not services:
            continue
        ch = guild.get_channel(int(config["channel"]))
        if not ch:
            continue
        lines = []
        for name, info in services.items():
            proto = info.get("protocol", "tcp")
            addr = info.get("address", "")
            port = int(info.get("port", 80))
            if proto == "tcp":
                up = await check_tcp(addr, port)
            else:
                url = addr if addr.startswith("http") else f"http://{addr}"
                up = await check_http(url)
            status_icon = "🟢 Online" if up else "🔴 Offline"
            lines.append(f"**{name}** — {status_icon}")
        embed = discord.Embed(
            title="📡 Service Status",
            description="\n".join(lines),
            color=0x57F287 if all("🟢" in l for l in lines) else 0xED4245,
            timestamp=datetime.datetime.now(datetime.UTC)
        )
        embed.set_footer(text="Last updated")
        try:
            msg = await ch.fetch_message(int(config["message"]))
            await msg.edit(embed=embed)
        except Exception:
            try:
                new_msg = await ch.send(embed=embed)
                db_set(f"guilds/{gid}/statusmonitor/message", str(new_msg.id))
            except Exception:
                pass

# ═══════════════════════════════════════════════════════════
#   MESSAGE EVENTS (badwords, spam, afk, antinuke)
# ═══════════════════════════════════════════════════════════

_spam_tracker: dict = {}

@bot.event
async def on_message(message: discord.Message):
    if not message.guild or message.author.bot:
        return await bot.process_commands(message)

    gid = message.guild.id
    uid = message.author.id

    # AFK check — mention someone who is AFK
    for mention in message.mentions:
        afk_data = db_get(f"guilds/{gid}/afk/{mention.id}")
        if afk_data:
            await message.channel.send(
                embed=info_embed(f"{mention.display_name} is AFK",
                    f"**Reason:** {afk_data.get('reason','AFK')}\n**Since:** {afk_data.get('time','?')[:10]}")
            )

    # Remove AFK from sender if they send a message
    afk_self = db_get(f"guilds/{gid}/afk/{uid}")
    if afk_self:
        db_delete(f"guilds/{gid}/afk/{uid}")
        try:
            await message.channel.send(embed=ok_embed("AFK Removed", f"Welcome back {message.author.mention}!"), delete_after=5)
        except Exception:
            pass

    # Bad words filter
    bw_enabled = db_get(f"guilds/{gid}/badwords_enabled")
    words = db_get(f"guilds/{gid}/badwords") or []
    if words:
        content_lower = message.content.lower()
        for w in words:
            if w in content_lower:
                try:
                    await message.delete()
                    await message.channel.send(
                        f"{message.author.mention}, that word is not allowed here! ⚠️",
                        delete_after=5
                    )
                except Exception:
                    pass
                return

    # Anti-spam
    spam_config = db_get(f"guilds/{gid}/antispam") or {}
    if spam_config.get("enabled"):
        limit = int(spam_config.get("limit", 5))
        interval = int(spam_config.get("interval", 5))
        now = time.time()
        key = f"{gid}:{uid}"
        if key not in _spam_tracker:
            _spam_tracker[key] = []
        _spam_tracker[key] = [t for t in _spam_tracker[key] if now - t < interval]
        _spam_tracker[key].append(now)
        if len(_spam_tracker[key]) >= limit:
            try:
                await message.author.timeout(discord.utils.utcnow() + datetime.timedelta(minutes=1), reason="Anti-spam")
                await message.channel.send(f"{message.author.mention} has been timed out for spamming.", delete_after=5)
                _spam_tracker[key] = []
            except Exception:
                pass

    await bot.process_commands(message)

# ═══════════════════════════════════════════════════════════
#   ANTI-NUKE EVENTS
# ═══════════════════════════════════════════════════════════

_action_tracker: dict = {}

async def antinuke_check(guild: discord.Guild, user: discord.Member | None, event: str):
    if not user or user.bot:
        return
    gid = guild.id
    config = db_get(f"guilds/{gid}/antinuke") or {}
    if not config.get("enabled"):
        return
    wl = config.get("whitelist") or {}
    if str(user.id) in wl or is_extra_owner(guild, user):
        return
    key = f"{gid}:{user.id}:{event}"
    now = time.time()
    if key not in _action_tracker:
        _action_tracker[key] = []
    _action_tracker[key] = [t for t in _action_tracker[key] if now - t < 10]
    _action_tracker[key].append(now)
    threshold = int(config.get("threshold", 3))
    if len(_action_tracker[key]) >= threshold:
        log_entry = {"event": event, "user": str(user.id), "time": datetime.datetime.now(datetime.UTC).isoformat()}
        db_push(f"guilds/{gid}/antinuke/logs", log_entry)
        try:
            await user.ban(reason=f"Anti-Nuke: {event} triggered")
        except Exception:
            pass

@bot.event
async def on_guild_channel_delete(channel):
    try:
        async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
            await antinuke_check(channel.guild, entry.user, "channel_delete")
    except Exception:
        pass

@bot.event
async def on_guild_role_delete(role):
    try:
        async for entry in role.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
            await antinuke_check(role.guild, entry.user, "role_delete")
    except Exception:
        pass

@bot.event
async def on_member_ban(guild, user):
    try:
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
            await antinuke_check(guild, entry.user, "ban")
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════
#   VERIFY BUTTON INTERACTION
# ═══════════════════════════════════════════════════════════

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        cid = interaction.data.get("custom_id", "")
        if cid == "verify_btn":
            gid = interaction.guild.id
            config = db_get(f"guilds/{gid}/verify") or {}
            role = interaction.guild.get_role(int(config.get("role", 0) or 0))
            if role:
                try:
                    await interaction.user.add_roles(role, reason="Verification button")
                    await interaction.response.send_message(embed=ok_embed("Verified!", f"You received {role.mention}"), ephemeral=True)
                except Exception:
                    await interaction.response.send_message(embed=err_embed("Failed", "Could not assign role."), ephemeral=True)
            else:
                await interaction.response.send_message(embed=err_embed("Role Not Found"), ephemeral=True)

# ═══════════════════════════════════════════════════════════
#   SERVERSTATS AUTO-UPDATE
# ═══════════════════════════════════════════════════════════

@tasks.loop(minutes=10)
async def update_server_stats():
    for guild in bot.guilds:
        gid = guild.id
        config = db_get(f"guilds/{gid}/serverstats") or {}
        if not config.get("enabled"):
            continue
        members_ch = guild.get_channel(int(config.get("members_ch", 0) or 0))
        bots_ch = guild.get_channel(int(config.get("bots_ch", 0) or 0))
        humans = sum(1 for m in guild.members if not m.bot)
        bots_count = sum(1 for m in guild.members if m.bot)
        try:
            if members_ch:
                await members_ch.edit(name=f"👥 Members: {humans}")
            if bots_ch:
                await bots_ch.edit(name=f"🤖 Bots: {bots_count}")
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════
#   BOT READY
# ═══════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"[Vantix] Logged in as {bot.user} ({bot.user.id})")
    try:
        synced = await tree.sync()
        print(f"[Vantix] Synced {len(synced)} commands globally.")
    except Exception as e:
        print(f"[Vantix] Sync error: {e}")

    # Load invite cache
    for guild in bot.guilds:
        try:
            invs = await guild.invites()
            invite_cache[guild.id] = {i.code: i.uses for i in invs}
        except Exception:
            pass

    # Re-register persistent views
    bot.add_view(TicketPanelView())
    bot.add_view(TicketCloseView())

    update_status.start()
    update_server_stats.start()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="over your server | Vantix V1"))
    print("[Vantix] Ready!")

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise ValueError("DISCORD_TOKEN is not set in your .env file.")
    bot.run(token)
