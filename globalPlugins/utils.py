import os
import requests
import wx
import ui
from scriptHandler import api  # NVDA-ன் Clipboard செயல்பாடுகளுக்காக

class Utils:
    def __init__(self):
        # NVDA Add-on தகவல்கள் சேமிக்கப்படும் பயனர் பாதை (User Config Directory)
        self.config_dir = os.path.join(os.path.expanduser("~"), ".nvda", "github_manager")
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir, exist_ok=True)
            
        self.token_path = os.path.join(self.config_dir, "gh_token.txt")

    def normalize_name(self, name_str):
        """பெயரைச் சீரமைக்கும் சார்பு (Spaces, Hyphens, Underscores நீக்கும்)."""
        if not name_str:
            return ""
        cleaned = str(name_str).lower().replace(" ", "").replace("-", "").replace("_", "")
        return cleaned

    def copy_to_clipboard(self, text):
        """உரையை Windows Clipboard-ல் நகலெடுத்து NVDA மூலம் அறிவிக்கும்."""
        try:
            api.copyToClip(text)
            ui.message("Raw URL Copied!")
        except Exception as e:
            ui.message(f"Failed to copy: {str(e)}")

    def load_token(self):
        """சேமிக்கப்பட்ட டோக்கனை மீட்டெடுக்கும்."""
        if os.path.exists(self.token_path):
            try:
                with open(self.token_path, "r", encoding="utf-8") as f:
                    token = f.read().strip()
                    return token
            except Exception:
                return ""
        return ""

    def save_token(self, new_token):
        """புதிய டோக்கனை கோப்பில் சேமிக்கும்."""
        try:
            with open(self.token_path, "w", encoding="utf-8") as f:
                f.write(str(new_token).strip())
            return True
        except Exception:
            return False

    def delete_token(self):
        """சேமிக்கப்பட்ட டோக்கனை நீக்கும்."""
        try:
            if os.path.exists(self.token_path):
                os.remove(self.token_path)
                return True
        except Exception:
            pass
        return False

    def get_auth_headers(self, token):
        """GitHub API கோரிக்கைகளுக்கான பிரத்யேக Headers."""
        headers = {
            "User-Agent": "GitHub-Manager-NVDA",
            "Accept": "application/vnd.github.v3+json"
        }
        if token and token.strip():
            headers["Authorization"] = f"token {token.strip()}"
        return headers

    def http_request_with_token(self, url_str, method, json_body=None, custom_token="", callback=None):
        """குறிப்பிட்ட டோக்கன் கொண்டு API கோரிக்கைகளை நிறைவேற்றும்."""
        def run():
            headers = self.get_auth_headers(custom_token)
            if json_body and method.upper() in ["POST", "PUT", "PATCH", "DELETE"]:
                headers["Content-Type"] = "application/json; charset=UTF-8"

            try:
                response = requests.request(
                    method=method.upper(),
                    url=url_str,
                    headers=headers,
                    data=json_body.encode('utf-8') if isinstance(json_body, str) else json_body,
                    timeout=30
                )
                code = response.status_code
                result = response.text
            except Exception as e:
                code = 0
                result = f"Error: {str(e)}"

            if callback:
                wx.CallAfter(callback, code, result)

        import threading
        threading.Thread(target=run, daemon=True).start()

    def http_request(self, url_str, method, json_body=None, callback=None):
        """சேமிக்கப்பட்ட டோக்கனை வைத்து கோரிக்கைகளை நிறைவேற்றும்."""
        token = self.load_token()
        if not token:
            if callback:
                wx.CallAfter(callback, 401, "Token missing")
            return
        self.http_request_with_token(url_str, method, json_body, token, callback)


# ----------------------------------------------------------------------
# NVDA Loading Dialog UI Component
# ----------------------------------------------------------------------

class LoadingDialog(wx.Dialog):
    """காத்திருப்பு நிலையைக் காண்பிக்கும் உரையாடல் சாளரம்."""
    def __init__(self, parent, message):
        super().__init__(parent, title="Please Wait", size=(300, 120))
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        self.lbl_msg = wx.StaticText(panel, label=message)
        sizer.Add(self.lbl_msg, 0, wx.ALL | wx.ALIGN_CENTER, 20)
        
        panel.SetSizer(sizer)

# ----------------------------------------------------------------------
# Singleton Instance உருவாக்கம்
# ----------------------------------------------------------------------

utils = Utils()

def show_loading(msg):
    """காத்திருப்புச் செய்தியை NVDA மூலம் அறிவிக்கும்."""
    ui.message(msg)
