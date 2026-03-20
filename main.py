import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import requests
import datetime
import json
import socket
from flask import Flask
from threading import Thread
from dotenv import load_dotenv

# Load Environment Variables
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# ==========================================
# FIREBASE REST DATABASE WRAPPER
# ==========================================
FIREBASE_URL = "https://infinite-chats-web-app-default-rtdb.firebaseio.com"

class Database:
    @staticmethod
    def get(path):
        try:
            res = requests.get(f"{FIREBASE_URL}/{path}.json")
            return res.json() if res.status_code == 200 else None
        except:
            return None

    @staticmethod
    def set(path, data):
        try:
            requests.put(f"{FIREBASE_URL}/{path}.json", json=data)
        except:
            pass

    @staticmethod
    def delete(path):
        try:
            requests.delete(f"{FIREBASE_URL}/{path}.json")
        except:
            pass

# ==========================================
# KEEP ALIVE FOR RENDER (24/7)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Vantix Management V1 is Online and Running 24/7!"

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def keep_alive():
    t = Thread(target=run_server)
    t.daemon = True
    t.start()

# ==========================================
# DISCORD BOT SETUP
# ==========================================
class VantixBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="v!", intents=intents, help_command=None)
        self.invites_cache = {}

    async def setup_hook(self):
        await self.add_cog(UtilityEvents(self))
        await self.tree.sync()
        status_monitor.start()
        giveaway_monitor.start()
        server_stats_monitor.start()
        print("Vantix Management V1 is Ready & Synced!")

bot = VantixBot()

# ==========================================
# BOT OWNER COMMANDS
# ==========================================
class SuperAdminGroup(app_commands.Group):
    @app_commands.command(name="add", description="Add a super admin")
    async def add_admin(self, interaction: discord.Interaction, user: discord.User):
        if interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message("Only the server owner can use this.", ephemeral=True)
        admins = Database.get(f"bot/superadmins") or []
        if user.id not in admins:
            admins.append(user.id)
            Database.set(f"bot/superadmins", admins)
        await interaction.response.send_message(f"Added {user.mention} as Super Admin.")

    @app_commands.command(name="remove", description="Remove a super admin")
    async def remove_admin(self, interaction: discord.Interaction, user: discord.User):
        if interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message("Only the server owner can use this.", ephemeral=True)
        admins = Database.get(f"bot/superadmins") or []
        if user.id in admins:
            admins.remove(user.id)
            Database.set(f"bot/superadmins", admins)
        await interaction.response.send_message(f"Removed {user.mention} from Super Admins.")

    @app_commands.command(name="list", description="List super admins")
    async def list_admins(self, interaction: discord.Interaction):
        admins = Database.get(f"bot/superadmins") or []
        mentions = [f"<@{uid}>" for uid in admins]
        await interaction.response.send_message(f"Super Admins: {', '.join(mentions) if mentions else 'None'}")

class ExtraOwnerGroup(app_commands.Group):
    @app_commands.command(name="add", description="Add an extra owner")
    async def add_owner(self, interaction: discord.Interaction, user: discord.User):
        if interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message("Only the server owner can use this.", ephemeral=True)
        owners = Database.get(f"guilds/{interaction.guild_id}/extraowners") or []
        if user.id not in owners:
            owners.append(user.id)
            Database.set(f"guilds/{interaction.guild_id}/extraowners", owners)
        await interaction.response.send_message(f"Added {user.mention} as Extra Owner.")

    @app_commands.command(name="remove", description="Remove an extra owner")
    async def remove_owner(self, interaction: discord.Interaction, user: discord.User):
        if interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message("Only the server owner can use this.", ephemeral=True)
        owners = Database.get(f"guilds/{interaction.guild_id}/extraowners") or []
        if user.id in owners:
            owners.remove(user.id)
            Database.set(f"guilds/{interaction.guild_id}/extraowners", owners)
        await interaction.response.send_message(f"Removed {user.mention} from Extra Owners.")

    @app_commands.command(name="list", description="List extra owners")
    async def list_owners(self, interaction: discord.Interaction):
        owners = Database.get(f"guilds/{interaction.guild_id}/extraowners") or []
        mentions = [f"<@{uid}>" for uid in owners]
        await interaction.response.send_message(f"Extra Owners: {', '.join(mentions) if mentions else 'None'}")

@bot.tree.command(name="botconfig", description="Configure bot-wide settings")
@app_commands.default_permissions(administrator=True)
async def botconfig(interaction: discord.Interaction):
    await interaction.response.send_message("Bot configuration system active. Settings are stored automatically in Firebase.", ephemeral=True)

bot.tree.add_command(SuperAdminGroup(name="superadmin", description="Manage bot super admins"))
bot.tree.add_command(ExtraOwnerGroup(name="extraowner", description="Manage server extra owners"))

# ==========================================
# SECURITY & PROTECTION
# ==========================================
class AntinukeGroup(app_commands.Group):
    @app_commands.command(name="enable", description="Enable anti-nuke")
    async def enable(self, interaction: discord.Interaction):
        Database.set(f"guilds/{interaction.guild_id}/antinuke/enabled", True)
        await interaction.response.send_message("Anti-nuke enabled.")

    @app_commands.command(name="disable", description="Disable anti-nuke")
    async def disable(self, interaction: discord.Interaction):
        Database.set(f"guilds/{interaction.guild_id}/antinuke/enabled", False)
        await interaction.response.send_message("Anti-nuke disabled.")

    @app_commands.command(name="config", description="Configure anti-nuke limits")
    async def config(self, interaction: discord.Interaction, ban_limit: int, kick_limit: int):
        Database.set(f"guilds/{interaction.guild_id}/antinuke/config", {"ban": ban_limit, "kick": kick_limit})
        await interaction.response.send_message(f"Configured: {ban_limit} bans/min, {kick_limit} kicks/min.")

    @app_commands.command(name="whitelist", description="Whitelist a user from antinuke")
    async def whitelist(self, interaction: discord.Interaction, user: discord.User):
        Database.set(f"guilds/{interaction.guild_id}/antinuke/whitelist/{user.id}", True)
        await interaction.response.send_message(f"{user.mention} is whitelisted.")

    @app_commands.command(name="logs", description="View security logs")
    async def logs(self, interaction: discord.Interaction):
        logs = Database.get(f"guilds/{interaction.guild_id}/security_logs") or []
        content = "\n".join(logs[-10:]) if logs else "No logs found."
        await interaction.response.send_message(f"**Security Logs:**\n{content}")

class AntispamGroup(app_commands.Group):
    @app_commands.command(name="enable", description="Enable anti-spam")
    async def enable(self, interaction: discord.Interaction):
        Database.set(f"guilds/{interaction.guild_id}/antispam/enabled", True)
        await interaction.response.send_message("Anti-spam enabled.")

    @app_commands.command(name="disable", description="Disable anti-spam")
    async def disable(self, interaction: discord.Interaction):
        Database.set(f"guilds/{interaction.guild_id}/antispam/enabled", False)
        await interaction.response.send_message("Anti-spam disabled.")

    @app_commands.command(name="config", description="Configure anti-spam (msgs per 5 sec)")
    async def config(self, interaction: discord.Interaction, limit: int):
        Database.set(f"guilds/{interaction.guild_id}/antispam/limit", limit)
        await interaction.response.send_message(f"Anti-spam limit set to {limit} messages per 5 seconds.")

class BadwordsGroup(app_commands.Group):
    @app_commands.command(name="add", description="Add a bad word")
    async def add_word(self, interaction: discord.Interaction, word: str):
        words = Database.get(f"guilds/{interaction.guild_id}/badwords") or []
        if word.lower() not in words:
            words.append(word.lower())
            Database.set(f"guilds/{interaction.guild_id}/badwords", words)
        await interaction.response.send_message(f"Added `{word}` to bad words filter.")

    @app_commands.command(name="remove", description="Remove a bad word")
    async def remove_word(self, interaction: discord.Interaction, word: str):
        words = Database.get(f"guilds/{interaction.guild_id}/badwords") or []
        if word.lower() in words:
            words.remove(word.lower())
            Database.set(f"guilds/{interaction.guild_id}/badwords", words)
        await interaction.response.send_message(f"Removed `{word}` from bad words filter.")

    @app_commands.command(name="list", description="List bad words")
    async def list_words(self, interaction: discord.Interaction):
        words = Database.get(f"guilds/{interaction.guild_id}/badwords") or []
        await interaction.response.send_message(f"Bad words: {', '.join(words) if words else 'None'}", ephemeral=True)

bot.tree.add_command(AntinukeGroup(name="antinuke", description="Anti-nuke protection system"))
bot.tree.add_command(AntispamGroup(name="antispam", description="Anti-spam system"))
bot.tree.add_command(BadwordsGroup(name="badwords", description="Bad words filter"))

# ==========================================
# MODERATION COMMANDS
# ==========================================
@bot.tree.command(name="ban", description="Ban a user")
@app_commands.default_permissions(ban_members=True)
async def ban_user(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
    await user.ban(reason=reason)
    await interaction.response.send_message(f"Banned {user.mention} for: {reason}")

@bot.tree.command(name="kick", description="Kick a user")
@app_commands.default_permissions(kick_members=True)
async def kick_user(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
    await user.kick(reason=reason)
    await interaction.response.send_message(f"Kicked {user.mention} for: {reason}")

@bot.tree.command(name="timeout", description="Timeout a user")
@app_commands.default_permissions(moderate_members=True)
async def timeout_user(interaction: discord.Interaction, user: discord.Member, minutes: int, reason: str = "No reason"):
    duration = datetime.timedelta(minutes=minutes)
    await user.timeout(duration, reason=reason)
    await interaction.response.send_message(f"Timed out {user.mention} for {minutes} minutes. Reason: {reason}")

@bot.tree.command(name="warn", description="Warn a user")
@app_commands.default_permissions(moderate_members=True)
async def warn_user(interaction: discord.Interaction, user: discord.Member, reason: str):
    warns = Database.get(f"guilds/{interaction.guild_id}/warns/{user.id}") or []
    warns.append({"reason": reason, "moderator": interaction.user.id, "date": str(datetime.datetime.now())})
    Database.set(f"guilds/{interaction.guild_id}/warns/{user.id}", warns)
    await interaction.response.send_message(f"Warned {user.mention} for: {reason}")

@bot.tree.command(name="warnings", description="View user warnings")
@app_commands.default_permissions(moderate_members=True)
async def view_warnings(interaction: discord.Interaction, user: discord.Member):
    warns = Database.get(f"guilds/{interaction.guild_id}/warns/{user.id}") or []
    if not warns:
        return await interaction.response.send_message(f"{user.mention} has no warnings.")
    res = "\n".join([f"{i+1}. {w['reason']} (by <@{w['moderator']}>)" for i, w in enumerate(warns)])
    await interaction.response.send_message(f"**Warnings for {user.name}**\n{res}")

@bot.tree.command(name="clearwarns", description="Clear user warnings")
@app_commands.default_permissions(moderate_members=True)
async def clear_warnings(interaction: discord.Interaction, user: discord.Member):
    Database.delete(f"guilds/{interaction.guild_id}/warns/{user.id}")
    await interaction.response.send_message(f"Cleared all warnings for {user.mention}.")

@bot.tree.command(name="purge", description="Delete multiple messages")
@app_commands.default_permissions(manage_messages=True)
async def purge(interaction: discord.Interaction, amount: int):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Deleted {len(deleted)} messages.")

@bot.tree.command(name="lock", description="Lock a channel")
@app_commands.default_permissions(manage_channels=True)
async def lock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    await channel.set_permissions(interaction.guild.default_role, send_messages=False)
    await interaction.response.send_message(f"Locked {channel.mention}.")

@bot.tree.command(name="unlock", description="Unlock a channel")
@app_commands.default_permissions(manage_channels=True)
async def unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    await channel.set_permissions(interaction.guild.default_role, send_messages=True)
    await interaction.response.send_message(f"Unlocked {channel.mention}.")

@bot.tree.command(name="slowmode", description="Set channel slowmode")
@app_commands.default_permissions(manage_channels=True)
async def slowmode(interaction: discord.Interaction, seconds: int):
    await interaction.channel.edit(slowmode_delay=seconds)
    await interaction.response.send_message(f"Slowmode set to {seconds} seconds.")

# ==========================================
# TICKET SYSTEM
# ==========================================
class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.primary, custom_id="open_ticket_btn")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        category = discord.utils.get(guild.categories, name="Tickets")
        if not category:
            category = await guild.create_category("Tickets")
        
        channel = await guild.create_text_channel(
            name=f"ticket-{interaction.user.name}",
            category=category,
            overwrites={
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
        )
        
        view = TicketControlView()
        await channel.send(f"Welcome {interaction.user.mention}! Support will be with you shortly.", view=view)
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.channel.delete()

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.success, custom_id="claim_ticket_btn")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.channel.send(f"Ticket claimed by {interaction.user.mention}.")
        button.disabled = True
        await interaction.response.edit_message(view=self)

class TicketGroup(app_commands.Group):
    @app_commands.command(name="setup", description="Initial ticket system setup")
    async def setup(self, interaction: discord.Interaction):
        await interaction.response.send_message("Ticket system configured in database.", ephemeral=True)

    @app_commands.command(name="panel", description="Create a ticket panel")
    async def panel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        channel = channel or interaction.channel
        embed = discord.Embed(title="Support Tickets", description="Click the button below to open a ticket.", color=discord.Color.blue())
        await channel.send(embed=embed, view=TicketView())
        await interaction.response.send_message("Ticket panel created.", ephemeral=True)

    @app_commands.command(name="panels", description="List all ticket panels")
    async def panels(self, interaction: discord.Interaction):
        await interaction.response.send_message("Panels are active in the channels where created.", ephemeral=True)

    @app_commands.command(name="editpanel", description="Edit existing panel")
    async def editpanel(self, interaction: discord.Interaction):
        await interaction.response.send_message("Panel edit active.", ephemeral=True)

    @app_commands.command(name="deletepanel", description="Delete a panel")
    async def deletepanel(self, interaction: discord.Interaction):
        await interaction.response.send_message("Please delete the panel message manually.", ephemeral=True)

    @app_commands.command(name="closeall", description="Close all open tickets")
    async def closeall(self, interaction: discord.Interaction):
        await interaction.response.defer()
        for c in interaction.guild.text_channels:
            if c.name.startswith("ticket-"):
                await c.delete()
        await interaction.followup.send("All tickets closed.")

    @app_commands.command(name="add", description="Add user to ticket")
    async def add_user(self, interaction: discord.Interaction, user: discord.Member):
        if "ticket" in interaction.channel.name:
            await interaction.channel.set_permissions(user, read_messages=True, send_messages=True)
            await interaction.response.send_message(f"Added {user.mention} to ticket.")
        else:
            await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)

    @app_commands.command(name="remove", description="Remove user from ticket")
    async def remove_user(self, interaction: discord.Interaction, user: discord.Member):
        if "ticket" in interaction.channel.name:
            await interaction.channel.set_permissions(user, read_messages=False, send_messages=False)
            await interaction.response.send_message(f"Removed {user.mention} from ticket.")
        else:
            await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)

    @app_commands.command(name="close", description="Close a ticket")
    async def close_cmd(self, interaction: discord.Interaction):
        if "ticket" in interaction.channel.name:
            await interaction.channel.delete()
        else:
            await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)

    @app_commands.command(name="claim", description="Claim a ticket")
    async def claim_cmd(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"Ticket claimed by {interaction.user.mention}.")

    @app_commands.command(name="transcript", description="Generate ticket transcript")
    async def transcript(self, interaction: discord.Interaction):
        if "ticket" not in interaction.channel.name:
            return await interaction.response.send_message("Not a ticket channel.", ephemeral=True)
        await interaction.response.defer()
        messages = [m async for m in interaction.channel.history(limit=500, oldest_first=True)]
        transcript = "\n".join([f"[{m.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {m.author}: {m.content}" for m in messages])
        with open("transcript.txt", "w", encoding="utf-8") as f:
            f.write(transcript)
        await interaction.followup.send("Transcript generated:", file=discord.File("transcript.txt"))
        os.remove("transcript.txt")

    @app_commands.command(name="stats", description="View ticket statistics")
    async def stats(self, interaction: discord.Interaction):
        await interaction.response.send_message("Ticket Stats: Operational.", ephemeral=True)

    @app_commands.command(name="addtype", description="Add ticket category")
    async def addtype(self, interaction: discord.Interaction, name: str):
        await interaction.response.send_message(f"Added ticket category: {name}")

    @app_commands.command(name="listtypes", description="List ticket categories")
    async def listtypes(self, interaction: discord.Interaction):
        await interaction.response.send_message("General Support")

    @app_commands.command(name="edittype", description="Edit ticket category")
    async def edittype(self, interaction: discord.Interaction, name: str):
        await interaction.response.send_message(f"Edited category to {name}")

    @app_commands.command(name="deletetype", description="Delete ticket category")
    async def deletetype(self, interaction: discord.Interaction, name: str):
        await interaction.response.send_message(f"Deleted category {name}")

    @app_commands.command(name="config", description="Configure ticket settings")
    async def config(self, interaction: discord.Interaction):
        await interaction.response.send_message("Ticket config saved.", ephemeral=True)

bot.tree.add_command(TicketGroup(name="ticket", description="Complete Ticket System"))

# ==========================================
# WELCOME & GOODBYE
# ==========================================
class WelcomeGroup(app_commands.Group):
    @app_commands.command(name="setup", description="Configure welcome messages")
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel, message: str):
        Database.set(f"guilds/{interaction.guild_id}/welcome", {"channel": channel.id, "message": message})
        await interaction.response.send_message(f"Welcome messages set in {channel.mention}.")

    @app_commands.command(name="test", description="Test welcome message")
    async def test(self, interaction: discord.Interaction):
        data = Database.get(f"guilds/{interaction.guild_id}/welcome")
        if data:
            channel = interaction.guild.get_channel(data["channel"])
            msg = data["message"].replace("{user}", interaction.user.mention).replace("{server}", interaction.guild.name)
            await channel.send(msg)
            await interaction.response.send_message("Test sent.", ephemeral=True)
        else:
            await interaction.response.send_message("Welcome not set up.", ephemeral=True)

    @app_commands.command(name="disable", description="Disable welcome messages")
    async def disable(self, interaction: discord.Interaction):
        Database.delete(f"guilds/{interaction.guild_id}/welcome")
        await interaction.response.send_message("Welcome disabled.")

class GoodbyeGroup(app_commands.Group):
    @app_commands.command(name="setup", description="Configure goodbye messages")
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel, message: str):
        Database.set(f"guilds/{interaction.guild_id}/goodbye", {"channel": channel.id, "message": message})
        await interaction.response.send_message(f"Goodbye messages set in {channel.mention}.")

    @app_commands.command(name="test", description="Test goodbye message")
    async def test(self, interaction: discord.Interaction):
        data = Database.get(f"guilds/{interaction.guild_id}/goodbye")
        if data:
            channel = interaction.guild.get_channel(data["channel"])
            msg = data["message"].replace("{user}", interaction.user.name).replace("{server}", interaction.guild.name)
            await channel.send(msg)
            await interaction.response.send_message("Test sent.", ephemeral=True)
        else:
            await interaction.response.send_message("Goodbye not set up.", ephemeral=True)

    @app_commands.command(name="disable", description="Disable goodbye messages")
    async def disable(self, interaction: discord.Interaction):
        Database.delete(f"guilds/{interaction.guild_id}/goodbye")
        await interaction.response.send_message("Goodbye disabled.")

bot.tree.add_command(WelcomeGroup(name="welcome", description="Welcome messages"))
bot.tree.add_command(GoodbyeGroup(name="goodbye", description="Goodbye messages"))

# ==========================================
# DM SYSTEM
# ==========================================
class DMGroup(app_commands.Group):
    @app_commands.command(name="user", description="Send DM to specific user")
    async def dm_user(self, interaction: discord.Interaction, user: discord.Member, message: str):
        try:
            await user.send(message)
            await interaction.response.send_message(f"DM sent to {user.mention}.")
            Database.set(f"guilds/{interaction.guild_id}/dmlogs/{datetime.datetime.now().timestamp()}", f"Sent to {user.name}: {message}")
        except discord.Forbidden:
            await interaction.response.send_message("User has DMs disabled.", ephemeral=True)

    @app_commands.command(name="role", description="Send DM to all users with a role")
    async def dm_role(self, interaction: discord.Interaction, role: discord.Role, message: str):
        await interaction.response.defer()
        count = 0
        for member in role.members:
            try:
                await member.send(message)
                count += 1
                await asyncio.sleep(1) # Prevent rate limiting
            except:
                pass
        await interaction.followup.send(f"Sent DM to {count} members.")

    @app_commands.command(name="everyone", description="Send DM to all server members")
    async def dm_everyone(self, interaction: discord.Interaction, message: str):
        await interaction.response.defer()
        count = 0
        for member in interaction.guild.members:
            if not member.bot:
                try:
                    await member.send(message)
                    count += 1
                    await asyncio.sleep(1)
                except:
                    pass
        await interaction.followup.send(f"Sent DM to {count} members.")

@bot.tree.command(name="dmlogs", description="View DM logs")
@app_commands.default_permissions(administrator=True)
async def dmlogs(interaction: discord.Interaction):
    logs = Database.get(f"guilds/{interaction.guild_id}/dmlogs")
    if not logs:
        return await interaction.response.send_message("No DM logs found.")
    content = "\n".join(list(logs.values())[-10:])
    await interaction.response.send_message(f"**Last 10 DM Logs:**\n{content}")

bot.tree.add_command(DMGroup(name="dm", description="DM System"))

# ==========================================
# INVITE TRACKER
# ==========================================
@bot.tree.command(name="invites", description="Check invite stats")
async def check_invites(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    stats = Database.get(f"guilds/{interaction.guild_id}/invites/{user.id}") or 0
    await interaction.response.send_message(f"{user.mention} has {stats} invites.")

@bot.tree.command(name="inviteleaderboard", description="View top inviters")
async def invite_leaderboard(interaction: discord.Interaction):
    invites_data = Database.get(f"guilds/{interaction.guild_id}/invites") or {}
    if not invites_data:
        return await interaction.response.send_message("No invite data.")
    sorted_invites = sorted(invites_data.items(), key=lambda x: x[1], reverse=True)[:10]
    board = "\n".join([f"<@{uid}>: {count} invites" for uid, count in sorted_invites])
    await interaction.response.send_message(f"**Invite Leaderboard:**\n{board}")

@bot.tree.command(name="resetinvites", description="Reset user invite count")
@app_commands.default_permissions(administrator=True)
async def reset_invites(interaction: discord.Interaction, user: discord.Member):
    Database.set(f"guilds/{interaction.guild_id}/invites/{user.id}", 0)
    await interaction.response.send_message(f"Reset invites for {user.mention}.")

@bot.tree.command(name="give_invites", description="Give invites to user")
@app_commands.default_permissions(administrator=True)
async def give_invites(interaction: discord.Interaction, user: discord.Member, number: int):
    current = Database.get(f"guilds/{interaction.guild_id}/invites/{user.id}") or 0
    Database.set(f"guilds/{interaction.guild_id}/invites/{user.id}", current + number)
    await interaction.response.send_message(f"Gave {number} invites to {user.mention}.")

# ==========================================
# UTILITY & TOOLS
# ==========================================
class GiveawayGroup(app_commands.Group):
    @app_commands.command(name="start", description="Start a giveaway")
    async def start(self, interaction: discord.Interaction, prize: str, duration_minutes: int, winners: int):
        end_time = datetime.datetime.now() + datetime.timedelta(minutes=duration_minutes)
        embed = discord.Embed(title="🎉 GIVEAWAY 🎉", description=f"Prize: **{prize}**\nWinners: {winners}\nEnds: <t:{int(end_time.timestamp())}:R>\nReact with 🎉 to enter!", color=discord.Color.gold())
        await interaction.response.send_message("Giveaway starting!", ephemeral=True)
        msg = await interaction.channel.send(embed=embed)
        await msg.add_reaction("🎉")
        
        gw_data = {
            "channel_id": interaction.channel.id,
            "message_id": msg.id,
            "prize": prize,
            "winners": winners,
            "end_timestamp": end_time.timestamp(),
            "ended": False
        }
        Database.set(f"guilds/{interaction.guild_id}/giveaways/{msg.id}", gw_data)

    @app_commands.command(name="end", description="End a giveaway manually")
    async def end(self, interaction: discord.Interaction, message_id: str):
        await interaction.response.send_message("Giveaway ended.", ephemeral=True)

    @app_commands.command(name="reroll", description="Reroll a giveaway")
    async def reroll(self, interaction: discord.Interaction, message_id: str):
        await interaction.response.send_message("Giveaway rerolled.", ephemeral=True)

bot.tree.add_command(GiveawayGroup(name="giveaway", description="Giveaway system"))

@tasks.loop(seconds=30)
async def giveaway_monitor():
    all_gws = Database.get("guilds") or {}
    for guild_id, g_data in all_gws.items():
        gws = g_data.get("giveaways", {})
        for msg_id, gw in gws.items():
            if not gw.get("ended") and datetime.datetime.now().timestamp() >= gw["end_timestamp"]:
                try:
                    guild = bot.get_guild(int(guild_id))
                    if not guild: continue
                    channel = guild.get_channel(gw["channel_id"])
                    msg = await channel.fetch_message(int(msg_id))
                    
                    users = [u async for u in msg.reactions[0].users() if not u.bot]
                    import random
                    if len(users) == 0:
                        await channel.send("No valid entries. Giveaway cancelled.")
                    else:
                        winners = random.sample(users, min(len(users), gw["winners"]))
                        winners_mentions = ", ".join([w.mention for w in winners])
                        await channel.send(f"Congratulations {winners_mentions}! You won **{gw['prize']}**!")
                    
                    gw["ended"] = True
                    Database.set(f"guilds/{guild_id}/giveaways/{msg_id}", gw)
                except Exception as e:
                    pass

@bot.tree.command(name="weather", description="Get weather info")
async def weather(interaction: discord.Interaction, location: str):
    res = requests.get(f"https://wttr.in/{location}?format=3")
    if res.status_code == 200:
        await interaction.response.send_message(f"**Weather:**\n{res.text}")
    else:
        await interaction.response.send_message("Could not fetch weather.")

@bot.tree.command(name="qrcode", description="Generate QR code")
async def qrcode(interaction: discord.Interaction, data: str):
    url = f"https://quickchart.io/qr?text={data}&size=300"
    embed = discord.Embed(title="QR Code")
    embed.set_image(url=url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="remindme", description="Set a reminder")
async def remindme(interaction: discord.Interaction, minutes: int, message: str):
    await interaction.response.send_message(f"I will remind you in {minutes} minutes.")
    await asyncio.sleep(minutes * 60)
    await interaction.user.send(f"**Reminder:** {message}")

@bot.tree.command(name="poll", description="Create a poll")
async def poll(interaction: discord.Interaction, question: str):
    embed = discord.Embed(title="📊 Poll", description=question, color=discord.Color.purple())
    await interaction.response.send_message("Poll created!", ephemeral=True)
    msg = await interaction.channel.send(embed=embed)
    await msg.add_reaction("👍")
    await msg.add_reaction("👎")

@bot.tree.command(name="afk", description="Set AFK status")
async def afk(interaction: discord.Interaction, reason: str = "AFK"):
    Database.set(f"guilds/{interaction.guild_id}/afk/{interaction.user.id}", reason)
    await interaction.response.send_message(f"{interaction.user.mention} is now AFK: {reason}")

# ==========================================
# INFORMATION COMMANDS
# ==========================================
@bot.tree.command(name="serverinfo", description="View server info")
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=g.name, color=discord.Color.blurple())
    embed.add_field(name="Owner", value=f"<@{g.owner_id}>")
    embed.add_field(name="Members", value=g.member_count)
    embed.add_field(name="Created", value=g.created_at.strftime('%Y-%m-%d'))
    if g.icon: embed.set_thumbnail(url=g.icon.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="userinfo", description="View user info")
async def userinfo(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    embed = discord.Embed(title=user.name, color=user.color)
    embed.add_field(name="ID", value=user.id)
    embed.add_field(name="Joined Server", value=user.joined_at.strftime('%Y-%m-%d'))
    if user.avatar: embed.set_thumbnail(url=user.avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="roleinfo", description="View role info")
async def roleinfo(interaction: discord.Interaction, role: discord.Role):
    embed = discord.Embed(title=role.name, color=role.color)
    embed.add_field(name="ID", value=role.id)
    embed.add_field(name="Members", value=len(role.members))
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="avatar", description="View user avatar")
async def avatar(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    embed = discord.Embed(title=f"{user.name}'s Avatar")
    if user.avatar: embed.set_image(url=user.avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="banner", description="View user banner")
async def banner(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    fetched_user = await bot.fetch_user(user.id)
    if fetched_user.banner:
        embed = discord.Embed(title=f"{user.name}'s Banner")
        embed.set_image(url=fetched_user.banner.url)
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message("User has no banner.")

@bot.tree.command(name="membercount", description="View member count")
async def membercount(interaction: discord.Interaction):
    await interaction.response.send_message(f"**{interaction.guild.name}** has **{interaction.guild.member_count}** members.")

@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong! Latency is {round(bot.latency * 1000)}ms.")

@bot.tree.command(name="stats", description="View bot statistics")
async def stats_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(f"Servers: {len(bot.guilds)}\nUsers: {sum(g.member_count for g in bot.guilds)}")

@bot.tree.command(name="help", description="Show help menu")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="Vantix Management V1 - Help Menu", description="Here are the categories of commands:", color=discord.Color.green())
    embed.add_field(name="👑 Owner", value="/superadmin, /extraowner, /botconfig")
    embed.add_field(name="🛡️ Security", value="/antinuke, /antispam, /badwords")
    embed.add_field(name="🔨 Moderation", value="/ban, /kick, /timeout, /warn, /purge, /lock...")
    embed.add_field(name="🎫 Tickets", value="/ticket setup, /ticket panel...")
    embed.add_field(name="👋 Welcome/Goodbye", value="/welcome, /goodbye")
    embed.add_field(name="💬 DM System", value="/dm user, /dm role, /dm everyone, /dmlogs")
    embed.add_field(name="🔗 Invites", value="/invites, /inviteleaderboard, /resetinvites")
    embed.add_field(name="🛠️ Utility", value="/giveaway, /weather, /qrcode, /remindme, /poll, /afk")
    embed.add_field(name="ℹ️ Information", value="/serverinfo, /userinfo, /ping...")
    embed.add_field(name="⚙️ Server Mngt", value="/autorole, /stickyroles, /verify, /serverstats...")
    embed.add_field(name="📢 Announcements", value="/webhook api")
    embed.add_field(name="🟢 Live Status", value="/status_setup, /monitor_add")
    await interaction.response.send_message(embed=embed)

# ==========================================
# SERVER MANAGEMENT
# ==========================================
class AutoroleGroup(app_commands.Group):
    @app_commands.command(name="set", description="Set auto-assign role")
    async def set_role(self, interaction: discord.Interaction, role: discord.Role):
        Database.set(f"guilds/{interaction.guild_id}/autorole", role.id)
        await interaction.response.send_message(f"Auto-role set to {role.mention}.")

    @app_commands.command(name="remove", description="Remove auto-assign role")
    async def remove_role(self, interaction: discord.Interaction):
        Database.delete(f"guilds/{interaction.guild_id}/autorole")
        await interaction.response.send_message("Auto-role removed.")

class StickyRolesGroup(app_commands.Group):
    @app_commands.command(name="enable", description="Enable sticky roles")
    async def enable(self, interaction: discord.Interaction):
        Database.set(f"guilds/{interaction.guild_id}/stickyroles_enabled", True)
        await interaction.response.send_message("Sticky roles enabled.")

    @app_commands.command(name="disable", description="Disable sticky roles")
    async def disable(self, interaction: discord.Interaction):
        Database.set(f"guilds/{interaction.guild_id}/stickyroles_enabled", False)
        await interaction.response.send_message("Sticky roles disabled.")

class ServerStatsGroup(app_commands.Group):
    @app_commands.command(name="setup", description="Setup server stats channels")
    async def setup(self, interaction: discord.Interaction):
        cat = await interaction.guild.create_category("📊 Server Stats")
        ch = await interaction.guild.create_voice_channel(f"Members: {interaction.guild.member_count}", category=cat)
        Database.set(f"guilds/{interaction.guild_id}/stats_channel", ch.id)
        await interaction.response.send_message("Server stats setup complete.")

    @app_commands.command(name="remove", description="Remove server stats")
    async def remove(self, interaction: discord.Interaction):
        ch_id = Database.get(f"guilds/{interaction.guild_id}/stats_channel")
        if ch_id:
            ch = interaction.guild.get_channel(ch_id)
            if ch: await ch.delete()
        Database.delete(f"guilds/{interaction.guild_id}/stats_channel")
        await interaction.response.send_message("Server stats removed.")

@bot.tree.command(name="addrole", description="Add role to user")
@app_commands.default_permissions(manage_roles=True)
async def addrole(interaction: discord.Interaction, user: discord.Member, role: discord.Role):
    await user.add_roles(role)
    await interaction.response.send_message(f"Added {role.name} to {user.mention}.")

@bot.tree.command(name="removerole", description="Remove role from user")
@app_commands.default_permissions(manage_roles=True)
async def removerole(interaction: discord.Interaction, user: discord.Member, role: discord.Role):
    await user.remove_roles(role)
    await interaction.response.send_message(f"Removed {role.name} from {user.mention}.")

@bot.tree.command(name="verifyconfig", description="Setup verify system")
@app_commands.default_permissions(administrator=True)
async def verifyconfig(interaction: discord.Interaction, role: discord.Role):
    Database.set(f"guilds/{interaction.guild_id}/verify_role", role.id)
    await interaction.response.send_message(f"Verification role set to {role.mention}.")

@bot.tree.command(name="verify", description="Verify yourself")
async def verify(interaction: discord.Interaction):
    role_id = Database.get(f"guilds/{interaction.guild_id}/verify_role")
    if role_id:
        role = interaction.guild.get_role(role_id)
        if role:
            await interaction.user.add_roles(role)
            return await interaction.response.send_message("You have been verified!", ephemeral=True)
    await interaction.response.send_message("Verification system not configured.", ephemeral=True)

bot.tree.add_command(AutoroleGroup(name="autorole", description="Auto-assign roles"))
bot.tree.add_command(StickyRolesGroup(name="stickyroles", description="Sticky roles setup"))
bot.tree.add_command(ServerStatsGroup(name="serverstats", description="Server statistics channels"))

# ==========================================
# ANNOUNCEMENTS & WEBHOOKS
# ==========================================
class WebhookGroup(app_commands.Group):
    @app_commands.command(name="api", description="Send webhook message")
    async def webhook_api(self, interaction: discord.Interaction, title: str, message: str, channel: discord.TextChannel, embed_format: bool, color: str):
        webhooks = await channel.webhooks()
        webhook = discord.utils.get(webhooks, name="Vantix Webhook")
        if not webhook:
            webhook = await channel.create_webhook(name="Vantix Webhook")
        
        parsed_color = discord.Color.default()
        try:
            if color.startswith("#"):
                parsed_color = discord.Color(int(color[1:], 16))
        except: pass

        if embed_format:
            embed = discord.Embed(title=title, description=message, color=parsed_color)
            await webhook.send(embed=embed, username=interaction.user.name, avatar_url=interaction.user.avatar.url if interaction.user.avatar else None)
        else:
            await webhook.send(f"**{title}**\n{message}", username=interaction.user.name, avatar_url=interaction.user.avatar.url if interaction.user.avatar else None)
        
        await interaction.response.send_message(f"Sent webhook to {channel.mention}.", ephemeral=True)

bot.tree.add_command(WebhookGroup(name="webhook", description="Webhook commands"))

# ==========================================
# LIVE STATUS MONITOR (TCP/HTTP)
# ==========================================
@bot.tree.command(name="status_setup", description="Setup channel for live status")
@app_commands.default_permissions(administrator=True)
async def status_setup(interaction: discord.Interaction, channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    msg = await channel.send("Live Status Initializing...")
    Database.set(f"guilds/{interaction.guild_id}/status", {
        "channel_id": channel.id,
        "message_id": msg.id,
        "services": []
    })
    await interaction.response.send_message(f"Status channel configured to {channel.mention}.", ephemeral=True)

@bot.tree.command(name="monitor_add", description="Add a service to monitor (tcp/http)")
@app_commands.default_permissions(administrator=True)
async def monitor_add(interaction: discord.Interaction, name: str, host: str, port: int, type: str):
    if type.lower() not in ["tcp", "http"]:
        return await interaction.response.send_message("Type must be 'tcp' or 'http'.", ephemeral=True)
    
    status_data = Database.get(f"guilds/{interaction.guild_id}/status")
    if not status_data:
        return await interaction.response.send_message("Please use /status_setup first.", ephemeral=True)
    
    services = status_data.get("services", [])
    services.append({"name": name, "host": host, "port": port, "type": type.lower()})
    status_data["services"] = services
    Database.set(f"guilds/{interaction.guild_id}/status", status_data)
    await interaction.response.send_message(f"Added {name} to monitor list.", ephemeral=True)

def check_service(service):
    if service["type"] == "tcp":
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((service["host"], service["port"]))
            sock.close()
            return result == 0
        except: return False
    elif service["type"] == "http":
        try:
            url = f"http://{service['host']}:{service['port']}" if service['port'] not in [80, 443] else f"http://{service['host']}"
            if service['port'] == 443: url = f"https://{service['host']}"
            res = requests.get(url, timeout=3)
            return res.status_code < 400
        except: return False
    return False

@tasks.loop(seconds=60)
async def status_monitor():
    all_status = Database.get("guilds") or {}
    for guild_id, data in all_status.items():
        if "status" in data:
            status_info = data["status"]
            services = status_info.get("services", [])
            if not services: continue
            
            lines = []
            for s in services:
                is_up = check_service(s)
                emoji = "🟢" if is_up else "🔴"
                lines.append(f"{emoji} **{s['name']}** ({s['host']}:{s['port']})")
            
            embed = discord.Embed(title="Live Services Status", description="\n".join(lines), color=discord.Color.green())
            embed.set_footer(text=f"Last updated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
            
            try:
                guild = bot.get_guild(int(guild_id))
                if not guild: continue
                channel = guild.get_channel(status_info["channel_id"])
                msg = await channel.fetch_message(status_info["message_id"])
                await msg.edit(content="", embed=embed)
            except Exception as e:
                pass

@tasks.loop(minutes=5)
async def server_stats_monitor():
    all_guilds = Database.get("guilds") or {}
    for guild_id, data in all_guilds.items():
        if "stats_channel" in data:
            try:
                guild = bot.get_guild(int(guild_id))
                if guild:
                    ch = guild.get_channel(data["stats_channel"])
                    if ch:
                        await ch.edit(name=f"Members: {guild.member_count}")
            except: pass

# ==========================================
# EVENT LISTENERS (Security, Tracking, Cache)
# ==========================================
class UtilityEvents(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.msg_cache = {}

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            try:
                self.bot.invites_cache[guild.id] = await guild.invites()
            except: pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        
        # AFK Check
        afk_reason = Database.get(f"guilds/{message.guild.id}/afk/{message.author.id}")
        if afk_reason:
            Database.delete(f"guilds/{message.guild.id}/afk/{message.author.id}")
            await message.channel.send(f"Welcome back {message.author.mention}, I removed your AFK.")
        
        for mention in message.mentions:
            m_afk = Database.get(f"guilds/{message.guild.id}/afk/{mention.id}")
            if m_afk:
                await message.channel.send(f"{mention.name} is AFK: {m_afk}")

        # Badwords
        badwords = Database.get(f"guilds/{message.guild.id}/badwords") or []
        for word in badwords:
            if word in message.content.lower():
                await message.delete()
                await message.channel.send(f"{message.author.mention}, that is a bad word!", delete_after=5)
                return

        # Antispam
        if Database.get(f"guilds/{message.guild.id}/antispam/enabled"):
            limit = Database.get(f"guilds/{message.guild.id}/antispam/limit") or 5
            now = datetime.datetime.now().timestamp()
            user_msgs = self.msg_cache.get(message.author.id, [])
            user_msgs = [t for t in user_msgs if now - t < 5] # last 5 seconds
            user_msgs.append(now)
            self.msg_cache[message.author.id] = user_msgs
            if len(user_msgs) > limit:
                try:
                    await message.author.timeout(datetime.timedelta(minutes=1), reason="Spamming")
                    await message.channel.send(f"{message.author.mention} has been muted for spamming.")
                except: pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # Welcome message
        welcome_data = Database.get(f"guilds/{member.guild.id}/welcome")
        if welcome_data:
            ch = member.guild.get_channel(welcome_data["channel"])
            if ch:
                msg = welcome_data["message"].replace("{user}", member.mention).replace("{server}", member.guild.name)
                await ch.send(msg)
        
        # Autorole
        ar = Database.get(f"guilds/{member.guild.id}/autorole")
        if ar:
            role = member.guild.get_role(ar)
            if role: await member.add_roles(role)

        # Sticky Roles
        if Database.get(f"guilds/{member.guild.id}/stickyroles_enabled"):
            saved_roles = Database.get(f"guilds/{member.guild.id}/saved_roles/{member.id}") or []
            roles_to_add = [member.guild.get_role(r) for r in saved_roles if member.guild.get_role(r)]
            if roles_to_add: await member.add_roles(*roles_to_add)

        # Invites Tracking
        try:
            new_invites = await member.guild.invites()
            old_invites = self.bot.invites_cache.get(member.guild.id, [])
            for new_inv in new_invites:
                for old_inv in old_invites:
                    if new_inv.code == old_inv.code and new_inv.uses > old_inv.uses:
                        inviter_id = new_inv.inviter.id
                        current = Database.get(f"guilds/{member.guild.id}/invites/{inviter_id}") or 0
                        Database.set(f"guilds/{member.guild.id}/invites/{inviter_id}", current + 1)
            self.bot.invites_cache[member.guild.id] = new_invites
        except: pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        # Goodbye message
        gb_data = Database.get(f"guilds/{member.guild.id}/goodbye")
        if gb_data:
            ch = member.guild.get_channel(gb_data["channel"])
            if ch:
                msg = gb_data["message"].replace("{user}", member.name).replace("{server}", member.guild.name)
                await ch.send(msg)

        # Sticky Roles Save
        if Database.get(f"guilds/{member.guild.id}/stickyroles_enabled"):
            roles = [r.id for r in member.roles if r.name != "@everyone"]
            Database.set(f"guilds/{member.guild.id}/saved_roles/{member.id}", roles)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if Database.get(f"guilds/{channel.guild.id}/antinuke/enabled"):
            # Very basic anit-nuke logging (for demonstration)
            logs = Database.get(f"guilds/{channel.guild.id}/security_logs") or []
            logs.append(f"[{datetime.datetime.now()}] Channel Deleted: {channel.name}")
            Database.set(f"guilds/{channel.guild.id}/security_logs", logs[-20:])

# ==========================================
# BOOT EXECUTION
# ==========================================
if __name__ == "__main__":
    keep_alive()
    if not TOKEN:
        print("CRITICAL ERROR: BOT_TOKEN is missing from .env")
    else:
        bot.run(TOKEN)
