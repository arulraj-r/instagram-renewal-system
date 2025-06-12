import os
import time
import json
import logging
import requests
import dropbox
import base64
from datetime import datetime
from nacl import encoding, public

# Telegram logging
def send_telegram_message(message):
    try:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not bot_token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        requests.post(url, data=payload)
    except Exception as e:
        print(f"Telegram error: {e}")

# Encrypt and update secret to GitHub
def update_github_secret(secret_name, secret_value):
    try:
        github_token = os.getenv("GH_PAT")
        repo = os.getenv("GITHUB_REPOSITORY")  # GitHub sets this automatically in Actions
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github+json"
        }
        key_url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
        key_resp = requests.get(key_url, headers=headers).json()

        public_key = public.PublicKey(key_resp["key"].encode("utf-8"), encoding.Base64Encoder())
        sealed_box = public.SealedBox(public_key)
        encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
        encrypted_value = base64.b64encode(encrypted).decode("utf-8")

        update_url = f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}"
        payload = {
            "encrypted_value": encrypted_value,
            "key_id": key_resp["key_id"]
        }
        r = requests.put(update_url, headers=headers, json=payload)
        return r.status_code in [201, 204]
    except Exception as e:
        print(f"GitHub secret update error: {e}")
        return False

class DropboxToInstagramUploader:
    DROPBOX_TOKEN_URL = "https://api.dropbox.com/oauth2/token"
    INSTAGRAM_API_BASE = "https://graph.facebook.com/v18.0"

    def __init__(self):
        self.instagram_access_token = os.getenv("IG_INKWISPS_TOKEN")
        self.instagram_account_id = os.getenv("IG_INKWISPS_ID")

        self.dropbox_app_key = os.getenv("DROPBOX_INKWISPS_APP_KEY")
        self.dropbox_app_secret = os.getenv("DROPBOX_INKWISPS_APP_SECRET")
        self.dropbox_access_token = os.getenv("DROPBOX_INKWISPS_TOKEN")
        self.dropbox_refresh_token = os.getenv("DROPBOX_INKWISPS_REFRESH")
        self.dropbox_folder = "/inkwisps"

        self.history_file = "upload_history_inkwisps.json"
        self.upload_history = self.load_upload_history()

        os.makedirs("logs", exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler("logs/inkwisps_post.log", encoding="utf-8"),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger()

        self.refresh_dropbox_token()
        self.dbx = dropbox.Dropbox(oauth2_access_token=self.dropbox_access_token)

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
            self.dropbox_access_token = new_token
            self.logger.info("Dropbox token refreshed.")

            # Update GitHub secret
            updated = update_github_secret("DROPBOX_INKWISPS_TOKEN", new_token)
            if updated:
                self.logger.info("GitHub secret updated successfully.")
            else:
                self.logger.error("Failed to update GitHub secret.")
        else:
            self.logger.error(f"Dropbox token refresh failed: {r.text}")

    def load_upload_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r") as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def save_upload_history(self):
        with open(self.history_file, "w") as f:
            json.dump(self.upload_history, f, indent=2)

    def list_dropbox_files(self):
        try:
            result = self.dbx.files_list_folder(self.dropbox_folder)
            valid = (".mp4", ".mov", ".jpg", ".jpeg", ".png")
            return [f for f in result.entries if f.name.lower().endswith(valid)]
        except Exception as e:
            self.logger.error(f"Dropbox list error: {e}")
            return []

    def is_duplicate(self, file_metadata):
        key = f"{file_metadata.id}_{file_metadata.client_modified}"
        return key in self.upload_history

    def mark_uploaded(self, file_metadata):
        key = f"{file_metadata.id}_{file_metadata.client_modified}"
        self.upload_history[key] = {
            "filename": file_metadata.name,
            "timestamp": datetime.utcnow().isoformat()
        }
        self.save_upload_history()

    def generate_caption(self, filename):
        return "#inkwisps ✨"

    def delete_dropbox_file(self, path_lower):
        try:
            self.dbx.files_delete_v2(path_lower)
            self.logger.info(f"Deleted: {path_lower}")
        except Exception as e:
            self.logger.error(f"Failed to delete: {e}")

    def upload_media(self, media_url, caption, media_type):
        container_url = f"{self.INSTAGRAM_API_BASE}/{self.instagram_account_id}/media"
        publish_url = f"{self.INSTAGRAM_API_BASE}/{self.instagram_account_id}/media_publish"

        data = {
            "access_token": self.instagram_access_token,
            "caption": caption
        }

        if media_type == "REELS":
            data["media_type"] = "REELS"
            data["video_url"] = media_url
        elif media_type == "IMAGE":
            data["image_url"] = media_url

        r = requests.post(container_url, data=data)
        if r.status_code != 200:
            return False, r.text

        creation_id = r.json().get("id")
        if not creation_id:
            return False, "Missing creation ID"

        if media_type == "REELS":
            for _ in range(12):
                status = requests.get(
                    f"{self.INSTAGRAM_API_BASE}/{creation_id}?fields=status_code&access_token={self.instagram_access_token}"
                )
                if status.json().get("status_code") == "FINISHED":
                    break
                elif status.json().get("status_code") == "ERROR":
                    return False, "Video processing failed"
                time.sleep(5)

        pub = requests.post(publish_url, data={
            "creation_id": creation_id,
            "access_token": self.instagram_access_token
        })

        if pub.status_code == 200:
            return True, "Posted successfully"
        return False, pub.text

    def run(self):
        try:
            files = self.list_dropbox_files()
            total_files = len(files)

            if total_files == 0:
                send_telegram_message("📭 No files in `/inkwisps` Dropbox folder.")
                return

            for f in files:
                if self.is_duplicate(f):
                    continue

                name = f.name
                ext = name.lower()
                media_type = "REELS" if ext.endswith((".mp4", ".mov")) else "IMAGE"
                caption = self.generate_caption(name)
                link = self.dbx.files_get_temporary_link(f.path_lower).link

                success, info = self.upload_media(link, caption, media_type)

                if success:
                    self.mark_uploaded(f)
                    self.delete_dropbox_file(f.path_lower)
                    send_telegram_message(
                        f"✅ *Success:*\n📸 `{name}`\n📦 Files left: `{total_files - 1}`"
                    )
                else:
                    send_telegram_message(
                        f"❌ *Failed to post:*\n📸 `{name}`\n🧾 `{info}`"
                    )
                break

        except Exception as e:
            msg = f"🚨 Error:\n`{str(e)}`"
            self.logger.error(msg)
            send_telegram_message(msg)

if __name__ == "__main__":
    DropboxToInstagramUploader().run()
