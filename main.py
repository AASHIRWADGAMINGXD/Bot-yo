import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import asyncio
import datetime
from collections import deque, defaultdict
from flask import Flask
from threading import Thread

# ==========================================
# CONFIGURATION & SETUP
# ==========================================

# Replace with your Bot Token if not using Environment Variables
TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")

# File to store data (blocked words, autoreplies, etc.)
DATA_FILE = "bot_data.json"

# Dynamic Slowmode Config
SLOWMODE_TRIGGER_MSG_COUNT = 5  # Messages within window to trigger slowmode
SLOWMODE_WINDOW = 5             # Time window in seconds
SLOWMODE_DELAY = 10             # Seconds of slowmode to apply
SLOWMODE_OFF_THRESHOLD = 2      # If msgs < this, turn off slowmode

# Intents
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# In-memory data storage
bot_data = {
    "blocked_words": [],
    "auto_replies": {},
    "ticket_config": {},
    "active_tickets": {}
}

# Message tracking for dynamic slowmode
# Stores timestamps: {channel_id: deque([time1, time2], maxlen=10)}
channel_traffic = defaultdict(lambda: deque(maxlen=20))

# ==========================================
# KEEP ALIVE SYSTEM (Flask)
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive and running!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ==========================================
# DATA HANDLERS
# ==========================================
def load_data():
    global bot_data
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            bot_data = json.load(f)

def save_data():
    with open(DATA_FILE, 'w') as f:
        json.dump(bot_data, f, indent=4)

# ==========================================
# TICKET SYSTEM CLASSES
# ==========================================

class TicketLauncher(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.blurple, custom_id="create_ticket_btn", emoji="📩")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        category = discord.utils.get(guild.categories, name="Tickets")
        
        if not category:
            category = await guild.create_category("Tickets")

        # Check if user already has a ticket
        existing_channel = discord.utils.get(guild.text_channels, name=f"ticket-{interaction.user.name.lower()}")
        if existing_channel:
            await interaction.response.send_message(f"You already have a ticket open: {existing_channel.mention}", ephemeral=True)
            return

        # Create Channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        channel = await guild.create_text_channel(
            name=f"ticket-{interaction.user.name}", 
            category=category, 
            overwrites=overwrites
        )

        embed = discord.Embed(
            title=f"Ticket for {interaction.user.name}",
            description="Support will be with you shortly. Click the button below to close this ticket.",
            color=discord.Color.green()
        )
        
        await channel.send(content=f"{interaction.user.mention}", embed=embed, view=TicketControls())
        await interaction.response.send_message(f"Ticket created! {channel.mention}", ephemeral=True)

class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket_btn", emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Deleting ticket in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

# ==========================================
# BOT EVENTS
# ==========================================

@bot.event
async def on_ready():
    load_data()
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    
    # Sync Slash Commands
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(e)
    
    # Register persistent views (for tickets to work after restart)
    bot.add_view(TicketLauncher())
    bot.add_view(TicketControls())

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # 1. Blocked Words Logic
    msg_content_lower = message.content.lower()
    if any(word in msg_content_lower for word in bot_data["blocked_words"]):
        await message.delete()
        await message.channel.send(f"{message.author.mention}, that word is not allowed here!", delete_after=5)
        return

    # 2. Auto Reply Logic
    if message.content in bot_data["auto_replies"]:
        await message.channel.send(bot_data["auto_replies"][message.content])

    # 3. Dynamic Slowmode Logic
    cid = message.channel.id
    now = datetime.datetime.now().timestamp()
    
    # Add current timestamp
    channel_traffic[cid].append(now)
    
    # Remove old timestamps outside the window
    while len(channel_traffic[cid]) > 0 and channel_traffic[cid][0] < now - SLOWMODE_WINDOW:
        channel_traffic[cid].popleft()
        
    count = len(channel_traffic[cid])
    
    # Apply/Remove Slowmode
    if count >= SLOWMODE_TRIGGER_MSG_COUNT:
        if message.channel.slowmode_delay != SLOWMODE_DELAY:
            await message.channel.edit(slowmode_delay=SLOWMODE_DELAY)
            await message.channel.send(f"🚦 Traffic is high! Slowmode enabled ({SLOWMODE_DELAY}s).", delete_after=5)
    elif count <= SLOWMODE_OFF_THRESHOLD:
        if message.channel.slowmode_delay != 0:
            await message.channel.edit(slowmode_delay=0)
            # await message.channel.send("🟢 Traffic normalized. Slowmode disabled.", delete_after=5)

    await bot.process_commands(message)

# ==========================================
# SLASH COMMANDS
# ==========================================

# --- UTILITY COMMANDS ---

@bot.tree.command(name="serverinfo", description="Get information about the server")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    embed = discord.Embed(title=f"{guild.name} Info", color=discord.Color.blue())
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Owner", value=guild.owner.mention, inline=True)
    embed.add_field(name="Members", value=guild.member_count, inline=True)
    embed.add_field(name="Created At", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Channels", value=len(guild.channels), inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="userinfo", description="Get information about a user")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    roles = [role.mention for role in member.roles if role.name != "@everyone"]
    
    embed = discord.Embed(title=f"User Info - {member.name}", color=member.color)
    embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Joined At", value=member.joined_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Created At", value=member.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Roles", value=", ".join(roles) if roles else "None", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="avatar", description="Get a user's avatar")
async def avatar(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"{member.name}'s Avatar", color=discord.Color.purple())
    embed.set_image(url=member.avatar.url if member.avatar else member.default_avatar.url)
    await interaction.response.send_message(embed=embed)

# --- MODERATION COMMANDS ---

@bot.tree.command(name="lock", description="Lock the current channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def lock(interaction: discord.Interaction):
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False)
    await interaction.response.send_message(f"🔒 {interaction.channel.mention} has been locked.")

@bot.tree.command(name="unlock", description="Unlock the current channel")
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock(interaction: discord.Interaction):
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=True)
    await interaction.response.send_message(f"🔓 {interaction.channel.mention} has been unlocked.")

@bot.tree.command(name="blockword", description="Add or remove a blocked word")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(action="add or remove", word="The word to block")
async def blockword(interaction: discord.Interaction, action: str, word: str):
    word = word.lower()
    if action == "add":
        if word not in bot_data["blocked_words"]:
            bot_data["blocked_words"].append(word)
            save_data()
            await interaction.response.send_message(f"Added ||{word}|| to blocked words.")
        else:
            await interaction.response.send_message("Word already blocked.", ephemeral=True)
    elif action == "remove":
        if word in bot_data["blocked_words"]:
            bot_data["blocked_words"].remove(word)
            save_data()
            await interaction.response.send_message(f"Removed ||{word}|| from blocked words.")
        else:
            await interaction.response.send_message("Word not found.", ephemeral=True)
    else:
        await interaction.response.send_message("Invalid action. Use 'add' or 'remove'.", ephemeral=True)

# --- AUTO REPLY COMMANDS ---

@bot.tree.command(name="autoreply", description="Setup auto replies")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(trigger="Message to react to", response="Bot response")
async def autoreply(interaction: discord.Interaction, trigger: str, response: str):
    bot_data["auto_replies"][trigger] = response
    save_data()
    await interaction.response.send_message(f"Auto-reply added: If someone says `{trigger}`, I say `{response}`.")

# --- ADVANCED TICKET SYSTEM ---

@bot.tree.command(name="setup_ticket", description="Create an advanced ticket panel")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    title="Embed Title", 
    description="Embed Description", 
    image_url="Large background image URL (optional)", 
    thumbnail_url="Small corner image URL (optional)"
)
async def setup_ticket(interaction: discord.Interaction, title: str, description: str, image_url: str = None, thumbnail_url: str = None):
    embed = discord.Embed(title=title, description=description, color=discord.Color.gold())
    
    if image_url:
        embed.set_image(url=image_url)
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
        
    embed.set_footer(text="Powered by All-In-One Bot")
    
    await interaction.channel.send(embed=embed, view=TicketLauncher())
    await interaction.response.send_message("Ticket panel created!", ephemeral=True)

# ==========================================
# ERROR HANDLING
# ==========================================
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You don't have permission to do that.", ephemeral=True)
    else:
        await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)

# ==========================================
# START
# ==========================================

if __name__ == '__main__':
    # Start the Keep Alive Web Server
    keep_alive()
    
    # Run the Bot
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"Error starting bot: {e}")
        print("Check if your Token is correct and intents are enabled.")
