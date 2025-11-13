import discord
from discord.ext import commands
from discord.ui import Button, View

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# When bot is ready
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

# --- Ticket System ---
@bot.command()
@commands.has_permissions(manage_channels=True)
async def ticket(ctx):
    """Send ticket creation button"""
    button = Button(label="Create Ticket", style=discord.ButtonStyle.green, custom_id="create_ticket")

    async def button_callback(interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        existing_channel = discord.utils.get(guild.text_channels, name=f"ticket-{user.name.lower()}")
        if existing_channel:
            await interaction.response.send_message("You already have a ticket open.", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        channel = await guild.create_text_channel(f"ticket-{user.name}", overwrites=overwrites)
        await channel.send(f"{user.mention}, thank you for opening a ticket. A staff member will assist you soon.")
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)

    button.callback = button_callback
    view = View()
    view.add_item(button)
    await ctx.send("Click below to create a ticket:", view=view)

# --- Close Ticket Command ---
@bot.command()
@commands.has_permissions(manage_channels=True)
async def close(ctx):
    """Close the ticket channel"""
    if ctx.channel.name.startswith("ticket-"):
        await ctx.send("Closing this ticket in 5 seconds...")
        await discord.utils.sleep_until(discord.utils.utcnow() + discord.utils.timedelta(seconds=5))
        await ctx.channel.delete()
    else:
        await ctx.send("This command can only be used inside a ticket channel.")

# --- Moderation Commands ---
@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason=None):
    await member.kick(reason=reason)
    await ctx.send(f"{member} has been kicked.")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason=None):
    await member.ban(reason=reason)
    await ctx.send(f"{member} has been banned.")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def mute(ctx, member: discord.Member, *, reason=None):
    guild = ctx.guild
    muted_role = discord.utils.get(guild.roles, name="Muted")

    if not muted_role:
        muted_role = await guild.create_role(name="Muted")
        for channel in guild.channels:
            await channel.set_permissions(muted_role, send_messages=False, speak=False)

    await member.add_roles(muted_role, reason=reason)
    await ctx.send(f"{member} has been muted.")

# --- Unmute Command ---
@bot.command()
@commands.has_permissions(manage_roles=True)
async def unmute(ctx, member: discord.Member):
    muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if muted_role in member.roles:
        await member.remove_roles(muted_role)
        await ctx.send(f"{member} has been unmuted.")
    else:
        await ctx.send("That user is not muted.")

# --- Run Bot ---
bot.run("YOUR_BOT_TOKEN")
