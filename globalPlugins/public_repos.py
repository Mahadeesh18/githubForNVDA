import base64
import json
import math
import re
import urllib.parse
import urllib.request

from . import my_repos
from . import token_module
from . import utils

# Global variables
current_sort_option = "Name (A-Z)"
current_file_sort_option = "Name (A-Z)"
current_public_user_sort_option = "Name (A-Z)"
last_fetched_items = None

current_page_index = 0
page_size = 10
cached_query_for_pagination = ""

user_list_page_size = 100
current_users_list = []
current_users_page_index = 0
total_api_users_count = 0


def format_size(bytes_val):
    try:
        b = float(bytes_val)
    except Exception:
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


def format_accessible_date(iso_str):
    if not iso_str or iso_str in ["", "N/A"]:
        return "N/A"
    match = re.search(r"(\d+)-(\d+)-(\d+)T(\d+):(\d+)", iso_str)
    if not match:
        return iso_str

    y, m, d, h, min_val = match.groups()
    months = [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]
    m_int = int(m)
    month_name = months[m_int - 1] if 1 <= m_int <= 12 else m

    hour_num = int(h)
    ampm = "AM"
    if hour_num >= 12:
        ampm = "PM"
        if hour_num > 12:
            hour_num -= 12
    elif hour_num == 0:
        hour_num = 12

    return f"{int(d)} {month_name} {y} at {hour_num:02d}:{min_val} {ampm}"


def get_search_history():
    history_list = []
    try:
        sp = utils.service.getSharedPreferences(
            "public_repos_prefs", utils.Context.MODE_PRIVATE
        )
        json_str = sp.getString("search_history", "[]")
        json_arr = json.loads(json_str)
        history_list = list(json_arr)
    except Exception:
        pass
    return history_list


def save_search_history(history_list):
    try:
        json_str = json.dumps(history_list)
        sp = utils.service.getSharedPreferences(
            "public_repos_prefs", utils.Context.MODE_PRIVATE
        )
        editor = sp.edit()
        editor.putString("search_history", json_str)
        editor.commit()
    except Exception:
        pass


def add_query_to_history(query):
    if not query or not query.strip():
        return
    clean_query = query.strip()
    history_list = get_search_history()
    new_list = [clean_query]
    for item in history_list:
        if item != clean_query:
            new_list.append(item)
    while len(new_list) > 5:
        new_list.pop()
    save_search_history(new_list)


def delete_query_from_history(query):
    history_list = get_search_history()
    new_list = [item for item in history_list if item != query]
    save_search_history(new_list)


def clear_all_search_history():
    save_search_history([])


def sort_repositories_list(repo_list):
    if not repo_list:
        return []
    sorted_list = list(repo_list)
    if current_sort_option == "Name (A-Z)":
        sorted_list.sort(key=lambda x: str(x.get("full_name", "")).lower())
    elif current_sort_option == "Name (Z-A)":
        sorted_list.sort(
            key=lambda x: str(x.get("full_name", "")).lower(), reverse=True
        )
    elif current_sort_option == "Date Newest":
        sorted_list.sort(
            key=lambda x: str(x.get("updated_at", "")), reverse=True
        )
    elif current_sort_option == "Date Oldest":
        sorted_list.sort(key=lambda x: str(x.get("updated_at", "")))
    return sorted_list


def sort_files_list(files_list):
    if not files_list:
        return []

    folders = [v for v in files_list if v.get("type") == "dir"]
    files = [v for v in files_list if v.get("type") != "dir"]

    def get_sort_key(item):
        if current_file_sort_option in ["Name (A-Z)", "Name (Z-A)"]:
            return str(item.get("name", "")).lower()
        else:
            return str(item.get("sha", ""))

    reverse_flag = current_file_sort_option in ["Name (Z-A)", "Date Newest"]

    folders.sort(key=get_sort_key, reverse=reverse_flag)
    files.sort(key=get_sort_key, reverse=reverse_flag)

    return folders + files


def sort_public_users_list(users_list):
    if not users_list:
        return []
    sorted_list = list(users_list)
    if current_public_user_sort_option == "Name (A-Z)":
        sorted_list.sort(key=lambda x: str(x.get("login", "")).lower())
    elif current_public_user_sort_option == "Name (Z-A)":
        sorted_list.sort(
            key=lambda x: str(x.get("login", "")).lower(), reverse=True
        )
    return sorted_list


def http_public_request(url_str, method="GET", data=None, callback=None):
    def run_thread():
        code = -1
        response = ""
        try:
            req = urllib.request.Request(url_str, method=method)
            req.add_header("User-Agent", "GitHubManagerApp")
            req.add_header("Accept", "application/vnd.github.v3+json")

            saved_token = ""
            try:
                saved_token = utils.loadToken()
            except Exception:
                pass

            if saved_token and saved_token.strip():
                req.add_header("Authorization", f"token {saved_token.strip()}")

            body_bytes = None
            if method in ["POST", "PUT", "PATCH"] or (
                data is not None and data != ""
            ):
                send_data = data or ""
                if send_data:
                    req.add_header("Content-Type", "application/json")
                body_bytes = send_data.encode("utf-8")

            with urllib.request.urlopen(req, data=body_bytes, timeout=15) as res:
                code = res.getcode()
                response = res.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            code = e.code
            try:
                response = e.read().decode("utf-8")
            except Exception:
                response = ""
        except Exception:
            code = -1
            response = ""

        def run_on_main():
            if callback:
                callback(code, response)

        utils.Handler(utils.Looper.getMainLooper()).post(
            utils.Runnable({"run": run_on_main})
        )

    utils.Thread(utils.Runnable({"run": run_thread})).start()


def start_download_file(
    url_str, save_file_name, known_total_size, on_cancel, on_success
):
    is_cancelled = False
    handler = utils.Handler(utils.Looper.getMainLooper())
    active_cancel_dialog = [None]

    root = utils.LinearLayout(utils.service)
    root.setOrientation(utils.LinearLayout.VERTICAL)
    root.setBackgroundColor(utils.Color.BLACK)
    root.setPadding(20, 20, 20, 20)

    scroll = utils.ScrollView(utils.service)
    layout = utils.LinearLayout(utils.service)
    layout.setOrientation(utils.LinearLayout.VERTICAL)

    layout.addView(utils.createHeader("Downloading File"))

    txt_file = utils.TextView(utils.service)
    txt_file.setText(f"File: {save_file_name}")
    txt_file.setTextColor(utils.Color.WHITE)
    txt_file.setPadding(0, 10, 0, 10)
    layout.addView(txt_file)

    btn_status = utils.Button(utils.service)
    btn_status.setText("Downloading...")
    btn_status.setEnabled(False)
    layout.addView(btn_status)

    download_manager = utils.service.getSystemService(
        utils.Context.DOWNLOAD_SERVICE
    )
    download_id = -1

    try:
        request = utils.DownloadManager.Request(utils.Uri.parse(url_str))
        request.setNotificationVisibility(
            utils.DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED
        )
        request.setDestinationInExternalPublicDir(
            utils.Environment.DIRECTORY_DOWNLOADS, save_file_name
        )
        request.addRequestHeader("User-Agent", "GitHubManagerApp")

        saved_token = ""
        try:
            saved_token = utils.loadToken()
        except Exception:
            pass
        if saved_token and saved_token.strip():
            request.addRequestHeader(
                "Authorization", f"token {saved_token.strip()}"
            )

        download_id = download_manager.enqueue(request)
    except Exception:
        download_id = -1

    if download_id == -1:
        utils.Toast.makeText(
            utils.service, "Failed to start download.", utils.Toast.LENGTH_SHORT
        ).show()
        on_cancel()
        return

    def do_cancel_action():
        nonlocal is_cancelled
        if is_cancelled:
            return
        is_cancelled = True

        if active_cancel_dialog[0]:
            try:
                active_cancel_dialog[0].dismiss()
            except Exception:
                pass
            active_cancel_dialog[0] = None

        try:
            handler.removeCallbacksAndMessages(None)
        except Exception:
            pass

        try:
            long_array = utils.Array.newInstance(utils.Long.TYPE, 1)
            utils.Array.setLong(
                long_array, 0, utils.Long(download_id).longValue()
            )
            download_manager.remove(long_array)
        except Exception:
            pass

        utils.Toast.makeText(
            utils.service, "Download cancelled.", utils.Toast.LENGTH_SHORT
        ).show()
        on_cancel()

    def show_cancel_confirmation():
        try:
            if active_cancel_dialog[0]:
                active_cancel_dialog[0].dismiss()
                active_cancel_dialog[0] = None

            builder = utils.AlertDialog.Builder(utils.service)
            builder.setTitle("Cancel Download")
            builder.setMessage("Are you sure you want to cancel the download?")

            def on_pos_click(dialog, which):
                active_cancel_dialog[0] = None
                try:
                    dialog.dismiss()
                except Exception:
                    pass
                do_cancel_action()

            def on_neg_click(dialog, which):
                active_cancel_dialog[0] = None
                try:
                    dialog.dismiss()
                except Exception:
                    pass

            builder.setPositiveButton(
                "Yes", utils.DialogInterface.OnClickListener({"onClick": on_pos_click})
            )
            builder.setNegativeButton(
                "No", utils.DialogInterface.OnClickListener({"onClick": on_neg_click})
            )

            dlg = builder.create()
            try:
                if dlg.getWindow():
                    dlg.getWindow().setType(
                        utils.WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
                    )
            except Exception:
                pass
            active_cancel_dialog[0] = dlg
            dlg.show()
        except Exception:
            pass

    btn_cancel = utils.Button(utils.service)
    btn_cancel.setText("Cancel Download")
    btn_cancel.setOnClickListener(
        utils.View.OnClickListener({"onClick": lambda v: show_cancel_confirmation()})
    )
    layout.addView(btn_cancel)

    scroll.addView(layout)
    root.addView(scroll)

    utils.enableBackKey(root, show_cancel_confirmation)
    utils.setScreen(root)

    last_status_str = [""]

    def check_progress():
        if is_cancelled:
            return
        is_done = False
        is_success = False

        try:
            query = utils.DownloadManager.Query()
            id_array = utils.Array.newInstance(utils.Long.TYPE, 1)
            utils.Array.setLong(id_array, 0, utils.Long(download_id).longValue())
            query.setFilterById(id_array)

            cursor = download_manager.query(query)
            if cursor:
                if cursor.moveToFirst():
                    bytes_idx = cursor.getColumnIndex(
                        utils.DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR
                    )
                    total_idx = cursor.getColumnIndex(
                        utils.DownloadManager.COLUMN_TOTAL_SIZE_BYTES
                    )
                    status_idx = cursor.getColumnIndex(
                        utils.DownloadManager.COLUMN_STATUS
                    )

                    bytes_val = cursor.getLong(bytes_idx)
                    total_val = cursor.getLong(total_idx)
                    status = cursor.getInt(status_idx)

                    num_bytes = int(bytes_val) if bytes_val else 0
                    num_total = int(total_val) if total_val else 0

                    if num_total <= 0 and known_total_size and int(known_total_size) > 0:
                        num_total = int(known_total_size)

                    if num_total > 0:
                        status_str = f"Downloading {format_size(num_bytes)} / {format_size(num_total)}"
                    else:
                        status_str = f"Downloading {format_size(num_bytes)} / Unknown"

                    if status_str != last_status_str[0]:
                        last_status_str[0] = status_str
                        btn_status.setText(status_str)

                    if status == utils.DownloadManager.STATUS_SUCCESSFUL:
                        is_done = True
                        is_success = True
                        if num_total > 0:
                            btn_status.setText(
                                f"Downloading {format_size(num_total)} / {format_size(num_total)}"
                            )
                        else:
                            btn_status.setText(
                                f"Downloading {format_size(num_bytes)} / {format_size(num_bytes)}"
                            )
                    elif status == utils.DownloadManager.STATUS_FAILED:
                        is_done = True
                        is_success = False

                cursor.close()
        except Exception:
            pass

        if is_cancelled:
            return

        if is_done:
            try:
                handler.removeCallbacksAndMessages(None)
            except Exception:
                pass
            if active_cancel_dialog[0]:
                try:
                    active_cancel_dialog[0].dismiss()
                except Exception:
                    pass
                active_cancel_dialog[0] = None

            if is_success:
                utils.Toast.makeText(
                    utils.service,
                    "Download complete! Saved to Download folder.",
                    utils.Toast.LENGTH_LONG,
                ).show()
                on_success()
            else:
                utils.Toast.makeText(
                    utils.service, "Download failed.", utils.Toast.LENGTH_SHORT
                ).show()
                on_cancel()
        else:
            if not is_cancelled:
                handler.postDelayed(check_progress_runnable, 200)

    check_progress_runnable = utils.Runnable({"run": check_progress})
    handler.postDelayed(check_progress_runnable, 100)


def show_public_user_profile(target_username, on_back_to_parent):
    saved_token = utils.loadToken()
    utils.showLoading("Loading Profile...")

    url = f"https://api.github.com/users/{urllib.parse.quote(target_username)}"

    def on_res(code, res):
        try:
            if utils.hideLoading:
                utils.hideLoading()
        except Exception:
            pass

        if code == 200 and res:
            profile_data = {}
            try:
                obj = json.loads(res)
                profile_data["login"] = obj.get("login") or target_username
                profile_data["type"] = obj.get("type") or "User"
                profile_data["name"] = obj.get("name") or ""
                profile_data["bio"] = obj.get("bio") or ""
                profile_data["location"] = obj.get("location") or ""
                profile_data["email"] = obj.get("email") or ""
                profile_data["public_repos"] = str(obj.get("public_repos", 0))
                profile_data["public_gists"] = str(obj.get("public_gists", 0))
                profile_data["followers"] = str(obj.get("followers", 0))
                profile_data["following"] = str(obj.get("following", 0))
                profile_data["created_at"] = obj.get("created_at") or "N/A"
            except Exception:
                pass

            root = utils.LinearLayout(utils.service)
            root.setOrientation(utils.LinearLayout.VERTICAL)
            root.setBackgroundColor(utils.Color.BLACK)
            root.setPadding(20, 20, 20, 20)

            scroll = utils.ScrollView(utils.service)
            layout = utils.LinearLayout(utils.service)
            layout.setOrientation(utils.LinearLayout.VERTICAL)

            btn_back = utils.Button(utils.service)
            btn_back.setText("Back")
            btn_back.setOnClickListener(
                utils.View.OnClickListener(
                    {"onClick": lambda v: on_back_to_parent()}
                )
            )
            layout.addView(btn_back)

            layout.addView(
                utils.createHeader(f"{profile_data.get('login', '')}'s Profile")
            )

            details = [
                ("Username", profile_data.get("login")),
                ("Account Type", profile_data.get("type")),
                (
                    "Name",
                    profile_data.get("name")
                    if profile_data.get("name")
                    else "No Name Added",
                ),
                (
                    "Bio",
                    profile_data.get("bio")
                    if profile_data.get("bio")
                    else "No Bio Added",
                ),
                (
                    "Location",
                    profile_data.get("location")
                    if profile_data.get("location")
                    else "No Location Added",
                ),
                (
                    "Email",
                    profile_data.get("email")
                    if profile_data.get("email") and profile_data.get("email") != "null"
                    else "No Public Email",
                ),
                ("Public Repositories", profile_data.get("public_repos")),
                ("Public Gists", profile_data.get("public_gists")),
                ("Followers", profile_data.get("followers")),
                ("Following", profile_data.get("following")),
                (
                    "Account Created",
                    format_accessible_date(profile_data.get("created_at")),
                ),
            ]

            for item in details:
                txt = utils.TextView(utils.service)
                txt.setText(f"{item[0]}: {item[1]}")
                txt.setTextColor(utils.Color.WHITE)
                txt.setTextSize(16)
                txt.setPadding(10, 10, 10, 10)
                layout.addView(txt)

                if item[0] == "Followers":
                    btn_view_followers = utils.Button(utils.service)
                    btn_view_followers.setText("View Followers List")

                    def click_followers(v):
                        show_public_user_list_screen(
                            profile_data["login"],
                            "followers",
                            "Followers List",
                            lambda: show_public_user_profile(
                                target_username, on_back_to_parent
                            ),
                            1,
                            int(profile_data["followers"])
                            if profile_data["followers"].isdigit()
                            else 0,
                        )

                    btn_view_followers.setOnClickListener(
                        utils.View.OnClickListener({"onClick": click_followers})
                    )
                    layout.addView(btn_view_followers)

                elif item[0] == "Following":
                    btn_view_following = utils.Button(utils.service)
                    btn_view_following.setText("View Following List")

                    def click_following(v):
                        show_public_user_list_screen(
                            profile_data["login"],
                            "following",
                            "Following List",
                            lambda: show_public_user_profile(
                                target_username, on_back_to_parent
                            ),
                            1,
                            int(profile_data["following"])
                            if profile_data["following"].isdigit()
                            else 0,
                        )

                    btn_view_following.setOnClickListener(
                        utils.View.OnClickListener({"onClick": click_following})
                    )
                    layout.addView(btn_view_following)

            btn_copy_profile = utils.Button(utils.service)
            btn_copy_profile.setText("Copy Profile")

            def copy_prof_click(v):
                full_info = (
                    f"Username: {profile_data.get('login')}\n"
                    f"Account Type: {profile_data.get('type')}\n"
                    f"Name: {profile_data.get('name') or 'No Name Added'}\n"
                    f"Bio: {profile_data.get('bio') or 'No Bio Added'}\n"
                    f"Location: {profile_data.get('location') or 'No Location Added'}\n"
                    f"Email: {profile_data.get('email') if profile_data.get('email') and profile_data.get('email') != 'null' else 'No Public Email'}\n"
                    f"Public Repositories: {profile_data.get('public_repos')}\n"
                    f"Public Gists: {profile_data.get('public_gists')}\n"
                    f"Followers: {profile_data.get('followers')}\n"
                    f"Following: {profile_data.get('following')}\n"
                    f"Account Created: {format_accessible_date(profile_data.get('created_at'))}"
                )
                utils.service.copy(full_info)
                utils.Toast.makeText(
                    utils.service,
                    "Profile info copied successfully",
                    utils.Toast.LENGTH_SHORT,
                ).show()

            btn_copy_profile.setOnClickListener(
                utils.View.OnClickListener({"onClick": copy_prof_click})
            )
            layout.addView(btn_copy_profile)

            btn_follow = utils.Button(utils.service)
            btn_follow.setText("Checking Status...")
            btn_follow.setEnabled(False)
            layout.addView(btn_follow)

            def check_follow_status():
                if not saved_token:
                    btn_follow.setText("Follow")
                    btn_follow.setEnabled(True)
                    return
                check_url = f"https://api.github.com/user/following/{urllib.parse.quote(profile_data['login'])}"

                def on_check(f_code, f_res):
                    if f_code in [24, 204]:
                        btn_follow.setText("Unfollow")
                    else:
                        btn_follow.setText("Follow")
                    btn_follow.setEnabled(True)

                http_public_request(check_url, "GET", None, on_check)

            check_follow_status()

            def follow_click(v):
                cur_token = utils.loadToken()
                if not cur_token:
                    token_module.showTokenMissingScreen(
                        lambda: show_public_user_profile(
                            target_username, on_back_to_parent
                        )
                    )
                    return

                current_text = str(btn_follow.getText())
                target_url = f"https://api.github.com/user/following/{urllib.parse.quote(profile_data['login'])}"
                btn_follow.setEnabled(False)

                if current_text == "Unfollow":

                    def on_unfollow(act_code, act_res):
                        if act_code in [204, 200]:
                            utils.Toast.makeText(
                                utils.service,
                                f"Unfollowed {profile_data['login']}",
                                utils.Toast.LENGTH_SHORT,
                            ).show()
                            check_follow_status()
                        else:
                            utils.Toast.makeText(
                                utils.service,
                                "Failed to unfollow user.",
                                utils.Toast.LENGTH_SHORT,
                            ).show()
                            btn_follow.setEnabled(True)

                    http_public_request(target_url, "DELETE", None, on_unfollow)
                else:

                    def on_follow(act_code, act_res):
                        if act_code in [204, 200, 201]:
                            utils.Toast.makeText(
                                utils.service,
                                f"Followed {profile_data['login']}",
                                utils.Toast.LENGTH_SHORT,
                            ).show()
                            check_follow_status()
                        else:
                            utils.Toast.makeText(
                                utils.service,
                                "Failed to follow user.",
                                utils.Toast.LENGTH_SHORT,
                            ).show()
                            btn_follow.setEnabled(True)

                    http_public_request(target_url, "PUT", "", on_follow)

            btn_follow.setOnClickListener(
                utils.View.OnClickListener({"onClick": follow_click})
            )

            scroll.addView(layout)
            root.addView(scroll)
            utils.enableBackKey(root, on_back_to_parent)
            utils.setScreen(root)
        else:
            utils.Toast.makeText(
                utils.service,
                "Failed to load profile details.",
                utils.Toast.LENGTH_SHORT,
            ).show()

    http_public_request(url, "GET", None, on_res)


def show_public_user_list_screen(
    target_username, list_type, header_title, on_back_to_parent, page=1, total_count=0
):
    global current_users_list, current_users_page_index, total_api_users_count
    api_page = page or 1
    if total_count:
        total_api_users_count = total_count
    utils.showLoading(f"Loading {header_title}...")

    url = f"https://api.github.com/users/{urllib.parse.quote(target_username)}/{list_type}?per_page=100&page={api_page}"

    def on_res(code, res):
        global current_users_list, current_users_page_index
        try:
            if utils.hideLoading:
                utils.hideLoading()
        except Exception:
            pass

        raw_user_list = []
        if code == 200 and res:
            try:
                arr = json.loads(res)
                for obj in arr:
                    raw_user_list.append({"login": obj.get("login")})
            except Exception:
                pass

        current_users_list = raw_user_list
        current_users_page_index = 0

        root = utils.LinearLayout(utils.service)
        root.setOrientation(utils.LinearLayout.VERTICAL)
        root.setBackgroundColor(utils.Color.BLACK)
        root.setPadding(20, 20, 20, 20)

        scroll = utils.ScrollView(utils.service)
        layout = utils.LinearLayout(utils.service)
        layout.setOrientation(utils.LinearLayout.VERTICAL)

        btn_back = utils.Button(utils.service)
        btn_back.setText("Back")
        btn_back.setOnClickListener(
            utils.View.OnClickListener({"onClick": lambda v: on_back_to_parent()})
        )
        layout.addView(btn_back)

        layout.addView(utils.createHeader(header_title))

        btn_sort = utils.Button(utils.service)
        btn_sort.setText(f"Sort By: {current_public_user_sort_option}")
        layout.addView(btn_sort)

        edt_search = utils.EditText(utils.service)
        edt_search.setHint("Search user...")
        edt_search.setTextColor(utils.Color.WHITE)
        edt_search.setHintTextColor(utils.Color.GRAY)
        layout.addView(edt_search)

        btn_search = utils.Button(utils.service)
        btn_search.setText("Search")
        btn_search.setEnabled(False)
        layout.addView(btn_search)

        def text_changed(s):
            str_val = str(s).strip()
            btn_search.setEnabled(bool(str_val))

        edt_search.addTextChangedListener(
            utils.TextWatcher(
                {
                    "afterTextChanged": text_changed,
                    "beforeTextChanged": lambda s, start, count, after: None,
                    "onTextChanged": lambda s, start, before, count: None,
                }
            )
        )

        list_container = utils.LinearLayout(utils.service)
        list_container.setOrientation(utils.LinearLayout.VERTICAL)
        layout.addView(list_container)

        scroll.addView(layout)
        root.addView(scroll)

        def render_users(users):
            global current_users_page_index
            list_container.removeAllViews()
            sorted_users = sort_public_users_list(users)

            if not sorted_users:
                txt_empty = utils.TextView(utils.service)
                txt_empty.setText("No users found.")
                txt_empty.setTextColor(utils.Color.GRAY)
                txt_empty.setPadding(10, 20, 10, 20)
                list_container.addView(txt_empty)
            else:
                total_users = len(sorted_users)
                total_pages = math.ceil(total_users / user_list_page_size)
                if current_users_page_index >= total_pages:
                    current_users_page_index = total_pages - 1
                if current_users_page_index < 0:
                    current_users_page_index = 0

                start_index = current_users_page_index * user_list_page_size
                end_index = min(
                    start_index + user_list_page_size, total_users
                )

                for i in range(start_index, end_index):
                    u = sorted_users[i]
                    item_layout = utils.LinearLayout(utils.service)
                    item_layout.setOrientation(utils.LinearLayout.VERTICAL)
                    item_layout.setPadding(10, 15, 10, 15)

                    btn_user = utils.Button(utils.service)
                    btn_user.setText(u["login"])

                    def user_click(v, u_login=u["login"]):
                        cur_token = utils.loadToken()
                        if cur_token and cur_token.strip():

                            def on_my_user(u_code, u_res):
                                my_login = ""
                                if u_code == 200 and u_res:
                                    try:
                                        u_obj = json.loads(u_res)
                                        my_login = u_obj.get("login", "")
                                    except Exception:
                                        pass
                                if (
                                    my_login
                                    and u_login.lower() == my_login.lower()
                                ):
                                    try:
                                        builder = utils.AlertDialog.Builder(
                                            utils.service
                                        )
                                        builder.setTitle("Notice")
                                        builder.setMessage(
                                            "You can view your own profile by clicking My Profile."
                                        )

                                        def ok_click(dialog, which):
                                            try:
                                                dialog.dismiss()
                                            except Exception:
                                                pass

                                        builder.setPositiveButton(
                                            "OK",
                                            utils.DialogInterface.OnClickListener(
                                                {"onClick": ok_click}
                                            ),
                                        )
                                        dlg = builder.create()
                                        if dlg.getWindow():
                                            dlg.getWindow().setType(
                                                utils.WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
                                            )
                                        dlg.show()
                                    except Exception:
                                        pass
                                else:
                                    show_public_user_profile(
                                        u_login,
                                        lambda: show_public_user_list_screen(
                                            target_username,
                                            list_type,
                                            header_title,
                                            on_back_to_parent,
                                            api_page,
                                            total_api_users_count,
                                        ),
                                    )

                            http_public_request(
                                "https://api.github.com/user",
                                "GET",
                                None,
                                on_my_user,
                            )
                        else:
                            show_public_user_profile(
                                u_login,
                                lambda: show_public_user_list_screen(
                                    target_username,
                                    list_type,
                                    header_title,
                                    on_back_to_parent,
                                    api_page,
                                    total_api_users_count,
                                ),
                            )

                    btn_user.setOnClickListener(
                        utils.View.OnClickListener({"onClick": user_click})
                    )
                    item_layout.addView(btn_user)
                    list_container.addView(item_layout)

            pagination_layout = utils.LinearLayout(utils.service)
            pagination_layout.setOrientation(utils.LinearLayout.HORIZONTAL)
            pagination_layout.setPadding(0, 10, 0, 10)

            max_api_pages = max(1, math.ceil(total_api_users_count / 100))

            btn_prev = utils.Button(utils.service)
            btn_prev.setText("Previous Page")
            prev_params = utils.LinearLayout.LayoutParams(
                0, utils.LinearLayout.LayoutParams.WRAP_CONTENT, 1.0
            )
            btn_prev.setLayoutParams(prev_params)
            btn_prev.setEnabled(api_page > 1)

            def prev_click(v):
                if api_page > 1:
                    show_public_user_list_screen(
                        target_username,
                        list_type,
                        header_title,
                        on_back_to_parent,
                        api_page - 1,
                        total_api_users_count,
                    )

            btn_prev.setOnClickListener(
                utils.View.OnClickListener({"onClick": prev_click})
            )
            pagination_layout.addView(btn_prev)

            btn_next = utils.Button(utils.service)
            btn_next.setText("Next Page")
            next_params = utils.LinearLayout.LayoutParams(
                0, utils.LinearLayout.LayoutParams.WRAP_CONTENT, 1.0
            )
            btn_next.setLayoutParams(next_params)
            btn_next.setEnabled(api_page < max_api_pages or len(raw_user_list) == 100)

            def next_click(v):
                show_public_user_list_screen(
                    target_username,
                    list_type,
                    header_title,
                    on_back_to_parent,
                    api_page + 1,
                    total_api_users_count,
                )

            btn_next.setOnClickListener(
                utils.View.OnClickListener({"onClick": next_click})
            )
            pagination_layout.addView(btn_next)

            list_container.addView(pagination_layout)

        def sort_click(v):
            global current_public_user_sort_option, current_users_page_index
            options = ["Name (A-Z)", "Name (Z-A)", "Cancel"]
            try:
                builder = utils.AlertDialog.Builder(utils.service)
                builder.setTitle("Sort By")

                def item_click(dialog, which):
                    global current_public_user_sort_option, current_users_page_index
                    try:
                        dialog.dismiss()
                    except Exception:
                        pass
                    selected = options[which]
                    if selected != "Cancel":
                        current_public_user_sort_option = selected
                        btn_sort.setText(
                            f"Sort By: {current_public_user_sort_option}"
                        )
                        current_users_page_index = 0
                        render_users(current_users_list)

                builder.setItems(
                    options,
                    utils.DialogInterface.OnClickListener(
                        {"onClick": item_click}
                    ),
                )
                dlg = builder.create()
                if dlg.getWindow():
                    dlg.getWindow().setType(
                        utils.WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
                    )
                dlg.show()
            except Exception:
                pass

        btn_sort.setOnClickListener(
            utils.View.OnClickListener({"onClick": sort_click})
        )

        def search_click(v):
            global current_users_list, current_users_page_index
            q = str(edt_search.getText()).strip().lower()
            if not q:
                current_users_list = raw_user_list
            else:
                filtered = [
                    u
                    for u in raw_user_list
                    if q in str(u.get("login", "")).lower()
                ]
                current_users_list = filtered
            current_users_page_index = 0
            render_users(current_users_list)

        btn_search.setOnClickListener(
            utils.View.OnClickListener({"onClick": search_click})
        )

        if code == 200:
            render_users(current_users_list)
        else:
            list_container.removeAllViews()
            err_txt = utils.TextView(utils.service)
            err_txt.setText(f"Failed to load list (Error {code}).")
            err_txt.setTextColor(utils.Color.RED)
            list_container.addView(err_txt)

        utils.enableBackKey(root, on_back_to_parent)
        utils.setScreen(root)

    http_public_request(url, "GET", None, on_res)


def search_repositories(query, container, main_on_back):
    global cached_query_for_pagination, last_fetched_items, current_page_index
    container.removeAllViews()

    loading_text = utils.TextView(utils.service)
    loading_text.setText("Searching repositories, please wait...")
    loading_text.setTextColor(utils.Color.WHITE)
    loading_text.setPadding(0, 20, 0, 20)
    container.addView(loading_text)

    clean_query = query.strip()
    cached_query_for_pagination = clean_query

    owner_match, repo_match = None, None
    m1 = re.search(r"github\.com/([^/]+)/([^/%s/?#]+)", clean_query)
    if m1:
        owner_match, repo_match = m1.group(1), m1.group(2)
    else:
        m2 = re.search(r"^([^/]+)/([^/%s]+)$", clean_query)
        if m2:
            owner_match, repo_match = m2.group(1), m2.group(2)

    if owner_match and repo_match:
        repo_match = re.sub(r"\.git$", "", repo_match)
        direct_url = f"https://api.github.com/repos/{urllib.parse.quote(owner_match)}/{urllib.parse.quote(repo_match)}"

        def on_direct(code, response):
            global last_fetched_items, current_page_index
            container.removeAllViews()
            direct_list = []
            if code == 200 and response:
                try:
                    item_obj = json.loads(response)
                    repo_name = item_obj.get("name")
                    full_name = item_obj.get("full_name")
                    stars = item_obj.get("stargazers_count", 0)
                    desc = item_obj.get("description") or "No description"
                    default_branch = item_obj.get("default_branch", "main")
                    updated_at = item_obj.get("updated_at", "")

                    owner_name = owner_match
                    if "owner" in item_obj and isinstance(item_obj["owner"], dict):
                        owner_name = item_obj["owner"].get("login", owner_match)

                    direct_list.append(
                        {
                            "name": repo_name,
                            "full_name": full_name,
                            "stars": stars,
                            "description": desc,
                            "default_branch": default_branch,
                            "owner_login": owner_name,
                            "updated_at": updated_at,
                        }
                    )
                except Exception:
                    pass

            if direct_list:
                last_fetched_items = direct_list
                current_page_index = 0
                render_repositories(
                    container, direct_list, query, main_on_back
                )
            else:
                last_fetched_items = []
                current_page_index = 0
                empty_text = utils.TextView(utils.service)
                empty_text.setText(
                    f"No repository found for link: {clean_query}"
                )
                empty_text.setTextColor(utils.Color.YELLOW)
                container.addView(empty_text)

        http_public_request(direct_url, "GET", None, on_direct)
        return

    url1 = f"https://api.github.com/search/repositories?q={urllib.parse.quote(clean_query)}"

    def on_search1(code, response):
        global last_fetched_items, current_page_index
        items_list = []
        if code == 200 and response:
            try:
                json_obj = json.loads(response)
                items_arr = json_obj.get("items", [])
                for item_obj in items_arr:
                    repo_name = item_obj.get("name")
                    full_name = item_obj.get("full_name")
                    stars = item_obj.get("stargazers_count", 0)
                    desc = item_obj.get("description") or "No description"
                    default_branch = item_obj.get("default_branch", "main")
                    updated_at = item_obj.get("updated_at", "")

                    owner_name = ""
                    if "owner" in item_obj and isinstance(item_obj["owner"], dict):
                        owner_name = item_obj["owner"].get("login", "")

                    items_list.append(
                        {
                            "name": repo_name,
                            "full_name": full_name,
                            "stars": stars,
                            "description": desc,
                            "default_branch": default_branch,
                            "owner_login": owner_name,
                            "updated_at": updated_at,
                        }
                    )
            except Exception:
                pass

        if items_list:
            last_fetched_items = items_list
            current_page_index = 0
            render_repositories(container, items_list, query, main_on_back)
        else:
            url2 = f"https://api.github.com/users/{urllib.parse.quote(clean_query)}/repos"

            def on_search2(code2, response2):
                global last_fetched_items, current_page_index
                container.removeAllViews()
                user_items_list = []
                if code2 == 200 and response2:
                    try:
                        arr = json.loads(response2)
                        for item_obj in arr:
                            repo_name = item_obj.get("name")
                            full_name = item_obj.get("full_name")
                            stars = item_obj.get("stargazers_count", 0)
                            desc = item_obj.get("description") or "No description"
                            default_branch = item_obj.get(
                                "default_branch", "main"
                            )
                            updated_at = item_obj.get("updated_at", "")

                            owner_name = clean_query
                            if "owner" in item_obj and isinstance(
                                item_obj["owner"], dict
                            ):
                                owner_name = item_obj["owner"].get(
                                    "login", clean_query
                                )

                            user_items_list.append(
                                {
                                    "name": repo_name,
                                    "full_name": full_name,
                                    "stars": stars,
                                    "description": desc,
                                    "default_branch": default_branch,
                                    "owner_login": owner_name,
                                    "updated_at": updated_at,
                                }
                            )
                    except Exception:
                        pass

                if user_items_list:
                    last_fetched_items = user_items_list
                    current_page_index = 0
                    render_repositories(
                        container, user_items_list, query, main_on_back
                    )
                else:
                    last_fetched_items = []
                    current_page_index = 0
                    empty_text = utils.TextView(utils.service)
                    empty_text.setText(
                        f"No repository or username found for: {clean_query}"
                    )
                    empty_text.setTextColor(utils.Color.YELLOW)
                    container.addView(empty_text)

            http_public_request(url2, "GET", None, on_search2)

    http_public_request(url1, "GET", None, on_search1)


def render_repositories(container, items, query, main_on_back):
    global current_page_index
    container.removeAllViews()
    sorted_items = sort_repositories_list(items)
    if sorted_items:
        total_items = len(sorted_items)
        total_pages = math.ceil(total_items / page_size)
        if current_page_index >= total_pages:
            current_page_index = total_pages - 1
        if current_page_index < 0:
            current_page_index = 0

        start_index = current_page_index * page_size
        end_index = min(start_index + page_size, total_items)

        for i in range(start_index, end_index):
            item = sorted_items[i]
            btn_repo = utils.Button(utils.service)
            btn_repo.setText(
                f"{item.get('full_name')} ({item.get('stars')} Stars)\n{item.get('description')}"
            )

            def repo_click(v, curr_item=item):
                saved_token = utils.loadToken()

                def back_to_public_menu():
                    show_public_repos(main_on_back, query)

                if not saved_token:
                    show_repo_details(curr_item, back_to_public_menu, "")
                else:

                    def on_user_check(u_code, u_res):
                        current_username = ""
                        if u_code == 200 and u_res:
                            try:
                                u_obj = json.loads(u_res)
                                current_username = u_obj.get("login", "")
                            except Exception:
                                pass

                        if (
                            current_username
                            and str(curr_item.get("owner_login")).lower()
                            == str(current_username).lower()
                        ):
                            my_repos.showFilesList(
                                curr_item.get("owner_login"),
                                curr_item.get("name"),
                                "",
                                back_to_public_menu,
                            )
                        else:
                            show_repo_details(
                                curr_item, back_to_public_menu, ""
                            )

                    http_public_request(
                        "https://api.github.com/user",
                        "GET",
                        None,
                        on_user_check,
                    )

            btn_repo.setOnClickListener(
                utils.View.OnClickListener({"onClick": repo_click})
            )
            container.addView(btn_repo)

        pagination_layout = utils.LinearLayout(utils.service)
        pagination_layout.setOrientation(utils.LinearLayout.HORIZONTAL)
        pagination_layout.setPadding(0, 10, 0, 10)

        btn_prev = utils.Button(utils.service)
        btn_prev.setText("Previous Search Result")
        prev_params = utils.LinearLayout.LayoutParams(
            0, utils.LinearLayout.LayoutParams.WRAP_CONTENT, 1.0
        )
        btn_prev.setLayoutParams(prev_params)
        btn_prev.setEnabled(current_page_index > 0)

        def prev_click(v):
            global current_page_index
            if current_page_index > 0:
                current_page_index -= 1
                render_repositories(container, items, query, main_on_back)

        btn_prev.setOnClickListener(
            utils.View.OnClickListener({"onClick": prev_click})
        )
        pagination_layout.addView(btn_prev)

        btn_next = utils.Button(utils.service)
        btn_next.setText("Next Search Result")
        next_params = utils.LinearLayout.LayoutParams(
            0, utils.LinearLayout.LayoutParams.WRAP_CONTENT, 1.0
        )
        btn_next.setLayoutParams(next_params)
        btn_next.setEnabled(current_page_index < total_pages - 1)

        def next_click(v):
            global current_page_index
            if current_page_index < total_pages - 1:
                current_page_index += 1
                render_repositories(container, items, query, main_on_back)

        btn_next.setOnClickListener(
            utils.View.OnClickListener({"onClick": next_click})
        )
        pagination_layout.addView(btn_next)

        container.addView(pagination_layout)
    else:
        empty_text = utils.TextView(utils.service)
        empty_text.setText("No repositories found.")
        empty_text.setTextColor(utils.Color.YELLOW)
        container.addView(empty_text)


def show_public_repos(main_on_back, initial_query=""):
    global current_sort_option
    root = utils.LinearLayout(utils.service)
    root.setOrientation(utils.LinearLayout.VERTICAL)
    root.setBackgroundColor(utils.Color.BLACK)
    root.setPadding(20, 20, 20, 20)

    scroll = utils.ScrollView(utils.service)
    layout = utils.LinearLayout(utils.service)
    layout.setOrientation(utils.LinearLayout.VERTICAL)

    btn_back = utils.Button(utils.service)
    btn_back.setText("Back")
    btn_back.setOnClickListener(
        utils.View.OnClickListener({"onClick": lambda v: main_on_back()})
    )
    layout.addView(btn_back)

    layout.addView(utils.createHeader("Public Repositories"))

    results_container = utils.LinearLayout(utils.service)
    results_container.setOrientation(utils.LinearLayout.VERTICAL)

    btn_sort = utils.Button(utils.service)
    btn_sort.setText(f"Sort By: {current_sort_option}")

    def sort_click(v):
        global current_sort_option
        options = [
            "Name (A-Z)",
            "Name (Z-A)",
            "Date Newest",
            "Date Oldest",
            "Cancel",
        ]
        try:
            builder = utils.AlertDialog.Builder(utils.service)
            builder.setTitle("Sort By")

            def item_click(dialog, which):
                global current_sort_option
                try:
                    dialog.dismiss()
                except Exception:
                    pass
                selected = options[which]
                if selected != "Cancel":
                    current_sort_option = selected
                    btn_sort.setText(f"Sort By: {current_sort_option}")
                    if last_fetched_items:
                        curr_q = str(edt_search.getText()).strip()
                        render_repositories(
                            results_container,
                            last_fetched_items,
                            curr_q,
                            main_on_back,
                        )

            builder.setItems(
                options,
                utils.DialogInterface.OnClickListener({"onClick": item_click}),
            )
            dlg = builder.create()
            if dlg.getWindow():
                dlg.getWindow().setType(
                    utils.WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
                )
            dlg.show()
        except Exception:
            pass

    btn_sort.setOnClickListener(
        utils.View.OnClickListener({"onClick": sort_click})
    )
    layout.addView(btn_sort)

    edt_search = utils.EditText(utils.service)
    edt_search.setHint("Type repository, user name or link...")
    edt_search.setTextColor(utils.Color.WHITE)
    edt_search.setHintTextColor(utils.Color.GRAY)
    if initial_query:
        edt_search.setText(initial_query)
    layout.addView(edt_search)

    btn_search = utils.Button(utils.service)
    btn_search.setText("Search")
    btn_search.setEnabled(bool(initial_query))

    def on_search_text_changed(s):
        q = str(edt_search.getText()).strip()
        btn_search.setEnabled(bool(q))

    edt_search.addTextChangedListener(
        utils.TextWatcher(
            {
                "onTextChanged": lambda s, start, before, count: on_search_text_changed(
                    s
                ),
                "beforeTextChanged": lambda s, start, count, after: None,
                "afterTextChanged": lambda s: None,
            }
        )
    )
    layout.addView(btn_search)

    txt_header = utils.TextView(utils.service)
    txt_header.setText("Search History")
    txt_header.setTextColor(utils.Color.YELLOW)
    txt_header.setPadding(0, 15, 0, 5)

    history_container = utils.LinearLayout(utils.service)
    history_container.setOrientation(utils.LinearLayout.VERTICAL)

    def render_history():
        history_container.removeAllViews()
        history_list = get_search_history()

        if not history_list:
            txt_empty = utils.TextView(utils.service)
            txt_empty.setText("No Search History")
            txt_empty.setTextColor(utils.Color.GRAY)
            txt_empty.setPadding(0, 5, 0, 15)
            history_container.addView(txt_empty)
        else:
            btn_clear_all = utils.Button(utils.service)
            btn_clear_all.setText("Clear All Search History")

            def clear_click(v):
                clear_all_search_history()
                show_public_repos(main_on_back, initial_query)

            btn_clear_all.setOnClickListener(
                utils.View.OnClickListener({"onClick": clear_click})
            )
            history_container.addView(btn_clear_all)

            for h_query in history_list:
                row_layout = utils.LinearLayout(utils.service)
                row_layout.setOrientation(utils.LinearLayout.HORIZONTAL)
                row_layout.setPadding(0, 5, 0, 5)

                btn_item = utils.Button(utils.service)
                btn_item.setText(h_query)
                item_params = utils.LinearLayout.LayoutParams(
                    0, utils.LinearLayout.LayoutParams.WRAP_CONTENT, 1.0
                )
                btn_item.setLayoutParams(item_params)

                def item_click_fn(v, q=h_query):
                    add_query_to_history(q)
                    show_public_repos(main_on_back, q)

                btn_item.setOnClickListener(
                    utils.View.OnClickListener({"onClick": item_click_fn})
                )
                row_layout.addView(btn_item)

                btn_delete = utils.Button(utils.service)
                btn_delete.setText("Delete")
                del_params = utils.LinearLayout.LayoutParams(
                    utils.LinearLayout.LayoutParams.WRAP_CONTENT,
                    utils.LinearLayout.LayoutParams.WRAP_CONTENT,
                )
                btn_delete.setLayoutParams(del_params)

                def delete_click_fn(v, q=h_query):
                    delete_query_from_history(q)
                    show_public_repos(main_on_back, initial_query)

                btn_delete.setOnClickListener(
                    utils.View.OnClickListener({"onClick": delete_click_fn})
                )
                row_layout.addView(btn_delete)

                history_container.addView(row_layout)

    if not initial_query:
        layout.addView(txt_header)
        layout.addView(history_container)
        render_history()

    layout.addView(results_container)

    def do_search_click(v):
        query = str(edt_search.getText()).strip()
        if query:
            try:
                layout.removeView(txt_header)
                layout.removeView(history_container)
            except Exception:
                pass
            add_query_to_history(query)
            search_repositories(query, results_container, main_on_back)

    btn_search.setOnClickListener(
        utils.View.OnClickListener({"onClick": do_search_click})
    )

    scroll.addView(layout)
    root.addView(scroll)

    utils.enableBackKey(root, main_on_back)
    utils.setScreen(root)

    if initial_query:
        add_query_to_history(initial_query)
        if (
            last_fetched_items
            and cached_query_for_pagination == initial_query.strip()
        ):
            render_repositories(
                results_container, last_fetched_items, initial_query, main_on_back
            )
        else:
            search_repositories(initial_query, results_container, main_on_back)


def show_repo_details(item, on_back_to_search, path=""):
    current_path = path or ""
    root = utils.LinearLayout(utils.service)
    root.setOrientation(utils.LinearLayout.VERTICAL)
    root.setBackgroundColor(utils.Color.BLACK)
    root.setPadding(20, 20, 20, 20)

    scroll = utils.ScrollView(utils.service)
    layout = utils.LinearLayout(utils.service)
    layout.setOrientation(utils.LinearLayout.VERTICAL)

    btn_top_back = utils.Button(utils.service)
    btn_top_back.setText("Back")

    def handle_back_action():
        if current_path:
            parent_match = re.search(r"(.+)/[^/]+$", current_path)
            parent_path = parent_match.group(1) if parent_match else ""
            show_repo_details(item, on_back_to_search, parent_path)
        else:
            if callable(on_back_to_search):
                on_back_to_search()

    btn_top_back.setOnClickListener(
        utils.View.OnClickListener({"onClick": lambda v: handle_back_action()})
    )
    layout.addView(btn_top_back)

    layout.addView(utils.createHeader(item.get("name") or "Repository"))

    if not current_path:
        owner_name_val = str(
            item.get("owner_login") if item.get("owner_login") else "Unknown"
        )
        txt_owner = utils.TextView(utils.service)
        txt_owner.setText(f"Owner: {owner_name_val}")
        txt_owner.setTextColor(utils.Color.WHITE)
        txt_owner.setPadding(0, 10, 0, 5)
        layout.addView(txt_owner)

        btn_view_owner = utils.Button(utils.service)
        btn_view_owner.setText(f"View {owner_name_val} Profile")

        def view_owner_click(v):
            show_public_user_profile(
                owner_name_val,
                lambda: show_repo_details(
                    item, on_back_to_search, current_path
                ),
            )

        btn_view_owner.setOnClickListener(
            utils.View.OnClickListener({"onClick": view_owner_click})
        )
        layout.addView(btn_view_owner)

        txt_desc = utils.TextView(utils.service)
        desc_str = (
            item.get("description")
            if item.get("description")
            else "No description provided"
        )
        txt_desc.setText(f"Description: {desc_str}")
        txt_desc.setTextColor(utils.Color.LTGRAY)
        txt_desc.setPadding(0, 0, 0, 15)
        layout.addView(txt_desc)

    edt_search_file = utils.EditText(utils.service)
    edt_search_file.setHint("Search files in folder...")
    edt_search_file.setTextColor(utils.Color.WHITE)
    edt_search_file.setHintTextColor(utils.Color.GRAY)
    layout.addView(edt_search_file)

    btn_search_file = utils.Button(utils.service)
    btn_search_file.setText("Search")
    btn_search_file.setEnabled(False)
    layout.addView(btn_search_file)

    btn_sort_files = utils.Button(utils.service)
    btn_sort_files.setText(f"Sort By: {current_file_sort_option}")
    layout.addView(btn_sort_files)

    btn_more_options = utils.Button(utils.service)
    btn_more_options.setText("More Options")
    btn_more_options.setOnClickListener(
        utils.View.OnClickListener(
            {
                "onClick": lambda v: show_more_options(
                    item, on_back_to_search, current_path
                )
            }
        )
    )
    layout.addView(btn_more_options)

    txt_files_header = utils.TextView(utils.service)
    txt_files_header.setText(
        f"Files & Folders {f'({current_path})' if current_path else ''}"
    )
    txt_files_header.setTextColor(utils.Color.YELLOW)
    txt_files_header.setPadding(0, 15, 0, 10)
    layout.addView(txt_files_header)

    files_container = utils.LinearLayout(utils.service)
    files_container.setOrientation(utils.LinearLayout.VERTICAL)
    layout.addView(files_container)

    scroll.addView(layout)
    root.addView(scroll)

    utils.enableBackKey(root, handle_back_action)
    utils.setScreen(root)

    raw_files_list = []

    def render_current_files(list_to_render):
        files_container.removeAllViews()
        sorted_list = sort_files_list(list_to_render)
        if not sorted_list:
            empty_text = utils.TextView(utils.service)
            empty_text.setText("No files or folders found.")
            empty_text.setTextColor(utils.Color.GRAY)
            files_container.addView(empty_text)
            return

        for f_item in sorted_list:
            btn_item = utils.Button(utils.service)
            if f_item.get("type") == "dir":
                btn_item.setText(f"[Folder] {f_item.get('name')}")
            else:
                btn_item.setText(f"[File] {f_item.get('name')}")

            def item_click(v, curr_f=f_item):
                if curr_f.get("type") == "dir":
                    show_repo_details(
                        item, on_back_to_search, curr_f.get("path")
                    )
                else:
                    show_file_view(
                        item,
                        curr_f.get("path"),
                        curr_f.get("name"),
                        on_back_to_search,
                        current_path,
                    )

            btn_item.setOnClickListener(
                utils.View.OnClickListener({"onClick": item_click})
            )
            files_container.addView(btn_item)

    def file_text_changed(s):
        q_text = str(edt_search_file.getText())
        btn_search_file.setEnabled(bool(q_text.strip()))

    edt_search_file.addTextChangedListener(
        utils.TextWatcher(
            {
                "onTextChanged": lambda s, start, before, count: file_text_changed(
                    s
                ),
                "beforeTextChanged": lambda s, start, count, after: None,
                "afterTextChanged": lambda s: None,
            }
        )
    )

    def search_file_click(v):
        q_text = str(edt_search_file.getText()).strip()
        if q_text:
            lower_q = q_text.lower()
            filtered = [
                f
                for f in raw_files_list
                if lower_q in str(f.get("name", "")).lower()
            ]
            render_current_files(filtered)

    btn_search_file.setOnClickListener(
        utils.View.OnClickListener({"onClick": search_file_click})
    )

    def sort_files_click(v):
        global current_file_sort_option
        options = [
            "Name (A-Z)",
            "Name (Z-A)",
            "Date Newest",
            "Date Oldest",
            "Cancel",
        ]
        try:
            builder = utils.AlertDialog.Builder(utils.service)
            builder.setTitle("Sort By")

            def item_click(dialog, which):
                global current_file_sort_option
                try:
                    dialog.dismiss()
                except Exception:
                    pass
                selected = options[which]
                if selected != "Cancel":
                    current_file_sort_option = selected
                    btn_sort_files.setText(
                        f"Sort By: {current_file_sort_option}"
                    )
                    q_text = str(edt_search_file.getText()).strip()
                    if q_text:
                        lower_q = q_text.lower()
                        filtered = [
                            f
                            for f in raw_files_list
                            if lower_q in str(f.get("name", "")).lower()
                        ]
                        render_current_files(filtered)
                    else:
                        render_current_files(raw_files_list)

            builder.setItems(
                options,
                utils.DialogInterface.OnClickListener({"onClick": item_click}),
            )
            dlg = builder.create()
            if dlg.getWindow():
                dlg.getWindow().setType(
                    utils.WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
                )
            dlg.show()
        except Exception:
            pass

    btn_sort_files.setOnClickListener(
        utils.View.OnClickListener({"onClick": sort_files_click})
    )

    url = f"https://api.github.com/repos/{urllib.parse.quote(item.get('owner_login'))}/{urllib.parse.quote(item.get('name'))}/contents/{urllib.parse.quote(current_path)}"

    def on_files_res(code, response):
        nonlocal raw_files_list
        files_container.removeAllViews()
        if code != 200 or not response:
            err_text = utils.TextView(utils.service)
            err_text.setText(f"Failed to load files (Error Code: {code}).")
            err_text.setTextColor(utils.Color.RED)
            files_container.addView(err_text)
            return

        raw_files_list = []
        try:
            arr = json.loads(response)
            for obj in arr:
                raw_files_list.append(
                    {
                        "name": obj.get("name"),
                        "type": obj.get("type"),
                        "path": obj.get("path"),
                        "sha": obj.get("sha", ""),
                        "size": obj.get("size", 0),
                    }
                )
        except Exception:
            pass

        if not raw_files_list:
            empty_text = utils.TextView(utils.service)
            empty_text.setText("This folder is empty.")
            empty_text.setTextColor(utils.Color.GRAY)
            files_container.addView(empty_text)
            return

        render_current_files(raw_files_list)

    http_public_request(url, "GET", None, on_files_res)


def show_more_options(item, on_back_to_search, current_path):
    root = utils.LinearLayout(utils.service)
    root.setOrientation(utils.LinearLayout.VERTICAL)
    root.setBackgroundColor(utils.Color.BLACK)
    root.setPadding(20, 20, 20, 20)

    scroll = utils.ScrollView(utils.service)
    layout = utils.LinearLayout(utils.service)
    layout.setOrientation(utils.LinearLayout.VERTICAL)

    btn_back = utils.Button(utils.service)
    btn_back.setText("Back to Repository")
    btn_back.setOnClickListener(
        utils.View.OnClickListener(
            {
                "onClick": lambda v: show_repo_details(
                    item, on_back_to_search, current_path
                )
            }
        )
    )
    layout.addView(btn_back)

    layout.addView(utils.createHeader(f"More Options: {item.get('name')}"))

    btn_star = utils.Button(utils.service)
    btn_star.setText("Checking Star Status...")
    btn_star.setEnabled(False)
    layout.addView(btn_star)

    def update_star_button_state(is_starred, star_count):
        item["stars"] = star_count
        if is_starred:
            btn_star.setText(f"Unstar Repository ({star_count} Stars)")
        else:
            btn_star.setText(f"Star Repository ({star_count} Stars)")
        btn_star.setEnabled(True)

    def check_star_status():
        url = f"https://api.github.com/user/starred/{urllib.parse.quote(item.get('owner_login'))}/{urllib.parse.quote(item.get('name'))}"

        def on_check(code, response):
            is_starred = code == 204
            count_url = f"https://api.github.com/repos/{urllib.parse.quote(item.get('owner_login'))}/{urllib.parse.quote(item.get('name'))}"

            def on_count(c_code, c_resp):
                current_stars = item.get("stars", 0)
                if c_code == 200 and c_resp:
                    try:
                        obj = json.loads(c_resp)
                        current_stars = obj.get("stargazers_count", current_stars)
                    except Exception:
                        pass
                update_star_button_state(is_starred, current_stars)

            http_public_request(count_url, "GET", None, on_count)

        http_public_request(url, "GET", None, on_check)

    if not utils.loadToken():
        btn_star.setText(f"Star Repository ({item.get('stars', 0)} Stars)")
        btn_star.setEnabled(True)
    else:
        check_star_status()

    def star_click(v):
        if not utils.loadToken():
            token_module.showTokenMissingScreen(
                lambda: show_more_options(item, on_back_to_search, current_path)
            )
            return

        url = f"https://api.github.com/user/starred/{urllib.parse.quote(item.get('owner_login'))}/{urllib.parse.quote(item.get('name'))}"
        btn_star.setEnabled(False)

        def on_get_star(code, response):
            is_currently_starred = code == 204
            method = "DELETE" if is_currently_starred else "PUT"

            def on_act_star(act_code, act_resp):
                if act_code in [204, 200]:
                    check_star_status()
                else:
                    utils.Toast.makeText(
                        utils.service,
                        "Failed to update star status.",
                        utils.Toast.LENGTH_SHORT,
                    ).show()
                    btn_star.setEnabled(True)

            http_public_request(url, method, "", on_act_star)

        http_public_request(url, "GET", None, on_get_star)

    btn_star.setOnClickListener(
        utils.View.OnClickListener({"onClick": star_click})
    )

    btn_download_repo = utils.Button(utils.service)
    btn_download_repo.setText("Download Repository")

    def download_repo_click(v):
        default_branch = item.get("default_branch", "main")
        url = f"https://api.github.com/repos/{urllib.parse.quote(item.get('owner_login'))}/{urllib.parse.quote(item.get('name'))}"

        def on_branch_res(b_code, b_res):
            nonlocal default_branch
            repo_size_in_bytes = 0
            if b_code == 200 and b_res:
                try:
                    b_obj = json.loads(b_res)
                    default_branch = b_obj.get("default_branch", default_branch)
                    size_kb = b_obj.get("size", 0)
                    if size_kb > 0:
                        repo_size_in_bytes = size_kb * 1024
                except Exception:
                    pass
            zip_url = f"https://github.com/{item.get('owner_login')}/{item.get('name')}/archive/refs/heads/{default_branch}.zip"
            file_name = f"{item.get('name')}-{default_branch}.zip"

            start_download_file(
                zip_url,
                file_name,
                repo_size_in_bytes,
                lambda: show_more_options(item, on_back_to_search, current_path),
                lambda: show_more_options(item, on_back_to_search, current_path),
            )

        http_public_request(url, "GET", None, on_branch_res)

    btn_download_repo.setOnClickListener(
        utils.View.OnClickListener({"onClick": download_repo_click})
    )
    layout.addView(btn_download_repo)

    btn_copy_repo_url = utils.Button(utils.service)
    btn_copy_repo_url.setText("Copy Repo Link")

    def copy_repo_click(v):
        repo_url = f"https://github.com/{item.get('owner_login')}/{item.get('name')}"
        utils.service.copy(repo_url)

    btn_copy_repo_url.setOnClickListener(
        utils.View.OnClickListener({"onClick": copy_repo_click})
    )
    layout.addView(btn_copy_repo_url)

    btn_copy_zip_url = utils.Button(utils.service)
    btn_copy_zip_url.setText("Copy Zip Link")

    def copy_zip_click(v):
        default_branch = item.get("default_branch", "main")
        zip_url = f"https://github.com/{item.get('owner_login')}/{item.get('name')}/archive/refs/heads/{default_branch}.zip"
        utils.service.copy(zip_url)

    btn_copy_zip_url.setOnClickListener(
        utils.View.OnClickListener({"onClick": copy_zip_click})
    )
    layout.addView(btn_copy_zip_url)

    btn_fork_repo = utils.Button(utils.service)
    btn_fork_repo.setText("Fork to My Repositories")

    def fork_click(v):
        if not utils.loadToken():
            token_module.showTokenMissingScreen(
                lambda: show_more_options(item, on_back_to_search, current_path)
            )
            return

        utils.showLoading("Forking repository...")
        fork_url = f"https://api.github.com/repos/{urllib.parse.quote(item.get('owner_login'))}/{urllib.parse.quote(item.get('name'))}/forks"

        def on_fork_res(f_code, f_res):
            try:
                if utils.hideLoading:
                    utils.hideLoading()
            except Exception:
                pass

            if f_code in [202, 200, 201]:
                utils.Toast.makeText(
                    utils.service,
                    "Repository successfully forked!",
                    utils.Toast.LENGTH_LONG,
                ).show()
            else:
                utils.Toast.makeText(
                    utils.service,
                    f"Failed to fork repository (Error {f_code}).",
                    utils.Toast.LENGTH_SHORT,
                ).show()

            show_more_options(item, on_back_to_search, current_path)

        http_public_request(fork_url, "POST", "", on_fork_res)

    btn_fork_repo.setOnClickListener(
        utils.View.OnClickListener({"onClick": fork_click})
    )
    layout.addView(btn_fork_repo)

    scroll.addView(layout)
    root.addView(scroll)

    utils.enableBackKey(
        root, lambda: show_repo_details(item, on_back_to_search, current_path)
    )
    utils.setScreen(root)


def show_file_view(item, file_path, file_name, on_back_to_search, current_path):
    root = utils.LinearLayout(utils.service)
    root.setOrientation(utils.LinearLayout.VERTICAL)
    root.setBackgroundColor(utils.Color.BLACK)
    root.setPadding(20, 20, 20, 20)

    scroll = utils.ScrollView(utils.service)
    layout = utils.LinearLayout(utils.service)
    layout.setOrientation(utils.LinearLayout.VERTICAL)

    btn_back = utils.Button(utils.service)
    btn_back.setText("Back to Folder")
    btn_back.setOnClickListener(
        utils.View.OnClickListener(
            {
                "onClick": lambda v: show_repo_details(
                    item, on_back_to_search, current_path
                )
            }
        )
    )
    layout.addView(btn_back)

    layout.addView(utils.createHeader(file_name))

    btn_download = utils.Button(utils.service)
    btn_download.setText("Download File")
    btn_download.setEnabled(False)
    layout.addView(btn_download)

    txt_content = utils.TextView(utils.service)
    txt_content.setText("Loading file content...")
    txt_content.setTextColor(utils.Color.WHITE)
    txt_content.setPadding(0, 15, 0, 15)
    layout.addView(txt_content)

    scroll.addView(layout)
    root.addView(scroll)

    utils.enableBackKey(
        root, lambda: show_repo_details(item, on_back_to_search, current_path)
    )
    utils.setScreen(root)

    url = f"https://api.github.com/repos/{urllib.parse.quote(item.get('owner_login'))}/{urllib.parse.quote(item.get('name'))}/contents/{urllib.parse.quote(file_path)}"

    def on_content_res(code, response):
        if code != 200 or not response:
            txt_content.setText(
                f"Failed to load file content (Error Code: {code})."
            )
            txt_content.setTextColor(utils.Color.RED)
            return

        download_url = ""
        decoded_text = ""
        parse_success = False
        file_size = 0

        try:
            obj = json.loads(response)
            if obj.get("download_url"):
                download_url = obj.get("download_url")

            if obj.get("size"):
                file_size = obj.get("size")

            if obj.get("content"):
                raw_content = re.sub(r"\s+", "", obj.get("content"))
                decoded_bytes = base64.b64decode(raw_content)
                decoded_text = decoded_bytes.decode("utf-8", errors="replace")
                parse_success = True
        except Exception:
            pass

        if parse_success:
            txt_content.setText(decoded_text)
        else:
            txt_content.setText("Unable to preview this file type directly.")
            txt_content.setTextColor(utils.Color.YELLOW)

        if download_url:
            btn_download.setEnabled(True)

            def download_click(v):
                start_download_file(
                    download_url,
                    file_name,
                    file_size,
                    lambda: show_file_view(
                        item, file_path, file_name, on_back_to_search, current_path
                    ),
                    lambda: show_file_view(
                        item, file_path, file_name, on_back_to_search, current_path
                    ),
                )

            btn_download.setOnClickListener(
                utils.View.OnClickListener({"onClick": download_click})
            )

    http_public_request(url, "GET", None, on_content_res)
