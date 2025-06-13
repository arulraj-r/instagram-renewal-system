import os
import time
import json
import logging
import requests
import dropbox
from telegram import Bot
from datetime import datetime, timedelta
from pytz import timezone, utc
from nacl import encoding, public

class DropboxToInstagramUploader:
    DROPBOX_TOKEN_URL = "https://api.dropbox.com/oauth2/token"
    INSTAGRAM_API_BASE = "https://graph.facebook.com/v18.0"

    def __init__(self):
        self.script_name = "eclipsed_by_you_post.py"
        self.MAX_WAIT_SECONDS = int(os.getenv("MAX_WAIT_SECONDS", 600))  # 10 minutes default
        self.ist = timezone('Asia/Kolkata')  # Indian Standard Time

        # Logging setup
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler()]
        )
        self.logger = logging.getLogger()

        # Environment secrets
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

        self.dropbox_access_token = self.refresh_dropbox_token()
        self.dbx = dropbox.Dropbox(oauth2_access_token=self.dropbox_access_token)

    def send_message(self, msg):
        prefix = f"[{self.script_name}]\n"
        try:
            self.telegram_bot.send_message(chat_id=self.telegram_chat_id, text=prefix + msg)
        except Exception as e:
            self.logger.error(f"Telegram send error: {e}")

    def refresh_dropbox_token(self):
        self.logger.info("🔁 Refreshing Dropbox token...")
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
            self.logger.info("✅ Dropbox token refreshed.")
            return new_token
        else:
            self.send_message("❌ Dropbox token refresh failed:\n" + r.text)
            raise Exception("Dropbox refresh failed.")

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

            res = requests.put(
                f"https://api.github.com/repos/{self.repo}/actions/secrets/{secret_name}",
                headers=headers,
                json={"encrypted_value": encrypted, "key_id": key_data["key_id"]}
            )
            return res.status_code in [201, 204]
        except Exception as e:
            self.logger.error(f"GitHub secret update failed: {e}")
            return False

    def list_dropbox_files(self):
        try:
            files = self.dbx.files_list_folder(self.dropbox_folder).entries
            valid_exts = ('.mp4', '.mov', '.jpg', '.jpeg', '.png')
            return [f for f in files if f.name.lower().endswith(valid_exts)]
        except Exception as e:
            self.send_message(f"❌ Dropbox folder read failed: {e}")
            return []

    def is_scheduled_time(self):
        try:
            with open("scheduler/config.json", "r") as f:
                schedule = json.load(f)
            
            # Get current time in IST
            now_utc = datetime.now(utc)
            now_ist = now_utc.astimezone(self.ist)
            today = now_ist.strftime("%A")
            
            self.logger.info(f"Current IST time: {now_ist.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            
            allowed_times = schedule.get("eclipsed_by_you", {}).get(today, [])
            now_str = now_ist.strftime("%H:%M")
            match_found = False

            for t in allowed_times:
                try:
                    scheduled_dt = datetime.strptime(t, "%H:%M").time()
                    scheduled_time = now_ist.replace(hour=scheduled_dt.hour, minute=scheduled_dt.minute, second=0, microsecond=0)
                    delta = int((scheduled_time - now_ist).total_seconds())

                    if -120 <= delta <= 600:  # Accept from 1 min before to 10 min after
                        if delta > 0:
                            self.logger.info(f"⏳ Sleeping {delta} seconds for schedule match: {t}")
                            time.sleep(delta)
                        match_found = True
                        break
                except Exception as e:
                    self.logger.error(f"⛔ Schedule parse error: {e}")

            if not match_found:
                self.logger.info("⏰ Not in schedule window.")
                return False

            return True
            
        except Exception as e:
            self.logger.error(f"Schedule check failed: {e}")
            return True  # fail-safe fallback

    def post_to_instagram(self, file):
        name = file.name
        ext = name.lower()
        media_type = "REELS" if ext.endswith((".mp4", ".mov")) else "IMAGE"

        temp_link = self.dbx.files_get_temporary_link(file.path_lower).link
        file_size = f"{file.size / 1024 / 1024:.2f}MB"
        files_remaining = len(self.list_dropbox_files())

        self.send_message(f"🚀 Uploading: {name}\n📂 Type: {media_type}\n📐 Size: {file_size}\n📦 Remaining: {files_remaining}")

        caption = "#eclipsed_by_you ✨\n#🎵 #🎶 #🎧 #aesthetic"

        # Step 1: Upload to IG
        upload_url = f"{self.INSTAGRAM_API_BASE}/{self.instagram_account_id}/media"
        data = {
            "access_token": self.instagram_access_token,
            "caption": caption
        }

        if media_type == "REELS":
            data.update({
                "media_type": "REELS",
                "video_url": temp_link,
                "share_to_feed": "false"  # Set during media creation
            })
        else:
            data["image_url"] = temp_link

        res = requests.post(upload_url, data=data)
        if res.status_code != 200:
            err = res.json().get("error", {}).get("message", "Unknown")
            code = res.json().get("error", {}).get("code", "N/A")
            self.send_message(f"❌ Failed: {name}\n🧾 Error: {err}\n🪪 Code: {code}\n📐 {file_size}")
            return False

        creation_id = res.json()["id"]

        # Step 2: If REELS, wait for it to process
        if media_type == "REELS":
            for _ in range(12):  # wait up to 1 minute
                status = requests.get(
                    f"{self.INSTAGRAM_API_BASE}/{creation_id}",
                    params={"fields": "status_code", "access_token": self.instagram_access_token}
                ).json()
                if status.get("status_code") == "FINISHED":
                    break
                elif status.get("status_code") == "ERROR":
                    self.send_message(f"❌ IG processing failed: {name}")
                    return False
                time.sleep(5)

        # Step 3: Publish
        publish_url = f"{self.INSTAGRAM_API_BASE}/{self.instagram_account_id}/media_publish"
        publish_data = {
            "creation_id": creation_id,
            "access_token": self.instagram_access_token
        }

        pub = requests.post(publish_url, data=publish_data)
        if pub.status_code == 200:
            self.send_message(f"✅ Uploaded: {name}\n📦 Files left: {files_remaining - 1}")
            self.dbx.files_delete_v2(file.path_lower)
            return True
        else:
            self.send_message(f"❌ Publish failed: {name}\n{pub.text}")
            return False

    def run(self):
        if not self.is_scheduled_time():
            self.logger.info("⏰ Not in schedule, skipping.")
            return

        files = self.list_dropbox_files()
        if not files:
            self.send_message("📭 No eligible files found.")
            return

        for file in files:
            success = self.post_to_instagram(file)
            if success:
                break  # only post one file per run

if __name__ == "__main__":
    DropboxToInstagramUploader().run()
