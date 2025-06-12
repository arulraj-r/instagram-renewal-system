import os
import json
import time
import requests
import dropbox
import logging
from datetime import datetime, timezone
from telegram import Bot

class DropboxToInstagramUploader:
    DROPBOX_TOKEN_URL = "https://api.dropbox.com/oauth2/token"
    INSTAGRAM_GRAPH_API_BASE = "https://graph.facebook.com/v18.0"

    def __init__(self):
        # Setup logging
        self.setup_logging()
        
        # Get environment variables
        self.instagram_access_token = os.getenv('IG_INKWISPS_TOKEN')
        self.instagram_account_id = os.getenv('IG_INKWISPS_ID')
        
        self.dropbox_app_key = os.getenv('DROPBOX_INKWISPS_APP_KEY')
        self.dropbox_app_secret = os.getenv('DROPBOX_INKWISPS_APP_SECRET')
        self.dropbox_refresh_token = os.getenv('DROPBOX_INKWISPS_REFRESH')
        
        # Telegram setup
        self.telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        if self.telegram_bot_token and self.telegram_chat_id:
            self.telegram_bot = Bot(token=self.telegram_bot_token)
        
        # Configuration
        self.max_posts_per_run = 1  # Process only one post per run
        self.retry_attempts = 3
        self.retry_delay = 60
        
        self.dropbox_folder = "/inkwisps"
        
        # Initialize Dropbox
        self.refresh_dropbox_access_token()
        self.dbx = dropbox.Dropbox(oauth2_access_token=self.dropbox_access_token)

    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger()

    def send_telegram_message(self, message):
        try:
            if hasattr(self, 'telegram_bot'):
                self.telegram_bot.send_message(
                    chat_id=self.telegram_chat_id,
                    text=message,
                    parse_mode='HTML'
                )
        except Exception as e:
            self.logger.error(f"Failed to send Telegram message: {e}")

    def refresh_dropbox_access_token(self):
        self.logger.info("Refreshing Dropbox access token...")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.dropbox_refresh_token,
            "client_id": self.dropbox_app_key,
            "client_secret": self.dropbox_app_secret,
        }
        resp = requests.post(self.DROPBOX_TOKEN_URL, data=data)
        if resp.status_code == 200:
            new_token = resp.json().get("access_token")
            if new_token:
                self.dropbox_access_token = new_token
                self.logger.info("Dropbox access token refreshed successfully.")
                # Update GitHub secret
                self.update_github_secret("DROPBOX_INKWISPS_TOKEN", new_token)
            else:
                error_msg = "Failed to get new Dropbox access token from response."
                self.logger.error(error_msg)
                self.send_telegram_message(f"❌ {error_msg}")
        else:
            error_msg = f"Dropbox token refresh failed: {resp.text}"
            self.logger.error(error_msg)
            self.send_telegram_message(f"❌ {error_msg}")

    def update_github_secret(self, secret_name, secret_value):
        try:
            repo = os.getenv("GITHUB_REPOSITORY")
            pat = os.getenv("GH_PAT")
            
            if not repo or not pat:
                self.logger.error("Missing GITHUB_REPOSITORY or GH_PAT environment variables")
                return False
                
            url = f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}"
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "Authorization": f"token {pat}"
            }
            
            # Get public key
            public_key_url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
            public_key_response = requests.get(public_key_url, headers=headers)
            
            if public_key_response.status_code != 200:
                self.logger.error(f"Failed to get public key: {public_key_response.text}")
                return False
                
            public_key_data = public_key_response.json()
            
            # Encrypt the secret
            from nacl import encoding, public
            public_key = public.PublicKey(public_key_data["key"].encode("utf-8"), encoding.Base64Encoder())
            sealed_box = public.SealedBox(public_key)
            encrypted_value = sealed_box.encrypt(secret_value.encode("utf-8"))
            
            # Update secret
            data = {
                "encrypted_value": encoding.Base64Encoder().encode(encrypted_value).decode("utf-8"),
                "key_id": public_key_data["key_id"]
            }
            
            response = requests.put(url, headers=headers, json=data)
            
            if response.status_code in [201, 204]:
                self.logger.info(f"Successfully updated GitHub secret: {secret_name}")
                return True
            else:
                self.logger.error(f"Failed to update GitHub secret: {response.text}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error updating GitHub secret: {str(e)}")
            return False

    def list_dropbox_files(self):
        try:
            self.logger.info(f"Listing files in Dropbox folder: {self.dropbox_folder}")
            result = self.dbx.files_list_folder(self.dropbox_folder)
            valid_exts = ('.mp4', '.mov', '.jpg', '.jpeg', '.png')
            return [entry for entry in result.entries if entry.name.lower().endswith(valid_exts)]
        except Exception as e:
            error_msg = f"Dropbox list folder error: {e}"
            self.logger.error(error_msg)
            self.send_telegram_message(f"❌ {error_msg}")
            return []

    def generate_caption(self, filename):
        return "✨ :) #myboyishlife ✨\n\n#quotes #love #motivation #quoteoftheday #life #motivationalquotes\n\n#poetry #lovequotes #quotesaboutlife #quotesdaily #lifequotes #loveyourself #mindset #quotesoftheday #lifestyle #happiness #happy"

    def upload_video_to_instagram(self, temp_link, caption):
        return self._upload_media_to_instagram(temp_link, caption, media_type="REELS")

    def upload_image_to_instagram(self, temp_link, caption):
        return self._upload_media_to_instagram(temp_link, caption, media_type="IMAGE")

    def _upload_media_to_instagram(self, media_url, caption, media_type):
        def try_upload(access_token):
            container_url = f"{self.INSTAGRAM_GRAPH_API_BASE}/{self.instagram_account_id}/media"
            container_data = {
                "caption": caption,
                "access_token": access_token
            }
            if media_type == "REELS":
                container_data["media_type"] = "REELS"
                container_data["video_url"] = media_url
            elif media_type == "IMAGE":
                container_data["image_url"] = media_url

            self.logger.info(f"Creating Instagram {media_type.lower()} container...")
            resp = requests.post(container_url, data=container_data)
            if resp.status_code != 200:
                error_msg = f"{media_type} container creation failed: {resp.text}"
                self.logger.error(error_msg)
                self.send_telegram_message(f"❌ {error_msg}")
                return False

            creation_id = resp.json().get("id")
            if not creation_id:
                error_msg = f"No creation ID returned for {media_type}."
                self.logger.error(error_msg)
                self.send_telegram_message(f"❌ {error_msg}")
                return False

            if media_type == "REELS":
                status_url = f"{self.INSTAGRAM_GRAPH_API_BASE}/{creation_id}?fields=status_code,status&access_token={access_token}"
                interval = 5  # Check every 5 seconds
                max_attempts = 12  # Maximum 1 minute of checking
                attempts = 0

                while attempts < max_attempts:
                    try:
                        status_resp = requests.get(status_url)
                        if status_resp.status_code != 200:
                            error_data = status_resp.json()
                            error_msg = f"Status check failed (HTTP {status_resp.status_code}): {error_data.get('error', {}).get('message', 'Unknown error')}"
                            self.logger.warning(error_msg)
                            time.sleep(interval)
                            attempts += 1
                            continue

                        status_data = status_resp.json()
                        status = status_data.get("status_code")
                        status_message = status_data.get("status", "No status message")
                        
                        self.logger.info(f"Media container status: {status} - {status_message}")
                        
                        if status == "FINISHED":
                            self.logger.info("Media container created successfully")
                            break
                        elif status == "ERROR":
                            error_msg = f"Media container creation failed: {status_message}"
                            self.logger.error(error_msg)
                            self.send_telegram_message(f"❌ {error_msg}")
                            return False
                        elif status == "IN_PROGRESS":
                            self.logger.info(f"Creating media container... (attempt {attempts + 1}/{max_attempts})")
                            
                        time.sleep(interval)
                        attempts += 1
                    except Exception as e:
                        error_msg = f"Error checking media container status: {str(e)}"
                        self.logger.error(error_msg)
                        time.sleep(interval)
                        attempts += 1
                        continue
                else:
                    error_msg = "Media container creation timed out after 1 minute"
                    self.logger.error(error_msg)
                    self.send_telegram_message(f"❌ {error_msg}")
                    return False

            publish_url = f"{self.INSTAGRAM_GRAPH_API_BASE}/{self.instagram_account_id}/media_publish"
            publish_data = {
                "creation_id": creation_id,
                "access_token": access_token
            }
            self.logger.info("Publishing media to Instagram...")
            pub_resp = requests.post(publish_url, data=publish_data)
            if pub_resp.status_code == 200:
                success_msg = f"{media_type} published successfully!"
                self.logger.info(success_msg)
                self.send_telegram_message(f"✅ {success_msg}")
                return True
            else:
                error_msg = f"{media_type} publish failed: {pub_resp.text}"
                self.logger.error(error_msg)
                self.send_telegram_message(f"❌ {error_msg}")
                return False

        return try_upload(self.instagram_access_token)

    def delete_dropbox_file(self, path_lower):
        try:
            self.dbx.files_delete_v2(path_lower)
            self.logger.info(f"Deleted Dropbox file {path_lower}")
            return True
        except Exception as e:
            error_msg = f"Failed to delete Dropbox file {path_lower}: {e}"
            self.logger.error(error_msg)
            self.send_telegram_message(f"❌ {error_msg}")
            return False

    def process_one_file(self):
        files = self.list_dropbox_files()
        if not files:
            self.logger.info("No files found in Dropbox folder.")
            return

        # Process only the first file found
        file_metadata = files[0]
        self.logger.info(f"Processing file: {file_metadata.name}")
        caption = self.generate_caption(file_metadata.name)

        try:
            temp_link_obj = self.dbx.files_get_temporary_link(file_metadata.path_lower)
            temp_link = temp_link_obj.link
        except Exception as e:
            error_msg = f"Failed to get temporary link for {file_metadata.name}: {e}"
            self.logger.error(error_msg)
            self.send_telegram_message(f"❌ {error_msg}")
            return

        ext = file_metadata.name.lower()
        if ext.endswith(('.mp4', '.mov')):
            success = self.upload_video_to_instagram(temp_link, caption)
        elif ext.endswith(('.jpg', '.jpeg', '.png')):
            success = self.upload_image_to_instagram(temp_link, caption)
        else:
            self.logger.warning(f"Unsupported file type: {file_metadata.name}")
            return

        if success:
            self.delete_dropbox_file(file_metadata.path_lower)
            self.logger.info(f"Successfully uploaded and deleted {file_metadata.name}")
        else:
            self.logger.warning(f"Failed to upload {file_metadata.name}")

    def run(self):
        try:
            self.process_one_file()
        except Exception as e:
            error_msg = f"Error in main process: {str(e)}"
            self.logger.error(error_msg)
            self.send_telegram_message(f"❌ {error_msg}")

if __name__ == "__main__":
    uploader = DropboxToInstagramUploader()
    uploader.run()
