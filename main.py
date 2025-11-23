import discord
from discord.ext import commands
from discord.ui import Button, View
import os
import asyncio
from keep_alive import keep_alive

# --- CONFIGURATION ---
# Load token from Environment Variable (Secure for Render)
TOKEN = os.getenv("DISCORD_TOKEN") 
GITHUB_REPO_LINK = "https://github.com/YourUsername/YourRepo" # Change this

# Setup Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True # Required for Kick/Ban and Tickets

# Define Bot
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            activity=discord.Game(name="Moderating..."),
            status=discord.Status.dnd # SET STATUS TO DND
        )

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')
        # Make ticket view persistent (listen for clicks even after restart)
        self.add_view(TicketLauncher())

bot = MyBot()

# --- MODERATION COMMANDS ---

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason=None):
    """Kicks a member."""
    await member.kick(reason=reason)
    await ctx.send(f'User {member} has been kicked. Reason: {reason}')

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason=None):
    """Bans a member."""
    await member.ban(reason=reason)
    await ctx.send(f'User {member} has been banned. Reason: {reason}')

@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int):
    """Clears chat messages."""
    await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f'Cleared {amount} messages.', delete_after=3)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You don't have permission to use this command!")

# --- GITHUB COMMAND ---

@bot.command()
async def github(ctx):
    """Sends the GitHub repository link."""
    embed = discord.Embed(title="GitHub Repository", description="Check out the source code below:", color=discord.Color.dark_grey())
    embed.add_field(name="Link", value=GITHUB_REPO_LINK)
    await ctx.send(embed=embed)

# --- TICKET SYSTEM (UI BASED) ---

# Button to Close Ticket
class CloseButton(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id="ticket_close")
    async def close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("Closing this ticket in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

# Button to Open Ticket
class TicketLauncher(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.green, emoji="📩", custom_id="ticket_create")
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        # Check if user already has a ticket (optional logic can go here)
        
        channel_name = f"ticket-{interaction.user.name}"
        channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites)
        
        await interaction.response.send_message(f"Ticket created! Check {channel.mention}", ephemeral=True)
        
        embed = discord.Embed(title="Support Ticket", description=f"Hello {interaction.user.mention}, a staff member will be with you shortly.", color=discord.Color.blue())
        await channel.send(embed=embed, view=CloseButton())

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_ticket(ctx):
    """Spawns the ticket creation message."""
    embed = discord.Embed(title="Support System", description="Click the button below to open a ticket!", color=discord.Color.green())
    await ctx.send(embed=embed, view=TicketLauncher())

# --- RUN BOT ---
if __name__ == "__main__":
    keep_alive() # Start the web server
    if TOKEN:
        bot.run(TOKEN)
