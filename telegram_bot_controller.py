# -*- coding: utf-8 -*-
# full_telegram_bot.py

import os
import json
import logging
import requests
import base64
import dropbox
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext
)
from nacl import encoding, public

# ----------- LOGGER SETUP ----------- #
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------- FILE PATHS ----------- #
SCHEDULER_DIR = "scheduler"
CONFIG_PATH = os.path.join(SCHEDULER_DIR, "config.json")
CAPTIONS_PATH = os.path.join(SCHEDULER_DIR, "captions.json")
PAUSED_PATH = os.path.join(SCHEDULER_DIR, "paused.json")
EXPIRY_PATH = os.path.join(SCHEDULER_DIR, "token_expiry.json")
RESULTS_PATH = os.path.join(SCHEDULER_DIR, "post_results.json")
USERS_PATH = os.path.join(SCHEDULER_DIR, "users.json")
BANNED_PATH = os.path.join(SCHEDULER_DIR, "banned.json")

# ----------- GLOBAL STATE ----------- #
USER_STATE = {}  # Holds password prompts etc.
AUTHORIZED_USERS = set()
GITHUB_SECRET_NAME = "TELEGRAM_BOT_PASSWORD"

# ----------- UTILS ----------- #
def ensure_file(path, default):
    if not os.path.exists(path):
        with open(path, 'w') as f:
            json.dump(default, f, indent=2)

def load_json(path):
    ensure_file(path, {})
    with open(path, 'r') as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    push_file_to_github(os.path.basename(path))

def push_file_to_github(filename):
    try:
        gh_token = os.getenv("GH_PAT")
        repo = os.getenv("GITHUB_REPOSITORY")
        if not gh_token or not repo:
            return

        path = f"{SCHEDULER_DIR}/{filename}"
        with open(path, "rb") as f:
            content = base64.b64encode(f.read()).decode()

        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        headers = {"Authorization": f"token {gh_token}", "Accept": "application/vnd.github+json"}
        sha = get_file_sha(url, headers)
        payload = {
            "message": f"Update {filename} via bot",
            "content": content,
            "branch": "main"
        }
        if sha:
            payload["sha"] = sha

        r = requests.put(url, headers=headers, json=payload)
        logger.info(f"Pushed {filename}: {r.status_code}")
    except Exception as e:
        logger.error(f"GitHub sync failed for {filename}: {e}")

def get_file_sha(url, headers):
    try:
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            return res.json().get("sha")
    except:
        pass
    return None

# ----------- SECURITY ----------- #
def is_banned(user_id):
    banned = load_json(BANNED_PATH)
    return str(user_id) in banned

def ban_user(user_id):
    banned = load_json(BANNED_PATH)
    banned[str(user_id)] = True
    save_json(BANNED_PATH, banned)

def is_authenticated(user_id):
    users = load_json(USERS_PATH)
    return str(user_id) in users

def validate_password(user_id, password):
    users = load_json(USERS_PATH)
    return users.get(str(user_id)) == password

def add_user(user_id, password):
    users = load_json(USERS_PATH)
    users[str(user_id)] = password
    save_json(USERS_PATH, users)

# ----------- TELEGRAM HANDLERS ----------- #
def start(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    if is_banned(user_id):
        update.message.reply_text("🚫 You are banned.")
        return

    USER_STATE[user_id] = 'awaiting_password'
    update.message.reply_text("🔐 Enter your password to access the bot:")

def handle_password(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    if USER_STATE.get(user_id) != 'awaiting_password':
        return

    text = update.message.text.strip()
    if validate_password(user_id, text):
        AUTHORIZED_USERS.add(user_id)
        del USER_STATE[user_id]
        update.message.reply_text("✅ Login successful.")
        show_main_menu(update, context)
    else:
        update.message.reply_text("❌ Wrong password.")
        ban_user(user_id)

# ----------- MAIN MENU ----------- #
def show_main_menu(update: Update, context: CallbackContext):
    accounts = ["inkwisps", "ink_wisps", "eclipsed_by_you"]
    buttons = [[InlineKeyboardButton(acc, callback_data=f"account:{acc}")] for acc in accounts]
    update.message.reply_text("📂 Select Instagram account:", reply_markup=InlineKeyboardMarkup(buttons))

# ----------- ENTRY POINT ----------- #
def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN is not set")
        return

    os.makedirs(SCHEDULER_DIR, exist_ok=True)
    updater = Updater(token)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_password))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
