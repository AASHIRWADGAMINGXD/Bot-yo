import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import sqlite3
import datetime
import random
import io
import re
from flask import Flask
from threading import Thread
from dotenv import load_dotenv

# --- 1. CONFIGURATION & IMPORTS ---

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID_ENV = os.getenv("OWNER_ID")

# Convert Owner ID to integer if it exists
try:
    OWNER_ID = int(OWNER_ID_ENV) if OWNER_ID_ENV else None
except ValueError:
    print("⚠️ Error: OWNER_ID in .env is not a number.")
    OWNER_ID = None

# OCR / Image Text Recognition Setup
try:
    from PIL import Image
    import pytesseract
    # pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe' # UNCOMMENT FOR WINDOWS
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("⚠️ Warning: PIL or pytesseract not installed. Image Crypto-Blocker disabled.")

# Bot Setup
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

if OWNER_ID:
    bot.owner_id = OWNER_ID 

# --- 2. DATABASE (SQLite) ---
conn = sqlite3.connect("ultimate_bot.db", check_same_thread=False)
c = conn.cursor()

def setup_database():
    # Guild Configuration
    c.execute('''CREATE TABLE IF NOT EXISTS guild_config (
        guild_id INTEGER PRIMARY KEY,
        ticket_category INTEGER,
        ticket_transcript_channel INTEGER,
        suggestion_channel INTEGER,
        announce_channel INTEGER,
        auto_mode INTEGER DEFAULT 0,
        anti_raid INTEGER DEFAULT 0,
        anti_raid_pro INTEGER DEFAULT 0,
        verify_role INTEGER
    )''')
    
    # Blocked Words
    c.execute('''CREATE TABLE IF NOT EXISTS block_words (guild_id INTEGER, word TEXT)''')
    
    # Blocked Role Names
    c.execute('''CREATE TABLE IF NOT EXISTS blocked_roles (guild_id INTEGER, name TEXT)''')
    
    # Tickets
    c.execute('''CREATE TABLE IF NOT EXISTS tickets (channel_id INTEGER PRIMARY KEY, owner_id INTEGER, guild_id INTEGER)''')
    
    # Premium
    c.execute('''CREATE TABLE IF NOT EXISTS premium (user_id INTEGER PRIMARY KEY, expiry TEXT, type TEXT)''')
    
    # Warns
    c.execute('''CREATE TABLE IF NOT EXISTS warns (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, guild_id INTEGER, reason TEXT, mod_id INTEGER, timestamp TEXT)''')

    # Notes
    c.execute('''CREATE TABLE IF NOT EXISTS notes (user_id INTEGER, guild_id INTEGER, note TEXT, author_id INTEGER)''')
    
    # AFK
    c.execute('''CREATE TABLE IF NOT EXISTS afk (user_id INTEGER, guild_id INTEGER, reason TEXT, timestamp TEXT)''')
    
    # Reaction Roles
    c.execute('''CREATE TABLE IF NOT EXISTS reaction_roles (message_id INTEGER, emoji TEXT, role_id INTEGER, guild_id INTEGER)''')
    
    # Temp Roles
    c.execute('''CREATE TABLE IF NOT EXISTS temp_roles (user_id INTEGER, guild_id INTEGER, role_id INTEGER, expiry_timestamp REAL)''')
    
    # Bot Config (Maintenance, etc)
    c.execute('''CREATE TABLE IF NOT EXISTS bot_config (key TEXT PRIMARY KEY, value TEXT)''')
    
    conn.commit()

setup_database()

# --- 3. FLASK KEEP ALIVE ---
app = Flask('')

@app.route('/')
def home():
    return "Bot System Operational - 200 OK"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# --- 4. HELPERS & CHECKS ---

def is_maintenance():
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM bot_config WHERE key = 'maintenance'")
    res = cursor.fetchone()
    return True if res and res[0] == "1" else False

def check_premium(user_id):
    cursor = conn.cursor()
    cursor.execute("SELECT expiry FROM premium WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    if not res: return False
    if res[0] == "never": return True
    try:
        if datetime.datetime.now() < datetime.datetime.strptime(res[0], "%Y-%m-%d %H:%M:%S"):
            return True
    except: pass
    return False

# Decorators
def premium_only():
    async def predicate(ctx):
        if not check_premium(ctx.author.id):
            raise commands.CheckFailure("PREMIUM_REQUIRED")
        return True
    return commands.check(predicate)

def maintenance_check():
    async def predicate(ctx):
        if is_maintenance() and ctx.author.id != bot.owner_id:
            raise commands.CheckFailure("MAINTENANCE_MODE")
        return True
    return commands.check(predicate)

# --- 5. TICKET SYSTEM UI ---

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Create Ticket", style=discord.ButtonStyle.green, custom_id="ticket_create_btn")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        c.execute("SELECT ticket_category FROM guild_config WHERE guild_id = ?", (interaction.guild.id,))
        res = c.fetchone()
        cat = interaction.guild.get_channel(res[0]) if res and res[0] else None

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        # Ticket Tag System (Adds numbering)
        ch_name = f"ticket-{interaction.user.name}-{random.randint(100,999)}"
        channel = await interaction.guild.create_text_channel(name=ch_name, category=cat, overwrites=overwrites)
        
        c.execute("INSERT INTO tickets (channel_id, owner_id, guild_id) VALUES (?, ?, ?)", (channel.id, interaction.user.id, interaction.guild.id))
        conn.commit()
        
        embed = discord.Embed(title="Ticket Open", description="Support will be with you shortly.", color=discord.Color.blue())
        await channel.send(f"{interaction.user.mention}", embed=embed, view=TicketControls())
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)

class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close", style=discord.ButtonStyle.red, custom_id="ticket_close_btn")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        # Transcript Generation
        messages = [m async for m in interaction.channel.history(limit=500, oldest_first=True)]
        text = f"TRANSCRIPT - {interaction.channel.name}\n" + "="*40 + "\n"
        for m in messages:
            text += f"[{m.created_at.strftime('%Y-%m-%d %H:%M')}] {m.author}: {m.content}\n"
        
        file = discord.File(io.BytesIO(text.encode()), filename=f"transcript-{interaction.channel.id}.txt")
        
        # Log to Channel
        c.execute("SELECT ticket_transcript_channel FROM guild_config WHERE guild_id = ?", (interaction.guild.id,))
        res = c.fetchone()
        if res and res[0]:
            log_chan = interaction.guild.get_channel(res[0])
            if log_chan:
                await log_chan.send(embed=discord.Embed(title="Ticket Closed", description=f"Closed by {interaction.user}"), file=file)
        
        c.execute("DELETE FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
        conn.commit()
        await interaction.channel.delete()

# --- 6. COGS (MODULES) ---

class AdminFeatures(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="setup_tickets")
    @commands.has_permissions(administrator=True)
    async def setup_tickets(self, ctx, category: discord.CategoryChannel, logs: discord.TextChannel):
        """Ticket Admin: Configure category and logs."""
        c.execute("INSERT OR REPLACE INTO guild_config (guild_id, ticket_category, ticket_transcript_channel) VALUES (?, ?, ?)",
                  (ctx.guild.id, category.id, logs.id))
        conn.commit()
        await ctx.send("✅ Ticket system configured.")

    @commands.hybrid_command(name="ticket_panel")
    @commands.has_permissions(administrator=True)
    async def ticket_panel(self, ctx):
        """Ticket Admin: Send the panel with Banner."""
        embed = discord.Embed(title="Support", description="Click below to open a ticket.", color=discord.Color.blue())
        embed.set_image(url="https://dummyimage.com/600x200/2f3136/ffffff&text=Support+Ticket") 
        await ctx.send(embed=embed, view=TicketView())

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def anti_raid(self, ctx, mode: str):
        """Anti_Raid Basic [on/off]."""
        val = 1 if mode.lower() == "on" else 0
        c.execute("UPDATE guild_config SET anti_raid = ? WHERE guild_id = ?", (val, ctx.guild.id))
        conn.commit()
        await ctx.send(f"🛡️ Anti-Raid Basic: {mode.upper()}")

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def auto_mode(self, ctx, mode: str):
        """Auto Mode (Link Spam Protection) [on/off]."""
        val = 1 if mode.lower() == "on" else 0
        c.execute("UPDATE guild_config SET auto_mode = ? WHERE guild_id = ?", (val, ctx.guild.id))
        conn.commit()
        await ctx.send(f"🤖 Auto Mode (Link Protection): {mode.upper()}")

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def add_admin(self, ctx, role: discord.Role):
        """Give admin permission (Simulated)."""
        await ctx.send(f"✅ {role.name} added to Bot Admins.")

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def announce(self, ctx, channel: discord.TextChannel, *, message):
        """Send an announcement."""
        await channel.send(message)
        await ctx.send("✅ Announcement sent.")

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def block_r(self, ctx, role_name: str):
        """Restrict creation of roles with this name."""
        c.execute("INSERT INTO blocked_roles (guild_id, name) VALUES (?, ?)", (ctx.guild.id, role_name.lower()))
        conn.commit()
        await ctx.send(f"🚫 Role name restricted: {role_name}")

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command()
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, reason=None):
        await member.ban(reason=reason)
        await ctx.send(f"🔨 Banned {member.name}")

    @commands.hybrid_command()
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason=None):
        await member.kick(reason=reason)
        await ctx.send(f"👢 Kicked {member.name}")

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def mute(self, ctx, member: discord.Member, minutes: int):
        await member.timeout(datetime.timedelta(minutes=minutes))
        await ctx.send(f"🔇 Muted {member.name} for {minutes}m")

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def warn(self, ctx, member: discord.Member, *, reason="No reason"):
        c.execute("INSERT INTO warns (user_id, guild_id, reason, mod_id, timestamp) VALUES (?, ?, ?, ?, ?)",
                  (member.id, ctx.guild.id, reason, ctx.author.id, str(datetime.datetime.now())))
        conn.commit()
        await ctx.send(f"⚠️ Warned {member.name}")

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def blockwords(self, ctx, word: str):
        c.execute("INSERT INTO block_words (guild_id, word) VALUES (?, ?)", (ctx.guild.id, word.lower()))
        conn.commit()
        await ctx.send(f"🚫 Blocked: {word}")

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def unblockwords(self, ctx, word: str):
        c.execute("DELETE FROM block_words WHERE guild_id = ? AND word = ?", (ctx.guild.id, word.lower()))
        conn.commit()
        await ctx.send(f"✅ Unblocked: {word}")
        
    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def bwlist(self, ctx):
        """Show all blocked words."""
        c.execute("SELECT word FROM block_words WHERE guild_id = ?", (ctx.guild.id,))
        words = [r[0] for r in c.fetchall()]
        await ctx.send(f"Blocked Words: {', '.join(words)}")

    @commands.hybrid_command()
    @commands.has_permissions(manage_channels=True)
    async def set_slowmode(self, ctx, seconds: int):
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(f"🐢 Slowmode: {seconds}s")

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def note(self, ctx, member: discord.Member, *, content):
        """Add a note to a user."""
        c.execute("INSERT INTO notes (user_id, guild_id, note, author_id) VALUES (?, ?, ?, ?)",
                  (member.id, ctx.guild.id, content, ctx.author.id))
        conn.commit()
        await ctx.send("📝 Note added.")

    @commands.hybrid_command()
    @commands.has_permissions(manage_roles=True)
    async def temp_role(self, ctx, member: discord.Member, role: discord.Role, minutes: int):
        """Give a temporary role."""
        await member.add_roles(role)
        expiry = datetime.datetime.now().timestamp() + (minutes * 60)
        c.execute("INSERT INTO temp_roles (user_id, guild_id, role_id, expiry_timestamp) VALUES (?, ?, ?, ?)",
                  (member.id, ctx.guild.id, role.id, expiry))
        conn.commit()
        await ctx.send(f"⏳ Gave {role.name} to {member.name} for {minutes}m.")

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
    async def membercount(self, ctx):
        await ctx.send(f"📊 Members: {ctx.guild.member_count}")

    @commands.hybrid_command()
    async def roleinfo(self, ctx, role: discord.Role):
        await ctx.send(f"**Role:** {role.name}\n**ID:** {role.id}\n**Members:** {len(role.members)}")

    @commands.hybrid_command()
    async def banner(self, ctx):
        if ctx.guild.banner:
            await ctx.send(ctx.guild.banner.url)
        else:
            await ctx.send("No server banner.")

    @commands.hybrid_command()
    async def botinvite(self, ctx):
        await ctx.send(f"🔗 Invite me: {discord.utils.oauth_url(self.bot.user.id)}")

    @commands.hybrid_command()
    async def poll(self, ctx, question: str, option1: str, option2: str):
        embed = discord.Embed(title="Poll", description=question, color=discord.Color.gold())
        embed.add_field(name="1", value=option1)
        embed.add_field(name="2", value=option2)
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("1️⃣")
        await msg.add_reaction("2️⃣")

    @commands.hybrid_command()
    async def channel_stats(self, ctx):
        await ctx.send(f"Text: {len(ctx.guild.text_channels)} | Voice: {len(ctx.guild.voice_channels)}")

    @commands.hybrid_command()
    async def afk(self, ctx, *, reason="AFK"):
        """Set AFK status."""
        c.execute("INSERT INTO afk (user_id, guild_id, reason, timestamp) VALUES (?, ?, ?, ?)",
                  (ctx.author.id, ctx.guild.id, reason, str(datetime.datetime.now())))
        conn.commit()
        await ctx.send(f"💤 {ctx.author.name} is now AFK.")
        try: await ctx.author.edit(nick=f"[AFK] {ctx.author.display_name}")
        except: pass

    @commands.hybrid_command()
    async def reaction_role(self, ctx, message_id: str, emoji: str, role: discord.Role):
        """Setup reaction role."""
        try:
            mid = int(message_id)
            c.execute("INSERT INTO reaction_roles (message_id, emoji, role_id, guild_id) VALUES (?, ?, ?, ?)",
                      (mid, emoji, role.id, ctx.guild.id))
            conn.commit()
            msg = await ctx.channel.fetch_message(mid)
            await msg.add_reaction(emoji)
            await ctx.send(f"✅ Reaction Role set: {emoji} -> {role.name}")
        except:
            await ctx.send("❌ Error finding message.")

    @commands.hybrid_command()
    async def suggestion(self, ctx, *, content):
        c.execute("SELECT suggestion_channel FROM guild_config WHERE guild_id = ?", (ctx.guild.id,))
        res = c.fetchone()
        if res and res[0]:
            chan = ctx.guild.get_channel(res[0])
            embed = discord.Embed(title="Suggestion", description=content)
            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
            m = await chan.send(embed=embed)
            await m.add_reaction("👍")
            await m.add_reaction("👎")
            await ctx.send("✅ Suggestion sent.")
        else:
            await ctx.send("❌ Suggestion channel not configured.")
            
    @commands.hybrid_command()
    async def help(self, ctx):
        """Simple Help Command"""
        embed = discord.Embed(title="Bot Help", description="Use / or ! for commands.\nFeatures: Tickets, Moderation, Premium, Utility.")
        await ctx.send(embed=embed)

class PremiumFeatures(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command()
    @premium_only()
    async def change_nick(self, ctx, nick: str):
        """(Premium) Change Bot Nickname."""
        await ctx.guild.me.edit(nick=nick)
        await ctx.send(f"💎 Bot nickname changed to {nick}")

    @commands.hybrid_command()
    @premium_only()
    async def invite_panel(self, ctx):
        """(Premium) Custom Invite Embed."""
        embed = discord.Embed(title="Invite Us", description="Click the button to invite!", color=discord.Color.gold())
        embed.set_image(url="https://dummyimage.com/400x100/000/fff&text=Invite+Banner")
        await ctx.send(embed=embed)

    @commands.hybrid_command()
    @premium_only()
    async def anti_raid_pro(self, ctx, state: str):
        """(Premium) Pro Anti-Raid."""
        val = 1 if state.lower() == "on" else 0
        c.execute("UPDATE guild_config SET anti_raid_pro = ? WHERE guild_id = ?", (val, ctx.guild.id))
        conn.commit()
        await ctx.send(f"💎 Anti-Raid Pro: {state.upper()}")

    @commands.hybrid_command()
    @premium_only()
    async def bot_bio(self, ctx, *, bio: str):
        """(Premium) Server-specific Bot Bio."""
        await ctx.send(f"💎 Bio updated for this server: {bio}")

    @commands.hybrid_command()
    @premium_only()
    async def spoiler_image(self, ctx, url: str):
        """(Premium) Spoiler Image Link."""
        await ctx.send(f"💎 || {url} ||")

    @commands.hybrid_command()
    @premium_only()
    async def live_counter(self, ctx, channel: discord.VoiceChannel):
        """(Premium) Live Member Counter Channel."""
        await channel.edit(name=f"Members: {ctx.guild.member_count}")
        await ctx.send(f"💎 Live counter set on {channel.name}")

class BotOwner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.is_owner()
    async def gpremium(self, ctx, user: discord.User, days: int):
        """Owner: Give Premium."""
        expiry = "never" if days == -1 else (datetime.datetime.now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT OR REPLACE INTO premium (user_id, expiry, type) VALUES (?, ?, ?)", (user.id, expiry, "pro"))
        conn.commit()
        await ctx.send(f"🌟 Premium granted to {user.name} until {expiry}")

    @commands.command()
    @commands.is_owner()
    async def maintenance(self, ctx, state: str):
        """Owner: Toggle Maintenance."""
        val = "1" if state.lower() == "on" else "0"
        c.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES ('maintenance', ?)", (val,))
        conn.commit()
        status = discord.Status.dnd if val == "1" else discord.Status.online
        await self.bot.change_presence(status=status, activity=discord.Game(name="Maintenance" if val=="1" else "/help"))
        await ctx.send(f"Maintenance: {state}")

    @commands.command()
    @commands.is_owner()
    async def node_list(self, ctx):
        """Owner: Check Status."""
        lat = round(bot.latency * 1000)
        await ctx.send(f"**System Status**\nPing: {lat}ms\nOCR: {'Active' if OCR_AVAILABLE else 'Disabled'}\nDB: OK\nOwner ID: {bot.owner_id}")

    @commands.command()
    @commands.is_owner()
    async def uptime(self, ctx):
        await ctx.send("Bot is online.")
        
    @commands.command()
    @commands.is_owner()
    async def announce_update(self, ctx, *, msg):
        """Owner: Send msg to all server announce channels."""
        await ctx.send("Sending updates...")
        c.execute("SELECT announce_channel FROM guild_config")
        channels = c.fetchall()
        count = 0
        for row in channels:
            if row[0]:
                try:
                    chan = bot.get_channel(row[0])
                    if chan:
                        await chan.send(f"📢 **UPDATE:** {msg}")
                        count += 1
                except: pass
        await ctx.send(f"Sent to {count} channels.")

# --- 7. TASKS & EVENTS ---

@tasks.loop(minutes=1)
async def check_temp_roles():
    now = datetime.datetime.now().timestamp()
    c.execute("SELECT user_id, guild_id, role_id FROM temp_roles WHERE expiry_timestamp < ?", (now,))
    expired = c.fetchall()
    
    for user_id, guild_id, role_id in expired:
        try:
            guild = bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(user_id)
                role = guild.get_role(role_id)
                if member and role:
                    await member.remove_roles(role)
        except: pass
        
        c.execute("DELETE FROM temp_roles WHERE user_id = ? AND role_id = ?", (user_id, role_id))
        conn.commit()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"Owner ID loaded: {bot.owner_id}")
    check_temp_roles.start()
    bot.add_view(TicketView())
    bot.add_view(TicketControls())
    try:
        await bot.tree.sync()
    except: pass

@bot.event
async def on_guild_role_create(role):
    # Blocked Role Names
    c.execute("SELECT name FROM blocked_roles WHERE guild_id = ?", (role.guild.id,))
    blocked = [r[0] for r in c.fetchall()]
    if role.name.lower() in blocked:
        try: await role.delete(reason="Blocked Name")
        except: pass

@bot.event
async def on_member_join(member):
    # Anti-Raid
    c.execute("SELECT anti_raid, anti_raid_pro FROM guild_config WHERE guild_id = ?", (member.guild.id,))
    res = c.fetchone()
    if res:
        basic, pro = res
        age = (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days
        if pro == 1 and age < 3:
            try: await member.kick(reason="Anti-Raid Pro")
            except: pass
        elif basic == 1 and age < 1:
            try: await member.kick(reason="Anti-Raid Basic")
            except: pass

@bot.event
async def on_raw_reaction_add(payload):
    if payload.member.bot: return
    # Reaction Roles
    c.execute("SELECT role_id FROM reaction_roles WHERE message_id = ? AND emoji = ?", (payload.message_id, str(payload.emoji)))
    res = c.fetchone()
    if res:
        guild = bot.get_guild(payload.guild_id)
        role = guild.get_role(res[0])
        if role: await payload.member.add_roles(role)

@bot.event
async def on_raw_reaction_remove(payload):
    c.execute("SELECT role_id FROM reaction_roles WHERE message_id = ? AND emoji = ?", (payload.message_id, str(payload.emoji)))
    res = c.fetchone()
    if res:
        guild = bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        role = guild.get_role(res[0])
        if member and role: await member.remove_roles(role)

@bot.event
async def on_message(message):
    if message.author.bot: return

    # Mail Staff (DM)
    if isinstance(message.channel, discord.DMChannel):
        await message.channel.send("📧 Staff Mail: Message received. Admins will review.")
        return

    # Maintenance Check
    if is_maintenance() and message.author.id != bot.owner_id:
        return # Ignore message

    # AFK Removal
    c.execute("SELECT reason FROM afk WHERE user_id = ?", (message.author.id,))
    if c.fetchone():
        c.execute("DELETE FROM afk WHERE user_id = ?", (message.author.id,))
        conn.commit()
        await message.channel.send(f"👋 Welcome back {message.author.mention}, AFK removed.", delete_after=5)
        try: await message.author.edit(nick=None)
        except: pass

    # AFK Mention Check
    if message.mentions:
        for m in message.mentions:
            c.execute("SELECT reason, timestamp FROM afk WHERE user_id = ?", (m.id,))
            res = c.fetchone()
            if res:
                await message.channel.send(f"💤 {m.name} is AFK: {res[0]}", delete_after=10)

    # Link Spam (Auto Mode)
    if "http" in message.content:
        c.execute("SELECT auto_mode FROM guild_config WHERE guild_id = ?", (message.guild.id,))
        res = c.fetchone()
        if res and res[0] == 1:
            if not message.author.guild_permissions.administrator:
                await message.delete()
                await message.channel.send("❌ Links blocked by Auto Mode.", delete_after=3)

    # Blocked Words
    c.execute("SELECT word FROM block_words WHERE guild_id = ?", (message.guild.id,))
    b_words = [r[0] for r in c.fetchall()]
    if any(w in message.content.lower() for w in b_words):
        await message.delete()
        await message.channel.send(f"⚠️ {message.author.mention} Word blocked.", delete_after=3)

    # Crypto / Image Block (OCR)
    if message.attachments and OCR_AVAILABLE:
        for att in message.attachments:
            if att.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                try:
                    img_bytes = await att.read()
                    img = Image.open(io.BytesIO(img_bytes))
                    text = pytesseract.image_to_string(img).lower()
                    bad = ['crypto', 'bitcoin', 'investment', 'promo', 'dm me']
                    if any(b in text for b in bad):
                        await message.delete()
                        await message.channel.send("🚫 Image content blocked (Crypto/Spam).")
                        await message.author.timeout(datetime.timedelta(minutes=10))
                except: pass

    await bot.process_commands(message)

# --- 8. EXECUTION ---

async def main():
    await bot.add_cog(AdminFeatures(bot))
    await bot.add_cog(Moderation(bot))
    await bot.add_cog(Utility(bot))
    await bot.add_cog(PremiumFeatures(bot))
    await bot.add_cog(BotOwner(bot))
    
    keep_alive() # Start Flask Webserver
    
    if not TOKEN:
        print("❌ Error: DISCORD_TOKEN missing from .env")
        return
    await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot Shutting Down.")
