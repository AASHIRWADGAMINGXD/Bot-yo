import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, View, Select
import os
import json
import asyncio
import datetime
import io
import re
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv

# --- SETUP & CONFIGURATION ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# File to store all data (Database replacement)
DB_FILE = "bot_data.json"

# --- DATA MANAGER ---
def load_db():
    if not os.path.exists(DB_FILE):
        return {
            "config": {"maintenance": False, "owners": []},
            "premium": {}, # user_id: expiry_timestamp
            "blockwords": [],
            "tickets": {"count": 0, "active": {}, "panel_msg_id": None},
            "roles": {"blocked_names": [], "reaction_roles": {}},
            "settings": {"anti_raid": False, "anti_raid_pro": False, "log_channel": None},
            "afk": {},
            "warns": {},
            "notes": {},
            "admins": [], # IDs of bot admins
            "stats_channels": {}
        }
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

db = load_db()

# --- KEEP ALIVE WEB SERVER ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Alive")

def run_server():
    server = HTTPServer(('0.0.0.0', 8080), SimpleHandler)
    server.serve_forever()

def keep_alive():
    t = Thread(target=run_server)
    t.start()

# --- HELPER FUNCTIONS ---
def is_bot_admin(interaction: discord.Interaction):
    return interaction.user.id in db["admins"] or interaction.user.id == interaction.guild.owner_id

def is_premium(user_id):
    if str(user_id) in db["premium"]:
        expiry = db["premium"][str(user_id)]
        if expiry == "lifetime" or expiry > datetime.datetime.now().timestamp():
            return True
        else:
            del db["premium"][str(user_id)]
            save_db(db)
    return False

async def log_action(guild, title, description, color=discord.Color.blue()):
    # Find a channel named 'mod-logs' or user configured one
    channel = discord.utils.get(guild.text_channels, name="mod-logs")
    if channel:
        embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.datetime.now())
        await channel.send(embed=embed)

# --- TICKET SYSTEM VIEWS ---
class TicketLauncher(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.blurple, emoji="📩", custom_id="ticket_open")
    async def ticket_open(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        
        # Check if already has ticket
        for channel_id, user_id in db["tickets"]["active"].items():
            if user_id == interaction.user.id:
                await interaction.followup.send("You already have an open ticket!", ephemeral=True)
                return

        guild = interaction.guild
        db["tickets"]["count"] += 1
        save_db(db)
        
        ticket_name = f"ticket-{db['tickets']['count']:04d}"
        
        # Permissions
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        # Add staff role if exists
        staff_role = discord.utils.get(guild.roles, name="Staff")
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True)

        channel = await guild.create_text_channel(ticket_name, overwrites=overwrites)
        
        db["tickets"]["active"][str(channel.id)] = interaction.user.id
        save_db(db)

        embed = discord.Embed(title=f"Ticket #{db['tickets']['count']}", description="Support will be with you shortly.\nClick 🔒 to close.", color=discord.Color.green())
        await channel.send(content=f"{interaction.user.mention}", embed=embed, view=TicketControls())
        await interaction.followup.send(f"Ticket created: {channel.mention}", ephemeral=True)

class TicketControls(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, emoji="🔒", custom_id="ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        if str(interaction.channel.id) in db["tickets"]["active"]:
            del db["tickets"]["active"][str(interaction.channel.id)]
            save_db(db)
            await interaction.response.send_message("Ticket closing in 5 seconds...")
            
            # Transcript Logic (Simple Text File)
            messages = [f"{m.created_at}: {m.author}: {m.content}" async for m in interaction.channel.history(limit=500)]
            messages.reverse()
            transcript_content = "\n".join(messages)
            file = discord.File(io.StringIO(transcript_content), filename=f"transcript-{interaction.channel.name}.txt")
            
            # Send to user
            try:
                await interaction.user.send("Here is your ticket transcript.", file=file)
            except:
                pass
            
            await asyncio.sleep(5)
            await interaction.channel.delete()

# --- EVENTS ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")
    keep_alive()
    bot.add_view(TicketLauncher())
    bot.add_view(TicketControls())
    
    # Sync Commands
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(e)
        
    update_stats.start()
    check_status.start()

@bot.event
async def on_guild_role_create(role):
    # Block_R feature
    if role.name in db["roles"]["blocked_names"]:
        try:
            await role.delete(reason="Role name is blocked by bot config.")
        except:
            pass

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Maintenance Check
    if db["config"]["maintenance"] and message.author.id not in db["admins"]:
        return

    # Mail Staff (DM -> Mod Logs)
    if isinstance(message.channel, discord.DMChannel):
        # Find a mutual guild where bot has 'Mail' channel
        # Simplified: Just notify console or specific server
        pass 

    # AFK System
    if str(message.author.id) in db["afk"]:
        del db["afk"][str(message.author.id)]
        save_db(db)
        await message.channel.send(f"Welcome back {message.author.mention}, removed your AFK.", delete_after=5)

    for mention in message.mentions:
        if str(mention.id) in db["afk"]:
            msg = db["afk"][str(mention.id)]
            await message.channel.send(f"{mention.name} is AFK: {msg}", delete_after=5)

    # Blockwords System
    msg_content_lower = message.content.lower()
    for word in db["blockwords"]:
        if word in msg_content_lower:
            await message.delete()
            await message.channel.send(f"{message.author.mention} That word is not allowed!", delete_after=3)
            return

    # Crypto/Promotion Image Check (Basic Keyword in Filename/OCR Simulation)
    # Note: Real OCR requires Tesseract installed on the server. We will use filename checks and basic text analysis.
    banned_img_terms = ["crypto", "btc", "eth", "promo", "investment", "giveaway_winner"]
    
    suspicious = False
    if any(term in msg_content_lower for term in banned_img_terms):
        suspicious = True
        
    for attachment in message.attachments:
        if any(term in attachment.filename.lower() for term in banned_img_terms):
            suspicious = True
            
    if suspicious:
        await message.delete()
        try:
            await message.author.timeout(datetime.timedelta(minutes=10), reason="Auto-Mod: Suspicious Crypto/Promo content")
            await message.channel.send(f"{message.author.mention} has been muted for posting potential crypto/scam content.")
        except:
            await message.channel.send(f"Blocked suspicious content from {message.author.mention}.")

    await bot.process_commands(message)

# --- TASKS ---
@tasks.loop(minutes=10)
async def update_stats():
    # Update Channel Stats
    for guild_id, channels in db["stats_channels"].items():
        guild = bot.get_guild(int(guild_id))
        if guild:
            member_count = guild.member_count
            # Update member count channel if exists
            # Logic would go here to edit channel name
            pass

@tasks.loop(minutes=5)
async def check_status():
    await bot.change_presence(activity=discord.Game(name=f"Watching {len(bot.guilds)} servers | /help"))

# --- COMMANDS (SLASH COMMANDS) ---

# 1. MODERATION
@bot.tree.command(name="ban", description="Ban a user")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.ban(reason=reason)
    await interaction.response.send_message(embed=discord.Embed(title="Banned", description=f"{member.mention} has been banned.\nReason: {reason}", color=discord.Color.red()))

@bot.tree.command(name="kick", description="Kick a user")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.kick(reason=reason)
    await interaction.response.send_message(embed=discord.Embed(title="Kicked", description=f"{member.mention} has been kicked.\nReason: {reason}", color=discord.Color.orange()))

@bot.tree.command(name="mute", description="Timeout a user")
@app_commands.checks.has_permissions(moderate_members=True)
async def mute(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason"):
    await member.timeout(datetime.timedelta(minutes=minutes), reason=reason)
    await interaction.response.send_message(f"{member.mention} muted for {minutes} minutes.")

@bot.tree.command(name="warn", description="Warn a user")
@app_commands.checks.has_permissions(manage_messages=True)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    uid = str(member.id)
    if uid not in db["warns"]: db["warns"][uid] = []
    db["warns"][uid].append({"reason": reason, "mod": interaction.user.id, "time": str(datetime.datetime.now())})
    save_db(db)
    await interaction.response.send_message(f"Warned {member.mention} for: {reason}")

@bot.tree.command(name="blockword", description="Add a word to blocklist")
@app_commands.checks.has_permissions(administrator=True)
async def blockword(interaction: discord.Interaction, word: str):
    if word not in db["blockwords"]:
        db["blockwords"].append(word.lower())
        save_db(db)
        await interaction.response.send_message(f"Added `{word}` to blocklist.")
    else:
        await interaction.response.send_message("Word already blocked.")

@bot.tree.command(name="unblockword", description="Remove a word from blocklist")
@app_commands.checks.has_permissions(administrator=True)
async def unblockword(interaction: discord.Interaction, word: str):
    if word in db["blockwords"]:
        db["blockwords"].remove(word.lower())
        save_db(db)
        await interaction.response.send_message(f"Removed `{word}` from blocklist.")
    else:
        await interaction.response.send_message("Word not found.")

@bot.tree.command(name="bwlist", description="Show blocked words")
@app_commands.checks.has_permissions(manage_messages=True)
async def bwlist(interaction: discord.Interaction):
    await interaction.response.send_message(f"Blocked Words: {', '.join(db['blockwords'])}", ephemeral=True)

@bot.tree.command(name="set_slowmode", description="Set channel slowmode")
@app_commands.checks.has_permissions(manage_channels=True)
async def set_slowmode(interaction: discord.Interaction, seconds: int):
    await interaction.channel.edit(slowmode_delay=seconds)
    await interaction.response.send_message(f"Slowmode set to {seconds} seconds.")

@bot.tree.command(name="lock_role_creation", description="Prevent a role name from being created")
@app_commands.checks.has_permissions(administrator=True)
async def block_r(interaction: discord.Interaction, name: str):
    db["roles"]["blocked_names"].append(name)
    save_db(db)
    await interaction.response.send_message(f"The role name `{name}` is now restricted.")

# 2. TICKETS & UTILITY
@bot.tree.command(name="setup_tickets", description="Create the ticket panel")
@app_commands.checks.has_permissions(administrator=True)
async def setup_tickets(interaction: discord.Interaction):
    embed = discord.Embed(title="Support Tickets", description="Click the button below to open a ticket.", color=discord.Color.blue())
    embed.set_image(url="https://dummyimage.com/600x200/000/fff&text=Support+Banner") # Placeholder banner
    await interaction.channel.send(embed=embed, view=TicketLauncher())
    await interaction.response.send_message("Ticket panel created!", ephemeral=True)

@bot.tree.command(name="avatar", description="Get user avatar")
async def avatar(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"{member.name}'s Avatar")
    embed.set_image(url=member.avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="membercount", description="Server member count")
async def membercount(interaction: discord.Interaction):
    await interaction.response.send_message(f"Total Members: {interaction.guild.member_count}")

@bot.tree.command(name="poll", description="Create a poll")
async def poll(interaction: discord.Interaction, question: str, option1: str, option2: str):
    embed = discord.Embed(title="Poll", description=question, color=discord.Color.gold())
    embed.add_field(name="Option A", value=option1)
    embed.add_field(name="Option B", value=option2)
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    await msg.add_reaction("🇦")
    await msg.add_reaction("🇧")

@bot.tree.command(name="userinfo", description="Get user info")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title="User Info", color=member.color)
    embed.add_field(name="ID", value=member.id)
    embed.add_field(name="Joined", value=member.joined_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Created", value=member.created_at.strftime("%Y-%m-%d"))
    embed.set_thumbnail(url=member.avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="afk", description="Set AFK status")
async def afk(interaction: discord.Interaction, message: str = "AFK"):
    db["afk"][str(interaction.user.id)] = message
    save_db(db)
    await interaction.response.send_message(f"Set AFK: {message}")
    await interaction.user.edit(nick=f"[AFK] {interaction.user.display_name}")

@bot.tree.command(name="set_nick", description="Change a user's nickname")
@app_commands.checks.has_permissions(manage_nicknames=True)
async def set_nick(interaction: discord.Interaction, member: discord.Member, nick: str):
    await member.edit(nick=nick)
    await interaction.response.send_message(f"Changed nickname to {nick}")

@bot.tree.command(name="announce", description="Make an announcement")
@app_commands.checks.has_permissions(manage_messages=True)
async def announce(interaction: discord.Interaction, channel: discord.TextChannel, title: str, message: str):
    embed = discord.Embed(title=title, description=message, color=discord.Color.purple())
    embed.set_footer(text=f"Announced by {interaction.user.name}")
    await channel.send(embed=embed)
    await interaction.response.send_message("Announcement sent!", ephemeral=True)

# 3. PREMIUM FEATURES
@bot.tree.command(name="spoiler_image", description="[Premium] Create a spoiler image link")
async def spoiler_image(interaction: discord.Interaction, url: str):
    if not is_premium(interaction.user.id):
        return await interaction.response.send_message("This is a Premium feature.", ephemeral=True)
    
    await interaction.response.send_message(f"|| {url} ||") # Simplified logic for single file

@bot.tree.command(name="invite_panel", description="[Premium] Create invite tracking panel")
async def invite_panel(interaction: discord.Interaction):
    if not is_premium(interaction.user.id):
        return await interaction.response.send_message("Premium only.", ephemeral=True)
    embed = discord.Embed(title="Invite Tracker", description="Invites: 0\n(Real tracking requires database)", color=discord.Color.gold())
    await interaction.response.send_message(embed=embed)

# 4. BOT ADMIN / OWNER
@bot.tree.command(name="add_admin", description="[Owner] Add bot admin")
async def add_admin(interaction: discord.Interaction, user: discord.User):
    if interaction.user.id != interaction.guild.owner_id: # Basic owner check
        return await interaction.response.send_message("Only server owner can do this.")
    
    if user.id not in db["admins"]:
        db["admins"].append(user.id)
        save_db(db)
        await interaction.response.send_message(f"Added {user.name} to bot admins.")

@bot.tree.command(name="gpremium", description="[Admin] Give premium to user")
async def gpremium(interaction: discord.Interaction, user: discord.User, days: int):
    if interaction.user.id not in db["admins"]:
        return await interaction.response.send_message("Bot Admins only.")
    
    expiry = (datetime.datetime.now() + datetime.timedelta(days=days)).timestamp()
    db["premium"][str(user.id)] = expiry
    save_db(db)
    await interaction.response.send_message(f"Given premium to {user.name} for {days} days.")

@bot.tree.command(name="node_list", description="[Admin] Check bot health")
async def node_list(interaction: discord.Interaction):
    if interaction.user.id not in db["admins"]: return
    embed = discord.Embed(title="System Status", color=discord.Color.green())
    embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)}ms")
    embed.add_field(name="Guilds", value=str(len(bot.guilds)))
    embed.add_field(name="Tickets Open", value=str(len(db["tickets"]["active"])))
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="maintenance", description="[Admin] Toggle maintenance mode")
async def maintenance(interaction: discord.Interaction, state: bool):
    if interaction.user.id not in db["admins"]: return
    db["config"]["maintenance"] = state
    save_db(db)
    status = "ON" if state else "OFF"
    await interaction.response.send_message(f"Maintenance mode is now {status}.")
    await bot.change_presence(status=discord.Status.dnd if state else discord.Status.online)

# --- NOTES SYSTEM ---
@bot.tree.command(name="note", description="Add a note to a user")
@app_commands.checks.has_permissions(manage_messages=True)
async def note(interaction: discord.Interaction, user: discord.User, content: str):
    uid = str(user.id)
    if uid not in db["notes"]: db["notes"][uid] = []
    db["notes"][uid].append(content)
    save_db(db)
    await interaction.response.send_message(f"Note added to {user.name}")

@bot.tree.command(name="read_notes", description="Read notes of a user")
@app_commands.checks.has_permissions(manage_messages=True)
async def r_note(interaction: discord.Interaction, user: discord.User):
    uid = str(user.id)
    notes = db["notes"].get(uid, ["No notes."])
    await interaction.response.send_message(f"Notes for {user.name}:\n" + "\n- ".join(notes), ephemeral=True)

# --- ANTI RAID (BASIC) ---
@bot.tree.command(name="antiraid", description="Toggle basic anti-raid")
@app_commands.checks.has_permissions(administrator=True)
async def antiraid(interaction: discord.Interaction, state: bool):
    db["settings"]["anti_raid"] = state
    save_db(db)
    await interaction.response.send_message(f"Anti-Raid set to {state}")

# --- START ---
if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_TOKEN not found in .env")
    else:
        bot.run(TOKEN)
