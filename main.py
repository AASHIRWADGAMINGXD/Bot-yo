import discord
from discord.ext import commands
import os
import sqlite3
import datetime
import asyncio
import random
from flask import Flask
from threading import Thread
from dotenv import load_dotenv

# --- LOAD ENVIRONMENT VARIABLES ---
load_dotenv()
TOKEN = os.getenv("TOKEN")

# --- FLASK SERVER (For 24/7 Uptime) ---
app = Flask('')

@app.route('/')
def home():
    return "Vantix Management is Online 24/7!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('vantix_data.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS settings 
                 (guild_id TEXT PRIMARY KEY, prefix TEXT, welcome_enabled INTEGER, bad_words TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS auto_replies 
                 (guild_id TEXT, trigger TEXT, response TEXT)''')
    conn.commit()
    return conn

db_conn = init_db()

# --- TICKET SYSTEM (Persistent View) ---
class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.danger, custom_id="vantix:ticket_btn")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user
        
        # Check if ticket already exists for this user to prevent spam
        existing_channel = discord.utils.get(guild.text_channels, name=f"ticket-{user.name.lower()}")
        if existing_channel:
            return await interaction.response.send_message(f"You already have an open ticket: {existing_channel.mention}", ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        channel = await guild.create_text_channel(f"ticket-{user.name}", overwrites=overwrites)
        
        embed = discord.Embed(
            title="Vantix Support", 
            description=f"Hello {user.mention}, thank you for reaching out to **Vantix Nodes**.\nOur staff will be with you shortly. Please describe your issue in detail.",
            color=0xFF0000,
            timestamp=datetime.datetime.utcnow()
        )
        embed.set_footer(text="Vantix Management System | Ticket Generated")
        
        await channel.send(embed=embed)
        await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)

# --- BOT CLASS ---
class VantixBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix=self.get_custom_prefix, intents=intents, help_command=None)

    async def get_custom_prefix(self, bot, message):
        if not message.guild: return "!"
        cursor = db_conn.cursor()
        cursor.execute("SELECT prefix FROM settings WHERE guild_id = ?", (str(message.guild.id),))
        res = cursor.fetchone()
        return res[0] if res else "!"

    async def setup_hook(self):
        self.add_view(TicketView())
        print("Successfully registered persistent TicketView.")

bot = VantixBot()

# --- EVENTS ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    await bot.change_presence(
        status=discord.Status.online, 
        activity=discord.Activity(type=discord.ActivityType.watching, name="Vantix Nodes Management")
    )

@bot.event
async def on_message(message):
    if message.author.bot: return

    # Bad Words Filter
    cursor = db_conn.cursor()
    cursor.execute("SELECT bad_words FROM settings WHERE guild_id = ?", (str(message.guild.id),))
    res = cursor.fetchone()
    if res and res[0]:
        bad_words = res[0].split(',')
        if any(word in message.content.lower() for word in bad_words):
            await message.delete()
            return

    # Auto Reply logic
    cursor.execute("SELECT response FROM auto_replies WHERE guild_id = ? AND trigger = ?", (str(message.guild.id), message.content.lower()))
    rep = cursor.fetchone()
    if rep:
        await message.reply(rep[0])

    await bot.process_commands(message)

# --- ADMINISTRATIVE COMMANDS ---

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_ticket(ctx):
    """Setup the ticket message in the current channel"""
    embed = discord.Embed(
        title="Vantix Support & Services",
        description="Click the button below to start a private consultation for:\n\n"
                    "🔌 **Minecraft Hosting Support**\n"
                    "🛠️ **Discord Server Setup Plans**\n"
                    "💳 **Billing & Payments**",
        color=0xFF0000
    )
    embed.set_image(url="https://via.placeholder.com/800x200/ff0000/ffffff?text=Vantix+Management") # Replace with your banner
    embed.set_footer(text="Official Vantix Management Bot")
    await ctx.send(embed=embed, view=TicketView())

@bot.command()
@commands.has_permissions(manage_messages=True)
async def add_badword(ctx, word: str):
    cursor = db_conn.cursor()
    cursor.execute("SELECT bad_words FROM settings WHERE guild_id = ?", (str(ctx.guild.id),))
    res = cursor.fetchone()
    current = res[0] if res and res[0] else ""
    new_list = f"{current},{word.lower()}" if current else word.lower()
    cursor.execute("INSERT OR REPLACE INTO settings (guild_id, bad_words) VALUES (?, ?)", (str(ctx.guild.id), new_list))
    db_conn.commit()
    await ctx.send(f"🚫 `{word}` added to the blacklist.")

@bot.command()
@commands.is_owner()
async def setstatus(ctx, mode: str):
    modes = {"online": discord.Status.online, "idle": discord.Status.idle, "dnd": discord.Status.dnd}
    if mode.lower() in modes:
        await bot.change_presence(status=modes[mode.lower()])
        await ctx.send(f"✅ Bot status set to `{mode}`")

@bot.command()
@commands.is_owner()
async def dm(ctx, member: discord.Member, *, content: str):
    try:
        await member.send(f"**Message from Vantix Admin:**\n{content}")
        await ctx.send(f"✅ DM sent to {member.display_name}")
    except:
        await ctx.send("❌ User has DMs disabled.")

# --- STARTUP ---
if __name__ == "__main__":
    if not TOKEN:
        print("CRITICAL ERROR: TOKEN not found in environment variables!")
    else:
        keep_alive()
        try:
            bot.run(TOKEN)
        except Exception as e:
            print(f"Failed to start bot: {e}")

