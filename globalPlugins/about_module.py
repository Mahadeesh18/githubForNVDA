import wx
import gui

class AboutDialog(wx.Dialog):
    def __init__(self, parent, show_main_screen_callback=None):
        super(AboutDialog, self).__init__(parent, title="About & User Guide", size=(600, 500))
        self.show_main_screen_callback = show_main_screen_callback
        
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Back / Close Button
        btn_back = wx.Button(self, label="Back to Main Menu")
        btn_back.Bind(wx.EVT_BUTTON, self.on_back)
        main_sizer.Add(btn_back, 0, wx.ALL | wx.EXPAND, 5)

        # Creator Info
        info_text = (
            "welcome to the Step-by-Step Complete Guide"
                    )
        lbl_info = wx.StaticText(self, label=info_text)
        main_sizer.Add(lbl_info, 0, wx.ALL | wx.EXPAND, 10)

        # User Guide Section
        guide_title = wx.StaticText(self, label="created by mahadeesh")
        font = guide_title.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        guide_title.SetFont(font)
        main_sizer.Add(guide_title, 0, wx.TOP | wx.LEFT | wx.RIGHT, 10)

        # Text Area for Steps (Read-only for screen reader compatibility)
        steps_content = self.get_guide_text()
        txt_guide = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY)
        txt_guide.SetValue(steps_content)
        main_sizer.Add(txt_guide, 1, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(main_sizer)
        self.Centre()

    def on_back(self, event):
        self.Close()
        if self.show_main_screen_callback:
            self.show_main_screen_callback()

    def get_guide_text(self):
        return (
            "Step 1: Setting Up Your Personal Access Token\n"
            "To use this extension, you must connect it to your GitHub account using a Personal Access Token (PAT). Open 'Set / Edit Personal Access Token' from the main menu. Paste your token (starting with ghp_...) into the text box. Tap 'Save Token'. The app will instantly verify your token with GitHub, display your username and account name upon success, and securely save it for future use.\n\n"
            
            "Step 2: Viewing & Managing Your GitHub Profile\n"
            "Tap 'My Profile' on the main menu to inspect your full GitHub account details. Here you can view your username, account type, display name, bio, location, public/private repository counts, starred repositories count, public/private gists, followers count, following count, GitHub plan type, and account creation date.\n"
            "- Editing Details: You can directly edit your Name, Bio, and Location by tapping their respective Edit buttons.\n"
            "- Starred Repositories: Tap 'View Starred Repositories' to browse all the repositories you have starred. You can sort them by Name or Date, search through them, and tap any repository to view its files or details.\n"
            "- Followers & Following: Tap 'View Followers List' or 'View Following List' to inspect who follows you or who you follow. In these lists, you can sort users alphabetically (A-Z or Z-A), search for specific users, and navigate through pages using the Previous and Next Page buttons. Tapping any user's name opens their detailed profile screen.\n\n"
            
            "Step 3: Viewing, Sorting & Searching Repositories\n"
            "Once your token is saved and verified, tap 'My Repositories' on the main menu to view all your public and private repositories.\n"
            "- Sorting Options: Tap 'Sort By' to organize your list by Name (A-Z / Z-A), Date (Newest / Oldest), or filter to show Public Only or Private Only.\n"
            "- Search Feature: Type any repository name in the search bar and tap 'Search' to quickly find a specific repository without scrolling.\n\n"
            
            "Step 4: Creating a New Repository\n"
            "Open 'My Repositories' from the main menu and tap 'Create New Repository' at the top of the screen.\n"
            "- Repository Name & Description: Enter your desired repository name in the first text box. Optionally, you can also write a detailed Description in the optional text box below it.\n"
            "- Visibility Selection: Tap 'Create', then choose whether you want your new repository to be Public or Private. The extension will create it instantly with your provided details.\n\n"
            
            "Step 5: Browsing, Sorting & Searching Repository Files\n"
            "Tap on any repository to enter its file browser and inspect all files and folders.\n"
            "- Sort & Search Files: Tap 'Sort By' to arrange files and folders by Name or Date. Use the search input box to search for any file name inside the current directory.\n"
            "- File Management: Tap on any file to edit its text content, rename it, copy its raw URL, or delete it. You can navigate through sub-folders seamlessly using the Back button.\n\n"
            
            "Step 6: Multi-Selection Mode & Bulk Deletion\n"
            "To manage multiple items quickly, press and hold (long-press) on any repository, folder, or file. This instantly enables Selection Mode! You can tap 'Select All' to choose every item on screen or check items individually, then tap 'Delete Selected Repositories' or 'Delete Selected Files' to delete them all in a single bulk operation.\n\n"
            
            "Step 7: Creating Text Files & Phone Storage Upload\n"
            "Inside any repository screen, tap 'More Options'. Choose 'Create Text File' to compose a file directly within the app. Or choose 'Upload File From Phone Storage' to browse your phone's internal storage, inspect file sizes, select multiple files (up to 50MB per file limit), and upload them directly to your repository. The app also remembers your last browsed path for convenience.\n\n"
            
            "Step 8: File Overwrite & Conflict Handling\n"
            "Whenever you create or upload a file that already exists in your GitHub repository, the extension automatically prompts you before making changes. You can choose 'Yes, Overwrite' to update the file, or 'Remove from list' / 'Cancel' to skip it safely without replacing your existing data.\n\n"
            
            "Step 9: Advanced Repository Options (My Repositories)\n"
            "Inside any of your own repositories, tap 'More Options' to access powerful repository controls:\n"
            "- Star / Unstar Repository: Tap to easily star or unstar the current repository and see its total star count.\n"
            "- View & Edit Description: Inspect the current description or tap to update / add a new description for your repository.\n"
            "- Download Repository: Tap 'Download Repository' to download the entire repository as a ZIP file directly into your phone's Download folder with live progress tracking.\n"
            "- Links & Settings: Copy direct repository links, copy ZIP download links, rename your repository, switch visibility between Public and Private, or permanently delete the entire repository.\n\n"
            
            "Step 10: Searching Public Repositories & Users\n"
            "Tap 'Explore Public Repositories' from the main menu to search across all of GitHub.\n"
            "- Flexible Searching: Enter any keyword, a specific GitHub username, or a direct repository link (such as owner/repo or full github.com/owner/repo link) into the search box and tap 'Search'.\n"
            "- Direct Link Parsing: When you paste a direct repository link, the extension automatically detects the owner and repo name, skipping general search to fetch the exact repository directly.\n\n"
            
            "Step 11: Managing Search History\n"
            "Your search queries are stored safely under 'Search History' for instant access:\n"
            "- Auto-Save Queries: Up to 5 of your most recent search queries are saved automatically on the Public Repositories screen.\n"
            "- Quick Re-Search: Tap any saved search query button to instantly run that search again without typing.\n"
            "- Removing History: Tap 'Delete' next to any query to remove it individually, or tap 'Clear All Search History' to wipe your entire search history at once.\n\n"
            
            "Step 12: Sorting & Navigating Public Search Results\n"
            "When repository search results appear on screen, you can organize and navigate through them effortlessly:\n"
            "- Sorting Results: Tap 'Sort By' to arrange search results by Name (A-Z or Z-A) or Date (Date Newest or Date Oldest).\n"
            "- Pagination: Results are displayed 10 items per page. Tap 'Next Search Result' to view the next page or 'Previous Search Result' to return to previous results.\n"
            "- Result Details: Each repository item clearly displays its full name, total star count, and short description directly on the list button.\n\n"
            
            "Step 13: Exploring Public Repository Details & Owner Profiles\n"
            "Tap any repository in the search results to enter its details screen:\n"
            "- Owner Details: The owner's username is clearly displayed. Tap 'View [Owner] Profile' to open their detailed account profile screen.\n"
            "- Description Display: View the complete repository description directly under the owner details.\n"
            "- Navigation: Use the top 'Back' button or your phone's Back Key to return to the search results or parent directory at any time.\n\n"
            
            "Step 14: Searching & Sorting Files in Public Repositories\n"
            "Inside any public repository file browser:\n"
            "- Folder & File Identifiers: Items are clearly labeled with '[Folder]' or '[File]' for clear accessibility and distinction.\n"
            "- Searching Files: Type a file name into 'Search files in folder...' and tap 'Search' to instantly filter items in the current directory.\n"
            "- Sorting Files: Tap 'Sort By' to organize files and folders by Name (A-Z / Z-A) or Date (Newest / Oldest). Folders always remain organized at the top for easy browsing.\n\n"
            
            "Step 15: Public Repository 'More Options', Downloading & Forking\n"
            "Inside any public repository screen, tap 'More Options' to access advanced repository tools:\n"
            "- Star / Unstar Toggle: Live star status and total count are fetched automatically. Tap 'Star Repository' or 'Unstar Repository' to update your star status directly on GitHub (Requires PAT).\n"
            "- Download Repository: Tap 'Download Repository' to download the full repository as a ZIP file into your phone's Download folder with live progress tracking.\n"
            "- Copy Links: Easily copy the direct GitHub repository web URL via 'Copy Repo Link' or copy the direct ZIP archive link via 'Copy Zip Link'.\n"
            "- Fork to My Repositories: Tap 'Fork to My Repositories' to clone and add this public repository directly into your personal GitHub account.\n\n"
            
            "Step 16: File Code Preview & Download Progress Tracker\n"
            "Tap on any file inside a public repository to open its view screen:\n"
            "- Base64 Code Preview: The app fetches and decodes Base64 content into readable UTF-8 text so you can preview code directly on screen.\n"
            "- Downloading Files: Tap 'Download File' to download the file directly into your phone's Download folder using Android's DownloadManager.\n"
            "- Live Progress Tracking: A dedicated screen tracks download progress in real-time (percentage or KB loaded).\n"
            "- Cancellation Dialog: Tap 'Cancel Download' to safely abort an active download with a confirmation prompt.\n\n"
            
            "Step 17: Public User Profiles & Social Actions\n"
            "When viewing any public user's profile:\n"
            "- Comprehensive Profile Information: View username, account type, display name, bio, location, public email, public repos count, gists count, followers, following, and formatted account creation date.\n"
            "- Follow & Unfollow Actions: Tap 'Follow' or 'Unfollow' to manage social connections directly. The button automatically checks and reflects your live follow status.\n"
            "- Personal Access Token Check: If you attempt to follow/unfollow without a saved PAT, the extension safely prompts you to save your token first.\n\n"
            
            "Step 18: Exploring User Followers & Following Networks\n"
            "On any user profile screen:\n"
            "- Network Lists: Tap 'View Followers List' or 'View Following List' to inspect who follows the user or who the user is following.\n"
            "- Alphabetical Sorting: Tap 'Sort By' to arrange user lists alphabetically (Name A-Z or Name Z-A).\n"
            "- User Search: Use 'Search user...' input box to filter users in the list by name.\n"
            "- Network Pagination: Use 'Previous Page' and 'Next Page' buttons to navigate seamlessly through large user networks (100 users per API page, 10 per local screen).\n"
            "- Profile Navigation: Tap any user's name in the list to open their complete profile screen instantly.\n\n"
            
            "Step 19: Easy Navigation & Closing Extension\n"
            "You can press your phone's Back Button at any moment to safely return to the previous menu. To exit, tap 'Close Extension' on the main menu.\n\n"
            
            "Step 20: Automatic Updates\n"
            "Whenever you launch the extension with an active internet connection, it automatically checks for new updates in the background. If an update is found, an update dialog will appear showing the latest changes ('What's New'). Simply tap 'Update Now' to download and install the update automatically, then tap 'Restart Extension' to reload and apply all changes smoothly."
        )

def show_about_screen(show_main_screen_callback=None):
    gui.mainFrame.prePopup()
    dialog = AboutDialog(gui.mainFrame, show_main_screen_callback)
    dialog.ShowModal()
    gui.mainFrame.postPopup()