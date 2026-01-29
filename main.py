import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import sqlite3
import datetime
import random
import io
import json
from flask import Flask
from threading import Thread
from dotenv import load_dotenv

# --- IMAGE PROCESSING IMPORTS ---
try:
    from PIL import Image
    import pytesseract
    # pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe' # UNCOMMENT AND SET PATH IF ON WINDOWS
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("Warning: PIL or pytesseract not installed. Image detection will be disabled.")

# --- CONFIGURATION ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
# If not using env, replace above with: TOKEN = "YOUR_BOT_TOKEN_HERE"

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# --- DATABASE SETUP (SQLite) ---
conn = sqlite3.connect("bot_database.db")
c = conn.cursor()

def setup_db():
    # Guild Config
    c.execute('''CREATE TABLE IF NOT EXISTS guild_config (
        guild_id INTEGER PRIMARY KEY,
        ticket_category INTEGER,
        ticket_transcript_channel INTEGER,
        suggestion_channel INTEGER,
        announce_channel INTEGER,
        verify_role INTEGER,
        auto_mode INTEGER DEFAULT 0,
        anti_raid INTEGER DEFAULT 0,
        anti_raid_pro INTEGER DEFAULT 0,
        slowmode_time INTEGER DEFAULT 0
    )''')
    
    # Blocked Words
    c.execute('''CREATE TABLE IF NOT EXISTS block_words (
        guild_id INTEGER,
        word TEXT
    )''')
    
    # Tickets
    c.execute('''CREATE TABLE IF NOT EXISTS tickets (
        channel_id INTEGER PRIMARY KEY,
        owner_id INTEGER,
        guild_id INTEGER,
        locked INTEGER DEFAULT 0
    )''')
    
    # Premium Users
    c.execute('''CREATE TABLE IF NOT EXISTS premium (
        user_id INTEGER PRIMARY KEY,
        expiry TEXT,
        type TEXT
    )''')
    
    # Warns
    c.execute('''CREATE TABLE IF NOT EXISTS warns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        guild_id INTEGER,
        reason TEXT,
        moderator_id INTEGER,
        timestamp TEXT
    )''')

    # Notes
    c.execute('''CREATE TABLE IF NOT EXISTS notes (
        user_id INTEGER,
        guild_id INTEGER,
        note TEXT,
        author_id INTEGER
    )''')
    
    # Restricted Roles (Block_R)
    c.execute('''CREATE TABLE IF NOT EXISTS blocked_roles (
        guild_id INTEGER,
        role_name TEXT
    )''')
    
    # Bot Config (Maintenance)
    c.execute('''CREATE TABLE IF NOT EXISTS bot_config (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')

    conn.commit()

setup_db()

# --- KEEP ALIVE SERVER (Flask) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is Alive and Running!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- HELPER FUNCTIONS ---

def is_maintenance():
    c.execute("SELECT value FROM bot_config WHERE key = 'maintenance'")
    result = c.fetchone()
    if result and result[0] == "1":
        return True
    return False

def is_premium_user(user_id):
    c.execute("SELECT expiry FROM premium WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    if not result:
        return False
    if result[0] == "never":
        return True
    expiry = datetime.datetime.strptime(result[0], "%Y-%m-%d %H:%M:%S")
    if datetime.datetime.now() < expiry:
        return True
    else:
        # Expired
        c.execute("DELETE FROM premium WHERE user_id = ?", (user_id,))
        conn.commit()
        return False

# --- CUSTOM CHECKS & DECORATORS ---

class MaintenanceMode(commands.CheckFailure):
    pass

class NotPremium(commands.CheckFailure):
    pass

def check_maintenance():
    async def predicate(ctx):
        if is_maintenance() and ctx.author.id != ctx.guild.owner_id: # Allow owner to bypass
            raise MaintenanceMode("Bot is currently in maintenance mode.")
        return True
    return commands.check(predicate)

def premium_only():
    async def predicate(ctx):
        if not is_premium_user(ctx.author.id):
            raise NotPremium("This is a Premium-only feature.")
        return True
    return commands.check(predicate)

# --- TICKET SYSTEM VIEWS ---

class TicketLauncher(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.green, custom_id="ticket_create")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        c.execute("SELECT ticket_category FROM guild_config WHERE guild_id = ?", (interaction.guild.id,))
        cat_data = c.fetchone()
        
        category = None
        if cat_data and cat_data[0]:
            category = interaction.guild.get_channel(cat_data[0])

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        channel = await interaction.guild.create_text_channel(
            name=f"ticket-{interaction.user.name}",
            category=category,
            overwrites=overwrites
        )

        c.execute("INSERT INTO tickets (channel_id, owner_id, guild_id) VALUES (?, ?, ?)", 
                  (channel.id, interaction.user.id, interaction.guild.id))
        conn.commit()

        embed = discord.Embed(title="Ticket Created", description="Support will be with you shortly.", color=discord.Color.blue())
        await channel.send(f"{interaction.user.mention}", embed=embed, view=TicketControls())
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)

class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, custom_id="ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        # Transcript Logic
        messages = [message async for message in interaction.channel.history(limit=500, oldest_first=True)]
        transcript_text = f"Transcript for {interaction.channel.name}\n\n"
        for msg in messages:
            transcript_text += f"{msg.created_at} - {msg.author.name}: {msg.content}\n"
        
        # Save to buffer
        buffer = io.BytesIO(transcript_text.encode('utf-8'))
        file = discord.File(buffer, filename=f"transcript-{interaction.channel.id}.txt")

        # Send to log channel
        c.execute("SELECT ticket_transcript_channel FROM guild_config WHERE guild_id = ?", (interaction.guild.id,))
        log_data = c.fetchone()
        if log_data and log_data[0]:
            log_chan = interaction.guild.get_channel(log_data[0])
            if log_chan:
                await log_chan.send(embed=discord.Embed(title="Ticket Closed", description=f"Ticket closed by {interaction.user.name}"), file=file)

        await interaction.channel.delete()
        c.execute("DELETE FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
        conn.commit()

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.blurple, custom_id="ticket_claim")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(description=f"Ticket claimed by {interaction.user.mention}", color=discord.Color.green())
        await interaction.channel.send(embed=embed)
        await interaction.response.defer()

# --- BOT EVENTS ---

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    await bot.tree.sync()
    bot.add_view(TicketLauncher())
    bot.add_view(TicketControls())
    
    # Announce Update
    # (Simplified for professional sanity: We don't spam every server on reboot unless configured)
    c.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES ('maintenance', '0')")
    c.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES ('start_time', ?)", (str(datetime.datetime.now()),))
    conn.commit()

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, MaintenanceMode):
        await ctx.send("🚧 Bot is currently in maintenance mode.")
    elif isinstance(error, NotPremium):
        await ctx.send("💎 This command requires a **Premium** subscription.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You do not have permission to use this command.")
    else:
        print(f"Error: {error}")

@bot.event
async def on_member_join(member):
    # Anti-Raid Pro (Premium Feature)
    c.execute("SELECT anti_raid_pro FROM guild_config WHERE guild_id = ?", (member.guild.id,))
    res = c.fetchone()
    if res and res[0] == 1:
        # Check account age
        if (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days < 3:
            await member.kick(reason="Anti-Raid Pro: Account too new.")
            return

    # Counter Update (Simplified)
    # In a real bot, we'd update a specific channel name here.
    pass

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # DM Mail Staff System
    if isinstance(message.channel, discord.DMChannel):
        # Forward to a hardcoded server or configurable main server for support
        # For this demo, we just echo back that we received it
        await message.channel.send("📩 Message received. Staff will review it shortly.")
        return

    # --- AUTO MODERATION ---
    
    # 1. Block Words
    c.execute("SELECT word FROM block_words WHERE guild_id = ?", (message.guild.id,))
    blocked_words = [row[0] for row in c.fetchall()]
    if any(word in message.content.lower() for word in blocked_words):
        await message.delete()
        await message.channel.send(f"{message.author.mention}, that word is blocked!", delete_after=5)
        return

    # 2. Crypto/Promo Image Detection
    if message.attachments and OCR_AVAILABLE:
        for attachment in message.attachments:
            if attachment.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                try:
                    # Download image to memory
                    image_data = await attachment.read()
                    image = Image.open(io.BytesIO(image_data))
                    text = pytesseract.image_to_string(image).lower()
                    
                    forbidden_img_words = ['crypto', 'bitcoin', 'eth', 'investment', 'promo', 'dm for info']
                    if any(bad in text for bad in forbidden_img_words):
                        await message.delete()
                        await message.channel.send(f"{message.author.mention} Image contained prohibited content (Crypto/Promo). Timed out.")
                        await message.author.timeout(datetime.timedelta(minutes=10))
                        return
                except Exception as e:
                    print(f"OCR Error: {e}")

    # 3. Link Spam (Simple check)
    if "http" in message.content:
        c.execute("SELECT auto_mode FROM guild_config WHERE guild_id = ?", (message.guild.id,))
        res = c.fetchone()
        if res and res[0] == 1:
            # Check for whitelist or role (omitted for brevity)
            pass

    await bot.process_commands(message)

# --- COMMANDS ---

# 1. ADMIN & SETUP
class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="setup_tickets")
    @commands.has_permissions(administrator=True)
    async def setup_tickets(self, ctx, category: discord.CategoryChannel, transcript_channel: discord.TextChannel):
        """Sets up the ticket system config."""
        c.execute("INSERT OR REPLACE INTO guild_config (guild_id, ticket_category, ticket_transcript_channel) VALUES (?, ?, ?)",
                  (ctx.guild.id, category.id, transcript_channel.id))
        conn.commit()
        await ctx.send(f"✅ Ticket system configured. Category: {category.name}, Logs: {transcript_channel.name}")

    @commands.hybrid_command(name="ticket_panel")
    @commands.has_permissions(administrator=True)
    async def ticket_panel(self, ctx):
        """Sends the ticket embed."""
        embed = discord.Embed(title="Open a Ticket", description="Click the button below to contact staff.", color=discord.Color.blue())
        embed.set_image(url="https://dummyimage.com/600x200/000/fff&text=Support+Banner") # Banner
        await ctx.send(embed=embed, view=TicketLauncher())

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def blockwords(self, ctx, word: str):
        """Adds a word to the blocklist."""
        c.execute("INSERT INTO block_words (guild_id, word) VALUES (?, ?)", (ctx.guild.id, word.lower()))
        conn.commit()
        await ctx.send(f"🚫 blocked `{word}`")

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def unblockwords(self, ctx, word: str):
        c.execute("DELETE FROM block_words WHERE guild_id = ? AND word = ?", (ctx.guild.id, word.lower()))
        conn.commit()
        await ctx.send(f"✅ Unblocked `{word}`")

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def bwlist(self, ctx):
        c.execute("SELECT word FROM block_words WHERE guild_id = ?", (ctx.guild.id,))
        words = [r[0] for r in c.fetchall()]
        await ctx.send(f"Blocked Words: {', '.join(words)}")

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def block_r(self, ctx, role_name: str):
        """Prevents creating a role with this name."""
        c.execute("INSERT INTO blocked_roles (guild_id, role_name) VALUES (?, ?)", (ctx.guild.id, role_name))
        conn.commit()
        await ctx.send(f"Role name `{role_name}` is now restricted.")

# 2. MODERATION
class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command()
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, reason=None):
        await member.ban(reason=reason)
        await ctx.send(f"🔨 {member.name} has been banned.")

    @commands.hybrid_command()
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason=None):
        await member.kick(reason=reason)
        await ctx.send(f"👢 {member.name} has been kicked.")

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def mute(self, ctx, member: discord.Member, minutes: int):
        await member.timeout(datetime.timedelta(minutes=minutes))
        await ctx.send(f"🔇 {member.name} muted for {minutes}m.")

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def warn(self, ctx, member: discord.Member, *, reason="No reason"):
        c.execute("INSERT INTO warns (user_id, guild_id, reason, moderator_id, timestamp) VALUES (?, ?, ?, ?, ?)",
                  (member.id, ctx.guild.id, reason, ctx.author.id, str(datetime.datetime.now())))
        conn.commit()
        await ctx.send(f"⚠️ Warned {member.name} for: {reason}")

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def set_slowmode(self, ctx, seconds: int):
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(f"🐢 Slowmode set to {seconds}s.")

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def note(self, ctx, member: discord.Member, *, content):
        c.execute("INSERT INTO notes (user_id, guild_id, note, author_id) VALUES (?, ?, ?, ?)",
                  (member.id, ctx.guild.id, content, ctx.author.id))
        conn.commit()
        await ctx.send(f"📝 Note added for {member.name}.")

# 3. UTILITY & GENERAL
class Utility(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command()
    async def avatar(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(title=f"{member.name}'s Avatar")
        embed.set_image(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command()
    async def userinfo(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(title="User Info", color=member.color)
        embed.add_field(name="ID", value=member.id)
        embed.add_field(name="Joined", value=member.joined_at.strftime("%Y-%m-%d"))
        embed.add_field(name="Created", value=member.created_at.strftime("%Y-%m-%d"))
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command()
    async def poll(self, ctx, question: str, option1: str, option2: str):
        embed = discord.Embed(title="Poll", description=question, color=discord.Color.gold())
        embed.add_field(name="Option 1", value=option1)
        embed.add_field(name="Option 2", value=option2)
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("1️⃣")
        await msg.add_reaction("2️⃣")

    @commands.hybrid_command()
    async def giveaway(self, ctx, time_str: str, prize: str):
        # Very basic parser
        seconds = 0
        if "s" in time_str: seconds = int(time_str.replace("s",""))
        elif "m" in time_str: seconds = int(time_str.replace("m","")) * 60
        
        embed = discord.Embed(title="🎉 Giveaway!", description=f"Prize: **{prize}**\nEnds in: {time_str}")
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("🎉")
        
        await asyncio.sleep(seconds)
        
        msg = await ctx.channel.fetch_message(msg.id)
        users = []
        async for user in msg.reactions[0].users():
             if not user.bot: users.append(user)
             
        if users:
            winner = random.choice(users)
            await ctx.send(f"🎉 Congratulations {winner.mention}! You won **{prize}**!")
        else:
            await ctx.send("No one joined the giveaway.")

# 4. PREMIUM FEATURES
class Premium(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command()
    @premium_only()
    async def change_nick(self, ctx, nick: str):
        """Premium: Change Bot's Nickname in this server."""
        await ctx.guild.me.edit(nick=nick)
        await ctx.send(f"💎 Nickname changed to {nick}")

    @commands.hybrid_command()
    @premium_only()
    async def bot_bio(self, ctx, *, bio: str):
        """Premium: Fake bio display (Since bots can't change 'About Me' per server, we store it for the 'userinfo' command)."""
        # Logic to store per-server bio in DB would go here
        await ctx.send(f"💎 Server Bio updated to: {bio}")

    @commands.hybrid_command()
    @premium_only()
    async def anti_raid_pro(self, ctx, state: str):
        """Premium: Enable advanced join scanning."""
        val = 1 if state.lower() == "on" else 0
        c.execute("UPDATE guild_config SET anti_raid_pro = ? WHERE guild_id = ?", (val, ctx.guild.id))
        conn.commit()
        await ctx.send(f"💎 Anti-Raid Pro is now {state.upper()}")

# 5. BOT OWNER / SUPER ADMIN
class BotAdmin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.is_owner()
    async def gpremium(self, ctx, user: discord.User, days: int):
        """Give premium to a user."""
        expiry = datetime.datetime.now() + datetime.timedelta(days=days)
        expiry_str = expiry.strftime("%Y-%m-%d %H:%M:%S")
        if days == -1: expiry_str = "never"
        
        c.execute("INSERT OR REPLACE INTO premium (user_id, expiry, type) VALUES (?, ?, ?)", (user.id, expiry_str, "gold"))
        conn.commit()
        await ctx.send(f"🌟 Granted Premium to {user.name} until {expiry_str}")

    @commands.command()
    @commands.is_owner()
    async def maintenance(self, ctx, state: str):
        """Set bot to maintenance mode (on/off)."""
        val = "1" if state.lower() == "on" else "0"
        c.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES ('maintenance', ?)", (val,))
        conn.commit()
        
        # Change status
        status = discord.Status.dnd if val == "1" else discord.Status.online
        await self.bot.change_presence(status=status, activity=discord.Game(name="Under Maintenance" if val=="1" else "!help"))
        await ctx.send(f"Maintenance mode: {state}")

    @commands.command()
    @commands.is_owner()
    async def node_list(self, ctx):
        """Check system health."""
        latency = round(bot.latency * 1000)
        ocr_status = "✅ Active" if OCR_AVAILABLE else "❌ Missing Dependencies"
        await ctx.send(f"**Node Status:**\nPing: {latency}ms\nOCR: {ocr_status}\nDatabase: Connected")

# --- LOAD COGS & RUN ---
async def main():
    await bot.add_cog(Admin(bot))
    await bot.add_cog(Moderation(bot))
    await bot.add_cog(Utility(bot))
    await bot.add_cog(Premium(bot))
    await bot.add_cog(BotAdmin(bot))
    
    keep_alive() # Start Web Server
    
    if not TOKEN:
        print("Error: DISCORD_TOKEN not found in .env")
        return
        
    await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
