import datetime
import json
import threading
import requests
import wx
import gui
import ui

from . import token_module
from . import my_repos
from . import public_repos

current_sort_option = "Name (A-Z)"
current_repo_sort_option = "Name (A-Z)"


def format_accessible_date(iso_str):
    if not iso_str or iso_str == "N/A":
        return "N/A"
    try:
        dt = datetime.datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%d %B %Y at %I:%M %p")
    except Exception:
        return iso_str


def sort_repos_list(repo_list):
    if not repo_list:
        return []
    sorted_list = list(repo_list)
    if current_repo_sort_option == "Name (A-Z)":
        sorted_list.sort(key=lambda x: str(x.get("full_name", "")).lower())
    elif current_repo_sort_option == "Name (Z-A)":
        sorted_list.sort(key=lambda x: str(x.get("full_name", "")).lower(), reverse=True)
    elif current_repo_sort_option == "Date Newest":
        sorted_list.sort(key=lambda x: str(x.get("updated_at", "")), reverse=True)
    elif current_repo_sort_option == "Date Oldest":
        sorted_list.sort(key=lambda x: str(x.get("updated_at", "")))
    return sorted_list


class ProfileDialog(wx.Dialog):
    def __init__(self, parent, token, show_main_screen_callback):
        super(ProfileDialog, self).__init__(parent, title="My GitHub Profile", size=(500, 550))
        self.token = token
        self.show_main_screen_callback = show_main_screen_callback
        self.profile_data = {}

        self.init_ui()
        self.load_profile()

    def init_ui(self):
        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.panel = wx.Panel(self)
        self.panel_sizer = wx.BoxSizer(wx.VERTICAL)

        # Header Title
        title_lbl = wx.StaticText(self.panel, label="My GitHub Profile")
        font = title_lbl.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        title_lbl.SetFont(font)
        self.panel_sizer.Add(title_lbl, 0, wx.ALL | wx.ALIGN_CENTER, 10)

        # Profile Details Container Box
        self.details_box = wx.BoxSizer(wx.VERTICAL)
        self.panel_sizer.Add(self.details_box, 1, wx.EXPAND | wx.ALL, 10)

        # Action Buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        self.btn_copy = wx.Button(self.panel, label="Copy Profile Details")
        self.btn_copy.Bind(wx.EVT_BUTTON, self.on_copy_profile)
        btn_sizer.Add(self.btn_copy, 0, wx.ALL, 5)

        self.btn_back = wx.Button(self.panel, label="Back")
        self.btn_back.Bind(wx.EVT_BUTTON, self.on_back)
        btn_sizer.Add(self.btn_back, 0, wx.ALL, 5)

        self.panel_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER, 10)
        self.panel.SetSizer(self.panel_sizer)

        self.main_sizer.Add(self.panel, 1, wx.EXPAND)
        self.SetSizer(self.main_sizer)
        self.Centre()

    def load_profile(self):
        ui.message("Loading Profile...")
        headers = {"Authorization": f"token {self.token}", "Accept": "application/vnd.github.v3+json"}
        threading.Thread(target=self._fetch_profile_thread, args=(headers,)).start()

    def _fetch_profile_thread(self, headers):
        try:
            res = requests.get("https://api.github.com/user", headers=headers, timeout=30)
            if res.status_code == 200:
                obj = res.json()
                plan_name = "Free"
                if "plan" in obj and obj["plan"]:
                    plan_name = obj["plan"].get("name", "Free").capitalize()

                self.profile_data = {
                    "login": obj.get("login", "N/A"),
                    "type": obj.get("type", "User"),
                    "name": obj.get("name") or "",
                    "bio": obj.get("bio") or "",
                    "location": obj.get("location") or "",
                    "public_repos": str(obj.get("public_repos", 0)),
                    "total_private_repos": str(obj.get("total_private_repos", obj.get("owned_private_repos", 0))),
                    "public_gists": str(obj.get("public_gists", 0)),
                    "private_gists": str(obj.get("private_gists", 0)),
                    "followers": str(obj.get("followers", 0)),
                    "following": str(obj.get("following", 0)),
                    "created_at": obj.get("created_at", "N/A"),
                    "starred_repos": "0",
                    "plan_name": plan_name
                }

                # Starred repos count fetch
                star_res = requests.get("https://api.github.com/user/starred?per_page=100", headers=headers, timeout=30)
                if star_res.status_code == 200:
                    link_header = star_res.headers.get("Link", "")
                    if "rel=\"last\"" in link_header:
                        import re
                        match = re.search(r'page=(\d+)>;\s*rel="last"', link_header)
                        if match:
                            self.profile_data["starred_repos"] = match.group(1)
                    else:
                        self.profile_data["starred_repos"] = str(len(star_res.json()))

                wx.CallAfter(self.render_profile_data)
            else:
                wx.CallAfter(ui.message, f"Failed to fetch profile details (Error {res.status_code}).")
        except Exception as e:
            wx.CallAfter(ui.message, "Internet error: Request timed out or failed.")

    def render_profile_data(self):
        self.details_box.Clear(True)

        fields = [
            ("Username", self.profile_data["login"]),
            ("Account Type", self.profile_data["type"]),
            ("Name", self.profile_data["name"] or "No Name Added"),
            ("Bio", self.profile_data["bio"] or "No Bio Added"),
            ("Location", self.profile_data["location"] or "No Location Added"),
            ("Public Repositories", str(self.profile_data["public_repos"])),
            ("Private Repositories", str(self.profile_data["total_private_repos"])),
            ("Starred Repositories", str(self.profile_data.get("starred_repos", "0"))),
            ("Public Gists", str(self.profile_data["public_gists"])),
            ("Private Gists", str(self.profile_data["private_gists"])),
            ("Followers", str(self.profile_data["followers"])),
            ("Following", str(self.profile_data["following"])),
            ("GitHub Plan", self.profile_data["plan_name"]),
            ("Account Created", format_accessible_date(self.profile_data["created_at"]))
        ]

        # Add fields as read-only text controls (better for NVDA and accessibility)
        for label, val in fields:
            # For better Android-like TextEdit view experience:
            # Display label and value in a cleaner format
            
            # Create label
            lbl = wx.StaticText(self.panel, label=f"{label}:")
            lbl_font = lbl.GetFont()
            lbl_font.SetWeight(wx.FONTWEIGHT_BOLD)
            lbl.SetFont(lbl_font)
            self.details_box.Add(lbl, 0, wx.TOP | wx.LEFT | wx.RIGHT, 5)
            
            # Create read-only text field for value
            # WITHOUT wx.TE_WORDWRAP to preserve original formatting
            # Let text display exactly as user wrote it
            txt_ctrl = wx.TextCtrl(self.panel, value=str(val), style=wx.TE_READONLY | wx.TE_MULTILINE)
            
            # Auto-calculate height based on content lines
            lines = str(val).count('\n') + 1
            height = max(30, lines * 20)  # 20 pixels per line
            txt_ctrl.SetSize((-1, height))
            
            self.details_box.Add(txt_ctrl, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 5)

            # Add Edit buttons for editable fields
            if label == "Name":
                btn_txt = "Edit Name" if self.profile_data["name"] else "Add Name"
                btn = wx.Button(self.panel, label=btn_txt)
                btn.Bind(wx.EVT_BUTTON, lambda e: show_single_field_edit_screen(self.token, "name", "Name", self.profile_data["name"], self.reload_screen))
                self.details_box.Add(btn, 0, wx.BOTTOM | wx.LEFT | wx.RIGHT | wx.EXPAND, 5)
            elif label == "Bio":
                btn_txt = "Edit Bio" if self.profile_data["bio"] else "Add Bio"
                btn = wx.Button(self.panel, label=btn_txt)
                btn.Bind(wx.EVT_BUTTON, lambda e: show_single_field_edit_screen(self.token, "bio", "Bio", self.profile_data["bio"], self.reload_screen))
                self.details_box.Add(btn, 0, wx.BOTTOM | wx.LEFT | wx.RIGHT | wx.EXPAND, 5)
            elif label == "Location":
                btn_txt = "Edit Location" if self.profile_data["location"] else "Add Location"
                btn = wx.Button(self.panel, label=btn_txt)
                btn.Bind(wx.EVT_BUTTON, lambda e: show_location_edit_screen(self.token, self.profile_data["location"], self.reload_screen))
                self.details_box.Add(btn, 0, wx.BOTTOM | wx.LEFT | wx.RIGHT | wx.EXPAND, 5)
            elif label == "Starred Repositories":
                btn = wx.Button(self.panel, label="View Starred Repositories")
                btn.Bind(wx.EVT_BUTTON, lambda e: show_starred_repos_screen(self.token, self.profile_data["login"], self.reload_screen))
                self.details_box.Add(btn, 0, wx.BOTTOM | wx.LEFT | wx.RIGHT | wx.EXPAND, 5)
            elif label == "Followers":
                btn = wx.Button(self.panel, label="View Followers List")
                btn.Bind(wx.EVT_BUTTON, lambda e: public_repos.show_public_user_list_screen(self.profile_data["login"], "followers", "Followers List", self.reload_screen, 1, int(self.profile_data["followers"])))
                self.details_box.Add(btn, 0, wx.BOTTOM | wx.LEFT | wx.RIGHT | wx.EXPAND, 5)
            elif label == "Following":
                btn = wx.Button(self.panel, label="View Following List")
                btn.Bind(wx.EVT_BUTTON, lambda e: public_repos.show_public_user_list_screen(self.profile_data["login"], "following", "Following List", self.reload_screen, 1, int(self.profile_data["following"])))
                self.details_box.Add(btn, 0, wx.BOTTOM | wx.LEFT | wx.RIGHT | wx.EXPAND, 5)

        # Refresh layout to display content
        self.panel.Layout()
        self.panel_sizer.Layout()
        self.Layout()
        self.Refresh()
        
        # Announce to user that profile loaded
        wx.CallAfter(ui.message, "Profile loaded. Use Tab to navigate through fields.")

    def reload_screen(self):
        self.Show()
        self.load_profile()

    def on_copy_profile(self, event):
        profile_str = ""
        for child in self.details_box.GetChildren():
            widget = child.GetWindow()
            
            # Copy labels (StaticText)
            if isinstance(widget, wx.StaticText):
                profile_str += widget.GetLabel() + " "
            
            # Copy values (TextCtrl) - THIS WAS MISSING!
            elif isinstance(widget, wx.TextCtrl):
                profile_str += widget.GetValue() + "\n"

        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(profile_str))
            wx.TheClipboard.Close()
            ui.message("Profile details copied to clipboard.")

    def on_back(self, event):
        self.Close()
        if self.show_main_screen_callback:
            self.show_main_screen_callback()


class SingleFieldEditDialog(wx.Dialog):
    def __init__(self, parent, token, field_key, field_label, current_value, back_callback):
        super(SingleFieldEditDialog, self).__init__(parent, title=f"Edit {field_label}", size=(400, 200))
        self.token = token
        self.field_key = field_key
        self.field_label = field_label
        self.back_callback = back_callback

        sizer = wx.BoxSizer(wx.VERTICAL)

        lbl = wx.StaticText(self, label=f"Enter new {field_label}:")
        sizer.Add(lbl, 0, wx.ALL, 5)

        self.txt_val = wx.TextCtrl(self, value=current_value or "")
        sizer.Add(self.txt_val, 0, wx.ALL | wx.EXPAND, 5)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_save = wx.Button(self, label="Save Changes")
        btn_save.Bind(wx.EVT_BUTTON, self.on_save)
        btn_sizer.Add(btn_save, 0, wx.ALL, 5)

        btn_cancel = wx.Button(self, label="Cancel")
        btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)
        btn_sizer.Add(btn_cancel, 0, wx.ALL, 5)

        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER, 5)
        self.SetSizer(sizer)
        self.Centre()

    def on_save(self, event):
        new_val = self.txt_val.GetValue().strip()
        ui.message(f"Updating {self.field_label}...")
        headers = {"Authorization": f"token {self.token}", "Accept": "application/vnd.github.v3+json"}
        payload = {self.field_key: new_val}

        def thread_target():
            try:
                res = requests.patch("https://api.github.com/user", headers=headers, json=payload, timeout=30)
                if res.status_code == 200:
                    wx.CallAfter(ui.message, f"{self.field_label} updated successfully!")
                    wx.CallAfter(self.finish)
                else:
                    wx.CallAfter(ui.message, f"Failed to update {self.field_label}.")
            except Exception:
                wx.CallAfter(ui.message, "Internet error occurred.")

        threading.Thread(target=thread_target).start()

    def finish(self):
        self.Close()
        if self.back_callback:
            self.back_callback()

    def on_cancel(self, event):
        self.finish()


def show_profile_screen(show_main_screen_callback=None):
    token = token_module.load_token()
    if not token:
        token_module.show_token_missing_screen(show_main_screen_callback)
        return
    gui.mainFrame.prePopup()
    dlg = ProfileDialog(gui.mainFrame, token, show_main_screen_callback)
    dlg.ShowModal()
    gui.mainFrame.postPopup()


def show_single_field_edit_screen(token, field_key, field_label, current_value, back_callback):
    dlg = SingleFieldEditDialog(gui.mainFrame, token, field_key, field_label, current_value, back_callback)
    dlg.ShowModal()


def show_location_edit_screen(token, current_loc, back_callback):
    show_single_field_edit_screen(token, "location", "Location", current_loc, back_callback)


def show_starred_repos_screen(token, current_username, back_callback):
    ui.message("Loading Starred Repositories...")
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}

    def fetch_thread():
        try:
            res = requests.get("https://api.github.com/user/starred?per_page=100", headers=headers, timeout=30)
            if res.status_code == 200:
                repos = res.json()
                # Sort & show list in NVDA menu
                wx.CallAfter(public_repos.show_repo_list, repos, back_callback)
            else:
                wx.CallAfter(ui.message, f"Failed to load starred repos (Error {res.status_code})")
        except Exception:
            wx.CallAfter(ui.message, "Internet error occurred.")

    threading.Thread(target=fetch_thread).start()