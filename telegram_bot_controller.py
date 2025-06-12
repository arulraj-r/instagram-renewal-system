# telegram_bot_controller.py

import os
import json
import logging
import requests
import dropbox
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
RESULTS_PATH = "scheduler/post_results.json"

# ----------- DROPBOX HELPERS ----------- #
def get_dropbox_client(account):
    token = os.getenv(f"DROPBOX_{account.upper()}_TOKEN")
    if not token:
        return None
    return dropbox.Dropbox(token)

def get_remaining_files(account):
    try:
        dbx = get_dropbox_client(account)
        if not dbx:
            return 0
        
        result = dbx.files_list_folder(f"/{account}")
        return len(result.entries)
    except Exception as e:
        logger.error(f"Dropbox error for {account}: {e}")
        return 0

def check_low_files(account, context):
    count = get_remaining_files(account)
    if count < 5:
        message = f"⚠️ Only {count} files remaining in /{account} Dropbox folder"
        context.bot.send_message(chat_id=os.getenv("TELEGRAM_CHAT_ID"), text=message)
    return count

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
        success = update_github_secret(secret_name, text)
        if success:
            update.message.reply_text("✅ Token updated. Now enter expiry date (YYYY-MM-DD):")
            context.user_data['next_action'] = 'token_expiry'
        else:
            update.message.reply_text("❌ Failed to update token.")
            context.user_data.clear()

    elif context.user_data.get('next_action') == 'token_expiry':
        try:
            expiry_date = datetime.strptime(text, "%Y-%m-%d")
            if expiry_date < datetime.now():
                update.message.reply_text("❌ Expiry date cannot be in the past. Try again:")
                return
                
            update_token_expiry(account, text)
            update.message.reply_text("✅ Token expiry date saved.")
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
    results = load_json(RESULTS_PATH)

    # Get Dropbox file count
    remaining_files = get_remaining_files(account)
    
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

    update.callback_query.message.reply_text(status, parse_mode='Markdown')

def handle_reset(update: Update, context: CallbackContext):
    account = context.user_data['account']
    cfg = load_json(CONFIG_PATH)
    cfg[account] = {}
    save_json(CONFIG_PATH, cfg)
    update.callback_query.message.reply_text(f"♻ Schedule for {account} cleared.")

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
    accounts = ["inkwisps", "ink_wisps", "eclipsed_by_you"]
    for account in accounts:
        check_low_files(account, context)
        check_token_expiry(account, context)

# ----------- MAIN ----------- #
def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN not set")
        return

    updater = Updater(token)
    dp = updater.dispatcher

    # Add periodic checks every 6 hours
    job_queue = updater.job_queue
    job_queue.run_repeating(periodic_checks, interval=21600, first=10)

    dp.add_handler(CommandHandler("start", start))
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
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
