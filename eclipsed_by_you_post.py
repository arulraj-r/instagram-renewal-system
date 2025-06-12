
import os
import time
import json
import logging
import requests
import dropbox
from telegram import Bot
from datetime import datetime

class DropboxToInstagramUploader:
    DROPBOX_TOKEN_URL = "https://api.dropbox.com/oauth2/token"
    INSTAGRAM_API_BASE = "https://graph.facebook.com/v18.0"

    def __init__(self):
        self.script_name = "ECLIPSED_BY_YOU_post.py"

        # Logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler()]
        )
        self.logger = logging.getLogger()

        # Secrets from environment
        self.instagram_access_token = os.getenv("IG_ECLIPSED_BY_YOU_TOKEN")
        self.instagram_account_id = os.getenv("IG_ECLIPSED_BY_YOU_ID")
        self.dropbox_app_key = os.getenv("DROPBOX_ECLIPSED_BY_YOU_APP_KEY")
        self.dropbox_app_secret = os.getenv("DROPBOX_ECLIPSED_BY_YOU_APP_SECRET")
        self.dropbox_refresh_token = os.getenv("DROPBOX_ECLIPSED_BY_YOU_REFRESH")
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.repo = os.getenv("GITHUB_REPOSITORY")
        self.gh_pat = os.getenv("GH_PAT")

        self.dropbox_folder = "/ECLIPSED_BY_YOU"
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
        self.logger.info("Refreshing Dropbox token...")
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
            self.logger.info("Dropbox token refreshed.")
            return new_token
        else:
            self.send_message("❌ Dropbox refresh failed: " + r.text)
            raise Exception("Dropbox refresh failed.")

    def update_github_secret(self, secret_name, secret_value):
        try:
            headers = {
                "Authorization": f"token {self.gh_pat}",
                "Accept": "application/vnd.github+json"
            }
            pubkey_resp = requests.get(f"https://api.github.com/repos/{self.repo}/actions/secrets/public-key", headers=headers)
            key_data = pubkey_resp.json()
            from nacl import encoding, public
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
            self.logger.error(f"Failed to update secret: {e}")
            return False

    def list_dropbox_files(self):
        files = self.dbx.files_list_folder(self.dropbox_folder).entries
        valid_exts = ('.mp4', '.mov', '.jpg', '.jpeg', '.png')
        return [f for f in files if f.name.lower().endswith(valid_exts)]

    def post_to_instagram(self, file):
        name = file.name
        ext = name.lower()
        media_type = "REELS" if ext.endswith((".mp4", ".mov")) else "IMAGE"

        temp_link = self.dbx.files_get_temporary_link(file.path_lower).link
        file_size = f"{file.size / 1024 / 1024:.2f}MB"
        files_remaining = len(self.list_dropbox_files())

        self.send_message(f"🚀 Uploading: {name}\n📂 Type: {media_type}\n📐 Size: {file_size}\n📄 Path: {file.path_lower}\n📦 Remaining: {files_remaining}")

        caption = "#ECLIPSED_BY_YOU ✨"

        url = f"{self.INSTAGRAM_API_BASE}/{self.instagram_account_id}/media"
        data = {
            "access_token": self.instagram_access_token,
            "caption": caption
        }

        if media_type == "REELS":
            data["media_type"] = "REELS"
            data["video_url"] = temp_link
        else:
            data["image_url"] = temp_link

        res = requests.post(url, data=data)
        if res.status_code != 200:
            err = res.json().get("error", {}).get("message", "Unknown")
            code = res.json().get("error", {}).get("code", "N/A")
            self.send_message(f"❌ Failed: {name}\n🧾 Error: {err}\n🪪 Code: {code}\n📐 {file_size}")
            return False

        creation_id = res.json()["id"]

        if media_type == "REELS":
            for _ in range(12):
                status = requests.get(f"{self.INSTAGRAM_API_BASE}/{creation_id}?fields=status_code&access_token={self.instagram_access_token}").json()
                if status.get("status_code") == "FINISHED":
                    break
                elif status.get("status_code") == "ERROR":
                    self.send_message(f"❌ IG processing failed: {name}")
                    return False
                time.sleep(5)

        pub = requests.post(f"{self.INSTAGRAM_API_BASE}/{self.instagram_account_id}/media_publish",
                            data={"creation_id": creation_id, "access_token": self.instagram_access_token})
        if pub.status_code == 200:
            self.send_message(f"✅ Uploaded: {name}\n📦 Files left: {files_remaining - 1}")
            self.dbx.files_delete_v2(file.path_lower)
            return True
        else:
            self.send_message(f"❌ Publish failed: {name}\n{pub.text}")
            return False

    def run(self):
        files = self.list_dropbox_files()
        for file in files:
            success = self.post_to_instagram(file)
            if success:
                break  # Post only one file
        else:
            self.send_message("📭 No eligible files found.")

if __name__ == "__main__":
    DropboxToInstagramUploader().run() 
