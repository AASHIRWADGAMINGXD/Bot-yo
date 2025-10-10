# bot.py
# Single-file Discord ticket bot (Python 3.10+)
# Requires: discord.py (v2.3+). Install: pip install -U discord.py

import os
import io
import asyncio
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button

# ---------- Configuration from environment ----------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")  # required
GUILD_ID = os.getenv("GUILD_ID")  # optional: use for testing a single guild (fast command register)
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID")) if os.getenv("STAFF_ROLE_ID") else None
TICKETS_CATEGORY_NAME = os.getenv("TICKETS_CATEGORY_NAME", "TICKETS")
TICKET_CHANNEL_PREFIX = os.getenv("TICKET_CHANNEL_PREFIX", "ticket-")

if not DISCORD_TOKEN:
    raise SystemExit("Missing DISCORD_TOKEN environment variable")

# ---------- Intents ----------
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = True
intents.members = True

# ---------- Bot setup ----------
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ---------- Helper utilities ----------
def safe_channel_name(name: str, limit=90):
    name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name).lower()
    return name[:limit].strip("-")

async def ensure_category(guild: discord.Guild, category_name: str):
    for cat in guild.categories:
        if cat.name == category_name:
            return cat
    return await guild.create_category(category_name)

def make_transcript_text(messages):
    lines = []
    for m in reversed(messages):  # chronological
        ts = m.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        author = f"{m.author} ({m.author.id})"
        content = m.content or ""
        # Mark attachments
        if m.attachments:
            attach_urls = " ".join(a.url for a in m.attachments)
            content += f"\n[attachments: {attach_urls}]"
        lines.append(f"[{ts}] {author}: {content}")
    return "\n\n".join(lines)

# ---------- UI View ----------
class TicketView(View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=None)
        self.owner_id = owner_id

    @discord.ui.button(label="Close ticket", style=discord.ButtonStyle.danger, emoji="🔒")
    async def close_button(self, interaction: discord.Interaction, button: Button):
        # Only allow staff or the ticket owner to close
        is_owner = interaction.user.id == self.owner_id
        is_staff = False
        if STAFF_ROLE_ID:
            is_staff = any(r.id == STAFF_ROLE_ID for r in interaction.user.roles)
        if not (is_owner or is_staff or interaction.user.guild_permissions.manage_channels):
            await interaction.response.send_message("You don't have permission to close this ticket.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=False)
        channel = interaction.channel
        # gather last 100 messages
        messages = [m async for m in channel.history(limit=1000)]
        transcript = make_transcript_text(messages)
        bio = io.BytesIO(transcript.encode("utf-8"))
        bio.seek(0)
        filename = f"transcript-{channel.name}.txt"
        try:
            await interaction.followup.send("Ticket closed. Transcript attached.", file=discord.File(bio, filename=filename))
        except Exception:
            # fallback: DM owner
            owner = interaction.guild.get_member(self.owner_id)
            if owner:
                try:
                    await owner.send("Here is your ticket transcript:", file=discord.File(bio, filename=filename))
                except Exception:
                    pass
        # optional: rename / archive / lock channel
        try:
            await channel.set_permissions(interaction.guild.default_role, read_messages=False, send_messages=False)
            if STAFF_ROLE_ID:
                staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
                if staff_role:
                    await channel.set_permissions(staff_role, read_messages=True, send_messages=False)
        except Exception:
            pass

        # move channel name to closed- prefix
        try:
            await channel.edit(name=f"closed-{channel.name}", reason="Ticket closed")
        except Exception:
            pass

        # disable view buttons
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

# ---------- Slash commands ----------
@tree.command(name="ticket", description="Open a support ticket", guild=discord.Object(id=int(GUILD_ID)) if GUILD_ID else None)
@app_commands.describe(reason="Short reason for the ticket", image="Optional image attachment or image URL")
async def ticket(interaction: discord.Interaction, reason: str = None, image: discord.Attachment = None):
    """Slash command: /ticket [reason] [image]"""
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("This command must be used in a server.", ephemeral=True)
        return

    # Ensure category
    category = await ensure_category(guild, TICKETS_CATEGORY_NAME)

    # channel name
    base_name = f"{TICKET_CHANNEL_PREFIX}{interaction.user.name}-{interaction.user.discriminator}"
    channel_name = safe_channel_name(base_name)[:90]

    # avoid name collisions by appending short suffix if exists
    existing = discord.utils.get(guild.channels, name=channel_name)
    if existing:
        channel_name = f"{channel_name}-{interaction.user.id}"

    # permissions: default deny, allow user + staff + manage_channels perms
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True)
    }
    if STAFF_ROLE_ID:
        staff_role = guild.get_role(STAFF_ROLE_ID)
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True, read_message_history=True)

    # create the channel
    channel = await guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites, reason=f"Ticket created by {interaction.user}")

    # build embed
    embed = discord.Embed(title="New Ticket", color=discord.Color.blue(), timestamp=datetime.utcnow())
    embed.add_field(name="User", value=f"{interaction.user.mention} ({interaction.user})", inline=False)
    if reason:
        embed.add_field(name="Reason", value=reason[:1024], inline=False)

    # handle image: attachment precedence, else if user provided an attachment object it's already passed
    if image:
        # if it's an attachment, its url will be accessible
        embed.set_image(url=image.url)
    # else, try to see if reason contains an image URL (not attempting advanced validation)

    await channel.send(content=f"{interaction.user.mention} Thank you — a staff member will be with you shortly.", embed=embed, view=TicketView(owner_id=interaction.user.id))

    # notify the user
    await interaction.followup.send(f"Ticket created: {channel.mention}", ephemeral=True)

# ---------- Simple admin helper to close ticket via slash ----------
@tree.command(name="close_ticket", description="Close the ticket in this channel (staff only)", guild=discord.Object(id=int(GUILD_ID)) if GUILD_ID else None)
async def close_ticket(interaction: discord.Interaction):
    if not interaction.channel:
        await interaction.response.send_message("This command must be used in a server channel.", ephemeral=True)
        return
    view = TicketView(owner_id=0)  # owner not required here; permission check below
    # reuse the close button logic by calling it: simulate a button press
    # only allow staff / manage_channels
    is_staff = False
    if STAFF_ROLE_ID:
        is_staff = any(r.id == STAFF_ROLE_ID for r in interaction.user.roles)
    if not (is_staff or interaction.user.guild_permissions.manage_channels):
        await interaction.response.send_message("You don't have permission to close this ticket.", ephemeral=True)
        return
    # Close: gather messages and send transcript then lock channel
    await interaction.response.defer(ephemeral=False)
    messages = [m async for m in interaction.channel.history(limit=1000)]
    transcript = make_transcript_text(messages)
    bio = io.BytesIO(transcript.encode("utf-8"))
    bio.seek(0)
    filename = f"transcript-{interaction.channel.name}.txt"
    try:
        await interaction.followup.send("Ticket closed. Transcript attached.", file=discord.File(bio, filename=filename))
    except Exception:
        pass
    try:
        await interaction.channel.set_permissions(interaction.guild.default_role, read_messages=False, send_messages=False)
        if STAFF_ROLE_ID:
            staff_role = interaction.guild.get_role(STAFF_ROLE_ID)
            if staff_role:
                await interaction.channel.set_permissions(staff_role, read_messages=True, send_messages=False)
    except Exception:
        pass
    try:
        await interaction.channel.edit(name=f"closed-{interaction.channel.name}")
    except Exception:
        pass

# ---------- On ready ----------
@bot.event
async def on_ready():
    # Sync commands to the guild (fast). If GUILD_ID not set, use global sync.
    try:
        if GUILD_ID:
            await tree.sync(guild=discord.Object(id=int(GUILD_ID)))
        else:
            await tree.sync()
        print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    except Exception as e:
        print("Failed to sync commands:", e)

# ---------- Run ----------
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
