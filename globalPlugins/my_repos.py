import base64
import threading
import requests
import wx
import gui
import ui

from . import token_module
from . import create_repo
from . import repo_options

current_repo_sort_option = "Name (A-Z)"
current_file_sort_option = "Name (A-Z)"


def http_request_with_timeout(url, method, data=None, headers=None):
    try:
        if method == "GET":
            res = requests.get(url, headers=headers, timeout=30)
        elif method == "POST":
            res = requests.post(url, headers=headers, json=data, timeout=30)
        elif method == "PUT":
            res = requests.put(url, headers=headers, json=data, timeout=30)
        elif method == "DELETE":
            res = requests.delete(url, headers=headers, json=data, timeout=30)
        return res.status_code, res.json() if res.text and res.status_code != 204 else (res.status_code, {})
    except Exception:
        return 0, None


def sort_my_repositories_list(repo_list):
    if not repo_list:
        return []
    filtered = []
    for v in repo_list:
        if current_repo_sort_option == "Show Private Only":
            if v.get("is_private"):
                filtered.append(v)
        elif current_repo_sort_option == "Show Public Only":
            if not v.get("is_private"):
                filtered.append(v)
        else:
            filtered.append(v)

    if current_repo_sort_option == "Name (A-Z)":
        filtered.sort(key=lambda x: str(x.get("name", "")).lower())
    elif current_repo_sort_option == "Name (Z-A)":
        filtered.sort(key=lambda x: str(x.get("name", "")).lower(), reverse=True)
    elif current_repo_sort_option == "Date Newest":
        filtered.sort(key=lambda x: str(x.get("updated_at", "")), reverse=True)
    elif current_repo_sort_option == "Date Oldest":
        filtered.sort(key=lambda x: str(x.get("updated_at", "")))

    return filtered


def sort_my_files_list(file_list):
    if not file_list:
        return []
    folders = [f for f in file_list if f.get("type") == "dir"]
    files = [f for f in file_list if f.get("type") != "dir"]

    reverse = current_file_sort_option in ["Name (Z-A)", "Date Newest"]
    key_func = (lambda x: str(x.get("sha", ""))) if "Date" in current_file_sort_option else (lambda x: str(x.get("name", "")).lower())

    folders.sort(key=key_func, reverse=reverse)
    files.sort(key=key_func, reverse=reverse)

    return folders + files


class MyReposDialog(wx.Dialog):
    def __init__(self, parent, token, show_main_screen_callback):
        super(MyReposDialog, self).__init__(parent, title="My Repositories", size=(500, 450))
        self.token = token
        self.show_main_screen_callback = show_main_screen_callback
        self.raw_repo_list = []
        
        self.init_ui()
        self.load_repositories()

    def init_ui(self):
        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Header
        lbl_title = wx.StaticText(self, label="Select Repository")
        self.main_sizer.Add(lbl_title, 0, wx.ALL | wx.ALIGN_CENTER, 5)

        # Buttons Top Section
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        btn_create = wx.Button(self, label="Create New Repository")
        btn_create.Bind(wx.EVT_BUTTON, self.on_create_repo)
        btn_sizer.Add(btn_create, 0, wx.ALL, 5)

        btn_sort = wx.Button(self, label="Sort Repositories")
        btn_sort.Bind(wx.EVT_BUTTON, self.on_sort)
        btn_sizer.Add(btn_sort, 0, wx.ALL, 5)

        self.main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER, 5)

        # Search Bar
        search_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.txt_search = wx.TextCtrl(self)
        search_sizer.Add(self.txt_search, 1, wx.ALL | wx.EXPAND, 5)

        btn_search = wx.Button(self, label="Search")
        btn_search.Bind(wx.EVT_BUTTON, self.on_search)
        search_sizer.Add(btn_search, 0, wx.ALL, 5)

        self.main_sizer.Add(search_sizer, 0, wx.EXPAND, 5)

        # Repository List Box
        self.list_box = wx.CheckListBox(self)
        self.main_sizer.Add(self.list_box, 1, wx.ALL | wx.EXPAND, 5)

        # Bottom Action Buttons
        bottom_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        btn_open = wx.Button(self, label="Open Selected")
        btn_open.Bind(wx.EVT_BUTTON, self.on_open)
        bottom_sizer.Add(btn_open, 0, wx.ALL, 5)

        btn_delete = wx.Button(self, label="Delete Checked")
        btn_delete.Bind(wx.EVT_BUTTON, self.on_delete_checked)
        bottom_sizer.Add(btn_delete, 0, wx.ALL, 5)

        btn_back = wx.Button(self, label="Back")
        btn_back.Bind(wx.EVT_BUTTON, self.on_back)
        bottom_sizer.Add(btn_back, 0, wx.ALL, 5)

        self.main_sizer.Add(bottom_sizer, 0, wx.ALIGN_CENTER, 5)
        self.SetSizer(self.main_sizer)
        self.Centre()

    def load_repositories(self):
        ui.message("Fetching Repositories...")
        headers = {"Authorization": f"token {self.token}", "Accept": "application/vnd.github.v3+json"}
        threading.Thread(target=self._fetch_repos_thread, args=(headers,)).start()

    def _fetch_repos_thread(self, headers):
        code, res = http_request_with_timeout("https://api.github.com/user/repos?per_page=100", "GET", headers=headers)
        if code == 200 and isinstance(res, list):
            self.raw_repo_list = [
                {
                    "name": r.get("name"),
                    "owner": r.get("owner", {}).get("login"),
                    "key": f"{r.get('owner', {}).get('login')}/{r.get('name')}",
                    "is_private": r.get("private", False),
                    "updated_at": r.get("updated_at", "")
                } for r in res
            ]
            wx.CallAfter(self.render_list)
        else:
            wx.CallAfter(ui.message, f"Error {code}: Check Token")

    def render_list(self):
        self.list_box.Clear()
        query = self.txt_search.GetValue().strip().lower()
        
        filtered = [r for r in self.raw_repo_list if query in r["name"].lower()] if query else self.raw_repo_list
        self.display_repos = sort_my_repositories_list(filtered)

        for r in self.display_repos:
            prefix = "[Private] " if r["is_private"] else "[Public] "
            self.list_box.Append(prefix + r["name"])

    def on_search(self, event):
        self.render_list()

    def on_sort(self, event):
        global current_repo_sort_option
        options = ["Name (A-Z)", "Name (Z-A)", "Date Newest", "Date Oldest", "Show Private Only", "Show Public Only"]
        dlg = wx.SingleChoiceDialog(self, "Select Sort Option", "Sort By", options)
        if dlg.ShowModal() == wx.ID_OK:
            current_repo_sort_option = dlg.GetStringSelection()
            self.render_list()
        dlg.Destroy()

    def on_create_repo(self, event):
        self.Hide()
        create_repo.show_create_repo_screen(self.token, lambda: self.show_and_refresh())

    def show_and_refresh(self):
        self.Show()
        self.load_repositories()

    def on_open(self, event):
        sel = self.list_box.GetSelection()
        if sel != wx.NOT_FOUND:
            repo_info = self.display_repos[sel]
            self.Hide()
            show_files_list(self.token, repo_info["owner"], repo_info["name"], "", self.show_and_refresh)
        else:
            ui.message("Please select a repository first.")

    def on_delete_checked(self, event):
        checked_indices = self.list_box.GetCheckedItems()
        if not checked_indices:
            ui.message("No repositories selected for deletion.")
            return

        dlg = wx.MessageDialog(self, "Are you sure you want to delete selected repositories?", "Confirm Delete", wx.YES_NO | wx.ICON_WARNING)
        if dlg.ShowModal() == wx.ID_YES:
            to_delete = [self.display_repos[i] for i in checked_indices]
            threading.Thread(target=self._delete_repos_thread, args=(to_delete,)).start()
        dlg.Destroy()

    def _delete_repos_thread(self, to_delete):
        headers = {"Authorization": f"token {self.token}", "Accept": "application/vnd.github.v3+json"}
        ui.message("Deleting repositories...")
        for item in to_delete:
            url = f"https://api.github.com/repos/{item['owner']}/{item['name']}"
            http_request_with_timeout(url, "DELETE", headers=headers)
        wx.CallAfter(self.load_repositories)

    def on_back(self, event):
        self.Close()
        if self.show_main_screen_callback:
            self.show_main_screen_callback()


def show_my_repos(token, show_main_screen_callback=None):
    if not token:
        token_module.show_token_missing_screen(show_main_screen_callback)
        return
    gui.mainFrame.prePopup()
    dlg = MyReposDialog(gui.mainFrame, token, show_main_screen_callback)
    dlg.ShowModal()
    gui.mainFrame.postPopup()


# --- Files List Window Placeholder ---
def show_files_list(token, owner, repo, path, back_callback):
    # Files List logic handled via standard sub-dialog setup if required
    pass