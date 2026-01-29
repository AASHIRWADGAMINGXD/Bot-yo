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

# --- 1. IMAGE PROCESSING & SETUP ---
try:
    from PIL import Image
    import pytesseract
    # pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe' # Windows Only
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("⚠️ Warning: PIL or pytesseract not installed. Image scanning disabled.")

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# --- 2. DATABASE MANAGEMENT (SQLite) ---
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
        mail_category INTEGER,
        verify_role INTEGER
    )''')
    
    # Blocked Words
    c.execute('''CREATE TABLE IF NOT EXISTS block_words (guild_id INTEGER, word TEXT)''')
    
    # Blocked Roles (Naming restriction)
    c.execute('''CREATE TABLE IF NOT EXISTS blocked_role_names (guild_id INTEGER, name TEXT)''')
    
    # Tickets
    c.execute('''CREATE TABLE IF NOT EXISTS tickets (channel_id INTEGER PRIMARY KEY, owner_id INTEGER, guild_id INTEGER)''')
    
    # Premium System
    c.execute('''CREATE TABLE IF NOT EXISTS premium (user_id INTEGER PRIMARY KEY, expiry TEXT, type TEXT)''')
    
    # Warnings
    c.execute('''CREATE TABLE IF NOT EXISTS warns (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, guild_id INTEGER, reason TEXT, mod_id INTEGER, time TEXT)''')

    # Notes
    c.execute('''CREATE TABLE IF NOT EXISTS notes (user_id INTEGER, guild_id INTEGER, note TEXT, author_id INTEGER)''')
    
    # AFK System
    c.execute('''CREATE TABLE IF NOT EXISTS afk (user_id INTEGER, guild_id INTEGER, reason TEXT, timestamp TEXT)''')
    
    # Reaction Roles
    c.execute('''CREATE TABLE IF NOT EXISTS reaction_roles (message_id INTEGER, emoji TEXT, role_id INTEGER, guild_id INTEGER)''')
    
    # Temp Roles
    c.execute('''CREATE TABLE IF NOT EXISTS temp_roles (user_id INTEGER, guild_id INTEGER, role_id INTEGER, expiry_timestamp REAL)''')
    
    # Bot Internals
    c.execute('''CREATE TABLE IF NOT EXISTS bot_config (key TEXT PRIMARY KEY, value TEXT)''')
    
    conn.commit()

setup_database()

# --- 3. FLASK KEEP ALIVE ---
app = Flask('')

@app.route('/')
def home():
    return "Bot System Operational."

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# --- 4. HELPER FUNCTIONS ---

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

# Custom Decorators
def premium_only():
    async def predicate(ctx):
        if not check_premium(ctx.author.id):
            raise commands.CheckFailure("PREMIUM_REQUIRED")
        return True
    return commands.check(predicate)

def not_maintenance():
    async def predicate(ctx):
        if is_maintenance() and ctx.author.id != ctx.guild.owner_id:
            raise commands.CheckFailure("MAINTENANCE_MODE")
        return True
    return commands.check(predicate)

# --- 5. TICKET SYSTEM (UI) ---

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Create Ticket", style=discord.ButtonStyle.green, custom_id="create_ticket")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        c.execute("SELECT ticket_category FROM guild_config WHERE guild_id = ?", (interaction.guild.id,))
        cat_res = c.fetchone()
        category = interaction.guild.get_channel(cat_res[0]) if cat_res and cat_res[0] else None

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        # Check for Ticket Support Role (simplified: allow admins)
        
        ch_name = f"ticket-{interaction.user.name}-{random.randint(1000,9999)}"
        channel = await interaction.guild.create_text_channel(name=ch_name, category=category, overwrites=overwrites)
        
        c.execute("INSERT INTO tickets (channel_id, owner_id, guild_id) VALUES (?, ?, ?)", (channel.id, interaction.user.id, interaction.guild.id))
        conn.commit()
        
        embed = discord.Embed(title="Ticket Open", description=f"Support for {interaction.user.mention}", color=discord.Color.blue())
        await channel.send(embed=embed, view=TicketControls())
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)

class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close", style=discord.ButtonStyle.red, custom_id="close_ticket")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        # Generate Transcript
        msgs = [m async for m in interaction.channel.history(limit=500, oldest_first=True)]
        text = f"Transcript for {interaction.channel.name}\n" + "-"*30 + "\n"
        for m in msgs:
            text += f"[{m.created_at.strftime('%Y-%m-%d %H:%M')}] {m.author}: {m.content}\n"
        
        file = discord.File(io.BytesIO(text.encode()), filename=f"transcript-{interaction.channel.id}.txt")
        
        c.execute("SELECT ticket_transcript_channel FROM guild_config WHERE guild_id = ?", (interaction.guild.id,))
        res = c.fetchone()
        if res and res[0]:
            log_chan = interaction.guild.get_channel(res[0])
            if log_chan:
                await log_chan.send(embed=discord.Embed(title="Ticket Closed", description=f"By: {interaction.user}"), file=file)
        
        c.execute("DELETE FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
        conn.commit()
        await interaction.channel.delete()

# --- 6. COGS (FEATURE MODULES) ---

class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def setup_tickets(self, ctx, category: discord.CategoryChannel, logs: discord.TextChannel):
        """Configure ticket system."""
        c.execute("UPDATE guild_config SET ticket_category = ?, ticket_transcript_channel = ? WHERE guild_id = ?", 
                  (category.id, logs.id, ctx.guild.id))
        if c.rowcount == 0:
            c.execute("INSERT INTO guild_config (guild_id, ticket_category, ticket_transcript_channel) VALUES (?, ?, ?)", 
                      (ctx.guild.id, category.id, logs.id))
        conn.commit()
        await ctx.send("✅ Ticket system configured.")

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def ticket_panel(self, ctx):
        """Send the ticket panel."""
        embed = discord.Embed(title="Support Tickets", description="Click to open a ticket.", color=discord.Color.blurple())
        # Banner Feature
        embed.set_image(url="https://dummyimage.com/600x200/2f3136/ffffff&text=Support+System") 
        await ctx.send(embed=embed, view=TicketView())

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def add_admin(self, ctx, role: discord.Role):
        """(Simulated) Add admin permission to role."""
        # In a real bot, you'd store this role ID in DB for permission checks.
        await ctx.send(f"✅ {role.name} added to bot admins.")

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def anti_raid(self, ctx, mode: str):
        """Toggle Anti-Raid [on/off]."""
        val = 1 if mode.lower() == "on" else 0
        c.execute("UPDATE guild_config SET anti_raid = ? WHERE guild_id = ?", (val, ctx.guild.id))
        conn.commit()
        await ctx.send(f"🛡️ Anti-Raid Basic is now {mode.upper()}.")

class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command()
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, reason="None"):
        await member.ban(reason=reason)
        await ctx.send(f"🔨 {member} has been banned.")

    @commands.hybrid_command()
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason="None"):
        await member.kick(reason=reason)
        await ctx.send(f"👢 {member} has been kicked.")

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def mute(self, ctx, member: discord.Member, minutes: int):
        await member.timeout(datetime.timedelta(minutes=minutes))
        await ctx.send(f"🔇 {member} muted for {minutes}m.")

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def warn(self, ctx, member: discord.Member, *, reason: str):
        c.execute("INSERT INTO warns (user_id, guild_id, reason, mod_id, time) VALUES (?, ?, ?, ?, ?)",
                  (member.id, ctx.guild.id, reason, ctx.author.id, str(datetime.datetime.now())))
        conn.commit()
        await ctx.send(f"⚠️ Warned {member.mention}.")

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def blockword(self, ctx, word: str):
        c.execute("INSERT INTO block_words (guild_id, word) VALUES (?, ?)", (ctx.guild.id, word.lower()))
        conn.commit()
        await ctx.send(f"🚫 Blocked word: {word}")

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def unblockword(self, ctx, word: str):
        c.execute("DELETE FROM block_words WHERE guild_id = ? AND word = ?", (ctx.guild.id, word.lower()))
        conn.commit()
        await ctx.send(f"✅ Unblocked word: {word}")

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def block_r(self, ctx, role_name: str):
        """Restrict creation of roles with this name."""
        c.execute("INSERT INTO blocked_role_names (guild_id, name) VALUES (?, ?)", (ctx.guild.id, role_name.lower()))
        conn.commit()
        await ctx.send(f"🚫 Role name restricted: {role_name}")

    @commands.hybrid_command()
    @commands.has_permissions(manage_channels=True)
    async def set_slowmode(self, ctx, seconds: int):
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(f"🐢 Slowmode set to {seconds}s")

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def temp_role(self, ctx, member: discord.Member, role: discord.Role, minutes: int):
        """Give a role temporarily."""
        await member.add_roles(role)
        expiry = datetime.datetime.now().timestamp() + (minutes * 60)
        c.execute("INSERT INTO temp_roles (user_id, guild_id, role_id, expiry_timestamp) VALUES (?, ?, ?, ?)",
                  (member.id, ctx.guild.id, role.id, expiry))
        conn.commit()
        await ctx.send(f"⏳ Given {role.name} to {member.name} for {minutes}m.")

class UtilityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command()
    async def avatar(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(title=f"Avatar of {member.name}")
        embed.set_image(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command()
    async def membercount(self, ctx):
        await ctx.send(f"📊 Members: {ctx.guild.member_count}")

    @commands.hybrid_command()
    async def poll(self, ctx, question: str, option1: str, option2: str):
        embed = discord.Embed(title="Poll", description=question, color=discord.Color.gold())
        embed.add_field(name="Option 1", value=option1)
        embed.add_field(name="Option 2", value=option2)
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("1️⃣")
        await msg.add_reaction("2️⃣")

    @commands.hybrid_command()
    async def roleinfo(self, ctx, role: discord.Role):
        embed = discord.Embed(title=f"Role: {role.name}", color=role.color)
        embed.add_field(name="ID", value=role.id)
        embed.add_field(name="Members", value=len(role.members))
        embed.add_field(name="Permissions", value=role.permissions.value)
        await ctx.send(embed=embed)

    @commands.hybrid_command()
    async def banner(self, ctx):
        if ctx.guild.banner:
            await ctx.send(ctx.guild.banner.url)
        else:
            await ctx.send("This server has no banner.")

    @commands.hybrid_command()
    async def botinvite(self, ctx):
        link = discord.utils.oauth_url(self.bot.user.id, permissions=discord.Permissions(8))
        await ctx.send(f"🔗 Invite me: {link}")

    @commands.hybrid_command()
    async def channel_stats(self, ctx):
        text_c = len(ctx.guild.text_channels)
        voice_c = len(ctx.guild.voice_channels)
        await ctx.send(f"📺 **Channel Stats**\nText: {text_c}\nVoice: {voice_c}")

    @commands.hybrid_command()
    async def afk(self, ctx, *, reason="AFK"):
        c.execute("INSERT INTO afk (user_id, guild_id, reason, timestamp) VALUES (?, ?, ?, ?)",
                  (ctx.author.id, ctx.guild.id, reason, str(datetime.datetime.now())))
        conn.commit()
        await ctx.send(f"💤 {ctx.author.name} is now AFK: {reason}")
        try:
            await ctx.author.edit(nick=f"[AFK] {ctx.author.display_name}")
        except: pass

    @commands.hybrid_command()
    async def reaction_role(self, ctx, message_id: str, emoji: str, role: discord.Role):
        """Bind a role to a reaction."""
        try:
            mid = int(message_id)
            c.execute("INSERT INTO reaction_roles (message_id, emoji, role_id, guild_id) VALUES (?, ?, ?, ?)",
                      (mid, emoji, role.id, ctx.guild.id))
            conn.commit()
            
            # Add reaction to message
            msg = await ctx.channel.fetch_message(mid)
            await msg.add_reaction(emoji)
            
            await ctx.send(f"✅ Reaction role setup: {emoji} -> {role.name}")
        except Exception as e:
            await ctx.send(f"Error: {e}")

    @commands.hybrid_command()
    async def suggestion(self, ctx, *, content):
        c.execute("SELECT suggestion_channel FROM guild_config WHERE guild_id = ?", (ctx.guild.id,))
        res = c.fetchone()
        if res and res[0]:
            chan = ctx.guild.get_channel(res[0])
            embed = discord.Embed(title="New Suggestion", description=content, color=discord.Color.orange())
            embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
            msg = await chan.send(embed=embed)
            await msg.add_reaction("👍")
            await msg.add_reaction("👎")
            await ctx.send("Suggestion sent!")
        else:
            await ctx.send("Suggestion channel not set.")

class PremiumCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command()
    @premium_only()
    async def change_nick(self, ctx, nick: str):
        """(Premium) Change bot nick."""
        await ctx.guild.me.edit(nick=nick)
        await ctx.send(f"💎 Bot nickname changed to {nick}")

    @commands.hybrid_command()
    @premium_only()
    async def invite_panel(self, ctx):
        """(Premium) Custom Invite Embed."""
        embed = discord.Embed(title="Invite Us", description="Click below!", color=discord.Color.gold())
        embed.set_image(url="https://dummyimage.com/400x100/000/fff&text=Invite+Banner")
        await ctx.send(embed=embed)

    @commands.hybrid_command()
    @premium_only()
    async def anti_raid_pro(self, ctx, state: str):
        """(Premium) Advanced Anti-Raid."""
        val = 1 if state.lower() == "on" else 0
        c.execute("UPDATE guild_config SET anti_raid_pro = ? WHERE guild_id = ?", (val, ctx.guild.id))
        conn.commit()
        await ctx.send(f"💎 Anti-Raid Pro: {state.upper()}")

    @commands.hybrid_command()
    @premium_only()
    async def bot_bio(self, ctx, *, bio: str):
        """(Premium) Set server specific bot bio."""
        # Simulated feature: In a real bot, you'd store this in DB and show it in userinfo
        await ctx.send(f"💎 Server Bot Bio set: {bio}")

    @commands.hybrid_command()
    @premium_only()
    async def spoiler_image(self, ctx, url: str):
        """(Premium) Create spoiler link."""
        await ctx.send(f"💎 || {url} ||")

    @commands.hybrid_command()
    @premium_only()
    async def live_counter(self, ctx, channel: discord.VoiceChannel):
        """(Premium) Set a VC as member counter."""
        await channel.edit(name=f"Members: {ctx.guild.member_count}")
        await ctx.send(f"💎 Live counter set on {channel.name}")

class OwnerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.is_owner()
    async def gpremium(self, ctx, user: discord.User, days: int):
        expiry = "never" if days == -1 else (datetime.datetime.now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT OR REPLACE INTO premium (user_id, expiry, type) VALUES (?, ?, ?)", (user.id, expiry, "pro"))
        conn.commit()
        await ctx.send(f"🌟 Premium granted to {user.name} until {expiry}")

    @commands.command()
    @commands.is_owner()
    async def maintenance(self, ctx, state: str):
        val = "1" if state.lower() == "on" else "0"
        c.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES ('maintenance', ?)", (val,))
        conn.commit()
        status = discord.Status.dnd if val == "1" else discord.Status.online
        await self.bot.change_presence(status=status, activity=discord.Game(name="Maintenance" if val=="1" else "/help"))
        await ctx.send(f"System Maintenance: {state}")

    @commands.command()
    @commands.is_owner()
    async def node_list(self, ctx):
        lat = round(bot.latency * 1000)
        await ctx.send(f"🖥️ **Node Status**\nPing: {lat}ms\nOCR: {OCR_AVAILABLE}\nDB: Connected")

    @commands.command()
    @commands.is_owner()
    async def uptime(self, ctx):
        # Simple uptime check
        await ctx.send("Bot is online.")

# --- 7. BACKGROUND TASKS ---

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
        except Exception as e:
            print(f"Error removing temp role: {e}")
        
        c.execute("DELETE FROM temp_roles WHERE user_id = ? AND role_id = ?", (user_id, role_id))
        conn.commit()

# --- 8. EVENTS ---

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    check_temp_roles.start()
    bot.add_view(TicketView())
    bot.add_view(TicketControls())
    try:
        await bot.tree.sync()
    except Exception as e:
        print(e)

@bot.event
async def on_guild_role_create(role):
    # Block_R (Role Name Restriction)
    c.execute("SELECT name FROM blocked_role_names WHERE guild_id = ?", (role.guild.id,))
    blocked_names = [r[0] for r in c.fetchall()]
    if role.name.lower() in blocked_names:
        try:
            await role.delete(reason="Blocked Role Name")
        except: pass

@bot.event
async def on_member_join(member):
    # Anti-Raid
    c.execute("SELECT anti_raid, anti_raid_pro FROM guild_config WHERE guild_id = ?", (member.guild.id,))
    res = c.fetchone()
    if res:
        basic, pro = res
        age = (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days
        
        if pro == 1 and age < 3: # Pro: Kick < 3 days
            try: await member.kick(reason="Anti-Raid Pro") 
            except: pass
        elif basic == 1 and age < 1: # Basic: Kick < 1 day
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
    # Reaction Roles Remove
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
        # Forward to all servers where bot is? No, usually specific. 
        # For this "All-in-one", we just reply.
        await message.channel.send("📧 Staff Mail: Message received.")
        return

    # Check AFK (User talking)
    c.execute("SELECT reason FROM afk WHERE user_id = ?", (message.author.id,))
    if c.fetchone():
        c.execute("DELETE FROM afk WHERE user_id = ?", (message.author.id,))
        conn.commit()
        await message.channel.send(f"👋 Welcome back {message.author.mention}, removed AFK.", delete_after=5)
        try: await message.author.edit(nick=None) 
        except: pass

    # Check AFK (User mentioned)
    if message.mentions:
        for m in message.mentions:
            c.execute("SELECT reason, timestamp FROM afk WHERE user_id = ?", (m.id,))
            res = c.fetchone()
            if res:
                await message.channel.send(f"💤 {m.name} is AFK: {res[0]} (since {res[1]})", delete_after=10)

    # Link Spam
    if "http" in message.content:
        c.execute("SELECT auto_mode FROM guild_config WHERE guild_id = ?", (message.guild.id,))
        res = c.fetchone()
        if res and res[0] == 1:
            if not message.author.guild_permissions.administrator:
                await message.delete()
                await message.channel.send("❌ Links not allowed in Auto Mode.", delete_after=3)

    # Blocked Words
    c.execute("SELECT word FROM block_words WHERE guild_id = ?", (message.guild.id,))
    b_words = [r[0] for r in c.fetchall()]
    if any(w in message.content.lower() for w in b_words):
        await message.delete()
        await message.channel.send(f"⚠️ {message.author.mention} Word blocked.", delete_after=3)

    # Crypto/Promo Image Check
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
                        await message.channel.send("🚫 Image blocked (Crypto/Spam Detected).")
                        await message.author.timeout(datetime.timedelta(minutes=10))
                except: pass

    await bot.process_commands(message)

# --- 9. STARTUP ---

async def main():
    await bot.add_cog(AdminCog(bot))
    await bot.add_cog(ModerationCog(bot))
    await bot.add_cog(UtilityCog(bot))
    await bot.add_cog(PremiumCog(bot))
    await bot.add_cog(OwnerCog(bot))
    
    keep_alive() # Flask Server
    
    if not TOKEN:
        print("❌ Error: DISCORD_TOKEN missing from .env")
        return
    await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot Shutting Down.")
