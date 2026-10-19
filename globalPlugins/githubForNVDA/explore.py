# GitHub for NVDA
# Copyright (C) 2025-2026 Mahadeesh and contributors
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for more details.

"""Explore GitHub (search), gists, status, the user guide and the main menu."""

import addonHandler
import gui
import os
import time
import ui
import wx

from .logic import (
	format_date,
	parse_github_link,
)
from .core import (
	Result,
	config_dir,
	gh,
	has_token,
	items_per_page,
	open_url,
	run_async,
)
from .dialogs import (
	AccessibleDialog,
	PAD,
	PagedListDialog,
	TextViewDialog,
	ask_form,
	confirm_typed,
	copy_to_clipboard,
	escape_label,
	get_clipboard_text,
	prompt_text,
	show_modal,
)
from .repotools import (
	show_bookmarks,
)
from .conversation import (
	show_my_work,
)
from .repos import (
	open_link,
	show_my_repos,
)
from .profile import (
	show_profile,
	show_settings_menu,
)
from .notify import (
	require_token,
	show_notifications,
)

addonHandler.initTranslation()


# ----------------------------------------------------------------------
# explore.py: Explore GitHub: search repositories, users, issues and code (works without a token,
# ----------------------------------------------------------------------

SEARCH_MAX_RESULTS = 1000


def sorts(kind=0):
	"""(label, API value) pairs for each kind of search. The list changes with the kind,
	but the Sort by box itself is always there and always enabled."""
	best = (_("Best match"), "")
	if kind == 0:
		return [best, (_("Most stars"), "stars"), (_("Most forks"), "forks"), (_("Recently updated"), "updated")]
	if kind == 1:
		return [best, (_("Most followers"), "followers"), (_("Most repositories"), "repositories"), (_("Recently joined"), "joined")]
	if kind == 2:
		return [
			(_("Recently updated"), "updated"), (_("Best match"), ""), (_("Most commented"), "comments"),
			(_("Most reactions"), "reactions"), (_("Recently created"), "created"),
		]
	return [best]  # GitHub cannot sort code search results


def kinds():
	return [_("Repositories"), _("Users and organizations"), _("Issues and pull requests"), _("Code")]


# --- no search history ----------------------------------------------------
# Searches are never saved. Earlier versions kept a list of them in a file; it is deleted.

def remove_saved_history():
	try:
		path = os.path.join(config_dir(), "search_history.json")
		if os.path.isfile(path):
			os.remove(path)
	except OSError:
		pass


class SearchDialog(AccessibleDialog):
	"""Search GitHub. Pasting a GitHub link opens it directly."""

	def __init__(self, parent, initial_query=""):
		super().__init__(parent, _("Explore GitHub"), (760, 620))
		self.page, self.results, self.total, self.kind = 1, [], 0, 0
		remove_saved_history()
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(self, label=_("Search &for:")), 0, wx.LEFT | wx.TOP, PAD)
		self.kind_choice = wx.Choice(self, choices=kinds())
		self.kind_choice.SetSelection(0)
		self.kind_choice.Bind(wx.EVT_CHOICE, self.on_kind)
		sizer.Add(self.kind_choice, 0, wx.ALL, PAD)
		sizer.Add(wx.StaticText(self, label=_("S&ort by:")), 0, wx.LEFT, PAD)
		self.sort = wx.Choice(self, choices=[x[0] for x in sorts(0)])
		self.sort.SetSelection(0)
		self._sort_kind = 0
		sizer.Add(self.sort, 0, wx.ALL, PAD)
		# Order on screen and for Tab: Sort by, the search box, the Search button, then the results.
		sizer.Add(wx.StaticText(self, label=_("&Search (keywords, owner/repo, or a GitHub link):")), 0, wx.LEFT, PAD)
		self.query = wx.TextCtrl(self, value=initial_query, style=wx.TE_PROCESS_ENTER)
		self.query.Bind(wx.EVT_TEXT_ENTER, lambda e: self.search(1))
		sizer.Add(self.query, 0, wx.EXPAND | wx.ALL, PAD)
		search_row = wx.BoxSizer(wx.HORIZONTAL)
		self.add_button(search_row, _("Searc&h"), lambda e: self.search(1))
		sizer.Add(search_row, 0, wx.LEFT, PAD)
		sizer.Add(wx.StaticText(self, label=_("&Results:")), 0, wx.LEFT | wx.TOP, PAD)
		self.listbox = wx.ListBox(self)
		self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, self.on_open)
		self.listbox.Bind(wx.EVT_KEY_DOWN, self.on_key)
		self.listbox.enter_action = lambda: self.on_open(None)
		sizer.Add(self.listbox, 1, wx.EXPAND | wx.ALL, PAD)
		row = wx.WrapSizer(wx.HORIZONTAL)
		self.btn_prev = self.add_button(row, _("&Previous Page"), lambda e: self.search(self.page - 1))
		self.btn_next = self.add_button(row, _("&Next Page"), lambda e: self.search(self.page + 1))
		row.Add(self.close_button(), 0, wx.ALL, 3)
		sizer.Add(row, 0, wx.ALIGN_CENTER)
		self.layout(sizer, focus=self.query)
		self.btn_prev.Disable()
		self.btn_next.Disable()
		self._search_id = 0
		if initial_query:
			wx.CallAfter(self.search, 1)

	def on_kind(self, event):
		"""Fill the Sort by box for the chosen kind of search. It is never disabled or hidden,
		so it stays in the Tab order whatever is searched for."""
		kind = self.kind_choice.GetSelection()
		if kind == self._sort_kind:
			return
		self._sort_kind = kind
		self.sort.Freeze()
		try:
			self.sort.Clear()
			for label, _value in sorts(kind):
				self.sort.Append(label)
			self.sort.SetSelection(0)
		finally:
			self.sort.Thaw()

	def on_key(self, event):
		if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			self.on_open(None)
		else:
			event.Skip()

	def _label(self, item):
		from .conversation import search_label
		from .repos import repo_label
		if self.kind == 0:
			return repo_label(item)
		if self.kind == 1:
			return _("{name}, {type}").format(name=item.get("login"), type=item.get("type", ""))
		if self.kind == 2:
			return search_label(item)
		repo = (item.get("repository") or {}).get("full_name", "")
		return _("{repo}: {path}").format(repo=repo, path=item.get("path", ""))

	def search(self, page):
		query = self.query.GetValue().strip()
		if not query:
			ui.message(_("Type something to search for."))
			self.query.SetFocus()
			return
		if page < 1:
			return
		if page == 1:
			from .repos import open_link
			if parse_github_link(query) and open_link(self, query):
				return
		kind = self.kind_choice.GetSelection()
		if kind == 3 and not has_token():
			ui.message(_("GitHub only allows code search when you are signed in. Set your token first."))
			return
		endpoint = ["/search/repositories", "/search/users", "/search/issues", "/search/code"][kind]
		params = {"q": query, "per_page": items_per_page(), "page": page}
		options = sorts(kind)
		sort = options[max(0, min(self.sort.GetSelection(), len(options) - 1))][1]
		if sort:
			params.update(sort=sort, order="desc")
		ui.message(_("Searching..."))
		self._search_id += 1
		request = self._search_id

		def done(res):
			if request != self._search_id:
				return
			if not res.ok or not isinstance(res.data, dict):
				ui.message(_("Search failed.") + " " + res.message)
				return
			items = res.data.get("items", [])
			if not items and page > 1:
				ui.message(_("No more results."))
				return
			self.kind, self.page, self.results = kind, page, items
			self.total = res.data.get("total_count", 0)
			self.listbox.Set([self._label(r) for r in items])
			self.btn_prev.Enable(page > 1)
			self.btn_next.Enable(page * items_per_page() < min(self.total, SEARCH_MAX_RESULTS))
			if items:
				self.listbox.SetSelection(0)
				self.listbox.SetFocus()
				ui.message(_("Page {page}, {count} of {total} results").format(page=page, count=len(items), total=self.total))
			else:
				ui.message(_("Nothing found."))

		if kind == 2:
			from .conversation import search_issues

			def work():
				res = search_issues(query, page, items_per_page(), sort)
				if res.ok:
					extra = SEARCH_MAX_RESULTS if res.has_next else 0
					return Result(200, {"items": res.data, "total_count": len(res.data) + extra}, {})
				return res
		else:
			def work():
				return gh("GET", endpoint, params=params)
		self.run(work, done)

	def on_open(self, event):
		i = self.listbox.GetSelection()
		if i == wx.NOT_FOUND or i >= len(self.results):
			ui.message(_("Select a result first."))
			return
		item = self.results[i]
		from .repos import open_repo
		if self.kind == 0:
			open_repo(self, item["full_name"])
		elif self.kind == 1:
			from .profile import show_user
			show_user(self, item["login"])
		elif self.kind == 2:
			import re
			from .conversation import show_conversation
			repo = re.sub(r"^.*/repos/", "", item.get("repository_url", ""))
			show_conversation(self, repo, item["number"], "pull_request" in item)
		else:
			full = (item.get("repository") or {}).get("full_name")
			path = item.get("path", "")
			if full:
				open_repo(self, full, path=path)


def show_explore(parent=None, initial_query=""):
	show_modal(SearchDialog(parent, initial_query))


# ----------------------------------------------------------------------
# gists.py: Your gists: list, read, create (from the clipboard too), edit and delete.
# ----------------------------------------------------------------------

def gist_label(g):
	files = list((g.get("files") or {}).keys())
	title = g.get("description") or (files[0] if files else _("Untitled"))
	count = len(files)
	return _("{title}, {count} files, {visibility}, updated {date}").format(
		title=title, count=count, visibility=_("public") if g.get("public") else _("secret"),
		date=format_date(g.get("updated_at")),
	)


def gist_text(g):
	lines = [
		g.get("description") or _("No description"),
		_("{visibility} gist. Updated {date}.").format(
			visibility=_("Public") if g.get("public") else _("Secret"), date=format_date(g.get("updated_at"))
		),
		_("Link: {url}").format(url=g.get("html_url")),
	]
	for name, f in (g.get("files") or {}).items():
		lines += ["", _("----- File: {name} -----").format(name=name), f.get("content") or _("(content not available)")]
	return "\n".join(lines)


def fetch_full_gist(gist_id):
	"""A gist with the complete text of every file (large files are fetched from raw_url)."""
	res = gh("GET", f"/gists/{gist_id}")
	if not res.ok or not isinstance(res.data, dict):
		return res
	for f in (res.data.get("files") or {}).values():
		if f.get("truncated") and f.get("raw_url"):
			raw = gh("GET", f["raw_url"], accept="text/plain", auth=False, binary=True, timeout=60)
			if raw.ok and isinstance(raw.data, bytes):
				f["content"] = raw.data.decode("utf-8", "replace")
				f["truncated"] = False
	return res


def show_gists(parent):
	def fetch(page, selections):
		return gh("GET", "/gists", params={"per_page": items_per_page(), "page": page})

	def view(dlg, g):
		ui.message(_("Loading gist..."))

		def done(res):
			if not res.ok:
				ui.message(_("Could not load the gist.") + " " + res.message)
				return
			full = res.data
			everything = "\n\n".join(f.get("content") or "" for f in (full.get("files") or {}).values())
			show_modal(TextViewDialog(
				dlg, _("Gist"), gist_text(full),
				buttons=[
					(_("Copy &Content"), lambda d: copy_to_clipboard(everything, _("Gist content copied."))),
					(_("Copy &Link"), lambda d: copy_to_clipboard(full.get("html_url", ""), _("Link copied."))),
					(_("Open in &Browser"), lambda d: open_url(full.get("html_url"))),
				],
				label=_("Gist &contents:"), wrap=False,
			))

		dlg.run(lambda: fetch_full_gist(g["id"]), done)

	def new_gist(dlg, _item):
		values = ask_form(
			dlg, _("New Gist"),
			[
				(_("&Description (optional):"), "desc", False, ""),
				(_("File &name:"), "name", False, "snippet.txt", True),
				(_("&Content (filled from the clipboard):"), "content", True, get_clipboard_text(), True),
			],
			checks=[(_("Make this gist &public (otherwise it is secret)"), "public", False)],
			ok_label=_("&Create Gist"),
		)
		if not values:
			return
		body = {
			"description": values["desc"].strip(), "public": values["public"],
			"files": {values["name"].strip(): {"content": values["content"]}},
		}
		ui.message(_("Creating gist..."))

		def done(res):
			if res.ok:
				copy_to_clipboard(res.data.get("html_url", ""), _("Gist created. Link copied to the clipboard."))
				dlg.load(1)
			else:
				ui.message(_("Could not create the gist.") + " " + res.message)

		dlg.run(lambda: gh("POST", "/gists", body=body), done)

	def edit(dlg, g):
		ui.message(_("Loading gist..."))

		def loaded(res):
			if not res.ok:
				ui.message(_("Could not load the gist.") + " " + res.message)
				return
			files = res.data.get("files") or {}
			if len(files) != 1:
				ui.message(_("This gist has several files. Edit it in the browser."))
				open_url(res.data.get("html_url"))
				return
			name, f = next(iter(files.items()))
			if f.get("truncated"):
				ui.message(_("This file is too large to edit here. Edit it in the browser."))
				open_url(res.data.get("html_url"))
				return
			values = ask_form(
				dlg, _("Edit Gist"),
				[
					(_("&Description:"), "desc", False, res.data.get("description") or ""),
					(_("&Content of {name}:").format(name=escape_label(name)), "content", True, f.get("content") or "", True),
				],
				ok_label=_("&Save"),
			)
			if not values:
				return

			def saved(result):
				if result.ok:
					ui.message(_("Gist saved."))
					dlg.reload()
				else:
					ui.message(_("Could not save the gist.") + " " + result.message)

			body = {"description": values["desc"].strip(), "files": {name: {"content": values["content"]}}}
			dlg.run(lambda: gh("PATCH", f"/gists/{g['id']}", body=body), saved)

		dlg.run(lambda: fetch_full_gist(g["id"]), loaded)

	def delete(dlg, g):
		if not confirm_typed(dlg, _("Delete Gist"), _("This permanently deletes the gist."), "DELETE"):
			ui.message(_("Deletion cancelled."))
			return

		def done(res):
			if res.ok:
				ui.message(_("Gist deleted."))
				dlg.reload()
			else:
				ui.message(_("Could not delete the gist.") + " " + res.message)

		dlg.run(lambda: gh("DELETE", f"/gists/{g['id']}"), done)

	actions = [
		(_("&View Gist"), view),
		(_("&New Gist"), new_gist, False),
		(_("&Edit Gist"), edit),
		(_("&Delete Gist"), delete),
		(_("Open in &Browser"), lambda d, g: open_url(g.get("html_url"))),
	]
	show_modal(PagedListDialog(
		parent, _("My Gists"), fetch, gist_label, actions,
		empty=_("You have no gists. Choose New Gist to create one."),
	))


# ----------------------------------------------------------------------
# status.py: API usage: how many requests GitHub still allows.
# ----------------------------------------------------------------------

def describe(resources):
	"""Readable lines from the /rate_limit 'resources' mapping."""
	names = {
		"core": _("General requests"), "search": _("Searches"), "code_search": _("Code searches"),
		"graphql": _("GraphQL"), "actions_runner_registration": _("Actions"),
	}
	lines = []
	now = time.time()
	for key, label in names.items():
		item = resources.get(key)
		if not item:
			continue
		minutes = max(0, int((item.get("reset", now) - now) // 60))
		lines.append(_("{label}: {remaining} of {limit} left. Resets in {minutes} minutes.").format(
			label=label, remaining=item.get("remaining", 0), limit=item.get("limit", 0), minutes=minutes
		))
	return lines


def show_status(parent):
	def work():
		res = gh("GET", "/rate_limit")
		if not res.ok:
			return res
		user = gh("GET", "/user")
		return Result(200, (res.data, user.data if user.ok else None), {})

	def done(res):
		from .core import say
		if not res.ok:
			say(_("Could not check GitHub.") + " " + res.message)
			return
		data, user = res.data
		lines = [_("Signed in as {name}.").format(name=user.get("login")) if user else _("Not signed in. Requests are limited.")]
		lines += describe((data or {}).get("resources", {}))
		show_modal(TextViewDialog(parent, _("GitHub Status"), "\n".join(lines), label=_("&Status:")))

	run_async(work, done)


# ----------------------------------------------------------------------
# guide.py: Where the user guide lives. The guide itself is doc/en/readme.html and opens in the browser.
# ----------------------------------------------------------------------

def user_guide_path():
	path = addonHandler.getCodeAddon().path
	for language in ("en",):
		candidate = os.path.join(path, "doc", language, "readme.html")
		if os.path.isfile(candidate):
			return candidate
	return ""


# ----------------------------------------------------------------------
# mainmenu.py: The main menu window.
# ----------------------------------------------------------------------

def needs_token(parent, action):
	"""Wrap a menu action so it first checks that a token exists."""
	def run():
		if require_token(parent):
			action()
	return run


class MainMenuDialog(AccessibleDialog):
	def __init__(self, parent):
		super().__init__(parent, _("GitHub for NVDA"), (440, 520), resizable=False)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(self, label=_("&Menu:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		entries = [
			(_("My Profile"), needs_token(self, lambda: show_profile(self))),
			(_("My Work"), needs_token(self, lambda: show_my_work(self))),
			(_("My Repositories"), needs_token(self, lambda: show_my_repos(self))),
			(_("GitHub Settings"), needs_token(self, lambda: show_settings_menu(self))),
			(_("Explore GitHub"), lambda: show_explore(self)),
			(_("Open Link or Repository"), self.open_by_name),
			(_("Notifications"), needs_token(self, lambda: show_notifications(self))),
			(_("Gists"), needs_token(self, lambda: show_gists(self))),
			(_("Bookmarked Repositories"), lambda: show_bookmarks(self)),
			(_("GitHub Status"), lambda: show_status(self)),
		]
		docs = user_guide_path()
		if docs:
			entries.append((_("User Guide in Browser"), lambda: os.startfile(docs) if hasattr(os, "startfile") else None))
		self.entries = entries
		# The options are one list: Up/Down to move, Enter to open. Exit is a separate button after it (Tab).
		self.listbox = wx.ListBox(self, choices=[label for label, _action in entries], size=(400, 300))
		self.listbox.SetSelection(0)
		self.listbox.enter_action = self.choose
		self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.choose())
		sizer.Add(self.listbox, 1, wx.EXPAND | wx.ALL, 8)
		sizer.Add(self.close_button(_("E&xit")), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		self.layout(sizer, fit=True, focus=self.listbox)

	def choose(self):
		index = self.listbox.GetSelection()
		if index != wx.NOT_FOUND:
			self.entries[index][1]()

	def open_by_name(self):
		clip = get_clipboard_text().strip()
		start = clip if clip and len(clip) < 300 and parse_github_link(clip) else ""
		text = prompt_text(self, _("Open Link or Repository"), _("Repository &name (owner/repo), user name, or GitHub link:"), start)
		if not text:
			return
		if not open_link(self, text.strip()):
			if text.strip() and "/" not in text and " " not in text.strip():
				from .profile import show_user
				show_user(self, text.strip().lstrip("@"))
			else:
				ui.message(_("Enter a repository as owner/repo, for example nvaccess/nvda, or paste a GitHub link."))


def open_main_dialog():
	show_modal(MainMenuDialog(gui.mainFrame))
