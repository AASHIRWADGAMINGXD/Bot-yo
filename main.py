import os
import logging
import asyncio
import random
import string
import httpx
from threading import Thread
from dotenv import load_dotenv

from pyrogram import Client, filters, enums, idle
from pyrogram.errors import UserNotParticipant, FloodWait
from pyrogram.types import (
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    Message, 
    CallbackQuery,
    ChatPrivileges
)
from pymongo import MongoClient
from flask import Flask

# --- Basic Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Load Environment Variables ---
load_dotenv()

# --- Configuration ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "")
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", 0)) 
UPDATE_CHANNEL = os.environ.get("UPDATE_CHANNEL", "") 
ADMIN_PASSWORD = os.environ.get("ADMIN_PASS", "BhaiKaSystem")
FIREBASE_URL = os.environ.get("FIREBASE_URL", "https://pain-bot-database-default-rtdb.firebaseio.com")

ADMIN_IDS_STR = os.environ.get("ADMIN_IDS", "")
ADMINS = [int(admin_id.strip()) for admin_id in ADMIN_IDS_STR.split(',') if admin_id.strip().lstrip('-').isdigit()]

# --- Flask Web Server (24/7 Keep Alive) ---
flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return "🚀 Combined System Bot is alive and running 24/7!", 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    import logging as flask_logging
    flask_logging.getLogger('werkzeug').setLevel(flask_logging.ERROR)
    flask_app.run(host='0.0.0.0', port=port, use_reloader=False)

def keep_alive():
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask Keep-Alive Server Started!")

# --- Databases Setup (Mongo & Firebase Sync) ---
try:
    client_db = MongoClient(MONGO_URI)
    db = client_db['file_link_bot']
    files_collection = db['files']
    settings_collection = db['settings']
    logger.info("✅ MongoDB Connected Successfully!")
except Exception as e:
    logger.error(f"❌ Error connecting to MongoDB: {e}")
    exit(1)

# In-Memory & Firebase Vars
authorized_users = set()  
warns = {}                
afk_users = {}            
auto_replies = {}         
restricted_words = set()  

async def fb_put(path: str, data):
    try:
        async with httpx.AsyncClient() as client:
            await client.put(f"{FIREBASE_URL}/{path}.json", json=data)
    except Exception as e:
        logger.error(f"Failed to put data in Firebase: {e}")

async def fb_delete(path: str):
    try:
        async with httpx.AsyncClient() as client:
            await client.delete(f"{FIREBASE_URL}/{path}.json")
    except Exception as e:
        logger.error(f"Failed to delete data from Firebase: {e}")

async def load_firebase_data():
    global auto_replies, afk_users, restricted_words
    try:
        async with httpx.AsyncClient() as client:
            res1 = await client.get(f"{FIREBASE_URL}/autoreplies.json")
            if res1.status_code == 200 and res1.json(): auto_replies = res1.json()
            
            res2 = await client.get(f"{FIREBASE_URL}/afk.json")
            if res2.status_code == 200 and res2.json(): afk_users = res2.json()
                
            res3 = await client.get(f"{FIREBASE_URL}/restricted.json")
            if res3.status_code == 200 and res3.json(): restricted_words = set(res3.json().keys())
                
        logger.info("✅ Firebase Data Loaded Successfully!")
    except Exception as e:
        logger.error(f"❌ Failed to load Firebase data: {e}")

# --- Pyrogram Client ---
app = Client("SystemBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Helpers ---
def generate_random_string(length=6):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

async def is_user_member(client: Client, user_id: int) -> bool:
    if not UPDATE_CHANNEL: return True 
    try:
        channel = int(UPDATE_CHANNEL) if UPDATE_CHANNEL.lstrip('-').isdigit() else UPDATE_CHANNEL
        if isinstance(channel, str) and not channel.startswith('@'):
            channel = f"@{channel}"
        await client.get_chat_member(chat_id=channel, user_id=user_id)
        return True
    except UserNotParticipant: return False
    except Exception: return False

async def get_bot_mode() -> str:
    setting = settings_collection.find_one({"_id": "bot_mode"})
    if setting: return setting.get("mode", "public")
    settings_collection.update_one({"_id": "bot_mode"}, {"$set": {"mode": "public"}}, upsert=True)
    return "public"

async def is_admin(message: Message) -> bool:
    user_id = message.from_user.id
    if user_id in ADMINS or user_id in authorized_users: return True
    if message.chat.type == enums.ChatType.PRIVATE: return True
    
    try:
        member = await app.get_chat_member(message.chat.id, user_id)
        if member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
            return True
    except Exception: pass
    return False

# ==========================================
# ============ 1. FILE & LINK FEATURES =====
# ==========================================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    if len(message.command) > 1:
        file_id_str = message.command[1]
        
        if not await is_user_member(client, message.from_user.id):
            clean_channel = UPDATE_CHANNEL.replace('@', '')
            join_button = InlineKeyboardButton("🔗 Join Channel", url=f"https://t.me/{clean_channel}")
            joined_button = InlineKeyboardButton("✅ I Have Joined", callback_data=f"check_join_{file_id_str}")
            keyboard = InlineKeyboardMarkup([[join_button], [joined_button]])
            return await message.reply(
                f"👋 **Hello, {message.from_user.first_name}!**\n\nYe file access karne ke liye, aapko hamara update channel join karna hoga.",
                reply_markup=keyboard
            )

        file_record = files_collection.find_one({"_id": file_id_str})
        if file_record:
            try:
                await client.copy_message(chat_id=message.from_user.id, from_chat_id=LOG_CHANNEL, message_id=file_record['message_id'])
            except Exception as e:
                await message.reply(f"❌ Sorry, file bhejte waqt error aa gaya.\n`Error: {e}`")
        else:
            await message.reply("🤔 File not found! Link galat ya expire ho gaya ho.")
    else:
        txt = (
            "🙏 **Namaste Bhai! System ON hai.**\n\n"
            "**File Linker:** Mujhe private me koi file bhejo, main shareable link dunga.\n\n"
            "**Group Commands:**\n"
            "👮 `/warn` - Warning de bande ko\n"
            "☢️ `/nuke` - Chat clear\n"
            "📢 `/shout [msg]` - Zor se bol\n"
            "🛑 `/shoutconfig [word]` - Word restrict kar\n"
            "⬆️ `/promote` & ⬇️ `/demote` - Power control\n"
            "🐢 `/setslowmode [sec]` - Chat speed\n"
            "💤 `/afk [reason]` - Offline jao\n"
            "📌 `/pin` & `/unpin` - Message chipkao\n"
            "🎲 `/roll` & 🕺 `/bala` - Fun!\n"
            "🤖 `/setautoreply [word] | [reply]` - Auto jawab\n"
            "❌ `/deleteautoreply [word]`\n"
            "🔑 `/login [pass]` - Secret access\n"
            "⚙️ `/settings` - File bot mode (Admins)"
        )
        await message.reply(txt)

@app.on_message(filters.private & (filters.document | filters.video | filters.photo | filters.audio))
async def file_handler(client: Client, message: Message):
    bot_mode = await get_bot_mode()
    if bot_mode == "private" and message.from_user.id not in ADMINS:
        return await message.reply("😔 **Sorry!** Abhi sirf Admins hi files upload kar sakte hain.")

    status_msg = await message.reply("⏳ Please wait, file upload kar raha hu...", quote=True)
    try:
        forwarded_message = await message.forward(LOG_CHANNEL)
        file_id_str = generate_random_string()
        files_collection.insert_one({'_id': file_id_str, 'message_id': forwarded_message.id})
        
        bot_username = client.me.username if client.me else (await client.get_me()).username
        share_link = f"https://t.me/{bot_username}?start={file_id_str}"
        await status_msg.edit_text(f"✅ **Link Generated Successfully!**\n\n🔗 Your Link: `{share_link}`", disable_web_page_preview=True)
    except Exception as e:
        await status_msg.edit_text(f"❌ **Error!** Kuch galat ho gaya.\n`Details: {e}`")

@app.on_message(filters.command("settings") & filters.private)
async def settings_handler(client: Client, message: Message):
    if message.from_user.id not in ADMINS:
        return await message.reply("❌ Aapke paas permission nahi hai.")
    
    current_mode = await get_bot_mode()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 Public (Anyone)", callback_data="set_mode_public")],
        [InlineKeyboardButton("🔒 Private (Admins Only)", callback_data="set_mode_private")]
    ])
    await message.reply(f"⚙️ **Bot Settings**\nUpload mode: **{current_mode.upper()}**\nNaya mode select karein:", reply_markup=keyboard)

# ==========================================
# ============ 2. GROUP MODERATION =========
# ==========================================

@app.on_message(filters.command("login"))
async def login(client: Client, message: Message):
    if len(message.command) > 1 and message.command[1] == ADMIN_PASSWORD:
        authorized_users.add(message.from_user.id)
        await message.reply("😎 **System Set!** Ab tu Admin hai.")
    else:
        await message.reply("🤨 **Galat Password!** Nikal pehli fursat mein.")

@app.on_message(filters.command("warn") & filters.group)
async def warn_user(client: Client, message: Message):
    if not await is_admin(message): return await message.reply("⛔ Power nahi hai tere paas!")
    if not message.reply_to_message: return await message.reply("Kisko warn du? Message pe reply kar!")
        
    target = message.reply_to_message.from_user
    chat_id = message.chat.id
    bot_id = (await client.get_me()).id
    
    if target.id == bot_id:
        return await message.reply("🤬 **Apne baap ko warning dega?**")
        
    current_warns = warns.get(target.id, 0) + 1
    warns[target.id] = current_warns
    msg = f"⚠️ **Warning!**\nUser: {target.first_name}\nCount: {current_warns}/3\nSudhar ja varna uda dunga!"
    
    if current_warns >= 3:
        try:
            await client.ban_chat_member(chat_id, target.id)
            warns[target.id] = 0 
            msg = f"🚫 **Khatam!** {target.first_name} ko 3 warning ke baad uda diya."
        except Exception:
            msg += "\n(Main isko ban nahi kar pa raha, shayad ye Admin hai)"
    await message.reply(msg)

@app.on_message(filters.command("shout"))
async def shout(client: Client, message: Message):
    msg = " ".join(message.command[1:]).upper()
    if not msg: return await message.reply("Kya chilana hai? Likh to sahi!")
    await message.reply(f"📢 **{msg}**")

@app.on_message(filters.command("shoutconfig"))
async def shoutconfig(client: Client, message: Message):
    if not await is_admin(message): return
    if len(message.command) < 2: return await message.reply("❌ Kisko block karu? Likh to sahi.")
        
    word = "".join(message.command[1:]).replace(".", "").lower()
    restricted_words.add(word)
    await fb_put(f"restricted/{word}", True)
    await message.reply(f"🛑 **Restricted!** Word '{word}' ab block ho gaya hai.")

@app.on_message(filters.command("nuke") & filters.group)
async def nuke_request(client: Client, message: Message):
    if not await is_admin(message): return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Haan, Uda Do", callback_data='nuke_yes'),
         InlineKeyboardButton("❌ Nahi Mazak Tha", callback_data='nuke_no')]
    ])
    await message.reply("💣 **Confirm Nuke?**\nKya sach mein chat clear karni hai?", reply_markup=keyboard)

@app.on_message(filters.command("promote") & filters.group)
async def promote(client: Client, message: Message):
    if not await is_admin(message): return
    if not message.reply_to_message: return await message.reply("Reply kar jisko promote karna hai.")
    
    user_id = message.reply_to_message.from_user.id
    bot_id = (await client.get_me()).id
    if user_id == bot_id: return await message.reply("🤖 Main already sabse powerful hoon bhai!")
         
    try:
        await client.promote_chat_member(
            message.chat.id, user_id,
            privileges=ChatPrivileges(can_manage_chat=True, can_delete_messages=True, can_invite_users=True, can_pin_messages=True)
        )
        await message.reply("🌟 **Mubarak Ho!** Ab ye banda VIP (Admin) ban gaya hai.")
    except Exception as e: await message.reply(f"❌ Error: {e}")

@app.on_message(filters.command("demote") & filters.group)
async def demote(client: Client, message: Message):
    if not await is_admin(message): return
    if not message.reply_to_message: return
    
    user_id = message.reply_to_message.from_user.id
    bot_id = (await client.get_me()).id
    if user_id == bot_id: return await message.reply("❌ **Main apne aap ko demote nahi karunga!**")
         
    try:
        await client.promote_chat_member(
            message.chat.id, user_id, privileges=ChatPrivileges(can_manage_chat=False, can_delete_messages=False)
        )
        await message.reply("🤡 **Power Khatam!** Ab ye aam aadmi hai.")
    except Exception as e: await message.reply(f"❌ Error: {e}")

@app.on_message(filters.command("setslowmode") & filters.group)
async def set_slow_mode(client: Client, message: Message):
    if not await is_admin(message): return
    if len(message.command) < 2: return await message.reply("Time (seconds) likh bhai.")
    try:
        seconds = int(message.command[1])
        await client.set_slow_mode(message.chat.id, seconds)
        await message.reply(f"🐢 **Slow Mode On!** Ab har {seconds}s baad message aayega.")
    except Exception:
        await message.reply("❌ Error: Valid seconds daal (0, 10, 30, 60...).")

@app.on_message(filters.command("pin"))
async def pin_msg(client: Client, message: Message):
    if not await is_admin(message): return
    if not message.reply_to_message: return
    try:
        await message.reply_to_message.pin()
        await message.reply("📌 **Chipka Diya!** (Pinned)")
    except Exception: pass

@app.on_message(filters.command("unpin"))
async def unpin_msg(client: Client, message: Message):
    if not await is_admin(message): return
    if not message.reply_to_message: return
    try:
        await message.reply_to_message.unpin()
        await message.reply("📌 **Ukhad Diya!** (Unpinned)")
    except Exception: pass

# ==========================================
# ============ 3. FUN & AUTO SYSTEMS =======
# ==========================================

@app.on_message(filters.command("afk"))
async def afk(client: Client, message: Message):
    user = message.from_user
    reason = " ".join(message.command[1:]) if len(message.command) > 1 else "Bas aise hi"
    username = user.username.lower() if user.username else ""
    afk_data = {"reason": reason, "username": username}
    
    afk_users[str(user.id)] = afk_data
    await fb_put(f"afk/{user.id}", afk_data)
    await message.reply(f"💤 **{user.first_name}** abhi neend mein hai (AFK).\nReason: {reason}")

@app.on_message(filters.command("roll"))
async def roll(client: Client, message: Message):
    await client.send_dice(message.chat.id)

@app.on_message(filters.command("bala"))
async def bala(client: Client, message: Message):
    gif = "https://media1.tenor.com/m/C3eR0iU1tBIAAAAd/akshay-kumar-dance.gif"
    await client.send_animation(message.chat.id, gif, caption="🕺 **Shaitan ka Saala!**")

@app.on_message(filters.command("setautoreply"))
async def set_auto_reply(client: Client, message: Message):
    if not await is_admin(message): return
    text = " ".join(message.command[1:])
    if "|" not in text: return await message.reply("❌ Format galat hai.\nAise likh: `/setautoreply Hello | Namaste`")
    
    trigger, response = text.split("|", 1)
    clean_trigger = trigger.replace(" ", "").replace(".", "").lower()
    auto_replies[clean_trigger] = response.strip()
    await fb_put(f"autoreplies/{clean_trigger}", response.strip())
    await message.reply(f"✅ **Saved!** Jab koi '{trigger.strip()}' bolega, main '{response.strip()}' bolunga.")

@app.on_message(filters.command("deleteautoreply"))
async def delete_auto_reply(client: Client, message: Message):
    if not await is_admin(message): return
    if len(message.command) < 2: return await message.reply("❌ Kisko delete karu? Word likho.")
    
    trigger = " ".join(message.command[1:]).replace(" ", "").replace(".", "").lower()
    if trigger in auto_replies:
        del auto_replies[trigger]
        await fb_delete(f"autoreplies/{trigger}")
        await message.reply("🗑️ **Deleted!** Ab reply nahi karunga us word pe.")
    else:
        await message.reply("❌ Ye word set hi nahi tha bhai.")

# ==========================================
# ============ 4. CALLBACKS & INTERCEPTS ===
# ==========================================

@app.on_callback_query()
async def callbacks(client: Client, query: CallbackQuery):
    data = query.data
    user_id = query.from_user.id
    
    # 4a. Join Check
    if data.startswith("check_join_"):
        file_id_str = data.split("_", 2)[2]
        if await is_user_member(client, user_id):
            await query.answer("Thanks for joining! File bhej raha hu...", show_alert=True)
            file_record = files_collection.find_one({"_id": file_id_str})
            if file_record:
                try:
                    await client.copy_message(user_id, LOG_CHANNEL, file_record['message_id'])
                    await query.message.delete()
                except Exception as e: await query.message.edit_text(f"❌ Error: {e}")
            else: await query.message.edit_text("🤔 File not found!")
        else:
            await query.answer("Aapne abhi tak channel join nahi kiya hai!", show_alert=True)
            
    # 4b. Settings Modes
    elif data.startswith("set_mode_"):
        if user_id not in ADMINS: return await query.answer("Permission Denied!", show_alert=True)
        new_mode = data.split("_")[2]
        settings_collection.update_one({"_id": "bot_mode"}, {"$set": {"mode": new_mode}}, upsert=True)
        await query.answer(f"Mode {new_mode.upper()} par set ho gaya!", show_alert=True)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌍 Public (Anyone)", callback_data="set_mode_public")],
            [InlineKeyboardButton("🔒 Private (Admins Only)", callback_data="set_mode_private")]
        ])
        await query.message.edit_text(f"⚙️ **Bot Settings**\n✅ Upload mode ab **{new_mode.upper()}** hai.", reply_markup=keyboard)

    # 4c. Nuke Feature
    elif data == 'nuke_no':
        await query.edit_message_text("👍 **Bach gaye!** Nuke cancel kar diya.")
    elif data == 'nuke_yes':
        await query.edit_message_text("☢️ **Nuke Incoming...** Messages ud rahe hain.")
        msg_id = query.message.id
        msg_ids_to_delete = list(range(max(1, msg_id - 60), msg_id)) # Pyrogram accepts list of IDs!
        try:
            await client.delete_messages(query.message.chat.id, msg_ids_to_delete)
            await query.message.reply_text("💥 **Boom!** Messages ki chatni bana di.")
        except Exception:
            await query.message.reply_text("Error: Kuch messages delete nahi huye.")

@app.on_message(filters.text & ~filters.command & filters.group)
async def message_interceptor(client: Client, message: Message):
    text = message.text
    text_lower = text.lower()
    clean_text = text_lower.replace(" ", "").replace(".", "")
    user_id_str = str(message.from_user.id)

    # 1. Restricted Words Check
    for rword in restricted_words:
        if rword in clean_text:
            try: return await message.delete()
            except Exception: return

    # 2. Sender AFK Remove
    if user_id_str in afk_users:
        del afk_users[user_id_str]
        await fb_delete(f"afk/{user_id_str}") 
        await message.reply(f"👋 **Welcome Back {message.from_user.first_name}!**\nNeend khul gayi?")

    # 3. Replied User AFK
    if message.reply_to_message and message.reply_to_message.from_user:
        replied_user_id = str(message.reply_to_message.from_user.id)
        if replied_user_id in afk_users:
            reason = afk_users[replied_user_id]["reason"]
            await message.reply(f"Person Is busy: {reason}")

    # 4. Mentioned User AFK
    for uid, data in afk_users.items():
        afk_username = data.get("username")
        if afk_username and f"@{afk_username}" in text_lower:
            await message.reply(f"Person Is busy: {data['reason']}")
            break 

    # 5. Auto Reply Check
    for trigger, response in auto_replies.items():
        if trigger in clean_text:
            await message.reply(response)
            break

# --- Initialization Block ---
async def start_services():
    logger.info("🤖 Pyrogram Bot is connecting...")
    await app.start()
    logger.info("🔥 Loading Firebase In-Memory Data...")
    await load_firebase_data()
    logger.info("✅ Bot is fully running! Waiting for updates...")
    await idle()
    await app.stop()

if __name__ == "__main__":
    if not API_ID or not BOT_TOKEN:
        logger.error("❌ Required Environment variables are missing!")
        exit(1)
        
    keep_alive() # Start Web Server 24/7 Check
    
    # Run async setup tasks
    app.run(start_services())
