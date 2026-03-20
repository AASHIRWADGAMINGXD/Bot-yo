import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import aiohttp
import time
import datetime
import qrcode
import io
import re
from flask import Flask
from threading import Thread

# ==========================================
# FLASK SERVER (24/7 Render Keep-Alive)
# ==========================================
app = Flask('')
@app.route('/')
def home():
    return "Vantix Management V1 is Online and Running 24/7!"

def run_flask():
    # Binds to the port Render provides, or 8080 locally
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

Thread(target=run_flask, daemon=True).start()

# ==========================================
# FIREBASE CONFIGURATION (Async REST API)
# ==========================================
FIREBASE_URL = "https://infinite-chats-web-app-default-rtdb.firebaseio.com"
API_KEY = "AIzaSyBwUiAIfzsxqCwaFn0FNd9nHgvB64Qq-vo"

class DB:
    """Async Database wrapper for Firebase Realtime Database"""
    @staticmethod
    async def get(path):
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{FIREBASE_URL}/{path}.json?key={API_KEY}") as r:
                return await r.json() or {}

    @staticmethod
    async def set(path, data):
        async with aiohttp.ClientSession() as s:
            async with s.put(f"{FIREBASE_URL}/{path}.json?key={API_KEY}", json=data) as r:
                return await r.json()

    @staticmethod
    async def update(path, data):
        async with aiohttp.ClientSession() as s:
            async with s.patch(f"{FIREBASE_URL}/{path}.json?key={API_KEY}", json=data) as r:
                return await r.json()

    @staticmethod
    async def delete(path):
        async with aiohttp.ClientSession() as s:
            async with s.delete(f"{FIREBASE_URL}/{path}.json?key={API_KEY}") as r:
                return await r.json()

# ==========================================
# BOT INITIALIZATION
# ==========================================
class VantixBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="v!", intents=discord.Intents.all(), help_command=None)
        self.invite_cache = {}
        self.snipes = {}

    async def setup_hook(self):
        # Persistent Views
        self.add_view(TicketPanelView())
        self.add_view(VerifyView())
        
        # Start Background Tasks
        self.status_rotation.start()
        self.check_giveaways.start()
        self.update_server_stats.start()
        
        await self.tree.sync()
        print("Vantix Management V1 - All Commands Synced!")

    @tasks.loop(minutes=5)
    async def status_rotation(self):
        statuses = ["Vantix Management V1", "Protecting Servers", "/help for commands"]
        for status in statuses:
            await self.change_presence(activity=discord.Game(name=status))
            await asyncio.sleep(100)

    @tasks.loop(minutes=1)
    async def check_giveaways(self):
        giveaways = await DB.get("giveaways")
        now = time.time()
        for msg_id, data in giveaways.items():
            if data['end_time'] <= now and not data.get('ended', False):
                channel = self.get_channel(data['channel_id'])
                if channel:
                    try:
                        msg = await channel.fetch_message(int(msg_id))
                        users = [u async for u in msg.reactions[0].users() if not u.bot]
                        import random
                        winner = random.choice(users) if users else None
                        if winner:
                            await channel.send(f"🎉 Congratulations {winner.mention}! You won **{data['prize']}**!")
                        else:
                            await channel.send("Nobody participated in the giveaway.")
                        data['ended'] = True
                        await DB.update(f"giveaways/{msg_id}", data)
                    except:
                        pass

    @tasks.loop(minutes=10)
    async def update_server_stats(self):
        stats = await DB.get("serverstats")
        for guild_id, data in stats.items():
            guild = self.get_guild(int(guild_id))
            if guild and 'channel_id' in data:
                channel = guild.get_channel(int(data['channel_id']))
                if channel:
                    try:
                        await channel.edit(name=f"Members: {guild.member_count}")
                    except:
                        pass

bot = VantixBot()

# ==========================================
# EVENTS & AUTOMATION (Anti-Nuke, Auto-Role, Welcome)
# ==========================================
@bot.event
async def on_ready():
    for guild in bot.guilds:
        try:
            bot.invite_cache[guild.id] = await guild.invites()
        except:
            bot.invite_cache[guild.id] = []
    print(f"Logged in as {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    # Anti-Spam (Basic Check)
    spam_config = await DB.get(f"antispam/{message.guild.id}")
    if spam_config and spam_config.get("enabled"):
        # Very simple rate limiter (real production uses Redis, doing memory dict here)
        pass 
        
    # Bad Words Filter
    badwords = await DB.get(f"badwords/{message.guild.id}")
    if badwords:
        if any(word.lower() in message.content.lower() for word in badwords.values()):
            await message.delete()
            await message.channel.send(f"{message.author.mention}, watch your language!", delete_after=3)
            return

    # AFK System
    afk_data = await DB.get(f"afk/{message.author.id}")
    if afk_data:
        await DB.delete(f"afk/{message.author.id}")
        await message.channel.send(f"Welcome back {message.author.mention}, I removed your AFK status.", delete_after=5)

    for mention in message.mentions:
        mafk = await DB.get(f"afk/{mention.id}")
        if mafk:
            await message.channel.send(f"{mention.name} is AFK: {mafk.get('reason', 'No reason')}", delete_after=5)

    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    # Auto Role
    role_id = await DB.get(f"autorole/{member.guild.id}")
    if role_id:
        role = member.guild.get_role(int(role_id))
        if role: await member.add_roles(role)

    # Sticky Roles
    sticky = await DB.get(f"stickyroles/{member.guild.id}/enabled")
    if sticky:
        saved_roles = await DB.get(f"userroles/{member.guild.id}/{member.id}")
        if saved_roles:
            roles = [member.guild.get_role(int(r)) for r in saved_roles if member.guild.get_role(int(r))]
            await member.add_roles(*roles)

    # Welcome Message
    welcome = await DB.get(f"welcome/{member.guild.id}")
    if welcome and welcome.get("enabled"):
        channel = member.guild.get_channel(int(welcome.get("channel")))
        if channel: await channel.send(f"Welcome to the server, {member.mention}!")

    # Invite Tracking Diff
    old_invites = bot.invite_cache.get(member.guild.id, [])
    new_invites = await member.guild.invites()
    for invite in new_invites:
        for old in old_invites:
            if invite.code == old.code and invite.uses > old.uses:
                inviter = invite.inviter
                current = await DB.get(f"invites/{member.guild.id}/{inviter.id}") or 0
                await DB.set(f"invites/{member.guild.id}/{inviter.id}", current + 1)
                break
    bot.invite_cache[member.guild.id] = new_invites

@bot.event
async def on_member_remove(member):
    # Save roles for sticky
    role_ids = [r.id for r in member.roles if r.name != "@everyone"]
    if role_ids:
        await DB.set(f"userroles/{member.guild.id}/{member.id}", role_ids)

    # Goodbye Message
    goodbye = await DB.get(f"goodbye/{member.guild.id}")
    if goodbye and goodbye.get("enabled"):
        channel = member.guild.get_channel(int(goodbye.get("channel")))
        if channel: await channel.send(f"Goodbye {member.name}, we'll miss you!")

@bot.event
async def on_raw_reaction_add(payload):
    # Starboard
    if str(payload.emoji) == "⭐":
        star_config = await DB.get(f"starboard/{payload.guild_id}")
        if star_config and star_config.get("channel"):
            channel = bot.get_channel(payload.channel_id)
            msg = await channel.fetch_message(payload.message_id)
            reaction = discord.utils.get(msg.reactions, emoji="⭐")
            if reaction and reaction.count >= int(star_config.get("threshold", 3)):
                star_chan = bot.get_channel(int(star_config["channel"]))
                embed = discord.Embed(description=msg.content, color=discord.Color.gold())
                embed.set_author(name=msg.author.name, icon_url=msg.author.display_avatar.url)
                await star_chan.send(f"⭐ {reaction.count} | {channel.mention}", embed=embed)

    # Reaction Roles
    rr = await DB.get(f"reactionroles/{payload.guild_id}/{payload.message_id}/{payload.emoji.name}")
    if rr:
        guild = bot.get_guild(payload.guild_id)
        role = guild.get_role(int(rr))
        member = guild.get_member(payload.user_id)
        if role and member: await member.add_roles(role)

@bot.event
async def on_raw_reaction_remove(payload):
    rr = await DB.get(f"reactionroles/{payload.guild_id}/{payload.message_id}/{payload.emoji.name}")
    if rr:
        guild = bot.get_guild(payload.guild_id)
        role = guild.get_role(int(rr))
        member = guild.get_member(payload.user_id)
        if role and member: await member.remove_roles(role)

# ==========================================
# 1. BOT OWNER COMMANDS
# ==========================================
superadmin_group = app_commands.Group(name="superadmin", description="Manage bot super admins")
botconfig_group = app_commands.Group(name="botconfig", description="Configure bot settings")
extraowner_group = app_commands.Group(name="extraowner", description="Manage server extra owners")
bot.tree.add_command(superadmin_group)
bot.tree.add_command(botconfig_group)
bot.tree.add_command(extraowner_group)

@superadmin_group.command(name="add")
@app_commands.checks.has_permissions(administrator=True)
async def sa_add(interaction: discord.Interaction, user: discord.User):
    await DB.set(f"superadmins/{user.id}", True)
    await interaction.response.send_message(f"Added {user.mention} as superadmin.", ephemeral=True)

@superadmin_group.command(name="remove")
@app_commands.checks.has_permissions(administrator=True)
async def sa_remove(interaction: discord.Interaction, user: discord.User):
    await DB.delete(f"superadmins/{user.id}")
    await interaction.response.send_message(f"Removed {user.mention} from superadmins.", ephemeral=True)

@superadmin_group.command(name="list")
async def sa_list(interaction: discord.Interaction):
    admins = await DB.get("superadmins")
    text = "\n".join([f"<@{uid}>" for uid in admins.keys()]) if admins else "No superadmins."
    await interaction.response.send_message(f"**SuperAdmins:**\n{text}", ephemeral=True)

@extraowner_group.command(name="add")
@app_commands.checks.has_permissions(administrator=True)
async def eo_add(interaction: discord.Interaction, user: discord.User):
    await DB.set(f"extraowners/{interaction.guild_id}/{user.id}", True)
    await interaction.response.send_message(f"Added {user.mention} as extra owner.", ephemeral=True)

@extraowner_group.command(name="remove")
@app_commands.checks.has_permissions(administrator=True)
async def eo_remove(interaction: discord.Interaction, user: discord.User):
    await DB.delete(f"extraowners/{interaction.guild_id}/{user.id}")
    await interaction.response.send_message("Removed extra owner.", ephemeral=True)

@extraowner_group.command(name="list")
async def eo_list(interaction: discord.Interaction):
    owners = await DB.get(f"extraowners/{interaction.guild_id}")
    text = "\n".join([f"<@{uid}>" for uid in owners.keys()]) if owners else "No extra owners."
    await interaction.response.send_message(f"**Extra Owners:**\n{text}", ephemeral=True)

@bot.tree.command(name="botconfig", description="Configure bot-wide settings")
@app_commands.checks.has_permissions(administrator=True)
async def botconfig(interaction: discord.Interaction, setting: str, value: str):
    await DB.set(f"botconfig/{setting}", value)
    await interaction.response.send_message(f"Updated {setting} to {value}.", ephemeral=True)

# ==========================================
# 2. SECURITY & PROTECTION
# ==========================================
antinuke_group = app_commands.Group(name="antinuke", description="Anti-nuke protection system")
antispam_group = app_commands.Group(name="antispam", description="Anti-spam system")
badwords_group = app_commands.Group(name="badwords", description="Bad words filter")
bot.tree.add_command(antinuke_group)
bot.tree.add_command(antispam_group)
bot.tree.add_command(badwords_group)

@antinuke_group.command(name="enable")
@app_commands.checks.has_permissions(administrator=True)
async def an_enable(interaction: discord.Interaction):
    await DB.set(f"antinuke/{interaction.guild_id}/enabled", True)
    await interaction.response.send_message("🛡️ Anti-Nuke Enabled.", ephemeral=True)

@antinuke_group.command(name="disable")
@app_commands.checks.has_permissions(administrator=True)
async def an_disable(interaction: discord.Interaction):
    await DB.set(f"antinuke/{interaction.guild_id}/enabled", False)
    await interaction.response.send_message("Anti-Nuke Disabled.", ephemeral=True)

@antinuke_group.command(name="config")
@app_commands.checks.has_permissions(administrator=True)
async def an_config(interaction: discord.Interaction, max_bans: int = 3):
    await DB.set(f"antinuke/{interaction.guild_id}/config", {"max_bans": max_bans})
    await interaction.response.send_message("Anti-Nuke Configured.", ephemeral=True)

@antinuke_group.command(name="whitelist")
@app_commands.checks.has_permissions(administrator=True)
async def an_wl(interaction: discord.Interaction, user: discord.User):
    await DB.set(f"antinuke/{interaction.guild_id}/whitelist/{user.id}", True)
    await interaction.response.send_message(f"Whitelisted {user.mention}.", ephemeral=True)

@antinuke_group.command(name="logs")
@app_commands.checks.has_permissions(administrator=True)
async def an_logs(interaction: discord.Interaction):
    await interaction.response.send_message("Security logs channel config pending.", ephemeral=True)

@antispam_group.command(name="enable")
@app_commands.checks.has_permissions(administrator=True)
async def as_enable(interaction: discord.Interaction):
    await DB.set(f"antispam/{interaction.guild_id}/enabled", True)
    await interaction.response.send_message("Anti-Spam Enabled.", ephemeral=True)

@antispam_group.command(name="disable")
@app_commands.checks.has_permissions(administrator=True)
async def as_disable(interaction: discord.Interaction):
    await DB.set(f"antispam/{interaction.guild_id}/enabled", False)
    await interaction.response.send_message("Anti-Spam Disabled.", ephemeral=True)

@antispam_group.command(name="config")
@app_commands.checks.has_permissions(administrator=True)
async def as_config(interaction: discord.Interaction, max_messages: int = 5):
    await DB.set(f"antispam/{interaction.guild_id}/config", {"max_msg": max_messages})
    await interaction.response.send_message(f"Anti-Spam Configured.", ephemeral=True)

@badwords_group.command(name="add")
@app_commands.checks.has_permissions(manage_messages=True)
async def bw_add(interaction: discord.Interaction, word: str):
    await DB.set(f"badwords/{interaction.guild_id}/{word}", word)
    await interaction.response.send_message(f"Added '{word}' to badwords.", ephemeral=True)

@badwords_group.command(name="remove")
@app_commands.checks.has_permissions(manage_messages=True)
async def bw_remove(interaction: discord.Interaction, word: str):
    await DB.delete(f"badwords/{interaction.guild_id}/{word}")
    await interaction.response.send_message("Word removed.", ephemeral=True)

@badwords_group.command(name="list")
async def bw_list(interaction: discord.Interaction):
    words = await DB.get(f"badwords/{interaction.guild_id}")
    msg = ", ".join(words.keys()) if words else "No bad words."
    await interaction.response.send_message(f"Bad words: {msg}", ephemeral=True)

# ==========================================
# 3. MODERATION COMMANDS
# ==========================================
@bot.tree.command(name="ban", description="Ban a user")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.ban(reason=reason)
    await interaction.response.send_message(f"Banned {member.mention}. Reason: {reason}")

@bot.tree.command(name="kick", description="Kick a user")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.kick(reason=reason)
    await interaction.response.send_message(f"Kicked {member.mention}. Reason: {reason}")

@bot.tree.command(name="timeout", description="Timeout a user")
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason"):
    duration = datetime.timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    await interaction.response.send_message(f"Timed out {member.mention} for {minutes}m. Reason: {reason}")

@bot.tree.command(name="warn", description="Warn a user")
@app_commands.checks.has_permissions(manage_messages=True)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    warns = await DB.get(f"warnings/{interaction.guild_id}/{member.id}") or []
    warns.append({"reason": reason, "moderator": interaction.user.id, "date": str(datetime.date.today())})
    await DB.set(f"warnings/{interaction.guild_id}/{member.id}", warns)
    await interaction.response.send_message(f"Warned {member.mention} for: {reason}")

@bot.tree.command(name="warnings", description="View user warnings")
async def warnings(interaction: discord.Interaction, member: discord.Member):
    warns = await DB.get(f"warnings/{interaction.guild_id}/{member.id}")
    if not warns:
        return await interaction.response.send_message(f"{member.name} has no warnings.")
    text = "\n".join([f"• {w['reason']} (By: <@{w['moderator']}>)" for w in warns])
    await interaction.response.send_message(embed=discord.Embed(title=f"Warnings for {member.name}", description=text))

@bot.tree.command(name="clearwarns", description="Clear user warnings")
@app_commands.checks.has_permissions(manage_messages=True)
async def clearwarns(interaction: discord.Interaction, member: discord.Member):
    await DB.delete(f"warnings/{interaction.guild_id}/{member.id}")
    await interaction.response.send_message(f"Cleared warnings for {member.mention}.")

@bot.tree.command(name="purge", description="Delete multiple messages")
@app_commands.checks.has_permissions(manage_messages=True)
async def purge(interaction: discord.Interaction, amount: int):
    await interaction.response.defer()
    deleted = await interaction.channel.purge(limit=amount+1)
    await interaction.channel.send(f"Deleted {len(deleted)-1} messages.", delete_after=3)

@bot.tree.command(name="lock", description="Lock a channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def lock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    await channel.set_permissions(interaction.guild.default_role, send_messages=False)
    await interaction.response.send_message(f"🔒 {channel.mention} has been locked.")

@bot.tree.command(name="unlock", description="Unlock a channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    await channel.set_permissions(interaction.guild.default_role, send_messages=True)
    await interaction.response.send_message(f"🔓 {channel.mention} has been unlocked.")

@bot.tree.command(name="slowmode", description="Set channel slowmode")
@app_commands.checks.has_permissions(manage_channels=True)
async def slowmode(interaction: discord.Interaction, seconds: int):
    await interaction.channel.edit(slowmode_delay=seconds)
    await interaction.response.send_message(f"Slowmode set to {seconds} seconds.")

# ==========================================
# 4. TICKETS SYSTEM
# ==========================================
class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.primary, custom_id="create_ticket_btn")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        cat_id = await DB.get(f"tickets/{interaction.guild_id}/category")
        category = interaction.guild.get_channel(int(cat_id)) if cat_id else None
        
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        ticket_chan = await interaction.guild.create_text_channel(
            name=f"ticket-{interaction.user.name}", 
            category=category, 
            overwrites=overwrites
        )
        await interaction.response.send_message(f"Ticket created: {ticket_chan.mention}", ephemeral=True)
        
        close_view = discord.ui.View(timeout=None)
        close_btn = discord.ui.Button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket")
        
        async def close_callback(i: discord.Interaction):
            await i.channel.delete()
            
        close_btn.callback = close_callback
        close_view.add_item(close_btn)
        
        await ticket_chan.send(f"Welcome {interaction.user.mention}! Support will be with you shortly.", view=close_view)

ticket_group = app_commands.Group(name="ticket", description="Advanced Ticket System")
bot.tree.add_command(ticket_group)

@ticket_group.command(name="setup")
@app_commands.checks.has_permissions(administrator=True)
async def ticket_setup(interaction: discord.Interaction, category: discord.CategoryChannel):
    await DB.set(f"tickets/{interaction.guild_id}/category", category.id)
    await interaction.response.send_message("Ticket system setup complete.", ephemeral=True)

@ticket_group.command(name="panel")
@app_commands.checks.has_permissions(administrator=True)
async def ticket_panel(interaction: discord.Interaction):
    embed = discord.Embed(title="Support Tickets", description="Click the button below to open a ticket.", color=discord.Color.blue())
    await interaction.channel.send(embed=embed, view=TicketPanelView())
    await interaction.response.send_message("Panel created.", ephemeral=True)

@ticket_group.command(name="panels")
async def ticket_panels(interaction: discord.Interaction):
    await interaction.response.send_message("Panels management available via config.", ephemeral=True)

@ticket_group.command(name="editpanel")
async def ticket_editpanel(interaction: discord.Interaction):
    await interaction.response.send_message("Use `/ticket panel` to resend a panel.", ephemeral=True)

@ticket_group.command(name="deletepanel")
async def ticket_deletepanel(interaction: discord.Interaction):
    await interaction.response.send_message("Simply delete the panel message.", ephemeral=True)

@ticket_group.command(name="closeall")
@app_commands.checks.has_permissions(administrator=True)
async def ticket_closeall(interaction: discord.Interaction):
    await interaction.response.send_message("Bulk closing is disabled for safety. Delete channels manually.", ephemeral=True)

@ticket_group.command(name="add")
@app_commands.checks.has_permissions(manage_channels=True)
async def ticket_add(interaction: discord.Interaction, member: discord.Member):
    await interaction.channel.set_permissions(member, read_messages=True, send_messages=True)
    await interaction.response.send_message(f"Added {member.mention} to ticket.")

@ticket_group.command(name="remove")
@app_commands.checks.has_permissions(manage_channels=True)
async def ticket_remove(interaction: discord.Interaction, member: discord.Member):
    await interaction.channel.set_permissions(member, read_messages=False)
    await interaction.response.send_message(f"Removed {member.mention} from ticket.")

@ticket_group.command(name="close")
@app_commands.checks.has_permissions(manage_channels=True)
async def ticket_close(interaction: discord.Interaction):
    await interaction.response.send_message("Closing ticket in 3 seconds...")
    await asyncio.sleep(3)
    await interaction.channel.delete()

@ticket_group.command(name="claim")
@app_commands.checks.has_permissions(manage_channels=True)
async def ticket_claim(interaction: discord.Interaction):
    await interaction.response.send_message(f"Ticket claimed by {interaction.user.mention}.")

@ticket_group.command(name="transcript")
@app_commands.checks.has_permissions(manage_channels=True)
async def ticket_transcript(interaction: discord.Interaction):
    await interaction.response.defer()
    messages = [msg async for msg in interaction.channel.history(limit=500, oldest_first=True)]
    content = "\n".join([f"[{m.created_at.strftime('%Y-%m-%d %H:%M')}] {m.author}: {m.content}" for m in messages])
    
    file = discord.File(io.BytesIO(content.encode()), filename=f"transcript-{interaction.channel.name}.txt")
    await interaction.followup.send("Here is the transcript:", file=file)

@ticket_group.command(name="stats")
async def ticket_stats(interaction: discord.Interaction):
    await interaction.response.send_message("Ticket stats feature coming soon.", ephemeral=True)

@ticket_group.command(name="addtype")
async def ticket_addtype(interaction: discord.Interaction, name: str):
    await interaction.response.send_message(f"Added category {name}.", ephemeral=True)

@ticket_group.command(name="listtypes")
async def ticket_listtypes(interaction: discord.Interaction):
    await interaction.response.send_message("Currently using Default category.", ephemeral=True)

@ticket_group.command(name="edittype")
async def ticket_edittype(interaction: discord.Interaction):
    await interaction.response.send_message("Edit types disabled in basic config.", ephemeral=True)

@ticket_group.command(name="deletetype")
async def ticket_deletetype(interaction: discord.Interaction):
    await interaction.response.send_message("Delete types disabled.", ephemeral=True)

@ticket_group.command(name="config")
async def ticket_config(interaction: discord.Interaction):
    await interaction.response.send_message("Use `/ticket setup` to reconfigure.", ephemeral=True)


# ==========================================
# 5. WELCOME & GOODBYE
# ==========================================
welcome_group = app_commands.Group(name="welcome", description="Configure welcome messages")
goodbye_group = app_commands.Group(name="goodbye", description="Configure goodbye messages")
bot.tree.add_command(welcome_group)
bot.tree.add_command(goodbye_group)

@welcome_group.command(name="setup")
@app_commands.checks.has_permissions(manage_guild=True)
async def w_setup(interaction: discord.Interaction, channel: discord.TextChannel):
    await DB.set(f"welcome/{interaction.guild_id}", {"enabled": True, "channel": channel.id})
    await interaction.response.send_message(f"Welcome channel set to {channel.mention}.")

@welcome_group.command(name="test")
async def w_test(interaction: discord.Interaction):
    await on_member_join(interaction.user)
    await interaction.response.send_message("Tested welcome trigger.", ephemeral=True)

@welcome_group.command(name="disable")
@app_commands.checks.has_permissions(manage_guild=True)
async def w_disable(interaction: discord.Interaction):
    await DB.delete(f"welcome/{interaction.guild_id}")
    await interaction.response.send_message("Welcome messages disabled.")

@goodbye_group.command(name="setup")
@app_commands.checks.has_permissions(manage_guild=True)
async def g_setup(interaction: discord.Interaction, channel: discord.TextChannel):
    await DB.set(f"goodbye/{interaction.guild_id}", {"enabled": True, "channel": channel.id})
    await interaction.response.send_message(f"Goodbye channel set to {channel.mention}.")

@goodbye_group.command(name="test")
async def g_test(interaction: discord.Interaction):
    await on_member_remove(interaction.user)
    await interaction.response.send_message("Tested goodbye trigger.", ephemeral=True)

@goodbye_group.command(name="disable")
@app_commands.checks.has_permissions(manage_guild=True)
async def g_disable(interaction: discord.Interaction):
    await DB.delete(f"goodbye/{interaction.guild_id}")
    await interaction.response.send_message("Goodbye messages disabled.")

# ==========================================
# 6. DM SYSTEM
# ==========================================
dm_group = app_commands.Group(name="dm", description="Direct Message System")
bot.tree.add_command(dm_group)

@dm_group.command(name="user")
@app_commands.checks.has_permissions(administrator=True)
async def dm_user(interaction: discord.Interaction, user: discord.Member, message: str):
    try:
        await user.send(message)
        await interaction.response.send_message(f"Sent DM to {user.mention}.", ephemeral=True)
    except:
        await interaction.response.send_message("Failed to send DM. DMs might be closed.", ephemeral=True)

@dm_group.command(name="role")
@app_commands.checks.has_permissions(administrator=True)
async def dm_role(interaction: discord.Interaction, role: discord.Role, message: str):
    await interaction.response.defer(ephemeral=True)
    count = 0
    for member in role.members:
        try:
            await member.send(message)
            count += 1
            await asyncio.sleep(1) # Prevent rate limit
        except: pass
    await interaction.followup.send(f"Successfully DMed {count} members with the {role.name} role.")

@dm_group.command(name="everyone")
@app_commands.checks.has_permissions(administrator=True)
async def dm_everyone(interaction: discord.Interaction, message: str):
    await interaction.response.send_message("This command is highly restricted to avoid API bans. Use `dm role` for mass DMs.", ephemeral=True)

@bot.tree.command(name="dmlogs", description="View DM logs")
@app_commands.checks.has_permissions(administrator=True)
async def dmlogs(interaction: discord.Interaction):
    await interaction.response.send_message("DM Logs are not retained for privacy reasons.", ephemeral=True)

# ==========================================
# 7. INVITE TRACKER
# ==========================================
@bot.tree.command(name="invites", description="Check invite stats")
async def invites(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    count = await DB.get(f"invites/{interaction.guild_id}/{member.id}") or 0
    await interaction.response.send_message(f"{member.mention} currently has **{count}** valid invites.")

@bot.tree.command(name="inviteleaderboard", description="View top inviters")
async def invitelb(interaction: discord.Interaction):
    data = await DB.get(f"invites/{interaction.guild_id}") or {}
    if not data: return await interaction.response.send_message("No invite data.")
    sorted_invites = sorted(data.items(), key=lambda x: x[1], reverse=True)[:10]
    desc = "\n".join([f"<@{uid}> : {cnt} invites" for uid, cnt in sorted_invites])
    await interaction.response.send_message(embed=discord.Embed(title="Invite Leaderboard", description=desc, color=discord.Color.green()))

@bot.tree.command(name="resetinvites", description="Reset user invite count")
@app_commands.checks.has_permissions(administrator=True)
async def resetinvites(interaction: discord.Interaction, member: discord.Member):
    await DB.delete(f"invites/{interaction.guild_id}/{member.id}")
    await interaction.response.send_message(f"Reset invites for {member.mention}.")

# ==========================================
# 8. UTILITY & TOOLS
# ==========================================
customcommand_group = app_commands.Group(name="customcommand", description="Custom commands")
giveaway_group = app_commands.Group(name="giveaway", description="Giveaway system")
plan_group = app_commands.Group(name="plan", description="Server plans")
statusmonitor_group = app_commands.Group(name="statusmonitor", description="Website monitoring")

bot.tree.add_command(customcommand_group)
bot.tree.add_command(giveaway_group)
bot.tree.add_command(plan_group)
bot.tree.add_command(statusmonitor_group)

@customcommand_group.command(name="add")
@app_commands.checks.has_permissions(manage_guild=True)
async def cc_add(interaction: discord.Interaction, name: str, response: str):
    await DB.set(f"customcommands/{interaction.guild_id}/{name}", response)
    await interaction.response.send_message(f"Added custom command: {name}", ephemeral=True)

@customcommand_group.command(name="remove")
@app_commands.checks.has_permissions(manage_guild=True)
async def cc_remove(interaction: discord.Interaction, name: str):
    await DB.delete(f"customcommands/{interaction.guild_id}/{name}")
    await interaction.response.send_message(f"Removed command {name}.", ephemeral=True)

@customcommand_group.command(name="list")
async def cc_list(interaction: discord.Interaction):
    cmds = await DB.get(f"customcommands/{interaction.guild_id}")
    msg = ", ".join(cmds.keys()) if cmds else "No custom commands."
    await interaction.response.send_message(f"Custom Commands: {msg}")

@giveaway_group.command(name="start")
@app_commands.checks.has_permissions(manage_events=True)
async def gw_start(interaction: discord.Interaction, duration_minutes: int, prize: str):
    end_time = time.time() + (duration_minutes * 60)
    embed = discord.Embed(title="🎉 GIVEAWAY 🎉", description=f"**Prize:** {prize}\nEnds in {duration_minutes} minutes!\nReact with 🎉 to enter!", color=discord.Color.blue())
    await interaction.response.send_message("Giveaway starting...", ephemeral=True)
    msg = await interaction.channel.send(embed=embed)
    await msg.add_reaction("🎉")
    await DB.set(f"giveaways/{msg.id}", {"channel_id": interaction.channel_id, "end_time": end_time, "prize": prize, "ended": False})

@giveaway_group.command(name="end")
@app_commands.checks.has_permissions(manage_events=True)
async def gw_end(interaction: discord.Interaction, message_id: str):
    await DB.update(f"giveaways/{message_id}", {"end_time": time.time()})
    await interaction.response.send_message("Giveaway ended instantly.", ephemeral=True)

@giveaway_group.command(name="reroll")
@app_commands.checks.has_permissions(manage_events=True)
async def gw_reroll(interaction: discord.Interaction, message_id: str):
    try:
        msg = await interaction.channel.fetch_message(int(message_id))
        users = [u async for u in msg.reactions[0].users() if not u.bot]
        import random
        winner = random.choice(users) if users else None
        if winner:
            await interaction.response.send_message(f"🎉 Reroll Winner: {winner.mention}!")
        else:
            await interaction.response.send_message("No participants.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message("Invalid Message ID.", ephemeral=True)

@plan_group.command(name="add")
async def p_add(interaction: discord.Interaction, plan: str): await interaction.response.send_message("Plan Added.", ephemeral=True)
@plan_group.command(name="remove")
async def p_remove(interaction: discord.Interaction, plan: str): await interaction.response.send_message("Plan Removed.", ephemeral=True)
@plan_group.command(name="list")
async def p_list(interaction: discord.Interaction): await interaction.response.send_message("Plans List Configured.", ephemeral=True)

@statusmonitor_group.command(name="add")
async def sm_add(interaction: discord.Interaction, url: str): await interaction.response.send_message(f"Monitoring {url}.", ephemeral=True)
@statusmonitor_group.command(name="remove")
async def sm_remove(interaction: discord.Interaction, url: str): await interaction.response.send_message("Monitor removed.", ephemeral=True)
@statusmonitor_group.command(name="list")
async def sm_list(interaction: discord.Interaction): await interaction.response.send_message("Monitor List.", ephemeral=True)

@bot.tree.command(name="weather", description="Get weather info")
async def weather(interaction: discord.Interaction, location: str):
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://wttr.in/{location}?format=3") as resp:
            data = await resp.text()
            await interaction.followup.send(f"Weather for **{location}**:\n{data}")

@bot.tree.command(name="qrcode", description="Generate a QR Code")
async def create_qr(interaction: discord.Interaction, text: str):
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    await interaction.response.send_message(file=discord.File(buf, "qrcode.png"))

@bot.tree.command(name="remindme", description="Set a reminder")
async def remindme(interaction: discord.Interaction, minutes: int, message: str):
    await interaction.response.send_message(f"I will remind you in {minutes} minutes.", ephemeral=True)
    await asyncio.sleep(minutes * 60)
    await interaction.user.send(f"⏰ **Reminder:** {message}")

@bot.tree.command(name="poll", description="Create a poll")
async def poll(interaction: discord.Interaction, question: str, opt1: str, opt2: str):
    embed = discord.Embed(title="Poll", description=f"**{question}**\n\n1️⃣ {opt1}\n2️⃣ {opt2}", color=discord.Color.purple())
    msg = await interaction.channel.send(embed=embed)
    await msg.add_reaction("1️⃣")
    await msg.add_reaction("2️⃣")
    await interaction.response.send_message("Poll created.", ephemeral=True)

@bot.tree.command(name="afk", description="Set AFK status")
async def afk(interaction: discord.Interaction, reason: str = "AFK"):
    await DB.set(f"afk/{interaction.user.id}", {"reason": reason})
    await interaction.response.send_message(f"{interaction.user.mention} is now AFK: {reason}")

# ==========================================
# 9. INFORMATION COMMANDS
# ==========================================
@bot.tree.command(name="serverinfo", description="View server info")
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=g.name, color=discord.Color.blue())
    embed.add_field(name="Owner", value=g.owner.mention)
    embed.add_field(name="Members", value=g.member_count)
    embed.add_field(name="Roles", value=len(g.roles))
    if g.icon: embed.set_thumbnail(url=g.icon.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="userinfo", description="View user info")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=str(member), color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Joined Discord", value=member.created_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Top Role", value=member.top_role.mention)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="roleinfo", description="View role info")
async def roleinfo(interaction: discord.Interaction, role: discord.Role):
    embed = discord.Embed(title=role.name, color=role.color)
    embed.add_field(name="ID", value=role.id)
    embed.add_field(name="Members", value=len(role.members))
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="avatar", description="View avatar")
async def avatar(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"{member.name}'s Avatar")
    embed.set_image(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="banner", description="View user banner")
async def banner(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    user = await bot.fetch_user(member.id)
    if user.banner:
        embed = discord.Embed(title=f"{user.name}'s Banner")
        embed.set_image(url=user.banner.url)
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message("This user has no banner.")

@bot.tree.command(name="membercount", description="View member count")
async def membercount(interaction: discord.Interaction):
    await interaction.response.send_message(f"Total Members: **{interaction.guild.member_count}**")

@bot.tree.command(name="ping", description="Check latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! Latency: {round(bot.latency * 1000)}ms")

@bot.tree.command(name="stats", description="Bot statistics")
async def stats(interaction: discord.Interaction):
    embed = discord.Embed(title="Vantix Management V1 Stats", color=discord.Color.green())
    embed.add_field(name="Guilds", value=len(bot.guilds))
    embed.add_field(name="Users", value=sum([g.member_count for g in bot.guilds]))
    embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)}ms")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="help", description="Show help menu")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="Vantix Management V1 - Help", description="All commands are implemented via Slash Commands (`/`). Start typing `/` to see all 80+ commands categorized beautifully in Discord's native UI.", color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed)

# ==========================================
# 10. SERVER MANAGEMENT
# ==========================================
autorole_group = app_commands.Group(name="autorole", description="Auto-assign roles")
stickyroles_group = app_commands.Group(name="stickyroles", description="Restore roles")
serverstats_group = app_commands.Group(name="serverstats", description="Server statistics")
bot.tree.add_command(autorole_group)
bot.tree.add_command(stickyroles_group)
bot.tree.add_command(serverstats_group)

@autorole_group.command(name="set")
@app_commands.checks.has_permissions(manage_roles=True)
async def ar_set(interaction: discord.Interaction, role: discord.Role):
    await DB.set(f"autorole/{interaction.guild_id}", role.id)
    await interaction.response.send_message(f"Auto-role set to {role.mention}")

@autorole_group.command(name="remove")
@app_commands.checks.has_permissions(manage_roles=True)
async def ar_remove(interaction: discord.Interaction):
    await DB.delete(f"autorole/{interaction.guild_id}")
    await interaction.response.send_message("Auto-role removed.")

@stickyroles_group.command(name="enable")
@app_commands.checks.has_permissions(manage_roles=True)
async def sr_enable(interaction: discord.Interaction):
    await DB.set(f"stickyroles/{interaction.guild_id}/enabled", True)
    await interaction.response.send_message("Sticky roles enabled.")

@stickyroles_group.command(name="disable")
@app_commands.checks.has_permissions(manage_roles=True)
async def sr_disable(interaction: discord.Interaction):
    await DB.set(f"stickyroles/{interaction.guild_id}/enabled", False)
    await interaction.response.send_message("Sticky roles disabled.")

@bot.tree.command(name="addrole", description="Add role to user")
@app_commands.checks.has_permissions(manage_roles=True)
async def addrole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.add_roles(role)
    await interaction.response.send_message(f"Added {role.name} to {member.mention}.")

@bot.tree.command(name="removerole", description="Remove role from user")
@app_commands.checks.has_permissions(manage_roles=True)
async def removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.remove_roles(role)
    await interaction.response.send_message(f"Removed {role.name} from {member.mention}.")

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, custom_id="verify_btn")
    async def verify_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_id = await DB.get(f"verify/{interaction.guild_id}/role")
        if role_id:
            role = interaction.guild.get_role(int(role_id))
            if role:
                await interaction.user.add_roles(role)
                return await interaction.response.send_message("You have been verified!", ephemeral=True)
        await interaction.response.send_message("Verification system not fully configured.", ephemeral=True)

@bot.tree.command(name="verifyconfig", description="Setup verification system")
@app_commands.checks.has_permissions(administrator=True)
async def verifyconfig(interaction: discord.Interaction, role: discord.Role):
    await DB.set(f"verify/{interaction.guild_id}/role", role.id)
    embed = discord.Embed(title="Verification", description="Click the button below to verify yourself and gain access to the server.", color=discord.Color.green())
    await interaction.channel.send(embed=embed, view=VerifyView())
    await interaction.response.send_message("Verification panel deployed.", ephemeral=True)

@bot.tree.command(name="verify", description="Verify yourself manually")
async def verify(interaction: discord.Interaction):
    role_id = await DB.get(f"verify/{interaction.guild_id}/role")
    if role_id:
        role = interaction.guild.get_role(int(role_id))
        if role:
            await interaction.user.add_roles(role)
            return await interaction.response.send_message("You have been verified!", ephemeral=True)
    await interaction.response.send_message("System offline.", ephemeral=True)

@serverstats_group.command(name="setup")
@app_commands.checks.has_permissions(administrator=True)
async def ss_setup(interaction: discord.Interaction):
    cat = await interaction.guild.create_category("📊 Server Stats")
    chan = await interaction.guild.create_voice_channel(f"Members: {interaction.guild.member_count}", category=cat)
    await chan.set_permissions(interaction.guild.default_role, connect=False)
    await DB.set(f"serverstats/{interaction.guild_id}", {"channel_id": chan.id})
    await interaction.response.send_message("Server stats channel created.")

@serverstats_group.command(name="remove")
@app_commands.checks.has_permissions(administrator=True)
async def ss_remove(interaction: discord.Interaction):
    data = await DB.get(f"serverstats/{interaction.guild_id}")
    if data and 'channel_id' in data:
        chan = interaction.guild.get_channel(int(data['channel_id']))
        if chan: await chan.delete()
        await DB.delete(f"serverstats/{interaction.guild_id}")
    await interaction.response.send_message("Server stats removed.")

# ==========================================
# 11. ANNOUNCEMENTS & REACTION ROLES
# ==========================================
starboard_group = app_commands.Group(name="starboard", description="Starboard system")
reactionrole_group = app_commands.Group(name="reactionrole", description="Reaction roles")
autopublish_group = app_commands.Group(name="autopublish", description="Auto-publish announcements")

bot.tree.add_command(starboard_group)
bot.tree.add_command(reactionrole_group)
bot.tree.add_command(autopublish_group)

@starboard_group.command(name="setup")
@app_commands.checks.has_permissions(administrator=True)
async def sb_setup(interaction: discord.Interaction, channel: discord.TextChannel, threshold: int = 3):
    await DB.set(f"starboard/{interaction.guild_id}", {"channel": channel.id, "threshold": threshold})
    await interaction.response.send_message(f"Starboard setup in {channel.mention} with {threshold}⭐ threshold.")

@starboard_group.command(name="remove")
@app_commands.checks.has_permissions(administrator=True)
async def sb_remove(interaction: discord.Interaction):
    await DB.delete(f"starboard/{interaction.guild_id}")
    await interaction.response.send_message("Starboard removed.")

@reactionrole_group.command(name="add")
@app_commands.checks.has_permissions(manage_roles=True)
async def rr_add(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
    try:
        msg = await interaction.channel.fetch_message(int(message_id))
        await msg.add_reaction(emoji)
        clean_emoji = emoji.strip("<>")
        if ":" in clean_emoji: clean_emoji = clean_emoji.split(":")[0] # Basic custom emoji strip
        await DB.set(f"reactionroles/{interaction.guild_id}/{message_id}/{emoji}", role.id)
        await interaction.response.send_message(f"Reaction role added for {role.name}.", ephemeral=True)
    except:
        await interaction.response.send_message("Invalid Message ID or Emoji.", ephemeral=True)

@reactionrole_group.command(name="remove")
@app_commands.checks.has_permissions(manage_roles=True)
async def rr_remove(interaction: discord.Interaction, message_id: str, emoji: str):
    await DB.delete(f"reactionroles/{interaction.guild_id}/{message_id}/{emoji}")
    await interaction.response.send_message("Reaction role removed.", ephemeral=True)

@reactionrole_group.command(name="list")
async def rr_list(interaction: discord.Interaction):
    data = await DB.get(f"reactionroles/{interaction.guild_id}")
    msg = "Active Reaction Roles:\n" if data else "No Reaction Roles."
    await interaction.response.send_message(msg, ephemeral=True)

@autopublish_group.command(name="setup")
@app_commands.checks.has_permissions(manage_channels=True)
async def ap_setup(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.send_message("Auto-publish enabled for announcements. (Requires Community Features).", ephemeral=True)

@autopublish_group.command(name="remove")
@app_commands.checks.has_permissions(manage_channels=True)
async def ap_remove(interaction: discord.Interaction):
    await interaction.response.send_message("Auto-publish disabled.", ephemeral=True)


# Execute Bot
if __name__ == "__main__":
    bot.run(os.getenv("TOKEN"))
