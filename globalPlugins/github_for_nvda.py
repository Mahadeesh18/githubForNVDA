import os
import sys
import wx
import gui
import ui
import globalPluginHandler
import scriptHandler

# Module Imports
from . import token_module
from . import profile_module
from . import my_repos
from . import public_repos
from . import about_module

ADDON_DIR = os.path.dirname(__file__)

class MainMenuDialog(wx.Dialog):
    def __init__(self, parent):
        super(MainMenuDialog, self).__init__(parent, title="GitHub for NVDA", size=(400, 380))
        
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Header Label
        lbl_title = wx.StaticText(self, label="GitHub for NVDA")
        font = lbl_title.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        lbl_title.SetFont(font)
        main_sizer.Add(lbl_title, 0, wx.ALL | wx.ALIGN_CENTER, 10)

        # Set / Edit Token Button
        btn_set_token = wx.Button(self, label="Set / Edit Personal Access Token")
        btn_set_token.Bind(wx.EVT_BUTTON, self.on_set_token)
        main_sizer.Add(btn_set_token, 0, wx.ALL | wx.EXPAND, 5)

        # My Profile Button
        btn_profile = wx.Button(self, label="My Profile")
        btn_profile.Bind(wx.EVT_BUTTON, self.on_profile)
        main_sizer.Add(btn_profile, 0, wx.ALL | wx.EXPAND, 5)

        # My Repositories Button
        btn_my_repos = wx.Button(self, label="My Repositories")
        btn_my_repos.Bind(wx.EVT_BUTTON, self.on_my_repos)
        main_sizer.Add(btn_my_repos, 0, wx.ALL | wx.EXPAND, 5)

        # Explore Public Repositories Button
        btn_public_repos = wx.Button(self, label="Explore Public Repositories")
        btn_public_repos.Bind(wx.EVT_BUTTON, self.on_public_repos)
        main_sizer.Add(btn_public_repos, 0, wx.ALL | wx.EXPAND, 5)

        # About & User Guide Button
        btn_about = wx.Button(self, label="About & User Guide")
        btn_about.Bind(wx.EVT_BUTTON, self.on_about)
        main_sizer.Add(btn_about, 0, wx.ALL | wx.EXPAND, 5)

        self.SetSizer(main_sizer)
        self.Centre()

    def on_set_token(self, event):
        self.Hide()
        token_module.show_token_edit_screen(self.show_main_screen)

    def on_profile(self, event):
        self.Hide()
        profile_module.show_profile_screen(self.show_main_screen)

    def on_my_repos(self, event):
        self.Hide()
        my_repos.show_my_repos(self.show_main_screen)

    def on_public_repos(self, event):
        self.Hide()
        public_repos.show_public_repos(self.show_main_screen)

    def on_about(self, event):
        self.Hide()
        about_module.show_about_screen(self.show_main_screen)

    def show_main_screen(self):
        self.Show()

def show_main_screen():
    # Folder renaming logic if needed locally
    old_folder = os.path.join(ADDON_DIR, "GitHub Manager")
    new_folder = os.path.join(ADDON_DIR, "GitHub for NVDA")
    if os.path.exists(old_folder):
        try:
            os.rename(old_folder, new_folder)
        except Exception:
            pass

    # Open main menu directly (NVDA addon store handles all updates automatically)
    open_main_dialog()

def open_main_dialog():
    gui.mainFrame.prePopup()
    dialog = MainMenuDialog(gui.mainFrame)
    dialog.ShowModal()
    gui.mainFrame.postPopup()

# ----------------------------------------------------------------------
# NVDA Global Plugin Integration & Shortcut Key Assignment
# ----------------------------------------------------------------------

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    """NVDA ஆட்-ஆனுக்கான குளோபல் பிளகின் வகுப்பு."""
    
    scriptCategory = "GitHub for NVDA"

    @scriptHandler.script(
        description="Opens the GitHub for NVDA Main Menu",
        gesture="kb:NVDA+Control+G"
    )
    def script_openGitHubToolkit(self, gesture):
        """NVDA+Control+G அழுத்தும்போது மெயின் மெனுவை திறக்கும்."""
        wx.CallAfter(show_main_screen)
