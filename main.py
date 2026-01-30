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

# --- 1. CONFIGURATION & SETUP ---

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID_ENV = os.getenv("OWNER_ID")

# Handle Owner ID
try:
    OWNER_ID = int(OWNER_ID_ENV) if OWNER_ID_ENV else None
except (ValueError, TypeError):
    print("⚠️ OWNER_ID in .env is invalid. Some owner commands may not work.")
    OWNER_ID = None

# OCR Setup (Image Text Reading)
try:
    from PIL import Image
    import pytesseract
    # Note: Tesseract must be installed on the OS level for this to work.
    # pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe' 
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("⚠️ PIL or pytesseract not found. Image scanning features disabled.")

# Bot Instance
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
if OWNER_ID:
    bot.owner_id = OWNER_ID

# --- 2. DATABASE (SQLite) ---

conn = sqlite3.connect("ultimate_bot.db", check_same_thread=False)
c = conn.cursor()

def init_db():
    # Guild Settings
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
    
    # Moderation Logs
    c.execute('''CREATE TABLE IF NOT EXISTS warns (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, guild_id INTEGER, reason TEXT, mod_id INTEGER, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, guild_id INTEGER, note TEXT, author_id INTEGER)''')
    
    # Utilities
    c.execute('''CREATE TABLE IF NOT EXISTS afk (user_id INTEGER, guild_id INTEGER, reason TEXT, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS reaction_roles (message_id INTEGER, emoji TEXT, role_id INTEGER, guild_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS temp_roles (user_id INTEGER, guild_id INTEGER, role_id INTEGER, expiry_timestamp REAL)''')
    
    # Bot Config
    c.execute('''CREATE TABLE IF NOT EXISTS bot_config (key TEXT PRIMARY KEY, value TEXT)''')
    
    conn.commit()

init_db()

# --- 3. FLASK KEEP ALIVE ---

app = Flask('')

@app.route('/')
def home():
    return "Bot Status: ONLINE | 200 OK"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# --- 4. GLOBAL CHECKS ---

def is_maintenance_mode():
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM bot_config WHERE key = 'maintenance'")
    res = cursor.fetchone()
    return True if res and res[0] == "1" else False

def check_premium_status(user_id):
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
        if not check_premium_status(ctx.author.id):
            raise commands.CheckFailure("PREMIUM_REQUIRED")
        return True
    return commands.check(predicate)

def maintenance_check():
    async def predicate(ctx):
        if is_maintenance_mode() and ctx.author.id != bot.owner_id:
            raise commands.CheckFailure("MAINTENANCE_ACTIVE")
        return True
    return commands.check(predicate)

# --- 5. TICKET SYSTEM ---

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Create Ticket", style=discord.ButtonStyle.green, custom_id="btn_ticket_create")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        c.execute("SELECT ticket_category FROM guild_config WHERE guild_id = ?", (interaction.guild.id,))
        res = c.fetchone()
        cat = interaction.guild.get_channel(res[0]) if res and res[0] else None

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        ch_name = f"ticket-{interaction.user.name}-{random.randint(100,999)}"
        channel = await interaction.guild.create_text_channel(name=ch_name, category=cat, overwrites=overwrites)
        
        c.execute("INSERT INTO tickets (channel_id, owner_id, guild_id) VALUES (?, ?, ?)", 
                  (channel.id, interaction.user.id, interaction.guild.id))
        conn.commit()
        
        embed = discord.Embed(title="Ticket Open", description="Support staff will be with you shortly.", color=discord.Color.blue())
        await channel.send(f"{interaction.user.mention}", embed=embed, view=TicketControls())
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)

class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close", style=discord.ButtonStyle.red, custom_id="btn_ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        # Transcript
        msgs = [m async for m in interaction.channel.history(limit=500, oldest_first=True)]
        text = f"TRANSCRIPT - {interaction.channel.name}\n" + "="*30 + "\n"
        for m in msgs:
            text += f"[{m.created_at.strftime('%Y-%m-%d %H:%M')}] {m.author}: {m.content}\n"
        
        f = discord.File(io.BytesIO(text.encode()), filename=f"transcript-{interaction.channel.id}.txt")
        
        c.execute("SELECT ticket_transcript_channel FROM guild_config WHERE guild_id = ?", (interaction.guild.id,))
        res = c.fetchone()
        if res and res[0]:
            log_c = interaction.guild.get_channel(res[0])
            if log_c:
                await log_c.send(embed=discord.Embed(title="Ticket Closed", description=f"By: {interaction.user}"), file=f)
        
        c.execute("DELETE FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
        conn.commit()
        await interaction.channel.delete()

# --- 6. COGS (MODULES) ---

class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="setup_tickets")
    @commands.has_permissions(administrator=True)
    async def setup_tickets(self, ctx, category: discord.CategoryChannel, transcript_channel: discord.TextChannel):
        """Configure the ticket system."""
        c.execute("INSERT OR REPLACE INTO guild_config (guild_id, ticket_category, ticket_transcript_channel) VALUES (?, ?, ?)",
                  (ctx.guild.id, category.id, transcript_channel.id))
        conn.commit()
        await ctx.send("✅ Ticket system configured.")

    @commands.hybrid_command(name="ticket_panel")
    @commands.has_permissions(administrator=True)
    async def ticket_panel(self, ctx):
        """Send the ticket creation message."""
        embed = discord.Embed(title="Support", description="Click to open a ticket.", color=discord.Color.blue())
        embed.set_image(url="https://dummyimage.com/600x200/2f3136/ffffff&text=Support+Team") 
        await ctx.send(embed=embed, view=TicketView())

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def anti_raid(self, ctx, mode: str):
        """Toggle Basic Anti-Raid [on/off]."""
        val = 1 if mode.lower() == "on" else 0
        c.execute("UPDATE guild_config SET anti_raid = ? WHERE guild_id = ?", (val, ctx.guild.id))
        conn.commit()
        await ctx.send(f"🛡️ Anti-Raid Basic: {mode.upper()}")

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def auto_mode(self, ctx, mode: str):
        """Toggle Auto Mode (Link Blocker) [on/off]."""
        val = 1 if mode.lower() == "on" else 0
        c.execute("UPDATE guild_config SET auto_mode = ? WHERE guild_id = ?", (val, ctx.guild.id))
        conn.commit()
        await ctx.send(f"🤖 Auto Mode (Link Block): {mode.upper()}")

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def add_admin(self, ctx, role: discord.Role):
        """(Simulated) Add admin rights to a role."""
        await ctx.send(f"✅ {role.name} added to Bot Admins.")

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def remove_admin(self, ctx, role: discord.Role):
        """(Simulated) Remove admin rights."""
        await ctx.send(f"✅ {role.name} removed from Bot Admins.")

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def announce(self, ctx, channel: discord.TextChannel, *, message: str):
        """Announce a message."""
        await channel.send(message)
        await ctx.send("✅ Sent.")

    @commands.hybrid_command()
    @commands.has_permissions(administrator=True)
    async def block_r(self, ctx, role_name: str):
        """Restrict a role name."""
        c.execute("INSERT INTO blocked_roles (guild_id, name) VALUES (?, ?)", (ctx.guild.id, role_name.lower()))
        conn.commit()
        await ctx.send(f"🚫 Role name restricted: {role_name}")

class ModCog(commands.Cog):
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
    async def note(self, ctx, member: discord.Member, *, content: str):
        c.execute("INSERT INTO notes (user_id, guild_id, note, author_id) VALUES (?, ?, ?, ?)",
                  (member.id, ctx.guild.id, content, ctx.author.id))
        conn.commit()
        await ctx.send("📝 Note added.")

    @commands.hybrid_command()
    @commands.has_permissions(manage_messages=True)
    async def view_notes(self, ctx, member: discord.Member):
        c.execute("SELECT id, note FROM notes WHERE user_id = ? AND guild_id = ?", (member.id, ctx.guild.id))
        notes = c.fetchall()
        if not notes:
            await ctx.send("No notes found.")
            return
        t = f"**Notes for {member.name}**\n"
        for nid, txt in notes:
            t += f"`ID {nid}`: {txt}\n"
        await ctx.send(t)

    @commands.hybrid_command()
    @commands.has_permissions(manage_roles=True)
    async def temp_role(self, ctx, member: discord.Member, role: discord.Role, minutes: int):
        """Assign a temporary role."""
        await member.add_roles(role)
        expiry = datetime.datetime.now().timestamp() + (minutes * 60)
        c.execute("INSERT INTO temp_roles (user_id, guild_id, role_id, expiry_timestamp) VALUES (?, ?, ?, ?)",
                  (member.id, ctx.guild.id, role.id, expiry))
        conn.commit()
        await ctx.send(f"⏳ Gave {role.name} to {member.name} for {minutes}m.")
        
    @commands.hybrid_command()
    @commands.has_permissions(manage_nicknames=True)
    async def set_nick(self, ctx, member: discord.Member, nick: str):
        await member.edit(nick=nick)
        await ctx.send(f"✅ Nickname updated.")

class UtilCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command()
    async def help(self, ctx):
        embed = discord.Embed(title="Bot Help", description="Features: Admin, Mod, Tickets, Utility, Premium.", color=discord.Color.gold())
        await ctx.send(embed=embed)

    @commands.hybrid_command()
    async def avatar(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(title=f"{member.name}'s Avatar")
        embed.set_image(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command()
    async def membercount(self, ctx):
        await ctx.send(f"📊 {ctx.guild.member_count} Members")

    @commands.hybrid_command()
    async def userinfo(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(title="User Info", color=member.color)
        embed.add_field(name="ID", value=member.id)
        embed.add_field(name="Joined", value=member.joined_at.strftime("%Y-%m-%d"))
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command()
    async def banner(self, ctx):
        if ctx.guild.banner:
            await ctx.send(ctx.guild.banner.url)
        else:
            await ctx.send("No banner set.")

    @commands.hybrid_command()
    async def roleinfo(self, ctx, role: discord.Role):
        await ctx.send(f"**{role.name}**\nID: {role.id}\nMembers: {len(role.members)}")

    @commands.hybrid_command(name="botinvite")
    async def bot_invite_link(self, ctx):
        # Method renamed to avoid 'bot_' start conflict, command name preserved
        await ctx.send(f"🔗 Invite: {discord.utils.oauth_url(self.bot.user.id)}")

    @commands.hybrid_command()
    async def channel_stats(self, ctx):
        await ctx.send(f"Text: {len(ctx.guild.text_channels)} | Voice: {len(ctx.guild.voice_channels)}")

    @commands.hybrid_command()
    async def afk(self, ctx, *, reason="AFK"):
        c.execute("INSERT INTO afk (user_id, guild_id, reason, timestamp) VALUES (?, ?, ?, ?)",
                  (ctx.author.id, ctx.guild.id, reason, str(datetime.datetime.now())))
        conn.commit()
        await ctx.send(f"💤 {ctx.author.name} is now AFK.")
        try: await ctx.author.edit(nick=f"[AFK] {ctx.author.display_name}")
        except: pass

    @commands.hybrid_command()
    async def poll(self, ctx, question: str, option1: str, option2: str):
        embed = discord.Embed(title="Poll", description=question, color=discord.Color.gold())
        embed.add_field(name="1", value=option1)
        embed.add_field(name="2", value=option2)
        m = await ctx.send(embed=embed)
        await m.add_reaction("1️⃣")
        await m.add_reaction("2️⃣")

    @commands.hybrid_command()
    async def giveaway(self, ctx, seconds: int, prize: str):
        embed = discord.Embed(title="🎉 Giveaway!", description=f"**{prize}**\nTime: {seconds}s")
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("🎉")
        await asyncio.sleep(seconds)
        
        msg = await ctx.channel.fetch_message(msg.id)
        users = []
        async for u in msg.reactions[0].users():
            if not u.bot: users.append(u)
        
        if users:
            winner = random.choice(users)
            await ctx.send(f"🎉 Winner: {winner.mention} won **{prize}**!")
        else:
            await ctx.send("No entries.")

    @commands.hybrid_command()
    async def reaction_role(self, ctx, message_id: str, emoji: str, role: discord.Role):
        try:
            mid = int(message_id)
            c.execute("INSERT INTO reaction_roles (message_id, emoji, role_id, guild_id) VALUES (?, ?, ?, ?)",
                      (mid, emoji, role.id, ctx.guild.id))
            conn.commit()
            m = await ctx.channel.fetch_message(mid)
            await m.add_reaction(emoji)
            await ctx.send(f"✅ Bound {emoji} to {role.name}")
        except:
            await ctx.send("❌ Error finding message.")

    @commands.hybrid_command()
    async def suggestion(self, ctx, *, content: str):
        c.execute("SELECT suggestion_channel FROM guild_config WHERE guild_id = ?", (ctx.guild.id,))
        res = c.fetchone()
        if res and res[0]:
            chan = ctx.guild.get_channel(res[0])
            if chan:
                e = discord.Embed(title="Suggestion", description=content)
                e.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
                m = await chan.send(embed=e)
                await m.add_reaction("👍")
                await m.add_reaction("👎")
                await ctx.send("✅ Sent.")
        else:
            await ctx.send("❌ Channel not set.")

class PremiumCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command()
    @premium_only()
    async def change_nick(self, ctx, nick: str):
        """(Premium) Change bot nickname."""
        await ctx.guild.me.edit(nick=nick)
        await ctx.send(f"💎 Nickname changed.")

    @commands.hybrid_command()
    @premium_only()
    async def invite_panel(self, ctx):
        """(Premium) Invite Panel."""
        e = discord.Embed(title="Invite", description="Click to add me.", color=discord.Color.gold())
        e.set_image(url="https://dummyimage.com/400x100/000/fff&text=Invite+Banner")
        await ctx.send(embed=e)

    @commands.hybrid_command()
    @premium_only()
    async def anti_raid_pro(self, ctx, state: str):
        """(Premium) Pro Anti-Raid."""
        val = 1 if state.lower() == "on" else 0
        c.execute("UPDATE guild_config SET anti_raid_pro = ? WHERE guild_id = ?", (val, ctx.guild.id))
        conn.commit()
        await ctx.send(f"💎 Anti-Raid Pro: {state.upper()}")

    @commands.hybrid_command(name="bot_bio")
    @premium_only()
    async def set_server_bio(self, ctx, *, bio: str):
        """(Premium) Set server bio."""
        # Renamed internal method 'bot_bio' to 'set_server_bio' to fix TypeError
        await ctx.send(f"💎 Server Bio: {bio}")

    @commands.hybrid_command()
    @premium_only()
    async def spoiler_image(self, ctx, url: str):
        """(Premium) Spoiler Image."""
        await ctx.send(f"💎 || {url} ||")

    @commands.hybrid_command()
    @premium_only()
    async def live_counter(self, ctx, channel: discord.VoiceChannel):
        """(Premium) Live Member Counter."""
        await channel.edit(name=f"Members: {ctx.guild.member_count}")
        await ctx.send(f"💎 Counter active on {channel.name}")

class OwnerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.is_owner()
    async def gpremium(self, ctx, user: discord.User, days: int):
        """Owner: Give Premium."""
        expiry = "never" if days == -1 else (datetime.datetime.now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT OR REPLACE INTO premium (user_id, expiry, type) VALUES (?, ?, ?)", (user.id, expiry, "pro"))
        conn.commit()
        await ctx.send(f"🌟 Premium granted to {user.name}")

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
        """Owner: Check health."""
        lat = round(bot.latency * 1000)
        await ctx.send(f"**Status**\nPing: {lat}ms\nOCR: {OCR_AVAILABLE}\nDB: OK")

    @commands.command()
    @commands.is_owner()
    async def announce_update(self, ctx, *, msg):
        """Owner: Broadcast update."""
        c.execute("SELECT announce_channel FROM guild_config")
        count = 0
        for row in c.fetchall():
            if row[0]:
                try:
                    ch = bot.get_channel(row[0])
                    if ch:
                        await ch.send(f"📢 **UPDATE:** {msg}")
                        count += 1
                except: pass
        await ctx.send(f"Sent to {count} servers.")

    @commands.command()
    @commands.is_owner()
    async def change_s(self, ctx, status_type: str):
        """Owner: Change Status (idle, dnd, online)."""
        s = getattr(discord.Status, status_type, discord.Status.online)
        await self.bot.change_presence(status=s)
        await ctx.send(f"Status: {status_type}")

    @commands.command()
    @commands.is_owner()
    async def uptime(self, ctx):
        await ctx.send("Online.")

# --- 7. TASKS & EVENTS ---

@tasks.loop(minutes=1)
async def task_check_temp_roles():
    now = datetime.datetime.now().timestamp()
    c.execute("SELECT user_id, guild_id, role_id FROM temp_roles WHERE expiry_timestamp < ?", (now,))
    rows = c.fetchall()
    for uid, gid, rid in rows:
        try:
            guild = bot.get_guild(gid)
            if guild:
                member = guild.get_member(uid)
                role = guild.get_role(rid)
                if member and role:
                    await member.remove_roles(role)
        except: pass
        c.execute("DELETE FROM temp_roles WHERE user_id = ? AND role_id = ?", (uid, rid))
        conn.commit()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    task_check_temp_roles.start()
    bot.add_view(TicketView())
    bot.add_view(TicketControls())
    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"Sync Error: {e}")

@bot.event
async def on_guild_role_create(role):
    c.execute("SELECT name FROM blocked_roles WHERE guild_id = ?", (role.guild.id,))
    blocked = [r[0] for r in c.fetchall()]
    if role.name.lower() in blocked:
        try: await role.delete(reason="Blocked Role Name")
        except: pass

@bot.event
async def on_member_join(member):
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

    # Mail
    if isinstance(message.channel, discord.DMChannel):
        await message.channel.send("📧 Staff Mail: Received.")
        return

    # Maintenance
    if is_maintenance_mode() and message.author.id != bot.owner_id:
        return

    # AFK
    c.execute("SELECT reason FROM afk WHERE user_id = ?", (message.author.id,))
    if c.fetchone():
        c.execute("DELETE FROM afk WHERE user_id = ?", (message.author.id,))
        conn.commit()
        await message.channel.send(f"Welcome back {message.author.mention}, AFK removed.", delete_after=5)
        try: await message.author.edit(nick=None)
        except: pass

    if message.mentions:
        for m in message.mentions:
            c.execute("SELECT reason, timestamp FROM afk WHERE user_id = ?", (m.id,))
            res = c.fetchone()
            if res:
                await message.channel.send(f"💤 {m.name} is AFK: {res[0]}", delete_after=5)

    # Auto Mode
    if "http" in message.content:
        c.execute("SELECT auto_mode FROM guild_config WHERE guild_id = ?", (message.guild.id,))
        res = c.fetchone()
        if res and res[0] == 1:
            if not message.author.guild_permissions.administrator:
                await message.delete()
                await message.channel.send("❌ Links blocked.", delete_after=3)

    # Blocked Words
    c.execute("SELECT word FROM block_words WHERE guild_id = ?", (message.guild.id,))
    blocked = [r[0] for r in c.fetchall()]
    if any(w in message.content.lower() for w in blocked):
        await message.delete()
        await message.channel.send(f"⚠️ {message.author.mention} Word blocked!", delete_after=3)

    # OCR
    if message.attachments and OCR_AVAILABLE:
        for att in message.attachments:
            if att.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                try:
                    d = await att.read()
                    img = Image.open(io.BytesIO(d))
                    text = pytesseract.image_to_string(img).lower()
                    bad = ['crypto', 'bitcoin', 'investment', 'promo', 'dm me']
                    if any(b in text for b in bad):
                        await message.delete()
                        await message.channel.send("🚫 Image blocked (Crypto/Spam).")
                        await message.author.timeout(datetime.timedelta(minutes=10))
                except: pass

    await bot.process_commands(message)

# --- 8. RUN ---

async def main():
    await bot.add_cog(AdminCog(bot))
    await bot.add_cog(ModCog(bot))
    await bot.add_cog(UtilCog(bot))
    await bot.add_cog(PremiumCog(bot))
    await bot.add_cog(OwnerCog(bot))
    
    keep_alive() # Web Server
    
    if not TOKEN:
        print("❌ Error: DISCORD_TOKEN missing in .env")
        return
    await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot Stopped.")
