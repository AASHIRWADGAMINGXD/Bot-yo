import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import View, Button
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

# --- CONFIGURATION ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 0))

# --- DATABASE SYSTEM ---
class Database:
    def __init__(self, file="bot_data.json"):
        self.file = file
        self.data = self.load()

    def load(self):
        if not os.path.exists(self.file):
            return {
                "config": {
                    "maintenance": [], "modmail_channel": None, "sugg_channel": None,
                    "automod_links": False, "antiraid": False, "log_channel": None
                },
                "blocklist": [],
                "blocked_roles": [],
                "users": {}, # notes, warns, afk
                "premium": {}, # user_id: expiry_timestamp
                "tickets": {"count": 0, "category": None, "admin_role": None},
                "admins": [],
                "counters": {}, # channel_id: type
                "reaction_roles": {}, # msg_id: {emoji: role_id}
                "bot_bio": "System Active",
                "join_log": []
            }
        try:
            with open(self.file, "r") as f:
                return json.load(f)
        except:
            return {}

    def save(self):
        with open(self.file, "w") as f:
            json.dump(self.data, f, indent=4)

db = Database()

# --- WEB SERVER (Keep Alive) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is Online and Healthy!"

def run_server():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_server)
    t.start()

# --- BOT CLASS ---
class ProfessionalBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.start_time = time.time()

    async def setup_hook(self):
        self.counter_loop.start()
        keep_alive()
        # Register persistent views so buttons work after restart
        self.add_view(TicketLauncher())
        
    async def on_ready(self):
        print(f"✅ Logged in as {self.user}")
        print("🔄 Syncing Slash Commands...")
        try:
            synced = await self.tree.sync()
            print(f"✅ Synced {len(synced)} commands.")
        except Exception as e:
            print(f"❌ Sync Error: {e}")
        await self.change_presence(activity=discord.Game(name=db.data["bot_bio"]))

    @tasks.loop(minutes=10)
    async def counter_loop(self):
        # Updates the names of "Counter" channels
        for channel_id, c_type in list(db.data["counters"].items()):
            try:
                channel = self.get_channel(int(channel_id))
                if channel:
                    count = 0
                    if c_type == "members": count = channel.guild.member_count
                    elif c_type == "bots": count = sum(1 for m in channel.guild.members if m.bot)
                    elif c_type == "banned": 
                        try: count = len([entry async for entry in channel.guild.bans()])
                        except: pass
                    await channel.edit(name=f"{c_type.title()}: {count}")
            except:
                continue

client = ProfessionalBot()

# --- HELPER CHECKS ---
def is_bot_admin(interaction: discord.Interaction):
    return interaction.user.id == OWNER_ID or interaction.user.id in db.data["admins"] or interaction.user.guild_permissions.administrator

def is_premium(interaction: discord.Interaction):
    if interaction.user.id == OWNER_ID: return True
    uid = str(interaction.user.id)
    if uid in db.data["premium"]:
        expiry = db.data["premium"][uid]
        if expiry == "never" or float(expiry) > time.time(): return True
    return False

# --- TICKET VIEWS ---
class TicketControl(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, emoji="🔒", custom_id="tkt_close")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("Ticket closing in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

    @discord.ui.button(label="Transcript", style=discord.ButtonStyle.blurple, emoji="📄", custom_id="tkt_trans")
    async def transcript(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        messages = [f"{m.created_at} - {m.author}: {m.content}" async for m in interaction.channel.history(limit=None, oldest_first=True)]
        output = "\n".join(messages)
        file = discord.File(io.BytesIO(output.encode()), filename=f"transcript-{interaction.channel.name}.txt")
        await interaction.followup.send("Transcript generated:", file=file)

class TicketLauncher(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.green, emoji="📩", custom_id="tkt_open")
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        db.data["tickets"]["count"] += 1
        db.save()
        
        t_name = f"ticket-{db.data['tickets']['count']}"
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True)
        }
        
        # Add Admin Role permissions if set
        if db.data["tickets"]["admin_role"]:
            role = interaction.guild.get_role(db.data["tickets"]["admin_role"])
            if role: overwrites[role] = discord.PermissionOverwrite(read_messages=True)

        category = interaction.guild.get_channel(db.data["tickets"]["category"]) if db.data["tickets"]["category"] else None
        
        try:
            channel = await interaction.guild.create_text_channel(t_name, category=category, overwrites=overwrites)
            embed = discord.Embed(title="Support Ticket", description=f"Welcome {interaction.user.mention}. Describe your issue.", color=discord.Color.green())
            await channel.send(embed=embed, view=TicketControl())
            await interaction.response.send_message(f"Ticket opened: {channel.mention}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error creating channel: {e}", ephemeral=True)

# --- EVENTS & AUTOMOD ---
@client.event
async def on_message(message):
    if message.author.bot: return

    # 1. Modmail (DM to Server)
    if isinstance(message.channel, discord.DMChannel):
        if db.data["config"]["modmail_channel"]:
            for guild in client.guilds:
                chan = guild.get_channel(db.data["config"]["modmail_channel"])
                if chan:
                    embed = discord.Embed(title="📨 Modmail", description=message.content, color=discord.Color.blue())
                    embed.set_author(name=message.author, icon_url=message.author.display_avatar)
                    embed.set_footer(text=f"ID: {message.author.id}")
                    await chan.send(embed=embed)
                    await message.add_reaction("✅")
                    return

    # 2. AFK Check
    if message.mentions:
        for user in message.mentions:
            uid = str(user.id)
            if uid in db.data["users"] and "afk" in db.data["users"][uid]:
                await message.channel.send(f"💤 **{user.name}** is AFK: {db.data['users'][uid]['afk']}", delete_after=5)

    # 3. Blocked Words (AutoMod)
    # The fix: Ensure this logic DOES NOT run on interactions/slash commands
    if not message.content.startswith("/"):
        content = message.content.lower()
        for word in db.data["blocklist"]:
            if word in content:
                try:
                    await message.delete()
                    await message.channel.send(f"⚠️ {message.author.mention}, that word is not allowed.", delete_after=3)
                    return # Stop processing
                except: pass

    # 4. Link Spam
    if db.data["config"]["automod_links"] and ("http://" in message.content or "https://" in message.content):
        if not message.author.guild_permissions.administrator:
            await message.delete()
            await message.channel.send("⚠️ No links allowed.", delete_after=3)
            return
            
    # 5. Crypto/Promo Image Check
    if message.attachments:
        for att in message.attachments:
            fname = att.filename.lower()
            if "promo" in fname or "crypto" in fname or "pump" in fname:
                await message.delete()
                await message.channel.send("⚠️ Suspicious image detected.", delete_after=3)

@client.event
async def on_member_join(member):
    if db.data["config"]["antiraid"]:
        now = time.time()
        db.data["join_log"].append(now)
        # Keep logs only from last 10s
        db.data["join_log"] = [t for t in db.data["join_log"] if now - t < 10]
        db.save()
        
        if len(db.data["join_log"]) > 5:
            try: await member.kick(reason="Anti-Raid Protection")
            except: pass

@client.event
async def on_raw_reaction_add(payload):
    if payload.user_id == client.user.id: return
    msg_id = str(payload.message_id)
    if msg_id in db.data["reaction_roles"]:
        emoji = str(payload.emoji)
        if emoji in db.data["reaction_roles"][msg_id]:
            guild = client.get_guild(payload.guild_id)
            role = guild.get_role(db.data["reaction_roles"][msg_id][emoji])
            member = guild.get_member(payload.user_id)
            if role and member: await member.add_roles(role)

@client.event
async def on_guild_role_create(role):
    if role.name in db.data["blocked_roles"]:
        try: await role.delete(reason="Blocked Role Name")
        except: pass

# --- SLASH COMMAND GROUPS ---

# 1. MODERATION
class Moderation(app_commands.Group):
    def __init__(self):
        super().__init__(name="mod", description="Moderation commands")

    @app_commands.command(description="Ban a user")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, user: discord.Member, reason: str = "None"):
        await user.ban(reason=reason)
        await interaction.response.send_message(f"🔨 Banned {user.name}")

    @app_commands.command(description="Kick a user")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, user: discord.Member, reason: str = "None"):
        await user.kick(reason=reason)
        await interaction.response.send_message(f"👢 Kicked {user.name}")

    @app_commands.command(description="Timeout a user")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mute(self, interaction: discord.Interaction, user: discord.Member, minutes: int, reason: str = "None"):
        await user.timeout(datetime.timedelta(minutes=minutes), reason=reason)
        await interaction.response.send_message(f"😶 Muted {user.name} for {minutes}m")

    @app_commands.command(description="Warn a user")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def warn(self, interaction: discord.Interaction, user: discord.Member, reason: str):
        uid = str(user.id)
        if uid not in db.data["users"]: db.data["users"][uid] = {}
        if "warns" not in db.data["users"][uid]: db.data["users"][uid]["warns"] = []
        db.data["users"][uid]["warns"].append(f"{reason} - {interaction.user.name}")
        db.save()
        await interaction.response.send_message(f"⚠️ Warned {user.name}")

    @app_commands.command(description="Block a word (Fixes auto-deletion bug)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def blockword(self, interaction: discord.Interaction, word: str):
        if word.lower() not in db.data["blocklist"]:
            db.data["blocklist"].append(word.lower())
            db.save()
            await interaction.response.send_message(f"🚫 Blocked: `{word}`")
        else:
            await interaction.response.send_message("Already blocked.")

    @app_commands.command(description="Unblock a word")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def unblockword(self, interaction: discord.Interaction, word: str):
        if word.lower() in db.data["blocklist"]:
            db.data["blocklist"].remove(word.lower())
            db.save()
            await interaction.response.send_message(f"✅ Unblocked: `{word}`")
        else:
            await interaction.response.send_message("Word not found.")

    @app_commands.command(description="See all blocked words")
    async def blocklist(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"Blocked: {', '.join(db.data['blocklist'])}", ephemeral=True)

    @app_commands.command(description="Restrict specific role name creation")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def block_role_name(self, interaction: discord.Interaction, name: str):
        db.data["blocked_roles"].append(name)
        db.save()
        await interaction.response.send_message(f"🚫 Role name restricted: {name}")

    @app_commands.command(description="Set Slowmode")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, seconds: int):
        await interaction.channel.edit(slowmode_delay=seconds)
        await interaction.response.send_message(f"Slowmode: {seconds}s")

    @app_commands.command(description="Toggle Automod (Links)")
    @app_commands.checks.has_permissions(administrator=True)
    async def automod(self, interaction: discord.Interaction, enabled: bool):
        db.data["config"]["automod_links"] = enabled
        db.save()
        await interaction.response.send_message(f"Link Automod: {enabled}")

    @app_commands.command(description="Toggle Anti-Raid")
    @app_commands.checks.has_permissions(administrator=True)
    async def antiraid(self, interaction: discord.Interaction, enabled: bool):
        db.data["config"]["antiraid"] = enabled
        db.save()
        await interaction.response.send_message(f"Anti-Raid: {enabled}")

client.tree.add_command(Moderation())

# 2. ADMIN & TICKETS
class Admin(app_commands.Group):
    def __init__(self):
        super().__init__(name="admin", description="Admin commands")

    @app_commands.command(description="Setup Ticket Panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_ticket(self, interaction: discord.Interaction):
        embed = discord.Embed(title="Support", description="Click below to open a ticket.", color=discord.Color.blue())
        await interaction.channel.send(embed=embed, view=TicketLauncher())
        await interaction.response.send_message("Done.", ephemeral=True)

    @app_commands.command(description="Set Ticket Admin Role")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_role(self, interaction: discord.Interaction, role: discord.Role):
        db.data["tickets"]["admin_role"] = role.id
        db.save()
        await interaction.response.send_message(f"Ticket Admin Role: {role.name}")

    @app_commands.command(description="Set Modmail Channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_modmail(self, interaction: discord.Interaction):
        db.data["config"]["modmail_channel"] = interaction.channel_id
        db.save()
        await interaction.response.send_message("Modmail channel set here.")

    @app_commands.command(description="Set Suggestion Channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_suggestions(self, interaction: discord.Interaction):
        db.data["config"]["sugg_channel"] = interaction.channel_id
        db.save()
        await interaction.response.send_message("Suggestion channel set here.")
    
    @app_commands.command(description="Add Bot Admin (Owner Only)")
    async def add_bot_admin(self, interaction: discord.Interaction, user: discord.User):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("Owner only.", ephemeral=True)
        if user.id not in db.data["admins"]:
            db.data["admins"].append(user.id)
            db.save()
            await interaction.response.send_message(f"Added {user.name} to admins.")

    @app_commands.command(description="Grant Premium")
    async def gpremium(self, interaction: discord.Interaction, user: discord.User, days: int):
        if interaction.user.id != OWNER_ID: return await interaction.response.send_message("Owner only.", ephemeral=True)
        expiry = time.time() + (days * 86400) if days > 0 else "never"
        db.data["premium"][str(user.id)] = expiry
        db.save()
        await interaction.response.send_message(f"💎 Premium granted to {user.name}")

    @app_commands.command(description="Create Live Counter Channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def create_counter(self, interaction: discord.Interaction, type: str):
        if type not in ["members", "bots", "banned"]:
            return await interaction.response.send_message("Invalid type. Use: members, bots, or banned")
        
        vc = await interaction.guild.create_voice_channel(f"{type.title()}: Loading...")
        db.data["counters"][str(vc.id)] = type
        db.save()
        await interaction.response.send_message(f"Counter created: {vc.name}")

    @app_commands.command(description="Set Bot Status")
    async def bot_status(self, interaction: discord.Interaction, text: str):
        if not is_bot_admin(interaction): return await interaction.response.send_message("No permission.", ephemeral=True)
        db.data["bot_bio"] = text
        db.save()
        await client.change_presence(activity=discord.Game(name=text))
        await interaction.response.send_message("Updated.")
    
    @app_commands.command(description="Reaction Role Setup")
    @app_commands.checks.has_permissions(administrator=True)
    async def reaction_role(self, interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
        if message_id not in db.data["reaction_roles"]: db.data["reaction_roles"][message_id] = {}
        db.data["reaction_roles"][message_id][emoji] = role.id
        db.save()
        msg = await interaction.channel.fetch_message(int(message_id))
        await msg.add_reaction(emoji)
        await interaction.response.send_message("Reaction role set.")

client.tree.add_command(Admin())

# 3. UTILITY & DM COMMAND
class Utility(app_commands.Group):
    def __init__(self):
        super().__init__(name="utils", description="Utility commands")

    # --- THE REQUESTED DM COMMAND ---
    @app_commands.command(description="Send DM to User")
    async def dm(self, interaction: discord.Interaction, user: discord.User, message: str):
        # Only admins should use this to prevent spam
        if not is_bot_admin(interaction): 
            return await interaction.response.send_message("Admin only.", ephemeral=True)
        try:
            await user.send(f"**Message from {interaction.guild.name} Staff:**\n{message}")
            await interaction.response.send_message("✅ Sent.", ephemeral=True)
        except:
            await interaction.response.send_message("❌ Failed (User has DMs closed).", ephemeral=True)

    @app_commands.command(description="Show Avatar")
    async def avatar(self, interaction: discord.Interaction, user: discord.User = None):
        user = user or interaction.user
        await interaction.response.send_message(embed=discord.Embed().set_image(url=user.display_avatar.url))

    @app_commands.command(description="Show Banner")
    async def banner(self, interaction: discord.Interaction, user: discord.User = None):
        user = user or interaction.user
        fetched = await client.fetch_user(user.id)
        if fetched.banner:
            await interaction.response.send_message(embed=discord.Embed().set_image(url=fetched.banner.url))
        else:
            await interaction.response.send_message("No banner.", ephemeral=True)

    @app_commands.command(description="Member Count")
    async def membercount(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"Members: {interaction.guild.member_count}")

    @app_commands.command(description="Create Poll")
    async def poll(self, interaction: discord.Interaction, question: str):
        embed = discord.Embed(title="Poll", description=question, color=discord.Color.gold())
        await interaction.response.send_message("Poll created.")
        msg = await interaction.channel.send(embed=embed)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")

    @app_commands.command(description="Giveaway")
    async def giveaway(self, interaction: discord.Interaction, duration: str, prize: str):
        # Parses 10m, 10s
        unit = duration[-1]
        try:
            val = int(duration[:-1])
            secs = val * 60 if unit == "m" else val
        except:
            return await interaction.response.send_message("Format: 10s or 5m")

        embed = discord.Embed(title="🎉 Giveaway", description=f"Prize: **{prize}**\nReact with 🎉", color=discord.Color.purple())
        await interaction.response.send_message("Giveaway started!")
        msg = await interaction.channel.send(embed=embed)
        await msg.add_reaction("🎉")
        
        await asyncio.sleep(secs)
        
        msg = await interaction.channel.fetch_message(msg.id)
        users = [u async for u in msg.reactions[0].users() if not u.bot]
        if users:
            winner = random.choice(users)
            await interaction.channel.send(f"🎉 Winner: {winner.mention} won **{prize}**!")
        else:
            await interaction.channel.send("No entries.")

    @app_commands.command(description="Submit Suggestion")
    async def suggestion(self, interaction: discord.Interaction, content: str):
        cid = db.data["config"]["sugg_channel"]
        if not cid: return await interaction.response.send_message("Suggestion channel not set.", ephemeral=True)
        chan = client.get_channel(cid)
        embed = discord.Embed(description=content, title="New Suggestion", color=discord.Color.orange())
        embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
        msg = await chan.send(embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        await interaction.response.send_message("Sent.", ephemeral=True)

    @app_commands.command(description="Set User Nickname")
    @app_commands.checks.has_permissions(manage_nicknames=True)
    async def setnick(self, interaction: discord.Interaction, user: discord.Member, nick: str):
        await user.edit(nick=nick)
        await interaction.response.send_message(f"Changed to {nick}")

    @app_commands.command(description="Set Role to User")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def setrole(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role):
        await user.add_roles(role)
        await interaction.response.send_message(f"Added {role.name}")

    @app_commands.command(description="Set Note")
    async def note(self, interaction: discord.Interaction, user: discord.User, content: str):
        if not is_bot_admin(interaction): return
        uid = str(user.id)
        if uid not in db.data["users"]: db.data["users"][uid] = {}
        if "notes" not in db.data["users"][uid]: db.data["users"][uid]["notes"] = []
        db.data["users"][uid]["notes"].append(content)
        db.save()
        await interaction.response.send_message("Note saved.")

    @app_commands.command(description="Read Notes")
    async def read_notes(self, interaction: discord.Interaction, user: discord.User):
        if not is_bot_admin(interaction): return
        uid = str(user.id)
        notes = db.data["users"].get(uid, {}).get("notes", ["None"])
        await interaction.response.send_message(f"Notes: {notes}", ephemeral=True)

    @app_commands.command(description="Set AFK")
    async def afk(self, interaction: discord.Interaction, message: str = "AFK"):
        uid = str(interaction.user.id)
        if uid not in db.data["users"]: db.data["users"][uid] = {}
        db.data["users"][uid]["afk"] = message
        db.save()
        await interaction.response.send_message(f"Set AFK: {message}", ephemeral=True)

    @app_commands.command(description="User Info")
    async def userinfo(self, interaction: discord.Interaction, user: discord.Member = None):
        user = user or interaction.user
        embed = discord.Embed(title=user.name)
        embed.add_field(name="ID", value=user.id)
        embed.add_field(name="Joined", value=user.joined_at.strftime("%Y-%m-%d"))
        await interaction.response.send_message(embed=embed)
        
    @app_commands.command(description="Uptime")
    async def uptime(self, interaction: discord.Interaction):
        up = time.time() - client.start_time
        await interaction.response.send_message(f"Uptime: {int(up)} seconds")

client.tree.add_command(Utility())

# 4. PREMIUM
class Premium(app_commands.Group):
    def __init__(self):
        super().__init__(name="premium", description="Premium commands")

    @app_commands.command(description="Change Bot Nickname")
    async def bot_nick(self, interaction: discord.Interaction, nick: str):
        if not is_premium(interaction): return await interaction.response.send_message("Premium only.", ephemeral=True)
        await interaction.guild.me.edit(nick=nick)
        await interaction.response.send_message("Done.")

    @app_commands.command(description="Spoiler Image")
    async def spoiler_img(self, interaction: discord.Interaction, url: str):
        if not is_premium(interaction): return await interaction.response.send_message("Premium only.", ephemeral=True)
        
        view = View()
        btn = Button(label="Show Image", style=discord.ButtonStyle.secondary)
        async def cb(i): await i.response.send_message(f"|| {url} ||", ephemeral=True)
        btn.callback = cb
        view.add_item(btn)
        
        await interaction.response.send_message(f"🔒 **{interaction.user.name}** shared a secret.", view=view)

client.tree.add_command(Premium())

# --- ERROR HANDLER ---
@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(f"Cooldown: {error.retry_after:.2f}s", ephemeral=True)
    elif isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Missing Permissions.", ephemeral=True)
    elif isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("❌ Access Denied (Maintenance/Premium/Admin)", ephemeral=True)
    else:
        print(f"Error: {error}")

if __name__ == "__main__":
    if TOKEN:
        client.run(TOKEN)
    else:
        print("❌ Error: DISCORD_TOKEN not found in .env file.")
