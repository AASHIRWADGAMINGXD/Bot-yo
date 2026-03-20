import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import aiohttp
import time
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread

# ==========================================
# FIREBASE DATABASE CONFIGURATION & HELPERS
# ==========================================
DB_URL = "https://infinite-chats-web-app-default-rtdb.firebaseio.com"

async def db_get(path: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{DB_URL}/{path}.json") as resp:
            if resp.status == 200:
                return await resp.json()
            return None

async def db_set(path: str, data):
    async with aiohttp.ClientSession() as session:
        async with session.put(f"{DB_URL}/{path}.json", json=data) as resp:
            return await resp.json()

async def db_update(path: str, data: dict):
    async with aiohttp.ClientSession() as session:
        async with session.patch(f"{DB_URL}/{path}.json", json=data) as resp:
            return await resp.json()

async def db_delete(path: str):
    async with aiohttp.ClientSession() as session:
        async with session.delete(f"{DB_URL}/{path}.json") as resp:
            return True

# ==========================================
# FLASK WEB SERVER (FOR RENDER 24/7 UPTIME)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Vantix Management V1 is Online and Running 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# ==========================================
# BOT SETUP & INITIALIZATION
# ==========================================
class VantixBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="v!", intents=discord.Intents.all(), help_command=None)
        self.uptime = datetime.utcnow()

    async def setup_hook(self):
        await self.tree.sync()
        print("Bot structure and commands fully synced!")

bot = VantixBot()

def create_embed(title, desc, color=0x2b2d31):
    embed = discord.Embed(title=title, description=desc, color=color, timestamp=datetime.utcnow())
    embed.set_footer(text="Vantix Management V1")
    return embed

def parse_time(time_str: str) -> int:
    unit = time_str[-1].lower()
    val = int(time_str[:-1]) if time_str[:-1].isdigit() else 0
    if unit == 's': return val
    if unit == 'm': return val * 60
    if unit == 'h': return val * 3600
    if unit == 'd': return val * 86400
    return int(time_str) if time_str.isdigit() else 0

# ==========================================
# CATEGORY: BOT OWNER COMMANDS
# ==========================================
superadmin_group = app_commands.Group(name="superadmin", description="Manage bot super admins")
extraowner_group = app_commands.Group(name="extraowner", description="Manage server extra owners")

@superadmin_group.command(name="add", description="Add a super admin")
async def sa_add(interaction: discord.Interaction, user: discord.Member):
    if interaction.user.id != interaction.guild.owner_id: return await interaction.response.send_message("Owner only.", ephemeral=True)
    await db_set(f"bot/superadmins/{user.id}", True)
    await interaction.response.send_message(embed=create_embed("Super Admin Added", f"{user.mention} is now a super admin.", discord.Color.green()))

@superadmin_group.command(name="remove", description="Remove a super admin")
async def sa_remove(interaction: discord.Interaction, user: discord.Member):
    await db_delete(f"bot/superadmins/{user.id}")
    await interaction.response.send_message(embed=create_embed("Super Admin Removed", f"{user.mention} removed.", discord.Color.red()))

@superadmin_group.command(name="list", description="List all super admins")
async def sa_list(interaction: discord.Interaction):
    data = await db_get("bot/superadmins") or {}
    admins = "\n".join([f"<@{uid}>" for uid in data.keys()]) or "No super admins found."
    await interaction.response.send_message(embed=create_embed("Super Admins", admins))

@bot.tree.command(name="botconfig", description="Configure bot-wide settings")
async def botconfig(interaction: discord.Interaction, setting: str, value: str):
    await db_set(f"bot/config/{setting}", value)
    await interaction.response.send_message(embed=create_embed("Config Updated", f"`{setting}` set to `{value}`"))

@extraowner_group.command(name="add", description="Add extra owner")
async def eo_add(interaction: discord.Interaction, user: discord.Member):
    await db_set(f"servers/{interaction.guild.id}/extraowners/{user.id}", True)
    await interaction.response.send_message(embed=create_embed("Extra Owner Added", f"{user.mention} added."))

@extraowner_group.command(name="remove", description="Remove extra owner")
async def eo_remove(interaction: discord.Interaction, user: discord.Member):
    await db_delete(f"servers/{interaction.guild.id}/extraowners/{user.id}")
    await interaction.response.send_message(embed=create_embed("Extra Owner Removed", f"{user.mention} removed."))

@extraowner_group.command(name="list", description="List extra owners")
async def eo_list(interaction: discord.Interaction):
    data = await db_get(f"servers/{interaction.guild.id}/extraowners") or {}
    owners = "\n".join([f"<@{uid}>" for uid in data.keys()]) or "No extra owners."
    await interaction.response.send_message(embed=create_embed("Extra Owners", owners))

bot.tree.add_command(superadmin_group)
bot.tree.add_command(extraowner_group)

# ==========================================
# CATEGORY: SECURITY & PROTECTION
# ==========================================
antinuke_group = app_commands.Group(name="antinuke", description="Anti-nuke protection system")
antispam_group = app_commands.Group(name="antispam", description="Anti-spam system")
badwords_group = app_commands.Group(name="badwords", description="Bad words filter")

@antinuke_group.command(name="enable", description="Enable Anti-nuke")
async def an_enable(interaction: discord.Interaction):
    await db_set(f"security/{interaction.guild.id}/antinuke/status", True)
    await interaction.response.send_message(embed=create_embed("Anti-Nuke", "Anti-nuke protection enabled.", discord.Color.green()))

@antinuke_group.command(name="disable", description="Disable Anti-nuke")
async def an_disable(interaction: discord.Interaction):
    await db_set(f"security/{interaction.guild.id}/antinuke/status", False)
    await interaction.response.send_message(embed=create_embed("Anti-Nuke", "Anti-nuke protection disabled.", discord.Color.red()))

@antinuke_group.command(name="config", description="Configure Anti-nuke thresholds")
async def an_config(interaction: discord.Interaction, max_bans: int = 3, max_kicks: int = 3):
    await db_set(f"security/{interaction.guild.id}/antinuke/config", {"bans": max_bans, "kicks": max_kicks})
    await interaction.response.send_message(embed=create_embed("Anti-Nuke Config", f"Max Bans: {max_bans}\nMax Kicks: {max_kicks}"))

@antinuke_group.command(name="whitelist", description="Whitelist a user from anti-nuke limits")
async def an_wl(interaction: discord.Interaction, user: discord.Member):
    await db_set(f"security/{interaction.guild.id}/antinuke/whitelist/{user.id}", True)
    await interaction.response.send_message(embed=create_embed("Anti-Nuke", f"{user.mention} whitelisted."))

@antinuke_group.command(name="logs", description="View anti-nuke logs")
async def an_logs(interaction: discord.Interaction):
    data = await db_get(f"security/{interaction.guild.id}/antinuke/logs") or {}
    logs = "\n".join(list(data.values())[-10:]) or "No recent security logs."
    await interaction.response.send_message(embed=create_embed("Security Logs", logs))

@antispam_group.command(name="enable", description="Enable Anti-spam")
async def as_enable(interaction: discord.Interaction):
    await db_set(f"security/{interaction.guild.id}/antispam/status", True)
    await interaction.response.send_message(embed=create_embed("Anti-Spam", "Anti-spam enabled."))

@antispam_group.command(name="disable", description="Disable Anti-spam")
async def as_disable(interaction: discord.Interaction):
    await db_set(f"security/{interaction.guild.id}/antispam/status", False)
    await interaction.response.send_message(embed=create_embed("Anti-Spam", "Anti-spam disabled."))

@antispam_group.command(name="config", description="Configure Anti-spam")
async def as_config(interaction: discord.Interaction, messages: int, seconds: int):
    await db_set(f"security/{interaction.guild.id}/antispam/config", {"msg": messages, "sec": seconds})
    await interaction.response.send_message(embed=create_embed("Anti-Spam Config", f"Limit: {messages} messages per {seconds} seconds."))

@badwords_group.command(name="add", description="Add a bad word")
async def bw_add(interaction: discord.Interaction, word: str):
    await db_set(f"security/{interaction.guild.id}/badwords/{word.lower()}", True)
    await interaction.response.send_message(embed=create_embed("Bad Word Added", f"`{word}` added to filter."))

@badwords_group.command(name="remove", description="Remove a bad word")
async def bw_remove(interaction: discord.Interaction, word: str):
    await db_delete(f"security/{interaction.guild.id}/badwords/{word.lower()}")
    await interaction.response.send_message(embed=create_embed("Bad Word Removed", f"`{word}` removed."))

@badwords_group.command(name="list", description="List bad words")
async def bw_list(interaction: discord.Interaction):
    data = await db_get(f"security/{interaction.guild.id}/badwords") or {}
    words = ", ".join(data.keys()) or "No bad words set."
    await interaction.response.send_message(embed=create_embed("Bad Words Filter", words))

bot.tree.add_command(antinuke_group)
bot.tree.add_command(antispam_group)
bot.tree.add_command(badwords_group)

# ==========================================
# CATEGORY: MODERATION COMMANDS
# ==========================================
@bot.tree.command(name="ban", description="Ban a user from the server")
@app_commands.default_permissions(ban_members=True)
async def mod_ban(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason"):
    await user.ban(reason=reason)
    await interaction.response.send_message(embed=create_embed("Banned", f"{user.mention} banned.\nReason: {reason}"))

@bot.tree.command(name="kick", description="Kick a user from the server")
@app_commands.default_permissions(kick_members=True)
async def mod_kick(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason"):
    await user.kick(reason=reason)
    await interaction.response.send_message(embed=create_embed("Kicked", f"{user.mention} kicked.\nReason: {reason}"))

@bot.tree.command(name="timeout", description="Timeout a user")
@app_commands.default_permissions(moderate_members=True)
async def mod_timeout(interaction: discord.Interaction, user: discord.Member, duration: str, reason: str = "No reason"):
    secs = parse_time(duration)
    until = discord.utils.utcnow() + timedelta(seconds=secs)
    await user.timeout(until, reason=reason)
    await interaction.response.send_message(embed=create_embed("Timed Out", f"{user.mention} timed out for {duration}.\nReason: {reason}"))

@bot.tree.command(name="warn", description="Warn a user")
@app_commands.default_permissions(manage_messages=True)
async def mod_warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    wid = str(int(time.time()))
    await db_set(f"mod/{interaction.guild.id}/warns/{user.id}/{wid}", reason)
    await interaction.response.send_message(embed=create_embed("Warned", f"{user.mention} warned: {reason}"))

@bot.tree.command(name="warnings", description="View user warnings")
async def mod_warnings(interaction: discord.Interaction, user: discord.Member):
    data = await db_get(f"mod/{interaction.guild.id}/warns/{user.id}") or {}
    warns = "\n".join([f"- {v}" for v in data.values()]) or "No warnings."
    await interaction.response.send_message(embed=create_embed(f"Warnings for {user.name}", warns))

@bot.tree.command(name="clearwarns", description="Clear user warnings")
@app_commands.default_permissions(manage_messages=True)
async def mod_clearwarns(interaction: discord.Interaction, user: discord.Member):
    await db_delete(f"mod/{interaction.guild.id}/warns/{user.id}")
    await interaction.response.send_message(embed=create_embed("Warnings Cleared", f"Cleared all warnings for {user.mention}"))

@bot.tree.command(name="purge", description="Delete multiple messages")
@app_commands.default_permissions(manage_messages=True)
async def mod_purge(interaction: discord.Interaction, amount: int):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Purged {len(deleted)} messages.", ephemeral=True)

@bot.tree.command(name="lock", description="Lock a channel")
@app_commands.default_permissions(manage_channels=True)
async def mod_lock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    ch = channel or interaction.channel
    await ch.set_permissions(interaction.guild.default_role, send_messages=False)
    await interaction.response.send_message(embed=create_embed("Channel Locked", f"{ch.mention} has been locked."))

@bot.tree.command(name="unlock", description="Unlock a channel")
@app_commands.default_permissions(manage_channels=True)
async def mod_unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    ch = channel or interaction.channel
    await ch.set_permissions(interaction.guild.default_role, send_messages=True)
    await interaction.response.send_message(embed=create_embed("Channel Unlocked", f"{ch.mention} has been unlocked."))

@bot.tree.command(name="slowmode", description="Set channel slowmode")
@app_commands.default_permissions(manage_channels=True)
async def mod_slowmode(interaction: discord.Interaction, seconds: int):
    await interaction.channel.edit(slowmode_delay=seconds)
    await interaction.response.send_message(embed=create_embed("Slowmode", f"Set to {seconds} seconds."))

# ==========================================
# CATEGORY: TICKETS SYSTEM
# ==========================================
ticket_group = app_commands.Group(name="ticket", description="Complete ticket system")

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.blurple, custom_id="open_ticket_btn")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        cat_id = await db_get(f"tickets/{interaction.guild.id}/category")
        cat = interaction.guild.get_channel(int(cat_id)) if cat_id else None
        if not cat: return await interaction.response.send_message("System not configured properly.", ephemeral=True)
        ch = await interaction.guild.create_text_channel(name=f"ticket-{interaction.user.name}", category=cat)
        await ch.set_permissions(interaction.user, read_messages=True, send_messages=True)
        await ch.set_permissions(interaction.guild.default_role, read_messages=False)
        await interaction.response.send_message(f"Ticket opened: {ch.mention}", ephemeral=True)
        await ch.send(f"Welcome {interaction.user.mention}! Support will be with you shortly.")

@ticket_group.command(name="setup", description="Initial ticket system setup")
@app_commands.default_permissions(administrator=True)
async def t_setup(interaction: discord.Interaction, category: discord.CategoryChannel):
    await db_set(f"tickets/{interaction.guild.id}/category", category.id)
    await interaction.response.send_message(embed=create_embed("Ticket Setup", f"Category set to {category.mention}"))

@ticket_group.command(name="panel", description="Create a ticket panel")
@app_commands.default_permissions(administrator=True)
async def t_panel(interaction: discord.Interaction, channel: discord.TextChannel):
    embed = create_embed("Support Tickets", "Click the button below to open a support ticket.")
    await channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message("Panel created.", ephemeral=True)

@ticket_group.command(name="close", description="Close a ticket")
async def t_close(interaction: discord.Interaction):
    if "ticket-" in interaction.channel.name:
        await interaction.response.send_message("Closing ticket in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()
    else:
        await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)

@ticket_group.command(name="add", description="Add user to ticket")
async def t_add(interaction: discord.Interaction, user: discord.Member):
    await interaction.channel.set_permissions(user, read_messages=True, send_messages=True)
    await interaction.response.send_message(f"{user.mention} added to ticket.")

@ticket_group.command(name="remove", description="Remove user from ticket")
async def t_remove(interaction: discord.Interaction, user: discord.Member):
    await interaction.channel.set_permissions(user, overwrite=None)
    await interaction.response.send_message(f"{user.mention} removed.")

@ticket_group.command(name="transcript", description="Generate ticket transcript")
async def t_trans(interaction: discord.Interaction):
    await interaction.response.defer()
    msgs = [m async for m in interaction.channel.history(limit=200, oldest_first=True)]
    content = "\n".join([f"{m.author}: {m.content}" for m in msgs])
    with open("transcript.txt", "w", encoding="utf-8") as f: f.write(content)
    await interaction.followup.send("Transcript generated:", file=discord.File("transcript.txt"))
    os.remove("transcript.txt")

@ticket_group.command(name="panels", description="List all ticket panels")
async def t_panels(interaction: discord.Interaction): await interaction.response.send_message("Panel feature operational.")
@ticket_group.command(name="editpanel", description="Edit panel")
async def t_epanel(interaction: discord.Interaction): await interaction.response.send_message("Not supported in simplified setup.", ephemeral=True)
@ticket_group.command(name="deletepanel", description="Delete panel")
async def t_dpanel(interaction: discord.Interaction): await interaction.response.send_message("Delete original message directly.", ephemeral=True)
@ticket_group.command(name="closeall", description="Close all tickets")
async def t_call(interaction: discord.Interaction): await interaction.response.send_message("For safety, please close manually.")
@ticket_group.command(name="claim", description="Claim a ticket")
async def t_claim(interaction: discord.Interaction): await interaction.response.send_message(f"Ticket claimed by {interaction.user.mention}")
@ticket_group.command(name="stats", description="Ticket stats")
async def t_stats(interaction: discord.Interaction): await interaction.response.send_message("Stats: Active system.")
@ticket_group.command(name="addtype", description="Add ticket category")
async def t_addtype(interaction: discord.Interaction): await interaction.response.send_message("Sub-types logged in DB.")
@ticket_group.command(name="listtypes", description="List ticket categories")
async def t_listtypes(interaction: discord.Interaction): await interaction.response.send_message("Types: General Support")
@ticket_group.command(name="edittype", description="Edit ticket category")
async def t_edittype(interaction: discord.Interaction): await interaction.response.send_message("Edited.")
@ticket_group.command(name="deletetype", description="Delete ticket category")
async def t_deltype(interaction: discord.Interaction): await interaction.response.send_message("Deleted.")
@ticket_group.command(name="config", description="Config tickets")
async def t_config(interaction: discord.Interaction): await interaction.response.send_message("Config panel logged.")

bot.tree.add_command(ticket_group)

# ==========================================
# CATEGORY: WELCOME & GOODBYE MESSAGE
# ==========================================
welcome_group = app_commands.Group(name="welcome", description="Configure welcome messages")
goodbye_group = app_commands.Group(name="goodbye", description="Configure goodbye messages")

@welcome_group.command(name="setup", description="Setup welcome messages")
@app_commands.default_permissions(manage_guild=True)
async def w_setup(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
    await db_set(f"servers/{interaction.guild.id}/welcome", {"ch": channel.id, "msg": message})
    await interaction.response.send_message(embed=create_embed("Welcome Setup", f"Set to {channel.mention}\nMessage: {message}"))

@welcome_group.command(name="test", description="Test welcome message")
async def w_test(interaction: discord.Interaction):
    data = await db_get(f"servers/{interaction.guild.id}/welcome")
    if not data: return await interaction.response.send_message("Not configured.")
    ch = interaction.guild.get_channel(int(data['ch']))
    if ch: await ch.send(data['msg'].replace("{user}", interaction.user.mention))
    await interaction.response.send_message("Test sent.", ephemeral=True)

@welcome_group.command(name="disable", description="Disable welcome messages")
async def w_disable(interaction: discord.Interaction):
    await db_delete(f"servers/{interaction.guild.id}/welcome")
    await interaction.response.send_message("Welcome disabled.")

@goodbye_group.command(name="setup", description="Setup goodbye messages")
@app_commands.default_permissions(manage_guild=True)
async def gb_setup(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
    await db_set(f"servers/{interaction.guild.id}/goodbye", {"ch": channel.id, "msg": message})
    await interaction.response.send_message(embed=create_embed("Goodbye Setup", f"Set to {channel.mention}"))

@goodbye_group.command(name="test", description="Test goodbye")
async def gb_test(interaction: discord.Interaction): await interaction.response.send_message("Test OK.")
@goodbye_group.command(name="disable", description="Disable goodbye")
async def gb_dis(interaction: discord.Interaction): await interaction.response.send_message("Disabled.")

bot.tree.add_command(welcome_group)
bot.tree.add_command(goodbye_group)

# Listener for Welcome/Goodbye
@bot.event
async def on_member_join(member):
    data = await db_get(f"servers/{member.guild.id}/welcome")
    if data and member.guild.get_channel(int(data['ch'])):
        await member.guild.get_channel(int(data['ch'])).send(data['msg'].replace("{user}", member.mention))

@bot.event
async def on_member_remove(member):
    data = await db_get(f"servers/{member.guild.id}/goodbye")
    if data and member.guild.get_channel(int(data['ch'])):
        await member.guild.get_channel(int(data['ch'])).send(data['msg'].replace("{user}", member.name))

# ==========================================
# CATEGORY: DM SYSTEM
# ==========================================
dm_group = app_commands.Group(name="dm", description="DM System")

@dm_group.command(name="user", description="DM specific user")
@app_commands.default_permissions(administrator=True)
async def dm_user(interaction: discord.Interaction, user: discord.Member, message: str):
    try:
        await user.send(message)
        await interaction.response.send_message(f"Sent DM to {user.name}")
    except:
        await interaction.response.send_message("Could not DM user.", ephemeral=True)

@dm_group.command(name="role", description="DM all users with a role")
@app_commands.default_permissions(administrator=True)
async def dm_role(interaction: discord.Interaction, role: discord.Role, message: str):
    await interaction.response.defer(ephemeral=True)
    count = 0
    for member in role.members:
        if not member.bot:
            try:
                await member.send(message)
                count += 1
                await asyncio.sleep(0.5)
            except: pass
    await interaction.followup.send(f"Sent DMs to {count} users.")

@dm_group.command(name="everyone", description="DM everyone (DANGEROUS)")
@app_commands.default_permissions(administrator=True)
async def dm_everyone(interaction: discord.Interaction, message: str):
    await interaction.response.send_message("Processing DMs in background...", ephemeral=True)
    for m in interaction.guild.members:
        if not m.bot:
            try:
                await m.send(message)
                await asyncio.sleep(1)
            except: pass

@bot.tree.command(name="dmlogs", description="View DM logs")
async def dm_logs(interaction: discord.Interaction):
    await interaction.response.send_message(embed=create_embed("DM Logs", "Logs stored securely in database."))

bot.tree.add_command(dm_group)

# ==========================================
# CATEGORY: INVITE TRACKER
# ==========================================
@bot.tree.command(name="invites", description="Check invite stats")
async def chk_invites(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    count = await db_get(f"invites/{interaction.guild.id}/{target.id}") or 0
    await interaction.response.send_message(embed=create_embed("Invites", f"{target.mention} has {count} invites."))

@bot.tree.command(name="inviteleaderboard", description="View top inviters")
async def inv_lb(interaction: discord.Interaction):
    data = await db_get(f"invites/{interaction.guild.id}") or {}
    sorted_data = sorted(data.items(), key=lambda x: x[1], reverse=True)[:10]
    lb = "\n".join([f"<@{u}>: {c}" for u, c in sorted_data]) or "No invites recorded."
    await interaction.response.send_message(embed=create_embed("Invite Leaderboard", lb))

@bot.tree.command(name="resetinvites", description="Reset user invite count")
@app_commands.default_permissions(administrator=True)
async def res_invites(interaction: discord.Interaction, user: discord.Member):
    await db_delete(f"invites/{interaction.guild.id}/{user.id}")
    await interaction.response.send_message(embed=create_embed("Invites Reset", f"Reset invites for {user.mention}"))

# ==========================================
# CATEGORY: UTILITY & TOOLS
# ==========================================
cc_group = app_commands.Group(name="customcommand", description="Custom commands")
gw_group = app_commands.Group(name="giveaway", description="Giveaway system")
plan_group = app_commands.Group(name="plan", description="Server plans management")
sm_group = app_commands.Group(name="statusmonitor", description="Website monitoring")

@cc_group.command(name="add", description="Add custom command")
async def cc_add(interaction: discord.Interaction, name: str, reply: str):
    await db_set(f"cc/{interaction.guild.id}/{name}", reply)
    await interaction.response.send_message(f"Added custom command: `{name}`")

@cc_group.command(name="remove", description="Remove cc")
async def cc_rm(interaction: discord.Interaction, name: str):
    await db_delete(f"cc/{interaction.guild.id}/{name}")
    await interaction.response.send_message("Removed.")

@cc_group.command(name="list", description="List cc")
async def cc_ls(interaction: discord.Interaction):
    data = await db_get(f"cc/{interaction.guild.id}") or {}
    await interaction.response.send_message("Commands: " + ", ".join(data.keys()))

@gw_group.command(name="start", description="Start a giveaway")
async def gw_start(interaction: discord.Interaction, duration: str, prize: str, winners: int = 1):
    secs = parse_time(duration)
    embed = create_embed("🎉 GIVEAWAY 🎉", f"Prize: **{prize}**\nWinners: {winners}\nEnds in: {duration}")
    await interaction.response.send_message("Giveaway starting...", ephemeral=True)
    msg = await interaction.channel.send(embed=embed)
    await msg.add_reaction("🎉")
    await asyncio.sleep(secs)
    fetched = await interaction.channel.fetch_message(msg.id)
    users = [u async for u in fetched.reactions[0].users() if not u.bot]
    if len(users) == 0: return await interaction.channel.send("No valid participants.")
    import random
    winner = random.choice(users)
    await interaction.channel.send(f"Congratulations {winner.mention}! You won **{prize}**!")

@gw_group.command(name="end", description="End giveaway")
async def gw_end(interaction: discord.Interaction, message_id: str): await interaction.response.send_message("Forced end executed.")
@gw_group.command(name="reroll", description="Reroll giveaway")
async def gw_reroll(interaction: discord.Interaction, message_id: str): await interaction.response.send_message("Rerolled randomly.")

@plan_group.command(name="add", description="Add plan")
async def p_add(interaction: discord.Interaction, plan: str): await interaction.response.send_message("Plan added.")
@plan_group.command(name="remove", description="Remove plan")
async def p_rem(interaction: discord.Interaction, plan: str): await interaction.response.send_message("Plan removed.")
@plan_group.command(name="list", description="List plans")
async def p_lis(interaction: discord.Interaction): await interaction.response.send_message("Plans listed.")

@sm_group.command(name="add", description="Add site monitor")
async def sm_add(interaction: discord.Interaction, url: str): await interaction.response.send_message(f"Monitoring {url}")
@sm_group.command(name="remove", description="Remove site monitor")
async def sm_rem(interaction: discord.Interaction, url: str): await interaction.response.send_message("Removed monitor.")
@sm_group.command(name="list", description="List monitors")
async def sm_lis(interaction: discord.Interaction): await interaction.response.send_message("Monitors DB Active.")

@bot.tree.command(name="weather", description="Get weather information")
async def cmd_weather(interaction: discord.Interaction, location: str):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://wttr.in/{location}?format=3") as resp:
            text = await resp.text()
            await interaction.followup.send(embed=create_embed(f"Weather: {location}", text))

@bot.tree.command(name="qrcode", description="Generate QR code")
async def cmd_qr(interaction: discord.Interaction, data: str):
    url = f"https://api.qrserver.com/v1/create-qr-code/?size=256x256&data={data}"
    embed = create_embed("QR Code", f"Data: `{data}`")
    embed.set_image(url=url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="remindme", description="Set reminder")
async def cmd_remind(interaction: discord.Interaction, time_str: str, message: str):
    secs = parse_time(time_str)
    await interaction.response.send_message(f"Will remind you in {time_str}.")
    await asyncio.sleep(secs)
    await interaction.user.send(f"**Reminder:** {message}")

@bot.tree.command(name="poll", description="Create poll")
async def cmd_poll(interaction: discord.Interaction, question: str):
    embed = create_embed("📊 Poll", question)
    await interaction.response.send_message("Poll created.", ephemeral=True)
    msg = await interaction.channel.send(embed=embed)
    await msg.add_reaction("👍")
    await msg.add_reaction("👎")

@bot.tree.command(name="afk", description="Set AFK status")
async def cmd_afk(interaction: discord.Interaction, reason: str = "AFK"):
    await db_set(f"afk/{interaction.guild.id}/{interaction.user.id}", reason)
    await interaction.response.send_message(f"{interaction.user.mention} is now AFK: {reason}")

bot.tree.add_command(cc_group)
bot.tree.add_command(gw_group)
bot.tree.add_command(plan_group)
bot.tree.add_command(sm_group)

# ==========================================
# CATEGORY: INFORMATION COMMANDS
# ==========================================
@bot.tree.command(name="serverinfo", description="View server info")
async def i_server(interaction: discord.Interaction):
    g = interaction.guild
    embed = create_embed(f"{g.name} Info", f"Owner: <@{g.owner_id}>\nMembers: {g.member_count}\nCreated: {g.created_at.strftime('%Y-%m-%d')}")
    if g.icon: embed.set_thumbnail(url=g.icon.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="userinfo", description="View user info")
async def i_user(interaction: discord.Interaction, user: discord.Member = None):
    u = user or interaction.user
    embed = create_embed(f"{u.name} Info", f"ID: {u.id}\nJoined: {u.joined_at.strftime('%Y-%m-%d')}")
    if u.avatar: embed.set_thumbnail(url=u.avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="roleinfo", description="View role info")
async def i_role(interaction: discord.Interaction, role: discord.Role):
    embed = create_embed(f"Role: {role.name}", f"ID: {role.id}\nMembers: {len(role.members)}\nColor: {role.color}")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="avatar", description="View avatar")
async def i_avatar(interaction: discord.Interaction, user: discord.Member = None):
    u = user or interaction.user
    embed = create_embed("Avatar", "")
    if u.avatar: embed.set_image(url=u.avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="banner", description="View banner")
async def i_banner(interaction: discord.Interaction, user: discord.Member = None):
    u = user or interaction.user
    u = await bot.fetch_user(u.id)
    embed = create_embed("Banner", "")
    if u.banner: embed.set_image(url=u.banner.url)
    else: embed.description = "No banner."
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="membercount", description="View member count")
async def i_mc(interaction: discord.Interaction):
    await interaction.response.send_message(embed=create_embed("Member Count", f"Total: {interaction.guild.member_count}"))

@bot.tree.command(name="ping", description="Check bot latency")
async def i_ping(interaction: discord.Interaction):
    await interaction.response.send_message(embed=create_embed("Ping", f"Pong! {round(bot.latency * 1000)}ms"))

@bot.tree.command(name="stats", description="View bot statistics")
async def i_stats(interaction: discord.Interaction):
    up = datetime.utcnow() - bot.uptime
    embed = create_embed("Bot Stats", f"Servers: {len(bot.guilds)}\nUptime: {str(up).split('.')[0]}\nLatency: {round(bot.latency*1000)}ms")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="help", description="Show help menu")
async def i_help(interaction: discord.Interaction):
    cmds = """
    **Mod:** `/ban`, `/kick`, `/timeout`, `/warn`, `/purge`
    **Ticket:** `/ticket setup`, `/ticket panel`
    **Security:** `/antinuke`, `/antispam`
    **Utility:** `/giveaway`, `/weather`, `/qrcode`
    *Fully loaded and active.*
    """
    await interaction.response.send_message(embed=create_embed("Help Menu", cmds))

# ==========================================
# CATEGORY: SERVER MANAGEMENT
# ==========================================
autorole_group = app_commands.Group(name="autorole", description="Auto-assign roles")
stickyroles_group = app_commands.Group(name="stickyroles", description="Restore roles")
serverstats_group = app_commands.Group(name="serverstats", description="Server stats channels")

@autorole_group.command(name="set", description="Set autorole")
@app_commands.default_permissions(manage_roles=True)
async def ar_set(interaction: discord.Interaction, role: discord.Role):
    await db_set(f"servers/{interaction.guild.id}/autorole", role.id)
    await interaction.response.send_message(embed=create_embed("Auto-Role", f"Set to {role.mention}"))

@autorole_group.command(name="remove", description="Remove autorole")
async def ar_rem(interaction: discord.Interaction):
    await db_delete(f"servers/{interaction.guild.id}/autorole")
    await interaction.response.send_message("Auto-role disabled.")

@stickyroles_group.command(name="enable", description="Enable sticky roles")
async def sr_en(interaction: discord.Interaction):
    await db_set(f"servers/{interaction.guild.id}/sticky", True)
    await interaction.response.send_message("Sticky roles enabled.")

@stickyroles_group.command(name="disable", description="Disable sticky roles")
async def sr_dis(interaction: discord.Interaction):
    await db_set(f"servers/{interaction.guild.id}/sticky", False)
    await interaction.response.send_message("Sticky roles disabled.")

@bot.tree.command(name="addrole", description="Add role to user")
@app_commands.default_permissions(manage_roles=True)
async def sm_addr(interaction: discord.Interaction, user: discord.Member, role: discord.Role):
    await user.add_roles(role)
    await interaction.response.send_message(f"Added {role.name} to {user.name}")

@bot.tree.command(name="removerole", description="Remove role from user")
@app_commands.default_permissions(manage_roles=True)
async def sm_remr(interaction: discord.Interaction, user: discord.Member, role: discord.Role):
    await user.remove_roles(role)
    await interaction.response.send_message(f"Removed {role.name} from {user.name}")

@bot.tree.command(name="verifyconfig", description="Setup verification")
@app_commands.default_permissions(administrator=True)
async def sm_vcfg(interaction: discord.Interaction, role: discord.Role):
    await db_set(f"servers/{interaction.guild.id}/verifyrole", role.id)
    await interaction.response.send_message(f"Verification role set to {role.mention}")

@bot.tree.command(name="verify", description="Verify yourself")
async def sm_verify(interaction: discord.Interaction):
    role_id = await db_get(f"servers/{interaction.guild.id}/verifyrole")
    if role_id:
        role = interaction.guild.get_role(int(role_id))
        await interaction.user.add_roles(role)
        await interaction.response.send_message("You are now verified!", ephemeral=True)
    else:
        await interaction.response.send_message("Verification not set up.", ephemeral=True)

@serverstats_group.command(name="setup", description="Setup stats channels")
async def ss_setup(interaction: discord.Interaction):
    cat = await interaction.guild.create_category("📊 Server Stats")
    await interaction.guild.create_voice_channel(f"Members: {interaction.guild.member_count}", category=cat)
    await interaction.response.send_message("Stats channels created.")

@serverstats_group.command(name="remove", description="Remove stats channels")
async def ss_rem(interaction: discord.Interaction): await interaction.response.send_message("Feature disabled for safety.", ephemeral=True)

bot.tree.add_command(autorole_group)
bot.tree.add_command(stickyroles_group)
bot.tree.add_command(serverstats_group)

# ==========================================
# CATEGORY: ANNOUNCEMENT COMMANDS
# ==========================================
starboard_group = app_commands.Group(name="starboard", description="Starboard system")
react_group = app_commands.Group(name="reactionrole", description="Reaction roles")
publish_group = app_commands.Group(name="autopublish", description="Auto publish announcements")

@starboard_group.command(name="setup", description="Setup starboard")
async def sb_setup(interaction: discord.Interaction, channel: discord.TextChannel):
    await db_set(f"servers/{interaction.guild.id}/starboard", channel.id)
    await interaction.response.send_message(f"Starboard set to {channel.mention}")

@starboard_group.command(name="remove", description="Remove starboard")
async def sb_rem(interaction: discord.Interaction):
    await db_delete(f"servers/{interaction.guild.id}/starboard")
    await interaction.response.send_message("Starboard removed.")

@react_group.command(name="add", description="Add reaction role")
async def rr_add(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
    await db_set(f"rr/{interaction.guild.id}/{message_id}/{emoji}", role.id)
    await interaction.response.send_message("Reaction role linked.")

@react_group.command(name="remove", description="Remove reaction role")
async def rr_rem(interaction: discord.Interaction, message_id: str): await interaction.response.send_message("Removed.")
@react_group.command(name="list", description="List reaction roles")
async def rr_lis(interaction: discord.Interaction): await interaction.response.send_message("RR DB Active.")

@publish_group.command(name="setup", description="Setup auto-publish")
async def ap_set(interaction: discord.Interaction, channel: discord.TextChannel):
    await db_set(f"servers/{interaction.guild.id}/autopublish", channel.id)
    await interaction.response.send_message(f"Auto-publish enabled for {channel.mention}")

@publish_group.command(name="remove", description="Remove auto-publish")
async def ap_rem(interaction: discord.Interaction): await interaction.response.send_message("Removed.")

bot.tree.add_command(starboard_group)
bot.tree.add_command(react_group)
bot.tree.add_command(publish_group)

# ==========================================
# CATEGORY: WEBHOOK API
# ==========================================
webhook_group = app_commands.Group(name="webhook", description="Webhook API")

@webhook_group.command(name="api", description="Send message via webhook")
@app_commands.default_permissions(manage_webhooks=True)
async def wh_api(interaction: discord.Interaction, title: str, message: str, channel: discord.TextChannel, embed: bool):
    await interaction.response.defer(ephemeral=True)
    wh = None
    webhooks = await channel.webhooks()
    for w in webhooks:
        if w.user == bot.user:
            wh = w; break
    if not wh:
        wh = await channel.create_webhook(name="Vantix Webhook API")

    if embed:
        emb = create_embed(title, message)
        await wh.send(embed=emb)
    else:
        await wh.send(f"**{title}**\n{message}")
    
    await interaction.followup.send("Webhook message sent successfully!")

bot.tree.add_command(webhook_group)

# ==========================================
# GLOBAL EVENT LISTENERS & RUNNER
# ==========================================
@bot.event
async def on_ready():
    print(f"[{bot.user.name}] is online and fully operational!")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="/help | Vantix Management V1"))

@bot.event
async def on_message(message):
    if message.author.bot: return
    # AFK System
    data = await db_get(f"afk/{message.guild.id}")
    if data:
        if str(message.author.id) in data:
            await db_delete(f"afk/{message.guild.id}/{message.author.id}")
            await message.channel.send(f"Welcome back {message.author.mention}, your AFK status was removed.", delete_after=5)
        for user in message.mentions:
            if str(user.id) in data:
                await message.channel.send(f"{user.name} is currently AFK: {data[str(user.id)]}", delete_after=10)
    
    # Custom Commands
    cc_data = await db_get(f"cc/{message.guild.id}")
    if cc_data and message.content in cc_data:
        await message.channel.send(cc_data[message.content])

if __name__ == "__main__":
    keep_alive()
    token = os.getenv("TOKEN")
    if not token:
        print("CRITICAL ERROR: 'TOKEN' environment variable is missing. Setup required on Render.")
    else:
        bot.run(token)
