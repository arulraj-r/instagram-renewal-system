def refresh_dropbox_token(self):
    """
    Always refresh the Dropbox access token on each run and update GitHub secrets.
    This ensures we always have a fresh token regardless of expiration time.
    """
    # Validate credentials first
    if not all([self.dropbox_refresh_token, self.dropbox_app_key, self.dropbox_app_secret]):
        missing = []
        if not self.dropbox_refresh_token:
            missing.append("DROPBOX_INKWISPS_REFRESH")
        if not self.dropbox_app_key:
            missing.append("DROPBOX_INKWISPS_APP_KEY")
        if not self.dropbox_app_secret:
            missing.append("DROPBOX_INKWISPS_APP_SECRET")
        self.logger.error(f"❌ Missing required Dropbox credentials: {', '.join(missing)}")
        return False

    self.logger.info("🔄 Refreshing Dropbox token on startup...")
    
    data = {
        "grant_type": "refresh_token",
        "refresh_token": self.dropbox_refresh_token,
        "client_id": self.dropbox_app_key,
        "client_secret": self.dropbox_app_secret,
    }

    try:
        r = requests.post(self.DROPBOX_TOKEN_URL, data=data)
        if r.status_code == 200:
            new_token = r.json().get("access_token")
            if not new_token:
                self.logger.error("❌ No access token in response")
                return False
                
            self.dropbox_access_token = new_token
            self.logger.info("✅ Dropbox token refreshed successfully.")

            # Always update GitHub secret with new token
            updated = update_github_secret("DROPBOX_INKWISPS_TOKEN", new_token)
            if updated:
                self.logger.info("✅ GitHub secret updated with new token.")
                return True
            else:
                self.logger.error("❌ Failed to update GitHub secret.")
                return False
        else:
            self.logger.error(f"❌ Dropbox token refresh failed: {r.text}")
            return False
    except Exception as e:
        self.logger.error(f"❌ Error during token refresh: {str(e)}")
        return False
