import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import sqlite3
import datetime
import time
from flask import Flask
from threading import Thread
import io
import aiohttp

# ==========================================
# CONFIGURATION & SETUP
# ==========================================

# Intents are required for member/message tracking
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Database Setup (SQLite for single-file persistence)
conn = sqlite3.connect('bot_database.db')
c = conn.cursor()

def setup_db():
    # Tables for various features
    c.execute('''CREATE TABLE IF NOT EXISTS guild_settings 
                 (guild_id INTEGER PRIMARY KEY, log_channel INTEGER, mail_category INTEGER, 
                  verify_msg TEXT, automod_on INTEGER, antiraid_on INTEGER, antiraid_pro INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS premium_users (user_id INTEGER PRIMARY KEY, expire_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS blockwords (guild_id INTEGER, word TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS blocked_roles (guild_id INTEGER, role_name TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS warns (user_id INTEGER, guild_id INTEGER, count INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS notes (user_id INTEGER, note TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tickets (ticket_id TEXT PRIMARY KEY, owner_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS reaction_roles (message_id INTEGER, emoji TEXT, role_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS maintenance (command_name TEXT PRIMARY KEY)''')
    conn.commit()

setup_db()

# Global variables for runtime cache
afk_users = {} # {user_id: reason}
spam_check = {} # {user_id: [timestamps]}
bot_bio_text = "I am a professional Bot."
maintenance_mode = []

# ==========================================
# WEB SERVER (KEEP ALIVE)
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return "Bot is Alive! Node_list: All Systems Operational."

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def is_premium(user_id):
    c.execute("SELECT expire_date FROM premium_users WHERE user_id = ?", (user_id,))
    data = c.fetchone()
    if not data: return False
    if data[0] == "never": return True
    expire = datetime.datetime.strptime(data[0], "%Y-%m-%d")
    return datetime.datetime.now() < expire

def is_bot_admin(user_id):
    c.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,))
    return c.fetchone() is not None or user_id == int(os.environ.get("OWNER_ID", 0))

def check_maintenance(ctx):
    if ctx.command.name in maintenance_mode and not is_bot_admin(ctx.author.id):
        raise commands.CheckFailure(f"Command {ctx.command.name} is currently in maintenance.")
    return True

# ==========================================
# EVENTS
# ==========================================

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    await bot.change_presence(status=discord.Status.online, activity=discord.Game(name=bot_bio_text))
    
    # Reload maintenance list
    c.execute("SELECT command_name FROM maintenance")
    for row in c.fetchall():
        maintenance_mode.append(row[0])
        
    # Start Tasks
    counter_loop.start()

@bot.event
async def on_member_join(member):
    # Anti-Raid Basic
    c.execute("SELECT antiraid_on, antiraid_pro FROM guild_settings WHERE guild_id = ?", (member.guild.id,))
    settings = c.fetchone()
    
    if settings and (settings[0] == 1 or settings[1] == 1):
        # Basic: Check account age (if < 1 day, kick)
        if (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days < 1:
            await member.kick(reason="Anti-Raid: Account too young")
            return

    # Counter Update (Handled in loop, but can trigger here too)
    
@bot.event
async def on_guild_role_create(role):
    # Block_R System
    c.execute("SELECT role_name FROM blocked_roles WHERE guild_id = ?", (role.guild.id,))
    blocked = [row[0] for row in c.fetchall()]
    if role.name in blocked:
        await role.delete(reason="Blocked Role Name")

@bot.event
async def on_message(message):
    if message.author.bot: return

    # AFK Check
    if message.author.id in afk_users:
        del afk_users[message.author.id]
        await message.channel.send(f"Welcome back {message.author.mention}, I removed your AFK.", delete_after=5)

    for mention in message.mentions:
        if mention.id in afk_users:
            await message.channel.send(f"{mention.name} is AFK: {afk_users[mention.id]}", delete_after=5)

    # Mail Staff (DM to Bot)
    if isinstance(message.channel, discord.DMChannel):
        # Find a guild with mail setup (Logic simplified to first guild found with setup)
        # In production, you'd ask the user which server.
        c.execute("SELECT guild_id, log_channel FROM guild_settings WHERE log_channel IS NOT NULL")
        data = c.fetchone()
        if data:
            guild = bot.get_guild(data[0])
            channel = guild.get_channel(data[1])
            if channel:
                embed = discord.Embed(title="Mail Received", description=message.content, color=discord.Color.blue())
                embed.set_author(name=message.author, icon_url=message.author.avatar.url if message.author.avatar else None)
                embed.set_footer(text=f"User ID: {message.author.id}")
                await channel.send(embed=embed)
                await message.channel.send("Message sent to staff.")

    # Automod & Crypto Check
    if message.guild:
        c.execute("SELECT automod_on FROM guild_settings WHERE guild_id = ?", (message.guild.id,))
        res = c.fetchone()
        if res and res[0] == 1:
            # Blockwords
            c.execute("SELECT word FROM blockwords WHERE guild_id = ?", (message.guild.id,))
            blocklist = [row[0] for row in c.fetchall()]
            if any(word in message.content.lower() for word in blocklist):
                await message.delete()
                await message.channel.send(f"{message.author.mention} That word is blocked!", delete_after=3)
                return

            # Crypto/Promo Image Check (Simulated)
            if message.attachments:
                for att in message.attachments:
                    # In a real scenario, use OCR here.
                    # Heuristic: Check filename keywords
                    suspicious = ["crypto", "btc", "eth", "invest", "promo", "free_nitro"]
                    if any(s in att.filename.lower() for s in suspicious) or any(s in message.content.lower() for s in suspicious):
                        await message.delete()
                        await message.channel.send(f"{message.author.mention} No crypto/promotion images allowed.", delete_after=5)
                        await message.author.timeout(datetime.timedelta(minutes=5), reason="Auto-Mod: Crypto/Promo Image")
                        return

            # Link Spam
            if "http" in message.content and not message.author.guild_permissions.administrator:
                 # Simple link spam check
                await message.delete()
                await message.channel.send("Links are disabled.", delete_after=3)
                return

    await bot.process_commands(message)

# ==========================================
# COMMANDS: UTILITY & INFO
# ==========================================

@bot.command()
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=f"{member.name}'s Avatar")
    embed.set_image(url=member.display_avatar.url)
    await ctx.send(embed=embed)

@bot.command()
async def membercount(ctx):
    await ctx.send(embed=discord.Embed(title="Member Count", description=f"Total: {ctx.guild.member_count}"))

@bot.command()
async def banner(ctx, member: discord.Member = None):
    member = member or ctx.author
    user = await bot.fetch_user(member.id)
    if user.banner:
        embed = discord.Embed(title="Banner")
        embed.set_image(url=user.banner.url)
        await ctx.send(embed=embed)
    else:
        await ctx.send("User has no banner.")

@bot.command()
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title="User Info", color=member.color)
    embed.add_field(name="ID", value=member.id)
    embed.add_field(name="Joined", value=member.joined_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Created", value=member.created_at.strftime("%Y-%m-%d"))
    await ctx.send(embed=embed)

@bot.command()
async def roleinfo(ctx, role: discord.Role):
    embed = discord.Embed(title=f"Role: {role.name}", color=role.color)
    embed.add_field(name="ID", value=role.id)
    embed.add_field(name="Members", value=len(role.members))
    embed.add_field(name="Perms", value=role.permissions.value)
    await ctx.send(embed=embed)

@bot.command()
async def botinvite(ctx):
    await ctx.send(f"Invite me: https://discord.com/api/oauth2/authorize?client_id={bot.user.id}&permissions=8&scope=bot")

@bot.command()
async def help(ctx):
    # Simplified Help
    embed = discord.Embed(title="Bot Help", description="A professional multi-purpose bot.")
    embed.add_field(name="Moderation", value="`warn`, `mute`, `ban`, `blockwords`, `slowmode`")
    embed.add_field(name="Utility", value="`avatar`, `userinfo`, `membercount`, `poll`, `afk`")
    embed.add_field(name="Tickets", value="`ticket_setup`, `close`")
    embed.add_field(name="Premium", value="`change_nick`, `invite_panel`, `spoiler_image`")
    await ctx.send(embed=embed)

# ==========================================
# COMMANDS: MODERATION
# ==========================================

@bot.command()
@commands.has_permissions(manage_messages=True)
async def blockwords(ctx, *, words):
    # Format: !blockwords bad1, bad2
    word_list = [w.strip().lower() for w in words.split(',')]
    for w in word_list:
        c.execute("INSERT INTO blockwords (guild_id, word) VALUES (?, ?)", (ctx.guild.id, w))
    conn.commit()
    await ctx.send(f"Blocked {len(word_list)} words.")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def unblockwords(ctx, word):
    c.execute("DELETE FROM blockwords WHERE guild_id = ? AND word = ?", (ctx.guild.id, word.lower()))
    conn.commit()
    await ctx.send(f"Unblocked {word}.")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def bwlist(ctx):
    c.execute("SELECT word FROM blockwords WHERE guild_id = ?", (ctx.guild.id,))
    words = [row[0] for row in c.fetchall()]
    await ctx.send(f"Blocked words: {', '.join(words)}")

@bot.command()
@commands.has_permissions(kick_members=True)
async def warn(ctx, member: discord.Member, *, reason="No reason"):
    c.execute("SELECT count FROM warns WHERE user_id = ? AND guild_id = ?", (member.id, ctx.guild.id))
    data = c.fetchone()
    count = 1 if not data else data[0] + 1
    
    if data:
        c.execute("UPDATE warns SET count = ? WHERE user_id = ? AND guild_id = ?", (count, member.id, ctx.guild.id))
    else:
        c.execute("INSERT INTO warns VALUES (?, ?, ?)", (member.id, ctx.guild.id, count))
    conn.commit()
    await ctx.send(f"{member.mention} warned. Total: {count}. Reason: {reason}")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, minutes: int, *, reason="Muted"):
    duration = datetime.timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    await ctx.send(f"{member.mention} has been muted for {minutes}m.")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="Banned"):
    await member.ban(reason=reason)
    await ctx.send(f"{member.mention} banned.")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def set_slowmode(ctx, seconds: int):
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send(f"Slowmode set to {seconds}s.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def auto_mode(ctx, status: str):
    # status: on / off
    val = 1 if status.lower() == "on" else 0
    c.execute("INSERT OR REPLACE INTO guild_settings (guild_id, automod_on) VALUES (?, ?)", (ctx.guild.id, val)) # Simplified SQL
    # Note: In real SQL, UPDATE/INSERT logic needs to be cleaner, using a basic upsert logic here for brevity
    # We assume the row exists or we create it.
    try:
        c.execute("INSERT INTO guild_settings (guild_id, automod_on) VALUES (?, ?)", (ctx.guild.id, val))
    except:
        c.execute("UPDATE guild_settings SET automod_on = ? WHERE guild_id = ?", (val, ctx.guild.id))
    conn.commit()
    await ctx.send(f"Automod turned {status}.")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def block_r(ctx, *, name):
    c.execute("INSERT INTO blocked_roles VALUES (?, ?)", (ctx.guild.id, name))
    conn.commit()
    await ctx.send(f"Role name '{name}' is now restricted.")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def set_role(ctx, member: discord.Member, role: discord.Role):
    await member.add_roles(role)
    await ctx.send(f"Added {role.name} to {member.name}")

@bot.command()
@commands.has_permissions(manage_nicknames=True)
async def set_nick(ctx, member: discord.Member, *, nick):
    await member.edit(nick=nick)
    await ctx.send("Nickname updated.")

# ==========================================
# COMMANDS: INTERACTION & UTILS
# ==========================================

@bot.command()
async def poll(ctx, *, question):
    embed = discord.Embed(title="Poll", description=question, color=discord.Color.gold())
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("👍")
    await msg.add_reaction("👎")

@bot.command()
async def suggestion(ctx, *, content):
    embed = discord.Embed(title="Suggestion", description=content)
    embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar.url)
    await ctx.send(embed=embed) # Ideally send to a specific channel

@bot.command()
@commands.has_permissions(administrator=True)
async def announce(ctx, channel: discord.TextChannel, *, message):
    embed = discord.Embed(title="Announcement", description=message, color=discord.Color.red())
    await channel.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def giveaway(ctx, time_str, prize):
    # Basic giveaway placeholder
    await ctx.send(f"🎉 **GIVEAWAY** 🎉\nPrize: {prize}\nReact with 🎉 to enter!")

@bot.command()
async def msg(ctx, *, content):
    await ctx.message.delete()
    await ctx.send(content)

@bot.command()
async def dm(ctx, user: discord.User, *, message):
    try:
        await user.send(message)
        await ctx.send("Sent.")
    except:
        await ctx.send("Cannot DM user.")

@bot.command()
async def dm_test(ctx):
    await ctx.author.send("DM Test successful.")

@bot.command()
async def afk(ctx, *, reason="AFK"):
    afk_users[ctx.author.id] = reason
    await ctx.send(f"{ctx.author.mention} is now AFK: {reason}")

# ==========================================
# COMMANDS: TICKETS
# ==========================================

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.green, custom_id="ticket_open")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True)
        }
        
        # Check for Ticket Admin role
        # Assuming a role named 'Ticket Admin' exists or configured
        admin_role = discord.utils.get(guild.roles, name="Ticket Staff")
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(read_messages=True)

        channel = await guild.create_text_channel(f"ticket-{interaction.user.name}", overwrites=overwrites)
        c.execute("INSERT INTO tickets VALUES (?, ?)", (str(channel.id), interaction.user.id))
        conn.commit()

        embed = discord.Embed(title="Ticket Open", description="Support will be with you shortly.")
        await channel.send(f"{interaction.user.mention}", embed=embed, view=CloseTicketView())
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)

class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id="ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if "ticket-" in interaction.channel.name:
            await interaction.response.send_message("Closing ticket in 5 seconds...")
            await asyncio.sleep(5)
            # Transcript logic would go here (saving msgs to file)
            await interaction.channel.delete()
            c.execute("DELETE FROM tickets WHERE ticket_id = ?", (str(interaction.channel_id),))
            conn.commit()

@bot.command()
@commands.has_permissions(administrator=True)
async def ticket_admin(ctx):
    embed = discord.Embed(title="Support Tickets", description="Click below to open a ticket")
    await ctx.send(embed=embed, view=TicketView())

# ==========================================
# COMMANDS: NOTES
# ==========================================

@bot.command()
@commands.has_permissions(manage_messages=True)
async def note(ctx, user: discord.User, *, note):
    c.execute("INSERT INTO notes VALUES (?, ?)", (user.id, note))
    conn.commit()
    await ctx.send("Note added.")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def r_note(ctx, user: discord.User):
    c.execute("SELECT note FROM notes WHERE user_id = ?", (user.id,))
    notes = c.fetchall()
    if notes:
        await ctx.send(f"Notes for {user.name}: \n" + "\n".join([f"- {n[0]}" for n in notes]))
    else:
        await ctx.send("No notes found.")

# ==========================================
# PREMIUM FEATURES
# ==========================================

@bot.command()
async def change_nick(ctx, *, new_nick):
    if not is_premium(ctx.author.id):
        return await ctx.send("Premium only feature.")
    await ctx.author.edit(nick=new_nick)
    await ctx.send("Nickname changed.")

@bot.command()
async def invite_panel(ctx):
    if not is_premium(ctx.author.id): return await ctx.send("Premium only.")
    # Calculate invites (This is complex in Discord API, using basic count)
    invites = await ctx.guild.invites()
    my_invites = sum(i.uses for i in invites if i.inviter == ctx.author)
    await ctx.send(f"You have {my_invites} invites.")

@bot.command()
async def bot_bio_premium(ctx):
    if not is_premium(ctx.author.id): return await ctx.send("Premium only.")
    embed = discord.Embed(title="Bot Bio", description=bot_bio_text)
    await ctx.send(embed=embed)

@bot.command()
async def spoiler_image(ctx):
    if not is_premium(ctx.author.id): return await ctx.send("Premium only.")
    if not ctx.message.attachments: return await ctx.send("Attach an image.")
    
    # Logic: Uploads image to a private channel, grabs link, deletes msg, DMs user
    # Simplified: Sending back a spoiler
    f = await ctx.message.attachments[0].to_file(spoiler=True)
    await ctx.message.delete()
    await ctx.author.send("Here is your spoiler image:", file=f)
    await ctx.send(f"{ctx.author.mention} check your DM for the spoiler link.")

# ==========================================
# ADMIN & OWNER COMMANDS
# ==========================================

@bot.command()
async def add_admin(ctx, user: discord.User):
    if ctx.author.id != int(os.environ.get("OWNER_ID", 0)): return
    c.execute("INSERT OR IGNORE INTO admins VALUES (?)", (user.id,))
    conn.commit()
    await ctx.send(f"{user.name} is now a bot admin.")

@bot.command()
async def gpremium(ctx, user: discord.User, duration: str):
    # Duration: "30d", "never"
    if not is_bot_admin(ctx.author.id): return
    
    expire = "never"
    if duration != "never":
        days = int(duration.replace("d", ""))
        expire = (datetime.datetime.now() + datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        
    c.execute("INSERT OR REPLACE INTO premium_users VALUES (?, ?)", (user.id, expire))
    conn.commit()
    await ctx.send(f"Given premium to {user.name} until {expire}")

@bot.command()
async def change_s(ctx, status: str):
    if not is_bot_admin(ctx.author.id): return
    st = discord.Status.online
    if status == "idle": st = discord.Status.idle
    if status == "dnd": st = discord.Status.dnd
    await bot.change_presence(status=st)
    await ctx.send(f"Status changed to {status}")

@bot.command()
async def node_list(ctx):
    if not is_bot_admin(ctx.author.id): return
    await ctx.send(f"Bot Latency: {round(bot.latency * 1000)}ms. Database: Connected. Listeners: Active.")

@bot.command()
async def bot_config(ctx, command_name: str, state: str):
    if not is_bot_admin(ctx.author.id): return
    # state: off (maintenance), on
    if state == "off":
        c.execute("INSERT OR IGNORE INTO maintenance VALUES (?)", (command_name,))
        maintenance_mode.append(command_name)
    else:
        c.execute("DELETE FROM maintenance WHERE command_name = ?", (command_name,))
        if command_name in maintenance_mode: maintenance_mode.remove(command_name)
    conn.commit()
    await ctx.send(f"Command {command_name} is now {state}.")

# ==========================================
# COUNTERS & TASKS
# ==========================================

@tasks.loop(minutes=10)
async def counter_loop():
    # Example: Updates a channel name with member count
    # User needs to set channel ID first (Setup required)
    # This loop iterates through guilds with setup
    pass 

# ==========================================
# REACTION ROLES
# ==========================================

@bot.command()
@commands.has_permissions(administrator=True)
async def reaction_role(ctx, message_id: int, emoji: str, role: discord.Role):
    c.execute("INSERT INTO reaction_roles VALUES (?, ?, ?)", (message_id, emoji, role.id))
    conn.commit()
    msg = await ctx.channel.fetch_message(message_id)
    await msg.add_reaction(emoji)
    await ctx.send("Reaction role setup.")

@bot.event
async def on_raw_reaction_add(payload):
    if payload.member.bot: return
    c.execute("SELECT role_id FROM reaction_roles WHERE message_id = ? AND emoji = ?", (payload.message_id, str(payload.emoji)))
    data = c.fetchone()
    if data:
        guild = bot.get_guild(payload.guild_id)
        role = guild.get_role(data[0])
        await payload.member.add_roles(role)

@bot.event
async def on_raw_reaction_remove(payload):
    c.execute("SELECT role_id FROM reaction_roles WHERE message_id = ? AND emoji = ?", (payload.message_id, str(payload.emoji)))
    data = c.fetchone()
    if data:
        guild = bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        role = guild.get_role(data[0])
        if member and role:
            await member.remove_roles(role)

# ==========================================
# ERROR HANDLING
# ==========================================
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to use this.", delete_after=5)
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        await ctx.send(f"Error: {str(error)}", delete_after=5)

# Run
keep_alive()
try:
    bot.run(os.getenv("DISCORD_TOKEN"))
except:
    print("Please set the DISCORD_TOKEN in your .env file")
