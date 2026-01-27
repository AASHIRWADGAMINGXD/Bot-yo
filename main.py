import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import json
import asyncio
import datetime
import re
from flask import Flask
from threading import Thread
from dotenv import load_dotenv

# --- CONFIGURATION & SETUP ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")  # Put your token in .env or Render Environment Variables
PREFIX = "!" 

# Intents
intents = discord.Intents.all()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# --- DATABASE MANAGEMENT (JSON) ---
DATA_FILE = "bot_data.json"

def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "blockwords": [],
            "blocked_role_names": [],
            "notes": {},
            "warns": {},
            "afk": {},
            "automod": True,
            "ticket_config": {"category": None, "log_channel": None, "admin_role": None},
            "reaction_roles": {}
        }
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

db = load_data()

# --- KEEP ALIVE SERVER (FOR RENDER) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive and running!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- EVENTS ---

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print("Syncing commands...")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s).")
    except Exception as e:
        print(e)
    
    check_temp_roles.start()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Processing Tickets"))

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
    if message.author.bot:
        return

    # 1. Modmail / Mail_staff
    if isinstance(message.channel, discord.DMChannel):
        guild = bot.guilds[0] # Assumes bot is mainly for one server, or you can config this
        # Find a modmail channel or create logic
        # For simplicity, we forward to the system channel or owner
        if guild.system_channel:
            embed = discord.Embed(title="📩 New Modmail", description=message.content, color=discord.Color.blue())
            embed.set_author(name=message.author, icon_url=message.author.avatar.url if message.author.avatar else None)
            embed.set_footer(text=f"ID: {message.author.id}")
            await guild.system_channel.send(embed=embed)
            await message.channel.send("Message sent to staff.")
        return

    # 2. AFK System
    if str(message.author.id) in db["afk"]:
        del db["afk"][str(message.author.id)]
        save_data(db)
        await message.channel.send(f"Welcome back {message.author.mention}, I removed your AFK.", delete_after=5)

    for mention in message.mentions:
        if str(mention.id) in db["afk"]:
            reason = db["afk"][str(mention.id)]
            await message.channel.send(f"**{mention.name}** is AFK: {reason}", delete_after=10)

    # 3. Automod (Blockwords & Link Spam)
    if db["automod"] and not message.author.guild_permissions.administrator:
        # Blockwords
        for word in db["blockwords"]:
            if word.lower() in message.content.lower():
                await message.delete()
                await message.channel.send(f"{message.author.mention} That word is not allowed!", delete_after=3)
                return

        # Link Spam
        if re.search(r"(https?://\S+)", message.content):
            # Whitelist logic can be added here
            await message.delete()
            await message.channel.send(f"{message.author.mention} Links are not allowed.", delete_after=3)
            return

    await bot.process_commands(message)

# --- TICKET SYSTEM ---

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.green, emoji="📩", custom_id="create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        category_id = db["ticket_config"]["category"]
        category = guild.get_channel(category_id) if category_id else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        # Add admin role permissions
        if db["ticket_config"]["admin_role"]:
            role = guild.get_role(db["ticket_config"]["admin_role"])
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel_name = f"ticket-{interaction.user.name}"
        channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)

        embed = discord.Embed(title="Ticket Created", description="Support will be with you shortly.\nClick 🔒 to close.", color=discord.Color.green())
        await channel.send(f"{interaction.user.mention} <@&{db['ticket_config']['admin_role']}>" if db['ticket_config']['admin_role'] else interaction.user.mention, embed=embed, view=CloseTicketView())
        
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)

class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, emoji="🔒", custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Closing ticket in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

@bot.command()
@commands.has_permissions(administrator=True)
async def ticket_admin(ctx):
    """Sets up the ticket panel."""
    embed = discord.Embed(title="Support Tickets", description="Click the button below to open a ticket.", color=discord.Color.blurple())
    embed.set_image(url="https://dummyimage.com/600x200/5865F2/fff.png&text=Support+Banner") # Placeholder banner
    await ctx.send(embed=embed, view=TicketView())

@bot.command()
@commands.has_permissions(administrator=True)
async def ticket_config(ctx, category: discord.CategoryChannel, admin_role: discord.Role):
    """Config: !ticket_config <Category> <AdminRole>"""
    db["ticket_config"]["category"] = category.id
    db["ticket_config"]["admin_role"] = admin_role.id
    save_data(db)
    await ctx.send(f"Ticket config updated. Category: {category.name}, Role: {admin_role.name}")

@bot.command()
async def ticket_tag(ctx, user: discord.Member):
    """Add a user to the current ticket."""
    if "ticket-" in ctx.channel.name:
        await ctx.channel.set_permissions(user, read_messages=True, send_messages=True)
        await ctx.send(f"{user.mention} has been added to the ticket.")
    else:
        await ctx.send("This is not a ticket channel.")

# --- MODERATION ---

@bot.hybrid_command(name="blockwords", description="Add a word to the blocklist")
@commands.has_permissions(manage_messages=True)
async def blockwords(ctx, word: str):
    if word not in db["blockwords"]:
        db["blockwords"].append(word)
        save_data(db)
        await ctx.send(f"Added `{word}` to blocklist.")
    else:
        await ctx.send("Word is already blocked.")

@bot.hybrid_command(name="unblockwords", description="Remove a word from the blocklist")
@commands.has_permissions(manage_messages=True)
async def unblockwords(ctx, word: str):
    if word in db["blockwords"]:
        db["blockwords"].remove(word)
        save_data(db)
        await ctx.send(f"Removed `{word}` from blocklist.")
    else:
        await ctx.send("Word not found.")

@bot.hybrid_command(name="bwlist", description="Show all blocked words")
@commands.has_permissions(manage_messages=True)
async def bwlist(ctx):
    words = ", ".join(db["blockwords"])
    await ctx.send(f"**Blocked Words:** {words if words else 'None'}")

@bot.command()
@commands.has_permissions(administrator=True)
async def block_r(ctx, role_name: str):
    """Prevent a role with this name from being created."""
    if role_name not in db["blocked_role_names"]:
        db["blocked_role_names"].append(role_name)
        save_data(db)
        await ctx.send(f"Restricted creation of role name: `{role_name}`")

@bot.hybrid_command()
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member, *, reason="No reason provided"):
    uid = str(member.id)
    if uid not in db["warns"]:
        db["warns"][uid] = []
    
    db["warns"][uid].append({"reason": reason, "mod": ctx.author.name, "date": str(datetime.datetime.now())})
    save_data(db)
    
    embed = discord.Embed(title="User Warned", color=discord.Color.orange())
    embed.add_field(name="User", value=member.mention)
    embed.add_field(name="Reason", value=reason)
    await ctx.send(embed=embed)
    try:
        await member.send(f"You were warned in {ctx.guild.name}: {reason}")
    except:
        pass

@bot.hybrid_command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="No reason"):
    await member.ban(reason=reason)
    await ctx.send(f"🔨 {member.mention} has been banned. Reason: {reason}")

@bot.hybrid_command()
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, minutes: int, *, reason="No reason"):
    duration = datetime.timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    await ctx.send(f"🔇 {member.mention} has been muted for {minutes} minutes.")

@bot.command()
@commands.has_permissions(manage_nicknames=True)
async def set_nick(ctx, member: discord.Member, *, nickname: str):
    await member.edit(nick=nickname)
    await ctx.send(f"Changed nickname for {member.name} to {nickname}")

@bot.command()
@commands.has_permissions(administrator=True)
async def auto_mode(ctx, state: str):
    """Usage: !auto_mode on/off"""
    if state.lower() == "on":
        db["automod"] = True
        await ctx.send("Auto Moderation enabled.")
    elif state.lower() == "off":
        db["automod"] = False
        await ctx.send("Auto Moderation disabled.")
    save_data(db)

# --- UTILITIES ---

@bot.hybrid_command()
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=f"{member.name}'s Avatar", color=discord.Color.purple())
    embed.set_image(url=member.avatar.url if member.avatar else member.default_avatar.url)
    await ctx.send(embed=embed)

@bot.hybrid_command()
async def banner(ctx, member: discord.Member = None):
    member = member or ctx.author
    user = await bot.fetch_user(member.id)
    if user.banner:
        embed = discord.Embed(title=f"{member.name}'s Banner", color=discord.Color.purple())
        embed.set_image(url=user.banner.url)
        await ctx.send(embed=embed)
    else:
        await ctx.send("User does not have a banner.")

@bot.hybrid_command()
async def membercount(ctx):
    await ctx.send(f"👥 **Member Count:** {ctx.guild.member_count}")

@bot.hybrid_command()
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title="User Info", color=member.color)
    embed.set_thumbnail(url=member.avatar.url)
    embed.add_field(name="ID", value=member.id)
    embed.add_field(name="Joined", value=member.joined_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Created", value=member.created_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Top Role", value=member.top_role.mention)
    await ctx.send(embed=embed)

@bot.hybrid_command()
async def roleinfo(ctx, role: discord.Role):
    embed = discord.Embed(title=f"Role Info: {role.name}", color=role.color)
    embed.add_field(name="ID", value=role.id)
    embed.add_field(name="Members", value=len(role.members))
    embed.add_field(name="Mentionable", value=role.mentionable)
    embed.add_field(name="Position", value=role.position)
    await ctx.send(embed=embed)

@bot.hybrid_command()
async def botinvite(ctx):
    permissions = discord.Permissions.all()
    url = discord.utils.oauth_url(bot.user.id, permissions=permissions)
    await ctx.send(f"Invite me here: [Click Me]({url})")

@bot.command()
async def afk(ctx, *, reason="AFK"):
    db["afk"][str(ctx.author.id)] = reason
    save_data(db)
    await ctx.send(f"{ctx.author.mention} I set your AFK: {reason}")
    await ctx.author.edit(nick=f"[AFK] {ctx.author.display_name}"[:32])

# --- CHANNEL & ROLES ---

@bot.command()
@commands.has_permissions(manage_roles=True)
async def set_role(ctx, member: discord.Member, role: discord.Role):
    await member.add_roles(role)
    await ctx.send(f"Added {role.name} to {member.name}")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def set_slowmode(ctx, seconds: int):
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send(f"Slowmode set to {seconds} seconds.")

@bot.command()
async def channel_stats(ctx):
    channel = ctx.channel
    embed = discord.Embed(title=f"Stats for #{channel.name}", color=discord.Color.blue())
    embed.add_field(name="ID", value=channel.id)
    embed.add_field(name="Type", value=str(channel.type))
    embed.add_field(name="Topic", value=channel.topic or "None")
    await ctx.send(embed=embed)

# --- NOTES SYSTEM ---

@bot.command()
@commands.has_permissions(manage_messages=True)
async def note(ctx, user: discord.User, *, note_content):
    uid = str(user.id)
    if uid not in db["notes"]:
        db["notes"][uid] = []
    db["notes"][uid].append(note_content)
    save_data(db)
    await ctx.send(f"Note added for {user.name}")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def edit_note(ctx, user: discord.User, index: int, *, new_content):
    uid = str(user.id)
    if uid in db["notes"] and 0 <= index < len(db["notes"][uid]):
        db["notes"][uid][index] = new_content
        save_data(db)
        await ctx.send("Note updated.")
    else:
        await ctx.send("Note not found.")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def r_note(ctx, user: discord.User, index: int):
    uid = str(user.id)
    if uid in db["notes"] and 0 <= index < len(db["notes"][uid]):
        db["notes"][uid].pop(index)
        save_data(db)
        await ctx.send("Note removed.")
    else:
        await ctx.send("Note not found.")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def view_notes(ctx, user: discord.User):
    uid = str(user.id)
    if uid in db["notes"] and db["notes"][uid]:
        notes_list = "\n".join([f"{i}. {n}" for i, n in enumerate(db["notes"][uid])])
        await ctx.send(f"**Notes for {user.name}:**\n{notes_list}")
    else:
        await ctx.send("No notes found.")

# --- TEMP ROLE SYSTEM ---
# Simplified: Stores role removal time in memory/task loop. 
# For production, this should be in DB to survive restarts.

temp_roles = [] # Format: (user_id, role_id, end_time_timestamp)

@bot.command()
@commands.has_permissions(manage_roles=True)
async def temp_role(ctx, member: discord.Member, role: discord.Role, minutes: int):
    await member.add_roles(role)
    end_time = datetime.datetime.now().timestamp() + (minutes * 60)
    temp_roles.append((member.id, role.id, end_time))
    await ctx.send(f"Given {role.name} to {member.name} for {minutes} minutes.")

@tasks.loop(seconds=60)
async def check_temp_roles():
    current = datetime.datetime.now().timestamp()
    for item in temp_roles[:]:
        uid, rid, end = item
        if current >= end:
            guild = bot.guilds[0] # assuming single server for simplicity
            member = guild.get_member(uid)
            role = guild.get_role(rid)
            if member and role:
                await member.remove_roles(role)
            temp_roles.remove(item)

# --- INTERACTION & COMMUNITY ---

@bot.command()
async def poll(ctx, question, *options):
    if len(options) > 10:
        await ctx.send("Max 10 options.")
        return
    
    emoji_numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    desc = ""
    for i, opt in enumerate(options):
        desc += f"{emoji_numbers[i]} {opt}\n"
        
    embed = discord.Embed(title=f"📊 {question}", description=desc, color=discord.Color.gold())
    msg = await ctx.send(embed=embed)
    
    for i in range(len(options)):
        await msg.add_reaction(emoji_numbers[i])

@bot.command()
@commands.has_permissions(administrator=True)
async def giveaway(ctx, duration_str, prize):
    """Usage: !giveaway 10s/1m/1h Prize Name"""
    unit = duration_str[-1]
    time_val = int(duration_str[:-1])
    seconds = 0
    if unit == 's': seconds = time_val
    elif unit == 'm': seconds = time_val * 60
    elif unit == 'h': seconds = time_val * 3600
    
    embed = discord.Embed(title="🎉 GIVEAWAY 🎉", description=f"Prize: **{prize}**\nReact with 🎉 to enter!\nEnds in: {duration_str}", color=discord.Color.red())
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("🎉")
    
    await asyncio.sleep(seconds)
    
    new_msg = await ctx.channel.fetch_message(msg.id)
    users = [u async for u in new_msg.reactions[0].users() if not u.bot]
    
    if users:
        import random
        winner = random.choice(users)
        await ctx.send(f"🎉 Congratulations {winner.mention}! You won **{prize}**!")
    else:
        await ctx.send("No one entered the giveaway.")

@bot.command()
async def suggestion(ctx, *, content):
    # Sends to a channel named 'suggestions' if it exists
    channel = discord.utils.get(ctx.guild.text_channels, name="suggestions")
    if channel:
        embed = discord.Embed(title="New Suggestion", description=content, color=discord.Color.green())
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
        msg = await channel.send(embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        await ctx.send("Suggestion sent!")
    else:
        await ctx.send("Suggestion channel not found. Please ask admin to create channel named `suggestions`.")

@bot.command()
@commands.has_permissions(administrator=True)
async def announce(ctx, channel: discord.TextChannel, *, message):
    await channel.send(message)
    await ctx.send("Announcement sent.")

@bot.command()
async def dm(ctx, user: discord.User, *, message):
    try:
        await user.send(f"Message from {ctx.author.name}: {message}")
        await ctx.send("DM Sent.")
    except:
        await ctx.send("Could not DM user.")

@bot.command()
async def dm_test(ctx):
    try:
        await ctx.author.send("This is a test DM.")
        await ctx.send("Check your DMs.")
    except:
        await ctx.send("I cannot DM you. Check your privacy settings.")

# --- REACTION ROLES ---
# Very basic implementation
@bot.command()
@commands.has_permissions(administrator=True)
async def reaction_role(ctx, role: discord.Role, emoji: str, *, message_content):
    msg = await ctx.send(f"{message_content}\n\nReact with {emoji} to get {role.mention}")
    await msg.add_reaction(emoji)
    db["reaction_roles"][str(msg.id)] = {"role_id": role.id, "emoji": emoji}
    save_data(db)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.member.bot: return
    msg_id = str(payload.message_id)
    if msg_id in db["reaction_roles"]:
        data = db["reaction_roles"][msg_id]
        if str(payload.emoji) == data["emoji"]:
            guild = bot.get_guild(payload.guild_id)
            role = guild.get_role(data["role_id"])
            await payload.member.add_roles(role)

@bot.event
async def on_raw_reaction_remove(payload):
    msg_id = str(payload.message_id)
    if msg_id in db["reaction_roles"]:
        data = db["reaction_roles"][msg_id]
        if str(payload.emoji) == data["emoji"]:
            guild = bot.get_guild(payload.guild_id)
            member = guild.get_member(payload.user_id)
            role = guild.get_role(data["role_id"])
            if member and role:
                await member.remove_roles(role)

# --- HELP ---

@bot.hybrid_command()
async def help(ctx):
    embed = discord.Embed(title="Bot Help", description="List of Commands", color=discord.Color.dark_theme())
    embed.add_field(name="🛡️ Moderation", value="`warn`, `mute`, `ban`, `kick`, `blockwords`, `unblockwords`, `set_nick`, `auto_mode`, `block_r`", inline=False)
    embed.add_field(name="📩 Tickets", value="`ticket_admin` (setup), `ticket_tag`, `close` (button)", inline=False)
    embed.add_field(name="🛠️ Utility", value="`avatar`, `banner`, `userinfo`, `roleinfo`, `membercount`, `afk`, `channel_stats`", inline=False)
    embed.add_field(name="🎉 Fun/Community", value="`poll`, `giveaway`, `suggestion`, `announce`, `dm`, `dm_test`", inline=False)
    embed.add_field(name="👮 Roles", value="`set_role`, `temp_role`, `reaction_role`", inline=False)
    embed.add_field(name="📝 Notes", value="`note`, `view_notes`, `edit_note`, `r_note`", inline=False)
    await ctx.send(embed=embed)

# --- EXECUTION ---
if __name__ == "__main__":
    keep_alive() # Starts Flask server
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("Error: DISCORD_TOKEN not found in environment.")
