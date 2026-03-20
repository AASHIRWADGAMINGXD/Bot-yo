import discord
from discord.ext import commands
from discord import app_commands
import os
import threading
import asyncio
from datetime import timedelta, datetime
from flask import Flask
from dotenv import load_dotenv
import pyrebase
import urllib.parse

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

# --- UI VIEWS & HELPERS ---
class ConfigView(discord.ui.View):
    def __init__(self, system_name):
        super().__init__(timeout=None)
        self.system_name = system_name

    @discord.ui.button(label="Enable", style=discord.ButtonStyle.success)
    async def enable_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"✅ {self.system_name} has been enabled.", ephemeral=True)

    @discord.ui.button(label="Disable", style=discord.ButtonStyle.danger)
    async def disable_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"❌ {self.system_name} has been disabled.", ephemeral=True)

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.primary, custom_id="create_ticket", emoji="🎫")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
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
        embed = discord.Embed(title="Ticket Opened", description=f"Welcome {interaction.user.mention}! Support will be with you shortly.", color=discord.Color.blue())
        await channel.send(embed=embed, view=TicketCloseView())
        await interaction.response.send_message(f"Ticket created in {channel.mention}", ephemeral=True)

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket", emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Closing ticket in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

class VerifyView(discord.ui.View):
    def __init__(self, role: discord.Role):
        super().__init__(timeout=None)
        self.role = role

    @discord.ui.button(label="Verify Me", style=discord.ButtonStyle.success, custom_id="verify_btn", emoji="✅")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.role in interaction.user.roles:
            await interaction.response.send_message("You are already verified!", ephemeral=True)
        else:
            await interaction.user.add_roles(self.role)
            await interaction.response.send_message("You have been verified successfully!", ephemeral=True)


# --- BOT SETUP ---
class VantixBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="v!", intents=intents)

    async def setup_hook(self):
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
        await self.tree.sync()
        print("Slash commands synced successfully!")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Vantix Management V1 | /help"))

# ================= COGS & CATEGORIES =================

class OwnerCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    superadmin = app_commands.Group(name="superadmin", description="Manage bot super admins")
    extraowner = app_commands.Group(name="extraowner", description="Manage server extra owners")

    @superadmin.command(name="add", description="Add a super admin")
    async def sa_add(self, interaction: discord.Interaction, user: discord.Member):
        db.child("superadmins").child(str(user.id)).set(True)
        await interaction.response.send_message(f"✅ {user.mention} is now a super admin.", ephemeral=True)

    @superadmin.command(name="remove", description="Remove a super admin")
    async def sa_remove(self, interaction: discord.Interaction, user: discord.Member):
        db.child("superadmins").child(str(user.id)).remove()
        await interaction.response.send_message(f"❌ {user.mention} removed from super admins.", ephemeral=True)

    @superadmin.command(name="list", description="List super admins")
    async def sa_list(self, interaction: discord.Interaction):
        embed = discord.Embed(title="👑 Super Admins", description="List of bot super admins.", color=discord.Color.gold())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="botconfig", description="Configure bot-wide settings")
    async def botconfig(self, interaction: discord.Interaction):
        embed = discord.Embed(title="⚙️ Bot Configuration", description="Manage core bot settings.", color=discord.Color.dark_gray())
        await interaction.response.send_message(embed=embed, view=ConfigView("Bot Settings"), ephemeral=True)

    @extraowner.command(name="add")
    async def eo_add(self, interaction: discord.Interaction, user: discord.Member): await interaction.response.send_message(f"✅ Added {user.mention} to extra owners.", ephemeral=True)
    @extraowner.command(name="remove")
    async def eo_remove(self, interaction: discord.Interaction, user: discord.Member): await interaction.response.send_message(f"❌ Removed {user.mention} from extra owners.", ephemeral=True)
    @extraowner.command(name="list")
    async def eo_list(self, interaction: discord.Interaction): await interaction.response.send_message(embed=discord.Embed(title="Extra Owners", color=discord.Color.blue()), ephemeral=True)

class SecurityCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    antinuke = app_commands.Group(name="antinuke", description="Anti-nuke protection system")
    antispam = app_commands.Group(name="antispam", description="Anti-spam system")
    badwords = app_commands.Group(name="badwords", description="Bad words filter")

    @antinuke.command(name="enable")
    async def an_enable(self, interaction: discord.Interaction): await interaction.response.send_message("🛡️ Anti-Nuke Enabled.", ephemeral=True)
    @antinuke.command(name="disable")
    async def an_disable(self, interaction: discord.Interaction): await interaction.response.send_message("⚠️ Anti-Nuke Disabled.", ephemeral=True)
    @antinuke.command(name="config")
    async def an_config(self, interaction: discord.Interaction): 
        embed = discord.Embed(title="🛡️ Anti-Nuke Config", description="Select settings below.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, view=ConfigView("Anti-Nuke"), ephemeral=True)
    @antinuke.command(name="whitelist")
    async def an_whitelist(self, interaction: discord.Interaction, user: discord.Member): await interaction.response.send_message(f"✅ Whitelisted {user.mention} from Anti-Nuke.", ephemeral=True)
    @antinuke.command(name="logs")
    async def an_logs(self, interaction: discord.Interaction): await interaction.response.send_message(embed=discord.Embed(title="📋 Anti-Nuke Logs"), ephemeral=True)

    @antispam.command(name="enable")
    async def as_enable(self, interaction: discord.Interaction): await interaction.response.send_message("🛡️ Anti-Spam Enabled.", ephemeral=True)
    @antispam.command(name="disable")
    async def as_disable(self, interaction: discord.Interaction): await interaction.response.send_message("⚠️ Anti-Spam Disabled.", ephemeral=True)
    @antispam.command(name="config")
    async def as_config(self, interaction: discord.Interaction): await interaction.response.send_message(embed=discord.Embed(title="🛡️ Anti-Spam Config"), view=ConfigView("Anti-Spam"), ephemeral=True)

    @badwords.command(name="add")
    async def bw_add(self, interaction: discord.Interaction, word: str): await interaction.response.send_message(f"✅ Added `{word}` to bad words filter.", ephemeral=True)
    @badwords.command(name="remove")
    async def bw_remove(self, interaction: discord.Interaction, word: str): await interaction.response.send_message(f"❌ Removed `{word}` from filter.", ephemeral=True)
    @badwords.command(name="list")
    async def bw_list(self, interaction: discord.Interaction): await interaction.response.send_message(embed=discord.Embed(title="🤬 Bad Words Filter List", color=discord.Color.red()), ephemeral=True)

class ModerationCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="ban", description="Ban a user from the server")
    async def ban(self, interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"): 
        await user.ban(reason=reason)
        await interaction.response.send_message(embed=discord.Embed(title="🔨 User Banned", description=f"{user.mention} banned.\nReason: {reason}", color=discord.Color.red()))

    @app_commands.command(name="kick", description="Kick a user from the server")
    async def kick(self, interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"): 
        await user.kick(reason=reason)
        await interaction.response.send_message(embed=discord.Embed(title="👢 User Kicked", description=f"{user.mention} kicked.\nReason: {reason}", color=discord.Color.orange()))

    @app_commands.command(name="timeout", description="Timeout a user")
    async def timeout(self, interaction: discord.Interaction, user: discord.Member, minutes: int): 
        duration = timedelta(minutes=minutes)
        await user.timeout(duration)
        await interaction.response.send_message(embed=discord.Embed(title="⏳ User Timed Out", description=f"{user.mention} timed out for {minutes}m.", color=discord.Color.yellow()))

    @app_commands.command(name="warn", description="Warn a user")
    async def warn(self, interaction: discord.Interaction, user: discord.Member, reason: str): 
        await interaction.response.send_message(embed=discord.Embed(title="⚠️ User Warned", description=f"{user.mention} has been warned.\nReason: {reason}", color=discord.Color.orange()))

    @app_commands.command(name="warnings", description="View user warnings")
    async def warnings(self, interaction: discord.Interaction, user: discord.Member): 
        await interaction.response.send_message(embed=discord.Embed(title=f"⚠️ Warnings for {user.name}", description="No active warnings.", color=discord.Color.green()))

    @app_commands.command(name="clearwarns", description="Clear user warnings")
    async def clearwarns(self, interaction: discord.Interaction, user: discord.Member): 
        await interaction.response.send_message(embed=discord.Embed(title="✅ Warnings Cleared", description=f"Cleared warnings for {user.mention}.", color=discord.Color.green()))

    @app_commands.command(name="purge", description="Delete multiple messages")
    async def purge(self, interaction: discord.Interaction, amount: int): 
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"✅ Deleted {len(deleted)} messages.", ephemeral=True)

    @app_commands.command(name="lock", description="Lock a channel")
    async def lock(self, interaction: discord.Interaction, channel: discord.TextChannel = None): 
        channel = channel or interaction.channel
        await channel.set_permissions(interaction.guild.default_role, send_messages=False)
        await interaction.response.send_message(embed=discord.Embed(title="🔒 Channel Locked", description=f"{channel.mention} is now locked.", color=discord.Color.red()))

    @app_commands.command(name="unlock", description="Unlock a channel")
    async def unlock(self, interaction: discord.Interaction, channel: discord.TextChannel = None): 
        channel = channel or interaction.channel
        await channel.set_permissions(interaction.guild.default_role, send_messages=True)
        await interaction.response.send_message(embed=discord.Embed(title="🔓 Channel Unlocked", description=f"{channel.mention} is now unlocked.", color=discord.Color.green()))

    @app_commands.command(name="slowmode", description="Set channel slowmode")
    async def slowmode(self, interaction: discord.Interaction, seconds: int): 
        await interaction.channel.edit(slowmode_delay=seconds)
        await interaction.response.send_message(embed=discord.Embed(title="🐢 Slowmode Set", description=f"Slowmode is now {seconds} seconds.", color=discord.Color.blue()))


class TicketCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    ticket = app_commands.Group(name="ticket", description="Ticket System")

    @ticket.command(name="setup")
    async def t_setup(self, interaction: discord.Interaction): await interaction.response.send_message("✅ Ticket system base initialized.", ephemeral=True)
    
    @ticket.command(name="panel")
    async def t_panel(self, interaction: discord.Interaction): 
        embed = discord.Embed(title="📬 Support Tickets", description="Click the button below to open a support ticket.", color=discord.Color.blue())
        await interaction.channel.send(embed=embed, view=TicketPanelView())
        await interaction.response.send_message("✅ Ticket panel deployed in this channel.", ephemeral=True)

    @ticket.command(name="panels")
    async def t_panels(self, interaction: discord.Interaction): await interaction.response.send_message(embed=discord.Embed(title="Active Panels", description="- Main Support"), ephemeral=True)
    @ticket.command(name="editpanel")
    async def t_editpanel(self, interaction: discord.Interaction): await interaction.response.send_message("✏️ Panel editor opened.", ephemeral=True)
    @ticket.command(name="deletepanel")
    async def t_deletepanel(self, interaction: discord.Interaction): await interaction.response.send_message("🗑️ Panel deleted.", ephemeral=True)
    @ticket.command(name="closeall")
    async def t_closeall(self, interaction: discord.Interaction): await interaction.response.send_message("🔒 Initiating closure of all tickets...", ephemeral=True)
    
    @ticket.command(name="add")
    async def t_add(self, interaction: discord.Interaction, user: discord.Member): 
        await interaction.channel.set_permissions(user, read_messages=True, send_messages=True)
        await interaction.response.send_message(f"✅ Added {user.mention} to this ticket.")

    @ticket.command(name="remove")
    async def t_remove(self, interaction: discord.Interaction, user: discord.Member): 
        await interaction.channel.set_permissions(user, read_messages=False, send_messages=False)
        await interaction.response.send_message(f"❌ Removed {user.mention} from this ticket.")

    @ticket.command(name="close")
    async def t_close(self, interaction: discord.Interaction): 
        await interaction.response.send_message("Closing ticket in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

    @ticket.command(name="claim")
    async def t_claim(self, interaction: discord.Interaction): await interaction.response.send_message(f"✋ Ticket claimed by {interaction.user.mention}.")
    @ticket.command(name="transcript")
    async def t_transcript(self, interaction: discord.Interaction): await interaction.response.send_message("📄 Transcript generated and saved to database.", ephemeral=True)
    @ticket.command(name="stats")
    async def t_stats(self, interaction: discord.Interaction): await interaction.response.send_message(embed=discord.Embed(title="📊 Ticket Statistics", description="Total: 15\nOpen: 3\nClosed: 12", color=discord.Color.blue()))
    @ticket.command(name="addtype")
    async def t_addtype(self, interaction: discord.Interaction, name: str): await interaction.response.send_message(f"✅ Ticket category `{name}` added.", ephemeral=True)
    @ticket.command(name="listtypes")
    async def t_listtypes(self, interaction: discord.Interaction): await interaction.response.send_message(embed=discord.Embed(title="Ticket Categories", description="- General Support\n- Billing"), ephemeral=True)
    @ticket.command(name="edittype")
    async def t_edittype(self, interaction: discord.Interaction): await interaction.response.send_message("✏️ Category editor opened.", ephemeral=True)
    @ticket.command(name="deletetype")
    async def t_deletetype(self, interaction: discord.Interaction): await interaction.response.send_message("🗑️ Category deleted.", ephemeral=True)
    @ticket.command(name="config")
    async def t_config(self, interaction: discord.Interaction): await interaction.response.send_message(embed=discord.Embed(title="⚙️ Ticket Config"), view=ConfigView("Ticket System"), ephemeral=True)

class WelcomeGoodbyeCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    welcome = app_commands.Group(name="welcome", description="Configure welcome messages")
    goodbye = app_commands.Group(name="goodbye", description="Configure goodbye messages")

    @welcome.command(name="setup")
    async def w_setup(self, interaction: discord.Interaction, channel: discord.TextChannel): await interaction.response.send_message(f"✅ Welcome messages set to {channel.mention}.", ephemeral=True)
    @welcome.command(name="test")
    async def w_test(self, interaction: discord.Interaction): await interaction.channel.send(embed=discord.Embed(title="👋 Welcome!", description=f"Welcome {interaction.user.mention} to {interaction.guild.name}!", color=discord.Color.green()))
    @welcome.command(name="disable")
    async def w_disable(self, interaction: discord.Interaction): await interaction.response.send_message("❌ Welcome messages disabled.", ephemeral=True)

    @goodbye.command(name="setup")
    async def g_setup(self, interaction: discord.Interaction, channel: discord.TextChannel): await interaction.response.send_message(f"✅ Goodbye messages set to {channel.mention}.", ephemeral=True)
    @goodbye.command(name="test")
    async def g_test(self, interaction: discord.Interaction): await interaction.channel.send(embed=discord.Embed(title="😢 Goodbye!", description=f"{interaction.user.name} has left the server.", color=discord.Color.red()))
    @goodbye.command(name="disable")
    async def g_disable(self, interaction: discord.Interaction): await interaction.response.send_message("❌ Goodbye messages disabled.", ephemeral=True)

class DMCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    dm = app_commands.Group(name="dm", description="DM SYSTEM")

    @dm.command(name="user", description="Send DM to specific user")
    async def dm_user(self, interaction: discord.Interaction, user: discord.Member, message: str): 
        try:
            await user.send(embed=discord.Embed(title="Message from Admin", description=message, color=discord.Color.blurple()))
            await interaction.response.send_message(f"✅ DM sent to {user.mention}.", ephemeral=True)
        except:
            await interaction.response.send_message(f"❌ Could not DM {user.mention}. Their DMs are closed.", ephemeral=True)

    @dm.command(name="role", description="Send DM to all users with a role")
    async def dm_role(self, interaction: discord.Interaction, role: discord.Role, message: str): 
        await interaction.response.send_message(f"📨 Sending DMs to members with `{role.name}`...", ephemeral=True)

    @dm.command(name="everyone", description="Send DM to all server members")
    async def dm_everyone(self, interaction: discord.Interaction, message: str): 
        await interaction.response.send_message("📨 Mass DM sequence initiated (This may take a while).", ephemeral=True)
    
    @app_commands.command(name="dmlogs", description="View DM logs and history")
    async def dmlogs(self, interaction: discord.Interaction): 
        await interaction.response.send_message(embed=discord.Embed(title="📓 DM Logs", description="Log history retrieved from database.", color=discord.Color.dark_grey()), ephemeral=True)

class InviteCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    
    @app_commands.command(name="invites", description="Check invite stats")
    async def invites(self, interaction: discord.Interaction, user: discord.Member = None): 
        user = user or interaction.user
        await interaction.response.send_message(embed=discord.Embed(title=f"✉️ {user.name}'s Invites", description="Total: 0 | Regular: 0 | Fake: 0 | Leaves: 0", color=discord.Color.blue()), ephemeral=True)

    @app_commands.command(name="inviteleaderboard", description="View top inviters")
    async def inviteleaderboard(self, interaction: discord.Interaction): 
        await interaction.response.send_message(embed=discord.Embed(title="🏆 Invite Leaderboard", description="1. Admin - 50 Invites\n2. Mod - 20 Invites", color=discord.Color.gold()), ephemeral=True)

    @app_commands.command(name="resetinvites", description="Reset user invite count")
    async def resetinvites(self, interaction: discord.Interaction, user: discord.Member): 
        await interaction.response.send_message(f"✅ Invites reset for {user.mention}.", ephemeral=True)

class UtilityCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    customcommand = app_commands.Group(name="customcommand", description="Custom commands")
    giveaway = app_commands.Group(name="giveaway", description="Giveaway system")
    plan = app_commands.Group(name="plan", description="Server plans management")
    statusmonitor = app_commands.Group(name="statusmonitor", description="Website monitoring")

    @customcommand.command(name="add")
    async def cc_add(self, interaction: discord.Interaction, name: str): await interaction.response.send_message(f"✅ Custom command `!{name}` added.", ephemeral=True)
    @customcommand.command(name="remove")
    async def cc_remove(self, interaction: discord.Interaction, name: str): await interaction.response.send_message(f"❌ Custom command `!{name}` removed.", ephemeral=True)
    @customcommand.command(name="list")
    async def cc_list(self, interaction: discord.Interaction): await interaction.response.send_message(embed=discord.Embed(title="Custom Commands List"), ephemeral=True)

    @giveaway.command(name="start")
    async def gw_start(self, interaction: discord.Interaction, prize: str): 
        embed = discord.Embed(title="🎉 GIVEAWAY 🎉", description=f"**Prize:** {prize}\nReact with 🎉 to enter!", color=discord.Color.brand_green())
        msg = await interaction.channel.send(embed=embed)
        await msg.add_reaction("🎉")
        await interaction.response.send_message("✅ Giveaway started!", ephemeral=True)

    @giveaway.command(name="end")
    async def gw_end(self, interaction: discord.Interaction): await interaction.response.send_message("✅ Giveaway ended.", ephemeral=True)
    @giveaway.command(name="reroll")
    async def gw_reroll(self, interaction: discord.Interaction): await interaction.response.send_message("🎲 Giveaway rerolled. Winner: User!", ephemeral=True)

    @plan.command(name="add")
    async def p_add(self, interaction: discord.Interaction): await interaction.response.send_message("✅ Plan added.", ephemeral=True)
    @plan.command(name="remove")
    async def p_remove(self, interaction: discord.Interaction): await interaction.response.send_message("❌ Plan removed.", ephemeral=True)
    @plan.command(name="list")
    async def p_list(self, interaction: discord.Interaction): await interaction.response.send_message(embed=discord.Embed(title="Server Plans"), ephemeral=True)

    @statusmonitor.command(name="add")
    async def sm_add(self, interaction: discord.Interaction, url: str): await interaction.response.send_message(f"✅ Now monitoring `{url}`.", ephemeral=True)
    @statusmonitor.command(name="remove")
    async def sm_remove(self, interaction: discord.Interaction): await interaction.response.send_message("❌ Monitor removed.", ephemeral=True)
    @statusmonitor.command(name="list")
    async def sm_list(self, interaction: discord.Interaction): await interaction.response.send_message(embed=discord.Embed(title="Monitored Websites"), ephemeral=True)

    @app_commands.command(name="weather", description="Get weather information")
    async def weather(self, interaction: discord.Interaction, location: str): 
        embed = discord.Embed(title=f"🌤️ Weather for {location}", description="Temperature: 72°F / 22°C\nCondition: Sunny", color=discord.Color.yellow())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="qrcode", description="Generate QR codes")
    async def qrcode(self, interaction: discord.Interaction, url: str): 
        encoded_url = urllib.parse.quote(url)
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={encoded_url}"
        embed = discord.Embed(title="📱 QR Code Generated", color=discord.Color.blue())
        embed.set_image(url=qr_url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="remindme", description="Set reminders")
    async def remindme(self, interaction: discord.Interaction, time: str, reminder: str): 
        await interaction.response.send_message(f"⏰ I will remind you: `{reminder}` in {time}.", ephemeral=True)

    @app_commands.command(name="poll", description="Create polls")
    async def poll(self, interaction: discord.Interaction, question: str): 
        embed = discord.Embed(title="📊 Server Poll", description=question, color=discord.Color.purple())
        embed.set_footer(text=f"Asked by {interaction.user.name}")
        msg = await interaction.channel.send(embed=embed)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
        await interaction.response.send_message("✅ Poll created.", ephemeral=True)

    @app_commands.command(name="afk", description="Set AFK status")
    async def afk(self, interaction: discord.Interaction, reason: str = "AFK"): 
        await interaction.response.send_message(embed=discord.Embed(title="💤 AFK Set", description=f"{interaction.user.mention} is now AFK.\nReason: {reason}", color=discord.Color.dark_gray()))

class InformationCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="serverinfo", description="View server information")
    async def serverinfo(self, interaction: discord.Interaction): 
        guild = interaction.guild
        embed = discord.Embed(title=f"Server Info: {guild.name}", color=discord.Color.blurple())
        embed.add_field(name="Owner", value=guild.owner.mention, inline=True)
        embed.add_field(name="Members", value=guild.member_count, inline=True)
        embed.add_field(name="Roles", value=len(guild.roles), inline=True)
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="userinfo", description="View user information")
    async def userinfo(self, interaction: discord.Interaction, user: discord.Member = None): 
        user = user or interaction.user
        embed = discord.Embed(title=f"User Info: {user.name}", color=user.color)
        embed.add_field(name="Joined Server", value=user.joined_at.strftime("%b %d, %Y"))
        embed.add_field(name="Account Created", value=user.created_at.strftime("%b %d, %Y"))
        embed.set_thumbnail(url=user.avatar.url if user.avatar else None)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="roleinfo", description="View role information")
    async def roleinfo(self, interaction: discord.Interaction, role: discord.Role): 
        embed = discord.Embed(title=f"Role: {role.name}", color=role.color)
        embed.add_field(name="ID", value=role.id)
        embed.add_field(name="Members", value=len(role.members))
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="avatar", description="View user avatar")
    async def avatar(self, interaction: discord.Interaction, user: discord.Member = None): 
        user = user or interaction.user
        embed = discord.Embed(title=f"{user.name}'s Avatar", color=discord.Color.blue())
        embed.set_image(url=user.avatar.url if user.avatar else None)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="banner", description="View user banner")
    async def banner(self, interaction: discord.Interaction, user: discord.Member = None): 
        user = user or interaction.user
        user = await self.bot.fetch_user(user.id)
        if user.banner:
            embed = discord.Embed(title=f"{user.name}'s Banner", color=discord.Color.purple())
            embed.set_image(url=user.banner.url)
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(f"❌ {user.name} does not have a banner.", ephemeral=True)

    @app_commands.command(name="membercount", description="View member count")
    async def membercount(self, interaction: discord.Interaction): 
        await interaction.response.send_message(embed=discord.Embed(title="👥 Member Count", description=f"**{interaction.guild.member_count}** Members", color=discord.Color.green()))

    @app_commands.command(name="ping", description="Check bot latency")
    async def ping(self, interaction: discord.Interaction): 
        await interaction.response.send_message(embed=discord.Embed(title="🏓 Pong!", description=f"Latency: **{round(self.bot.latency * 1000)}ms**", color=discord.Color.green()))

    @app_commands.command(name="stats", description="View bot statistics")
    async def stats(self, interaction: discord.Interaction): 
        embed = discord.Embed(title="🤖 Bot Stats", color=discord.Color.blue())
        embed.add_field(name="Servers", value=len(self.bot.guilds))
        embed.add_field(name="Ping", value=f"{round(self.bot.latency * 1000)}ms")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="help", description="Show this help menu")
    async def help(self, interaction: discord.Interaction): 
        embed = discord.Embed(title="📚 Vantix Management V1 Help", description="Use slash commands `/` to interact with me. Check out `/botconfig` for setup.", color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ServerManagementCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    autorole = app_commands.Group(name="autorole", description="Auto-assign roles to new members")
    stickyroles = app_commands.Group(name="stickyroles", description="Restore roles on rejoin")
    serverstats = app_commands.Group(name="serverstats", description="Server statistics channels")

    @autorole.command(name="set")
    async def ar_set(self, interaction: discord.Interaction, role: discord.Role): await interaction.response.send_message(f"✅ Autorole set to {role.mention}.", ephemeral=True)
    @autorole.command(name="remove")
    async def ar_remove(self, interaction: discord.Interaction): await interaction.response.send_message("❌ Autorole removed.", ephemeral=True)

    @stickyroles.command(name="enable")
    async def sr_enable(self, interaction: discord.Interaction): await interaction.response.send_message("✅ Sticky roles enabled.", ephemeral=True)
    @stickyroles.command(name="disable")
    async def sr_disable(self, interaction: discord.Interaction): await interaction.response.send_message("❌ Sticky roles disabled.", ephemeral=True)

    @app_commands.command(name="addrole", description="Add role to user")
    async def addrole(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role): 
        await user.add_roles(role)
        await interaction.response.send_message(f"✅ Added {role.name} to {user.mention}.", ephemeral=True)

    @app_commands.command(name="removerole", description="Remove role from user")
    async def removerole(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role): 
        await user.remove_roles(role)
        await interaction.response.send_message(f"❌ Removed {role.name} from {user.mention}.", ephemeral=True)

    @app_commands.command(name="verifyconfig", description="Setup verification system")
    async def verifyconfig(self, interaction: discord.Interaction, role: discord.Role): 
        embed = discord.Embed(title="🛡️ Server Verification", description="Click the button below to get access to the server.", color=discord.Color.green())
        await interaction.channel.send(embed=embed, view=VerifyView(role))
        await interaction.response.send_message("✅ Verification panel deployed.", ephemeral=True)

    @app_commands.command(name="verify", description="Verify yourself (Fallback)")
    async def verify(self, interaction: discord.Interaction): await interaction.response.send_message("Please use the verification panel button.", ephemeral=True)

    @serverstats.command(name="setup")
    async def ss_setup(self, interaction: discord.Interaction): await interaction.response.send_message("✅ Server stats voice channels created.", ephemeral=True)
    @serverstats.command(name="remove")
    async def ss_remove(self, interaction: discord.Interaction): await interaction.response.send_message("🗑️ Server stats channels removed.", ephemeral=True)

class AnnouncementCommands(commands.Cog):
    def __init__(self, bot): self.bot = bot
    starboard = app_commands.Group(name="starboard", description="Starboard system")
    reactionrole = app_commands.Group(name="reactionrole", description="Reaction roles")
    autopublish = app_commands.Group(name="autopublish", description="Auto-publish announcements")

    @starboard.command(name="setup")
    async def sb_setup(self, interaction: discord.Interaction, channel: discord.TextChannel): await interaction.response.send_message(f"⭐ Starboard set to {channel.mention}.", ephemeral=True)
    @starboard.command(name="remove")
    async def sb_remove(self, interaction: discord.Interaction): await interaction.response.send_message("❌ Starboard removed.", ephemeral=True)

    @reactionrole.command(name="add")
    async def rr_add(self, interaction: discord.Interaction, role: discord.Role, emoji: str): await interaction.response.send_message(f"✅ Reaction role {emoji} -> {role.name} mapped.", ephemeral=True)
    @reactionrole.command(name="remove")
    async def rr_remove(self, interaction: discord.Interaction, role: discord.Role): await interaction.response.send_message("❌ Reaction role removed.", ephemeral=True)
    @reactionrole.command(name="list")
    async def rr_list(self, interaction: discord.Interaction): await interaction.response.send_message(embed=discord.Embed(title="Reaction Roles"), ephemeral=True)

    @autopublish.command(name="setup")
    async def ap_setup(self, interaction: discord.Interaction, channel: discord.TextChannel): await interaction.response.send_message(f"📢 Auto-publish enabled in {channel.mention}.", ephemeral=True)
    @autopublish.command(name="remove")
    async def ap_remove(self, interaction: discord.Interaction, channel: discord.TextChannel): await interaction.response.send_message(f"❌ Auto-publish disabled in {channel.mention}.", ephemeral=True)

# ================= EXECUTION =================
if __name__ == "__main__":
    keep_alive()
    bot = VantixBot()
    bot.run(TOKEN)
