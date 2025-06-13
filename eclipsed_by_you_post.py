enhanced_eclipsed_by_you_post.py

import os import time import json import logging import requests import dropbox from telegram import Bot from datetime import datetime, timedelta from pytz import timezone, utc from nacl import encoding, public

class DropboxToInstagramUploader: DROPBOX_TOKEN_URL = "https://api.dropbox.com/oauth2/token" INSTAGRAM_API_BASE = "https://graph.facebook.com/v18.0"

def __init__(self):
    self.script_name = "eclipsed_by_you_post.py"
    self.ist = timezone('Asia/Kolkata')
    self.MAX_WAIT_SECONDS = int(os.getenv("MAX_WAIT_SECONDS", 600))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()]
    )
    self.logger = logging.getLogger()

    # Secrets
    self.instagram_access_token = os.getenv("IG_ECLIPSED_BY_YOU_TOKEN")
    self.instagram_account_id = os.getenv("IG_ECLIPSED_BY_YOU_ID")
    self.dropbox_app_key = os.getenv("DROPBOX_ECLIPSED_BY_YOU_APP_KEY")
    self.dropbox_app_secret = os.getenv("DROPBOX_ECLIPSED_BY_YOU_APP_SECRET")
    self.dropbox_refresh_token = os.getenv("DROPBOX_ECLIPSED_BY_YOU_REFRESH")
    self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    self.repo = os.getenv("GITHUB_REPOSITORY")
    self.gh_pat = os.getenv("GH_PAT")

    self.dropbox_folder = "/eclipsed.by.you"
    self.telegram_bot = Bot(token=self.telegram_bot_token)

    self.audit_log = []
    self.add_audit("📡 Run started at: " + datetime.now(self.ist).strftime('%Y-%m-%d %H:%M:%S'))

    try:
        self.dropbox_access_token = self.refresh_dropbox_token()
        self.dbx = dropbox.Dropbox(oauth2_access_token=self.dropbox_access_token)
    except Exception as e:
        self.add_audit(f"❌ Dropbox token refresh failed: {e}")
        self.send_audit_summary()
        raise

def add_audit(self, msg):
    self.audit_log.append(msg)

def send_audit_summary(self):
    full = f"[{self.script_name}]\n" + "\n".join(self.audit_log)
    try:
        self.telegram_bot.send_message(chat_id=self.telegram_chat_id, text=full)
    except Exception as e:
        self.logger.error(f"Telegram send error: {e}")

def refresh_dropbox_token(self):
    data = {
        "grant_type": "refresh_token",
        "refresh_token": self.dropbox_refresh_token,
        "client_id": self.dropbox_app_key,
        "client_secret": self.dropbox_app_secret,
    }
    r = requests.post(self.DROPBOX_TOKEN_URL, data=data)
    if r.status_code == 200:
        new_token = r.json().get("access_token")
        self.update_github_secret("DROPBOX_ECLIPSED_BY_YOU_TOKEN", new_token)
        self.add_audit("🔁 Dropbox token refreshed.")
        return new_token
    else:
        raise Exception(r.text)

def update_github_secret(self, secret_name, secret_value):
    try:
        headers = {
            "Authorization": f"token {self.gh_pat}",
            "Accept": "application/vnd.github+json"
        }
        pubkey_resp = requests.get(
            f"https://api.github.com/repos/{self.repo}/actions/secrets/public-key",
            headers=headers
        )
        key_data = pubkey_resp.json()
        pubkey = public.PublicKey(key_data["key"].encode(), encoding.Base64Encoder())
        sealed = public.SealedBox(pubkey).encrypt(secret_value.encode())
        encrypted = encoding.Base64Encoder().encode(sealed).decode()

        requests.put(
            f"https://api.github.com/repos/{self.repo}/actions/secrets/{secret_name}",
            headers=headers,
            json={"encrypted_value": encrypted, "key_id": key_data["key_id"]}
        )
    except Exception as e:
        self.add_audit(f"⚠️ GitHub secret update failed: {e}")

def is_scheduled_time(self):
    try:
        with open("scheduler/config.json", "r") as f:
            schedule = json.load(f)

        now_ist = datetime.now(utc).astimezone(self.ist)
        today = now_ist.strftime("%A")
        allowed_times = schedule.get("eclipsed_by_you", {}).get(today, [])
        now_str = now_ist.strftime("%H:%M")
        match_found = False

        for t in allowed_times:
            st = datetime.strptime(t, "%H:%M").time()
            scheduled_time = now_ist.replace(hour=st.hour, minute=st.minute, second=0, microsecond=0)
            delta = int((scheduled_time - now_ist).total_seconds())

            if -120 <= delta <= self.MAX_WAIT_SECONDS:
                if delta > 0:
                    self.add_audit(f"⏳ Sleeping {delta}s for match at {t}")
                    time.sleep(delta)
                match_found = True
                break

        if not match_found:
            self.add_audit(f"⏰ Not in schedule. Current: {now_str}, Allowed: {allowed_times}")
        return match_found
    except Exception as e:
        self.add_audit(f"⚠️ Schedule check error: {e}")
        return True

def list_dropbox_files(self):
    try:
        files = self.dbx.files_list_folder(self.dropbox_folder).entries
        media = [f for f in files if f.name.lower().endswith((".mp4", ".mov", ".jpg", ".jpeg", ".png"))]
        self.add_audit(f"📦 {len(media)} media files found in Dropbox.")
        return media
    except Exception as e:
        self.add_audit(f"❌ Dropbox list failed: {e}")
        return []

def post_to_instagram(self, file):
    name = file.name
    media_type = "REELS" if name.lower().endswith((".mp4", ".mov")) else "IMAGE"
    caption = "#eclipsed_by_you ✨\n#🎵 #🎶 #🎧 #aesthetic"

    try:
        temp_link = self.dbx.files_get_temporary_link(file.path_lower).link
        size = f"{file.size / 1024 / 1024:.2f}MB"
        self.add_audit(f"🚀 Uploading {name} ({media_type}, {size})")

        res = requests.post(
            f"{self.INSTAGRAM_API_BASE}/{self.instagram_account_id}/media",
            data={
                "access_token": self.instagram_access_token,
                "caption": caption,
                **({"image_url": temp_link} if media_type == "IMAGE" else {
                    "media_type": "REELS",
                    "video_url": temp_link,
                    "share_to_feed": "false"
                })
            }
        )
        if res.status_code != 200:
            raise Exception(res.text)

        creation_id = res.json()["id"]
        if media_type == "REELS":
            for _ in range(12):
                status = requests.get(
                    f"{self.INSTAGRAM_API_BASE}/{creation_id}",
                    params={"fields": "status_code", "access_token": self.instagram_access_token}
                ).json()
                if status.get("status_code") == "FINISHED":
                    break
                time.sleep(5)

        pub = requests.post(
            f"{self.INSTAGRAM_API_BASE}/{self.instagram_account_id}/media_publish",
            data={"creation_id": creation_id, "access_token": self.instagram_access_token}
        )
        if pub.status_code == 200:
            self.dbx.files_delete_v2(file.path_lower)
            self.add_audit(f"✅ Uploaded: {name}")
            return True
        else:
            raise Exception(pub.text)

    except Exception as e:
        self.add_audit(f"❌ Post failed: {e}")
        return False

def run(self):
    if not self.is_scheduled_time():
        self.send_audit_summary()
        return

    files = self.list_dropbox_files()
    if not files:
        self.add_audit("📭 No media to post.")
        self.send_audit_summary()
        return

    for file in files:
        if self.post_to_instagram(file):
            break

    self.add_audit("🏁 Run complete.")
    self.send_audit_summary()

if name == "main": DropboxToInstagramUploader().run()

