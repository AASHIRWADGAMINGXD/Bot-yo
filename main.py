import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import sqlite3
import datetime
import asyncio
import random
from flask import Flask
from threading import Thread

# --- FLASK SERVER FOR 24/7 ---
app = Flask('')

@app.route('/')
def home():
    return "Vantix Management is Online!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- DATABASE SETUP ---
conn = sqlite3.connect('vantix_data.db')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS settings 
             (guild_id TEXT PRIMARY KEY, prefix TEXT, welcome_enabled INTEGER, bad_words TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS auto_replies 
             (guild_id TEXT, trigger TEXT, response TEXT)''')
conn.commit()

# --- BOT SETUP ---
def get_prefix(bot, message):
    c.execute("SELECT prefix FROM settings WHERE guild_id = ?", (str(message.guild.id),))
    res = c.fetchone()
    return res[0] if res else "!"

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)

# --- HELPER FUNCTIONS ---
def is_owner(ctx):
    return ctx.author.id == ctx.guild.owner_id

# --- EVENTS ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    await bot.change_presence(status=discord.Status.online, activity=discord.Game(name="Managing Vantix Nodes"))

@bot.event
async def on_member_join(member):
    c.execute("SELECT welcome_enabled FROM settings WHERE guild_id = ?", (str(member.guild.id),))
    res = c.fetchone()
    if res and res[0] == 1:
        channel = discord.utils.get(member.guild.text_channels, name="welcome")
        if channel:
            embed = discord.Embed(title="Welcome to Vantix Nodes!", color=0xFF0000, timestamp=datetime.datetime.utcnow())
            embed.description = f"Welcome {member.mention} to the server!\n\n📜 **Read Rules:** <#rules-channel-id>\n⚖️ **Terms:** <#tos-channel-id>\n🚀 **Enjoy your stay!**"
            embed.set_thumbnail(url=member.avatar.url if member.avatar else None)
            embed.set_footer(text="Vantix Management System")
            await channel.send(embed=embed)

@bot.event
async def on_message(message):
    if message.author.bot: return

    # Bad Words Filter
    c.execute("SELECT bad_words FROM settings WHERE guild_id = ?", (str(message.guild.id),))
    res = c.fetchone()
    if res and res[0]:
        words = res[0].split(',')
        if any(word in message.content.lower() for word in words):
            await message.delete()
            await message.channel.send(f"{message.author.mention}, that word is not allowed here!", delete_after=5)
            return

    # Auto Reply
    c.execute("SELECT response FROM auto_replies WHERE guild_id = ? AND trigger = ?", (str(message.guild.id), message.content.lower()))
    rep = c.fetchone()
    if rep:
        await message.reply(rep[0])

    await bot.process_commands(message)

# --- MODERATION COMMANDS ---
@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    await member.kick(reason=reason)
    await ctx.send(f"🔴 **{member.name}** has been kicked. Reason: {reason}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    await member.ban(reason=reason)
    await ctx.send(f"🚫 **{member.name}** has been banned. Reason: {reason}")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def timeout(ctx, member: discord.Member, minutes: int, *, reason="No reason"):
    duration = datetime.timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    await ctx.send(f"⏳ **{member.name}** timed out for {minutes}m. Reason: {reason}")

# --- CHANNEL MANAGEMENT ---
@bot.command()
@commands.has_permissions(manage_channels=True)
async def lock(ctx):
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
    await ctx.send("🔒 Channel locked.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def unlock(ctx):
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=True)
    await ctx.send("🔓 Channel unlocked.")

# --- UTILITY & CUSTOMIZATION ---
@bot.command()
async def prefix(ctx, new_prefix: str):
    if not ctx.author.guild_permissions.administrator: return
    c.execute("INSERT OR REPLACE INTO settings (guild_id, prefix) VALUES (?, ?)", (str(ctx.guild.id), new_prefix))
    conn.commit()
    await ctx.send(f"✅ Prefix changed to: `{new_prefix}`")

@bot.command()
async def status(ctx, type: str):
    if not is_owner(ctx): return
    states = {"online": discord.Status.online, "idle": discord.Status.idle, "dnd": discord.Status.dnd}
    if type.lower() in states:
        await bot.change_presence(status=states[type.lower()])
        await ctx.send(f"Status changed to `{type}`")

@bot.command()
async def dm(ctx, member: discord.Member, *, message: str):
    if not is_owner(ctx): return
    try:
        await member.send(f"**Message from Vantix Admin:**\n{message}")
        await ctx.send(f"✅ Sent to {member.name}")
    except:
        await ctx.send("❌ Could not DM user.")

# --- TICKET SYSTEM (EMBED CUSTOMIZER) ---
@bot.command()
async def setup_ticket(ctx, title, color_hex, *, description):
    if not ctx.author.guild_permissions.administrator: return
    color = int(color_hex.replace("#", ""), 16)
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="Click the button below to open a ticket.")
    
    view = discord.ui.View()
    button = discord.ui.Button(label="Open Ticket", style=discord.ButtonStyle.danger, custom_id="open_ticket_btn")
    view.add_item(button)
    
    await ctx.send(embed=embed, view=view)

# --- GIVEAWAY ---
@bot.command()
async def giveaway(ctx, duration_sec: int, *, prize: str):
    if not ctx.author.guild_permissions.administrator: return
    embed = discord.Embed(title="🎉 GIVEAWAY 🎉", description=f"Prize: **{prize}**\nReact with 🎉 to enter!", color=0xFF0000)
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("🎉")
    await asyncio.sleep(duration_sec)
    
    new_msg = await ctx.channel.fetch_message(msg.id)
    users = [user async for user in new_msg.reactions[0].users() if not user.bot]
    if users:
        winner = random.choice(users)
        await ctx.send(f"Congratulations {winner.mention}! You won **{prize}**!")
    else:
        await ctx.send("No one entered the giveaway.")

# --- WEBHOOK COMMAND ---
@bot.command()
async def webhook_send(ctx, webhook_url: str, title: str, *, content: str):
    if not ctx.author.guild_permissions.administrator: return
    from discord import SyncWebhook
    webhook = SyncWebhook.from_url(webhook_url)
    embed = discord.Embed(title=title, description=content, color=0xFF0000)
    webhook.send(embed=embed, username="Vantix Management")
    await ctx.send("✅ Webhook message sent.")

# --- AUTO REPLY MGMT ---
@bot.command()
async def add_reply(ctx, trigger: str, *, response: str):
    if not ctx.author.guild_permissions.administrator: return
    c.execute("INSERT INTO auto_replies VALUES (?, ?, ?)", (str(ctx.guild.id), trigger.lower(), response))
    conn.commit()
    await ctx.send(f"✅ Auto-reply added for `{trigger}`")

@bot.command()
async def remove_reply(ctx, trigger: str):
    if not ctx.author.guild_permissions.administrator: return
    c.execute("DELETE FROM auto_replies WHERE guild_id = ? AND trigger = ?", (str(ctx.guild.id), trigger.lower()))
    conn.commit()
    await ctx.send(f"🗑️ Removed auto-reply for `{trigger}`")

# --- BAD WORDS MGMT ---
@bot.command()
async def add_badword(ctx, word: str):
    if not ctx.author.guild_permissions.administrator: return
    c.execute("SELECT bad_words FROM settings WHERE guild_id = ?", (str(ctx.guild.id),))
    res = c.fetchone()
    current = res[0] if res and res[0] else ""
    new_list = f"{current},{word.lower()}" if current else word.lower()
    c.execute("INSERT OR REPLACE INTO settings (guild_id, bad_words) VALUES (?, ?)", (str(ctx.guild.id), new_list))
    conn.commit()
    await ctx.send(f"🚫 Added `{word}` to blacklist.")

# --- STARTUP ---
keep_alive()
# token = os.environ.get("TOKEN")
# bot.run(token)
print("Run the bot by providing your token at the end of the file.")

