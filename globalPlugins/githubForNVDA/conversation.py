# GitHub for NVDA
# Copyright (C) 2025-2026 Mahadeesh and contributors
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for more details.

"""Issues, pull requests and GitHub Actions: conversation window, lists, My Work and workflow runs."""

import addonHandler
import io
import json
import re
import ui
import wx
import zipfile
from logHandler import log

from .core import (
	AccessibleDialog,
	apply_access_keys,
	ask_choice,
	ask_form,
	ask_multi_choice,
	confirm,
	copy_to_clipboard,
	current_login,
	escape_label,
	FilteredPager,
	format_date,
	format_datetime,
	get_pref,
	gh,
	gh_paged,
	items_per_page,
	make_label,
	open_url,
	PAD,
	PagedListDialog,
	prompt_text,
	Result,
	run_async,
	show_modal,
	split_names,
	tail,
	TextViewDialog,
)
from .repos import (
	list_branches,
	q,
)

addonHandler.initTranslation()


# ----------------------------------------------------------------------
# conversation.py: One issue or pull request with its comments, reviews and actions.
# ----------------------------------------------------------------------

_MERGE_STATES = {
	"clean": lambda: _("Ready to merge."),
	"dirty": lambda: _("Has merge conflicts that must be resolved."),
	"blocked": lambda: _("Merging is blocked by required reviews or checks."),
	"behind": lambda: _("The branch is behind its base branch."),
	"unstable": lambda: _("Some checks are failing, but merging is possible."),
	"draft": lambda: _("This is a draft and cannot be merged yet."),
	"has_hooks": lambda: _("Ready to merge."),
}


def user_login(item):
	return (item.get("user") or {}).get("login", _("unknown"))


def conversation_state(issue, pr):
	if pr and pr.get("merged"):
		return _("merged")
	if issue.get("state") == "open":
		return _("open, draft") if pr and pr.get("draft") else _("open")
	return _("closed")


def review_summary(reviews):
	"""Latest approving or blocking review of each reviewer -> (approved, changes_requested)."""
	latest = {}
	for review in reviews or []:
		who = (review.get("user") or {}).get("login")
		state = review.get("state")
		if not who or state in ("PENDING", "COMMENTED"):
			continue
		if state == "DISMISSED":
			latest.pop(who, None)
		else:
			latest[who] = state
	approved = sorted(w for w, s in latest.items() if s == "APPROVED")
	changes = sorted(w for w, s in latest.items() if s == "CHANGES_REQUESTED")
	return approved, changes


def compose_conversation(issue, pr=None, comments=(), files=None, reviews=(), inline=()):
	"""Everything about an issue or pull request as plain, readable text."""
	pr = pr or {}
	lines = [
		f"#{issue['number']} {issue['title']}",
		_("State: {state}. Opened by {author} on {date}.").format(
			state=conversation_state(issue, pr), author=user_login(issue), date=format_date(issue.get("created_at"))
		),
	]
	labels = ", ".join(label["name"] for label in issue.get("labels") or [] if isinstance(label, dict))
	if labels:
		lines.append(_("Labels: {labels}").format(labels=labels))
	assignees = ", ".join(a["login"] for a in issue.get("assignees") or [])
	if assignees:
		lines.append(_("Assigned to: {names}").format(names=assignees))
	if issue.get("milestone"):
		lines.append(_("Milestone: {title}").format(title=issue["milestone"].get("title", "")))
	if issue.get("locked"):
		lines.append(_("This conversation is locked."))
	if pr:
		lines.append(_("Branches: {head} into {base}.").format(
			head=(pr.get("head") or {}).get("label", "?"), base=(pr.get("base") or {}).get("label", "?")
		))
		lines.append(_("Changes: {additions} additions, {deletions} deletions in {files} files, {commits} commits.").format(
			additions=pr.get("additions", 0), deletions=pr.get("deletions", 0),
			files=pr.get("changed_files", 0), commits=pr.get("commits", 0),
		))
		if pr.get("merged"):
			lines.append(_("This pull request has been merged."))
		elif issue.get("state") == "open":
			state = pr.get("mergeable_state")
			if pr.get("mergeable") is None and state in (None, "unknown"):
				lines.append(_("Merge status is still being calculated. Refresh in a moment."))
			elif state in _MERGE_STATES:
				lines.append(_MERGE_STATES[state]())
			elif pr.get("mergeable") is False:
				lines.append(_("Has merge conflicts that must be resolved."))
		approved, changes = review_summary(reviews)
		if approved:
			lines.append(_("Approved by: {names}").format(names=", ".join(approved)))
		if changes:
			lines.append(_("Changes requested by: {names}").format(names=", ".join(changes)))
	lines += ["", _("Description:"), issue.get("body") or _("No description provided.")]
	for number, comment in enumerate(comments, 1):
		lines += [
			"", _("Comment {number} by {author} on {date}:").format(
				number=number, author=user_login(comment), date=format_date(comment.get("created_at"))
			),
			comment.get("body") or "",
		]
	written = [r for r in reviews or [] if r.get("state") != "PENDING" and (r.get("body") or r.get("state") != "COMMENTED")]
	if written:
		lines += ["", _("Reviews:")]
		for review in written:
			lines += [
				_("Review by {author} on {date}: {state}").format(
					author=user_login(review), date=format_date(review.get("submitted_at")),
					state=(review.get("state") or "").replace("_", " ").lower(),
				),
				review.get("body") or "",
			]
	if inline:
		lines += ["", _("Comments on the code:")]
		for comment in inline:
			where = comment.get("path", "")
			line = comment.get("line") or comment.get("original_line")
			if line:
				where += _(" line {line}").format(line=line)
			lines += [
				_("{where}, by {author} on {date}:").format(
					where=where, author=user_login(comment), date=format_date(comment.get("created_at"))
				),
				comment.get("body") or "",
			]
	if files:
		lines += ["", _("Files changed:")]
		for f in files:
			lines.append(f"{f['status']}: {f['filename']} (+{f['additions']} -{f['deletions']})")
		if len(files) >= 100:
			lines.append(_("Only the first 100 files are listed."))
	return "\n".join(lines)


class ConversationDialog(AccessibleDialog):
	"""Shows one issue or pull request with everything said about it."""

	def __init__(self, parent, full, number, is_pr=False, thread_id=None):
		title = _("Pull request #{number}") if is_pr else _("Issue #{number}")
		super().__init__(parent, title.format(number=number), (800, 620))
		self.full, self.number, self.is_pr, self.thread_id = full, number, is_pr, thread_id
		self.issue, self.pr = {}, {}
		self._marked_read = False
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(self, label=_("&Details and conversation:")), 0, wx.ALL, PAD)
		self.text = wx.TextCtrl(self, value=_("Loading..."), style=wx.TE_MULTILINE | wx.TE_READONLY)
		sizer.Add(self.text, 1, wx.EXPAND | wx.ALL, PAD)
		row = wx.WrapSizer(wx.HORIZONTAL)
		self.btn_comment = self.add_button(row, _("Add &Comment"), self.on_comment)
		self.btn_state = self.add_button(row, _("Close or Re&open"), self.on_toggle_state)
		self.btn_review = self.btn_merge = None
		if is_pr:
			self.btn_review = self.add_button(row, _("Submit &Review"), self.on_review)
			self.btn_merge = self.add_button(row, _("&Merge Pull Request"), self.on_merge)
		self.btn_more = self.add_button(row, _("M&ore Actions..."), self.on_more)
		row.Add(self.close_button(), 0, wx.ALL, 3)
		sizer.Add(row, 0, wx.ALIGN_CENTER)
		self.layout(sizer, focus=self.text)
		self._set_actions_enabled(False)
		self.load()

	def _set_actions_enabled(self, enabled):
		for button in (self.btn_comment, self.btn_state, self.btn_review, self.btn_merge):
			if button:
				button.Enable(enabled)

	def _url(self):
		kind = "pull" if self.is_pr else "issues"
		return f"https://github.com/{self.full}/{kind}/{self.number}"

	@property
	def _api(self):
		return f"/repos/{self.full}"

	# --- loading -----------------------------------------------------
	def load(self):
		ui.message(_("Loading..."))

		def work():
			issue = gh("GET", f"{self._api}/issues/{self.number}")
			if not issue.ok:
				return issue
			payload = {
				"issue": issue.data,
				"comments": gh_paged(f"{self._api}/issues/{self.number}/comments", max_items=200),
				"pr": None, "files": None, "reviews": None, "inline": None,
			}
			if self.is_pr:
				payload["pr"] = gh("GET", f"{self._api}/pulls/{self.number}")
				payload["files"] = gh("GET", f"{self._api}/pulls/{self.number}/files", params={"per_page": 100})
				payload["reviews"] = gh_paged(f"{self._api}/pulls/{self.number}/reviews", max_items=100)
				payload["inline"] = gh_paged(f"{self._api}/pulls/{self.number}/comments", max_items=200)
			return Result(200, payload, {})

		def data_of(result, default):
			return result.data if result is not None and result.ok else default

		def done(res):
			if not res.ok:
				self.text.SetValue(_("Could not load.") + " " + res.message)
				ui.message(res.message)
				return
			payload = res.data
			self.issue = payload["issue"]
			self.pr = data_of(payload["pr"], {})
			self.text.SetValue(compose_conversation(
				self.issue, self.pr, data_of(payload["comments"], []), data_of(payload["files"], None),
				data_of(payload["reviews"], []), data_of(payload["inline"], []),
			))
			self.text.SetInsertionPoint(0)
			self._update_buttons()
			self.text.SetFocus()
			ui.message(_("Loaded"))
			self._mark_read()

		self.run(work, done)

	def _update_buttons(self):
		merged = bool(self.pr.get("merged"))
		closed = self.issue.get("state") == "closed"
		self._set_actions_enabled(True)
		self.btn_state.Enable(not merged)
		if self.is_pr:
			self.btn_state.SetLabel(_("Re&open Pull Request") if closed else _("Close Pull &Request"))
		else:
			self.btn_state.SetLabel(_("Re&open Issue") if closed else _("Close &Issue"))
		if self.btn_merge:
			self.btn_merge.Enable(bool(self.pr) and not closed and not merged)
		if self.btn_review:
			self.btn_review.Enable(bool(self.pr) and not merged)
		apply_access_keys(self)

	def _mark_read(self):
		if self.thread_id and not self._marked_read and get_pref("markReadOnView"):
			self._marked_read = True
			thread = self.thread_id
			run_async(lambda: gh("PATCH", f"/notifications/threads/{thread}"))

	# --- actions -----------------------------------------------------
	def on_comment(self, event):
		body = prompt_text(self, _("Add Comment"), _("Your &comment:"), multiline=True)
		if not body or not body.strip():
			return
		ui.message(_("Posting comment..."))

		def done(res):
			if res.ok:
				ui.message(_("Comment posted."))
				self.load()
			else:
				ui.message(_("Could not post the comment.") + " " + res.message)

		self.run(lambda: gh("POST", f"{self._api}/issues/{self.number}/comments", body={"body": body}), done)

	def on_toggle_state(self, event):
		closing = self.issue.get("state") == "open"
		payload = {"state": "closed" if closing else "open"}
		if closing and not self.is_pr:
			reason = ask_choice(
				self, _("Close Issue"), _("Why is this issue being closed?"),
				[_("Completed"), _("Not planned")],
			)
			if reason is None:
				return
			payload["state_reason"] = ("completed", "not_planned")[reason]
		else:
			question = _("Do you want to close #{number}?") if closing else _("Do you want to reopen #{number}?")
			if not confirm(self, question.format(number=self.number)):
				return
			if not closing and not self.is_pr:
				payload["state_reason"] = "reopened"

		def done(res):
			if res.ok:
				text = _("#{number} closed.") if closing else _("#{number} reopened.")
				ui.message(text.format(number=self.number))
				self.load()
			else:
				ui.message(_("Could not change the state.") + " " + res.message)

		self.run(lambda: gh("PATCH", f"{self._api}/issues/{self.number}", body=payload), done)

	def on_merge(self, event):
		methods = [
			(_("Merge commit"), "merge"), (_("Squash and merge"), "squash"), (_("Rebase and merge"), "rebase"),
		]
		index = ask_choice(
			self, _("Merge Pull Request"), _("How should this pull request be merged?"), [m[0] for m in methods]
		)
		if index is None:
			return
		base = (self.pr.get("base") or {}).get("ref", "")
		if not confirm(
			self, _("Merge pull request #{number} into {base}? This cannot be undone.").format(
				number=self.number, base=base
			), _("Confirm Merge"), warning=True, default_no=True,
		):
			return
		ui.message(_("Merging..."))

		def done(res):
			if res.ok:
				ui.message(_("Pull request merged."))
				self.load()
			else:
				ui.message(_("Could not merge.") + " " + res.message)

		body = {"merge_method": methods[index][1], "sha": (self.pr.get("head") or {}).get("sha")}
		self.run(lambda: gh("PUT", f"{self._api}/pulls/{self.number}/merge", body=body), done)

	def on_review(self, event):
		kinds = [
			(_("Comment only"), "COMMENT"), (_("Approve"), "APPROVE"), (_("Request changes"), "REQUEST_CHANGES"),
		]
		values = ask_form(
			self, _("Submit Review"),
			[(_("Review &comment (required unless you approve):"), "body", True, "")],
			choices=[(_("Review &type:"), "kind", [k[0] for k in kinds], 0)],
			ok_label=_("&Submit Review"),
		)
		if not values:
			return
		event_name = kinds[values["kind"]][1]
		if event_name != "APPROVE" and not values["body"].strip():
			ui.message(_("Write a comment for this kind of review."))
			return
		body = {"event": event_name, "body": values["body"]}
		head = (self.pr.get("head") or {}).get("sha")
		if head:
			body["commit_id"] = head
		ui.message(_("Submitting review..."))

		def done(res):
			if res.ok:
				ui.message(_("Review submitted."))
				self.load()
			else:
				ui.message(_("Could not submit the review.") + " " + res.message)

		self.run(lambda: gh("POST", f"{self._api}/pulls/{self.number}/reviews", body=body), done)

	# --- more actions ------------------------------------------------
	def on_more(self, event):
		locked = bool(self.issue.get("locked"))
		self.popup_menu([
			(_("Edit &Title and Description..."), self.on_edit),
			(_("Edit &Labels..."), self.on_labels),
			(_("Edit &Assignees..."), self.on_assignees),
			(_("&Unlock Conversation") if locked else _("&Lock Conversation"), self.on_lock),
			None,
			(_("Copy Lin&k"), lambda: copy_to_clipboard(self._url(), _("Link copied."))),
			(_("Open in &Browser"), lambda: open_url(self._url())),
			(_("Re&fresh"), self.load),
		])

	def _patch(self, body, success):
		def done(res):
			if res.ok:
				ui.message(success)
				self.load()
			else:
				ui.message(_("That did not work.") + " " + res.message)

		self.run(lambda: gh("PATCH", f"{self._api}/issues/{self.number}", body=body), done)

	def on_edit(self):
		values = ask_form(
			self, _("Edit Title and Description"),
			[
				(_("&Title:"), "title", False, self.issue.get("title", ""), True),
				(_("&Description:"), "body", True, self.issue.get("body") or ""),
			],
			ok_label=_("&Save"),
		)
		if values:
			self._patch({"title": values["title"].strip(), "body": values["body"]}, _("Saved."))

	def on_labels(self):
		current = [lbl["name"] for lbl in self.issue.get("labels") or [] if isinstance(lbl, dict)]
		ui.message(_("Loading labels..."))

		def done(res):
			if not res.ok:
				ui.message(_("Could not load the labels.") + " " + res.message)
				return
			names = [lbl["name"] for lbl in res.data]
			if not names:
				ui.message(_("This repository has no labels yet."))
				return
			picked = ask_multi_choice(
				self, _("Edit Labels"), _("Tick the labels this conversation should have:"), names,
				[i for i, n in enumerate(names) if n in current],
			)
			if picked is None:
				return
			chosen = [names[i] for i in picked]

			def saved(result):
				if result.ok:
					ui.message(_("Labels updated."))
					self.load()
				else:
					ui.message(_("Could not update the labels.") + " " + result.message)

			self.run(
				lambda: gh("PUT", f"{self._api}/issues/{self.number}/labels", body={"labels": chosen}), saved
			)

		self.run(lambda: gh_paged(f"{self._api}/labels", max_items=500), done)

	def on_assignees(self):
		current = [a["login"] for a in self.issue.get("assignees") or []]
		ui.message(_("Loading people..."))

		def done(res):
			if not res.ok:
				ui.message(_("Could not load the people.") + " " + res.message)
				return
			names = [a["login"] for a in res.data]
			if not names:
				ui.message(_("Nobody can be assigned in this repository."))
				return
			picked = ask_multi_choice(
				self, _("Edit Assignees"), _("Tick the people this should be assigned to:"), names,
				[i for i, n in enumerate(names) if n in current],
			)
			if picked is not None:
				self._patch({"assignees": [names[i] for i in picked]}, _("Assignees updated."))

		self.run(lambda: gh_paged(f"{self._api}/assignees", max_items=500), done)

	def on_lock(self):
		locked = bool(self.issue.get("locked"))
		if not confirm(self, _("Unlock this conversation?") if locked else _("Lock this conversation?")):
			return

		def done(res):
			if res.ok:
				ui.message(_("Conversation unlocked.") if locked else _("Conversation locked."))
				self.load()
			else:
				ui.message(_("That did not work.") + " " + res.message)

		self.run(lambda: gh("DELETE" if locked else "PUT", f"{self._api}/issues/{self.number}/lock"), done)


def show_conversation(parent, full, number, is_pr, thread_id=None):
	show_modal(ConversationDialog(parent, full, number, is_pr, thread_id))


# ----------------------------------------------------------------------
# issues.py: Lists of issues and pull requests, and creating them.
# ----------------------------------------------------------------------

STATES = ["open", "closed", "all"]


def state_choices():
	return [_("Open"), _("Closed"), _("All")]


def issue_label(i):
	labels = ", ".join(label["name"] for label in i.get("labels") or [] if isinstance(label, dict))
	text = _("#{number}, {title}, {state}, by {author}, {comments} comments, updated {date}").format(
		number=i["number"], title=i["title"], state=_("open") if i["state"] == "open" else _("closed"),
		author=(i.get("user") or {}).get("login", _("unknown")), comments=i.get("comments", 0),
		date=format_date(i.get("updated_at")),
	)
	return text + (_(", labels: {labels}").format(labels=labels) if labels else "")


def pull_label(p):
	state = _("merged") if p.get("merged_at") else (_("open") if p["state"] == "open" else _("closed"))
	draft = _(", draft") if p.get("draft") else ""
	return _("#{number}, {title}, {state}{draft}, by {author}, {head} into {base}, updated {date}").format(
		number=p["number"], title=p["title"], state=state, draft=draft,
		author=(p.get("user") or {}).get("login", _("unknown")),
		head=(p.get("head") or {}).get("ref", "?"), base=(p.get("base") or {}).get("ref", "?"),
		date=format_date(p.get("updated_at")),
	)


def search_label(i):
	"""Label for issue/pull request search results, which span repositories."""
	repo = re.sub(r"^.*/repos/", "", i.get("repository_url", ""))
	kind = _("pull request") if "pull_request" in i else _("issue")
	return _("{repo} #{number}, {title}, {kind}, {state}, updated {date}").format(
		repo=repo, number=i["number"], title=i["title"], kind=kind, state=i["state"],
		date=format_date(i.get("updated_at")),
	)


def show_issues(parent, full, can_write=False):
	pager = FilteredPager(f"/repos/{full}/issues", lambda i: "pull_request" not in i)

	def fetch(page, selections):
		return pager.fetch(page, items_per_page(), key=tuple(selections), params={"state": STATES[selections[0]]})

	def new_issue(dlg, _item):
		"""Like the GitHub website: look for issue templates, let the user choose one, then fill it in."""
		from .conversation import choose_issue_input, fetch_templates
		ui.message(_("Looking for issue templates..."))

		def found(res):
			templates, config = res if isinstance(res, tuple) else ([], {"blank": True, "links": []})
			values = choose_issue_input(dlg, templates, config)
			if not values:
				return
			body = {"title": values["title"], "body": values["body"]}
			if values["labels"]:
				body["labels"] = values["labels"]
			if values["assignees"]:
				body["assignees"] = values["assignees"]

			def done(result):
				if result.ok:
					ui.message(_("Issue #{number} created.").format(number=result.data["number"]))
					dlg.reload()
				else:
					ui.message(_("Could not create the issue.") + " " + result.message)

			dlg.run(lambda: gh("POST", f"/repos/{full}/issues", body=body), done)

		dlg.run(lambda: fetch_templates(full), found)

	def view(dlg, issue):
		show_conversation(dlg, full, issue["number"], False)
		dlg.reload()

	actions = [
		(_("&View Issue"), view),
		(_("&New Issue"), new_issue, False),
		(_("Open in &Browser"), lambda d, i: open_url(i.get("html_url"))),
	]
	show_modal(PagedListDialog(
		parent, _("Issues: {repo}").format(repo=full), fetch, issue_label, actions,
		empty=_("No issues found."), choices=[(_("&State:"), state_choices())],
	))


def show_pulls(parent, full, ref="", default_branch="", can_write=False):
	def fetch(page, selections):
		return gh("GET", f"/repos/{full}/pulls", params={
			"state": STATES[selections[0]], "per_page": items_per_page(), "page": page,
		})

	def view(dlg, pull):
		show_conversation(dlg, full, pull["number"], True)
		dlg.reload()

	def new_pull(dlg, _item):
		create_pull_request(dlg, full, ref, default_branch, dlg.reload)

	actions = [
		(_("&View Pull Request"), view),
		(_("&New Pull Request"), new_pull, False),
		(_("Open in &Browser"), lambda d, p: open_url(p.get("html_url"))),
	]
	show_modal(PagedListDialog(
		parent, _("Pull Requests: {repo}").format(repo=full), fetch, pull_label, actions,
		empty=_("No pull requests found."), choices=[(_("&State:"), state_choices())],
	))


def create_pull_request(parent, full, head_hint="", base_hint="", on_done=None):
	"""Ask for branches, title and description, then open a pull request."""
	ui.message(_("Loading branches..."))

	def got(res):
		if not res.ok or not res.data:
			ui.message(_("Could not load branches.") + " " + res.message)
			return
		names = [b["name"] for b in res.data]
		if len(names) < 2:
			ui.message(_("A pull request needs at least two branches."))
			return
		head_default = names.index(head_hint) if head_hint in names else 0
		if head_hint == base_hint:
			others = [i for i, n in enumerate(names) if n != base_hint]
			head_default = others[0] if others else 0
		head = ask_choice(
			parent, _("New Pull Request"), _("Choose the branch with your changes (the compare branch):"),
			names, head_default,
		)
		if head is None:
			return
		base_default = names.index(base_hint) if base_hint in names else 0
		base = ask_choice(
			parent, _("New Pull Request"), _("Choose the branch to merge into (the base branch):"),
			names, base_default,
		)
		if base is None:
			return
		if head == base:
			ui.message(_("Choose two different branches."))
			return
		values = ask_form(
			parent, _("New Pull Request: {head} into {base}").format(head=names[head], base=names[base]),
			[
				(_("&Title:"), "title", False, names[head].replace("-", " ").replace("_", " "), True),
				(_("&Description:"), "body", True, ""),
			],
			checks=[(_("Open as a &draft"), "draft", False)],
			ok_label=_("&Create Pull Request"),
		)
		if not values:
			return
		body = {
			"title": values["title"].strip(), "body": values["body"], "head": names[head],
			"base": names[base], "draft": values["draft"],
		}
		ui.message(_("Creating pull request..."))

		def created(result):
			if result.ok and isinstance(result.data, dict):
				ui.message(_("Pull request #{number} created.").format(number=result.data["number"]))
				if on_done:
					on_done()
			else:
				ui.message(_("Could not create the pull request.") + " " + result.message)

		run_async(lambda: gh("POST", f"/repos/{full}/pulls", body=body), created)

	run_async(lambda: list_branches(full), got)


def _merge_key(item, sort):
	"""Sort key for merging the separate issue and pull request searches."""
	if sort == "comments":
		return item.get("comments") or 0
	if sort == "reactions":
		return (item.get("reactions") or {}).get("total_count", 0)
	return item.get("created_at" if sort == "created" else "updated_at") or ""


def search_issues(query, page, per_page, sort="updated"):
	"""Search issues and pull requests. Returns a Result whose data is a list.

	GitHub has asked for an explicit is:issue or is:pr in the past. If it still does,
	the two kinds are searched separately and merged.
	"""
	params = {"per_page": per_page, "page": page, "order": "desc"}
	if sort:
		params["sort"] = sort
	res = gh("GET", "/search/issues", params=dict(params, q=query))
	if res.code == 422 and "is:issue" in str(res.data):
		parts = [gh("GET", "/search/issues", params=dict(params, q=f"{query} {kind}")) for kind in ("is:issue", "is:pr")]
		if all(p.ok and isinstance(p.data, dict) for p in parts):
			merged = [i for p in parts for i in p.data.get("items", [])]
			merged.sort(key=lambda i: _merge_key(i, sort), reverse=True)
			more = any(p.has_next for p in parts)
			return Result(200, merged, {"Link": '<merged>; rel="next"'} if more else {})
		return next((p for p in parts if not p.ok), res)
	if res.ok and isinstance(res.data, dict):
		return Result(res.code, res.data.get("items", []), res.headers)
	return res


def search_results_dialog(parent, title, query, empty):
	"""A list of issues or pull requests found with GitHub search."""

	def fetch(page, selections):
		return search_issues(query, page, items_per_page())

	def view(dlg, item):
		repo = re.sub(r"^.*/repos/", "", item.get("repository_url", ""))
		show_conversation(dlg, repo, item["number"], "pull_request" in item)

	actions = [
		(_("&View"), view),
		(_("Open in &Browser"), lambda d, i: open_url(i.get("html_url"))),
	]
	show_modal(PagedListDialog(parent, title, fetch, search_label, actions, empty=empty))


# ----------------------------------------------------------------------
# mywork.py: My Work: what needs your attention across all of GitHub.
# ----------------------------------------------------------------------

def queries(login):
	"""(title, search query, empty message) for each view."""
	return [
		(_("Assigned to me"), f"is:open assignee:{login} archived:false", _("Nothing is assigned to you.")),
		(_("Pull requests waiting for my review"), f"is:open is:pr review-requested:{login} archived:false", _("No review requests.")),
		(_("My open pull requests"), f"is:open is:pr author:{login} archived:false", _("You have no open pull requests.")),
		(_("My open issues"), f"is:open is:issue author:{login} archived:false", _("You have no open issues.")),
		(_("Mentioning me"), f"is:open mentions:{login} archived:false", _("Nobody has mentioned you in open items.")),
		(_("Recently closed or merged by me"), f"is:closed author:{login} archived:false", _("Nothing found.")),
	]


def show_my_work(parent):
	"""Let the user pick a view, then show it."""
	from .core import ask_choice
	ui.message(_("Checking who you are..."))

	def done(login):
		if not login:
			ui.message(_("Could not find your account. Check your token."))
			return
		views = queries(login)
		index = ask_choice(parent, _("My Work"), _("What do you want to see?"), [v[0] for v in views])
		if index is not None:
			title, query, empty = views[index]
			search_results_dialog(parent, title, query, empty)

	run_async(current_login, done)


# ----------------------------------------------------------------------
# actions.py: GitHub Actions: workflow runs, their jobs and steps, logs, re-run and cancel.
# ----------------------------------------------------------------------

MAX_LOG_CHARS = 300000
_CONCLUSIONS = {
	"success": lambda: _("succeeded"), "failure": lambda: _("failed"), "cancelled": lambda: _("cancelled"),
	"skipped": lambda: _("skipped"), "timed_out": lambda: _("timed out"), "neutral": lambda: _("neutral"),
	"action_required": lambda: _("action required"), "stale": lambda: _("stale"),
	"startup_failure": lambda: _("failed to start"),
}
_STATUSES = {
	"queued": lambda: _("queued"), "in_progress": lambda: _("running"), "waiting": lambda: _("waiting"),
	"pending": lambda: _("pending"), "requested": lambda: _("requested"),
}


def outcome(item):
	"""'succeeded', 'failed', 'running'... for a run, job or step."""
	conclusion = item.get("conclusion")
	if conclusion:
		return _CONCLUSIONS.get(conclusion, lambda: conclusion)()
	status = item.get("status") or ""
	return _STATUSES.get(status, lambda: status or _("unknown"))()


def run_label(run):
	title = run.get("display_title") or run.get("name") or _("Workflow run")
	return _("{name}, {title}, {outcome}, branch {branch}, {event}, #{number}, {date}").format(
		name=run.get("name", ""), title=title, outcome=outcome(run), branch=run.get("head_branch") or "?",
		event=(run.get("event") or "").replace("_", " "), number=run.get("run_number", ""),
		date=format_datetime(run.get("created_at")),
	)


def run_text(run, jobs):
	lines = [
		f"{run.get('name', '')}: {run.get('display_title', '')}",
		_("Result: {outcome}").format(outcome=outcome(run)),
		_("Branch: {branch}. Triggered by {event} from {actor}.").format(
			branch=run.get("head_branch") or "?", event=(run.get("event") or "").replace("_", " "),
			actor=(run.get("actor") or {}).get("login", _("unknown")),
		),
		_("Commit: {sha}").format(sha=(run.get("head_sha") or "")[:7]),
		_("Started: {date}. Attempt {attempt}.").format(
			date=format_datetime(run.get("run_started_at") or run.get("created_at")), attempt=run.get("run_attempt", 1)
		),
		"", _("Jobs:"),
	]
	if not jobs:
		lines.append(_("No jobs have started yet."))
	for job in jobs:
		lines.append(f"- {job.get('name')}: {outcome(job)}")
		for step in job.get("steps") or []:
			lines.append("    " + _("Step {number}: {name}, {outcome}").format(
				number=step.get("number"), name=step.get("name"), outcome=outcome(step)
			))
	return "\n".join(lines)


def extract_log(data):
	"""Join every log file of a run's log ZIP, in order."""
	try:
		with zipfile.ZipFile(io.BytesIO(data)) as archive:
			names = sorted(n for n in archive.namelist() if n.endswith(".txt"))
			parts = []
			for name in names:
				text = archive.read(name).decode("utf-8", "replace")
				parts.append(f"===== {name} =====\n{text}")
		return "\n".join(parts)
	except (zipfile.BadZipFile, OSError):
		return ""


def show_run(parent, full, run_id, can_write=False):
	"""Details of one workflow run."""
	ui.message(_("Loading workflow run..."))

	def work():
		run = gh("GET", f"/repos/{full}/actions/runs/{run_id}")
		if not run.ok:
			return run
		jobs = gh_paged(f"/repos/{full}/actions/runs/{run_id}/jobs", unwrap="jobs", max_items=100)
		return Result(200, (run.data, jobs.data if jobs.ok else []), {})

	def done(res):
		if not res.ok:
			ui.message(_("Could not load the run.") + " " + res.message)
			return
		run, jobs = res.data

		def show_log(dialog):
			ui.message(_("Loading log..."))

			def got(result):
				if not result.ok:
					message = _("The log is not available. It may have expired or the run is still going.")
					ui.message(message if result.code in (404, 410) else result.message)
					return
				text = extract_log(result.data)
				if not text:
					ui.message(_("The log is empty."))
					return
				show_modal(TextViewDialog(
					dialog, _("Log: {name}").format(name=run.get("name", "")),
					tail(text, MAX_LOG_CHARS, _("[Log shortened. Showing the end.]") + "\n"),
					label=_("&Log (the end comes last):"), wrap=False,
				))

			run_async(lambda: gh("GET", f"/repos/{full}/actions/runs/{run_id}/logs", binary=True, timeout=120), got)

		def rerun(dialog, failed_only):
			if not confirm(dialog, _("Run this workflow again?")):
				return
			path = "rerun-failed-jobs" if failed_only else "rerun"

			def got(result):
				ui.message(_("The run was queued again.") if result.ok else _("Could not re-run.") + " " + result.message)

			run_async(lambda: gh("POST", f"/repos/{full}/actions/runs/{run_id}/{path}"), got)

		def cancel(dialog):
			if not confirm(dialog, _("Cancel this run?"), warning=True, default_no=True):
				return

			def got(result):
				ui.message(_("Cancel requested.") if result.ok else _("Could not cancel.") + " " + result.message)

			run_async(lambda: gh("POST", f"/repos/{full}/actions/runs/{run_id}/cancel"), got)

		buttons = [(_("Show &Log"), show_log)]
		if can_write:
			buttons += [
				(_("Re-run &All Jobs"), lambda d: rerun(d, False)),
				(_("Re-run &Failed Jobs"), lambda d: rerun(d, True)),
				(_("&Cancel Run"), cancel),
			]
		buttons += [
			(_("Copy Lin&k"), lambda d: copy_to_clipboard(run.get("html_url", ""), _("Link copied."))),
			(_("Open in &Browser"), lambda d: open_url(run.get("html_url"))),
		]
		show_modal(TextViewDialog(
			parent, _("Workflow run #{number}").format(number=run.get("run_number", "")), run_text(run, jobs),
			buttons=buttons, label=_("Run &details:"),
		))

	run_async(work, done)


def show_runs(parent, full, ref="", can_write=False):
	"""List workflow runs for a repository."""
	scopes = [_("All branches"), _("Current branch: {branch}").format(branch=ref or _("default"))]
	statuses = ["", "success", "failure", "in_progress"]

	def fetch(page, selections):
		params = {"per_page": items_per_page(), "page": page}
		if selections[0] == 1 and ref:
			params["branch"] = ref
		if statuses[selections[1]]:
			params["status"] = statuses[selections[1]]
		res = gh("GET", f"/repos/{full}/actions/runs", params=params)
		if res.ok and isinstance(res.data, dict):
			return Result(res.code, res.data.get("workflow_runs", []), res.headers)
		return res

	actions = [
		(_("&View Run"), lambda d, r: show_run(d, full, r["id"], can_write)),
		(_("Open in &Browser"), lambda d, r: open_url(r.get("html_url"))),
	]
	show_modal(PagedListDialog(
		parent, _("Actions: {repo}").format(repo=full), fetch, run_label, actions,
		empty=_("No workflow runs found. This repository may not use GitHub Actions."),
		choices=[
			(_("&Show:"), scopes),
			(_("S&tatus:"), [_("Any"), _("Succeeded"), _("Failed"), _("Running")]),
		],
	))


class YamlError(ValueError):
	pass


_BLOCK_HEADER = re.compile(r"^([|>])([+-]?)(\d?)([+-]?)$")
_INT = re.compile(r"^[-+]?\d+$")
_FLOAT = re.compile(r"^[-+]?(\d+\.\d*|\.\d+)([eE][-+]?\d+)?$")


def _strip_comment(text):
	"""Remove a trailing ' # comment' that is not inside quotes."""
	quote = None
	for index, char in enumerate(text):
		if quote:
			if char == quote:
				quote = None
		elif char in "'\"" and (index == 0 or text[index - 1] in " \t[{,:-"):
			quote = char
		elif char == "#" and (index == 0 or text[index - 1] in " \t"):
			return text[:index].rstrip()
	return text.rstrip()


def _unquote(text):
	if text[0] == "'":
		if len(text) < 2 or text[-1] != "'":
			raise YamlError("unterminated quote")
		return text[1:-1].replace("''", "'")
	if len(text) < 2 or text[-1] != '"':
		raise YamlError("unterminated quote")
	try:
		return json.loads(text)
	except ValueError:
		return text[1:-1].replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")


def _plain(text):
	if text in ("", "~") or text.lower() == "null":
		return None
	low = text.lower()
	if low == "true":
		return True
	if low == "false":
		return False
	if _INT.match(text):
		return int(text)
	if _FLOAT.match(text):
		return float(text)
	return text


def _flow(text, pos=0):
	"""Parse one flow value ([...], {...}, quoted or plain) starting at pos. Returns (value, new position)."""
	length = len(text)
	while pos < length and text[pos] in " \t":
		pos += 1
	if pos >= length:
		return None, pos
	char = text[pos]
	if char == "[":
		items, pos = [], pos + 1
		while True:
			while pos < length and text[pos] in " \t,":
				pos += 1
			if pos >= length:
				raise YamlError("unterminated [")
			if text[pos] == "]":
				return items, pos + 1
			value, pos = _flow(text, pos)
			items.append(value)
	if char == "{":
		result, pos = {}, pos + 1
		while True:
			while pos < length and text[pos] in " \t,":
				pos += 1
			if pos >= length:
				raise YamlError("unterminated {")
			if text[pos] == "}":
				return result, pos + 1
			key, pos = _flow_scalar(text, pos, ":}")
			while pos < length and text[pos] in " \t":
				pos += 1
			value = None
			if pos < length and text[pos] == ":":
				value, pos = _flow(text, pos + 1)
			result[str(key)] = value
	return _flow_scalar(text, pos, ",]}")


def _flow_scalar(text, pos, stops):
	length = len(text)
	if pos < length and text[pos] in "'\"":
		quote, end = text[pos], pos + 1
		while end < length:
			if text[end] == "\\" and quote == '"':
				end += 2
				continue
			if text[end] == quote:
				if quote == "'" and end + 1 < length and text[end + 1] == "'":
					end += 2
					continue
				break
			end += 1
		return _unquote(text[pos:end + 1]), end + 1
	end = pos
	while end < length and text[end] not in stops:
		end += 1
	return _plain(text[pos:end].strip()), end


def _split_key(content):
	"""'key: value' -> (key, value text). None when the line is not a mapping entry."""
	if not content or content[0] in "[{":
		return None
	if content[0] in "'\"":
		quote, end = content[0], 1
		while end < len(content) and content[end] != quote:
			end += 2 if content[end] == "\\" and quote == '"' else 1
		rest = content[end + 1:].lstrip()
		if end >= len(content) or not rest.startswith(":"):
			return None
		return _unquote(content[:end + 1]), rest[1:].strip()
	match = re.match(r"^(.*?[^\s]):(?:\s+(.*))?$", content)
	if not match or content.startswith("- "):
		return None
	return match.group(1).strip(), (match.group(2) or "").strip()


class _Reader:
	def __init__(self, text):
		text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
		self.lines = text.split("\n")
		self.i = 0

	def peek(self):
		"""(index, indent, content without comment) of the next meaningful line, or None."""
		j = self.i
		while j < len(self.lines):
			raw = self.lines[j]
			content = _strip_comment(raw.strip())
			if not content or (raw[:3] in ("---", "...") and raw.strip() in ("---", "...")):
				j += 1
				continue
			if content.startswith("%"):
				j += 1
				continue
			indent = len(raw) - len(raw.lstrip(" "))
			self.i = j
			return j, indent, content
		self.i = len(self.lines)
		return None

	def node(self, minimum):
		item = self.peek()
		if item is None or item[1] < minimum:
			return None
		_j, indent, content = item
		if content == "-" or content.startswith("- "):
			return self.sequence(indent)
		if _split_key(content):
			return self.mapping(indent)
		self.i += 1
		return self.inline(content)

	def inline(self, text):
		if text[0] in "[{":
			value, pos = _flow(text)
			if text[pos:].strip():
				raise YamlError("unexpected text after flow value")
			return value
		if text[0] in "'\"":
			return _unquote(text)
		return _plain(text)

	def block_scalar(self, header, parent_indent):
		match = _BLOCK_HEADER.match(header)
		style, chomp = match.group(1), (match.group(2) or match.group(4))
		lines = []
		block_indent = None
		while self.i < len(self.lines):
			raw = self.lines[self.i]
			if raw.strip() == "":
				lines.append("")
				self.i += 1
				continue
			indent = len(raw) - len(raw.lstrip(" "))
			if block_indent is None:
				if indent <= parent_indent:
					break
				block_indent = indent
			if indent < block_indent:
				break
			lines.append(raw[block_indent:])
			self.i += 1
		trailing = 0
		while lines and lines[-1] == "":
			lines.pop()
			trailing += 1
		if style == "|":
			text = "\n".join(lines)
		else:
			text, previous_blank = "", False
			for number, line in enumerate(lines):
				if number == 0:
					text = line
				elif line == "":
					text += "\n"
					previous_blank = True
				else:
					text += ("" if previous_blank else " ") + line
					previous_blank = False
		if not lines:
			return ""
		if chomp == "-":
			return text
		if chomp == "+":
			return text + "\n" * (trailing + 1)
		return text + "\n"

	def value(self, rest, indent):
		"""The value after 'key:' (rest) or after '- ' for an entry at the given indent."""
		if rest == "":
			item = self.peek()
			if item is None:
				return None
			_j, next_indent, content = item
			if next_indent > indent:
				return self.node(next_indent)
			if next_indent == indent and (content == "-" or content.startswith("- ")):
				return self.sequence(indent)
			return None
		if _BLOCK_HEADER.match(rest):
			return self.block_scalar(rest, indent)
		value = self.inline(rest)
		# A plain text that continues on the following, deeper lines.
		if isinstance(value, str) and rest[0] not in "'\"[{":
			while self.i < len(self.lines):
				raw = self.lines[self.i]
				content = _strip_comment(raw.strip())
				if not content or len(raw) - len(raw.lstrip(" ")) <= indent:
					break
				value += " " + content
				self.i += 1
		return value

	def mapping(self, indent):
		result = {}
		while True:
			item = self.peek()
			if item is None:
				break
			_j, current, content = item
			if current != indent:
				if current > indent:
					raise YamlError("bad indentation")
				break
			parts = _split_key(content)
			if not parts:
				break
			self.i += 1
			result[str(parts[0])] = self.value(parts[1], indent)
		return result

	def sequence(self, indent):
		items = []
		while True:
			item = self.peek()
			if item is None:
				break
			j, current, content = item
			if current != indent or not (content == "-" or content.startswith("- ")):
				if current > indent:
					raise YamlError("bad indentation")
				break
			after = content[1:].lstrip()
			offset = len(content) - len(after)
			if after and _split_key(after) and not _BLOCK_HEADER.match(after):
				# "- key: value": the entry is a mapping whose first key sits after the dash.
				raw = self.lines[j]
				self.lines[j] = " " * (indent + offset) + raw.strip()[offset:].lstrip()
				items.append(self.mapping(indent + offset))
				continue
			self.i += 1
			items.append(self.value(after, indent))
		return items


def load_yaml(text):
	"""Parse the YAML subset used by issue templates. Raises YamlError (a ValueError) when it cannot."""
	try:
		return _Reader(text).node(0)
	except YamlError:
		raise
	except Exception as e:
		raise YamlError(str(e))


TEMPLATE_DIRS = (".github/ISSUE_TEMPLATE", "ISSUE_TEMPLATE", "docs/ISSUE_TEMPLATE")
SINGLE_FILES = (".github/ISSUE_TEMPLATE.md", "ISSUE_TEMPLATE.md", "docs/ISSUE_TEMPLATE.md", ".github/issue_template.md")
TEMPLATE_EXTENSIONS = (".md", ".markdown", ".yml", ".yaml")
NO_RESPONSE = "_No response_"


def _text(value):
	return "" if value is None else str(value)


def _as_list(value):
	if isinstance(value, (list, tuple)):
		return [_text(v).strip() for v in value if _text(v).strip()]
	return split_names(_text(value))


# ----------------------------------------------------------------------
# Reading the templates
# ----------------------------------------------------------------------

def _front_matter_fallback(block):
	"""Read 'key: value' lines when the front matter is not valid YAML (a colon inside a title, say)."""
	data = {}
	for line in block.splitlines():
		match = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
		if match:
			value = match.group(2).strip()
			if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
				value = value[1:-1]
			data[match.group(1).lower()] = value
	return data


def parse_markdown_template(filename, content):
	content = content.lstrip("﻿").replace("\r\n", "\n")
	data, body = {}, content
	match = re.match(r"^---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)(.*)$", content, re.S)
	if match:
		block, body = match.group(1), match.group(2)
		try:
			loaded = load_yaml(block)
			data = loaded if isinstance(loaded, dict) else {}
		except Exception:
			data = _front_matter_fallback(block)
	name = _text(data.get("name")).strip() or re.sub(r"\.\w+$", "", filename).replace("_", " ").replace("-", " ")
	return {
		"kind": "markdown", "file": filename, "name": name, "about": _text(data.get("about") or data.get("description")).strip(),
		"title": _text(data.get("title")), "labels": _as_list(data.get("labels")),
		"assignees": _as_list(data.get("assignees")), "body": body.strip("\n"), "fields": [],
	}


def parse_form_template(filename, content):
	data = load_yaml(content.lstrip("﻿"))
	if not isinstance(data, dict) or not isinstance(data.get("body"), list):
		return None
	name = _text(data.get("name")).strip() or re.sub(r"\.\w+$", "", filename).replace("_", " ").replace("-", " ")
	fields = [item for item in data["body"] if isinstance(item, dict) and item.get("type") in (
		"markdown", "input", "textarea", "dropdown", "checkboxes")]
	return {
		"kind": "form", "file": filename, "name": name, "about": _text(data.get("description")).strip(),
		"title": _text(data.get("title")), "labels": _as_list(data.get("labels")),
		"assignees": _as_list(data.get("assignees")), "body": "", "fields": fields,
	}


def parse_template(filename, content):
	try:
		if filename.lower().endswith((".yml", ".yaml")):
			return parse_form_template(filename, content)
		return parse_markdown_template(filename, content)
	except Exception:
		log.debugWarning("GitHub for NVDA: could not read issue template %s", filename, exc_info=True)
		return None


def _fetch_text(url):
	res = gh("GET", url, accept="application/vnd.github.raw+json", binary=True)
	if res.ok and isinstance(res.data, (bytes, bytearray)):
		return bytes(res.data).decode("utf-8", errors="replace")
	return None


def fetch_templates(full):
	"""Blocking. Returns (templates, config) where config is {"blank": bool, "links": [(name, url, about)]}."""
	templates, config = [], {"blank": True, "links": []}
	entries, directory = [], ""
	for candidate in TEMPLATE_DIRS:
		res = gh("GET", f"/repos/{full}/contents/{q(candidate, '/')}")
		if res.ok and isinstance(res.data, list):
			entries, directory = res.data, candidate
			break
	for entry in sorted(entries, key=lambda e: e.get("name", "").lower()):
		name = entry.get("name", "")
		lowered = name.lower()
		if entry.get("type") != "file":
			continue
		if lowered in ("config.yml", "config.yaml"):
			text = _fetch_text(entry.get("url"))
			try:
				data = load_yaml(text or "") or {}
			except Exception:
				data = {}
			if isinstance(data, dict):
				config["blank"] = data.get("blank_issues_enabled", True) is not False
				for link in data.get("contact_links") or []:
					if isinstance(link, dict) and link.get("url"):
						config["links"].append((_text(link.get("name")) or _text(link["url"]), _text(link["url"]), _text(link.get("about"))))
			continue
		if not lowered.endswith(TEMPLATE_EXTENSIONS):
			continue
		text = _fetch_text(entry.get("url"))
		template = parse_template(name, text) if text is not None else None
		if template:
			templates.append(template)
	if not entries:
		for candidate in SINGLE_FILES:
			text = _fetch_text(f"/repos/{full}/contents/{q(candidate, '/')}")
			if text is not None:
				template = parse_template(candidate.rsplit("/", 1)[-1], text)
				if template:
					templates.append(template)
				break
	return templates, config


# ----------------------------------------------------------------------
# Issue forms (.yml templates)
# ----------------------------------------------------------------------

def _attr(item, key):
	attributes = item.get("attributes")
	return attributes.get(key) if isinstance(attributes, dict) else None


def _required(item):
	validations = item.get("validations")
	return isinstance(validations, dict) and validations.get("required") is True


class IssueFormDialog(AccessibleDialog):
	"""The questions of an issue form, one control each, plus the title. Ctrl+Enter submits."""

	def __init__(self, parent, template):
		super().__init__(parent, _("New Issue: {name}").format(name=template["name"]), (680, 620))
		self.template = template
		self.result = None
		self._rows = []  # (item, controls)
		sizer = wx.BoxSizer(wx.VERTICAL)
		scroller = wx.ScrolledWindow(self, style=wx.VSCROLL)
		scroller.SetScrollRate(0, 20)
		inner = wx.BoxSizer(wx.VERTICAL)
		self._scroller = scroller

		notes = "\n\n".join(
			_text(_attr(i, "value")).strip() for i in template["fields"] if i.get("type") == "markdown" and _text(_attr(i, "value")).strip()
		)
		if template["about"] and template["about"] not in notes:
			notes = (template["about"] + "\n\n" + notes).strip()
		if notes:
			inner.Add(wx.StaticText(scroller, label=_("Template &instructions:")), 0, wx.ALL, PAD)
			box = wx.TextCtrl(scroller, value=notes, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_BESTWRAP, size=(600, 110))
			inner.Add(box, 0, wx.EXPAND | wx.ALL, PAD)
		inner.Add(wx.StaticText(scroller, label=_("&Title:") + " " + _("(required)")), 0, wx.ALL, PAD)
		self.title = wx.TextCtrl(scroller, value=template["title"])
		inner.Add(self.title, 0, wx.EXPAND | wx.ALL, PAD)
		first = self.title

		for item in template["fields"]:
			kind = item.get("type")
			if kind == "markdown":
				continue
			label = _text(_attr(item, "label")).strip() or _text(item.get("id")) or kind
			description = _text(_attr(item, "description")).strip()
			shown = escape_label(label) + (" " + _("(required)") if _required(item) else "")
			if description:
				shown += ". " + escape_label(description)
			inner.Add(make_label(scroller, shown, wrap=600), 0, wx.LEFT | wx.TOP, PAD)
			if kind == "input":
				control = wx.TextCtrl(scroller, value=_text(_attr(item, "value")))
				control.SetHint(_text(_attr(item, "placeholder")))
				inner.Add(control, 0, wx.EXPAND | wx.ALL, PAD)
				self._rows.append((item, control))
			elif kind == "textarea":
				control = wx.TextCtrl(
					scroller, value=_text(_attr(item, "value")), style=wx.TE_MULTILINE | wx.TE_BESTWRAP, size=(600, 140),
				)
				control.SetHint(_text(_attr(item, "placeholder")))
				inner.Add(control, 0, wx.EXPAND | wx.ALL, PAD)
				self._rows.append((item, control))
			elif kind == "dropdown":
				options = [_text(o) for o in (_attr(item, "options") or [])]
				multiple = _attr(item, "multiple") is True
				if multiple:
					controls = []
					for option in options:
						box = wx.CheckBox(scroller, label=escape_label(option))
						inner.Add(box, 0, wx.ALL, PAD)
						controls.append(box)
					self._rows.append((item, controls))
				else:
					choices = options if _required(item) else [_("None")] + options
					control = wx.Choice(scroller, choices=choices)
					default = _attr(item, "default")
					index = default if isinstance(default, int) and 0 <= default < len(options) else 0
					control.SetSelection(index + (0 if _required(item) else 1) if (isinstance(default, int) or _required(item)) else 0)
					inner.Add(control, 0, wx.ALL, PAD)
					self._rows.append((item, control))
			else:  # checkboxes
				controls = []
				for option in _attr(item, "options") or []:
					if not isinstance(option, dict):
						continue
					text = _text(option.get("label")).strip()
					if option.get("required") is True:
						text += " " + _("(required)")
					box = wx.CheckBox(scroller, label=escape_label(text))
					inner.Add(box, 0, wx.ALL, PAD)
					controls.append((option, box))
				self._rows.append((item, controls))
		scroller.SetSizer(inner)
		sizer.Add(scroller, 1, wx.EXPAND)
		row = wx.BoxSizer(wx.HORIZONTAL)
		ok = wx.Button(self, wx.ID_OK, _("&Create Issue"))
		ok.SetDefault()
		ok.Bind(wx.EVT_BUTTON, self._on_ok)
		row.Add(ok, 0, wx.ALL, PAD)
		row.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancel")), 0, wx.ALL, PAD)
		sizer.Add(row, 0, wx.ALIGN_RIGHT)
		self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
		# No automatic access keys here: labels come from the template.
		self.SetSizer(sizer)
		self.Centre()
		first.SetFocus()

	def _on_char_hook(self, event):
		if event.ControlDown() and event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			self._on_ok(None)
		else:
			event.Skip()

	def _complain(self, message, control):
		ui.message(message)
		try:
			control.SetFocus()
		except Exception:
			pass

	def _on_ok(self, event):
		title = self.title.GetValue().strip()
		if not title:
			self._complain(_("Title is required."), self.title)
			return
		parts = []
		for item, control in self._rows:
			kind = item.get("type")
			label = _text(_attr(item, "label")).strip() or _text(item.get("id")) or kind
			required = _required(item)
			if kind in ("input", "textarea"):
				value = control.GetValue().strip()
				if required and not value:
					self._complain(_("{field} is required.").format(field=label), control)
					return
				if value and kind == "textarea" and _text(_attr(item, "render")).strip():
					value = "```" + _text(_attr(item, "render")).strip() + "\n" + value + "\n```"
				text = value or NO_RESPONSE
			elif kind == "dropdown":
				if isinstance(control, list):
					chosen = [c.GetLabel().replace("&&", "&") for c in control if c.GetValue()]
					if required and not chosen:
						self._complain(_("{field} is required.").format(field=label), control[0] if control else self)
						return
					text = ", ".join(chosen) or NO_RESPONSE
				else:
					selection = control.GetStringSelection()
					if selection == _("None") and not required:
						selection = ""
					if required and not selection:
						self._complain(_("{field} is required.").format(field=label), control)
						return
					text = selection or NO_RESPONSE
			else:
				lines = []
				for option, box in control:
					if option.get("required") is True and not box.GetValue():
						self._complain(_("You must tick: {name}").format(name=_text(option.get("label")).strip()), box)
						return
					lines.append(f"- [{'x' if box.GetValue() else ' '}] {_text(option.get('label')).strip()}")
				text = "\n".join(lines)
			parts.append(f"### {label}\n\n{text}")
		self.result = {
			"title": title, "body": "\n\n".join(parts), "labels": self.template["labels"],
			"assignees": self.template["assignees"],
		}
		self.EndModal(wx.ID_OK)


# ----------------------------------------------------------------------
# The whole "New Issue" flow
# ----------------------------------------------------------------------

def _template_choice_label(template):
	if template["about"]:
		return _("{name}: {about}").format(name=template["name"], about=template["about"])
	return template["name"]


def choose_issue_input(parent, templates, config):
	"""Ask which template to use and fill it in. Returns a dict (title, body, labels, assignees), or None."""
	from .core import open_url
	entries = [("template", t) for t in templates]
	if config["blank"] or not (templates or config["links"]):
		entries.append(("blank", None))
	for name, url, about in config["links"]:
		entries.append(("link", (name, url, about)))
	labels = []
	for kind, data in entries:
		if kind == "template":
			labels.append(_template_choice_label(data))
		elif kind == "blank":
			labels.append(_("Open a blank issue"))
		else:
			labels.append(_("{name}{about} (opens in your browser)").format(
				name=data[0], about=(": " + data[2]) if data[2] else ""))
	index = 0
	if len(entries) > 1 or entries[0][0] == "link":
		index = ask_choice(parent, _("Choose an Issue Template"), _("Choose a template for the new issue:"), labels)
		if index is None:
			return None
	kind, data = entries[index]
	if kind == "link":
		open_url(data[1])
		return None
	if kind == "template" and data["kind"] == "form":
		dialog = IssueFormDialog(parent, data)
		try:
			if dialog.ShowModal() == wx.ID_OK:
				return dialog.result
		finally:
			dialog.Destroy()
		return None
	template = data or {"title": "", "body": "", "labels": [], "assignees": [], "name": ""}
	values = ask_form(
		parent, _("New Issue") if not data else _("New Issue: {name}").format(name=template["name"]),
		[
			(_("&Title:"), "title", False, template["title"], True),
			(_("&Description:"), "body", True, template["body"]),
			(_("&Labels (optional, comma separated):"), "labels", False, ", ".join(template["labels"])),
		],
		ok_label=_("&Create Issue"),
	)
	if not values:
		return None
	return {
		"title": values["title"].strip(), "body": values["body"], "labels": split_names(values["labels"]),
		"assignees": template["assignees"],
	}
