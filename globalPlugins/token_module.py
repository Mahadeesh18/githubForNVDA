import json
import threading
import requests
import wx
import ui

# ----------------------------------------------------------------------
# உதவிச் சார்புகள் மற்றும் அமைப்புகள் (Helper Functions & Settings)
# ----------------------------------------------------------------------

# NVDA Add-on அமைப்புகளில் Token சேமிக்கப்படும் மாறிலிகள்
CONFIG_TOKEN = ""

def load_token():
    """சேமிக்கப்பட்ட டோக்கனை மீட்டெடுக்கும்."""
    global CONFIG_TOKEN
    return CONFIG_TOKEN

def save_token(token):
    """டோக்கனை உள்ளூரில் சேமிக்கும்."""
    global CONFIG_TOKEN
    CONFIG_TOKEN = token
    return True

def delete_token():
    """சேமிக்கப்பட்ட டோக்கனை நீக்கும்."""
    global CONFIG_TOKEN
    CONFIG_TOKEN = ""
    return True

def get_auth_headers(token):
    headers = {
        "User-Agent": "GitHubTokenManagerNVDA",
        "Accept": "application/vnd.github.v3+json"
    }
    if token and token.strip():
        headers["Authorization"] = f"token {token.strip()}"
    return headers

def verify_github_token_async(token, callback):
    """GitHub API மூலம் டோக்கனை சரிபார்க்கும் Asynchronous சார்பு."""
    def run():
        url = "https://api.github.com/user"
        headers = get_auth_headers(token)
        try:
            res = requests.get(url, headers=headers, timeout=30)
            wx.CallAfter(callback, res.status_code, res.text)
        except requests.exceptions.Timeout:
            wx.CallAfter(callback, -1, "Timeout")
        except Exception as e:
            wx.CallAfter(callback, 0, str(e))

    threading.Thread(target=run, daemon=True).start()


# ----------------------------------------------------------------------
# 1. டோக்கன் இல்லாததற்கான எச்சரிக்கை உரையாடல் (Token Missing Dialog)
# ----------------------------------------------------------------------

class TokenMissingDialog(wx.Dialog):
    def __init__(self, parent, show_main_screen=None):
        super().__init__(parent, title="Token Required", size=(350, 180))
        self.show_main_screen = show_main_screen

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        info_text = wx.StaticText(panel, label="Please set your GitHub Personal Access Token first!")
        sizer.Add(info_text, 0, wx.ALL | wx.EXPAND, 15)

        btn_ok = wx.Button(panel, label="OK")
        btn_ok.Bind(wx.EVT_BUTTON, self.on_ok)
        sizer.Add(btn_ok, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(sizer)

    def on_ok(self, event):
        self.Close()
        if self.show_main_screen:
            self.show_main_screen()


# ----------------------------------------------------------------------
# 2. டோக்கன் உள்ளீடு மற்றும் மேலாண்மை உரையாடல் (Token Edit Dialog)
# ----------------------------------------------------------------------

class TokenEditDialog(wx.Dialog):
    def __init__(self, parent, show_main_screen=None):
        super().__init__(parent, title="Set / Edit Personal Access Token", size=(450, 250))
        self.show_main_screen = show_main_screen

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label="Enter / Paste your Personal Access Token (ghp_...):"), 0, wx.ALL, 5)

        current_token = load_token()
        self.txtToken = wx.TextCtrl(panel, value=current_token, style=wx.TE_MULTILINE)
        sizer.Add(self.txtToken, 0, wx.EXPAND | wx.ALL, 5)

        # பொத்தான்களுக்கான Sizer
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.btnSave = wx.Button(panel, label="Save Token")
        self.btnSave.Bind(wx.EVT_BUTTON, self.on_save)
        btn_sizer.Add(self.btnSave, 0, wx.ALL, 5)

        if current_token:
            self.btnDelete = wx.Button(panel, label="Delete Token")
            self.btnDelete.Bind(wx.EVT_BUTTON, self.on_delete)
            btn_sizer.Add(self.btnDelete, 0, wx.ALL, 5)

        self.btnCancel = wx.Button(panel, label="Cancel")
        self.btnCancel.Bind(wx.EVT_BUTTON, self.on_cancel)
        btn_sizer.Add(self.btnCancel, 0, wx.ALL, 5)

        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        # TextWatcher-க்கு மாற்றாக Text Changed Event
        self.txtToken.Bind(wx.EVT_TEXT, self.update_save_state)
        self.update_save_state(None)

        panel.SetSizer(sizer)

    def update_save_state(self, event):
        clean_val = self.txtToken.GetValue().strip()
        self.btnSave.Enable(bool(clean_val))

    def on_save(self, event):
        clean_val = self.txtToken.GetValue().strip()
        if not clean_val:
            ui.message("Token cannot be empty. Please enter a valid token.")
            return

        ui.message("Verifying Token...")
        self.btnSave.Enable(False)

        def verify_callback(code, res):
            self.btnSave.Enable(True)
            if code == 200:
                save_token(clean_val)
                u_login = ""
                u_name = ""
                try:
                    obj = json.loads(res)
                    u_login = obj.get("login", "")
                    u_name = obj.get("name", "")
                except Exception:
                    pass

                msg = f"Token Verified Successfully!\nLogged in as: {u_login}"
                if u_name:
                    msg += f"\nName: {u_name}"
                
                wx.MessageBox(msg, "Success", wx.OK | wx.ICON_INFORMATION, self)
                self.Close()
                if self.show_main_screen:
                    self.show_main_screen()

            elif code == -1:
                ui.message("Connection timed out after 30 seconds. Please check your internet connection.")
            else:
                ui.message(f"Token verification failed (Error {code}). Please check your token.")

        verify_github_token_async(clean_val, verify_callback)

    def on_delete(self, event):
        dlg = wx.MessageDialog(
            self,
            "Are you sure you want to delete your saved Personal Access Token?",
            "Confirm Token Deletion",
            wx.YES_NO | wx.ICON_QUESTION
        )
        if dlg.ShowModal() == wx.ID_YES:
            delete_token()
            ui.message("Token deleted.")
            self.Close()
            if self.show_main_screen:
                self.show_main_screen()
        dlg.Destroy()

    def on_cancel(self, event):
        self.Close()
        if self.show_main_screen:
            self.show_main_screen()


# ----------------------------------------------------------------------
# தொகுதி அழைப்பு சார்புகள் (Module Functions)
# ----------------------------------------------------------------------

def show_token_missing_screen(show_main_screen_cb=None):
    wx.CallAfter(lambda: TokenMissingDialog(None, show_main_screen_cb).ShowModal())

def show_token_edit_screen(show_main_screen_cb=None):
    wx.CallAfter(lambda: TokenEditDialog(None, show_main_screen_cb).ShowModal())
