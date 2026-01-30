import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Select
import os
import json
import asyncio
import datetime
import time
from flask import Flask
from threading import Thread
from dotenv import load_dotenv

# --- CONFIGURATION & SETUP ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")  # Put your token in a .env file
DEFAULT_PREFIX = "!"

# Intents
intents = discord.Intents.all()
bot = commands.Bot(command_prefix=DEFAULT_PREFIX, intents=intents, help_command=None)

# --- DATABASE MANAGEMENT (JSON) ---
DB_FILE = "bot_data.json"

def load_data():
    if not os.path.exists(DB_FILE):
        return {
            "config": {"maintenance_mode": False, "maintenance_commands": []},
            "blocklist": [],
            "blocked_role_names": [],
            "notes": {},
            "warns": {},
            "premium_users": {},
            "tickets": {"count": 0, "admin_role_id": None, "category_id": None},
            "antiraid": {"basic": False, "pro": False},
            "automod": {"link_spam": False},
            "admins": [],
            "bot_bio": "I am a professional discord bot.",
            "join_log": []
        }
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

db = load_data()

# --- KEEP ALIVE (WEB SERVER) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- CHECKS & DECORATORS ---
def is_bot_admin():
    async def predicate(ctx):
        return ctx.author.id in db["admins"] or ctx.author.id == ctx.guild.owner_id
    return commands.check(predicate)

def is_premium():
    async def predicate(ctx):
        if str(ctx.author.id) in db["premium_users"]:
            expiry = db["premium_users"][str(ctx.author.id)]
            if expiry == "never" or float(expiry) > time.time():
                return True
        await ctx.send("❌ This command is for **Premium** users only.")
        return False
    return commands.check(predicate)

def maintenance_check(ctx):
    if db["config"]["maintenance_mode"] and ctx.author.id not in db["admins"]:
        if ctx.command.name in db["config"]["maintenance_commands"]:
            return False
    return True

bot.add_check(maintenance_check)

# --- UTILITY FUNCTIONS ---
async def create_log_embed(ctx, title, description, color=discord.Color.blue()):
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
    return embed

# --- EVENTS ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Game(name=db["bot_bio"]))
    keep_alive()

@bot.event
async def on_member_join(member):
    # Anti-Raid Basic
    if db["antiraid"]["basic"]:
        now = time.time()
        db["join_log"].append(now)
        # Clean old logs (older than 10s)
        db["join_log"] = [t for t in db["join_log"] if now - t < 10]
        save_data(db)
        
        if len(db["join_log"]) > 5: # More than 5 joins in 10s
             # Action: Kick recent joins or Lock channel (Simple implementation: Kick)
             try: await member.kick(reason="Anti-Raid: Basic Mode Triggered")
             except: pass

    # Counter
    # (Implementation requires specific channel IDs setup, omitted for generic single file)

@bot.event
async def on_guild_role_create(role):
    # Block_R System
    if role.name in db["blocked_role_names"]:
        try:
            await role.delete(reason="Blocked Role Name")
        except:
            pass

@bot.event
async def on_message(message):
    if message.author.bot: return

    # Mail Staff (DM)
    if isinstance(message.channel, discord.DMChannel):
        guild = bot.guilds[0] # Takes the first guild the bot is in (Simplification)
        # Logic to send to staff channel would go here
        return

    # Auto Mod: Block Words
    msg_content = message.content.lower()
    for word in db["blocklist"]:
        if word in msg_content:
            await message.delete()
            await message.channel.send(f"{message.author.mention}, that word is blocked!", delete_after=5)
            return

    # Auto Mod: Link Spam
    if db["automod"]["link_spam"] and ("http://" in msg_content or "https://" in msg_content):
        # Allow whitelisted roles check here
        await message.delete()
        await message.channel.send("Links are not allowed!", delete_after=3)
        return

    # Image Check (Crypto/Promo) - REQUIRES TESSERACT
    if message.attachments:
        for attachment in message.attachments:
            if attachment.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                # Placeholder for OCR logic. 
                # Real implementation requires downloading image and running pytesseract.image_to_string
                # Since we can't easily bundle Tesseract in one file, we mock the logic:
                if "promo" in attachment.filename or "crypto" in attachment.filename:
                     await message.delete()
                     await message.channel.send(f"{message.author.mention}, that image looks like spam/crypto!", delete_after=5)
                     await message.author.timeout(datetime.timedelta(minutes=5))

    await bot.process_commands(message)

# --- COMMANDS: TICKET SYSTEM ---
class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.green, emoji="📩")
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        db["tickets"]["count"] += 1
        save_data(db)
        
        ticket_name = f"ticket-{db['tickets']['count']}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True)
        }
        
        # Add Admin Role permissions
        if db["tickets"]["admin_role_id"]:
            role = guild.get_role(db["tickets"]["admin_role_id"])
            if role: overwrites[role] = discord.PermissionOverwrite(read_messages=True)

        category = guild.get_channel(db["tickets"]["category_id"]) if db["tickets"]["category_id"] else None
        
        channel = await guild.create_text_channel(ticket_name, overwrites=overwrites, category=category)
        
        embed = discord.Embed(title="Ticket Support", description=f"Hello {interaction.user.mention}, staff will be with you shortly.")
        await channel.send(embed=embed, view=TicketControlView())
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)

class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.channel.delete()
        # Transcript logic would generate a text file here

    @discord.ui.button(label="Transcript", style=discord.ButtonStyle.blurple, emoji="📄")
    async def transcript_ticket(self, interaction: discord.Interaction, button: Button):
        # Basic Transcript
        messages = [f"{m.author.name}: {m.content}" async for m in interaction.channel.history(limit=100)]
        content = "\n".join(messages)
        file = discord.File(fp=io.StringIO(content), filename="transcript.txt") # Requires import io
        await interaction.user.send("Here is your transcript:", file=file)

@bot.command()
@is_bot_admin()
async def setup_ticket(ctx):
    embed = discord.Embed(title="Support", description="Click below to open a ticket.", color=discord.Color.green())
    if ctx.guild.icon: embed.set_thumbnail(url=ctx.guild.icon.url)
    await ctx.send(embed=embed, view=TicketView())

@bot.command()
@is_bot_admin()
async def ticket_admin(ctx, role: discord.Role):
    db["tickets"]["admin_role_id"] = role.id
    save_data(db)
    await ctx.send(f"Ticket admin role set to {role.name}")

# --- COMMANDS: MODERATION & AUTOMOD ---
@bot.command()
@commands.has_permissions(manage_messages=True)
async def blockwords(ctx, *, word: str):
    db["blocklist"].append(word.lower())
    save_data(db)
    await ctx.send(f"Blocked word: `{word}`")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def unblockwords(ctx, *, word: str):
    if word.lower() in db["blocklist"]:
        db["blocklist"].remove(word.lower())
        save_data(db)
        await ctx.send(f"Unblocked: `{word}`")
    else:
        await ctx.send("Word not found.")

@bot.command()
@commands.has_permissions(kick_members=True)
async def warn(ctx, member: discord.Member, *, reason="No reason"):
    uid = str(member.id)
    if uid not in db["warns"]: db["warns"][uid] = []
    db["warns"][uid].append(reason)
    save_data(db)
    await ctx.send(f"⚠️ Warned {member.mention} for: {reason}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="None"):
    await member.ban(reason=reason)
    await ctx.send(f"🔨 Banned {member.name}")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def mute(ctx, member: discord.Member, minutes: int):
    await member.timeout(datetime.timedelta(minutes=minutes))
    await ctx.send(f"😶 Muted {member.name} for {minutes} minutes.")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def Block_R(ctx, *, role_name):
    db["blocked_role_names"].append(role_name)
    save_data(db)
    await ctx.send(f"🚫 Role name `{role_name}` is now restricted.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def set_slowmode(ctx, seconds: int):
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send(f"Slowmode set to {seconds}s")

@bot.command()
@commands.has_permissions(administrator=True)
async def automode(ctx, mode: str):
    if mode.lower() == "on":
        db["automod"]["link_spam"] = True
        await ctx.send("Automod: Link Spam ON")
    else:
        db["automod"]["link_spam"] = False
        await ctx.send("Automod: Link Spam OFF")
    save_data(db)

@bot.command()
@commands.has_permissions(administrator=True)
async def anti_raid(ctx, mode: str):
    if mode.lower() == "on":
        db["antiraid"]["basic"] = True
        await ctx.send("Anti-Raid Basic: ON")
    else:
        db["antiraid"]["basic"] = False
        await ctx.send("Anti-Raid: OFF")
    save_data(db)

# --- COMMANDS: UTILITY & FUN ---
@bot.command()
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = await create_log_embed(ctx, f"{member.name}'s Avatar", "")
    embed.set_image(url=member.avatar.url)
    await ctx.send(embed=embed)

@bot.command()
async def banner(ctx, member: discord.Member = None):
    member = member or ctx.author
    user = await bot.fetch_user(member.id) # Needed for banner
    if user.banner:
        embed = await create_log_embed(ctx, f"{member.name}'s Banner", "")
        embed.set_image(url=user.banner.url)
        await ctx.send(embed=embed)
    else:
        await ctx.send("User has no banner.")

@bot.command()
async def membercount(ctx):
    await ctx.send(f"Members: {ctx.guild.member_count}")

@bot.command()
async def poll(ctx, *, question):
    embed = discord.Embed(title="Poll", description=question, color=discord.Color.gold())
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("👍")
    await msg.add_reaction("👎")

@bot.command()
async def roleinfo(ctx, role: discord.Role):
    embed = discord.Embed(title=role.name, color=role.color)
    embed.add_field(name="ID", value=role.id)
    embed.add_field(name="Members", value=len(role.members))
    embed.add_field(name="Mentionable", value=role.mentionable)
    await ctx.send(embed=embed)

@bot.command()
async def botinvite(ctx):
    await ctx.send(f"Invite me: https://discord.com/oauth2/authorize?client_id={bot.user.id}&permissions=8&scope=bot")

@bot.command()
async def giveaway(ctx, time_str: str, *, prize: str):
    # Basic parsing for "10s", "1m"
    seconds = int(time_str[:-1]) * (60 if time_str.endswith("m") else 1)
    embed = discord.Embed(title="🎉 Giveaway!", description=f"Prize: **{prize}**\nReact with 🎉 to enter!", color=discord.Color.purple())
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("🎉")
    await asyncio.sleep(seconds)
    
    msg = await ctx.channel.fetch_message(msg.id)
    users = [user async for user in msg.reactions[0].users() if not user.bot]
    if users:
        import random
        winner = random.choice(users)
        await ctx.send(f"Congratulations {winner.mention}! You won **{prize}**!")
    else:
        await ctx.send("No one entered the giveaway.")

@bot.command()
async def channel_stats(ctx):
    embed = discord.Embed(title=f"Stats for #{ctx.channel.name}")
    embed.add_field(name="ID", value=ctx.channel.id)
    embed.add_field(name="Type", value=str(ctx.channel.type))
    await ctx.send(embed=embed)

@bot.command()
async def suggestion(ctx, *, content):
    channel = discord.utils.get(ctx.guild.channels, name="suggestions")
    if channel:
        embed = discord.Embed(description=content, color=discord.Color.orange())
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar.url)
        msg = await channel.send(embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        await ctx.send("Suggestion sent!")
    else:
        await ctx.send("Please create a channel named `#suggestions`.")

@bot.command()
async def set_nick(ctx, member: discord.Member, *, nick):
    if ctx.author.guild_permissions.manage_nicknames:
        await member.edit(nick=nick)
        await ctx.send(f"Nickname changed to {nick}")

@bot.command()
async def note(ctx, user: discord.User, *, note_content):
    if str(user.id) not in db["notes"]: db["notes"][str(user.id)] = []
    db["notes"][str(user.id)].append(note_content)
    save_data(db)
    await ctx.send("Note added.")

@bot.command()
async def r_note(ctx, user: discord.User):
    notes = db["notes"].get(str(user.id), ["No notes."])
    await ctx.send(f"Notes for {user.name}:\n" + "\n".join(notes))

@bot.command()
async def afk(ctx, *, message="AFK"):
    # Store AFK state in a temp dict (omitted for brevity, usually sets a nick or auto-reply)
    await ctx.send(f"{ctx.author.mention} is now AFK: {message}")

@bot.command()
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=f"User Info: {member.name}", color=member.color)
    embed.add_field(name="Joined", value=member.joined_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Registered", value=member.created_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Roles", value=", ".join([r.name for r in member.roles if r.name != "@everyone"]))
    await ctx.send(embed=embed)

# --- COMMANDS: ADMIN / OWNER ---
@bot.command()
@commands.is_owner()
async def add_admin(ctx, user: discord.User):
    if user.id not in db["admins"]:
        db["admins"].append(user.id)
        save_data(db)
        await ctx.send(f"{user.name} is now a Bot Admin.")

@bot.command()
@is_bot_admin()
async def remove_admin(ctx, user: discord.User):
    if user.id in db["admins"]:
        db["admins"].remove(user.id)
        save_data(db)
        await ctx.send(f"{user.name} removed from Bot Admins.")

@bot.command()
@is_bot_admin()
async def change_s(ctx, status_type: str):
    if status_type == "online": await bot.change_presence(status=discord.Status.online)
    elif status_type == "idle": await bot.change_presence(status=discord.Status.idle)
    elif status_type == "dnd": await bot.change_presence(status=discord.Status.dnd)
    await ctx.send(f"Status changed to {status_type}")

@bot.command()
@is_bot_admin()
async def bot_config(ctx, command_name: str, state: str):
    if state == "maintenance":
        db["config"]["maintenance_commands"].append(command_name)
    elif state == "active":
        if command_name in db["config"]["maintenance_commands"]:
            db["config"]["maintenance_commands"].remove(command_name)
    save_data(db)
    await ctx.send(f"Command `{command_name}` is now in {state}.")

@bot.command()
@is_bot_admin()
async def uptime(ctx):
    # Simple uptime calculation (needs start_time global)
    await ctx.send("Bot is online.") 

@bot.command()
@is_bot_admin()
async def announce_update(ctx, *, message):
    for guild in bot.guilds:
        # Tries to find 'general' or first text channel
        channel = discord.utils.get(guild.text_channels, name="general")
        if channel:
            try: await channel.send(f"**UPDATE:** {message}")
            except: pass

@bot.command()
@is_bot_admin()
async def gpremium(ctx, user: discord.User, duration: str):
    # duration: "30d", "never"
    if duration == "never":
        expiry = "never"
    else:
        days = int(duration.replace("d", ""))
        expiry = time.time() + (days * 86400)
    
    db["premium_users"][str(user.id)] = expiry
    save_data(db)
    await ctx.send(f"Granted Premium to {user.name} for {duration}.")

# --- COMMANDS: PREMIUM FEATURES ---
@bot.command()
@is_premium()
async def change_nick_bot(ctx, *, new_nick):
    await ctx.guild.me.edit(nick=new_nick)
    await ctx.send("My nickname has been changed (Premium).")

@bot.command()
@is_premium()
async def spoiler_image(ctx):
    # Logic: User uploads image, bot deletes, creates a button "View Image"
    # When clicked, sends ephemeral msg or DM
    if not ctx.message.attachments:
        await ctx.send("Please attach an image.")
        return
    
    attachment = ctx.message.attachments[0]
    # In real world, save this URL or file. simplified here:
    class SpoilerView(View):
        @discord.ui.button(label="View Spoiler", style=discord.ButtonStyle.gray, emoji="🫣")
        async def view_spoiler(self, interaction: discord.Interaction, button: Button):
            await interaction.response.send_message(f"Here is the image: {attachment.url}", ephemeral=True)
            
    await ctx.message.delete()
    await ctx.send(f"**Spoiler Image** sent by {ctx.author.mention}", view=SpoilerView())

# --- ERROR HANDLING ---
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Missing arguments.")
    elif isinstance(error, commands.CheckFailure):
        pass # Handled in checks
    else:
        print(f"Error: {error}")

# --- START ---
if __name__ == "__main__":
    import io # Imported here for transcript
    # Note: Keep Alive is started in on_ready
    bot.run(TOKEN)
