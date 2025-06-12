# telegram_bot_controller.py

import os
import json
import logging
import requests
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater, CommandHandler, CallbackContext, CallbackQueryHandler,
    MessageHandler, Filters
)
from nacl import encoding, public  # for GitHub secret encryption

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

def update_github_secret(secret_name, secret_value):
    try:
        github_token = os.getenv("GH_PAT")
        repo = os.getenv("GITHUB_REPOSITORY")
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github+json"
        }
        key_url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
        key_resp = requests.get(key_url, headers=headers).json()

        public_key = public.PublicKey(key_resp["key"].encode("utf-8"), encoding.Base64Encoder())
        sealed_box = public.SealedBox(public_key)
        encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
        encrypted_value = encoding.Base64Encoder().encode(encrypted).decode("utf-8")

        update_url = f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}"
        payload = {
            "encrypted_value": encrypted_value,
            "key_id": key_resp["key_id"]
        }
        r = requests.put(update_url, headers=headers, json=payload)
        return r.status_code in [201, 204]
    except Exception as e:
        logger.error(f"GitHub secret update error: {e}")
        return False

# ----------- TELEGRAM BOT STATE ----------- #
USER_STATE = {}

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
    context.user_data['weekday'] = weekday
    context.user_data['next_action'] = 'post_count'
    query.message.reply_text(f"How many posts to schedule for {weekday}?")

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
            update.message.reply_text("❌ Invalid number. Try again:")

    elif context.user_data.get('next_action') == 'timeslot':
        times = text.replace(" ", "").split(",")
        if len(times) != context.user_data.get('post_count'):
            update.message.reply_text(f"❌ Expected {context.user_data['post_count']} times. Try again.")
            return
        cfg = load_json(CONFIG_PATH)
        cfg.setdefault(account, {})[weekday] = times
        save_json(CONFIG_PATH, cfg)
        update.message.reply_text("✅ Schedule saved.")
        context.user_data.clear()

    elif context.user_data.get('next_action') == 'caption':
        captions = load_json(CAPTIONS_PATH)
        captions[account] = text
        save_json(CAPTIONS_PATH, captions)
        update.message.reply_text("✅ Static caption saved.")
        context.user_data.clear()

    elif context.user_data.get('next_action') == 'update_token':
        secret_name = context.user_data.get('secret_target')
        success = update_github_secret(secret_name, text)
        if success:
            update.message.reply_text("✅ Secret updated.")
        else:
            update.message.reply_text("❌ Failed to update secret.")
        context.user_data.clear()

def handle_caption(update: Update, context: CallbackContext):
    context.user_data['next_action'] = 'caption'
    update.callback_query.message.reply_text("Send your new static caption:")

def handle_update_token(update: Update, context: CallbackContext):
    buttons = [
        [InlineKeyboardButton("Instagram Token", callback_data="token:IG")],
        [InlineKeyboardButton("Dropbox Token", callback_data="token:DB")]
    ]
    update.callback_query.message.reply_text("Which token to update?", reply_markup=InlineKeyboardMarkup(buttons))

def handle_token_choice(update: Update, context: CallbackContext):
    token_type = update.callback_query.data.split(":")[1]
    account = context.user_data.get('account')
    if token_type == "IG":
        secret_name = f"IG_{account.upper()}_TOKEN"
    else:
        secret_name = f"DROPBOX_{account.upper()}_TOKEN"
    context.user_data['secret_target'] = secret_name
    context.user_data['next_action'] = 'update_token'
    update.callback_query.message.reply_text(f"Send new value for {secret_name}:")

def handle_pause(update: Update, context: CallbackContext):
    account = context.user_data['account']
    paused = load_json(PAUSED_PATH)
    paused[account] = not paused.get(account, False)
    save_json(PAUSED_PATH, paused)
    state = "⏸️ Paused" if paused[account] else "▶️ Resumed"
    update.callback_query.message.reply_text(f"{account} is now {state}")

def handle_status(update: Update, context: CallbackContext):
    account = context.user_data['account']
    cfg = load_json(CONFIG_PATH)
    exp = load_json(EXPIRY_PATH)
    caption = load_json(CAPTIONS_PATH)
    paused = load_json(PAUSED_PATH)

    status = f"📊 *Status for {account}*\n"
    status += f"Paused: {'✅ Yes' if paused.get(account) else '❌ No'}\n"
    status += f"Caption: {caption.get(account, 'None')}\n"
    status += f"Token expires: {exp.get(account, 'Unknown')}\n"
    schedule = cfg.get(account, {})
    for day, times in schedule.items():
        status += f"{day}: {', '.join(times)}\n"

    update.callback_query.message.reply_text(status, parse_mode='Markdown')

def handle_reset(update: Update, context: CallbackContext):
    account = context.user_data['account']
    cfg = load_json(CONFIG_PATH)
    cfg[account] = {}
    save_json(CONFIG_PATH, cfg)
    update.callback_query.message.reply_text(f"♻ Schedule for {account} cleared.")

# ----------- MAIN ----------- #
def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN not set")
        return

    updater = Updater(token)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CallbackQueryHandler(handle_account_selection, pattern="^account:"))
    dp.add_handler(CallbackQueryHandler(handle_schedule, pattern="^schedule$"))
    dp.add_handler(CallbackQueryHandler(handle_weekday, pattern="^weekday:"))
    dp.add_handler(CallbackQueryHandler(handle_caption, pattern="^caption$"))
    dp.add_handler(CallbackQueryHandler(handle_update_token, pattern="^update_token$"))
    dp.add_handler(CallbackQueryHandler(handle_pause, pattern="^pause$"))
    dp.add_handler(CallbackQueryHandler(handle_status, pattern="^status$"))
    dp.add_handler(CallbackQueryHandler(handle_reset, pattern="^reset$"))
    dp.add_handler(CallbackQueryHandler(handle_token_choice, pattern="^token:"))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
