# -*- coding: utf-8 -*-
# telegram_bot_controller.py

import os
import json
import logging
import requests
import dropbox
import base64
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
SCHEDULER_DIR = "scheduler"
CONFIG_PATH = os.path.join(SCHEDULER_DIR, "config.json")
CAPTIONS_PATH = os.path.join(SCHEDULER_DIR, "captions.json")
PAUSED_PATH = os.path.join(SCHEDULER_DIR, "paused.json")
EXPIRY_PATH = os.path.join(SCHEDULER_DIR, "token_expiry.json")
RESULTS_PATH = os.path.join(SCHEDULER_DIR, "post_results.json")
BANNED_PATH = os.path.join(SCHEDULER_DIR, "banned.json")

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

# ----------- DROPBOX HELPERS ----------- #
def get_dropbox_access_token(account):
    app_key = os.getenv(f"DROPBOX_{account.upper()}_APP_KEY")
    app_secret = os.getenv(f"DROPBOX_{account.upper()}_APP_SECRET")
    refresh_token = os.getenv(f"DROPBOX_{account.upper()}_REFRESH")

    if not app_key or not app_secret or not refresh_token:
        logger.error(f"Missing Dropbox credentials for {account}")
        return None

    try:
        response = requests.post("https://api.dropbox.com/oauth2/token", data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": app_key,
            "client_secret": app_secret
        })
        response.raise_for_status()
        return response.json().get("access_token")
    except Exception as e:
        logger.error(f"Dropbox token refresh error for {account}: {e}")
        return None

def get_dropbox_client(account):
    token = get_dropbox_access_token(account)
    if not token:
        logger.error(f"Dropbox access token failed for {account}")
        return None
    return dropbox.Dropbox(oauth2_access_token=token)

def count_files_in_folder(dbx, folder_path):
    try:
        count = 0
        result = dbx.files_list_folder(folder_path, recursive=True)
        
        while True:
            for entry in result.entries:
                if isinstance(entry, dropbox.files.FileMetadata):
                    count += 1
            
            if not result.has_more:
                break
                
            result = dbx.files_list_folder_continue(result.cursor)
            
        return count
    except Exception as e:
        logger.error(f"Error counting files in {folder_path}: {str(e)}")
        return 0

def get_remaining_files(account):
    try:
        dbx = get_dropbox_client(account)
        if not dbx:
            return 0
        
        # Count files in the main account folder
        main_folder = f"/{account}"
        count = count_files_in_folder(dbx, main_folder)
        
        logger.info(f"Found {count} files in {main_folder}")
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
    user_id = update.effective_user.id
    if is_banned(user_id):
        update.message.reply_text("🚫 Access denied.")
        return

    USER_STATE[user_id] = "awaiting_password"
    update.message.reply_text("🔐 Enter password to access bot:")

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
            update.message.reply_text("❌ Incorrect password. Access denied.")
            ban_user(user_id)
    except Exception as e:
        logger.error(f"Error in handle_password: {str(e)}")
        update.message.reply_text("❌ An error occurred during login. Please try again.")

@require_auth
def show_accounts(update: Update, context: CallbackContext):
    accounts = ["inkwisps", "ink_wisps", "eclipsed_by_you"]
    buttons = [[InlineKeyboardButton(acc, callback_data=f"account:{acc}")] for acc in accounts]
    reply_markup = InlineKeyboardMarkup(buttons)
    
    if update.callback_query:
        update.callback_query.message.edit_text("Choose an account:", reply_markup=reply_markup)
    else:
        update.message.reply_text("Choose an account:", reply_markup=reply_markup)

def handle_back_to_accounts(update: Update, context: CallbackContext):
    query = update.callback_query
    accounts = ["inkwisps", "ink_wisps", "eclipsed_by_you"]
    buttons = [[InlineKeyboardButton(acc, callback_data=f"account:{acc}")] for acc in accounts]
    reply_markup = InlineKeyboardMarkup(buttons)
    query.message.edit_text("Choose an account:", reply_markup=reply_markup)

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
            [InlineKeyboardButton("♻ Reset Schedule", callback_data="reset")],
            [InlineKeyboardButton("🔙 Back to Accounts", callback_data="back_to_accounts")]
        ]
        query.message.edit_text(f"Manage: {account}", reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logger.error(f"Error in handle_account_selection: {str(e)}")
        if update.callback_query:
            update.callback_query.message.reply_text("❌ An error occurred. Please try again.")

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
        text = f"📅 {day} Schedule for {account}:\n{', '.join(times)}"
    else:
        text = f"📅 No posts scheduled for {day}"
    
    buttons = [[InlineKeyboardButton("🔙 Back to Schedule", callback_data="view_schedule")]]
    query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

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
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    buttons = [[InlineKeyboardButton(day, callback_data=f"weekday:{day}")] for day in weekdays]
    update.callback_query.message.reply_text("Select a weekday:", reply_markup=InlineKeyboardMarkup(buttons))

def handle_weekday(update: Update, context: CallbackContext):
    query = update.callback_query
    weekday = query.data.split(":")[1]
    context.user_data['weekday'] = weekday
    context.user_data['next_action'] = 'post_count'
    query.message.reply_text(f"How many posts to schedule for {weekday}?")

def handle_time_selection(update: Update, context: CallbackContext):
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
        query.message.reply_text("✅ Schedule saved.")
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

def handle_message(update: Update, context: CallbackContext):
    text = update.message.text
    account = context.user_data.get('account')
    weekday = context.user_data.get('weekday')

    if context.user_data.get('next_action') == 'post_count':
        try:
            count = int(text)
            if count < 1 or count > 24:
                update.message.reply_text("❌ Please enter a number between 1 and 24.")
                return
            context.user_data['post_count'] = count
            context.user_data['selected_times'] = []
            update.message.reply_text(
                "Select time slots (15-minute intervals):",
                reply_markup=create_time_button_grid([], count)
            )
            context.user_data['next_action'] = 'timeslot'
        except:
            update.message.reply_text("❌ Invalid number. Try again:")

    elif context.user_data.get('next_action') == 'caption':
        captions = load_json(CAPTIONS_PATH)
        captions[account] = text
        save_json(CAPTIONS_PATH, captions)
        update.message.reply_text("✅ Static caption saved.")
        context.user_data.clear()

    elif context.user_data.get('next_action') == 'update_token':
        secret_name = context.user_data.get('secret_target')
        token_type = context.user_data.get('token_type')
        
        # Log the attempt
        logger.info(f"Attempting to update {token_type} token for {account}")
        
        success = update_github_secret(secret_name, text)
        if success:
            update.message.reply_text(
                f"✅ {token_type} token updated successfully.\n"
                "Now enter expiry date (YYYY-MM-DD):"
            )
            context.user_data['next_action'] = 'token_expiry'
        else:
            update.message.reply_text(
                f"❌ Failed to update {token_type} token.\n"
                "Please check the logs and try again."
            )
            context.user_data.clear()

    elif context.user_data.get('next_action') == 'token_expiry':
        try:
            expiry_date = datetime.strptime(text, "%Y-%m-%d")
            if expiry_date < datetime.now():
                update.message.reply_text("❌ Expiry date cannot be in the past. Try again:")
                return
                
            update_token_expiry(account, text)
            update.message.reply_text(
                "✅ Token expiry date saved.\n"
                f"Token will expire on {text}"
            )
            context.user_data.clear()
        except ValueError:
            update.message.reply_text("❌ Invalid date format. Use YYYY-MM-DD (e.g. 2024-12-31)")

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
        token_type_display = "Instagram"
    else:
        secret_name = f"DROPBOX_{account.upper()}_TOKEN"
        token_type_display = "Dropbox"
    
    context.user_data['secret_target'] = secret_name
    context.user_data['token_type'] = token_type_display
    context.user_data['next_action'] = 'update_token'
    
    buttons = [
        [InlineKeyboardButton("✅ Continue", callback_data="token:continue")],
        [InlineKeyboardButton("❌ Cancel", callback_data="back_to_menu")]
    ]
    
    update.callback_query.message.edit_text(
        f"⚠️ You are about to update the {token_type_display} token for {account}.\n\n"
        f"This will update the GitHub secret: {secret_name}\n\n"
        "Do you want to continue?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

def handle_token_continue(update: Update, context: CallbackContext):
    query = update.callback_query
    secret_name = context.user_data.get('secret_target')
    token_type = context.user_data.get('token_type')
    
    query.message.edit_text(
        f"Please send the new {token_type} token value.\n\n"
        f"⚠️ This will update: {secret_name}"
    )

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
    status += f"📝 Caption: {caption.get(account, 'None')}\n"
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
        status += f"{day}: {', '.join(times)}\n"

    update.callback_query.message.edit_text(status, parse_mode='Markdown')

# ----------- TIME SLOT HELPERS ----------- #
def generate_time_slots():
    slots = []
    for hour in range(24):
        for minute in range(0, 60, 15):
            time_str = f"{hour:02d}:{minute:02d}"
            slots.append(time_str)
    return slots

def create_time_button_grid(selected_times, max_slots):
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

# ----------- MAIN ----------- #
def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN not set")
        return

    # Debug print for environment variables
    print("TELEGRAM_BOT_PASSWORD env:", os.getenv("TELEGRAM_BOT_PASSWORD"))
    print("DROPBOX_INKWISPS_APP_KEY env:", os.getenv("DROPBOX_INKWISPS_APP_KEY"))
    print("DROPBOX_INKWISPS_APP_SECRET env:", os.getenv("DROPBOX_INKWISPS_APP_SECRET"))
    print("DROPBOX_INKWISPS_REFRESH env:", os.getenv("DROPBOX_INKWISPS_REFRESH"))

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
    dp.add_handler(CallbackQueryHandler(handle_token_continue, pattern="^token:continue$"))
    dp.add_handler(CallbackQueryHandler(handle_message, pattern="^message:"))
    
    # Navigation handlers
    dp.add_handler(CallbackQueryHandler(handle_back_to_accounts, pattern="^back_to_accounts$"))
    dp.add_handler(CallbackQueryHandler(handle_view_schedule, pattern="^view_schedule$"))
    dp.add_handler(CallbackQueryHandler(handle_view_day, pattern="^view_day:"))
    dp.add_handler(CallbackQueryHandler(handle_post_logs, pattern="^post_logs$"))
    dp.add_handler(CallbackQueryHandler(handle_confirm_reset, pattern="^confirm_reset$"))
    dp.add_handler(CallbackQueryHandler(handle_account_selection, pattern="^back_to_menu$"))
    
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
