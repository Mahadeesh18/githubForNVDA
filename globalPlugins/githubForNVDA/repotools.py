# GitHub for NVDA
# Copyright (C) 2025-2026 Mahadeesh and contributors
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for more details.

"""Repository helpers: API calls for files and commits, downloads, bookmarks, commit history and releases."""

import addonHandler
import base64
import json
import os
import time
import ui
from collections import namedtuple
from logHandler import log
from urllib.parse import quote

from .logic import (
	format_date,
	format_size,
	safe_filename,
	truncate,
	unique_path,
)
from .core import (
	Result,
	config_dir,
	downloads_dir,
	gh,
	gh_paged,
	items_per_page,
	open_url,
	run_async,
	say,
)
from .dialogs import (
	PagedListDialog,
	TextViewDialog,
	ask_choice,
	ask_form,
	confirm_typed,
	copy_to_clipboard,
	local_fetch,
	show_modal,
)

addonHandler.initTranslation()


# ----------------------------------------------------------------------
# repoapi.py: Repository helpers shared by the file viewer, repository browser and uploads.
# ----------------------------------------------------------------------

# What the file viewer needs to know about where a file lives.
FileContext = namedtuple("FileContext", "full ref can_write")


def q(text, safe=""):
	"""Percent-encode part of a URL."""
	return quote(str(text), safe=safe)


def content_url(full, path):
	return f"/repos/{full}/contents/{q(path, '/')}" if path else f"/repos/{full}/contents"


def permissions(repo):
	"""Return (can_write, can_admin) for a repository payload."""
	perms = repo.get("permissions") or {}
	archived = bool(repo.get("archived"))
	can_admin = bool(perms.get("admin"))
	can_write = bool(perms.get("push") or can_admin) and not archived
	return can_write, can_admin


def commit_file(full, path, ref, message, data_bytes, sha=None, retries=2):
	"""Create or update a file. Retries when GitHub reports a concurrent commit."""
	body = {"message": message, "content": base64.b64encode(data_bytes).decode("ascii")}
	if ref:
		body["branch"] = ref
	if sha:
		body["sha"] = sha
	result = Result(0, "", {})
	for attempt in range(retries + 1):
		result = gh("PUT", content_url(full, path), body=body)
		if result.code == 409 and attempt < retries:
			time.sleep(1.0 + attempt)
			continue
		break
	return result


def delete_file(full, path, ref, message, sha):
	body = {"message": message, "sha": sha}
	if ref:
		body["branch"] = ref
	return gh("DELETE", content_url(full, path), body=body)


def find_sha(full, path, ref):
	"""Return (exists, sha, is_folder) for a path on a branch."""
	res = gh("GET", content_url(full, path), params={"ref": ref} if ref else None)
	if res.ok and isinstance(res.data, dict) and res.data.get("type") == "file":
		return True, res.data.get("sha"), False
	if res.ok and isinstance(res.data, list):
		return False, None, True
	return False, None, False


def list_branches(full):
	return gh_paged(f"/repos/{full}/branches", max_items=1000)


def fetch_tree(full, ref):
	"""Every file path in a branch, using one API call."""
	return gh("GET", f"/repos/{full}/git/trees/{q(ref, '/')}", params={"recursive": "1"}, timeout=60)


def resolve_ref_path(full, segments):
	"""Work out which part of 'main/src/app.py' is the branch and which the path.

	Branch names can contain slashes, so try the shortest branch first.
	Returns (branch, path, contents payload) or None.
	"""
	segments = [s for s in segments if s]
	for cut in range(1, min(len(segments), 4) + 1):
		branch = "/".join(segments[:cut])
		path = "/".join(segments[cut:])
		res = gh("GET", content_url(full, path), params={"ref": branch})
		if res.ok:
			return branch, path, res.data
	return None


# ----------------------------------------------------------------------
# Git data helpers: change many files in ONE commit
# ----------------------------------------------------------------------

def create_blob(full, data_bytes):
	"""Store file content in the repository and return a Result whose data is the blob sha."""
	if data_bytes:
		body = {"content": base64.b64encode(data_bytes).decode("ascii"), "encoding": "base64"}
	else:
		body = {"content": "", "encoding": "utf-8"}
	res = gh("POST", f"/repos/{full}/git/blobs", body=body, timeout=120, retries=1)
	if res.ok and isinstance(res.data, dict):
		return Result(res.code, res.data.get("sha"), res.headers)
	return res


def tree_items(full, ref, path):
	"""Every file under a folder (or the one file) at `path`, taken from the branch's tree.

	Returns a Result whose data is a list of tree entries (path, mode, type, sha, size).
	"""
	res = fetch_tree(full, ref)
	if not res.ok or not isinstance(res.data, dict):
		return res
	if res.data.get("truncated"):
		return Result(0, "This repository is too large to list in one request.", {})
	prefix = path.strip("/")
	items = [
		e for e in res.data.get("tree", [])
		if e.get("type") in ("blob", "commit") and (e["path"] == prefix or e["path"].startswith(prefix + "/"))
	]
	return Result(200, items, res.headers)


def commit_tree(full, ref, message, entries, retries=2):
	"""Commit several file changes at once.

	entries: list of dicts {"path", "mode", "type", "sha"}; sha None removes the path.
	"""
	result = Result(0, "", {})
	for attempt in range(retries + 1):
		branch = gh("GET", f"/repos/{full}/branches/{q(ref, '/')}")
		if not branch.ok:
			return branch
		head = branch.data["commit"]["sha"]
		base_tree = branch.data["commit"]["commit"]["tree"]["sha"]
		tree = gh("POST", f"/repos/{full}/git/trees", body={"base_tree": base_tree, "tree": entries}, timeout=120)
		if not tree.ok:
			return tree
		commit = gh(
			"POST", f"/repos/{full}/git/commits",
			body={"message": message, "tree": tree.data["sha"], "parents": [head]},
		)
		if not commit.ok:
			return commit
		result = gh("PATCH", f"/repos/{full}/git/refs/heads/{q(ref, '/')}", body={"sha": commit.data["sha"]})
		if result.code in (409, 422) and attempt < retries:
			time.sleep(1.0 + attempt)
			continue
		break
	return result


# ----------------------------------------------------------------------
# downloads.py: Download files from GitHub into the Downloads folder with spoken progress.
# ----------------------------------------------------------------------

def download_file(url, name, accept=None, params=None, done=None):
	"""Stream a GitHub URL to disk. done(result) is called on the GUI thread.

	The result's data is the saved path when the download worked.
	"""

	def work():
		res = gh("GET", url, params=params, accept=accept, stream=True, timeout=60)
		if not res.ok:
			return res
		response = res.data
		destination = unique_path(downloads_dir(), safe_filename(name))
		try:
			total = int(response.headers.get("Content-Length") or 0)
		except ValueError:
			total = 0
		received, next_mark = 0, 25
		try:
			with open(destination, "wb") as f:
				for chunk in response.iter_content(65536):
					f.write(chunk)
					received += len(chunk)
					if total and next_mark < 100 and received * 100 // total >= next_mark:
						say(_("{percent} percent").format(percent=next_mark))
						next_mark += 25
		except Exception as e:
			return Result(0, str(e), {})
		finally:
			response.close()
		return Result(200, destination, {})

	def finished(res):
		if res.ok:
			say(_("Download complete. Saved to {path}").format(path=res.data))
		else:
			say(_("Download failed.") + " " + res.message)
		if done:
			done(res)

	say(_("Downloading {name}").format(name=name))
	run_async(work, finished)


# ----------------------------------------------------------------------
# bookmarks.py: Bookmarked repositories, stored in the add-on's folder.
# ----------------------------------------------------------------------

def _bookmarks_path():
	return os.path.join(config_dir(), "bookmarks.json")


def get_bookmarks():
	try:
		with open(_bookmarks_path(), "r", encoding="utf-8") as f:
			data = json.load(f)
		return [x for x in data if isinstance(x, str)] if isinstance(data, list) else []
	except FileNotFoundError:
		return []
	except Exception:
		log.debugWarning("GitHub for NVDA: could not read bookmarks", exc_info=True)
		return []


def _save_bookmarks(items):
	try:
		temporary = _bookmarks_path() + ".tmp"
		with open(temporary, "w", encoding="utf-8") as f:
			json.dump(items, f, indent=1)
		os.replace(temporary, _bookmarks_path())
		return True
	except OSError:
		log.error("GitHub for NVDA: could not save bookmarks", exc_info=True)
		ui.message(_("Could not save bookmarks."))
		return False


def is_bookmarked(full_name):
	return full_name.lower() in [b.lower() for b in get_bookmarks()]


def toggle_bookmark(full_name):
	"""Add or remove a bookmark. Returns True if it is now bookmarked."""
	items = get_bookmarks()
	lowered = [b.lower() for b in items]
	if full_name.lower() in lowered:
		items.pop(lowered.index(full_name.lower()))
		_save_bookmarks(items)
		return False
	items.append(full_name)
	_save_bookmarks(items)
	return True


def rename_bookmark(old_name, new_name):
	"""Keep a bookmark pointing at a repository that was renamed."""
	items = get_bookmarks()
	changed = False
	for index, name in enumerate(items):
		if name.lower() == old_name.lower():
			items[index] = new_name
			changed = True
	if changed:
		_save_bookmarks(items)


def remove_bookmark(full_name):
	items = [b for b in get_bookmarks() if b.lower() != full_name.lower()]
	_save_bookmarks(items)


def show_bookmarks(parent):
	from .repos import open_repo

	def fetch(page, selections):
		return local_fetch(sorted(get_bookmarks(), key=str.lower))(page, selections)

	def remove_action(dlg, name):
		toggle_bookmark(name)
		ui.message(_("Removed {name}").format(name=name))
		dlg.reload()

	actions = [
		(_("&Open"), lambda d, n: open_repo(d, n)),
		(_("&Remove Bookmark"), remove_action),
		(_("Open in &Browser"), lambda d, n: open_url(f"https://github.com/{n}")),
	]
	show_modal(PagedListDialog(
		parent, _("Bookmarked Repositories"), fetch, lambda n: n, actions,
		empty=_("You have no bookmarks. Open a repository and choose Add Bookmark."),
	))


# ----------------------------------------------------------------------
# commits.py: Commit history for a repository, branch, folder or single file.
# ----------------------------------------------------------------------

MAX_DIFF_CHARS = 200000


def commit_label(c):
	commit = c.get("commit") or {}
	lines = (commit.get("message") or "").splitlines()
	first = lines[0] if lines else _("(no message)")
	author = (commit.get("author") or {}).get("name") or _("unknown")
	date = format_date((commit.get("author") or {}).get("date"))
	return _("{sha}, {message}, by {author}, {date}").format(
		sha=c.get("sha", "")[:7], message=first, author=author, date=date
	)


def commit_summary(c):
	commit = c.get("commit") or {}
	author = commit.get("author") or {}
	stats = c.get("stats") or {}
	files = c.get("files") or []
	lines = [
		_("Commit {sha}").format(sha=c.get("sha", "")),
		_("Author: {name} on {date}").format(name=author.get("name", _("unknown")), date=author.get("date", "")),
		_("Changes: {additions} additions, {deletions} deletions in {count} files").format(
			additions=stats.get("additions", 0), deletions=stats.get("deletions", 0), count=len(files)
		),
		"",
		commit.get("message", ""),
		"",
		_("Files changed:"),
	]
	for f in files:
		lines.append(
			f"{f.get('status', 'changed')}: {f.get('filename')} (+{f.get('additions', 0)} -{f.get('deletions', 0)})"
		)
	if len(files) >= 300:
		lines.append(_("GitHub lists at most 300 files here. Use Show Full Diff or the browser for the rest."))
	return "\n".join(lines)


def show_commit(parent, full, sha):
	"""Load one commit and show its details."""
	ui.message(_("Loading commit..."))

	def done(res):
		if not res.ok or not isinstance(res.data, dict):
			ui.message(_("Could not load the commit.") + " " + res.message)
			return
		commit = res.data

		def load_diff(dialog):
			ui.message(_("Loading full diff..."))

			def got(result):
				if not result.ok:
					ui.message(_("Could not load the diff.") + " " + result.message)
					return
				text = result.data if isinstance(result.data, str) else str(result.data)
				dialog.set_text(truncate(text, MAX_DIFF_CHARS, "\n\n" + _("[Diff truncated. Open in browser for the rest.]")))
				dialog.text.SetFocus()
				ui.message(_("Diff loaded"))

			run_async(
				lambda: gh("GET", f"/repos/{full}/commits/{commit['sha']}", accept="application/vnd.github.diff"),
				got,
			)

		show_modal(TextViewDialog(
			parent, _("Commit {sha}").format(sha=commit["sha"][:7]), commit_summary(commit),
			buttons=[
				(_("Show Full &Diff"), load_diff),
				(_("Copy &SHA"), lambda d: copy_to_clipboard(commit["sha"], _("Commit SHA copied."))),
				(_("Open in &Browser"), lambda d: open_url(commit.get("html_url"))),
			],
			label=_("Commit &details:"),
		))

	run_async(lambda: gh("GET", f"/repos/{full}/commits/{sha}"), done)


def show_commits(parent, full, ref="", path=""):
	def fetch(page, selections):
		params = {"per_page": items_per_page(), "page": page}
		if ref:
			params["sha"] = ref
		if path:
			params["path"] = path
		return gh("GET", f"/repos/{full}/commits", params=params)

	actions = [
		(_("&View Commit"), lambda d, c: show_commit(d, full, c["sha"])),
		(_("Copy &SHA"), lambda d, c: copy_to_clipboard(c["sha"], _("Commit SHA copied."))),
		(_("Open in &Browser"), lambda d, c: open_url(c.get("html_url"))),
	]
	branch = ref or _("default branch")
	if path:
		title = _("Commits for {path} on {branch}").format(path=path, branch=branch)
	else:
		title = _("Commits on {branch}").format(branch=branch)
	show_modal(PagedListDialog(parent, title, fetch, commit_label, actions, empty=_("No commits found.")))


# ----------------------------------------------------------------------
# releases.py: Releases: read notes, download files, create and delete releases.
# ----------------------------------------------------------------------

def release_label(r):
	name = r.get("name") or r.get("tag_name")
	flags = []
	if r.get("prerelease"):
		flags.append(_("pre-release"))
	if r.get("draft"):
		flags.append(_("draft"))
	text = _("{name}, tag {tag}, {date}, {count} files").format(
		name=name, tag=r.get("tag_name"), date=format_date(r.get("published_at") or r.get("created_at")),
		count=len(r.get("assets") or []),
	)
	return text + (", " + ", ".join(flags) if flags else "")


def release_text(r):
	lines = [
		f"{r.get('name') or r.get('tag_name')}",
		_("Tag: {tag}").format(tag=r.get("tag_name")),
		_("Published: {date} by {author}").format(
			date=r.get("published_at") or _("not published"), author=(r.get("author") or {}).get("login", _("unknown"))
		),
		"", r.get("body") or _("No release notes."), "", _("Files:"),
	]
	assets = r.get("assets") or []
	if not assets:
		lines.append(_("No downloadable files attached."))
	for a in assets:
		lines.append(_("{name}, {size}, downloaded {count} times").format(
			name=a["name"], size=format_size(a.get("size")), count=a.get("download_count", 0)
		))
	lines += ["", _("Source code is also available as ZIP and tar.gz from the web page.")]
	return "\n".join(lines)


def download_asset(parent, full, release):
	assets = release.get("assets") or []
	if not assets:
		ui.message(_("This release has no downloadable files."))
		return
	names = [f"{a['name']} ({format_size(a.get('size'))})" for a in assets]
	index = ask_choice(parent, _("Download Release File"), _("Choose a file to download:"), names)
	if index is not None:
		asset = assets[index]
		download_file(
			f"/repos/{full}/releases/assets/{asset['id']}", asset["name"], accept="application/octet-stream"
		)


def create_release(parent, full, branch, on_done=None):
	values = ask_form(
		parent, _("New Release"),
		[
			(_("&Tag (for example v1.0.0):"), "tag", False, "", True),
			(_("Release &title (optional):"), "name", False, ""),
			(_("&Target branch:"), "target", False, branch or ""),
			(_("Release &notes:"), "notes", True, ""),
		],
		checks=[(_("This is a &pre-release"), "pre", False), (_("Save as a &draft"), "draft", False)],
		ok_label=_("&Create Release"),
	)
	if not values:
		return
	body = {
		"tag_name": values["tag"].strip(), "name": values["name"].strip() or values["tag"].strip(),
		"body": values["notes"], "draft": values["draft"], "prerelease": values["pre"],
	}
	if values["target"].strip():
		body["target_commitish"] = values["target"].strip()
	ui.message(_("Creating release..."))

	def done(res):
		if res.ok:
			ui.message(_("Release created."))
			if on_done:
				on_done()
		else:
			ui.message(_("Could not create the release.") + " " + res.message)

	run_async(lambda: gh("POST", f"/repos/{full}/releases", body=body), done)


def show_releases(parent, full, can_write=False, branch=""):
	def fetch(page, selections):
		return gh("GET", f"/repos/{full}/releases", params={"per_page": items_per_page(), "page": page})

	def view(dlg, r):
		show_modal(TextViewDialog(
			dlg, r.get("name") or r.get("tag_name"), release_text(r),
			buttons=[
				(_("&Download File"), lambda d: download_asset(d, full, r)),
				(_("Open in &Browser"), lambda d: open_url(r.get("html_url"))),
			],
			label=_("Release &details:"),
		))

	def new(dlg, _item):
		create_release(dlg, full, branch, dlg.reload)

	def delete(dlg, r):
		if not confirm_typed(
			dlg, _("Delete Release"),
			_("This deletes the release {name}. The tag stays in the repository.").format(
				name=r.get("name") or r.get("tag_name")
			),
			"DELETE",
		):
			ui.message(_("Deletion cancelled."))
			return

		def done(res):
			if res.ok:
				ui.message(_("Release deleted."))
				dlg.reload()
			else:
				ui.message(_("Could not delete the release.") + " " + res.message)

		run_async(lambda: gh("DELETE", f"/repos/{full}/releases/{r['id']}"), done)

	actions = [
		(_("&View Release"), view),
		(_("&Download File"), lambda d, r: download_asset(d, full, r)),
		(_("Open in &Browser"), lambda d, r: open_url(r.get("html_url"))),
	]
	if can_write:
		actions += [(_("&New Release"), new, False), (_("De&lete Release"), delete)]
	show_modal(PagedListDialog(
		parent, _("Releases: {repo}").format(repo=full), fetch, release_label, actions,
		empty=_("This repository has no releases."),
	))
