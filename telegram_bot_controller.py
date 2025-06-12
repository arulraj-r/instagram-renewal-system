import os, json, requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext

CONFIG_PATH = 'scheduler/config.json'
CAPTION_PATH = 'scheduler/captions.json'
PAUSED_PATH = 'scheduler/paused.json'
TOKEN_EXPIRY_PATH = 'scheduler/token_expiry.json'

ACCOUNTS = ["inkwisps", "ink_wisps", "eclipsed_by_you"]

# Load or initialize JSON

def load_json(path):
    return json.load(open(path)) if os.path.exists(path) else {}

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

# --- Start Menu ---
def start(update: Update, context: CallbackContext):
    buttons = [[InlineKeyboardButton(acc, callback_data=f"account|{acc}")] for acc in ACCOUNTS]
    update.message.reply_text("📱 Choose an Instagram account:", reply_markup=InlineKeyboardMarkup(buttons))

# --- Handle account selection ---
def account_menu(update: Update, context: CallbackContext):
    _, acc = update.callback_query.data.split('|')
    context.user_data['account'] = acc
    buttons = [
        [InlineKeyboardButton("📅 Schedule Post", callback_data=f"schedule|{acc}")],
        [InlineKeyboardButton("🔑 Update API Key", callback_data=f"update_api|{acc}")],
        [InlineKeyboardButton("📝 Set Caption", callback_data=f"caption|{acc}")],
        [InlineKeyboardButton("⏸️ Pause/Resume", callback_data=f"pause|{acc}")],
        [InlineKeyboardButton("📊 View Status", callback_data=f"status|{acc}")]
    ]
    update.callback_query.edit_message_text(f"⚙️ Actions for *{acc}*:", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(buttons))

# --- Schedule Post Logic (Simple Example) ---
def handle_schedule(update: Update, context: CallbackContext):
    _, acc = update.callback_query.data.split('|')
    update.callback_query.message.reply_text(f"📆 Enter post count for each weekday (Mon-Sun) for *{acc}*, separated by commas:", parse_mode='Markdown')
    context.user_data['mode'] = 'await_schedule'
    context.user_data['account'] = acc

# --- Capturing replies ---
def text_handler(update: Update, context: CallbackContext):
    mode = context.user_data.get('mode')
    acc = context.user_data.get('account')
    if mode == 'await_schedule':
        counts = update.message.text.split(',')
        if len(counts) != 7:
            return update.message.reply_text("❌ Please provide 7 numbers, comma-separated (e.g. 1,0,2,1,0,0,1)")
        config = load_json(CONFIG_PATH)
        config[acc] = {'weekdays': counts}
        save_json(CONFIG_PATH, config)
        update.message.reply_text("✅ Schedule updated!")
        context.user_data.clear()
    elif mode == 'await_caption':
        cap_data = load_json(CAPTION_PATH)
        cap_data[acc] = update.message.text
        save_json(CAPTION_PATH, cap_data)
        update.message.reply_text("📝 Caption saved.")
        context.user_data.clear()
    elif mode == 'await_api':
        token_data = load_json(TOKEN_EXPIRY_PATH)
        token_data[acc] = {'token': update.message.text}
        save_json(TOKEN_EXPIRY_PATH, token_data)
        update.message.reply_text("🔐 Token updated!")
        context.user_data.clear()

# --- API Key Update ---
def update_api(update: Update, context: CallbackContext):
    _, acc = update.callback_query.data.split('|')
    context.user_data['mode'] = 'await_api'
    context.user_data['account'] = acc
    update.callback_query.message.reply_text("🔐 Send new Instagram access token:")

# --- Set Caption ---
def set_caption(update: Update, context: CallbackContext):
    _, acc = update.callback_query.data.split('|')
    context.user_data['mode'] = 'await_caption'
    context.user_data['account'] = acc
    update.callback_query.message.reply_text("📝 Send the static caption you want to use:")

# --- Pause/Resume ---
def toggle_pause(update: Update, context: CallbackContext):
    _, acc = update.callback_query.data.split('|')
    paused = load_json(PAUSED_PATH)
    paused[acc] = not paused.get(acc, False)
    save_json(PAUSED_PATH, paused)
    status = "⏸️ Paused" if paused[acc] else "▶️ Resumed"
    update.callback_query.message.reply_text(f"{status} posting for {acc}.")

# --- Status ---
def view_status(update: Update, context: CallbackContext):
    _, acc = update.callback_query.data.split('|')
    config = load_json(CONFIG_PATH).get(acc, {})
    paused = load_json(PAUSED_PATH).get(acc, False)
    cap = load_json(CAPTION_PATH).get(acc, 'No caption')
    update.callback_query.message.reply_text(
        f"📊 *{acc}* status:
- Paused: `{paused}`
- Schedule: `{config}`
- Caption: `{cap}`",
        parse_mode='Markdown'
    )

# --- Main Bot Setup ---
def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CallbackQueryHandler(account_menu, pattern=r"^account\|"))
    dp.add_handler(CallbackQueryHandler(handle_schedule, pattern=r"^schedule\|"))
    dp.add_handler(CallbackQueryHandler(update_api, pattern=r"^update_api\|"))
    dp.add_handler(CallbackQueryHandler(set_caption, pattern=r"^caption\|"))
    dp.add_handler(CallbackQueryHandler(toggle_pause, pattern=r"^pause\|"))
    dp.add_handler(CallbackQueryHandler(view_status, pattern=r"^status\|"))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, text_handler))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
