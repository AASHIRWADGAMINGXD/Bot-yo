import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import asyncio
import logging
from datetime import datetime, timedelta
import os

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Bot configuration
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.moderation = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or('!'),
    intents=intents,
    help_command=None
)

# Load configuration
def load_config():
    if os.path.exists('config.json'):
        with open('config.json', 'r') as f:
            return json.load(f)
    return {
        "token": os.getenv("DISCORD_TOKEN"),
        "owner_id": None,
        "default_prefix": "!"
    }

config = load_config()

# Global data storage
warns = {}
mutes = {}

# ============================================================================
# BOT EVENTS
# ============================================================================

@bot.event
async def on_ready():
    logger.info(f'Bot logged in as {bot.user.name} (ID: {bot.user.id})')
    logger.info(f'Connected to {len(bot.guilds)} guilds')
    status_task.start()

@bot.event
async def on_member_join(member):
    """Welcome new members"""
    if os.path.exists('config.json'):
        with open('config.json', 'r') as f:
            guild_configs = json.load(f).get('guilds', {})
            guild_config = guild_configs.get(str(member.guild.id), {})
            if guild_config.get('welcome_channel'):
                channel = member.guild.get_channel(int(guild_config['welcome_channel']))
                if channel:
                    embed = discord.Embed(
                        title="Welcome!",
                        description=f"Welcome to **{member.guild.name}**, {member.mention}!",
                        color=discord.Color.green()
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    embed.add_field(name="Member Count", value=str(member.guild.member_count))
                    await channel.send(embed=embed)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    await bot.process_commands(message)

# ============================================================================
# STATUS TASK (24/7)
# ============================================================================

@tasks.loop(seconds=30)
async def status_task():
    """Keep bot active with rotating status"""
    statuses = [
        discord.Activity(type=discord.ActivityType.watching, name="over the server"),
        discord.Activity(type=discord.ActivityType.playing, name="with moderation"),
        discord.Activity(type=discord.ActivityType.listening, name="to commands"),
        discord.Activity(type=discord.ActivityType.competing, name="in moderation")
    ]
    await bot.change_presence(status=discord.Status.online, activity=statuses[int(datetime.now().timestamp()) % len(statuses)])

# ============================================================================
# MODERATION COG
# ============================================================================

class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        """Kick a member from the server"""
        if member.guild_permissions.administrator:
            await ctx.send("Cannot kick an administrator!")
            return

        try:
            await member.kick(reason=f"{ctx.author} - {reason}")
            embed = discord.Embed(
                title="Member Kicked",
                description=f"{member.mention} has been kicked from the server.",
                color=discord.Color.orange()
            )
            embed.add_field(name="Reason", value=reason)
            embed.add_field(name="Moderator", value=ctx.author.mention)
            embed.set_thumbnail(url=member.display_avatar.url)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"Error kicking member: {e}")

    @commands.command()
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        """Ban a member from the server"""
        if member.guild_permissions.administrator:
            await ctx.send("Cannot ban an administrator!")
            return

        try:
            await member.ban(reason=f"{ctx.author} - {reason}")
            embed = discord.Embed(
                title="Member Banned",
                description=f"{member.mention} has been banned from the server.",
                color=discord.Color.red()
            )
            embed.add_field(name="Reason", value=reason)
            embed.add_field(name="Moderator", value=ctx.author.mention)
            embed.set_thumbnail(url=member.display_avatar.url)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"Error banning member: {e}")

    @commands.command()
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx, user_id: int, *, reason: str = "No reason provided"):
        """Unban a member by ID"""
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=f"{ctx.author} - {reason}")
            embed = discord.Embed(
                title="Member Unbanned",
                description=f"{user.mention} has been unbanned.",
                color=discord.Color.green()
            )
            embed.add_field(name="Reason", value=reason)
            embed.add_field(name="Moderator", value=ctx.author.mention)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"Error unbanning member: {e}")

    @commands.command()
    @commands.has_permissions(moderate_members=True)
    async def timeout(self, ctx, member: discord.Member, minutes: int, *, reason: str = "No reason provided"):
        """Timeout a member"""
        if member.guild_permissions.administrator:
            await ctx.send("Cannot timeout an administrator!")
            return

        try:
            await member.timeout(timedelta(minutes=minutes), reason=f"{ctx.author} - {reason}")
            embed = discord.Embed(
                title="Member Timed Out",
                description=f"{member.mention} has been timed out for {minutes} minutes.",
                color=discord.Color.orange()
            )
            embed.add_field(name="Reason", value=reason)
            embed.add_field(name="Moderator", value=ctx.author.mention)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"Error timing out member: {e}")

    @commands.command()
    @commands.has_permissions(moderate_members=True)
    async def remove_timeout(self, ctx, member: discord.Member):
        """Remove timeout from a member"""
        try:
            await member.timeout(None)
            embed = discord.Embed(
                title="Timeout Removed",
                description=f"{member.mention}'s timeout has been removed.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"Error removing timeout: {e}")

    @commands.command()
    @commands.has_permissions(manage_messages=True)
    async def purge(self, ctx, amount: int):
        """Delete a specified number of messages"""
        if amount > 100:
            await ctx.send("Cannot delete more than 100 messages at once!")
            return

        deleted = await ctx.channel.purge(limit=amount + 1)
        embed = discord.Embed(
            title="Messages Purged",
            description=f"Deleted {len(deleted) - 1} messages.",
            color=discord.Color.blue()
        )
        embed.add_field(name="Moderator", value=ctx.author.mention)
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(5)
        try:
            await msg.delete()
        except:
            pass

    @commands.command()
    @commands.has_permissions(moderate_members=True)
    async def warn(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        """Warn a member"""
        guild_id = str(ctx.guild.id)
        member_id = str(member.id)

        if guild_id not in warns:
            warns[guild_id] = {}
        if member_id not in warns[guild_id]:
            warns[guild_id][member_id] = []

        warns[guild_id][member_id].append({
            "reason": reason,
            "moderator": str(ctx.author),
            "timestamp": datetime.now().isoformat()
        })

        embed = discord.Embed(
            title="Member Warned",
            description=f"{member.mention} has been warned.",
            color=discord.Color.orange()
        )
        embed.add_field(name="Reason", value=reason)
        embed.add_field(name="Moderator", value=ctx.author.mention)
        embed.add_field(name="Total Warns", value=str(len(warns[guild_id][member_id])))
        await ctx.send(embed=embed)

        # DM the member
        try:
            await member.send(f"You have been warned in **{ctx.guild.name}**.\nReason: {reason}")
        except:
            pass

    @commands.command()
    @commands.has_permissions(moderate_members=True)
    async def warnings(self, ctx, member: discord.Member):
        """Show warnings for a member"""
        guild_id = str(ctx.guild.id)
        member_id = str(member.id)

        if guild_id not in warns or member_id not in warns[guild_id] or not warns[guild_id][member_id]:
            embed = discord.Embed(
                title="No Warnings",
                description=f"{member.mention} has no warnings.",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title=f"Warnings for {member.display_name}",
                description=f"Total warnings: **{len(warns[guild_id][member_id])}**",
                color=discord.Color.orange()
            )
            for i, warn in enumerate(warns[guild_id][member_id], 1):
                embed.add_field(
                    name=f"Warning #{i}",
                    value=f"**Reason:** {warn['reason']}\n**Moderator:** {warn['moderator']}",
                    inline=False
                )
        await ctx.send(embed=embed)

    @commands.command()
    @commands.has_permissions(moderate_members=True)
    async def clear_warns(self, ctx, member: discord.Member):
        """Clear all warnings for a member"""
        guild_id = str(ctx.guild.id)
        member_id = str(member.id)

        if guild_id in warns and member_id in warns[guild_id]:
            warns[guild_id][member_id] = []
            embed = discord.Embed(
                title="Warnings Cleared",
                description=f"All warnings for {member.mention} have been cleared.",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="No Warnings",
                description=f"{member.mention} has no warnings to clear.",
                color=discord.Color.green()
            )
        await ctx.send(embed=embed)

    @commands.command()
    @commands.has_permissions(manage_channels=True)
    async def lock(self, ctx):
        """Lock the current channel"""
        overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        embed = discord.Embed(
            title="Channel Locked",
            description=f"{ctx.channel.mention} has been locked.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

    @commands.command()
    @commands.has_permissions(manage_channels=True)
    async def unlock(self, ctx):
        """Unlock the current channel"""
        overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = True
        await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        embed = discord.Embed(
            title="Channel Unlocked",
            description=f"{ctx.channel.mention} has been unlocked.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @commands.command()
    @commands.has_permissions(manage_channels=True)
    async def nuke(self, ctx):
        """Delete and recreate the current channel"""
        if not isinstance(ctx.channel, discord.TextChannel):
            await ctx.send("This command can only be used in text channels!")
            return

        confirm = await ctx.send("**Are you sure you want to nuke this channel?** (yes/no)")

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ['yes', 'no']

        try:
            msg = await bot.wait_for('message', timeout=30, check=check)
            if msg.content.lower() == 'yes':
                position = ctx.channel.position
                new_channel = await ctx.channel.clone()
                await ctx.channel.delete()
                await new_channel.move_to(position=position)
                embed = discord.Embed(
                    title="Channel Nuked",
                    description=f"{new_channel.mention} has been nuked!",
                    color=discord.Color.red()
                )
                await new_channel.send(embed=embed)
            else:
                await ctx.send("Cancelled.")
        except asyncio.TimeoutError:
            await ctx.send("Timed out.")

bot.add_cog(ModerationCog(bot))

# ============================================================================
# EMBED BUILDER COG
# ============================================================================

class EmbedBuilderCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="embed")
    @app_commands.describe(
        title="The title of the embed",
        description="The description of the embed",
        color="The color in hex (e.g., FF5733)"
    )
    @commands.has_permissions(manage_messages=True)
    async def embed(self, ctx, *, text: str):
        """Create an embed from text"""
        # Simple embed from text
        embed = discord.Embed(
            description=text,
            color=discord.Color.random()
        )
        embed.set_footer(text=f"Created by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @app_commands.command(name="embed_builder")
    @commands.has_permissions(manage_messages=True)
    async def embed_builder(self, ctx):
        """Interactive embed builder"""
        embed = discord.Embed(
            title="Embed Builder",
            description="Click the buttons below to build your embed!",
            color=discord.Color.blue()
        )

        view = discord.ui.View()

        async def send_embed(interaction: discord.Interaction):
            await interaction.response.send_message(
                "Use `/embed_send` to send your custom embed!",
                ephemeral=True
            )

        view.add_item(discord.ui.Button(label="Start Building", style=discord.ButtonStyle.primary))
        await ctx.send(embed=embed, view=view)

bot.add_cog(EmbedBuilderCog(bot))

# ============================================================================
# UTILITY COG
# ============================================================================

class UtilityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def ping(self, ctx):
        """Check bot latency"""
        latency = round(bot.latency * 1000)
        embed = discord.Embed(
            title="Pong!",
            description=f"Bot latency: **{latency}ms**",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @commands.command()
    async def serverinfo(self, ctx):
        """Show server information"""
        guild = ctx.guild
        embed = discord.Embed(
            title=f"Server Info: {guild.name}",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "Unknown")
        embed.add_field(name="Members", value=str(guild.member_count))
        embed.add_field(name="Channels", value=str(len(guild.channels)))
        embed.add_field(name="Roles", value=str(len(guild.roles)))
        embed.add_field(name="Created", value=guild.created_at.strftime("%B %d, %Y"))
        await ctx.send(embed=embed)

    @commands.command()
    async def userinfo(self, ctx, member: discord.Member = None):
        """Show user information"""
        member = member or ctx.author
        embed = discord.Embed(
            title=f"User Info: {member.display_name}",
            color=member.color
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=str(member.id))
        embed.add_field(name="Joined", value=member.joined_at.strftime("%B %d, %Y") if member.joined_at else "Unknown")
        embed.add_field(name="Registered", value=member.created_at.strftime("%B %d, %Y"))
        embed.add_field(name="Roles", value=f"{len(member.roles)} roles")
        await ctx.send(embed=embed)

    @commands.command()
    async def avatar(self, ctx, member: discord.Member = None):
        """Show user avatar"""
        member = member or ctx.author
        embed = discord.Embed(
            title=f"{member.display_name}'s Avatar",
            color=member.color
        )
        embed.set_image(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command()
    @commands.has_permissions(manage_guild=True)
    async def setwelcome(self, ctx, channel: discord.TextChannel):
        """Set the welcome channel"""
        if not os.path.exists('config.json'):
            with open('config.json', 'w') as f:
                json.dump({"guilds": {}}, f)

        with open('config.json', 'r') as f:
            data = json.load(f)

        if "guilds" not in data:
            data["guilds"] = {}

        if str(ctx.guild.id) not in data["guilds"]:
            data["guilds"][str(ctx.guild.id)] = {}

        data["guilds"][str(ctx.guild.id)]["welcome_channel"] = str(channel.id)

        with open('config.json', 'w') as f:
            json.dump(data, f, indent=2)

        embed = discord.Embed(
            title="Welcome Channel Set",
            description=f"Welcome messages will now be sent to {channel.mention}",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

bot.add_cog(UtilityCog(bot))

# ============================================================================
# HELP COMMAND
# ============================================================================

class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def help(self, ctx):
        """Show help message"""
        embed = discord.Embed(
            title="Help Menu",
            description="Welcome to the Advanced Discord Management Bot!",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="Moderation Commands",
            value="`!kick`, `!ban`, `!unban`, `!timeout`, `!remove_timeout`, `!purge`, `!warn`, `!warnings`, `!clear_warns`, `!lock`, `!unlock`, `!nuke`",
            inline=False
        )
        embed.add_field(
            name="Utility Commands",
            value="`!ping`, `!serverinfo`, `!userinfo`, `!avatar`, `!setwelcome`",
            inline=False
        )
        embed.add_field(
            name="Embed Commands",
            value="`/embed`, `/embed_builder`",
            inline=False
        )
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed)

bot.add_cog(HelpCog(bot))

# ============================================================================
# RUN BOT
# ============================================================================

if __name__ == "__main__":
    token = config.get("token") or os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("No token found! Set DISCORD_TOKEN environment variable or add it to config.json")
        exit(1)

    bot.run(token)
