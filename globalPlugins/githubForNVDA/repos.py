# GitHub for NVDA
# Copyright (C) 2025-2026 Mahadeesh and contributors
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for more details.

"""Repositories: browser, file viewer, file and folder actions, repository actions, My Repositories and link opening."""

import addonHandler
import os
import re
import ui
import wx
import zipfile

from .logic import (
	clean_branch_name,
	decode_text,
	encode_text,
	format_date,
	format_size,
	parse_github_link,
	safe_filename,
	split_names,
	unique_path,
)
from .core import (
	Result,
	ask_yes_no,
	downloads_dir,
	get_pref,
	gh,
	gh_paged,
	has_token,
	items_per_page,
	open_url,
	run_async,
	say,
)
from .dialogs import (
	AccessibleDialog,
	PAD,
	TextViewDialog,
	ask_choice,
	ask_form,
	confirm,
	confirm_typed,
	copy_to_clipboard,
	escape_label,
	inform,
	prompt_text,
	show_modal,
)
from .repotools import (
	FileContext,
	commit_file,
	commit_tree,
	content_url,
	create_blob,
	delete_file,
	download_file,
	find_sha,
	is_bookmarked,
	list_branches,
	permissions,
	q,
	rename_bookmark,
	resolve_ref_path,
	toggle_bookmark,
	tree_items,
)

addonHandler.initTranslation()


# ----------------------------------------------------------------------
# createrepo.py: Create a new repository.
# ----------------------------------------------------------------------

def valid_repo_name(name):
	return bool(re.fullmatch(r"[A-Za-z0-9._-]+", name or "")) and name not in (".", "..")


class CreateRepoDialog(AccessibleDialog):
	def __init__(self, parent):
		super().__init__(parent, _("Create Repository"), (520, 460), resizable=False)
		self.created = None
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(self, label=_("Repository &name:")), 0, wx.ALL, PAD)
		self.name = wx.TextCtrl(self)
		self.name.Bind(wx.EVT_TEXT, lambda e: self.btn_create.Enable(bool(self.name.GetValue().strip())))
		sizer.Add(self.name, 0, wx.EXPAND | wx.ALL, PAD)
		sizer.Add(wx.StaticText(self, label=_("&Description (optional):")), 0, wx.ALL, PAD)
		self.desc = wx.TextCtrl(self, style=wx.TE_MULTILINE, size=(-1, 70))
		sizer.Add(self.desc, 0, wx.EXPAND | wx.ALL, PAD)
		self.visibility = wx.RadioBox(self, label=_("Visibility"), choices=[_("&Private"), _("P&ublic")], majorDimension=1, style=wx.RA_SPECIFY_COLS)
		sizer.Add(self.visibility, 0, wx.ALL, PAD)
		self.readme = wx.CheckBox(self, label=_("Add a &README file"))
		sizer.Add(self.readme, 0, wx.ALL, PAD)
		sizer.Add(wx.StaticText(self, label=_("&License:")), 0, wx.LEFT | wx.TOP, PAD)
		self.licenses = [(_("None"), ""), ("MIT", "mit"), ("Apache 2.0", "apache-2.0"), ("GPL 3.0", "gpl-3.0"), ("GPL 2.0", "gpl-2.0"), ("BSD 3-Clause", "bsd-3-clause"), ("Unlicense", "unlicense")]
		self.license = wx.Choice(self, choices=[n for n, _k in self.licenses])
		self.license.SetSelection(0)
		sizer.Add(self.license, 0, wx.ALL, PAD)
		row = wx.BoxSizer(wx.HORIZONTAL)
		self.btn_create = wx.Button(self, wx.ID_OK, _("C&reate"))
		self.btn_create.SetDefault()
		self.btn_create.Bind(wx.EVT_BUTTON, self.on_create)
		self.btn_create.Disable()
		row.Add(self.btn_create, 0, wx.ALL, PAD)
		row.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancel")), 0, wx.ALL, PAD)
		sizer.Add(row, 0, wx.ALIGN_RIGHT)
		self.layout(sizer, focus=self.name)

	def on_create(self, event):
		name = self.name.GetValue().strip()
		if not valid_repo_name(name):
			ui.message(_("Use only letters, numbers, dots, hyphens and underscores in the name."))
			self.name.SetFocus()
			return
		body = {"name": name, "private": self.visibility.GetSelection() == 0, "auto_init": self.readme.GetValue()}
		description = self.desc.GetValue().strip()
		if description:
			body["description"] = description
		key = self.licenses[self.license.GetSelection()][1]
		if key:
			body["license_template"] = key
			body["auto_init"] = True
		ui.message(_("Creating repository..."))
		self.btn_create.Disable()

		def done(res):
			self.btn_create.Enable()
			if res.ok and isinstance(res.data, dict):
				self.created = res.data
				inform(self, _("Repository '{name}' created.").format(name=res.data["full_name"]), _("Success"))
				self.EndModal(wx.ID_OK)
			elif res.code == 422:
				ui.message(_("A repository with that name already exists, or the name is not allowed. Choose a different name."))
				self.name.SetFocus()
			else:
				ui.message(_("Could not create the repository.") + " " + res.message)

		self.run(lambda: gh("POST", "/user/repos", body=body), done)


def create_repository(parent=None):
	"""Returns the new repository's data, or None."""
	dialog = CreateRepoDialog(parent)
	try:
		if dialog.ShowModal() == wx.ID_OK:
			return dialog.created
	finally:
		dialog.Destroy()
	return None


# ----------------------------------------------------------------------
# fileviewer.py: View and edit one file of a repository.
# ----------------------------------------------------------------------

MAX_VIEW_BYTES = 1024 * 1024


class FileViewDialog(AccessibleDialog):
	"""Shows a file in a text box. Writers can edit it and commit the change.

	Shortcuts: Ctrl+F find, F3 find next, Ctrl+G go to line, Ctrl+S commit.
	"""

	def __init__(self, parent, ctx, entry):
		super().__init__(parent, entry["name"], (780, 600))
		self.ctx, self.entry = ctx, entry
		self.sha = entry.get("sha")
		self.encoding, self.crlf = "utf-8", False
		self.is_text = False
		self.can_edit = False
		self._last_search = ""

		sizer = wx.BoxSizer(wx.VERTICAL)
		label = _("File &content (editable)") if ctx.can_write else _("File &content (read only)")
		sizer.Add(wx.StaticText(self, label=label), 0, wx.ALL, PAD)
		wrap = get_pref("wrapFileText")
		style = wx.TE_MULTILINE | (wx.TE_BESTWRAP if wrap else wx.TE_DONTWRAP | wx.HSCROLL)
		self.text = wx.TextCtrl(self, style=style)
		sizer.Add(self.text, 1, wx.EXPAND | wx.ALL, PAD)

		row = wx.WrapSizer(wx.HORIZONTAL)
		self.btn_save = None
		if ctx.can_write:
			self.btn_save = self.add_button(row, _("Sa&ve Changes (Commit)"), self.on_save)
			self.btn_save.Enable(False)
		self.add_button(row, _("&Find..."), self.on_find)
		self.add_button(row, _("Find &Next"), self.on_find_next)
		self.add_button(row, _("&Go to Line..."), self.on_goto)
		self.add_button(row, _("Copy Raw &URL"), self.on_copy_url)
		self.add_button(row, _("Copy C&ontent"), self.on_copy_content)
		self.add_button(row, _("&Download"), self.on_download)
		self.add_button(row, _("File &History"), self.on_history)
		self.add_button(row, _("Open in &Browser"), self.on_browser)
		row.Add(self.close_button(), 0, wx.ALL, 3)
		sizer.Add(row, 0, wx.ALIGN_CENTER)
		self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
		self.layout(sizer, focus=self.text)

		self.text.SetEditable(False)
		if (entry.get("size") or 0) > MAX_VIEW_BYTES:
			self.text.SetValue(
				_("This file is larger than {size}. Use Download or Copy Raw URL.").format(
					size=format_size(MAX_VIEW_BYTES)
				)
			)
		else:
			self.text.SetValue(_("Loading..."))
			self._load()

	# --- loading -----------------------------------------------------
	def _load(self):
		def work():
			return gh(
				"GET", content_url(self.ctx.full, self.entry["path"]),
				params={"ref": self.ctx.ref} if self.ctx.ref else None,
				accept="application/vnd.github.raw+json", binary=True,
			)

		def done(res):
			if not res.ok:
				self.text.SetValue(_("Could not load the file.") + " " + res.message)
				return
			decoded = decode_text(res.data) if isinstance(res.data, bytes) else None
			if decoded is None:
				self.text.SetValue(_("This is a binary file. Use Download to save it."))
				return
			self.is_text = True
			self.encoding, self.crlf = decoded.encoding, decoded.crlf
			self.can_edit = self.ctx.can_write and decoded.editable
			self.text.SetValue(decoded.text)
			self.text.DiscardEdits()
			self.text.SetInsertionPoint(0)
			self.text.SetEditable(self.can_edit)
			if self.btn_save:
				self.btn_save.Enable(self.can_edit)
			self.text.SetFocus()
			if self.ctx.can_write and not decoded.editable:
				ui.message(_("File loaded. It is not UTF-8 text, so it is read only."))
			else:
				ui.message(_("File loaded"))

		self.run(work, done)

	# --- keyboard ----------------------------------------------------
	def _on_char_hook(self, event):
		code = event.GetKeyCode()
		if event.ControlDown() and code == ord("F"):
			self.on_find(None)
		elif event.ControlDown() and code == ord("G"):
			self.on_goto(None)
		elif event.ControlDown() and code == ord("S") and self.can_edit:
			self.on_save(None)
		elif code == wx.WXK_F3:
			self.on_find_next(None)
		else:
			event.Skip()

	# --- searching ---------------------------------------------------
	def on_find(self, event):
		if not self.is_text:
			ui.message(_("There is no text to search."))
			return
		term = prompt_text(self, _("Find"), _("&Find text (not case sensitive):"), self._last_search)
		if term:
			self._last_search = term
			self._find_from_caret()

	def on_find_next(self, event):
		if not self._last_search:
			self.on_find(event)
		else:
			self._find_from_caret()

	def _find_from_caret(self):
		content = self.text.GetValue()
		# Search the text as it is: lower-casing it first can change its length for some letters, which
		# would put the selection in the wrong place.
		pattern = re.compile(re.escape(self._last_search), re.IGNORECASE)
		start_sel, end_sel = self.text.GetSelection()
		start = end_sel if end_sel > start_sel else self.text.GetInsertionPoint()
		match = pattern.search(content, start)
		wrapped = False
		if match is None and start > 0:
			match = pattern.search(content)
			wrapped = True
		if match is None:
			ui.message(_("Not found."))
			return
		index = match.start()
		self.text.SetFocus()
		self.text.SetSelection(index, match.end())
		self.text.ShowPosition(index)
		line = content.count("\n", 0, index) + 1
		if wrapped:
			ui.message(_("Search wrapped. Line {line}").format(line=line))
		else:
			ui.message(_("Line {line}").format(line=line))

	def on_goto(self, event):
		if not self.is_text:
			ui.message(_("There is no text to move through."))
			return
		total = self.text.GetNumberOfLines()
		answer = prompt_text(
			self, _("Go to Line"), _("&Line number (1 to {total}):").format(total=total)
		)
		if not answer:
			return
		try:
			number = int(answer.strip())
		except ValueError:
			ui.message(_("Enter a whole number."))
			return
		if not 1 <= number <= total:
			ui.message(_("The file has {total} lines.").format(total=total))
			return
		position = self.text.XYToPosition(0, number - 1)
		self.text.SetFocus()
		self.text.SetInsertionPoint(position)
		self.text.ShowPosition(position)
		ui.message(_("Line {line}: {text}").format(line=number, text=self.text.GetLineText(number - 1)))

	# --- saving ------------------------------------------------------
	def can_close(self):
		if self.is_text and self.can_edit and self.text.IsModified():
			return confirm(
				self, _("You have unsaved changes. Close and discard them?"), _("Unsaved Changes"),
				warning=True, default_no=True,
			)
		return True

	def on_save(self, event):
		if not (self.is_text and self.can_edit):
			return
		if not self.text.IsModified():
			ui.message(_("No changes to save."))
			return
		default = _("Update {name}").format(name=self.entry["name"])
		message = prompt_text(self, _("Commit Message"), _("&Commit message:"), default)
		if message is None:
			return
		data = encode_text(self.text.GetValue(), self.encoding, self.crlf)
		ui.message(_("Saving..."))
		self.btn_save.Enable(False)

		def done(res):
			self.btn_save.Enable(True)
			if res.ok and isinstance(res.data, dict):
				self.sha = (res.data.get("content") or {}).get("sha", self.sha)
				self.entry["sha"] = self.sha
				self.text.DiscardEdits()
				ui.message(
					_("Changes committed to {branch}").format(branch=self.ctx.ref or _("the default branch"))
				)
			else:
				ui.message(_("Save failed.") + " " + res.message)

		self.run(
			lambda: commit_file(
				self.ctx.full, self.entry["path"], self.ctx.ref, message.strip() or default, data, self.sha
			),
			done,
		)

	# --- other actions -----------------------------------------------
	def on_copy_url(self, event):
		url = self.entry.get("download_url") or (
			f"https://raw.githubusercontent.com/{self.ctx.full}/{q(self.ctx.ref, '/')}/"
			f"{q(self.entry['path'], '/')}"
		)
		copy_to_clipboard(url, _("Raw URL copied."))

	def on_copy_content(self, event):
		if self.is_text:
			copy_to_clipboard(self.text.GetValue(), _("File content copied."))
		else:
			ui.message(_("There is no text content to copy."))

	def on_download(self, event):
		from .repotools import download_file
		download_file(
			content_url(self.ctx.full, self.entry["path"]), self.entry["name"],
			accept="application/vnd.github.raw+json",
			params={"ref": self.ctx.ref} if self.ctx.ref else None,
		)

	def on_history(self, event):
		from .repotools import show_commits
		show_commits(self, self.ctx.full, self.ctx.ref, self.entry["path"])

	def on_browser(self, event):
		open_url(
			f"https://github.com/{self.ctx.full}/blob/{q(self.ctx.ref or 'HEAD', '/')}/"
			f"{q(self.entry['path'], '/')}"
		)


# ----------------------------------------------------------------------
# itemactions.py: Actions for one file or folder of the repository browser.
# ----------------------------------------------------------------------

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_FOLDER_FILES = 3000
MAX_COPY_BYTES = 1024 * 1024
SKIPPED_FOLDERS = {".git"}


def _join(base, name):
	return (base.strip("/") + "/" if base else "") + name.strip("/")


def collect_folder(root):
	"""List the files of a local folder: (full_path, relative_path_with_slashes, size).

	Returns (files, too_big, truncated). The .git folder and links are left out.
	"""
	files, too_big, truncated = [], [], False
	for current, dirs, names in os.walk(root):
		dirs[:] = sorted(d for d in dirs if d not in SKIPPED_FOLDERS and not os.path.islink(os.path.join(current, d)))
		for name in sorted(names):
			full = os.path.join(current, name)
			if os.path.islink(full) or not os.path.isfile(full):
				continue
			rel = os.path.relpath(full, root).replace(os.sep, "/")
			try:
				size = os.path.getsize(full)
			except OSError:
				continue
			if size > MAX_UPLOAD_BYTES:
				too_big.append(rel)
			elif len(files) >= MAX_FOLDER_FILES:
				truncated = True
				return files, too_big, truncated
			else:
				files.append((full, rel, size))
	return files, too_big, truncated


class ItemActions:
	"""Everything in the file list's context menu. `dlg` is the RepoBrowserDialog."""

	def __init__(self, dlg):
		self.dlg = dlg

	# --- helpers -----------------------------------------------------
	@property
	def full(self):
		return self.dlg.full

	@property
	def ref(self):
		return self.dlg.ref

	def web_url(self, entry):
		kind = "tree" if entry.get("type") == "dir" else "blob"
		return f"https://github.com/{self.full}/{kind}/{q(self.ref or 'HEAD', '/')}/{q(entry['path'], '/')}"

	def raw_url(self, entry):
		return entry.get("download_url") or (
			f"https://raw.githubusercontent.com/{self.full}/{q(self.ref, '/')}/{q(entry['path'], '/')}"
		)

	# --- the menu ----------------------------------------------------
	def menu_entries(self, entry, more_entries):
		"""Menu entries for the selected file or folder."""
		d = self.dlg
		write = d.can_write
		kind = entry.get("type")
		if kind == "dir":
			items = [
				(_("&Open Folder"), lambda: d.open_entry(entry)),
				(_("Open Folder in &Browser"), lambda: open_url(self.web_url(entry))),
				None,
				(_("Copy Folder &Location"), lambda: copy_to_clipboard(entry["path"], _("Folder location copied."))),
				(_("Copy GitHub &Link"), lambda: copy_to_clipboard(self.web_url(entry), _("GitHub link copied."))),
				None,
				(_("Download Folder as &ZIP"), lambda: self.download_folder(entry)),
				(_("Folder &History"), lambda: self.history(entry)),
			]
			if write:
				items += [
					None,
					(_("&Upload Folder to GitHub..."), lambda: self.upload_folder(entry["path"])),
					(_("Upload &Files to This Folder..."), lambda: self.upload_files(entry["path"])),
					(_("Create &New File in This Folder..."), lambda: d.on_new_file(entry["path"])),
					(_("Create New F&older in This Folder..."), lambda: d.on_new_folder(entry["path"])),
					None,
					(_("Re&name or Move Folder..."), lambda: self.rename(entry)),
					(_("Dup&licate Folder..."), lambda: self.rename(entry, duplicate=True)),
					(_("&Delete Folder..."), lambda: self.delete_folder(entry)),
				]
		elif kind == "submodule":
			items = [
				(_("Copy &Location"), lambda: copy_to_clipboard(entry["path"], _("Location copied."))),
				(_("Open in &Browser"), lambda: open_url(entry.get("html_url") or self.web_url(entry))),
			]
		else:
			items = [
				(_("&Open File"), lambda: d.open_entry(entry)),
				(_("Open in &Browser"), lambda: open_url(self.web_url(entry))),
				None,
				(_("Copy File &Location"), lambda: copy_to_clipboard(entry["path"], _("File location copied."))),
				(_("Copy GitHub &Link"), lambda: copy_to_clipboard(self.web_url(entry), _("GitHub link copied."))),
				(_("Copy &Raw URL"), lambda: copy_to_clipboard(self.raw_url(entry), _("Raw URL copied."))),
				(_("Copy File &Content"), lambda: self.copy_content(entry)),
				None,
				(_("&Download File"), lambda: self.download_file(entry)),
				(_("File &History"), lambda: self.history(entry)),
				(_("&Properties"), lambda: self.properties(entry)),
			]
			if write:
				items += [
					None,
					(_("Re&name or Move File..."), lambda: self.rename(entry)),
					(_("Dup&licate File..."), lambda: self.rename(entry, duplicate=True)),
					(_("&Delete File..."), d.on_delete_file),
					None,
					(_("&Upload Files to This Folder..."), lambda: self.upload_files(d.path)),
					(_("Upload Fol&der to GitHub..."), lambda: self.upload_folder(d.path)),
				]
		if write and more_entries:
			items += [None, (_("&More Options"), list(more_entries))]
		return items

	# --- simple actions ------------------------------------------------
	def history(self, entry):
		from .repotools import show_commits
		show_commits(self.dlg, self.full, self.ref, entry["path"])

	def properties(self, entry):
		lines = [
			_("Name: {name}").format(name=entry.get("name")),
			_("Location in repository: {path}").format(path=entry.get("path")),
			_("Branch: {branch}").format(branch=self.ref or _("default")),
			_("Size: {size}").format(size=format_size(entry.get("size"))),
			_("Git object ID: {sha}").format(sha=entry.get("sha")),
			_("GitHub link: {url}").format(url=self.web_url(entry)),
			_("Raw URL: {url}").format(url=self.raw_url(entry)),
		]
		show_modal(TextViewDialog(self.dlg, entry.get("name", _("Properties")), "\n".join(lines), label=_("&Properties:")))

	def copy_content(self, entry):
		if (entry.get("size") or 0) > MAX_COPY_BYTES:
			ui.message(_("This file is larger than {size}. Use Download or Copy Raw URL.").format(size=format_size(MAX_COPY_BYTES)))
			return
		ui.message(_("Copying..."))

		def work():
			return gh(
				"GET", content_url(self.full, entry["path"]),
				params={"ref": self.ref} if self.ref else None,
				accept="application/vnd.github.raw+json", binary=True,
			)

		def done(res):
			if not res.ok:
				ui.message(_("Could not load the file.") + " " + res.message)
				return
			decoded = decode_text(res.data) if isinstance(res.data, bytes) else None
			if decoded is None:
				ui.message(_("This is a binary file. Use Download to save it."))
				return
			copy_to_clipboard(decoded.text, _("File content copied."))

		self.dlg.run(work, done)

	def download_file(self, entry):
		from .repotools import download_file
		download_file(
			content_url(self.full, entry["path"]), entry["name"],
			accept="application/vnd.github.raw+json",
			params={"ref": self.ref} if self.ref else None,
		)

	def download_folder(self, entry):
		"""Save a whole folder as one ZIP file in the Downloads folder."""
		full, ref, root = self.full, self.ref, entry["path"]
		ui.message(_("Preparing {name}...").format(name=entry["name"]))

		def work():
			listing = tree_items(full, ref, root)
			if not listing.ok:
				return listing
			items = [e for e in listing.data if e["type"] == "blob"]
			if not items:
				return Result(-1, None, {})
			destination = unique_path(downloads_dir(), safe_filename(entry["name"]) + ".zip")
			failed = 0
			try:
				with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
					for number, item in enumerate(items, 1):
						if len(items) > 20 and number % 20 == 0:
							say(_("{number} of {total} files").format(number=number, total=len(items)))
						blob = gh(
							"GET", f"/repos/{full}/git/blobs/{item['sha']}",
							accept="application/vnd.github.raw+json", binary=True, timeout=60,
						)
						if not blob.ok:
							failed += 1
							continue
						inside = entry["name"] + item["path"][len(root):]
						archive.writestr(inside, blob.data)
			except Exception as e:
				return Result(0, str(e), {})
			return Result(200, (destination, len(items) - failed, failed), {})

		def done(res):
			if res.code == -1:
				ui.message(_("This folder has no files to download."))
			elif res.ok:
				path, saved, failed = res.data
				text = _("Download complete. {count} files saved to {path}").format(count=saved, path=path)
				if failed:
					text += " " + _("{count} files could not be downloaded.").format(count=failed)
				ui.message(text)
			else:
				ui.message(_("Download failed.") + " " + res.message)

		self.dlg.run(work, done)

	# --- uploads -------------------------------------------------------
	def upload_files(self, target=None):
		d = self.dlg
		base = d.path if target is None else target
		dialog = wx.FileDialog(
			d, _("Choose files to upload"), style=wx.FD_OPEN | wx.FD_MULTIPLE | wx.FD_FILE_MUST_EXIST
		)
		try:
			if dialog.ShowModal() != wx.ID_OK:
				return
			paths = dialog.GetPaths()
		finally:
			dialog.Destroy()
		files, skipped = [], []
		for p in paths:
			if os.path.isfile(p) and os.path.getsize(p) <= MAX_UPLOAD_BYTES:
				files.append(p)
			else:
				skipped.append(os.path.basename(p))
		if skipped:
			ui.message(_("Skipping files over 50 MB: {names}").format(names=", ".join(skipped)))
		if not files:
			return
		message = prompt_text(d, _("Commit Message"), _("&Commit message:"), _("Upload files via GitHub for NVDA"))
		if message is None:
			return
		self._commit_local_files(
			[(p, _join(base, os.path.basename(p))) for p in files], message.strip() or _("Upload file"),
			_("Uploaded {ok} of {total} files."),
		)

	def upload_folder(self, target=None):
		"""Upload a whole folder from this computer, keeping its structure, in one commit."""
		d = self.dlg
		base = d.path if target is None else target
		dialog = wx.DirDialog(d, _("Choose the folder to upload"), style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST)
		try:
			if dialog.ShowModal() != wx.ID_OK:
				return
			root = dialog.GetPath()
		finally:
			dialog.Destroy()
		root = os.path.normpath(root)
		name = os.path.basename(root) or safe_filename(root.replace(":", "").replace("\\", ""))
		ui.message(_("Reading the folder..."))
		files, too_big, truncated = collect_folder(root)
		if not files:
			ui.message(_("There are no files to upload in that folder."))
			return
		total_size = sum(f[2] for f in files)
		where = "/" + _join(base, name)
		text = _(
			"Upload the folder '{name}' with {count} files ({size}) to {where} on branch {branch}? "
			"All files go in one commit. Files with the same name there are replaced."
		).format(name=name, count=len(files), size=format_size(total_size), where=where, branch=self.ref)
		if too_big:
			text += "\n" + _("{count} files over 50 MB will be skipped.").format(count=len(too_big))
		if truncated:
			text += "\n" + _("Only the first {count} files will be uploaded.").format(count=MAX_FOLDER_FILES)
		if not confirm(d, text, _("Upload Folder")):
			ui.message(_("Cancelled. Nothing was uploaded."))
			return
		default = _("Upload folder {name} via GitHub for NVDA").format(name=name)
		message = prompt_text(d, _("Commit Message"), _("&Commit message:"), default)
		if message is None:
			return
		self._commit_local_files(
			[(path, _join(base, name + "/" + rel)) for path, rel, size in files],
			message.strip() or default, _("Uploaded {ok} of {total} files."),
		)

	def _commit_local_files(self, pairs, message, summary):
		"""Upload [(local_path, repository_path)] as one commit, or file by file in an empty repository."""
		d = self.dlg
		full, ref = self.full, self.ref
		total = len(pairs)
		ui.message(_("Uploading {count} files...").format(count=total))

		def progress(number, name):
			if total <= 10 or number % 10 == 0 or number == total:
				say(_("Uploading {number} of {total}: {name}").format(number=number, total=total, name=name))

		def work():
			failed = []
			branch = gh("GET", f"/repos/{full}/branches/{q(ref, '/')}") if ref else Result(404, None, {})
			if not branch.ok and branch.code in (404, 409, 422):
				# An empty repository has no branch yet: the contents API can still create files.
				ok = 0
				for number, (path, remote) in enumerate(pairs, 1):
					progress(number, os.path.basename(path))
					try:
						with open(path, "rb") as f:
							data = f.read()
						res = commit_file(full, remote, ref, message, data, None)
						if res.ok:
							ok += 1
						else:
							failed.append(f"{os.path.basename(path)} ({res.message})")
					except Exception as e:
						failed.append(f"{os.path.basename(path)} ({e})")
				return Result(200, (ok, failed), {})
			if not branch.ok:
				return branch
			entries = []
			for number, (path, remote) in enumerate(pairs, 1):
				progress(number, os.path.basename(path))
				try:
					with open(path, "rb") as f:
						data = f.read()
					blob = create_blob(full, data)
					if blob.ok:
						entries.append({"path": remote, "mode": "100644", "type": "blob", "sha": blob.data})
					else:
						failed.append(f"{os.path.basename(path)} ({blob.message})")
				except Exception as e:
					failed.append(f"{os.path.basename(path)} ({e})")
			if not entries:
				return Result(200, (0, failed), {})
			say(_("Saving the commit..."))
			res = commit_tree(full, ref, message, entries)
			if not res.ok:
				return res
			return Result(200, (len(entries), failed), {})

		def done(res):
			if not isinstance(res.data, tuple):
				ui.message(_("Upload failed.") + " " + res.message)
				return
			ok, failed = res.data
			text = summary.format(ok=ok, total=total)
			if failed:
				text += " " + _("Not uploaded: {names}").format(names="; ".join(failed[:10]))
			ui.message(text)
			d.load_dir(d.path)

		d.run(work, done)

	# --- rename, move, duplicate, delete ---------------------------------
	def rename(self, entry, duplicate=False):
		d = self.dlg
		full, ref, old = self.full, self.ref, entry["path"]
		is_folder = entry.get("type") == "dir"
		if duplicate:
			title = _("Duplicate Folder") if is_folder else _("Duplicate File")
			label = _("&New location and name, from the top of the repository:")
			suggestion = old + "-copy" if is_folder else self._copy_name(old)
		else:
			title = _("Rename or Move Folder") if is_folder else _("Rename or Move File")
			label = _("&New location and name, from the top of the repository (change the folders to move it):")
			suggestion = old
		answer = prompt_text(d, title, label, suggestion)
		if answer is None:
			return
		new = answer.strip().strip("/")
		if not new or new == old:
			ui.message(_("Nothing was changed."))
			return
		if is_folder and (new + "/").startswith(old + "/"):
			ui.message(_("A folder cannot be moved into itself."))
			return
		verb = _("Duplicate {name}") if duplicate else _("Move {name} to {new}")
		default = verb.format(name=entry["name"], new=new)
		message = prompt_text(d, _("Commit Message"), _("&Commit message:"), default)
		if message is None:
			return
		ui.message(_("Working..."))

		def work():
			source = tree_items(full, ref, old)
			if not source.ok:
				return source
			if not source.data:
				return Result(-1, None, {})
			clash = tree_items(full, ref, new)
			if clash.ok and clash.data:
				return Result(-2, None, {})
			changes = []
			for item in source.data:
				target = new + item["path"][len(old):]
				changes.append({"path": target, "mode": item["mode"], "type": item["type"], "sha": item["sha"]})
				if not duplicate:
					changes.append({"path": item["path"], "mode": item["mode"], "type": item["type"], "sha": None})
			return commit_tree(full, ref, message.strip() or default, changes)

		def done(res):
			if res.code == -1:
				ui.message(_("That item no longer exists. Refreshing."))
			elif res.code == -2:
				ui.message(_("Something with that name already exists. Choose another name."))
			elif res.ok:
				ui.message(_("Duplicated.") if duplicate else _("Moved to {new}.").format(new=new))
			else:
				ui.message(_("The change failed.") + " " + res.message)
				return
			d.load_dir(d.path, select_name=entry["name"])

		d.run(work, done)

	@staticmethod
	def _copy_name(path):
		folder, name = os.path.split(path)
		base, ext = os.path.splitext(name)
		return (folder + "/" if folder else "") + base + "-copy" + ext

	def delete_folder(self, entry):
		d = self.dlg
		full, ref, path = self.full, self.ref, entry["path"]
		if not confirm_typed(
			d, _("Delete Folder"),
			_("This deletes the folder {path} and everything in it from branch {branch}. It creates a commit and can be restored from history.").format(
				path=path, branch=ref
			),
			entry["name"],
		):
			ui.message(_("Deletion cancelled."))
			return
		default = _("Delete folder {name}").format(name=entry["name"])
		message = prompt_text(d, _("Commit Message"), _("&Commit message:"), default)
		if message is None:
			return
		ui.message(_("Deleting..."))

		def work():
			listing = tree_items(full, ref, path)
			if not listing.ok:
				return listing
			if not listing.data:
				return Result(-1, None, {})
			changes = [
				{"path": i["path"], "mode": i["mode"], "type": i["type"], "sha": None} for i in listing.data
			]
			return commit_tree(full, ref, message.strip() or default, changes)

		def done(res):
			if res.code == -1:
				ui.message(_("That folder no longer exists. Refreshing."))
			elif res.ok:
				ui.message(_("{name} deleted.").format(name=entry["name"]))
			else:
				ui.message(_("Delete failed.") + " " + res.message)
				return
			d.load_dir(d.path)

		d.run(work, done)


# ----------------------------------------------------------------------
# repoactions.py: Actions on one repository, shared by My Repositories and the repository browser.
# ----------------------------------------------------------------------

class RepoActions:
	"""Everything you can do to a repository, wired to a parent window.

	dialog      the window that owns any sub window and runs background work
	repo        the repository payload; kept up to date after every change
	on_changed  called with the new payload after a successful change
	on_deleted  called after the repository was deleted
	ref_fn      returns the branch to use for the README and the download
	"""

	def __init__(self, dialog, repo, on_changed=None, on_deleted=None, ref_fn=None):
		self.dialog = dialog
		self.repo = repo
		self.on_changed = on_changed
		self.on_deleted = on_deleted
		self.ref_fn = ref_fn
		self.starred = None  # None until checked

	# --- helpers -----------------------------------------------------
	@property
	def full(self):
		return self.repo["full_name"]

	def ref(self):
		return (self.ref_fn() if self.ref_fn else None) or self.repo.get("default_branch") or ""

	def _need_admin(self):
		if permissions(self.repo)[1]:
			return True
		ui.message(_("You need admin access to {name} to do that.").format(name=self.full))
		return False

	def _patch(self, body, success, after=None):
		old_full = self.full

		def done(res):
			if res.ok and isinstance(res.data, dict):
				self.repo.update(res.data)
				if old_full.lower() != self.full.lower():
					rename_bookmark(old_full, self.full)
				ui.message(success)
				if self.on_changed:
					self.on_changed(self.repo)
				if after:
					after()
			else:
				ui.message(_("That did not work.") + " " + res.message)

		self.dialog.run(lambda: gh("PATCH", f"/repos/{old_full}", body=body), done)

	# --- the actions ---------------------------------------------------
	def open(self):
		open_repo(self.dialog, self.full)

	def rename(self):
		if not self._need_admin():
			return
		new = prompt_text(self.dialog, _("Rename Repository"), _("&New repository name:"), self.repo["name"])
		if new and new.strip() and new.strip() != self.repo["name"]:
			self._patch({"name": new.strip()}, _("Repository renamed."))

	def edit_description(self):
		if not self._need_admin():
			return
		new = prompt_text(
			self.dialog, _("Edit Description"), _("New &description:"), self.repo.get("description") or ""
		)
		if new is not None:
			self._patch({"description": new.strip()}, _("Description updated."))

	def edit_homepage(self):
		if not self._need_admin():
			return
		new = prompt_text(
			self.dialog, _("Edit Website Address"), _("&Website address:"), self.repo.get("homepage") or ""
		)
		if new is not None:
			self._patch({"homepage": new.strip()}, _("Website address updated."))

	def edit_topics(self):
		if not self._need_admin():
			return
		new = prompt_text(
			self.dialog, _("Edit Topics"), _("&Topics, separated by commas:"),
			", ".join(self.repo.get("topics") or []),
		)
		if new is None:
			return
		names = [t.lower().replace(" ", "-") for t in split_names(new)]

		def done(res):
			if res.ok and isinstance(res.data, dict):
				self.repo["topics"] = res.data.get("names", names)
				ui.message(_("Topics updated."))
				if self.on_changed:
					self.on_changed(self.repo)
			else:
				ui.message(_("Could not update topics.") + " " + res.message)

		self.dialog.run(lambda: gh("PUT", f"/repos/{self.full}/topics", body={"names": names}), done)

	def delete(self):
		if not self._need_admin():
			return
		if not confirm_typed(
			self.dialog, _("Delete Repository"),
			_("This permanently deletes {name} and cannot be undone.").format(name=self.full), self.repo["name"],
		):
			ui.message(_("Deletion cancelled."))
			return
		full = self.full
		ui.message(_("Deleting {name}...").format(name=full))

		def done(res):
			if res.ok:
				ui.message(_("Repository deleted."))
				if self.on_deleted:
					self.on_deleted()
			else:
				ui.message(
					_("Delete failed.") + " " + res.message + " "
					+ _("Deleting needs the delete_repo permission on your token.")
				)

		self.dialog.run(lambda: gh("DELETE", f"/repos/{full}"), done)

	def open_in_browser(self):
		open_url(self.repo.get("html_url") or f"https://github.com/{self.full}")

	def view_readme(self):
		ui.message(_("Loading README..."))
		ref = self.ref()

		def done(res):
			if not res.ok:
				ui.message(_("This repository has no README.") if res.code == 404 else res.message)
				return
			text = res.data.decode("utf-8", "replace") if isinstance(res.data, bytes) else str(res.data)
			show_modal(TextViewDialog(self.dialog, _("README"), text, label=_("&README:")))

		self.dialog.run(lambda: gh(
			"GET", f"/repos/{self.full}/readme", params={"ref": ref} if ref else None,
			accept="application/vnd.github.raw+json", binary=True,
		), done)

	def edit_readme(self):
		can_write = permissions(self.repo)[0]
		if not can_write:
			ui.message(_("You need write access to {name} to edit its README.").format(name=self.full))
			return
		ui.message(_("Loading README..."))
		ref = self.ref()

		def done(res):
			if not res.ok or not isinstance(res.data, dict):
				ui.message(_("This repository has no README.") if res.code == 404 else res.message)
				return
			context = FileContext(self.full, ref, True)
			show_modal(FileViewDialog(self.dialog, context, res.data))

		self.dialog.run(
			lambda: gh("GET", f"/repos/{self.full}/readme", params={"ref": ref} if ref else None), done
		)

	def toggle_bookmark(self):
		ui.message(_("Bookmark added") if toggle_bookmark(self.full) else _("Bookmark removed"))

	def fork(self):
		if not confirm(self.dialog, _("Fork {name} into your account?").format(name=self.full), _("Fork Repository")):
			return
		ui.message(_("Forking..."))

		def done(res):
			if res.code in (200, 201, 202) and isinstance(res.data, dict):
				ui.message(_("Fork created: {name}. It may take a moment to appear.").format(
					name=res.data.get("full_name", "")
				))
			else:
				ui.message(_("Fork failed.") + " " + res.message)

		self.dialog.run(lambda: gh("POST", f"/repos/{self.full}/forks", body={}), done)

	def copy_clone_url(self):
		copy_to_clipboard(self.repo.get("clone_url", f"https://github.com/{self.full}.git"), _("Clone URL copied."))

	def copy_link(self):
		copy_to_clipboard(self.repo.get("html_url") or f"https://github.com/{self.full}", _("Link copied."))

	def change_default_branch(self):
		if not self._need_admin():
			return
		ui.message(_("Loading branches..."))

		def got(res):
			if not res.ok or not res.data:
				ui.message(_("Could not load branches.") + " " + res.message)
				return
			names = [b["name"] for b in res.data]
			current = self.repo.get("default_branch")
			index = ask_choice(
				self.dialog, _("Change Default Branch"), _("Choose the new default branch:"), names,
				names.index(current) if current in names else 0,
			)
			if index is not None and names[index] != current:
				self._patch({"default_branch": names[index]}, _("Default branch is now {name}.").format(name=names[index]))

		self.dialog.run(lambda: list_branches(self.full), got)

	def toggle_archive(self):
		if not self._need_admin():
			return
		archive = not self.repo.get("archived")
		question = (
			_("Archive this repository? Archived repositories are read only.") if archive
			else _("Unarchive this repository?")
		)
		if confirm(self.dialog, question, _("Archive Repository") if archive else _("Unarchive Repository"), default_no=True):
			self._patch({"archived": archive}, _("Repository archived.") if archive else _("Repository unarchived."))

	def toggle_visibility(self):
		if not self._need_admin():
			return
		to_private = not self.repo.get("private")
		question = (
			_("Make this repository private?") if to_private
			else _("Make this repository public? Everyone will be able to see its code.")
		)
		if confirm(self.dialog, question, _("Change Visibility"), default_no=True):
			self._patch({"private": to_private}, _("Repository is now private.") if to_private else _("Repository is now public."))

	def download(self):
		ref = self.ref()
		name = self.repo.get("name", "repo")
		download_file(f"/repos/{self.full}/zipball/{q(ref, '/')}", f"{name}-{ref.replace('/', '-')}.zip")

	def copy_zip_link(self):
		branch = self.ref() or "main"
		copy_to_clipboard(f"https://github.com/{self.full}/archive/refs/heads/{q(branch, '/')}.zip", _("ZIP link copied."))

	# --- star ----------------------------------------------------------
	def check_star(self, done=None):
		"""Find out whether you starred this repository, then call done()."""
		if not has_token():
			if done:
				done()
			return

		def got(res):
			if res.code in (204, 404):
				self.starred = res.code == 204
			if done:
				done()

		self.dialog.run(lambda: gh("GET", f"/user/starred/{self.full}"), got)

	def toggle_star(self):
		if self.starred is None:
			ui.message(_("Still checking star status."))
			return
		method = "DELETE" if self.starred else "PUT"

		def done(res):
			if res.code == 204:
				self.starred = not self.starred
				ui.message(_("Starred") if self.starred else _("Unstarred"))
			else:
				ui.message(_("Star action failed.") + " " + res.message)

		self.dialog.run(lambda: gh(method, f"/user/starred/{self.full}"), done)

	# --- menus -------------------------------------------------------------
	def menu_entries(self):
		"""The repository menu, in the order GitHub for NVDA documents it."""
		entries = [
			(_("&Open Repository"), self.open),
			(_("Re&name"), self.rename),
			(_("Edit &Description"), self.edit_description),
			(_("Edit &Topics"), self.edit_topics),
			(_("Edit &Website Address"), self.edit_homepage),
			(_("De&lete"), self.delete),
			(_("Open in &Browser"), self.open_in_browser),
			(_("View &README"), self.view_readme),
			(_("&Edit README"), self.edit_readme),
			(_("Remove &Bookmark") if is_bookmarked(self.full) else _("Add &Bookmark"), self.toggle_bookmark),
		]
		if has_token():
			entries.append((_("Un&star") if self.starred else _("&Star"), self.toggle_star))
		entries += [
			(_("&Fork Repository"), self.fork),
			(_("Copy &Clone URL"), self.copy_clone_url),
			(_("Copy Repository &Link"), self.copy_link),
			(_("Copy ZIP L&ink"), self.copy_zip_link),
			(_("Change Default Bra&nch"), self.change_default_branch),
		]
		if permissions(self.repo)[1]:
			entries.append((_("Make &Public") if self.repo.get("private") else _("Make Pri&vate"), self.toggle_visibility))
		entries += [
			(_("&Archive Repository") if not self.repo.get("archived") else _("Un&archive Repository"), self.toggle_archive),
			(_("Download Repo&sitory"), self.download),
		]
		return entries


# ----------------------------------------------------------------------
# repobrowser.py: Repository browser, used for your own repositories and other people's.
# ----------------------------------------------------------------------


def repo_label(r):
	description = r.get("description") or _("No description")
	return _("{name}, {stars} stars, {language}, {description}").format(
		name=r.get("full_name"), stars=r.get("stargazers_count", 0),
		language=r.get("language") or _("unknown language"), description=description,
	)


def repo_info_text(repo, can_write, can_admin):
	license_name = (repo.get("license") or {}).get("name") or _("none")
	topics = ", ".join(repo.get("topics") or []) or _("none")
	flags = (_(", archived") if repo.get("archived") else "") + (_(", fork") if repo.get("fork") else "")
	access = _("admin") if can_admin else _("write") if can_write else _("read only")
	return "\n".join([
		str(repo.get("full_name")),
		repo.get("description") or _("No description"),
		_("{visibility}{flags}. Default branch: {branch}.").format(
			visibility=_("Private") if repo.get("private") else _("Public"), flags=flags,
			branch=repo.get("default_branch", _("unknown")),
		),
		_("Stars: {stars}. Forks: {forks}. Open issues and pull requests: {issues}.").format(
			stars=repo.get("stargazers_count", 0), forks=repo.get("forks_count", 0),
			issues=repo.get("open_issues_count", 0),
		),
		_("Language: {language}. License: {license}. Topics: {topics}.").format(
			language=repo.get("language") or _("unknown"), license=license_name, topics=topics
		),
		_("Last updated: {date}. Your access: {access}.").format(
			date=format_date(repo.get("updated_at")), access=access
		),
	])


class RepoBrowserDialog(AccessibleDialog):
	"""Browse folders and files, and work with a repository."""

	def __init__(self, parent, repo, ref=None, path="", open_file=None):
		super().__init__(parent, repo["full_name"], (800, 660))
		self.repo = repo
		self.full = repo["full_name"]
		self.ref = ref or repo.get("default_branch") or ""
		self.path = ""
		self.entries, self.shown = [], []
		self._search_timer = None
		self.can_write, self.can_admin = permissions(repo)
		self.has_token = has_token()
		self._open_after_load = open_file
		self._load_id = 0
		self.items = ItemActions(self)
		self.actions = RepoActions(
			self, repo, on_changed=self._repo_changed, on_deleted=lambda: self.EndModal(wx.ID_CANCEL),
			ref_fn=lambda: self.ref,
		)

		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(self, label=_("Repository &information:")), 0, wx.ALL, PAD)
		self.info = wx.TextCtrl(
			self, value=repo_info_text(repo, self.can_write, self.can_admin),
			style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 120),
		)
		sizer.Add(self.info, 0, wx.EXPAND | wx.ALL, PAD)
		sizer.Add(wx.StaticText(self, label=_("&Search files in this folder:")), 0, wx.LEFT, PAD)
		# A plain box, like the search box in My Repositories: focus leaves it only with Tab.
		self.search = wx.TextCtrl(self)
		self.search.Bind(wx.EVT_TEXT, self._on_search_text)
		sizer.Add(self.search, 0, wx.EXPAND | wx.ALL, PAD)
		self.lbl_path = wx.StaticText(self, label=_("&Files"))
		sizer.Add(self.lbl_path, 0, wx.LEFT, PAD)
		self.listbox = wx.ListBox(self)
		self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, self.on_open)
		self.listbox.Bind(wx.EVT_KEY_DOWN, self.on_key)
		self.listbox.enter_action = lambda: self.on_open(None)
		self.listbox.delete_action = self.on_delete_file
		self.listbox.Bind(wx.EVT_CONTEXT_MENU, self.on_item_menu)
		sizer.Add(self.listbox, 1, wx.EXPAND | wx.ALL, PAD)

		row = wx.WrapSizer(wx.HORIZONTAL)
		self.add_button(row, _("Go &Up"), self.on_up)
		self.add_button(row, _("Switch &Branch"), self.on_branch)
		self.add_button(row, _("Commit &History"), self.on_commits)
		self.add_button(row, _("&Issues"), self.on_issues)
		self.add_button(row, _("Pull &Requests"), self.on_pulls)
		self.add_button(row, _("A&ctions"), self.on_actions)
		self.add_button(row, _("Rel&eases"), self.on_releases)
		self.add_button(row, _("&More Options"), self.on_more_options)
		row.Add(self.close_button(), 0, wx.ALL, 3)
		sizer.Add(row, 0, wx.ALIGN_CENTER)
		self.layout(sizer)
		self.load_dir(path)

	# --- context for sub windows ---------------------------------------
	def context(self):
		return FileContext(self.full, self.ref, self.can_write)

	# --- navigation ----------------------------------------------------
	def load_dir(self, path, select_name=None):
		ui.message(_("Loading files..."))
		self._load_id += 1
		load_id = self._load_id
		params = {"ref": self.ref} if self.ref else None

		def done(res):
			if load_id != self._load_id:
				return
			message = str(res.data.get("message", "")).lower() if isinstance(res.data, dict) else ""
			if res.code in (404, 409) and "empty" in message:
				data = []
			elif res.ok and isinstance(res.data, dict) and res.data.get("type") == "file":
				# The path points at a file: show its folder and open the file.
				parent_path = os.path.dirname(path)
				self._open_after_load = res.data
				self.load_dir(parent_path, select_name=res.data.get("name"))
				return
			elif not res.ok or not isinstance(res.data, list):
				ui.message(_("Could not load files.") + " " + res.message)
				return
			else:
				data = res.data
			data = sorted(data, key=lambda e: (e.get("type") != "dir", e.get("name", "").lower()))
			self.entries, self.path = data, path
			where = "/" + path if path else _("/ (top folder)")
			self.lbl_path.SetLabel(
				_("&Files in {where} on branch {branch}").format(
					where=escape_label(where), branch=escape_label(self.ref or _("default"))
				)
			)
			self.search.ChangeValue("")
			self._show_entries(select_name)
			if data:
				self.listbox.SetFocus()
			ui.message(_("{count} items").format(count=len(data)) if data else _("This folder is empty"))
			pending, self._open_after_load = self._open_after_load, None
			if pending:
				wx.CallAfter(self.open_entry, pending)

		self.run(lambda: gh("GET", content_url(self.full, path), params=params), done)

	@staticmethod
	def _entry_label(e):
		kind = e.get("type")
		if kind == "dir":
			return _("Folder: {name}").format(name=e["name"])
		if kind == "submodule":
			return _("Submodule: {name}").format(name=e["name"])
		return _("File: {name}, {size}").format(name=e["name"], size=format_size(e.get("size")))

	def _show_entries(self, select_name=None):
		"""Fill the file list with the entries of this folder that match the search box."""
		words = self.search.GetValue().lower().split()
		self.shown = [e for e in self.entries if all(w in e.get("name", "").lower() for w in words)]
		self.listbox.Set([self._entry_label(e) for e in self.shown])
		if self.shown:
			index = next((i for i, e in enumerate(self.shown) if e["name"] == select_name), 0)
			self.listbox.SetSelection(index)

	def _on_search_text(self, event):
		self._show_entries()
		if self._search_timer is not None:
			self._search_timer.Stop()
		self._search_timer = wx.CallLater(700, self._announce_search)

	def _announce_search(self):
		if self.alive:
			ui.message(_("{count} matching items").format(count=len(self.shown)))

	def _selected(self):
		i = self.listbox.GetSelection()
		return self.shown[i] if 0 <= i < len(self.shown) else None

	def on_key(self, event):
		code = event.GetKeyCode()
		if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			self.on_open(None)
		elif code in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
			self.on_delete_file()
		elif code == wx.WXK_BACK:
			self.on_up(None)
		elif code == wx.WXK_F5:
			self.load_dir(self.path)
		elif code == ord("F") and event.ControlDown():
			self.search.SetFocus()
		else:
			event.Skip()

	def open_entry(self, entry):
		if not self.alive:
			return
		kind = entry.get("type")
		if kind == "dir":
			self.load_dir(entry["path"])
		elif kind == "submodule":
			ui.message(_("This is a submodule. Open its repository separately."))
		else:
			show_modal(FileViewDialog(self, self.context(), entry))

	def on_open(self, event):
		entry = self._selected()
		if entry:
			self.open_entry(entry)
		else:
			ui.message(_("Select a file or folder first."))

	def on_up(self, event):
		if self.path:
			parts = self.path.split("/")
			self.load_dir("/".join(parts[:-1]), select_name=parts[-1])
		else:
			ui.message(_("Already at the top folder."))

	# --- branches ---------------------------------------------------------
	def on_branch(self, event):
		ui.message(_("Loading branches..."))

		def done(res):
			if not res.ok or not res.data:
				ui.message(_("Could not load branches.") + " " + res.message)
				return
			names = [b["name"] for b in res.data]
			index = ask_choice(
				self, _("Switch Branch"), _("Choose a branch:"), names,
				names.index(self.ref) if self.ref in names else 0,
			)
			if index is not None:
				self.ref = names[index]
				ui.message(_("Branch {name}").format(name=self.ref))
				self.load_dir("")

		self.run(lambda: list_branches(self.full), done)

	def on_new_branch(self):
		name = prompt_text(
			self, _("Create Branch"), _("&New branch name (created from {branch}):").format(branch=escape_label(self.ref))
		)
		name = clean_branch_name(name)
		if not name:
			return

		def work():
			branch = gh("GET", f"/repos/{self.full}/branches/{q(self.ref, '/')}")
			if not branch.ok:
				return branch
			return gh(
				"POST", f"/repos/{self.full}/git/refs",
				body={"ref": f"refs/heads/{name}", "sha": branch.data["commit"]["sha"]},
			)

		def done(res):
			if res.ok:
				self.ref = name
				ui.message(_("Branch {name} created and selected.").format(name=name))
				self.load_dir("")
			else:
				ui.message(_("Could not create the branch.") + " " + res.message)

		self.run(work, done)

	def on_delete_branch(self):
		default = self.repo.get("default_branch")
		if not self.ref or self.ref == default:
			ui.message(_("The default branch cannot be deleted. Switch to another branch first."))
			return
		if not confirm_typed(
			self, _("Delete Branch"),
			_("This permanently deletes the branch {name}.").format(name=self.ref), self.ref,
		):
			ui.message(_("Deletion cancelled."))
			return
		deleted = self.ref

		def done(res):
			if res.ok:
				self.ref = default
				ui.message(_("Branch {name} deleted. Showing {default}.").format(name=deleted, default=default))
				self.load_dir("")
			else:
				ui.message(_("Could not delete the branch.") + " " + res.message)

		self.run(lambda: gh("DELETE", f"/repos/{self.full}/git/refs/heads/{q(deleted, '/')}"), done)

	# --- windows ------------------------------------------------------------
	def on_commits(self, event):
		from .repotools import show_commits
		show_commits(self, self.full, self.ref, self.path)

	def on_issues(self, event):
		from .conversation import show_issues
		show_issues(self, self.full, self.can_write)

	def on_pulls(self, event):
		from .conversation import show_pulls
		show_pulls(self, self.full, self.ref, self.repo.get("default_branch", ""), self.can_write)

	def on_actions(self, event):
		from .conversation import show_runs
		show_runs(self, self.full, self.ref, self.can_write)

	def on_releases(self, event):
		from .repotools import show_releases
		show_releases(self, self.full, self.can_write, self.ref)

	# --- menus --------------------------------------------------------------
	def _repo_changed(self, repo):
		"""Refresh the window after the repository was renamed, edited or archived."""
		self.repo = repo
		self.actions.repo = repo
		self.full = repo["full_name"]
		self.can_write, self.can_admin = permissions(repo)
		self.SetTitle(self.full)
		self.info.SetValue(repo_info_text(repo, self.can_write, self.can_admin))

	def _more_entries(self):
		"""The repository-wide write actions."""
		if not self.can_write:
			return []
		return [
			(_("Create &New File..."), self.on_new_file),
			(_("Create New F&older..."), self.on_new_folder),
			(_("&Upload Files..."), self.on_upload),
			(_("Upload Fol&der..."), self.on_upload_folder),
			(_("Create &Branch..."), self.on_new_branch),
			(_("Delete Current B&ranch..."), self.on_delete_branch),
		]

	def on_more_options(self, event):
		"""The More Options menu: everything the buttons and the repository menu do not cover."""
		entries = self._more_entries()
		if not entries:
			ui.message(_("No more options. Star, visibility and copy links are in the Applications key menu in My Repositories."))
			return
		if event is not None and isinstance(event, wx.ContextMenuEvent):
			self.popup_menu(entries, self.menu_position(event, self.listbox))
		else:
			self.popup_menu(entries)

	def on_item_menu(self, event):
		"""Applications key (or right click) on a file or folder: a menu for that item."""
		entry = self._selected()
		if entry is None:
			self.on_more_options(event)
			return
		entries = self.items.menu_entries(entry, self._more_entries())
		self.popup_menu(entries, self.menu_position(event, self.listbox))

	# --- write operations ------------------------------------------------------
	def on_new_file(self, target=None):
		base = self.path if target is None else target
		values = ask_form(
			self, _("Create New File in {name}").format(name=self.repo.get("name")),
			[
				(_("File &name with extension (for example notes.txt, index.html, script.py, data.json or README.md):"), "name", False, "", True),
				(_("File &content:"), "content", True, ""),
				(_("Commit &message:"), "message", False, _("Create file via GitHub for NVDA")),
			],
			ok_label=_("&Create File"),
		)
		if not values:
			return
		relative = (base + "/" if base else "") + values["name"].strip().strip("/")
		ui.message(_("Saving..."))

		def work():
			exists, sha, is_folder = find_sha(self.full, relative, self.ref)
			if is_folder:
				return Result(-3, None, {})
			if exists and not ask_yes_no(self, _("'{path}' already exists. Overwrite it?").format(path=relative), _("File Exists")):
				return Result(-2, None, {})
			return commit_file(
				self.full, relative, self.ref, values["message"].strip() or _("Create file"),
				values["content"].encode("utf-8"), sha,
			)

		def done(res):
			if res.code == -2:
				ui.message(_("Cancelled. Nothing was changed."))
			elif res.code == -3:
				ui.message(_("A folder with that name already exists."))
			elif res.ok:
				ui.message(_("File saved."))
				self.load_dir(self.path)
			else:
				ui.message(_("Could not save the file.") + " " + res.message)

		self.run(work, done)

	def on_new_folder(self, target=None):
		base = self.path if target is None else target
		values = ask_form(
			self, _("Create New Folder in {name}").format(name=self.repo.get("name")),
			[
				(_("Folder &name (for example docs, or docs/images for a nested folder):"), "name", False, "", True),
				(_("Commit &message:"), "message", False, _("Create folder via GitHub for NVDA")),
			],
			ok_label=_("Create &Folder"),
		)
		if not values:
			return
		folder = values["name"].strip().strip("/")
		if not folder:
			ui.message(_("Type a folder name."))
			return
		relative = (base + "/" if base else "") + folder
		# Git cannot store an empty folder, so GitHub's own web page adds a placeholder file. Do the same.
		placeholder = relative + "/.gitkeep"
		ui.message(_("Creating folder..."))

		def work():
			exists, sha, is_folder = find_sha(self.full, relative, self.ref)
			if is_folder:
				return Result(-3, None, {})
			if exists:
				return Result(-4, None, {})
			return commit_file(
				self.full, placeholder, self.ref, values["message"].strip() or _("Create folder"), b"", None,
			)

		def done(res):
			if res.code == -3:
				ui.message(_("A folder with that name already exists."))
			elif res.code == -4:
				ui.message(_("A file with that name already exists."))
			elif res.ok:
				ui.message(_("Folder created. It contains an empty .gitkeep file, because Git cannot store empty folders."))
				self.load_dir(self.path)
			else:
				ui.message(_("Could not create the folder.") + " " + res.message)

		self.run(work, done)

	def on_upload(self):
		self.items.upload_files()

	def on_upload_folder(self):
		self.items.upload_folder()

	def on_delete_file(self):
		if not self.can_write:
			ui.message(_("You do not have write access to this repository, so files cannot be deleted."))
			return
		entry = self._selected()
		if not entry or entry.get("type") != "file":
			ui.message(_("Select a file to delete. Folders cannot be deleted here."))
			return
		if not confirm(
			self,
			_("Delete '{path}' from branch {branch}? This creates a commit; it can be restored from history.").format(
				path=entry["path"], branch=self.ref
			),
			_("Delete File"), warning=True, default_no=True,
		):
			return
		default = _("Delete {name}").format(name=entry["name"])
		message = prompt_text(self, _("Commit Message"), _("&Commit message:"), default)
		if message is None:
			return

		def done(res):
			if res.ok:
				ui.message(_("{name} deleted.").format(name=entry["name"]))
				self.load_dir(self.path)
			else:
				ui.message(_("Delete failed.") + " " + res.message)

		self.run(
			lambda: delete_file(self.full, entry["path"], self.ref, message.strip() or default, entry["sha"]), done
		)


# ----------------------------------------------------------------------
# Generic list of repositories fetched from an API endpoint
# ----------------------------------------------------------------------

def show_repo_list(parent, title, url, params=None, empty=None, unwrap_items=False):
	from .dialogs import PagedListDialog

	def fetch(page, selections):
		query = dict(params or {})
		query.update(per_page=items_per_page(), page=page)
		res = gh("GET", url, params=query)
		if unwrap_items and res.ok and isinstance(res.data, dict):
			return Result(res.code, res.data.get("items", []), res.headers)
		return res

	def bookmark(dlg, repo):
		ui.message(_("Bookmark added") if toggle_bookmark(repo["full_name"]) else _("Bookmark removed"))

	actions = [
		(_("&Open"), lambda d, r: open_repo(d, r["full_name"])),
		(_("Open in &Browser"), lambda d, r: open_url(r.get("html_url"))),
		(_("Add or Remove &Bookmark"), bookmark),
	]
	show_modal(PagedListDialog(
		parent, title, fetch, repo_label, actions, empty=empty or _("No repositories found.")
	))


# ----------------------------------------------------------------------
# myrepos.py: My Repositories: list, search, sort, open, create and delete.
# ----------------------------------------------------------------------

_sort_index = 0  # remembered while NVDA runs


def sort_names():
	return [
		_("Name (A-Z)"), _("Name (Z-A)"), _("Recently updated"), _("Least recently updated"),
		_("Private only"), _("Public only"), _("Forks only"), _("Archived only"),
	]


def sort_repos(repos, sort_index, query=""):
	"""Filter and order repositories. sort_index refers to sort_names()."""
	query = (query or "").strip().lower()
	out = [r for r in repos if query in r.get("full_name", "").lower()] if query else list(repos)
	if sort_index == 4:
		out = [r for r in out if r.get("private")]
	elif sort_index == 5:
		out = [r for r in out if not r.get("private")]
	elif sort_index == 6:
		out = [r for r in out if r.get("fork")]
	elif sort_index == 7:
		out = [r for r in out if r.get("archived")]
	if sort_index == 1:
		out.sort(key=lambda r: r.get("name", "").lower(), reverse=True)
	elif sort_index == 2:
		out.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
	elif sort_index == 3:
		out.sort(key=lambda r: r.get("updated_at", ""))
	else:
		out.sort(key=lambda r: r.get("name", "").lower())
	return out


def repo_row(r, my_login=""):
	visibility = _("Private") if r.get("private") else _("Public")
	owner = (r.get("owner") or {}).get("login", "")
	name = r["name"] if not owner or owner.lower() == my_login.lower() else r["full_name"]
	extras = []
	if r.get("fork"):
		extras.append(_("fork"))
	if r.get("archived"):
		extras.append(_("archived"))
	return (
		f"{name}, {visibility}" + (", " + ", ".join(extras) if extras else "")
		+ _(", {stars} stars").format(stars=r.get("stargazers_count", 0))
	)


class MyReposDialog(AccessibleDialog):
	def __init__(self, parent):
		super().__init__(parent, _("My Repositories"), (680, 580))
		self.raw, self.display, self.my_login = [], [], ""
		sizer = wx.BoxSizer(wx.VERTICAL)
		row = wx.BoxSizer(wx.HORIZONTAL)
		# Sort by comes first so it is the first stop of the Tab key, before the search box.
		row.Add(wx.StaticText(self, label=_("S&ort by:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, PAD)
		self.sort = wx.Choice(self, choices=sort_names())
		self.sort.SetSelection(_sort_index)
		self.sort.Bind(wx.EVT_CHOICE, self.on_sort)
		row.Add(self.sort, 0, wx.ALL, PAD)
		row.Add(wx.StaticText(self, label=_("&Search:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, PAD)
		self.search = wx.TextCtrl(self)
		self.search.Bind(wx.EVT_TEXT, lambda e: self.render())
		row.Add(self.search, 1, wx.ALL, PAD)
		sizer.Add(row, 0, wx.EXPAND)
		sizer.Add(
			wx.StaticText(self, label=_("&Repositories (Enter opens, Delete deletes, Applications key for more):")),
			0, wx.LEFT, PAD,
		)
		self.listbox = wx.ListBox(self)
		self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, self.on_open)
		self.listbox.Bind(wx.EVT_KEY_DOWN, self.on_key)
		self.listbox.enter_action = lambda: self.on_open(None)
		self.listbox.delete_action = lambda: self.on_delete(None)
		self.listbox.Bind(wx.EVT_CONTEXT_MENU, self.on_context_menu)
		sizer.Add(self.listbox, 1, wx.EXPAND | wx.ALL, PAD)
		buttons = wx.WrapSizer(wx.HORIZONTAL)
		self.add_button(buttons, _("&New Repository"), self.on_create)
		self.add_button(buttons, _("Re&fresh"), lambda e: self.load())
		buttons.Add(self.close_button(), 0, wx.ALL, 3)
		sizer.Add(buttons, 0, wx.ALIGN_CENTER)
		self.layout(sizer, focus=self.search)
		self.load()

	def load(self):
		ui.message(_("Fetching repositories..."))

		def work():
			me = gh("GET", "/user")
			repos = gh_paged("/user/repos", {"sort": "updated", "affiliation": "owner,collaborator,organization_member"}, max_items=3000)
			return Result(200, (me, repos), {})

		def done(res):
			if not res.ok:
				ui.message(_("Could not load repositories.") + " " + res.message)
				return
			me, repos = res.data
			if not repos.ok:
				ui.message(_("Could not load repositories.") + " " + repos.message)
				return
			self.my_login = me.data.get("login", "") if me.ok and isinstance(me.data, dict) else ""
			self.raw = repos.data
			self.render()
			ui.message(_("{count} repositories").format(count=len(self.raw)))
			self.listbox.SetFocus()

		self.run(work, done)

	def render(self, keep=None):
		self.display = sort_repos(self.raw, self.sort.GetSelection(), self.search.GetValue())
		self.listbox.Set([repo_row(r, self.my_login) for r in self.display])
		if self.display:
			names = [r.get("full_name") for r in self.display]
			self.listbox.SetSelection(names.index(keep) if keep in names else 0)

	def on_sort(self, event):
		global _sort_index
		_sort_index = self.sort.GetSelection()
		self.render()

	def on_key(self, event):
		code = event.GetKeyCode()
		if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			self.on_open(None)
		elif code in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
			self.on_delete(None)
		elif code == wx.WXK_F5:
			self.load()
		else:
			event.Skip()

	def _selected(self):
		i = self.listbox.GetSelection()
		if i == wx.NOT_FOUND or i >= len(self.display):
			ui.message(_("Select a repository first."))
			return None
		return self.display[i]

	def _actions(self, repo):
		return RepoActions(self, repo, on_changed=lambda r: self.render(r.get("full_name")), on_deleted=self.load)

	def on_open(self, event):
		repo = self._selected()
		if repo:
			open_repo(self, repo["full_name"])

	def on_context_menu(self, event):
		repo = self._selected()
		if not repo:
			return
		position = self.menu_position(event, self.listbox)
		actions = self._actions(repo)
		# Learn whether the repository is starred first, so the menu says Star or Unstar correctly.
		actions.check_star(lambda: self.popup_menu(actions.menu_entries(), position))

	def on_create(self, event):
		if create_repository(self):
			self.load()

	def on_delete(self, event):
		repo = self._selected()
		if repo:
			self._actions(repo).delete()


def show_my_repos(parent=None):
	show_modal(MyReposDialog(parent))


# ----------------------------------------------------------------------
# navigation.py: Open repositories and any pasted GitHub link.
# ----------------------------------------------------------------------

def open_repo(parent, full_name, ref=None, path="", open_file=None):
	"""Fetch fresh details for owner/name and open the repository browser."""

	ui.message(_("Opening {name}...").format(name=full_name))

	def done(res):
		if res.ok and isinstance(res.data, dict):
			show_modal(RepoBrowserDialog(parent, res.data, ref, path, open_file))
		else:
			ui.message(_("Could not open {name}.").format(name=full_name) + " " + res.message)

	run_async(lambda: gh("GET", f"/repos/{full_name}"), done)


def open_blob_or_tree(parent, ref):
	"""Open a github.com/.../blob/... or /tree/... link."""
	full = ref.full_name
	ui.message(_("Opening {name}...").format(name=full))

	def work():
		repo = gh("GET", f"/repos/{full}")
		if not repo.ok:
			return repo
		resolved = resolve_ref_path(full, ref.rest)
		return Result(200, (repo.data, resolved), {})

	def done(res):
		if not res.ok:
			ui.message(_("Could not open {name}.").format(name=full) + " " + res.message)
			return
		repo, resolved = res.data
		if not resolved:
			show_modal(RepoBrowserDialog(parent, repo))
			return
		branch, path, payload = resolved
		if isinstance(payload, dict) and payload.get("type") == "file":
			# Show the folder the file is in, so Escape from the viewer leaves you next to it.
			show_modal(RepoBrowserDialog(parent, repo, branch, path.rpartition("/")[0], payload))
		else:
			show_modal(RepoBrowserDialog(parent, repo, branch, path))

	run_async(work, done)


def open_link(parent, text):
	"""Open whatever a pasted GitHub address points at. Returns False if it is not one."""
	ref = parse_github_link(text)
	if ref is None:
		return False
	if ref.kind == "repo":
		open_repo(parent, ref.full_name)
	elif ref.kind == "user":
		from .profile import show_user
		show_user(parent, ref.owner)
	elif ref.kind in ("issue", "pull"):
		from .conversation import show_conversation
		show_conversation(parent, ref.full_name, ref.number, ref.kind == "pull")
	elif ref.kind == "commit":
		from .repotools import show_commit
		show_commit(parent, ref.full_name, ref.number)
	elif ref.kind in ("blob", "tree"):
		open_blob_or_tree(parent, ref)
	elif ref.kind == "run":
		from .conversation import show_run
		show_run(parent, ref.full_name, ref.number)
	return True
