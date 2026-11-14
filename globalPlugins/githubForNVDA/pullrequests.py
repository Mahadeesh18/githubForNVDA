# GitHub for NVDA
# Copyright (C) 2025-2026 Mahadeesh and contributors
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for more details.

"""Pull request tools: changed files and their diffs, comments, reviewers, draft state, updating the branch
and resolving merge conflicts."""

import addonHandler
import ui
import wx

from .core import (
	AccessibleDialog,
	ask_choice,
	ask_form,
	ask_multi_choice,
	checks_summary,
	confirm,
	copy_to_clipboard,
	current_login,
	decode_text,
	Decoded,
	encode_text,
	format_date,
	gh,
	gh_paged,
	join_chunks,
	merge_text,
	numbered_patch,
	open_url,
	PAD,
	PagedListDialog,
	Problem,
	prompt_text,
	Result,
	show_modal,
	TextViewDialog,
	three_way_trees,
)
from .repos import create_blob, FileContext, FileViewDialog, q

addonHandler.initTranslation()


def _api(full):
	return f"/repos/{full}"


# ----------------------------------------------------------------------
# Checks
# ----------------------------------------------------------------------

def load_checks(full, sha):
	"""Blocking. Lines describing the checks of a commit, or [] when there are none or they cannot be read."""
	if not sha:
		return []
	runs = gh_paged(f"{_api(full)}/commits/{sha}/check-runs", unwrap="check_runs", max_items=300)
	status = gh("GET", f"{_api(full)}/commits/{sha}/status")
	statuses = (status.data or {}).get("statuses", []) if status.ok and isinstance(status.data, dict) else []
	return checks_summary(runs.data if runs.ok else [], statuses)


# ----------------------------------------------------------------------
# Files changed
# ----------------------------------------------------------------------

def file_label(f):
	# Translators: one changed file of a pull request, such as "modified: src/app.py, 3 added, 1 removed".
	text = _("{status}: {name}, {additions} added, {deletions} removed").format(
		status=file_status_label(f.get("status")), name=f.get("filename", ""),
		additions=f.get("additions", 0), deletions=f.get("deletions", 0),
	)
	if f.get("previous_filename"):
		text += ", " + _("was {name}").format(name=f["previous_filename"])
	return text


def file_status_label(status):
	return {
		"added": _("added"), "removed": _("removed"), "modified": _("modified"), "renamed": _("renamed"),
		"copied": _("copied"), "changed": _("changed"), "unchanged": _("unchanged"),
	}.get(status, status or "")


def show_files(parent, full, number, pr):
	"""Every file a pull request changes. View Changes reads the diff of one file with line numbers."""
	head = pr.get("head") or {}
	head_repo = (head.get("repo") or {}).get("full_name") or full
	url = f"{_api(full)}/pulls/{number}/files"

	def fetch(page, selections):
		res = gh_paged(url, max_items=3000)
		if not res.ok:
			return res
		return Result(200, res.data, {"X-Truncated": "1"} if (res.headers or {}).get("X-Truncated") else {})

	def view_changes(dlg, f):
		patch = f.get("patch")
		if not patch:
			ui.message(_("GitHub does not show the changes of this file here: it is binary or too large. Use Open in Browser."))
			return
		show_modal(TextViewDialog(
			dlg, _("Changes: {name}").format(name=f.get("filename", "")), numbered_patch(patch),
			buttons=[(_("Copy &Raw Diff"), lambda d: copy_to_clipboard(patch, _("Diff copied.")))],
			label=_("&Changes (line numbers, added and removed lines):"), wrap=False,
		))

	def view_file(dlg, f):
		if f.get("status") == "removed":
			ui.message(_("This file is deleted by the pull request."))
			return
		entry = {"name": f["filename"].rsplit("/", 1)[-1], "path": f["filename"], "sha": f.get("sha")}
		show_modal(FileViewDialog(dlg, FileContext(head_repo, head.get("ref", ""), False), entry))

	def comment_on_line(dlg, f):
		comment_line(dlg, full, number, pr, f["filename"])

	actions = [
		(_("&View Changes"), view_changes),
		(_("View &File"), view_file),
		(_("Comment on a &Line..."), comment_on_line),
		(_("Copy File &Name"), lambda d, f: copy_to_clipboard(f.get("filename", ""), _("File name copied."))),
		(_("Open in &Browser"), lambda d, f: open_url(f"{pr.get('html_url', '')}/files")),
	]
	show_modal(PagedListDialog(
		parent, _("Files changed in #{number}").format(number=number), fetch, file_label, actions,
		empty=_("This pull request changes no files."),
	))


def comment_line(parent, full, number, pr, path):
	"""Comment on one line of a changed file (a review comment)."""
	sides = [_("The new version (your changes)"), _("The old version (removed lines)")]
	values = ask_form(
		parent, _("Comment on a Line of {path}").format(path=path),
		[
			(_("&Line number (as View Changes shows it):"), "line", False, "", True),
			(_("&Comment:"), "body", True, "", True),
		],
		choices=[(_("The line &is in:"), "side", sides, 0)],
		ok_label=_("&Post Comment"),
	)
	if not values:
		return
	try:
		line = int(values["line"].strip())
	except ValueError:
		ui.message(_("Enter a whole number."))
		return
	body = {
		"body": values["body"], "commit_id": (pr.get("head") or {}).get("sha"), "path": path, "line": line,
		"side": "LEFT" if values["side"] == 1 else "RIGHT",
	}
	ui.message(_("Posting comment..."))

	def done(res):
		if res.ok:
			ui.message(_("Comment posted."))
		elif res.code == 422:
			ui.message(_("GitHub did not accept that line. You can only comment on lines that View Changes lists.") + " " + res.message)
		else:
			ui.message(_("Could not post the comment.") + " " + res.message)

	parent.run(lambda: gh("POST", f"{_api(full)}/pulls/{number}/comments", body=body), done)


# ----------------------------------------------------------------------
# Comments: read, reply, edit and delete
# ----------------------------------------------------------------------

def comment_label(c):
	first = next((line for line in (c.get("body") or "").splitlines() if line.strip()), "")
	author = (c.get("user") or {}).get("login", _("unknown"))
	if c.get("_kind") == "review":
		where = c.get("path", "")
		line = c.get("line") or c.get("original_line")
		if line:
			where = _("{path}, line {line}").format(path=where, line=line)
		kind = _("reply on {where}").format(where=where) if c.get("in_reply_to_id") else _("on {where}").format(where=where)
		# Translators: a comment on the code in the comments list, such as "anna, 2026-11-14, on app.py, line 3: Typo".
		return _("{author}, {date}, {kind}: {text}").format(author=author, date=format_date(c.get("created_at")), kind=kind, text=first)
	return _("{author}, {date}: {text}").format(author=author, date=format_date(c.get("created_at")), text=first)


def show_comments(parent, full, number, is_pr, on_change=None):
	"""Every comment of an issue or pull request. Your own comments can be edited and deleted."""
	who = {"me": ""}

	def fetch(page, selections):
		who["me"] = current_login().lower()
		res = gh_paged(f"{_api(full)}/issues/{number}/comments", max_items=1000)
		if not res.ok:
			return res
		items = [dict(c, _kind="issue") for c in res.data]
		if is_pr:
			inline = gh_paged(f"{_api(full)}/pulls/{number}/comments", max_items=1000)
			if inline.ok:
				items += [dict(c, _kind="review") for c in inline.data]
		items.sort(key=lambda c: c.get("created_at") or "")
		return Result(200, items, {})

	def url(c):
		kind = "pulls" if c["_kind"] == "review" else "issues"
		return f"{_api(full)}/{kind}/comments/{c['id']}"

	def mine(c):
		if (c.get("user") or {}).get("login", "").lower() != who["me"]:
			ui.message(_("You can only change your own comments."))
			return False
		return True

	def changed(dlg, message):
		def done(res):
			if res.ok:
				ui.message(message)
				dlg.reload()
				if on_change:
					on_change()
			else:
				ui.message(_("That did not work.") + " " + res.message)
		return done

	def read(dlg, c):
		title = _("Comment by {author}").format(author=(c.get("user") or {}).get("login", ""))
		show_modal(TextViewDialog(dlg, title, c.get("body") or "", label=_("&Comment:"), buttons=[
			(_("Copy &Text"), lambda d: copy_to_clipboard(c.get("body") or "", _("Comment copied."))),
		]))

	def reply(dlg, c):
		if c["_kind"] == "review":
			text = prompt_text(dlg, _("Reply"), _("Your &reply (it joins the discussion on that line):"), multiline=True)
			if text and text.strip():
				thread = c.get("in_reply_to_id") or c["id"]
				dlg.run(lambda: gh("POST", f"{_api(full)}/pulls/{number}/comments/{thread}/replies", body={"body": text}),
					changed(dlg, _("Reply posted.")))
			return
		quoted = "\n".join("> " + line for line in (c.get("body") or "").splitlines()) + "\n\n"
		text = prompt_text(dlg, _("Reply"), _("Your &reply (the comment is quoted at the top):"), quoted, multiline=True)
		if text and text.strip() and text != quoted:
			dlg.run(lambda: gh("POST", f"{_api(full)}/issues/{number}/comments", body={"body": text}),
				changed(dlg, _("Reply posted.")))

	def edit(dlg, c):
		if not mine(c):
			return
		text = prompt_text(dlg, _("Edit Comment"), _("&Comment:"), c.get("body") or "", multiline=True)
		if text is not None and text.strip() and text != c.get("body"):
			dlg.run(lambda: gh("PATCH", url(c), body={"body": text}), changed(dlg, _("Comment saved.")))

	def delete(dlg, c):
		if not mine(c):
			return
		if confirm(dlg, _("Delete this comment?"), _("Delete Comment"), warning=True, default_no=True):
			dlg.forget(c)
			dlg.run(lambda: gh("DELETE", url(c)), changed(dlg, _("Comment deleted.")))

	actions = [
		(_("&Read"), read),
		(_("Re&ply..."), reply),
		(_("&Edit..."), edit),
		(_("&Delete"), delete),
		(_("Copy Lin&k"), lambda d, c: copy_to_clipboard(c.get("html_url", ""), _("Link copied."))),
	]
	show_modal(PagedListDialog(
		parent, _("Comments on #{number}").format(number=number), fetch, comment_label, actions,
		empty=_("There are no comments yet."),
	))


# ----------------------------------------------------------------------
# Reviewers, draft state and updating the branch
# ----------------------------------------------------------------------

def request_reviewers(dlg, full, number, pr, after):
	author = (pr.get("user") or {}).get("login", "").lower()
	current = [u.get("login") for u in pr.get("requested_reviewers") or []]
	ui.message(_("Loading people..."))

	def loaded(res):
		if not res.ok:
			ui.message(_("Could not load the people.") + " " + res.message)
			return
		names = [a["login"] for a in res.data if a.get("login", "").lower() != author]
		if not names:
			ui.message(_("Nobody else can review pull requests in this repository."))
			return
		picked = ask_multi_choice(
			dlg, _("Request Reviewers"), _("Tick the people who should review this pull request:"), names,
			[i for i, n in enumerate(names) if n in current],
		)
		if picked is None:
			return
		chosen = [names[i] for i in picked]
		add = [n for n in chosen if n not in current]
		remove = [n for n in current if n not in chosen]
		if not add and not remove:
			ui.message(_("No changes to save."))
			return

		def work():
			url = f"{_api(full)}/pulls/{number}/requested_reviewers"
			if remove:
				res = gh("DELETE", url, body={"reviewers": remove})
				if not res.ok:
					return res
			return gh("POST", url, body={"reviewers": add}) if add else Result(200, None, {})

		def saved(result):
			if result.ok:
				ui.message(_("Reviewers updated."))
				after()
			else:
				ui.message(_("Could not update the reviewers.") + " " + result.message)

		dlg.run(work, saved)

	dlg.run(lambda: gh_paged(f"{_api(full)}/assignees", max_items=500), loaded)


def toggle_draft(dlg, pr, after):
	"""Mark a draft ready for review, or turn the pull request back into a draft (GitHub's API needs GraphQL)."""
	from .profile import graphql
	draft = bool(pr.get("draft"))
	mutation = "markPullRequestReadyForReview" if draft else "convertPullRequestToDraft"
	query = f"mutation($id: ID!) {{ {mutation}(input: {{pullRequestId: $id}}) {{ pullRequest {{ isDraft }} }} }}"

	def done(res):
		if res.ok:
			ui.message(_("Ready for review.") if draft else _("Converted to a draft."))
			after()
		else:
			ui.message(_("That did not work.") + " " + res.message)

	dlg.run(lambda: graphql(query, {"id": pr.get("node_id")}), done)


def update_branch(dlg, full, number, pr, after):
	"""Bring the latest changes of the base branch into the pull request's branch, like GitHub's Update branch button."""
	base = (pr.get("base") or {}).get("ref", "")
	head = (pr.get("head") or {}).get("ref", "")
	if not confirm(dlg, _("Merge the latest changes of {base} into {head}?").format(base=base, head=head), _("Update Branch")):
		return
	ui.message(_("Updating the branch..."))

	def done(res):
		if res.ok:
			ui.message(_("GitHub is updating the branch. Refresh in a moment to see the result."))
			after()
		elif res.code == 422 and "conflict" in str(res.data).lower():
			if confirm(dlg, _("The branches have merge conflicts, so GitHub cannot update the branch by itself. Resolve the conflicts now?"), _("Update Branch")):
				resolve_conflicts(dlg, full, pr, after)
		else:
			ui.message(_("Could not update the branch.") + " " + res.message)

	body = {"expected_head_sha": (pr.get("head") or {}).get("sha")}
	dlg.run(lambda: gh("PUT", f"{_api(full)}/pulls/{number}/update-branch", body=body), done)


# ----------------------------------------------------------------------
# Resolving merge conflicts
# ----------------------------------------------------------------------

_EMPTY = Decoded("", "utf-8", False, True)


def _tree_map(full, commit_sha):
	"""Result whose data is {path: (mode, type, sha)} for every file of a commit."""
	commit = gh("GET", f"{_api(full)}/git/commits/{commit_sha}")
	if not commit.ok:
		return commit
	tree_sha = commit.data["tree"]["sha"]
	tree = gh("GET", f"{_api(full)}/git/trees/{tree_sha}", params={"recursive": "1"}, timeout=90)
	if not tree.ok:
		return tree
	if tree.data.get("truncated"):
		return Problem(_("This repository is too large to merge here. Use the browser."))
	items = {e["path"]: (e["mode"], e["type"], e["sha"]) for e in tree.data.get("tree", []) if e.get("type") in ("blob", "commit")}
	return Result(200, (tree_sha, items), {})


def _blob_text(full, entry):
	"""Decoded text of a tree entry, _EMPTY for a missing file, None for binary data or submodules."""
	if entry is None:
		return _EMPTY
	if entry[1] != "blob":
		return None
	res = gh("GET", f"{_api(full)}/git/blobs/{entry[2]}", accept="application/vnd.github.raw+json", binary=True, timeout=60)
	if not res.ok or not isinstance(res.data, bytes):
		return None
	decoded = decode_text(res.data)
	return decoded if decoded is not None and decoded.editable else None


def analyse_merge(full, pr):
	"""Blocking. Work out what merging the base branch into the pull request's branch needs."""
	base_ref = (pr.get("base") or {}).get("ref", "")
	head_ref = (pr.get("head") or {}).get("ref", "")
	base_branch = gh("GET", f"{_api(full)}/branches/{q(base_ref, '/')}")
	head_branch = gh("GET", f"{_api(full)}/branches/{q(head_ref, '/')}")
	for res in (base_branch, head_branch):
		if not res.ok:
			return res
	base_sha, head_sha = base_branch.data["commit"]["sha"], head_branch.data["commit"]["sha"]
	compare = gh("GET", f"{_api(full)}/compare/{base_sha}...{head_sha}", params={"per_page": 1})
	if not compare.ok:
		return compare
	if compare.data.get("behind_by", 0) == 0:
		return Result(200, None, {})  # the branch already has everything from the base branch
	ancestor = compare.data["merge_base_commit"]["sha"]
	trees = [_tree_map(full, sha) for sha in (ancestor, head_sha, base_sha)]
	for res in trees:
		if not res.ok:
			return res
	(_t, old), (head_tree, ours), (_t2, theirs) = (t.data for t in trees)
	take, conflict_paths = three_way_trees(old, ours, theirs)
	files = []
	for path in conflict_paths:
		entries = (old.get(path), ours.get(path), theirs.get(path))
		texts = [_blob_text(full, e) for e in entries]
		item = {"path": path, "ours": ours.get(path), "theirs": theirs.get(path)}
		if None in texts or entries[1] is None or entries[2] is None:
			item["kind"] = "whole"  # binary, a submodule, or deleted on one side
		else:
			chunks, count = merge_text(texts[0].text, texts[1].text, texts[2].text)
			item.update(kind="text", chunks=chunks, count=count, encoding=texts[1].encoding, crlf=texts[1].crlf)
		files.append(item)
	return Result(200, {
		"base_ref": base_ref, "head_ref": head_ref, "base_sha": base_sha, "head_sha": head_sha,
		"head_tree": head_tree, "take": take, "files": files,
		# What we have now at every path that may change: a deletion must name the right mode and type.
		"ours": {path: ours.get(path) for path in list(take) + conflict_paths},
	}, {})


class ConflictDialog(AccessibleDialog):
	"""Walk through the conflicts of one file and choose what to keep in each."""

	def __init__(self, parent, path, chunks, head_ref, base_ref):
		super().__init__(parent, _("Resolve Conflicts: {path}").format(path=path), (820, 700))
		self.chunks = chunks
		self.conflicts = [c for c in chunks if c[0] == "conflict"]
		self.lines = self._line_numbers()
		self.results = {i: "".join(c[2]) for i, c in enumerate(self.conflicts)}  # yours by default
		self.seen = set()
		self.index = 0
		self.head_ref, self.base_ref = head_ref, base_ref
		sizer = wx.BoxSizer(wx.VERTICAL)
		self.where = wx.StaticText(self, label="")
		sizer.Add(self.where, 0, wx.ALL, PAD)
		self.mine = self._box(sizer, _("&Your branch ({name}):").format(name=head_ref))
		self.theirs = self._box(sizer, _("&Base branch ({name}):").format(name=base_ref))
		self.original = self._box(sizer, _("Before either &change:"))
		self.choice = wx.RadioBox(self, label=_("Keep"), choices=[
			_("Your branch"), _("The base branch"), _("Both, yours first"), _("Both, the base branch first"), _("Neither"),
		], majorDimension=1, style=wx.RA_SPECIFY_COLS)
		self.choice.Bind(wx.EVT_RADIOBOX, self._on_choice)
		sizer.Add(self.choice, 0, wx.ALL, PAD)
		sizer.Add(wx.StaticText(self, label=_("&Result (you can edit it):")), 0, wx.LEFT, PAD)
		self.result = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_DONTWRAP | wx.HSCROLL, size=(-1, 110))
		sizer.Add(self.result, 1, wx.EXPAND | wx.ALL, PAD)
		row = wx.WrapSizer(wx.HORIZONTAL)
		self.add_button(row, _("&Previous Conflict"), lambda e: self._move(-1))
		self.add_button(row, _("&Next Conflict"), lambda e: self._move(1))
		save = wx.Button(self, wx.ID_OK, _("&Save File"))
		save.Bind(wx.EVT_BUTTON, self._on_save)
		row.Add(save, 0, wx.ALL, 3)
		row.Add(wx.Button(self, wx.ID_CANCEL, _("Cancel")), 0, wx.ALL, 3)
		sizer.Add(row, 0, wx.ALIGN_CENTER)
		self.layout(sizer)
		self._show()

	def _box(self, sizer, label):
		sizer.Add(wx.StaticText(self, label=label), 0, wx.LEFT, PAD)
		box = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP | wx.HSCROLL, size=(-1, 80))
		sizer.Add(box, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, PAD)
		return box

	def _line_numbers(self):
		"""The line in your branch's version of the file where each conflict starts."""
		numbers, line = [], 1
		for chunk in self.chunks:
			if chunk[0] == "ok":
				line += len(chunk[1])
			else:
				numbers.append(line)
				line += len(chunk[2])
		return numbers

	def _show(self):
		_k, old, mine, theirs = self.conflicts[self.index]
		self.seen.add(self.index)
		self.where.SetLabel(_("Conflict {number} of {total}, at line {line} of your branch's version.").format(
			number=self.index + 1, total=len(self.conflicts), line=self.lines[self.index]))
		self.mine.SetValue("".join(mine) or _("(nothing: these lines are removed)"))
		self.theirs.SetValue("".join(theirs) or _("(nothing: these lines are removed)"))
		self.original.SetValue("".join(old) or _("(nothing: both sides added new lines here)"))
		self.result.SetValue(self.results[self.index])
		self.choice.SetSelection(self._guess(mine, theirs))
		self.mine.SetFocus()
		ui.message(self.where.GetLabel())

	def _guess(self, mine, theirs):
		text = self.results[self.index]
		options = self._options(mine, theirs)
		return options.index(text) if text in options else 0

	@staticmethod
	def _options(mine, theirs):
		a, b = "".join(mine), "".join(theirs)
		return [a, b, a + b, b + a, ""]

	def _on_choice(self, event):
		_k, _old, mine, theirs = self.conflicts[self.index]
		self.result.SetValue(self._options(mine, theirs)[self.choice.GetSelection()])

	def _keep_result(self):
		text = self.result.GetValue().replace("\r\n", "\n")
		_k, _old, mine, theirs = self.conflicts[self.index]
		if text and "".join(mine + theirs).endswith("\n") and not text.endswith("\n"):
			text += "\n"  # keep the next line on its own line
		self.results[self.index] = text

	def _move(self, step):
		self._keep_result()
		target = self.index + step
		if not 0 <= target < len(self.conflicts):
			ui.message(_("No more conflicts in this file.") if step > 0 else _("This is the first conflict."))
			return
		self.index = target
		self._show()

	def _on_save(self, event):
		self._keep_result()
		unseen = len(self.conflicts) - len(self.seen)
		if unseen and not confirm(self, _(
			"You have not looked at {count} conflicts. They keep your branch's version. Save anyway?"
		).format(count=unseen), _("Resolve Conflicts")):
			return
		self.EndModal(wx.ID_OK)

	def merged_text(self):
		return join_chunks(self.chunks, self.results)


def _choose_whole(parent, item, head_ref, base_ref):
	"""For a file that cannot be merged line by line: keep one side. Returns the chosen entry, False if cancelled."""

	def describe(entry, branch):
		if entry is None:
			return _("Delete the file, as on {branch}").format(branch=branch)
		return _("Keep the file as it is on {branch}").format(branch=branch)

	index = ask_choice(
		parent, _("Resolve Conflict"),
		_("{path} was changed on both branches and cannot be merged line by line (it is binary, a submodule, or deleted on one side). What do you want to keep?").format(path=item["path"]),
		[describe(item["ours"], head_ref), describe(item["theirs"], base_ref)],
	)
	if index is None:
		return False
	return item["ours"] if index == 0 else item["theirs"]


def resolve_conflicts(parent, full, pr, after=None):
	"""Merge the base branch into the pull request's branch, asking you about every conflict."""
	head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
	if head_repo and head_repo.lower() != full.lower():
		if confirm(parent, _(
			"This pull request comes from another repository ({repo}). Conflicts can only be resolved here when both "
			"branches are in the same repository. Open the pull request in your browser?"
		).format(repo=head_repo), _("Resolve Conflicts")):
			open_url(pr.get("html_url"))
		return
	ui.message(_("Comparing the branches. This can take a moment..."))

	def analysed(res):
		if not res.ok:
			ui.message(_("Could not compare the branches.") + " " + res.message)
			return
		plan = res.data
		if plan is None:
			ui.message(_("The branch already has every change of its base branch. There is nothing to merge."))
			return
		entries = {path: entry for path, entry in plan["take"].items()}
		resolved = {}  # path -> merged text, with the item it belongs to
		for item in plan["files"]:
			if item["kind"] == "text" and item["count"] == 0:
				resolved[item["path"]] = (join_chunks(item["chunks"]), item)
			elif item["kind"] == "text":
				dialog = ConflictDialog(parent, item["path"], item["chunks"], plan["head_ref"], plan["base_ref"])
				if show_modal(dialog) != wx.ID_OK:
					ui.message(_("Cancelled. Nothing was changed."))
					return
				resolved[item["path"]] = (dialog.merged_text(), item)
			else:
				choice = _choose_whole(parent, item, plan["head_ref"], plan["base_ref"])
				if choice is False:
					ui.message(_("Cancelled. Nothing was changed."))
					return
				if choice != item["ours"]:
					entries[item["path"]] = choice
		if not confirm(parent, _(
			"Commit the merge of {base} into {head}? {changed} files change; {conflicts} of them had conflicts."
		).format(base=plan["base_ref"], head=plan["head_ref"], changed=len(entries) + len(resolved),
			conflicts=len(plan["files"])), _("Resolve Conflicts")):
			ui.message(_("Cancelled. Nothing was changed."))
			return
		ui.message(_("Saving the merge..."))
		parent.run(lambda: commit_merge(full, plan, entries, resolved), committed)

	def committed(res):
		if res.ok:
			ui.message(_("Conflicts resolved. The merge was committed to the pull request's branch."))
			if after:
				after()
		elif res.code == 422:
			ui.message(_("The branch changed while you were resolving. Nothing was saved; try again.") + " " + res.message)
		else:
			ui.message(_("Could not save the merge.") + " " + res.message)

	parent.run(lambda: analyse_merge(full, pr), analysed)


def commit_merge(full, plan, entries, resolved):
	"""Blocking. Write a merge commit with two parents and move the pull request's branch to it."""
	tree = []
	for path, entry in entries.items():
		if entry is None:
			mode, kind = (plan["ours"].get(path) or ("100644", "blob", None))[:2]
			tree.append({"path": path, "mode": mode, "type": kind, "sha": None})
		else:
			tree.append({"path": path, "mode": entry[0], "type": entry[1], "sha": entry[2]})
	for path, (text, item) in resolved.items():
		blob = create_blob(full, encode_text(text, item["encoding"], item["crlf"]))
		if not blob.ok:
			return blob
		mode = (item["ours"] or item["theirs"])[0]
		tree.append({"path": path, "mode": mode, "type": "blob", "sha": blob.data})
	made = gh("POST", f"{_api(full)}/git/trees", body={"base_tree": plan["head_tree"], "tree": tree}, timeout=120)
	if not made.ok:
		return made
	message = _("Merge branch '{base}' into {head}").format(base=plan["base_ref"], head=plan["head_ref"])
	commit = gh("POST", f"{_api(full)}/git/commits", body={
		"message": message, "tree": made.data["sha"], "parents": [plan["head_sha"], plan["base_sha"]],
	})
	if not commit.ok:
		return commit
	# Not forced: if somebody pushed to the branch meanwhile, GitHub refuses and nothing is lost.
	return gh("PATCH", f"{_api(full)}/git/refs/heads/{q(plan['head_ref'], '/')}", body={"sha": commit.data["sha"], "force": False})
