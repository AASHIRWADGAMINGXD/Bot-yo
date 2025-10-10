# bot.py
# Telegram anti-thala bot
# Requires: pip install python-telegram-bot==21.5

import os
from telegram import Update, ChatPermissions
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

TOKEN = os.getenv("BOT_TOKEN")  # set in environment variables

if not TOKEN:
    raise SystemExit("Missing BOT_TOKEN environment variable")

# Track user spam count
user_spam_count = {}

# --- Handler: when message contains "thala" ---
async def catch_thala(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    text = update.message.text.lower()
    if "thala" in text:
        user_id = update.message.from_user.id
        chat_id = update.message.chat_id

        # Delete the message
        try:
            await update.message.delete()
        except Exception:
            pass

        # Reply funny text
        await context.bot.send_message(chat_id, "Tera upar bala")

        # Count spam
        user_spam_count[user_id] = user_spam_count.get(user_id, 0) + 1

        # If spammed 3+ times, mute for 10 mins
        if user_spam_count[user_id] >= 3:
            try:
                await context.bot.restrict_chat_member(
                    chat_id,
                    user_id,
                    ChatPermissions(can_send_messages=False),
                    until_date=None
                )
                await context.bot.send_message(chat_id, f"User {update.message.from_user.first_name} muted for spam 🚫")
            except Exception as e:
                print("Failed to mute:", e)

# --- Command: /clearthala (admin only) ---
async def clear_thala(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat = update.message.chat
    user = update.message.from_user

    # Only admins can use
    member = await chat.get_member(user.id)
    if not (member.status in ["administrator", "creator"]):
        await update.message.reply_text("You’re not an admin.")
        return

    await update.message.reply_text("Clearing all 'thala' messages...")

    async for msg in chat.get_history(limit=500):
        if msg.text and "thala" in msg.text.lower():
            try:
                await msg.delete()
            except Exception:
                pass

    await update.message.reply_text("All 'thala' messages removed ✅")

# --- Start command ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot active — any 'thala' message will be deleted automatically.")

# --- Main ---
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clearthala", clear_thala))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, catch_thala))

    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()

