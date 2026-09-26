import os
import json
import base64
import urllib.parse
import threading
import requests
import wx
import ui
from scriptHandler import api

# ----------------------------------------------------------------------
# உதவிச் சார்புகள் (Helper Functions)
# ----------------------------------------------------------------------

def format_size(size_bytes):
    try:
        b = float(size_bytes)
    except (ValueError, TypeError):
        b = 0.0
    if b <= 0:
        return "0 B"
    elif b < 1024:
        return f"{int(b)} B"
    elif b < (1024 * 1024):
        return f"{b / 1024:.2f} KB"
    elif b < (1024 * 1024 * 1024):
        return f"{b / (1024 * 1024):.2f} MB"
    else:
        return f"{b / (1024 * 1024 * 1024):.2f} GB"

def get_auth_headers(token):
    headers = {
        "User-Agent": "GitHubManagerNVDA",
        "Accept": "application/vnd.github.v3+json"
    }
    if token and token.strip():
        headers["Authorization"] = f"token {token.strip()}"
    return headers

def http_request_async(url, method="GET", data=None, token="", callback=None):
    def run():
        headers = get_auth_headers(token)
        try:
            if method.upper() == "GET":
                res = requests.get(url, headers=headers, timeout=30)
            elif method.upper() == "POST":
                res = requests.post(url, headers=headers, data=data, timeout=30)
            elif method.upper() == "PUT":
                res = requests.put(url, headers=headers, data=data, timeout=30)
            elif method.upper() == "PATCH":
                res = requests.patch(url, headers=headers, data=data, timeout=30)
            elif method.upper() == "DELETE":
                res = requests.delete(url, headers=headers, timeout=30)
            else:
                res = None
            
            status_code = res.status_code if res else 0
            text = res.text if res else ""
            if callback:
                wx.CallAfter(callback, status_code, text)
        except Exception as e:
            if callback:
                wx.CallAfter(callback, 0, str(e))

    threading.Thread(target=run, daemon=True).start()


# ----------------------------------------------------------------------
# உரைக்கோப்பு உருவாக்கும் உரையாடல் (Create Text File Dialog)
# ----------------------------------------------------------------------

class CreateTextFileDialog(wx.Dialog):
    def __init__(self, parent, owner, repo, token, on_finish):
        super().__init__(parent, title=f"Create Text File in {repo}", size=(400, 300))
        self.owner = owner
        self.repo = repo
        self.token = token
        self.on_finish = on_finish

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label="File Name (e.g. test.txt):"), 0, wx.ALL, 5)
        self.txtName = wx.TextCtrl(panel)
        sizer.Add(self.txtName, 0, wx.EXPAND | wx.ALL, 5)

        sizer.Add(wx.StaticText(panel, label="File Content:"), 0, wx.ALL, 5)
        self.txtContent = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        sizer.Add(self.txtContent, 1, wx.EXPAND | wx.ALL, 5)

        self.btnSubmit = wx.Button(panel, label="Create File")
        self.btnSubmit.Bind(wx.EVT_BUTTON, self.on_submit)
        sizer.Add(self.btnSubmit, 0, wx.ALIGN_RIGHT | wx.ALL, 5)

        panel.SetSizer(sizer)

    def on_submit(self, event):
        file_name = self.txtName.GetValue().strip()
        file_content = self.txtContent.GetValue()

        if not file_name:
            ui.message("Please enter a file name.")
            return

        ui.message("Checking file...")
        url = f"https://api.github.com/repos/{urllib.parse.quote(self.owner)}/{urllib.parse.quote(self.repo)}/contents/"
        
        def check_cb(code, res):
            found = False
            existing_name = ""
            file_sha = ""
            if code == 200:
                try:
                    items = json.loads(res)
                    for item in items:
                        if item.get("name", "").lower() == file_name.lower():
                            found = True
                            existing_name = item.get("name")
                            file_sha = item.get("sha")
                            break
                except Exception:
                    pass

            if found:
                dlg = wx.MessageDialog(
                    self,
                    f"File '{existing_name}' already exists. Do you want to overwrite it?",
                    "File Exists",
                    wx.YES_NO | wx.ICON_QUESTION
                )
                if dlg.ShowModal() == wx.ID_YES:
                    self.save_file(file_name, file_content, file_sha)
                dlg.Destroy()
            else:
                self.save_file(file_name, file_content, None)

        http_request_async(url, "GET", token=self.token, callback=check_cb)

    def save_file(self, name, content, sha):
        ui.message("Saving file...")
        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        payload = {"message": "Created via NVDA GitHub Manager", "content": encoded}
        if sha:
            payload["sha"] = sha

        url = f"https://api.github.com/repos/{urllib.parse.quote(self.owner)}/{urllib.parse.quote(self.repo)}/contents/{urllib.parse.quote(name)}"
        
        def save_cb(code, res):
            if code in (200, 201):
                ui.message("File saved successfully!")
                self.Close()
                if self.on_finish:
                    self.on_finish()
            else:
                ui.message("Failed to save file.")

        http_request_async(url, "PUT", data=json.dumps(payload), token=self.token, callback=save_cb)


# ----------------------------------------------------------------------
# கணினி கோப்பு தேர்வி மற்றும் பதிவேற்றி (File Picker & Queue Uploader)
# ----------------------------------------------------------------------

class FileUploaderDialog(wx.Dialog):
    def __init__(self, parent, owner, repo, token, on_finish):
        super().__init__(parent, title="Upload Files to Repository", size=(500, 400))
        self.owner = owner
        self.repo = repo
        self.token = token
        self.on_finish = on_finish

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(panel, label="Select Local Files to Upload:"), 0, wx.ALL, 5)

        self.filePicker = wx.FilePickerCtrl(panel, style=wx.FC_OPEN | wx.FC_MULTIPLE)
        sizer.Add(self.filePicker, 0, wx.EXPAND | wx.ALL, 5)

        self.btnUpload = wx.Button(panel, label="Start Upload")
        self.btnUpload.Bind(wx.EVT_BUTTON, self.on_start_upload)
        sizer.Add(self.btnUpload, 0, wx.ALIGN_RIGHT | wx.ALL, 5)

        panel.SetSizer(sizer)

    def on_start_upload(self, event):
        paths = self.filePicker.GetPaths()
        if not paths:
            ui.message("No files selected.")
            return

        valid_files = []
        for p in paths:
            if os.path.exists(p) and os.path.isfile(p):
                if os.path.getsize(p) > 50 * 1024 * 1024:
                    ui.message(f"Skipping {os.path.basename(p)}: Exceeds 50MB limit.")
                else:
                    valid_files.append(p)

        if valid_files:
            self.process_queue(valid_files, 0)

    def process_queue(self, queue, index):
        if index >= len(queue):
            ui.message("All selected files processed successfully!")
            self.Close()
            if self.on_finish:
                self.on_finish()
            return

        file_path = queue[index]
        file_name = os.path.basename(file_path)

        ui.message(f"Checking file {index + 1}/{len(queue)} ({file_name})...")
        url = f"https://api.github.com/repos/{urllib.parse.quote(self.owner)}/{urllib.parse.quote(self.repo)}/contents/"

        def check_cb(code, res):
            found = False
            file_sha = ""
            if code == 200:
                try:
                    items = json.loads(res)
                    for item in items:
                        if item.get("name", "").lower() == file_name.lower():
                            found = True
                            file_sha = item.get("sha")
                            break
                except Exception:
                    pass

            if found:
                dlg = wx.MessageDialog(
                    self,
                    f"File '{file_name}' already exists. Overwrite?",
                    "File Exists",
                    wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION
                )
                res_code = dlg.ShowModal()
                dlg.Destroy()
                if res_code == wx.ID_YES:
                    self.upload_file(file_path, file_sha, queue, index)
                else:
                    self.process_queue(queue, index + 1)
            else:
                self.upload_file(file_path, None, queue, index)

        http_request_async(url, "GET", token=self.token, callback=check_cb)

    def upload_file(self, path, sha, queue, index):
        file_name = os.path.basename(path)
        ui.message(f"Encoding & Uploading {file_name}...")

        def thread_target():
            try:
                with open(path, "rb") as f:
                    encoded = base64.b64encode(f.read()).decode("utf-8")
                
                payload = {"message": "Uploaded via NVDA GitHub Manager", "content": encoded}
                if sha:
                    payload["sha"] = sha

                url = f"https://api.github.com/repos/{urllib.parse.quote(self.owner)}/{urllib.parse.quote(self.repo)}/contents/{urllib.parse.quote(file_name)}"
                
                def upload_cb(code, res):
                    self.process_queue(queue, index + 1)

                http_request_async(url, "PUT", data=json.dumps(payload), token=self.token, callback=upload_cb)
            except Exception as e:
                wx.CallAfter(ui.message, f"Failed to upload {file_name}: {str(e)}")
                wx.CallAfter(self.process_queue, queue, index + 1)

        threading.Thread(target=thread_target, daemon=True).start()


# ----------------------------------------------------------------------
# முதன்மை விருப்பத்தேர்வுகள் உரையாடல் (Main Options Dialog)
# ----------------------------------------------------------------------

class RepoOptionsDialog(wx.Dialog):
    def __init__(self, parent, owner, repo, is_private=None, token="", on_back=None, on_deleted=None):
        super().__init__(parent, title=f"More Options: {repo}", size=(450, 550))
        self.owner = owner
        self.repo = repo
        self.is_private = is_private
        self.token = token
        self.on_back = on_back
        self.on_deleted = on_deleted
        self.stars_count = 0
        self.is_starred = False

        self.init_ui()
        self.check_repo_info()

    def init_ui(self):
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.btnStar = wx.Button(panel, label="Checking Star Status...")
        self.btnStar.Enable(False)
        self.btnStar.Bind(wx.EVT_BUTTON, self.on_toggle_star)
        sizer.Add(self.btnStar, 0, wx.EXPAND | wx.ALL, 5)

        self.btnDesc = wx.Button(panel, label="Loading Description...")
        self.btnDesc.Enable(False)
        self.btnDesc.Bind(wx.EVT_BUTTON, self.on_edit_description)
        sizer.Add(self.btnDesc, 0, wx.EXPAND | wx.ALL, 5)

        self.btnDownload = wx.Button(panel, label="Download Repository (ZIP)")
        self.btnDownload.Bind(wx.EVT_BUTTON, self.on_download_repo)
        sizer.Add(self.btnDownload, 0, wx.EXPAND | wx.ALL, 5)

        self.btnCopyRepoUrl = wx.Button(panel, label="Copy Repo Link")
        self.btnCopyRepoUrl.Bind(wx.EVT_BUTTON, self.on_copy_repo_url)
        sizer.Add(self.btnCopyRepoUrl, 0, wx.EXPAND | wx.ALL, 5)

        self.btnCopyZipUrl = wx.Button(panel, label="Copy Zip Link")
        self.btnCopyZipUrl.Bind(wx.EVT_BUTTON, self.on_copy_zip_url)
        sizer.Add(self.btnCopyZipUrl, 0, wx.EXPAND | wx.ALL, 5)

        self.btnCreateText = wx.Button(panel, label="Create Text File")
        self.btnCreateText.Bind(wx.EVT_BUTTON, self.on_create_text_file)
        sizer.Add(self.btnCreateText, 0, wx.EXPAND | wx.ALL, 5)

        self.btnUploadFile = wx.Button(panel, label="Upload File From Computer Storage")
        self.btnUploadFile.Bind(wx.EVT_BUTTON, self.on_upload_storage_file)
        sizer.Add(self.btnUploadFile, 0, wx.EXPAND | wx.ALL, 5)

        self.btnRename = wx.Button(panel, label="Rename Repository")
        self.btnRename.Bind(wx.EVT_BUTTON, self.on_rename_repo)
        sizer.Add(self.btnRename, 0, wx.EXPAND | wx.ALL, 5)

        self.btnVisibility = wx.Button(panel, label="Toggle Visibility")
        self.btnVisibility.Bind(wx.EVT_BUTTON, self.on_toggle_visibility)
        sizer.Add(self.btnVisibility, 0, wx.EXPAND | wx.ALL, 5)

        self.btnDelete = wx.Button(panel, label="Delete Repository")
        self.btnDelete.Bind(wx.EVT_BUTTON, self.on_delete_repo)
        sizer.Add(self.btnDelete, 0, wx.EXPAND | wx.ALL, 5)

        self.btnClose = wx.Button(panel, label="Close Options")
        self.btnClose.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        sizer.Add(self.btnClose, 0, wx.EXPAND | wx.ALL, 5)

        panel.SetSizer(sizer)

    def check_repo_info(self):
        star_url = f"https://api.github.com/user/starred/{urllib.parse.quote(self.owner)}/{urllib.parse.quote(self.repo)}"
        repo_url = f"https://api.github.com/repos/{urllib.parse.quote(self.owner)}/{urllib.parse.quote(self.repo)}"

        def repo_cb(code, res):
            if code == 200:
                try:
                    obj = json.loads(res)
                    self.stars_count = obj.get("stargazers_count", 0)
                    self.is_private = obj.get("private", False)
                    desc = obj.get("description") or "No description provided"
                    self.btnDesc.SetLabel(f"Description: {desc}")
                    self.btnDesc.Enable(True)
                except Exception:
                    pass
            self.update_star_button()

        def star_cb(code, res):
            self.is_starred = (code == 204)
            http_request_async(repo_url, "GET", token=self.token, callback=repo_cb)

        http_request_async(star_url, "GET", token=self.token, callback=star_cb)

    def update_star_button(self):
        label = f"Unstar Repository ({self.stars_count} Stars)" if self.is_starred else f"Star Repository ({self.stars_count} Stars)"
        self.btnStar.SetLabel(label)
        self.btnStar.Enable(True)

    def on_toggle_star(self, event):
        self.btnStar.Enable(False)
        star_url = f"https://api.github.com/user/starred/{urllib.parse.quote(self.owner)}/{urllib.parse.quote(self.repo)}"
        method = "DELETE" if self.is_starred else "PUT"

        def cb(code, res):
            if code in (200, 201, 204):
                action = "unstarred" if self.is_starred else "starred"
                ui.message(f"Repository {action}!")
                self.check_repo_info()
            else:
                ui.message("Action failed. Check permissions.")
                self.btnStar.Enable(True)

        http_request_async(star_url, method, data="", token=self.token, callback=cb)

    def on_edit_description(self, event):
        dlg = wx.TextEntryDialog(self, "Type new description:", "Edit Description")
        if dlg.ShowModal() == wx.ID_OK:
            new_desc = dlg.GetValue()
            url = f"https://api.github.com/repos/{urllib.parse.quote(self.owner)}/{urllib.parse.quote(self.repo)}"
            data = json.dumps({"description": new_desc})
            
            def cb(code, res):
                if code == 200:
                    ui.message("Description updated successfully!")
                    self.check_repo_info()
                else:
                    ui.message("Failed to update description.")

            http_request_async(url, "PATCH", data=data, token=self.token, callback=cb)
        dlg.Destroy()

    def on_download_repo(self, event):
        url = f"https://api.github.com/repos/{urllib.parse.quote(self.owner)}/{urllib.parse.quote(self.repo)}"
        
        def cb(code, res):
            default_branch = "main"
            if code == 200:
                try:
                    default_branch = json.loads(res).get("default_branch", "main")
                except Exception:
                    pass
            
            zip_url = f"https://github.com/{self.owner}/{self.repo}/archive/refs/heads/{default_branch}.zip"
            ui.message(f"Downloading ZIP from {zip_url}...")
            
            def download_thread():
                try:
                    headers = get_auth_headers(self.token)
                    r = requests.get(zip_url, headers=headers, stream=True)
                    download_folder = os.path.join(os.path.expanduser("~"), "Downloads")
                    file_path = os.path.join(download_folder, f"{self.repo}-{default_branch}.zip")
                    
                    with open(file_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                    wx.CallAfter(ui.message, f"Download complete! Saved to {file_path}")
                except Exception as e:
                    wx.CallAfter(ui.message, f"Download failed: {str(e)}")

            threading.Thread(target=download_thread, daemon=True).start()

        http_request_async(url, "GET", token=self.token, callback=cb)

    def on_copy_repo_url(self, event):
        url = f"https://github.com/{self.owner}/{self.repo}"
        api.copyToClip(url)
        ui.message("Repo link copied to clipboard.")

    def on_copy_zip_url(self, event):
        url = f"https://github.com/{self.owner}/{self.repo}/archive/refs/heads/main.zip"
        api.copyToClip(url)
        ui.message("Zip link copied to clipboard.")

    def on_create_text_file(self, event):
        dlg = CreateTextFileDialog(self, self.owner, self.repo, self.token, on_finish=None)
        dlg.ShowModal()
        dlg.Destroy()

    def on_upload_storage_file(self, event):
        dlg = FileUploaderDialog(self, self.owner, self.repo, self.token, on_finish=None)
        dlg.ShowModal()
        dlg.Destroy()

    def on_rename_repo(self, event):
        dlg = wx.TextEntryDialog(self, "Enter new repository name:", "Rename Repository", value=self.repo)
        if dlg.ShowModal() == wx.ID_OK:
            new_name = dlg.GetValue().strip()
            if new_name and new_name != self.repo:
                url = f"https://api.github.com/repos/{urllib.parse.quote(self.owner)}/{urllib.parse.quote(self.repo)}"
                data = json.dumps({"name": new_name})

                def cb(code, res):
                    if code == 200:
                        ui.message("Repository renamed successfully!")
                        self.repo = new_name
                        self.SetTitle(f"More Options: {self.repo}")
                    else:
                        ui.message("Failed to rename repository.")

                http_request_async(url, "PATCH", data=data, token=self.token, callback=cb)
        dlg.Destroy()

    def on_toggle_visibility(self, event):
        current_vis = "Private" if self.is_private else "Public"
        target_vis = "Public" if self.is_private else "Private"
        
        dlg = wx.MessageDialog(
            self,
            f"Current visibility: {current_vis}. Change to {target_vis}?",
            "Toggle Visibility",
            wx.YES_NO | wx.ICON_QUESTION
        )
        if dlg.ShowModal() == wx.ID_YES:
            url = f"https://api.github.com/repos/{urllib.parse.quote(self.owner)}/{urllib.parse.quote(self.repo)}"
            data = json.dumps({"private": not self.is_private})

            def cb(code, res):
                if code == 200:
                    self.is_private = not self.is_private
                    ui.message(f"Visibility changed to {'Private' if self.is_private else 'Public'}")
                else:
                    ui.message("Failed to change visibility.")

            http_request_async(url, "PATCH", data=data, token=self.token, callback=cb)
        dlg.Destroy()

    def on_delete_repo(self, event):
        dlg = wx.MessageDialog(
            self,
            f"Are you sure you want to delete '{self.repo}'? This action CANNOT be undone.",
            "Confirm Repository Deletion",
            wx.YES_NO | wx.ICON_WARNING
        )
        if dlg.ShowModal() == wx.ID_YES:
            url = f"https://api.github.com/repos/{urllib.parse.quote(self.owner)}/{urllib.parse.quote(self.repo)}"
            
            def cb(code, res):
                if code in (200, 204):
                    ui.message("Repository deleted successfully!")
                    self.Close()
                    if self.on_deleted:
                        self.on_deleted()
                else:
                    ui.message("Failed to delete repository.")

            http_request_async(url, "DELETE", token=self.token, callback=cb)
        dlg.Destroy()


# உரையாடலை வெளியிலிருந்து அழைப்பதற்கான சார்பு
def show_options(owner, repo, is_private=False, token="", on_back=None, on_deleted=None):
    wx.CallAfter(lambda: RepoOptionsDialog(None, owner, repo, is_private, token, on_back, on_deleted).ShowModal())
