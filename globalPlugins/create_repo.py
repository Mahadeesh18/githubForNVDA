import wx
import json
import threading
import requests
import gui
import ui

class CreateRepoDialog(wx.Dialog):
    def __init__(self, parent, token, show_main_screen_callback=None):
        super(CreateRepoDialog, self).__init__(parent, title="Create Repository", size=(450, 350))
        self.token = token
        self.show_main_screen_callback = show_main_screen_callback
        
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Repository Name Label & Input
        lbl_name = wx.StaticText(self, label="Repository Name:")
        main_sizer.Add(lbl_name, 0, wx.ALL, 5)
        self.txt_name = wx.TextCtrl(self)
        self.txt_name.Bind(wx.EVT_TEXT, self.on_text_change)
        main_sizer.Add(self.txt_name, 0, wx.ALL | wx.EXPAND, 5)

        # Description Label & Input
        lbl_desc = wx.StaticText(self, label="Description (Optional):")
        main_sizer.Add(lbl_desc, 0, wx.ALL, 5)
        self.txt_desc = wx.TextCtrl(self, style=wx.TE_MULTILINE, size=(-1, 60))
        main_sizer.Add(self.txt_desc, 0, wx.ALL | wx.EXPAND, 5)

        # Submit & Back Buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_create = wx.Button(self, label="Create")
        self.btn_create.Disable()  # Initial state disabled
        self.btn_create.Bind(wx.EVT_BUTTON, self.on_create_click)
        btn_sizer.Add(self.btn_create, 0, wx.ALL, 5)

        btn_back = wx.Button(self, label="Back")
        btn_back.Bind(wx.EVT_BUTTON, self.on_back)
        btn_sizer.Add(btn_back, 0, wx.ALL, 5)

        main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER, 5)
        self.SetSizer(main_sizer)
        self.Centre()

    def on_text_change(self, event):
        val = self.txt_name.GetValue().strip()
        self.btn_create.Enable(bool(val))

    def on_back(self, event):
        self.Close()
        if self.show_main_screen_callback:
            self.show_main_screen_callback()

    def on_create_click(self, event):
        repo_name = self.txt_name.GetValue().strip()
        repo_desc = self.txt_desc.GetValue().strip()
        
        ui.message("Checking repository availability...")
        threading.Thread(target=self.check_and_process_repo, args=(repo_name, repo_desc)).start()

    def check_and_process_repo(self, repo_name, repo_desc):
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }
        try:
            res = requests.get("https://api.github.com/user/repos?per_page=100", headers=headers, timeout=30)
            if res.status_code == 200:
                repos = res.json()
                found_repo = None
                for r in repos:
                    if r.get("name", "").lower() == repo_name.lower():
                        found_repo = r
                        break

                wx.CallAfter(self.handle_check_result, found_repo, repo_name, repo_desc)
            else:
                wx.CallAfter(ui.message, "Failed to fetch repository details.")
        except Exception as e:
            wx.CallAfter(ui.message, "Internet error: Request timed out or failed.")

    def handle_check_result(self, found_repo, repo_name, repo_desc):
        if found_repo:
            # Repository already exists prompt
            dlg = wx.MessageDialog(
                self,
                f"Repository '{found_repo['name']}' already exists. Do you want to overwrite it?",
                "Repository Exists",
                wx.YES_NO | wx.ICON_WARNING
            )
            if dlg.ShowModal() == wx.ID_YES:
                self.ask_visibility_and_create(repo_name, repo_desc, overwrite_owner=found_repo['owner']['login'], overwrite_name=found_repo['name'])
            dlg.Destroy()
        else:
            self.ask_visibility_and_create(repo_name, repo_desc)

    def ask_visibility_and_create(self, repo_name, repo_desc, overwrite_owner=None, overwrite_name=None):
        dlg = wx.SingleChoiceDialog(
            self,
            f"Choose repository visibility for '{repo_name}':",
            "Select Repository Type",
            ["Public Repository", "Private Repository"]
        )
        if dlg.ShowModal() == wx.ID_OK:
            is_private = (dlg.GetSelection() == 1)
            ui.message("Processing request...")
            threading.Thread(
                target=self.execute_repo_creation,
                args=(repo_name, repo_desc, is_private, overwrite_owner, overwrite_name)
            ).start()
        dlg.Destroy()

    def execute_repo_creation(self, repo_name, repo_desc, is_private, overwrite_owner, overwrite_name):
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }

        # Handle overwrite (Delete existing repository first)
        if overwrite_owner and overwrite_name:
            del_url = f"https://api.github.com/repos/{overwrite_owner}/{overwrite_name}"
            del_res = requests.delete(del_url, headers=headers, timeout=30)
            if del_res.status_code not in [200, 204]:
                wx.CallAfter(ui.message, "Failed to delete existing repository for overwrite.")
                return

        # Create new repository
        payload = {
            "name": repo_name,
            "private": is_private
        }
        if repo_desc:
            payload["description"] = repo_desc

        create_res = requests.post("https://api.github.com/user/repos", headers=headers, json=payload, timeout=30)
        if create_res.status_code in [200, 201]:
            wx.CallAfter(self.show_success_dialog, repo_name)
        else:
            wx.CallAfter(ui.message, "Failed to create repository.")

    def show_success_dialog(self, repo_name):
        wx.MessageBox(f"Repository '{repo_name}' created successfully!", "Success", wx.OK | wx.ICON_INFORMATION, self)
        self.Close()
        if self.show_main_screen_callback:
            self.show_main_screen_callback()

def show_create_repo_screen(token, show_main_screen_callback=None):
    if not token:
        ui.message("Personal Access Token is missing. Please set your token first.")
        return

    gui.mainFrame.prePopup()
    dialog = CreateRepoDialog(gui.mainFrame, token, show_main_screen_callback)
    dialog.ShowModal()
    gui.mainFrame.postPopup()