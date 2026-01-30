import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Select
import os
import json
import asyncio
import datetime
import time
import io
import random
from flask import Flask
from threading import Thread
from dotenv import load_dotenv

# --- CONFIGURATION & SETUP ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 0))  # Loads Owner ID from .env
DEFAULT_PREFIX = "!"

# Intents (REQUIRED for reading messages and member lists)
intents = discord.Intents.all()
bot = commands.Bot(command_prefix=DEFAULT_PREFIX, intents=intents, help_command=None)
bot.owner_id = OWNER_ID # Explicitly set owner ID

# --- DATABASE MANAGEMENT (JSON) ---
DB_FILE = "bot_data.json"

def load_data():
    if not os.path.exists(DB_FILE):
        return {
            "config": {
                "maintenance_mode": False, 
                "maintenance_commands": [],
                "modmail_channel": None,
                "suggestion_channel": None,
                "verify_role": None,
                "auto_role": None
            },
            "blocklist": [],
            "blocked_role_names": [],
            "notes": {},
            "warns": {},
            "premium_users": {},
            "tickets": {
                "count": 0, 
                "admin_role_id": None, 
                "category_id": None, 
                "log_channel": None
            },
            "antiraid": {"basic": False, "pro": False},
            "automod": {"link_spam": False},
            "admins": [],
            "bot_bio": "I am a professional discord bot.",
            "join_log": [],
            "counters": {}, # {channel_id: type}
            "reaction_roles": {} # {message_id: {emoji: role_id}}
        }
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except:
        return {} # Return empty if corrupted

def save_data(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

db = load_data()

# --- KEEP ALIVE (WEB SERVER) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive and running!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- CHECKS ---
def is_bot_admin():
    async def predicate(ctx):
        return ctx.author.id == OWNER_ID or ctx.author.id in db["admins"] or ctx.author.id == ctx.guild.owner_id
    return commands.check(predicate)

def is_premium():
    async def predicate(ctx):
        if ctx.author.id == OWNER_ID: return True
        uid = str(ctx.author.id)
        if uid in db["premium_users"]:
            expiry = db["premium_users"][uid]
            if expiry == "never": return True
            if float(expiry) > time.time(): return True
        await ctx.send("❌ This command is for **Premium** users only.")
        return False
    return commands.check(predicate)

# --- EVENTS ---
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"👑 Owner ID set to: {OWNER_ID}")
    await bot.change_presence(activity=discord.Game(name=db["bot_bio"]))
    update_counters.start() # Start counter loop
    keep_alive()

@bot.event
async def on_member_join(member):
    # Auto Role
    if db["config"]["auto_role"]:
        role = member.guild.get_role(db["config"]["auto_role"])
        if role: await member.add_roles(role)

    # Anti-Raid Basic
    if db["antiraid"]["basic"]:
        now = time.time()
        db["join_log"].append(now)
        db["join_log"] = [t for t in db["join_log"] if now - t < 10]
        save_data(db)
        if len(db["join_log"]) > 5:
             try: await member.kick(reason="Anti-Raid Triggered")
             except: pass

@bot.event
async def on_raw_reaction_add(payload):
    # Reaction Roles
    if payload.user_id == bot.user.id: return
    msg_id = str(payload.message_id)
    if msg_id in db["reaction_roles"]:
        emoji = str(payload.emoji)
        if emoji in db["reaction_roles"][msg_id]:
            guild = bot.get_guild(payload.guild_id)
            role_id = db["reaction_roles"][msg_id][emoji]
            role = guild.get_role(role_id)
            member = guild.get_member(payload.user_id)
            if role and member:
                await member.add_roles(role)

@bot.event
async def on_raw_reaction_remove(payload):
    # Reaction Roles Remove
    msg_id = str(payload.message_id)
    if msg_id in db["reaction_roles"]:
        emoji = str(payload.emoji)
        if emoji in db["reaction_roles"][msg_id]:
            guild = bot.get_guild(payload.guild_id)
            role_id = db["reaction_roles"][msg_id][emoji]
            role = guild.get_role(role_id)
            member = guild.get_member(payload.user_id)
            if role and member:
                await member.remove_roles(role)

@bot.event
async def on_message(message):
    if message.author.bot: return

    # Mail Staff (DM to Server)
    if isinstance(message.channel, discord.DMChannel):
        if db["config"]["modmail_channel"]:
            # Find the guild where the modmail channel exists
            for guild in bot.guilds:
                channel = guild.get_channel(db["config"]["modmail_channel"])
                if channel:
                    embed = discord.Embed(title="📩 New Modmail", description=message.content, color=discord.Color.blue())
                    embed.set_author(name=message.author.name, icon_url=message.author.avatar.url if message.author.avatar else None)
                    embed.set_footer(text=f"User ID: {message.author.id}")
                    await channel.send(embed=embed)
                    await message.channel.send("Message sent to staff!")
                    return

    # Check for Blocked Words
    msg_content = message.content.lower()
    for word in db["blocklist"]:
        if word in msg_content:
            await message.delete()
            await message.channel.send(f"{message.author.mention}, that word is blocked!", delete_after=5)
            return

    # Link Spam
    if db["automod"]["link_spam"] and ("http://" in msg_content or "https://" in msg_content):
        if not message.author.guild_permissions.administrator: # Admins bypass
            await message.delete()
            await message.channel.send("Links are not allowed!", delete_after=3)
            return

    # Image Crypto Check (Simplified Mock)
    if message.attachments:
        for attachment in message.attachments:
            fname = attachment.filename.lower()
            if "crypto" in fname or "promo" in fname or "pump" in fname:
                 await message.delete()
                 await message.channel.send(f"{message.author.mention}, suspicious image detected!", delete_after=5)
                 try: await message.author.timeout(datetime.timedelta(minutes=5))
                 except: pass

    await bot.process_commands(message)

# --- LOOPS ---
@tasks.loop(minutes=10)
async def update_counters():
    for channel_id, c_type in db["counters"].items():
        try:
            channel = bot.get_channel(int(channel_id))
            if not channel: continue
            
            count = 0
            if c_type == "members": count = channel.guild.member_count
            elif c_type == "bots": count = sum(1 for m in channel.guild.members if m.bot)
            elif c_type == "banned": 
                bans = [entry async for entry in channel.guild.bans()]
                count = len(bans)
            
            await channel.edit(name=f"{c_type.title()}: {count}")
        except Exception as e:
            print(f"Counter Error: {e}")

# --- TICKET SYSTEM ---
class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.green, emoji="📩", custom_id="ticket_open_btn")
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
        
        if db["tickets"]["admin_role_id"]:
            role = guild.get_role(db["tickets"]["admin_role_id"])
            if role: overwrites[role] = discord.PermissionOverwrite(read_messages=True)

        category = guild.get_channel(db["tickets"]["category_id"]) if db["tickets"]["category_id"] else None
        
        try:
            channel = await guild.create_text_channel(ticket_name, overwrites=overwrites, category=category)
            embed = discord.Embed(title="Ticket Support", description=f"Hello {interaction.user.mention}, staff will be with you shortly.")
            await channel.send(embed=embed, view=TicketControlView())
            await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error creating ticket: {e}", ephemeral=True)

class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, emoji="🔒", custom_id="ticket_close_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.channel.delete()

    @discord.ui.button(label="Transcript", style=discord.ButtonStyle.blurple, emoji="📄", custom_id="ticket_trans_btn")
    async def transcript_ticket(self, interaction: discord.Interaction, button: Button):
        if not is_premium(): # Basic transcript for everyone, but advanced features usually blocked
             pass 
        messages = [f"{m.created_at} - {m.author.name}: {m.content}" async for m in interaction.channel.history(limit=500)]
        content = "\n".join(messages)
        f = io.BytesIO(content.encode("utf-8"))
        file = discord.File(fp=f, filename=f"transcript-{interaction.channel.name}.txt")
        await interaction.user.send("Here is your transcript:", file=file)
        await interaction.response.send_message("Transcript sent to your DM.", ephemeral=True)

# --- COMMANDS ---

@bot.command()
@is_bot_admin()
async def setup_ticket(ctx):
    embed = discord.Embed(title="Support Ticket", description="Click the button below to open a ticket.", color=discord.Color.green())
    if ctx.guild.icon: embed.set_thumbnail(url=ctx.guild.icon.url)
    await ctx.send(embed=embed, view=TicketView())

@bot.command()
@is_bot_admin()
async def ticket_admin(ctx, role: discord.Role):
    db["tickets"]["admin_role_id"] = role.id
    save_data(db)
    await ctx.send(f"✅ Ticket admin role set to: {role.name}")

@bot.command()
@is_bot_admin()
async def mail_staff(ctx):
    # Sets the current channel as the Modmail destination
    db["config"]["modmail_channel"] = ctx.channel.id
    save_data(db)
    await ctx.send(f"✅ Modmail messages will now appear in {ctx.channel.mention}")

@bot.command()
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=f"{member.name}'s Avatar", color=member.color)
    embed.set_image(url=member.avatar.url)
    await ctx.send(embed=embed)

@bot.command()
async def banner(ctx, member: discord.Member = None):
    member = member or ctx.author
    user = await bot.fetch_user(member.id)
    if user.banner:
        embed = discord.Embed(title=f"{member.name}'s Banner")
        embed.set_image(url=user.banner.url)
        await ctx.send(embed=embed)
    else:
        await ctx.send("User has no banner.")

@bot.command()
async def membercount(ctx):
    embed = discord.Embed(title="Member Count", description=f"Total: {ctx.guild.member_count}", color=discord.Color.gold())
    await ctx.send(embed=embed)

@bot.command()
async def poll(ctx, *, question):
    embed = discord.Embed(title="📊 Poll", description=question, color=discord.Color.teal())
    embed.set_footer(text=f"Poll by {ctx.author.name}")
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("👍")
    await msg.add_reaction("👎")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def blockwords(ctx, *, word: str):
    db["blocklist"].append(word.lower())
    save_data(db)
    await ctx.send(f"🚫 Blocked word: `{word}`")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def unblockwords(ctx, *, word: str):
    if word.lower() in db["blocklist"]:
        db["blocklist"].remove(word.lower())
        save_data(db)
        await ctx.send(f"✅ Unblocked: `{word}`")
    else:
        await ctx.send("Word not found in blocklist.")

@bot.command()
async def bwlist(ctx):
    # Show blocklist
    words = ", ".join(db["blocklist"])
    await ctx.send(f"**Blocked Words:** {words if words else 'None'}")

@bot.command()
@commands.has_permissions(kick_members=True)
async def warn(ctx, member: discord.Member, *, reason="No reason"):
    uid = str(member.id)
    if uid not in db["warns"]: db["warns"][uid] = []
    db["warns"][uid].append(f"{reason} (by {ctx.author.name})")
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
    await ctx.send(f"🚫 Role name `{role_name}` is now restricted from creation (needs manual enforcement).")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def set_slowmode(ctx, seconds: int):
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send(f"🐢 Slowmode set to {seconds}s")

@bot.command()
@is_bot_admin()
async def automode(ctx, mode: str):
    if mode.lower() == "on":
        db["automod"]["link_spam"] = True
        await ctx.send("🤖 Automod: Link Spam ON")
    else:
        db["automod"]["link_spam"] = False
        await ctx.send("🤖 Automod: Link Spam OFF")
    save_data(db)

@bot.command()
@is_bot_admin()
async def anti_raid(ctx, mode: str):
    if mode.lower() == "on":
        db["antiraid"]["basic"] = True
        await ctx.send("🛡️ Anti-Raid Basic: ON")
    else:
        db["antiraid"]["basic"] = False
        await ctx.send("🛡️ Anti-Raid: OFF")
    save_data(db)

@bot.command()
async def giveaway(ctx, time_str: str, *, prize: str):
    # Format: !giveaway 10s Nitro
    try:
        unit = time_str[-1]
        val = int(time_str[:-1])
        seconds = val * 60 if unit == 'm' else val
    except:
        await ctx.send("Invalid format. Use `!giveaway 10s Prize` or `!giveaway 5m Prize`")
        return

    embed = discord.Embed(title="🎉 GIVEAWAY 🎉", description=f"Prize: **{prize}**\nTime: {time_str}\nReact with 🎉 to enter!", color=discord.Color.purple())
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("🎉")
    
    await asyncio.sleep(seconds)
    
    msg = await ctx.channel.fetch_message(msg.id)
    users = [u async for u in msg.reactions[0].users() if not u.bot]
    
    if len(users) > 0:
        winner = random.choice(users)
        await ctx.send(f"🎉 Congratulations {winner.mention}! You won **{prize}**!")
    else:
        await ctx.send("Giveaway ended. No participants.")

@bot.command()
async def suggestion(ctx, *, content):
    channel_id = db["config"]["suggestion_channel"]
    if channel_id:
        channel = bot.get_channel(channel_id)
        embed = discord.Embed(description=content, color=discord.Color.orange())
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar.url)
        msg = await channel.send(embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        await ctx.send("Suggestion sent!")
    else:
        await ctx.send("Suggestion channel not set. Admin must use `!set_sugg_channel`.")

@bot.command()
@is_bot_admin()
async def set_sugg_channel(ctx):
    db["config"]["suggestion_channel"] = ctx.channel.id
    save_data(db)
    await ctx.send("This channel is now the Suggestion channel.")

@bot.command()
@commands.has_permissions(manage_nicknames=True)
async def set_nick(ctx, member: discord.Member, *, nick):
    await member.edit(nick=nick)
    await ctx.send(f"Nickname changed to {nick}")

@bot.command()
@is_bot_admin()
async def reaction_role(ctx, emoji: str, role: discord.Role, *, message_content):
    # Sends a message and sets up reaction role
    msg = await ctx.send(message_content)
    await msg.add_reaction(emoji)
    
    if str(msg.id) not in db["reaction_roles"]: db["reaction_roles"][str(msg.id)] = {}
    db["reaction_roles"][str(msg.id)][emoji] = role.id
    save_data(db)

@bot.command()
@is_bot_admin()
async def counter(ctx, type: str):
    # Types: members, bots, banned
    if type not in ["members", "bots", "banned"]:
        await ctx.send("Invalid type. Use: members, bots, or banned")
        return
    
    # Create voice channel
    try:
        channel = await ctx.guild.create_voice_channel(f"{type.title()}: Loading...")
        db["counters"][str(channel.id)] = type
        save_data(db)
        await ctx.send(f"Counter created: {channel.name}")
    except Exception as e:
        await ctx.send(f"Error creating channel: {e}")

@bot.command()
@is_bot_admin()
async def set_role(ctx, member: discord.Member, role: discord.Role):
    await member.add_roles(role)
    await ctx.send(f"Added {role.name} to {member.name}")

@bot.command()
async def note(ctx, user: discord.User, *, note_content):
    if str(user.id) not in db["notes"]: db["notes"][str(user.id)] = []
    db["notes"][str(user.id)].append(f"{note_content} - {datetime.date.today()}")
    save_data(db)
    await ctx.send("📝 Note added.")

@bot.command()
async def r_note(ctx, user: discord.User):
    notes = db["notes"].get(str(user.id), ["No notes found."])
    await ctx.send(f"**Notes for {user.name}:**\n" + "\n".join(notes))

@bot.command()
async def afk(ctx, *, message="AFK"):
    await ctx.send(f"💤 {ctx.author.mention} is now AFK: {message}")
    # (Nickname change logic usually goes here but requires permission handling)

# --- ADMIN / OWNER COMMANDS ---

@bot.command()
async def add_admin(ctx, user: discord.User):
    if ctx.author.id != OWNER_ID:
        await ctx.send("❌ Only the Bot Owner can add admins.")
        return
    if user.id not in db["admins"]:
        db["admins"].append(user.id)
        save_data(db)
        await ctx.send(f"✅ {user.name} is now a Bot Admin.")

@bot.command()
async def remove_admin(ctx, user: discord.User):
    if ctx.author.id != OWNER_ID: return
    if user.id in db["admins"]:
        db["admins"].remove(user.id)
        save_data(db)
        await ctx.send(f"❌ {user.name} removed from Bot Admins.")

@bot.command()
@is_bot_admin()
async def bot_config(ctx, command_name: str, state: str):
    # Put command in maintenance
    if state == "maintenance":
        db["config"]["maintenance_commands"].append(command_name)
    elif state == "active":
        if command_name in db["config"]["maintenance_commands"]:
            db["config"]["maintenance_commands"].remove(command_name)
    save_data(db)
    await ctx.send(f"Command `{command_name}` is now in {state}.")

@bot.command()
@is_bot_admin()
async def change_s(ctx, status_type: str):
    if status_type == "online": await bot.change_presence(status=discord.Status.online)
    elif status_type == "idle": await bot.change_presence(status=discord.Status.idle)
    elif status_type == "dnd": await bot.change_presence(status=discord.Status.dnd)
    await ctx.send(f"Status changed to {status_type}")

@bot.command()
@is_bot_admin()
async def bot_bio(ctx, *, bio):
    db["bot_bio"] = bio
    save_data(db)
    await bot.change_presence(activity=discord.Game(name=bio))
    await ctx.send("Bot bio/status updated.")

@bot.command()
@is_bot_admin()
async def announce_update(ctx, *, message):
    count = 0
    for guild in bot.guilds:
        # Try finding a suitable channel
        channel = next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)
        if channel:
            try: 
                await channel.send(f"**📢 UPDATE:** {message}")
                count += 1
            except: pass
    await ctx.send(f"Sent update to {count} servers.")

@bot.command()
@is_bot_admin()
async def gpremium(ctx, user: discord.User, duration: str):
    # Duration: "30d", "never"
    if duration == "never":
        expiry = "never"
    else:
        try:
            days = int(duration.replace("d", ""))
            expiry = time.time() + (days * 86400)
        except:
            await ctx.send("Invalid format. Use 30d or never.")
            return
    
    db["premium_users"][str(user.id)] = expiry
    save_data(db)
    await ctx.send(f"💎 Granted Premium to {user.name} for {duration}.")

# --- PREMIUM COMMANDS ---

@bot.command()
@is_premium()
async def change_nick_bot(ctx, *, new_nick):
    await ctx.guild.me.edit(nick=new_nick)
    await ctx.send("My nickname has been changed (Premium).")

@bot.command()
@is_premium()
async def spoiler_image(ctx):
    if not ctx.message.attachments:
        await ctx.send("Please attach an image.")
        return
    
    attachment = ctx.message.attachments[0]
    # Creates a button that sends the image ephemerally
    view = View()
    btn = Button(style=discord.ButtonStyle.gray, label="View Spoiler", emoji="🫣")
    
    async def callback(interaction):
        await interaction.response.send_message(f"|| {attachment.url} ||", ephemeral=True)
    
    btn.callback = callback
    view.add_item(btn)
    
    await ctx.message.delete()
    await ctx.send(f"**Spoiler Image** sent by {ctx.author.mention}", view=view)

@bot.command()
async def help(ctx):
    # Simple help command
    embed = discord.Embed(title="Bot Commands", description="Available commands:", color=discord.Color.blue())
    embed.add_field(name="Moderation", value="warn, mute, ban, blockwords, unblockwords, automode")
    embed.add_field(name="Tickets", value="setup_ticket, ticket_admin, mail_staff")
    embed.add_field(name="Utility", value="avatar, banner, userinfo, membercount, poll, giveaway, suggestion")
    embed.add_field(name="Premium", value="change_nick_bot, spoiler_image")
    await ctx.send(embed=embed)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound): return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Missing arguments.")
    else:
        print(f"Error: {error}")

# --- START ---
if __name__ == "__main__":
    if not TOKEN:
        print("❌ Error: DISCORD_TOKEN not found in .env")
    else:
        try:
            bot.run(TOKEN)
        except Exception as e:
            print(f"❌ Connection Error: {e}")
