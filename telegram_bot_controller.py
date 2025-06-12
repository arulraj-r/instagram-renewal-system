import os
import json
import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackContext, CallbackQueryHandler
from datetime import datetime, timedelta

# ----------- SETUP LOGGING ----------- #
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------- FILE PATHS ----------- #
CONFIG_PATH = "scheduler/config.json"
CAPTIONS_PATH = "scheduler/captions.json"
PAUSED_PATH = "scheduler/paused.json"
EXPIRY_PATH = "scheduler/token_expiry.json"

# ----------- HELPERS ----------- #
def ensure_file(file_path, default):
    if not os.path.exists(file_path):
        with open(file_path, 'w') as f:
            json.dump(default, f, indent=2)

def load_json(path):
    ensure_file(path, {})
    with open(path, 'r') as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

# ----------- TELEGRAM HANDLERS ----------- #
def start(update: Update, context: CallbackContext):
    accounts = ["inkwisps", "ink_wisps", "eclipsed_by_you"]
    buttons = [[InlineKeyboardButton(acc, callback_data=f"account:{acc}")] for acc in accounts]
    reply_markup = InlineKeyboardMarkup(buttons)
    update.message.reply_text("Choose an account:", reply_markup=reply_markup)

def handle_account_selection(update: Update, context: CallbackContext):
    query = update.callback_query
    account = query.data.split(":")[1]
    context.user_data['account'] = account
    buttons = [
        [InlineKeyboardButton("📆 Schedule Posts", callback_data="schedule")],
        [InlineKeyboardButton("🔑 Update API Key", callback_data="update_token")],
        [InlineKeyboardButton("✏️ Set Static Caption", callback_data="caption")],
        [InlineKeyboardButton("⏸️ Pause/Resume", callback_data="pause")],
        [InlineKeyboardButton("📊 Status Summary", callback_data="status")],
        [InlineKeyboardButton("♻ Reset Schedule", callback_data="reset")]
    ]
    query.message.reply_text(f"Manage: {account}", reply_markup=InlineKeyboardMarkup(buttons))

def handle_schedule(update: Update, context: CallbackContext):
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    buttons = [[InlineKeyboardButton(day, callback_data=f"weekday:{day}")] for day in weekdays]
    update.callback_query.message.reply_text("Select a weekday:", reply_markup=InlineKeyboardMarkup(buttons))

def handle_weekday(update: Update, context: CallbackContext):
    query = update.callback_query
    weekday = query.data.split(":")[1]
    account = context.user_data['account']
    context.user_data['weekday'] = weekday
    query.message.reply_text(f"How many posts do you want on {weekday}?")
    context.user_data['next_action'] = 'post_count'

def handle_message(update: Update, context: CallbackContext):
    text = update.message.text
    account = context.user_data.get('account')
    weekday = context.user_data.get('weekday')

    if context.user_data.get('next_action') == 'post_count':
        try:
            count = int(text)
            context.user_data['post_count'] = count
            update.message.reply_text(f"Enter {count} scheduled times (e.g. 10:30, 14:00, 18:00)")
            context.user_data['next_action'] = 'timeslot'
        except:
            update.message.reply_text("Invalid number. Enter post count again:")

    elif context.user_data.get('next_action') == 'timeslot':
        times = text.replace(" ", "").split(",")
        if len(times) != context.user_data.get('post_count'):
            update.message.reply_text(f"You entered {len(times)} times but expected {context.user_data['post_count']}.")
            return

        cfg = load_json(CONFIG_PATH)
        cfg.setdefault(account, {})[weekday] = times
        save_json(CONFIG_PATH, cfg)
        update.message.reply_text("✅ Schedule saved.")
        context.user_data.clear()

# Add other handlers...

# ----------- MAIN FUNCTION ----------- #
def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN missing")
        return

    updater = Updater(token)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CallbackQueryHandler(handle_account_selection, pattern="^account:"))
    dp.add_handler(CallbackQueryHandler(handle_schedule, pattern="^schedule$"))
    dp.add_handler(CallbackQueryHandler(handle_weekday, pattern="^weekday:"))
    dp.add_handler(MessageHandler(None, handle_message))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
