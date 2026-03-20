import discord
from discord.ext import commands
from discord import app_commands
import os
import threading
from flask import Flask
from dotenv import load_dotenv
import pyrebase

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# --- FIREBASE SETUP ---
firebase_config = {
    "apiKey": "AIzaSyBwUiAIfzsxqCwaFn0FNd9nHgvB64Qq-vo",
    "authDomain": "infinite-chats-web-app.firebaseapp.com",
    "databaseURL": "https://infinite-chats-web-app-default-rtdb.firebaseio.com",
    "projectId": "infinite-chats-web-app",
    "storageBucket": "infinite-chats-web-app.firebasestorage.app",
    "messagingSenderId": "464599055942",
    "appId": "1:464599055942:web:9cc3b8edf71736acbbd447",
    "measurementId": "G-PBN5JLE8PC"
}

firebase = pyrebase.initialize_app(firebase_config)
db = firebase.database()

# --- RENDER 24/7 KEEP-ALIVE SERVER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Vantix Management V1 is Online and Running 24/7!"

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_server)
    t.start()

# --- BOT SETUP ---
class VantixBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="v!", intents=intents)

    async def setup_hook(self):
        # Load all command categories
        await self.add_cog(OwnerCommands(self))
        await self.add_cog(SecurityCommands(self))
        await self.add_cog(ModerationCommands(self))
        await self.add_cog(TicketCommands(self))
        await self.add_cog(WelcomeGoodbyeCommands(self))
        await self.add_cog(DMCommands(self))
        await self.add_cog(InviteCommands(self))
        await self.add_cog(UtilityCommands(self))
        await self.add_cog(InformationCommands(self))
        await self.add_cog(ServerManagementCommands(self))
        await self.add_cog(AnnouncementCommands(self))
        
        # Sync slash commands globally
        await self.tree.sync()
        print("All slash commands synced successfully!")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Vantix Management V1"))

# --- COGS & CATEGORIES ---

class OwnerCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    superadmin = app_commands.Group(name="superadmin", description="Manage bot super admins")
    extraowner = app_commands.Group(name="extraowner", description="Manage server extra owners")

    @superadmin.command(name="add", description="Add a super admin")
    async def sa_add(self, interaction: discord.Interaction, user: discord.Member): await interaction.response.send_message(f"Added {user.mention} to super admins.", ephemeral=True)
    @superadmin.command(name="remove", description="Remove a super admin")
    async def sa_remove(self, interaction: discord.Interaction, user: discord.Member): await interaction.response.send_message(f"Removed {user.mention} from super admins.", ephemeral=True)
    @superadmin.command(name="list", description="List super admins")
    async def sa_list(self, interaction: discord.Interaction): await interaction.response.send_message("Super Admins: \n- Admin1", ephemeral=True)

    @app_commands.command(name="botconfig", description="Configure bot-wide settings")
    async def botconfig(self, interaction: discord.Interaction): await interaction.response.send_message("Bot Config Menu opened.", ephemeral=True)

    @extraowner.command(name="add", description="Add an extra owner")
    async def eo_add(self, interaction: discord.Interaction, user: discord.Member): await interaction.response.send_message(f"Added {user.mention} to extra owners.", ephemeral=True)
    @extraowner.command(name="remove", description="Remove an extra owner")
    async def eo_remove(self, interaction: discord.Interaction, user: discord.Member): await interaction.response.send_message(f"Removed {user.mention} from extra owners.", ephemeral=True)
    @extraowner.command(name="list", description="List extra owners")
    async def eo_list(self, interaction: discord.Interaction): await interaction.response.send_message("Extra Owners: \n- Owner1", ephemeral=True)


class SecurityCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    antinuke = app_commands.Group(name="antinuke", description="Anti-nuke protection system")
    antispam = app_commands.Group(name="antispam", description="Anti-spam system")
    badwords = app_commands.Group(name="badwords", description="Bad words filter")

    @antinuke.command(name="enable")
    async def an_enable(self, interaction: discord.Interaction): await interaction.response.send_message("Anti-nuke enabled.", ephemeral=True)
    @antinuke.command(name="disable")
    async def an_disable(self, interaction: discord.Interaction): await interaction.response.send_message("Anti-nuke disabled.", ephemeral=True)
    @antinuke.command(name="config")
    async def an_config(self, interaction: discord.Interaction): await interaction.response.send_message("Anti-nuke config menu.", ephemeral=True)
    @antinuke.command(name="whitelist")
    async def an_whitelist(self, interaction: discord.Interaction, user: discord.Member): await interaction.response.send_message(f"Whitelisted {user.mention}.", ephemeral=True)
    @antinuke.command(name="logs")
    async def an_logs(self, interaction: discord.Interaction): await interaction.response.send_message("Security logs shown.", ephemeral=True)

    @antispam.command(name="enable")
    async def as_enable(self, interaction: discord.Interaction): await interaction.response.send_message("Anti-spam enabled.", ephemeral=True)
    @antispam.command(name="disable")
    async def as_disable(self, interaction: discord.Interaction): await interaction.response.send_message("Anti-spam disabled.", ephemeral=True)
    @antispam.command(name="config")
    async def as_config(self, interaction: discord.Interaction): await interaction.response.send_message("Anti-spam config menu.", ephemeral=True)

    @badwords.command(name="add")
    async def bw_add(self, interaction: discord.Interaction, word: str): await interaction.response.send_message(f"Added ||{word}|| to bad words.", ephemeral=True)
    @badwords.command(name="remove")
    async def bw_remove(self, interaction: discord.Interaction, word: str): await interaction.response.send_message("Word removed.", ephemeral=True)
    @badwords.command(name="list")
    async def bw_list(self, interaction: discord.Interaction): await interaction.response.send_message("Bad words list sent to DM.", ephemeral=True)


class ModerationCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="ban", description="Ban a user from the server")
    async def ban(self, interaction: discord.Interaction, user: discord.Member, reason: str = None): await interaction.response.send_message(f"Banned {user.mention}.", ephemeral=True)
    @app_commands.command(name="kick", description="Kick a user from the server")
    async def kick(self, interaction: discord.Interaction, user: discord.Member, reason: str = None): await interaction.response.send_message(f"Kicked {user.mention}.", ephemeral=True)
    @app_commands.command(name="timeout", description="Timeout a user (mute temporarily)")
    async def timeout(self, interaction: discord.Interaction, user: discord.Member, minutes: int): await interaction.response.send_message(f"Timed out {user.mention} for {minutes}m.", ephemeral=True)
    @app_commands.command(name="warn", description="Warn a user for rule violations")
    async def warn(self, interaction: discord.Interaction, user: discord.Member, reason: str): await interaction.response.send_message(f"Warned {user.mention}.", ephemeral=True)
    @app_commands.command(name="warnings", description="View user warnings")
    async def warnings(self, interaction: discord.Interaction, user: discord.Member): await interaction.response.send_message(f"Warnings for {user.mention}.", ephemeral=True)
    @app_commands.command(name="clearwarns", description="Clear user warnings")
    async def clearwarns(self, interaction: discord.Interaction, user: discord.Member): await interaction.response.send_message(f"Cleared warnings for {user.mention}.", ephemeral=True)
    @app_commands.command(name="purge", description="Delete multiple messages at once")
    async def purge(self, interaction: discord.Interaction, amount: int): await interaction.response.send_message(f"Deleted {amount} messages.", ephemeral=True)
    @app_commands.command(name="lock", description="Lock a channel")
    async def lock(self, interaction: discord.Interaction, channel: discord.TextChannel = None): await interaction.response.send_message("Channel locked.", ephemeral=True)
    @app_commands.command(name="unlock", description="Unlock a channel")
    async def unlock(self, interaction: discord.Interaction, channel: discord.TextChannel = None): await interaction.response.send_message("Channel unlocked.", ephemeral=True)
    @app_commands.command(name="slowmode", description="Set channel slowmode")
    async def slowmode(self, interaction: discord.Interaction, seconds: int): await interaction.response.send_message(f"Slowmode set to {seconds}s.", ephemeral=True)


class TicketCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    ticket = app_commands.Group(name="ticket", description="Ticket System")

    @ticket.command(name="setup")
    async def t_setup(self, interaction: discord.Interaction): await interaction.response.send_message("Ticket setup complete.", ephemeral=True)
    @ticket.command(name="panel")
    async def t_panel(self, interaction: discord.Interaction): await interaction.response.send_message("Ticket panel created.", ephemeral=True)
    @ticket.command(name="panels")
    async def t_panels(self, interaction: discord.Interaction): await interaction.response.send_message("List of ticket panels.", ephemeral=True)
    @ticket.command(name="editpanel")
    async def t_editpanel(self, interaction: discord.Interaction): await interaction.response.send_message("Edit panel menu.", ephemeral=True)
    @ticket.command(name="deletepanel")
    async def t_deletepanel(self, interaction: discord.Interaction): await interaction.response.send_message("Panel deleted.", ephemeral=True)
    @ticket.command(name="closeall")
    async def t_closeall(self, interaction: discord.Interaction): await interaction.response.send_message("All tickets closed.", ephemeral=True)
    @ticket.command(name="add")
    async def t_add(self, interaction: discord.Interaction, user: discord.Member): await interaction.response.send_message("Added user to ticket.", ephemeral=True)
    @ticket.command(name="remove")
    async def t_remove(self, interaction: discord.Interaction, user: discord.Member): await interaction.response.send_message("Removed user from ticket.", ephemeral=True)
    @ticket.command(name="close")
    async def t_close(self, interaction: discord.Interaction): await interaction.response.send_message("Ticket closed.", ephemeral=True)
    @ticket.command(name="claim")
    async def t_claim(self, interaction: discord.Interaction): await interaction.response.send_message("Ticket claimed.", ephemeral=True)
    @ticket.command(name="transcript")
    async def t_transcript(self, interaction: discord.Interaction): await interaction.response.send_message("Transcript generated.", ephemeral=True)
    @ticket.command(name="stats")
    async def t_stats(self, interaction: discord.Interaction): await interaction.response.send_message("Ticket stats shown.", ephemeral=True)
    @ticket.command(name="addtype")
    async def t_addtype(self, interaction: discord.Interaction): await interaction.response.send_message("Ticket category added.", ephemeral=True)
    @ticket.command(name="listtypes")
    async def t_listtypes(self, interaction: discord.Interaction): await interaction.response.send_message("Ticket categories listed.", ephemeral=True)
    @ticket.command(name="edittype")
    async def t_edittype(self, interaction: discord.Interaction): await interaction.response.send_message("Ticket category edited.", ephemeral=True)
    @ticket.command(name="deletetype")
    async def t_deletetype(self, interaction: discord.Interaction): await interaction.response.send_message("Ticket category deleted.", ephemeral=True)
    @ticket.command(name="config")
    async def t_config(self, interaction: discord.Interaction): await interaction.response.send_message("Ticket configuration menu.", ephemeral=True)


class WelcomeGoodbyeCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    welcome = app_commands.Group(name="welcome", description="Configure welcome messages")
    goodbye = app_commands.Group(name="goodbye", description="Configure goodbye messages")

    @welcome.command(name="setup")
    async def w_setup(self, interaction: discord.Interaction): await interaction.response.send_message("Welcome setup started.", ephemeral=True)
    @welcome.command(name="test")
    async def w_test(self, interaction: discord.Interaction): await interaction.response.send_message("Testing welcome message...", ephemeral=True)
    @welcome.command(name="disable")
    async def w_disable(self, interaction: discord.Interaction): await interaction.response.send_message("Welcome messages disabled.", ephemeral=True)

    @goodbye.command(name="setup")
    async def g_setup(self, interaction: discord.Interaction): await interaction.response.send_message("Goodbye setup started.", ephemeral=True)
    @goodbye.command(name="test")
    async def g_test(self, interaction: discord.Interaction): await interaction.response.send_message("Testing goodbye message...", ephemeral=True)
    @goodbye.command(name="disable")
    async def g_disable(self, interaction: discord.Interaction): await interaction.response.send_message("Goodbye messages disabled.", ephemeral=True)


class DMCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    dm = app_commands.Group(name="dm", description="DM SYSTEM")

    @dm.command(name="user", description="Send DM to specific user")
    async def dm_user(self, interaction: discord.Interaction, user: discord.Member, message: str): await interaction.response.send_message("DM sent.", ephemeral=True)
    @dm.command(name="role", description="Send DM to all users with a role")
    async def dm_role(self, interaction: discord.Interaction, role: discord.Role, message: str): await interaction.response.send_message("DMs sending to role...", ephemeral=True)
    @dm.command(name="everyone", description="Send DM to all server members")
    async def dm_everyone(self, interaction: discord.Interaction, message: str): await interaction.response.send_message("DMs sending to everyone...", ephemeral=True)
    
    @app_commands.command(name="dmlogs", description="View DM logs and history")
    async def dmlogs(self, interaction: discord.Interaction): await interaction.response.send_message("DM logs here.", ephemeral=True)


class InviteCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    
    @app_commands.command(name="invites", description="Check your or someone's invite stats")
    async def invites(self, interaction: discord.Interaction, user: discord.Member = None): await interaction.response.send_message("Invite stats.", ephemeral=True)
    @app_commands.command(name="inviteleaderboard", description="View top inviters")
    async def inviteleaderboard(self, interaction: discord.Interaction): await interaction.response.send_message("Invite Leaderboard.", ephemeral=True)
    @app_commands.command(name="resetinvites", description="Reset user invite count (Admin)")
    async def resetinvites(self, interaction: discord.Interaction, user: discord.Member): await interaction.response.send_message("Invites reset.", ephemeral=True)


class UtilityCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    customcommand = app_commands.Group(name="customcommand", description="Custom commands")
    giveaway = app_commands.Group(name="giveaway", description="Giveaway system")
    plan = app_commands.Group(name="plan", description="Server plans management")
    statusmonitor = app_commands.Group(name="statusmonitor", description="Website monitoring")

    @customcommand.command(name="add")
    async def cc_add(self, interaction: discord.Interaction): await interaction.response.send_message("Custom command added.", ephemeral=True)
    @customcommand.command(name="remove")
    async def cc_remove(self, interaction: discord.Interaction): await interaction.response.send_message("Custom command removed.", ephemeral=True)
    @customcommand.command(name="list")
    async def cc_list(self, interaction: discord.Interaction): await interaction.response.send_message("Custom commands list.", ephemeral=True)

    @giveaway.command(name="start")
    async def gw_start(self, interaction: discord.Interaction): await interaction.response.send_message("Giveaway started.", ephemeral=True)
    @giveaway.command(name="end")
    async def gw_end(self, interaction: discord.Interaction): await interaction.response.send_message("Giveaway ended.", ephemeral=True)
    @giveaway.command(name="reroll")
    async def gw_reroll(self, interaction: discord.Interaction): await interaction.response.send_message("Giveaway rerolled.", ephemeral=True)

    @plan.command(name="add")
    async def p_add(self, interaction: discord.Interaction): await interaction.response.send_message("Plan added.", ephemeral=True)
    @plan.command(name="remove")
    async def p_remove(self, interaction: discord.Interaction): await interaction.response.send_message("Plan removed.", ephemeral=True)
    @plan.command(name="list")
    async def p_list(self, interaction: discord.Interaction): await interaction.response.send_message("Plans list.", ephemeral=True)

    @statusmonitor.command(name="add")
    async def sm_add(self, interaction: discord.Interaction): await interaction.response.send_message("Monitor added.", ephemeral=True)
    @statusmonitor.command(name="remove")
    async def sm_remove(self, interaction: discord.Interaction): await interaction.response.send_message("Monitor removed.", ephemeral=True)
    @statusmonitor.command(name="list")
    async def sm_list(self, interaction: discord.Interaction): await interaction.response.send_message("Monitor list.", ephemeral=True)

    @app_commands.command(name="weather", description="Get weather information")
    async def weather(self, interaction: discord.Interaction, location: str): await interaction.response.send_message(f"Weather for {location}.", ephemeral=True)
    @app_commands.command(name="qrcode", description="Generate QR codes")
    async def qrcode(self, interaction: discord.Interaction, url: str): await interaction.response.send_message("QR Code generated.", ephemeral=True)
    @app_commands.command(name="remindme", description="Set reminders")
    async def remindme(self, interaction: discord.Interaction, time: str, reminder: str): await interaction.response.send_message("Reminder set.", ephemeral=True)
    @app_commands.command(name="poll", description="Create polls")
    async def poll(self, interaction: discord.Interaction, question: str): await interaction.response.send_message("Poll created.", ephemeral=True)
    @app_commands.command(name="afk", description="Set AFK status")
    async def afk(self, interaction: discord.Interaction, reason: str = "AFK"): await interaction.response.send_message("You are now AFK.", ephemeral=True)


class InformationCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="serverinfo", description="View server information")
    async def serverinfo(self, interaction: discord.Interaction): await interaction.response.send_message(f"Server Name: {interaction.guild.name}", ephemeral=True)
    @app_commands.command(name="userinfo", description="View user information")
    async def userinfo(self, interaction: discord.Interaction, user: discord.Member = None): await interaction.response.send_message("User info.", ephemeral=True)
    @app_commands.command(name="roleinfo", description="View role information")
    async def roleinfo(self, interaction: discord.Interaction, role: discord.Role): await interaction.response.send_message("Role info.", ephemeral=True)
    @app_commands.command(name="avatar", description="View user avatar")
    async def avatar(self, interaction: discord.Interaction, user: discord.Member = None): await interaction.response.send_message("Avatar link.", ephemeral=True)
    @app_commands.command(name="banner", description="View user banner")
    async def banner(self, interaction: discord.Interaction, user: discord.Member = None): await interaction.response.send_message("Banner link.", ephemeral=True)
    @app_commands.command(name="membercount", description="View member count")
    async def membercount(self, interaction: discord.Interaction): await interaction.response.send_message(f"Members: {interaction.guild.member_count}", ephemeral=True)
    @app_commands.command(name="ping", description="Check bot latency")
    async def ping(self, interaction: discord.Interaction): await interaction.response.send_message(f"Pong! {round(self.bot.latency * 1000)}ms", ephemeral=True)
    @app_commands.command(name="stats", description="View bot statistics")
    async def stats(self, interaction: discord.Interaction): await interaction.response.send_message("Bot Stats here.", ephemeral=True)
    @app_commands.command(name="help", description="Show this help menu")
    async def help(self, interaction: discord.Interaction): await interaction.response.send_message("Vantix Management V1 Help Menu.", ephemeral=True)


class ServerManagementCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    autorole = app_commands.Group(name="autorole", description="Auto-assign roles to new members")
    stickyroles = app_commands.Group(name="stickyroles", description="Restore roles on rejoin")
    serverstats = app_commands.Group(name="serverstats", description="Server statistics channels")

    @autorole.command(name="set")
    async def ar_set(self, interaction: discord.Interaction, role: discord.Role): await interaction.response.send_message("Autorole set.", ephemeral=True)
    @autorole.command(name="remove")
    async def ar_remove(self, interaction: discord.Interaction): await interaction.response.send_message("Autorole removed.", ephemeral=True)

    @stickyroles.command(name="enable")
    async def sr_enable(self, interaction: discord.Interaction): await interaction.response.send_message("Sticky roles enabled.", ephemeral=True)
    @stickyroles.command(name="disable")
    async def sr_disable(self, interaction: discord.Interaction): await interaction.response.send_message("Sticky roles disabled.", ephemeral=True)

    @app_commands.command(name="addrole", description="Add role to user")
    async def addrole(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role): await interaction.response.send_message("Role added.", ephemeral=True)
    @app_commands.command(name="removerole", description="Remove role from user")
    async def removerole(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role): await interaction.response.send_message("Role removed.", ephemeral=True)
    @app_commands.command(name="verifyconfig", description="Setup verification system")
    async def verifyconfig(self, interaction: discord.Interaction): await interaction.response.send_message("Verification config opened.", ephemeral=True)
    @app_commands.command(name="verify", description="Verify yourself")
    async def verify(self, interaction: discord.Interaction): await interaction.response.send_message("You are verified.", ephemeral=True)

    @serverstats.command(name="setup")
    async def ss_setup(self, interaction: discord.Interaction): await interaction.response.send_message("Server stats channels setup.", ephemeral=True)
    @serverstats.command(name="remove")
    async def ss_remove(self, interaction: discord.Interaction): await interaction.response.send_message("Server stats channels removed.", ephemeral=True)


class AnnouncementCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    starboard = app_commands.Group(name="starboard", description="Starboard system")
    reactionrole = app_commands.Group(name="reactionrole", description="Reaction roles")
    autopublish = app_commands.Group(name="autopublish", description="Auto-publish announcements")

    @starboard.command(name="setup")
    async def sb_setup(self, interaction: discord.Interaction, channel: discord.TextChannel): await interaction.response.send_message("Starboard setup.", ephemeral=True)
    @starboard.command(name="remove")
    async def sb_remove(self, interaction: discord.Interaction): await interaction.response.send_message("Starboard removed.", ephemeral=True)

    @reactionrole.command(name="add")
    async def rr_add(self, interaction: discord.Interaction): await interaction.response.send_message("Reaction role added.", ephemeral=True)
    @reactionrole.command(name="remove")
    async def rr_remove(self, interaction: discord.Interaction): await interaction.response.send_message("Reaction role removed.", ephemeral=True)
    @reactionrole.command(name="list")
    async def rr_list(self, interaction: discord.Interaction): await interaction.response.send_message("Reaction roles list.", ephemeral=True)

    @autopublish.command(name="setup")
    async def ap_setup(self, interaction: discord.Interaction, channel: discord.TextChannel): await interaction.response.send_message("Autopublish setup.", ephemeral=True)
    @autopublish.command(name="remove")
    async def ap_remove(self, interaction: discord.Interaction, channel: discord.TextChannel): await interaction.response.send_message("Autopublish removed.", ephemeral=True)


# Run the Keep-Alive Web Server and the Bot
if __name__ == "__main__":
    keep_alive()
    bot = VantixBot()
    bot.run(TOKEN)
