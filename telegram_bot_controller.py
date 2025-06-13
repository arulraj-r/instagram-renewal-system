# -*- coding: utf-8 -*-
# telegram_bot_controller.py

import os
import json
import logging
import requests
import dropbox
import base64
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import (
    Updater, CommandHandler, CallbackContext, CallbackQueryHandler,
    MessageHandler, Filters
)
from nacl import encoding, public  # for GitHub secret encryption
import asyncio

# ----------- SETUP LOGGING ----------- #
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------- FILE PATHS ----------- #
SCHEDULER_DIR = "scheduler"
CONFIG_PATH = os.path.join(SCHEDULER_DIR, "config.json")
CAPTIONS_PATH = os.path.join(SCHEDULER_DIR, "captions.json")
PAUSED_PATH = os.path.join(SCHEDULER_DIR, "paused.json")
EXPIRY_PATH = os.path.join(SCHEDULER_DIR, "token_expiry.json")
RESULTS_PATH = os.path.join(SCHEDULER_DIR, "post_results.json")
BANNED_PATH = os.path.join(SCHEDULER_DIR, "banned.json")
MESSAGE_DELETE_DELAY = 1800  # 30 minutes in seconds
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "bot_logs.json")

# ----------- SECURITY SETTINGS ----------- #
GITHUB_SECRET_NAME = "TELEGRAM_BOT_PASSWORD"
AUTHORIZED_USERS = {}
USER_STATE = {}

# ----------- FILE UTILITIES ----------- #
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
    push_scheduler_file_to_github(os.path.basename(path))

# ----------- SYNC TO GITHUB ----------- #
def push_scheduler_file_to_github(file_name):
    try:
        github_token = os.getenv("GH_PAT")
        repo = os.getenv("GITHUB_REPOSITORY")
        file_path = f"{SCHEDULER_DIR}/{file_name}"
        url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
        
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github+json"
        }

        with open(file_path, "rb") as f:
            content = base64.b64encode(f.read()).decode("utf-8")

        sha = get_existing_file_sha(url, headers)
        data = {
            "message": f"Update {file_path} via Telegram bot",
            "content": content,
            "branch": "main"
        }
        if sha:
            data["sha"] = sha

        res = requests.put(url, headers=headers, json=data)
        if res.status_code in [200, 201]:
            logger.info(f"Successfully pushed {file_name} to GitHub")
            return True
        else:
            logger.error(f"GitHub push failed for {file_name}: {res.text}")
            return False
    except Exception as e:
        logger.error(f"Error pushing to GitHub: {str(e)}")
        return False

def get_existing_file_sha(url, headers):
    try:
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            return res.json().get("sha")
        return None
    except Exception as e:
        logger.error(f"Error getting file SHA: {str(e)}")
        return None

# ----------- SECURITY HELPERS ----------- #
def is_banned(user_id):
    banned = load_json(BANNED_PATH)
    return str(user_id) in banned

def ban_user(user_id):
    banned = load_json(BANNED_PATH)
    if str(user_id) not in banned:
        banned.append(str(user_id))
        save_json(BANNED_PATH, banned)

def is_authorized(user_id):
    return str(user_id) in AUTHORIZED_USERS

def require_auth(func):
    def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
        try:
            user_id = update.effective_user.id
            if not is_authorized(user_id):
                if update.callback_query:
                    update.callback_query.message.reply_text("🔐 Please /start and login first.")
                else:
                    update.message.reply_text("🔐 Please /start and login first.")
                return
            return func(update, context, *args, **kwargs)
        except Exception as e:
            logger.error(f"Error in require_auth wrapper: {str(e)}")
            if update.callback_query:
                update.callback_query.message.reply_text("❌ An error occurred. Please try again.")
            else:
                update.message.reply_text("❌ An error occurred. Please try again.")
    return wrapper

# ----------- DROPBOX HELPERS ----------- #
def get_dropbox_access_token(account):
    """Get Dropbox access token with better error handling and debug logging."""
    # Map account names to their exact secret names
    account_secrets = {
        "inkwisps": {
            "app_key": "DROPBOX_INKWISPS_APP_KEY",
            "app_secret": "DROPBOX_INKWISPS_APP_SECRET",
            "refresh": "DROPBOX_INKWISPS_REFRESH",
            "token": "DROPBOX_INKWISPS_TOKEN"
        },
        "ink_wisps": {
            "app_key": "DROPBOX_INK_WISPS_APP_KEY",
            "app_secret": "DROPBOX_INK_WISPS_APP_SECRET",
            "refresh": "DROPBOX_INK_WISPS_REFRESH",
            "token": "DROPBOX_INK_WISPS_TOKEN"
        },
        "eclipsed_by_you": {
            "app_key": "DROPBOX_ECLIPSED_BY_YOU_APP_KEY",
            "app_secret": "DROPBOX_ECLIPSED_BY_YOU_APP_SECRET",
            "refresh": "DROPBOX_ECLIPSED_BY_YOU_REFRESH",
            "token": "DROPBOX_ECLIPSED_BY_YOU_TOKEN"
        }
    }

    if account not in account_secrets:
        logger.error(f"Unknown account: {account}")
        return None

    secrets = account_secrets[account]
    app_key = os.getenv(secrets["app_key"])
    app_secret = os.getenv(secrets["app_secret"])
    refresh_token = os.getenv(secrets["refresh"])

    # Debug logging
    logger.info(f"Checking Dropbox credentials for {account}")
    logger.debug(f"{secrets['app_key']} exists: {bool(app_key)}")
    logger.debug(f"{secrets['app_secret']} exists: {bool(app_secret)}")
    logger.debug(f"{secrets['refresh']} exists: {bool(refresh_token)}")

    missing_creds = []
    if not app_key:
        missing_creds.append(secrets["app_key"])
    if not app_secret:
        missing_creds.append(secrets["app_secret"])
    if not refresh_token:
        missing_creds.append(secrets["refresh"])

    if missing_creds:
        logger.error(f"Missing Dropbox credentials for {account}: {', '.join(missing_creds)}")
        return None

    try:
        response = requests.post(
            "https://api.dropbox.com/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": app_key,
                "client_secret": app_secret
            }
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if token:
            logger.info(f"Successfully obtained Dropbox token for {account}")
            return token
        else:
            logger.error(f"No access token in response for {account}")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Dropbox token refresh error for {account}: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting Dropbox token for {account}: {str(e)}")
        return None

def get_dropbox_client(account):
    token = get_dropbox_access_token(account)
    if not token:
        logger.error(f"Dropbox access token failed for {account}")
        return None
    return dropbox.Dropbox(oauth2_access_token=token)

def count_files_recursive(dbx, folder):
    count = 0
    try:
        result = dbx.files_list_folder(folder, recursive=True)
        while True:
            for entry in result.entries:
                if isinstance(entry, dropbox.files.FileMetadata):
                    count += 1
            if not result.has_more:
                break
            result = dbx.files_list_folder_continue(result.cursor)
    except Exception as e:
        logger.error(f"Error listing Dropbox files: {e}")
    return count

def get_remaining_files(account):
    try:
        dbx = get_dropbox_client(account)
        if not dbx:
            return 0
        
        # Count files in the main account folder and all subfolders
        main_folder = f"/{account}"
        count = count_files_recursive(dbx, main_folder)
        
        logger.info(f"Found {count} files in {main_folder} and subfolders")
        return count
    except Exception as e:
        logger.error(f"Dropbox error for {account}: {str(e)}")
        return 0

def check_low_files(account, context):
    try:
        count = get_remaining_files(account)
        if count < 5:
            message = f"⚠️ Only {count} files remaining in /{account} Dropbox folder"
            context.bot.send_message(chat_id=os.getenv("TELEGRAM_CHAT_ID"), text=message)
        return count
    except Exception as e:
        logger.error(f"Error checking low files for {account}: {str(e)}")
        return 0

# ----------- TOKEN EXPIRY HELPERS ----------- #
def update_token_expiry(account, expiry_date):
    exp = load_json(EXPIRY_PATH)
    exp[account] = expiry_date
    save_json(EXPIRY_PATH, exp)

def check_token_expiry(account, context):
    exp = load_json(EXPIRY_PATH)
    expiry = exp.get(account)
    if not expiry:
        return
    
    expiry_date = datetime.strptime(expiry, "%Y-%m-%d")
    days_left = (expiry_date - datetime.now()).days
    
    if days_left <= 5:
        message = f"⚠️ Instagram token for {account} expires in {days_left} days"
        context.bot.send_message(chat_id=os.getenv("TELEGRAM_CHAT_ID"), text=message)

# ----------- POST RESULT TRACKING ----------- #
def save_post_result(account, filename, success, error=None):
    results = load_json(RESULTS_PATH)
    results[account] = {
        "last_post": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "filename": filename,
        "success": success,
        "error": error
    }
    save_json(RESULTS_PATH, results)

# ----------- TELEGRAM HANDLERS ----------- #
def start(update: Update, context: CallbackContext):
    try:
        user_id = update.effective_user.id
        if is_banned(user_id):
            update.message.reply_text("🚫 Access denied.")
            return

        USER_STATE[user_id] = "awaiting_password"
        update.message.reply_text("🔐 Enter password to access bot:")
    except Exception as e:
        logger.error(f"Error in start handler: {str(e)}")
        update.message.reply_text("❌ An error occurred. Please try again.")

def send_audit_log(context, message):
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if chat_id:
        try:
            context.bot.send_message(chat_id=chat_id, text=f"📝 {message}")
            logger.info(f"Audit log sent: {message}")
        except Exception as e:
            logger.error(f"Failed to send audit log: {e}")

def handle_password(update: Update, context: CallbackContext):
    try:
        user_id = update.effective_user.id
        text = update.message.text.strip()
        
        if USER_STATE.get(user_id) != "awaiting_password":
            return

        # Get password from environment variable
        password = os.getenv(GITHUB_SECRET_NAME)
        logger.info(f"Checking password for user {user_id}")
        
        if not password:
            logger.error("TELEGRAM_BOT_PASSWORD not set in environment variables")
            update.message.reply_text(
                "❌ Bot configuration error: Password not set.\n"
                "Please contact the administrator."
            )
            return

        if text == password:
            AUTHORIZED_USERS[str(user_id)] = True
            del USER_STATE[user_id]
            logger.info(f"User {user_id} authenticated")
            send_audit_log(context, f"User {user_id} successfully logged in")

            accounts = ["inkwisps", "ink_wisps", "eclipsed_by_you"]
            status_text = "📊 Initial Status:\n\n"

            for account in accounts:
                try:
                    files = get_remaining_files(account)
                    status_text += f"{account}: {files} files in Dropbox\n"
                except Exception as e:
                    logger.error(f"Error for {account}: {e}")
                    status_text += f"{account}: error checking files\n"

            update.message.reply_text(status_text)
            show_accounts(update, context)
        else:
            logger.warning(f"Failed login for {user_id}")
            send_audit_log(context, f"Failed login attempt from user {user_id}")
            update.message.reply_text("❌ Incorrect password. Access denied.")
            ban_user(user_id)
    except Exception as e:
        logger.error(f"Error in handle_password: {str(e)}")
        update.message.reply_text("❌ An error occurred during login. Please try again.")

@require_auth
def show_accounts(update: Update, context: CallbackContext):
    try:
        accounts = ["inkwisps", "ink_wisps", "eclipsed_by_you"]
        buttons = [[InlineKeyboardButton(acc, callback_data=f"account:{acc}")] for acc in accounts]
        reply_markup = InlineKeyboardMarkup(buttons)
        
        if update.callback_query:
            update.callback_query.message.edit_text("Choose an account:", reply_markup=reply_markup)
        else:
            update.message.reply_text("Choose an account:", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in show_accounts: {str(e)}")
        if update.callback_query:
            update.callback_query.message.reply_text("❌ An error occurred. Please try again.")
        else:
            update.message.reply_text("❌ An error occurred. Please try again.")

def handle_back_to_accounts(update: Update, context: CallbackContext):
    query = update.callback_query
    accounts = ["inkwisps", "ink_wisps", "eclipsed_by_you"]
    buttons = [[InlineKeyboardButton(acc, callback_data=f"account:{acc}")] for acc in accounts]
    reply_markup = InlineKeyboardMarkup(buttons)
    query.message.edit_text("Choose an account:", reply_markup=reply_markup)

@require_auth
def handle_account_selection(update: Update, context: CallbackContext):
    try:
        query = update.callback_query
        if ":" not in query.data:
            logger.warning(f"Invalid callback data: {query.data}")
            return
            
        account = query.data.split(":")[1]
        context.user_data['account'] = account
        
        # Check token expiry on account selection
        check_token_expiry(account, context)
        
        buttons = [
            [InlineKeyboardButton("📆 Schedule Posts", callback_data="schedule")],
            [InlineKeyboardButton("📋 View Schedule", callback_data="view_schedule")],
            [InlineKeyboardButton("✏️ Set Static Caption", callback_data="caption")],
            [InlineKeyboardButton("🔑 Update API Key", callback_data="update_token")],
            [InlineKeyboardButton("⏸️ Pause/Resume", callback_data="pause")],
            [InlineKeyboardButton("📊 Status Summary", callback_data="status")],
            [InlineKeyboardButton("📤 Post Logs", callback_data="post_logs")],
            [InlineKeyboardButton("📝 View Bot Logs", callback_data="view_logs")],
            [InlineKeyboardButton("♻ Reset Schedule", callback_data="reset")],
            [InlineKeyboardButton("🔙 Back to Accounts", callback_data="back_to_accounts")]
        ]
        
        send_self_destructing_message(
            update,
            context,
            f"Manage: {account}",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logger.error(f"Error in handle_account_selection: {str(e)}")
        send_self_destructing_message(
            update,
            context,
            "❌ An error occurred. Please try again."
        )

def handle_view_schedule(update: Update, context: CallbackContext):
    query = update.callback_query
    account = context.user_data['account']
    cfg = load_json(CONFIG_PATH)
    
    # Create buttons for each day
    buttons = []
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        times = cfg.get(account, {}).get(day, [])
        if times:
            label = f"{day}: {', '.join(times)}"
        else:
            label = f"{day}: No posts"
        buttons.append([InlineKeyboardButton(label, callback_data=f"view_day:{day}")])
    
    # Add back button
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")])
    
    schedule_text = f"📅 Schedule for {account}\n\n"
    for day, times in cfg.get(account, {}).items():
        if times:
            schedule_text += f"{day}: {', '.join(times)}\n"
    
    query.message.edit_text(schedule_text, reply_markup=InlineKeyboardMarkup(buttons))

def handle_view_day(update: Update, context: CallbackContext):
    query = update.callback_query
    day = query.data.split(":")[1]
    account = context.user_data['account']
    cfg = load_json(CONFIG_PATH)
    
    times = cfg.get(account, {}).get(day, [])
    if times:
        text = f"📅 {day} Schedule for {account}:\n"
        for i, time in enumerate(times, 1):
            text += f"{i}. {time}\n"
        
        buttons = []
        for i, time in enumerate(times, 1):
            buttons.append([InlineKeyboardButton(f"Edit {time}", callback_data=f"edit_time:{day}:{time}")])
        buttons.append([InlineKeyboardButton("Add Time", callback_data=f"add_time:{day}")])
        buttons.append([InlineKeyboardButton("🔙 Back to Schedule", callback_data="view_schedule")])
        
        query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        buttons = [
            [InlineKeyboardButton("Add Time", callback_data=f"add_time:{day}")],
            [InlineKeyboardButton("🔙 Back to Schedule", callback_data="view_schedule")]
        ]
        query.message.edit_text(
            f"📅 No posts scheduled for {day}",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

def handle_post_logs(update: Update, context: CallbackContext):
    query = update.callback_query
    account = context.user_data['account']
    results = load_json(RESULTS_PATH)
    
    if account not in results:
        text = f"📤 No post logs available for {account}"
    else:
        last_post = results[account]
        text = f"📤 Last Post for {account}:\n"
        text += f"Time: {last_post['last_post']}\n"
        text += f"File: {last_post['filename']}\n"
        text += f"Status: {'✅ Success' if last_post['success'] else '❌ Failed'}\n"
        if not last_post['success'] and last_post.get('error'):
            text += f"Error: {last_post['error']}\n"
    
    buttons = [[InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]]
    query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

def handle_reset(update: Update, context: CallbackContext):
    query = update.callback_query
    account = context.user_data['account']
    
    buttons = [
        [InlineKeyboardButton("✅ Yes, Reset Schedule", callback_data="confirm_reset")],
        [InlineKeyboardButton("❌ No, Cancel", callback_data="back_to_menu")]
    ]
    query.message.edit_text(
        f"⚠️ Are you sure you want to reset the schedule for {account}?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

def handle_confirm_reset(update: Update, context: CallbackContext):
    query = update.callback_query
    account = context.user_data['account']
    cfg = load_json(CONFIG_PATH)
    cfg[account] = {}
    save_json(CONFIG_PATH, cfg)
    
    buttons = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    query.message.edit_text(
        f"✅ Schedule for {account} has been reset.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

def handle_schedule(update: Update, context: CallbackContext):
    """Show schedule options."""
    try:
        weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        buttons = [[InlineKeyboardButton(day, callback_data=f"weekday:{day}")] for day in weekdays]
        buttons.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
        reply_markup = InlineKeyboardMarkup(buttons)
        
        if update.callback_query:
            update.callback_query.message.edit_text(
                "Select a weekday to schedule posts:",
                reply_markup=reply_markup
            )
        else:
            update.message.reply_text(
                "Select a weekday to schedule posts:",
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error in handle_schedule: {str(e)}")
        if update.callback_query:
            update.callback_query.message.reply_text("❌ An error occurred. Please try again.")
        else:
            update.message.reply_text("❌ An error occurred. Please try again.")

def handle_weekday(update: Update, context: CallbackContext):
    """Handle weekday selection for scheduling."""
    try:
        query = update.callback_query
        weekday = query.data.split(":")[1]
        context.user_data['weekday'] = weekday
        context.user_data['next_action'] = 'post_count'
        
        # Add back button
        buttons = [
            [InlineKeyboardButton("🔙 Back to Schedule", callback_data="schedule")]
        ]
        reply_markup = InlineKeyboardMarkup(buttons)
        
        query.message.edit_text(
            f"How many posts to schedule for {weekday}?",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in handle_weekday: {str(e)}")
        query.message.reply_text("❌ An error occurred. Please try again.")

def handle_time_selection(update: Update, context: CallbackContext):
    """Handle time slot selection."""
    try:
        query = update.callback_query
        time = query.data.split(":")[1]
        
        if time == "done":
            if not context.user_data.get('selected_times'):
                query.message.reply_text("❌ Please select at least one time slot.")
                return
                
            account = context.user_data['account']
            weekday = context.user_data['weekday']
            cfg = load_json(CONFIG_PATH)
            cfg.setdefault(account, {})[weekday] = sorted(context.user_data['selected_times'])
            save_json(CONFIG_PATH, cfg)
            
            # Show success message with back button
            buttons = [
                [InlineKeyboardButton("🔙 Back to Schedule", callback_data="schedule")],
                [InlineKeyboardButton("📋 View Schedule", callback_data="view_schedule")]
            ]
            reply_markup = InlineKeyboardMarkup(buttons)
            
            query.message.edit_text(
                f"✅ Schedule saved for {weekday}:\n{', '.join(sorted(context.user_data['selected_times']))}",
                reply_markup=reply_markup
            )
            context.user_data.clear()
            return
            
        elif time == "clear":
            context.user_data['selected_times'] = []
            query.message.edit_text(
                "Select time slots (15-minute intervals):",
                reply_markup=create_time_button_grid([], context.user_data['post_count'])
            )
            return
        
        selected_times = context.user_data.get('selected_times', [])
        max_slots = context.user_data['post_count']
        
        if time in selected_times:
            selected_times.remove(time)
        elif len(selected_times) < max_slots:
            selected_times.append(time)
        else:
            query.answer("Maximum number of slots reached!")
            return
        
        context.user_data['selected_times'] = selected_times
        query.message.edit_text(
            f"Select time slots ({len(selected_times)}/{max_slots} selected):",
            reply_markup=create_time_button_grid(selected_times, max_slots)
        )
    except Exception as e:
        logger.error(f"Error in handle_time_selection: {str(e)}")
        query.message.reply_text("❌ An error occurred. Please try again.")

def handle_message(update: Update, context: CallbackContext):
    """Handle incoming messages."""
    try:
        text = update.message.text
        account = context.user_data.get('account')
        next_action = context.user_data.get('next_action')
        
        logger.info(f"[DEBUG] next_action = {next_action}")
        
        if next_action == 'update_token':
            secret_name = context.user_data.get('secret_target')
            token_type = context.user_data.get('token_type')
            token_value = text.strip()
            
            if not token_value or len(token_value) < 10:
                update.message.reply_text("❌ Invalid token. Please send a valid token.")
                return
            
            logger.info(f"[TOKEN UPDATE] Updating {secret_name}...")
            send_audit_log(context, f"User {update.effective_user.id} attempting to update {token_type} for {account}")
            
            if update_github_secret(secret_name, token_value):
                update.message.reply_text(
                    f"✅ {token_type} token updated successfully.\n\n"
                    "📅 Now enter the *expiry date* (format: YYYY-MM-DD):",
                    parse_mode="Markdown"
                )
                context.user_data['next_action'] = 'token_expiry'
                send_audit_log(context, f"User {update.effective_user.id} successfully updated {token_type} for {account}")
            else:
                update.message.reply_text(
                    "❌ Failed to update the token. Please try again or check your GitHub access."
                )
                context.user_data.clear()
                send_audit_log(context, f"User {update.effective_user.id} failed to update {token_type} for {account}")

        elif next_action == 'token_expiry':
            try:
                expiry_date = datetime.strptime(text.strip(), "%Y-%m-%d")
                if expiry_date < datetime.now():
                    update.message.reply_text("⚠️ Expiry date can't be in the past. Try again:")
                    return
                    
                update_token_expiry(account, expiry_date.strftime("%Y-%m-%d"))
                update.message.reply_text(
                    "✅ Token expiry date saved and linked to token.\n"
                    f"Token will expire on {expiry_date.strftime('%Y-%m-%d')}"
                )
                logger.info(f"[TOKEN EXPIRY] Set expiry for {account} to {expiry_date}")
                send_audit_log(context, f"User {update.effective_user.id} set token expiry for {account} to {expiry_date}")
                context.user_data.clear()
            except ValueError:
                update.message.reply_text("❌ Invalid date format. Use YYYY-MM-DD (e.g. 2025-12-31).")

        elif next_action == 'caption':
            if not text or len(text.strip()) < 5:
                update.message.reply_text("❌ Caption too short. Please send a longer caption.")
                return
                
            captions = load_json(CAPTIONS_PATH)
            captions[account] = text
            save_json(CAPTIONS_PATH, captions)
            send_audit_log(context, f"User {update.effective_user.id} updated caption for {account}")
            update.message.reply_text("✅ Static caption saved.")
            context.user_data.clear()

        elif next_action == 'post_count':
            try:
                count = int(text)
                if count < 1 or count > 24:
                    update.message.reply_text("❌ Please enter a number between 1 and 24.")
                    return
                context.user_data['post_count'] = count
                context.user_data['selected_times'] = []
                update.message.reply_text(
                    f"Select time slots for {weekday} ({count} posts):",
                    reply_markup=create_time_button_grid([], count)
                )
                context.user_data['next_action'] = 'timeslot'
            except ValueError:
                update.message.reply_text("❌ Invalid number. Please enter a number between 1 and 24.")

        elif next_action == 'add_user':
            try:
                new_user_id = int(text)
                if str(new_user_id) in AUTHORIZED_USERS:
                    update.message.reply_text("❌ This user already exists.")
                    return
                context.user_data['new_user_id'] = new_user_id
                context.user_data['next_action'] = 'add_user_password'
                update.message.reply_text("Please send the new user's password:")
            except ValueError:
                update.message.reply_text("❌ Invalid user ID. Please send a numeric ID.")

        elif next_action == 'add_user_password':
            new_user_id = context.user_data['new_user_id']
            if add_user(new_user_id, text):
                send_audit_log(context, f"User {update.effective_user.id} added new user {new_user_id}")
                update.message.reply_text(f"✅ User {new_user_id} added successfully.")
            else:
                update.message.reply_text("❌ Failed to add user. Please try again.")
            context.user_data.clear()

        elif next_action == 'change_password':
            if change_user_password(update.effective_user.id, text):
                send_audit_log(context, f"User {update.effective_user.id} changed their password")
                update.message.reply_text("✅ Password changed successfully.")
            else:
                update.message.reply_text("❌ Failed to change password. Please try again.")
            context.user_data.clear()

    except Exception as e:
        logger.error(f"Error in handle_message: {str(e)}")
        update.message.reply_text("❌ An error occurred. Please try again.")

def handle_caption(update: Update, context: CallbackContext):
    try:
        account = context.user_data['account']
        context.user_data['next_action'] = 'caption'
        send_audit_log(context, f"User {update.effective_user.id} started editing caption for {account}")
        update.callback_query.message.reply_text("Send your new static caption:")
    except Exception as e:
        logger.error(f"Error in handle_caption: {str(e)}")
        update.callback_query.message.reply_text("❌ An error occurred. Please try again.")

def handle_update_token(update: Update, context: CallbackContext):
    """Handle token update selection."""
    try:
        account = context.user_data['account']
        send_audit_log(context, f"User {update.effective_user.id} started updating token for {account}")
        
        buttons = [
            [InlineKeyboardButton("Instagram Token", callback_data="token:IG")],
            [InlineKeyboardButton("Dropbox App Key", callback_data="token:DB_APP_KEY")],
            [InlineKeyboardButton("Dropbox App Secret", callback_data="token:DB_APP_SECRET")],
            [InlineKeyboardButton("Dropbox Refresh Token", callback_data="token:DB_REFRESH")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
        ]
        
        send_self_destructing_message(
            update,
            context,
            f"Which token to update for {account}?",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logger.error(f"Error in handle_update_token: {str(e)}")
        send_self_destructing_message(
            update,
            context,
            "❌ An error occurred. Please try again."
        )

def handle_token_choice(update: Update, context: CallbackContext):
    """Handle token type selection."""
    try:
        query = update.callback_query
        token_type = query.data.split(":")[1]
        account = context.user_data.get('account')
        
        # Map token types to their secret names
        token_mapping = {
            "IG": {
                "secret": f"IG_{account.upper()}_TOKEN",
                "display": "Instagram",
                "prompt": "📥 Please send your new *Instagram* token now.\n\n"
            },
            "DB_APP_KEY": {
                "secret": f"DROPBOX_{account.upper()}_APP_KEY",
                "display": "Dropbox App Key",
                "prompt": "📥 Please send your new *Dropbox App Key* now.\n\n"
            },
            "DB_APP_SECRET": {
                "secret": f"DROPBOX_{account.upper()}_APP_SECRET",
                "display": "Dropbox App Secret",
                "prompt": "📥 Please send your new *Dropbox App Secret* now.\n\n"
            },
            "DB_REFRESH": {
                "secret": f"DROPBOX_{account.upper()}_REFRESH",
                "display": "Dropbox Refresh Token",
                "prompt": "📥 Please send your new *Dropbox Refresh Token* now.\n\n"
            }
        }
        
        if token_type not in token_mapping:
            logger.error(f"Invalid token type: {token_type}")
            query.message.reply_text("❌ Invalid token type selected.")
            return
            
        token_info = token_mapping[token_type]
        context.user_data['secret_target'] = token_info["secret"]
        context.user_data['token_type'] = token_info["display"]
        context.user_data['next_action'] = 'update_token'
        
        buttons = [
            [InlineKeyboardButton("🔙 Back", callback_data="update_token")]
        ]
        
        query.message.edit_text(
            f"{token_info['prompt']}"
            f"⚠️ This will update GitHub Secret: `{token_info['secret']}`",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
        
        # Log the action
        logger.info(f"User {update.effective_user.id} started updating {token_info['display']} for {account}")
        send_audit_log(context, f"User {update.effective_user.id} started updating {token_info['display']} for {account}")
        
    except Exception as e:
        logger.error(f"Error in handle_token_choice: {str(e)}")
        query.message.reply_text("❌ An error occurred. Please try again.")

def handle_token_confirm(update: Update, context: CallbackContext):
    """Handle token update confirmation."""
    try:
        query = update.callback_query
        secret_name = context.user_data.get('secret_target')
        token_type = context.user_data.get('token_type')
        temp_token = context.user_data.get('temp_token')
        
        if not temp_token:
            query.message.edit_text("❌ Token data lost. Please try again.")
            context.user_data.clear()
            return
            
        # Log the attempt
        logger.info(f"Attempting to update {token_type} for {context.user_data.get('account')}")
        
        success = update_github_secret(secret_name, temp_token)
        if success:
            query.message.edit_text(
                f"✅ {token_type} updated successfully.\n"
                "Now enter expiry date (YYYY-MM-DD):"
            )
            context.user_data['next_action'] = 'token_expiry'
        else:
            query.message.edit_text(
                f"❌ Failed to update {token_type}.\n"
                "Please check the logs and try again."
            )
            context.user_data.clear()
            
    except Exception as e:
        logger.error(f"Error in handle_token_confirm: {str(e)}")
        query.message.edit_text("❌ An error occurred. Please try again.")

def handle_edit_time(update: Update, context: CallbackContext):
    """Handle editing a specific time slot."""
    try:
        query = update.callback_query
        _, day, time = query.data.split(":")
        account = context.user_data['account']
        
        context.user_data['editing_day'] = day
        context.user_data['editing_time'] = time
        context.user_data['next_action'] = 'editing_time'
        
        buttons = [
            [InlineKeyboardButton("🔙 Back", callback_data=f"view_day:{day}")]
        ]
        
        query.message.edit_text(
            f"Enter new time for {day} (HH:MM format):",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logger.error(f"Error in handle_edit_time: {str(e)}")
        query.message.reply_text("❌ An error occurred. Please try again.")

def handle_add_time(update: Update, context: CallbackContext):
    """Handle adding a new time slot."""
    try:
        query = update.callback_query
        day = query.data.split(":")[1]
        account = context.user_data['account']
        
        context.user_data['adding_day'] = day
        context.user_data['next_action'] = 'adding_time'
        
        buttons = [
            [InlineKeyboardButton("🔙 Back", callback_data=f"view_day:{day}")]
        ]
        
        query.message.edit_text(
            f"Enter new time for {day} (HH:MM format):",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logger.error(f"Error in handle_add_time: {str(e)}")
        query.message.reply_text("❌ An error occurred. Please try again.")

def handle_pause(update: Update, context: CallbackContext):
    account = context.user_data['account']
    paused = load_json(PAUSED_PATH)
    paused[account] = not paused.get(account, False)
    save_json(PAUSED_PATH, paused)
    state = "⏸️ Paused" if paused[account] else "▶️ Resumed"
    update.callback_query.message.reply_text(f"{account} is now {state}")

def handle_status(update: Update, context: CallbackContext):
    """Show detailed status for an account."""
    try:
        account = context.user_data['account']
        cfg = load_json(CONFIG_PATH)
        exp = load_json(EXPIRY_PATH)
        caption = load_json(CAPTIONS_PATH)
        paused = load_json(PAUSED_PATH)
        results = load_json(RESULTS_PATH)

        # Get Dropbox file count with detailed logging
        remaining_files = get_remaining_files(account)
        logger.info(f"Status check for {account}: {remaining_files} files found")
        
        # Get next scheduled post time
        now = datetime.now()
        today = now.strftime("%A")
        next_post = None
        
        if today in cfg.get(account, {}):
            times = cfg[account][today]
            for time in times:
                post_time = datetime.strptime(time, "%H:%M").time()
                if post_time > now.time():
                    next_post = time
                    break
        
        if not next_post and today != "Sunday":
            tomorrow = (now + timedelta(days=1)).strftime("%A")
            if tomorrow in cfg.get(account, {}):
                next_post = f"Tomorrow at {cfg[account][tomorrow][0]}"

        status = f"📊 *Status for {account}*\n\n"
        status += f"📦 Dropbox Files: {remaining_files}\n"
        status += f"⏸️ Paused: {'✅ Yes' if paused.get(account) else '❌ No'}\n"
        
        # Show caption preview (first 50 chars)
        current_caption = caption.get(account, 'None')
        if current_caption != 'None':
            status += f"📝 Caption: {current_caption[:50]}...\n"
        else:
            status += f"📝 Caption: None\n"
            
        status += f"🔑 Token expires: {exp.get(account, 'Unknown')}\n"
        
        if next_post:
            status += f"⏰ Next post: {next_post}\n"
        
        if account in results:
            last_post = results[account]
            status += f"\n📤 Last Post:\n"
            status += f"Time: {last_post['last_post']}\n"
            status += f"File: {last_post['filename']}\n"
            status += f"Status: {'✅ Success' if last_post['success'] else '❌ Failed'}\n"
            if not last_post['success'] and last_post.get('error'):
                status += f"Error: {last_post['error']}\n"
        
        status += "\n📅 Schedule:\n"
        schedule = cfg.get(account, {})
        for day, times in schedule.items():
            if times:
                status += f"{day}: {', '.join(times)}\n"
            else:
                status += f"{day}: No posts\n"

        # Add Dropbox credentials status
        dbx_status = "✅" if get_dropbox_access_token(account) else "❌"
        status += f"\n📦 Dropbox Connection: {dbx_status}"

        update.callback_query.message.edit_text(status, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in handle_status: {str(e)}")
        update.callback_query.message.reply_text("❌ An error occurred while getting status.")

# ----------- TIME SLOT HELPERS ----------- #
def generate_time_slots():
    slots = []
    for hour in range(24):
        for minute in range(0, 60, 15):
            time_str = f"{hour:02d}:{minute:02d}"
            slots.append(time_str)
    return slots

def create_time_button_grid(selected_times, max_slots):
    """Create time selection grid with back button."""
    time_slots = generate_time_slots()
    buttons = []
    row = []
    
    for time in time_slots:
        # Add checkmark if time is selected
        label = f"✅ {time}" if time in selected_times else time
        row.append(InlineKeyboardButton(label, callback_data=f"time:{time}"))
        
        if len(row) == 4:  # 4 buttons per row
            buttons.append(row)
            row = []
    
    if row:  # Add any remaining buttons
        buttons.append(row)
    
    # Add control buttons
    control_row = []
    if selected_times:
        control_row.append(InlineKeyboardButton("✅ Done", callback_data="time:done"))
        control_row.append(InlineKeyboardButton("❌ Clear", callback_data="time:clear"))
    buttons.append(control_row)
    
    # Add back button
    buttons.append([InlineKeyboardButton("🔙 Back to Weekday", callback_data="schedule")])
    
    return InlineKeyboardMarkup(buttons)

# ----------- PERIODIC CHECKS ----------- #
def periodic_checks(context: CallbackContext):
    if not AUTHORIZED_USERS:
        logger.info("Skipping periodic check: no authorized users")
        return
        
    accounts = ["inkwisps", "ink_wisps", "eclipsed_by_you"]
    for account in accounts:
        try:
            check_token_expiry(account, context)
        except Exception as e:
            logger.error(f"Error during periodic check for {account}: {e}")

def handle_add_user(update: Update, context: CallbackContext):
    """Handle /add_user command."""
    try:
        # Only allow the first authorized user to add others
        first_user = next(iter(AUTHORIZED_USERS.keys()))
        if str(update.effective_user.id) != first_user:
            update.message.reply_text("❌ Only the admin can add new users.")
            return

        context.user_data['next_action'] = 'add_user'
        update.message.reply_text("Please send the new user's ID (numeric):")
    except Exception as e:
        logger.error(f"Error in handle_add_user: {str(e)}")
        update.message.reply_text("❌ An error occurred. Please try again.")

def handle_change_password(update: Update, context: CallbackContext):
    """Handle /change_password command."""
    try:
        context.user_data['next_action'] = 'change_password'
        update.message.reply_text("Please send your new password:")
    except Exception as e:
        logger.error(f"Error in handle_change_password: {str(e)}")
        update.message.reply_text("❌ An error occurred. Please try again.")

def update_github_secret(secret_name, secret_value):
    """Update a GitHub secret using the GitHub API."""
    try:
        repo = os.getenv("GITHUB_REPOSITORY")
        token = os.getenv("GH_PAT")
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json"
        }

        # Get the public key
        key_url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
        key_res = requests.get(key_url, headers=headers)
        
        if key_res.status_code != 200:
            logger.error(f"Failed to get public key: {key_res.text}")
            return False
            
        key_data = key_res.json()
        key_id = key_data.get("key_id")
        key = key_data.get("key")

        if not key_id or not key:
            logger.error("GitHub public key missing")
            return False

        # Encrypt the secret
        pub_key = public.PublicKey(key.encode(), encoding.Base64Encoder())
        sealed_box = public.SealedBox(pub_key)
        encrypted = sealed_box.encrypt(secret_value.encode())
        encrypted_value = encoding.Base64Encoder().encode(encrypted).decode()

        # Update the secret
        update_url = f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}"
        res = requests.put(update_url, headers=headers, json={
            "encrypted_value": encrypted_value,
            "key_id": key_id
        })

        if res.status_code in [201, 204]:
            logger.info(f"Successfully updated secret {secret_name}")
            return True
        else:
            logger.error(f"Failed to update secret {secret_name}: {res.text}")
            return False
            
    except Exception as e:
        logger.error(f"GitHub secret update failed: {e}")
        return False

def add_user(user_id, password):
    """Add a new authorized user."""
    try:
        AUTHORIZED_USERS[str(user_id)] = True
        # Update GitHub secret for the new user's password
        secret_name = f"USER_{user_id}_PASSWORD"
        success = update_github_secret(secret_name, password)
        if success:
            logger.info(f"Added new user {user_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error adding user {user_id}: {e}")
        return False

def change_user_password(user_id, new_password):
    """Change an existing user's password."""
    try:
        if str(user_id) not in AUTHORIZED_USERS:
            return False
        secret_name = f"USER_{user_id}_PASSWORD"
        success = update_github_secret(secret_name, new_password)
        if success:
            logger.info(f"Changed password for user {user_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error changing password for user {user_id}: {e}")
        return False

def handle_back_to_menu(update: Update, context: CallbackContext):
    """Handle back to menu navigation."""
    try:
        query = update.callback_query
        account = context.user_data.get('account')
        
        buttons = [
            [InlineKeyboardButton("📆 Schedule Posts", callback_data="schedule")],
            [InlineKeyboardButton("📋 View Schedule", callback_data="view_schedule")],
            [InlineKeyboardButton("✏️ Set Static Caption", callback_data="caption")],
            [InlineKeyboardButton("🔑 Update API Key", callback_data="update_token")],
            [InlineKeyboardButton("⏸️ Pause/Resume", callback_data="pause")],
            [InlineKeyboardButton("📊 Status Summary", callback_data="status")],
            [InlineKeyboardButton("📤 Post Logs", callback_data="post_logs")],
            [InlineKeyboardButton("📝 View Bot Logs", callback_data="view_logs")],
            [InlineKeyboardButton("♻ Reset Schedule", callback_data="reset")],
            [InlineKeyboardButton("🔙 Back to Accounts", callback_data="back_to_accounts")]
        ]
        
        query.message.edit_text(
            f"Manage: {account}",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logger.error(f"Error in handle_back_to_menu: {str(e)}")
        query.message.reply_text("❌ An error occurred. Please try again.")

def ensure_log_file():
    """Ensure log file exists."""
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w') as f:
            json.dump([], f)

def log_message(message_data):
    """Log message to file and GitHub."""
    try:
        ensure_log_file()
        with open(LOG_FILE, 'r') as f:
            logs = json.load(f)
        
        logs.append({
            "timestamp": datetime.now().isoformat(),
            "message_id": message_data.get("message_id"),
            "chat_id": message_data.get("chat_id"),
            "text": message_data.get("text"),
            "user_id": message_data.get("user_id"),
            "action": message_data.get("action")
        })
        
        with open(LOG_FILE, 'w') as f:
            json.dump(logs, f, indent=2)
            
        # Push to GitHub
        push_scheduler_file_to_github("logs/bot_logs.json")
    except Exception as e:
        logger.error(f"Error logging message: {str(e)}")

async def delete_message_later(context, chat_id, message_id):
    """Delete message after delay."""
    try:
        await asyncio.sleep(MESSAGE_DELETE_DELAY)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.error(f"Error deleting message: {str(e)}")

def send_self_destructing_message(update, context, text, reply_markup=None):
    """Send message that will self-destruct after delay."""
    try:
        if update.callback_query:
            message = update.callback_query.message.edit_text(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            message = update.message.reply_text(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
        # Log the message
        log_message({
            "message_id": message.message_id,
            "chat_id": message.chat_id,
            "text": text,
            "user_id": update.effective_user.id,
            "action": "message_sent"
        })
        
        # Schedule deletion
        asyncio.create_task(delete_message_later(context, message.chat_id, message.message_id))
        
        return message
    except Exception as e:
        logger.error(f"Error sending self-destructing message: {str(e)}")
        return None

def handle_view_logs(update: Update, context: CallbackContext):
    """Handle viewing bot logs."""
    try:
        ensure_log_file()
        with open(LOG_FILE, 'r') as f:
            logs = json.load(f)
            
        if not logs:
            send_self_destructing_message(
                update,
                context,
                "📝 No logs available."
            )
            return
            
        # Get last 10 logs
        recent_logs = logs[-10:]
        text = "📝 Recent Bot Activity:\n\n"
        
        for log in recent_logs:
            timestamp = datetime.fromisoformat(log["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
            text += f"🕒 {timestamp}\n"
            text += f"👤 User: {log['user_id']}\n"
            text += f"📝 Action: {log['action']}\n"
            if log.get("text"):
                text += f"💬 Message: {log['text'][:50]}...\n"
            text += "➖➖➖➖➖➖➖➖➖➖\n"
            
        buttons = [
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
        ]
        
        send_self_destructing_message(
            update,
            context,
            text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logger.error(f"Error viewing logs: {str(e)}")
        send_self_destructing_message(
            update,
            context,
            "❌ An error occurred while fetching logs."
        )

# ----------- MAIN ----------- #
def main():
    """Main function with environment variable validation."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN not set")
        return

    # Debug print for environment variables
    print("Environment Variables Check:")
    print("TELEGRAM_BOT_PASSWORD:", "Set" if os.getenv("TELEGRAM_BOT_PASSWORD") else "Not Set")
    print("TELEGRAM_CHAT_ID:", "Set" if os.getenv("TELEGRAM_CHAT_ID") else "Not Set")
    print("GH_PAT:", "Set" if os.getenv("GH_PAT") else "Not Set")
    
    # Check Dropbox credentials
    accounts = ["inkwisps", "ink_wisps", "eclipsed_by_you"]
    account_secrets = {
        "inkwisps": {
            "app_key": "DROPBOX_INKWISPS_APP_KEY",
            "app_secret": "DROPBOX_INKWISPS_APP_SECRET",
            "refresh": "DROPBOX_INKWISPS_REFRESH",
            "token": "DROPBOX_INKWISPS_TOKEN"
        },
        "ink_wisps": {
            "app_key": "DROPBOX_INK_WISPS_APP_KEY",
            "app_secret": "DROPBOX_INK_WISPS_APP_SECRET",
            "refresh": "DROPBOX_INK_WISPS_REFRESH",
            "token": "DROPBOX_INK_WISPS_TOKEN"
        },
        "eclipsed_by_you": {
            "app_key": "DROPBOX_ECLIPSED_BY_YOU_APP_KEY",
            "app_secret": "DROPBOX_ECLIPSED_BY_YOU_APP_SECRET",
            "refresh": "DROPBOX_ECLIPSED_BY_YOU_REFRESH",
            "token": "DROPBOX_ECLIPSED_BY_YOU_TOKEN"
        }
    }

    for account in accounts:
        secrets = account_secrets[account]
        print(f"\nDropbox credentials for {account}:")
        print(f"{secrets['app_key']}:", "Set" if os.getenv(secrets['app_key']) else "Not Set")
        print(f"{secrets['app_secret']}:", "Set" if os.getenv(secrets['app_secret']) else "Not Set")
        print(f"{secrets['refresh']}:", "Set" if os.getenv(secrets['refresh']) else "Not Set")
        print(f"{secrets['token']}:", "Set" if os.getenv(secrets['token']) else "Not Set")

    # Ensure scheduler directory exists
    os.makedirs(SCHEDULER_DIR, exist_ok=True)

    updater = Updater(token)
    dp = updater.dispatcher

    # Add periodic checks every 6 hours
    job_queue = updater.job_queue
    job_queue.run_repeating(periodic_checks, interval=21600, first=10)

    # Basic handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_password))

    # Protected handlers
    dp.add_handler(CallbackQueryHandler(handle_account_selection, pattern="^account:"))
    dp.add_handler(CallbackQueryHandler(handle_schedule, pattern="^schedule$"))
    dp.add_handler(CallbackQueryHandler(handle_weekday, pattern="^weekday:"))
    dp.add_handler(CallbackQueryHandler(handle_time_selection, pattern="^time:"))
    dp.add_handler(CallbackQueryHandler(handle_caption, pattern="^caption$"))
    dp.add_handler(CallbackQueryHandler(handle_update_token, pattern="^update_token$"))
    dp.add_handler(CallbackQueryHandler(handle_pause, pattern="^pause$"))
    dp.add_handler(CallbackQueryHandler(handle_status, pattern="^status$"))
    dp.add_handler(CallbackQueryHandler(handle_reset, pattern="^reset$"))
    dp.add_handler(CallbackQueryHandler(handle_token_choice, pattern="^token:"))
    dp.add_handler(CallbackQueryHandler(handle_token_confirm, pattern="^token:confirm$"))
    dp.add_handler(CallbackQueryHandler(handle_message, pattern="^message:"))
    
    # Navigation handlers
    dp.add_handler(CallbackQueryHandler(handle_back_to_accounts, pattern="^back_to_accounts$"))
    dp.add_handler(CallbackQueryHandler(handle_view_schedule, pattern="^view_schedule$"))
    dp.add_handler(CallbackQueryHandler(handle_edit_time, pattern="^edit_time:"))
    dp.add_handler(CallbackQueryHandler(handle_add_time, pattern="^add_time:"))
    dp.add_handler(CallbackQueryHandler(handle_post_logs, pattern="^post_logs$"))
    dp.add_handler(CallbackQueryHandler(handle_confirm_reset, pattern="^confirm_reset$"))
    dp.add_handler(CallbackQueryHandler(handle_back_to_menu, pattern="^back_to_menu$"))
    
    # Add new handler for logs
    dp.add_handler(CallbackQueryHandler(handle_view_logs, pattern="^view_logs$"))
    
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
