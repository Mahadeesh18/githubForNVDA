# GitHub for NVDA
# Copyright (C) 2025-2026 Mahadeesh and contributors
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for more details.

"""Notifications, the background monitor, and the add-on's category in NVDA's Settings dialog."""

import addonHandler
import datetime
import re
import threading
import tones
import ui
import wx
from gui import guiHelper
from gui.settingsDialogs import SettingsPanel
from logHandler import log

from .logic import (
	format_date,
	repo_from_api_url,
)
from .core import (
	NOTIFICATION_FILTER_ALL,
	NOTIFICATION_FILTER_DIRECT,
	NOTIFICATION_INTERVALS,
	Result,
	delete_token,
	forget_identity,
	get_pref,
	gh,
	has_token,
	items_per_page,
	load_token,
	notification_seconds,
	open_url,
	run_async,
	save_token,
	say,
	set_pref,
)
from .dialogs import (
	PagedListDialog,
	confirm,
	copy_to_clipboard,
	inform,
	show_modal,
)
from .conversation import (
	show_conversation,
)

addonHandler.initTranslation()


# ----------------------------------------------------------------------
# notifications.py: Notifications: read, open, mark as read, and the unread count.
# ----------------------------------------------------------------------

def reasons():
	return {
		"assign": _("assigned to you"), "author": _("you created it"), "comment": _("new comment"),
		"mention": _("you were mentioned"), "review_requested": _("review requested"),
		"subscribed": _("you are watching"), "team_mention": _("your team was mentioned"),
		"state_change": _("state changed"), "ci_activity": _("CI activity"),
		"security_alert": _("security alert"), "manual": _("you subscribed"),
		"approval_requested": _("approval requested"),
	}


def notification_label(n):
	subject = n.get("subject") or {}
	state = _("unread") if n.get("unread") else _("read")
	return _("{repo}: {title}, {type}, {reason}, {state}, {date}").format(
		repo=(n.get("repository") or {}).get("full_name", ""), title=subject.get("title", ""),
		type=subject.get("type", ""), reason=reasons().get(n.get("reason"), n.get("reason", "")),
		state=state, date=format_date(n.get("updated_at")),
	)


def subject_number(url):
	"""'.../issues/12' or '.../pulls/12' -> 12."""
	match = re.search(r"/(?:issues|pulls)/(\d+)$", url or "")
	return int(match.group(1)) if match else None


def html_url(n):
	"""Best-effort web address for a notification."""
	repo = (n.get("repository") or {}).get("html_url", "https://github.com/notifications")
	url = (n.get("subject") or {}).get("url") or ""
	match = re.search(r"/repos/([^/]+/[^/]+)/(issues|pulls|commits|releases)/([^/]+)$", url)
	if not match:
		return repo
	full, kind, ident = match.groups()
	if kind == "releases":
		return f"{repo}/releases"
	kind = {"issues": "issues", "pulls": "pull", "commits": "commit"}[kind]
	return f"https://github.com/{full}/{kind}/{ident}"


def count_unread():
	"""Blocking: return (count, more) of unread notifications, or a failed Result."""
	res = gh("GET", "/notifications", params={"per_page": 50}, retries=1, timeout=15)
	if not res.ok or not isinstance(res.data, list):
		return res
	return len(res.data), res.has_next


def unread_message(count, more=False):
	"""The sentence that says how many notifications are unread."""
	if count == 0:
		return _("You have no unread GitHub notifications.")
	if count == 1 and not more:
		return _("You have 1 unread GitHub notification.")
	if more:
		return _("You have {count} or more unread GitHub notifications.").format(count=count)
	return _("You have {count} unread GitHub notifications.").format(count=count)


def announce_unread():
	"""Speak how many unread notifications there are (used by a script)."""

	def done(res):
		if hasattr(res, "ok"):
			say(_("Could not check notifications.") + " " + res.message)
			return
		say(unread_message(*res))

	run_async(count_unread, done)


def open_notification(dialog, n):
	"""Open the thing a notification is about."""
	subject = n.get("subject") or {}
	number = subject_number(subject.get("url"))
	full = (n.get("repository") or {}).get("full_name") or repo_from_api_url(subject.get("url"))
	if number and subject.get("type") in ("Issue", "PullRequest") and full:
		show_conversation(dialog, full, number, subject.get("type") == "PullRequest", n.get("id"))
		dialog.reload()
	elif subject.get("type") == "Commit" and full and subject.get("url"):
		from .repotools import show_commit
		show_commit(dialog, full, subject["url"].rsplit("/", 1)[-1])
	else:
		open_url(html_url(n))


def show_notifications(parent):
	def fetch(page, selections):
		params = {"per_page": items_per_page(), "page": page}
		if selections[0] == 1:
			params["all"] = "true"
		elif selections[0] == 2:
			params["participating"] = "true"  # only threads you are directly involved in
		return gh("GET", "/notifications", params=params)

	def mark_read(dlg, n):
		def done(res):
			if res.ok:
				ui.message(_("Marked as read."))
				dlg.reload()
			else:
				ui.message(_("Could not mark as read.") + " " + res.message)

		dlg.run(lambda: gh("PATCH", f"/notifications/threads/{n['id']}"), done)

	def mark_done(dlg, n):
		def done(res):
			if res.ok:
				ui.message(_("Marked as done."))
				dlg.reload()
			else:
				ui.message(_("Could not mark as done.") + " " + res.message)

		dlg.run(lambda: gh("DELETE", f"/notifications/threads/{n['id']}"), done)

	def mute(dlg, n):
		def done(res):
			if res.ok:
				ui.message(_("Muted. You will not be notified about this thread again."))
				dlg.reload()
			else:
				ui.message(_("Could not mute it.") + " " + res.message)

		def work():
			gh("PUT", f"/notifications/threads/{n['id']}/subscription", body={"ignored": True})
			return gh("DELETE", f"/notifications/threads/{n['id']}")

		dlg.run(work, done)

	def mark_all(dlg, _item):
		if not confirm(dlg, _("Mark all notifications as read?"), _("Mark All Read")):
			return
		now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

		def done(res):
			if res.ok:
				ui.message(_("All notifications marked as read."))
				dlg.reload()
			else:
				ui.message(_("Could not mark all as read.") + " " + res.message)

		dlg.run(lambda: gh("PUT", "/notifications", body={"last_read_at": now}), done)

	actions = [
		(_("&View"), open_notification),
		(_("Mark as &Read"), mark_read),
		(_("Mark as &Done"), mark_done),
		(_("&Mute Thread"), mute),
		(_("Mark &All as Read"), mark_all, False),
		(_("Open in &Browser"), lambda d, n: open_url(html_url(n))),
		(_("Copy Lin&k"), lambda d, n: copy_to_clipboard(html_url(n), _("Link copied."))),
	]
	show_modal(PagedListDialog(
		parent, _("Notifications"), fetch, notification_label, actions, empty=_("No notifications."),
		choices=[(_("&Show:"), [_("Unread only"), _("All"), _("Unread, involving me directly")])],
	))


# ----------------------------------------------------------------------
# monitor.py: Background check for new notifications (off by default, set in Settings).
# ----------------------------------------------------------------------

_FIRST_DELAY_SECONDS = 1  # first check right after start, so the baseline is set at once
_FAST_SECONDS = 60  # below this many seconds between checks, GitHub's poll hint is not applied (see _interval_seconds)

# Reasons that mean the notification is about you personally, for the "only ones involving me" filter.
DIRECT_REASONS = frozenset({
	"assign", "author", "mention", "review_requested", "team_mention", "approval_requested", "security_alert",
})


def summarize(items, details):
	"""Sentence announcing new notifications. items: notification payloads."""
	count = len(items)
	if count == 0:
		return ""
	if details and count <= 3:
		parts = []
		for n in items:
			subject = n.get("subject") or {}
			repo = (n.get("repository") or {}).get("name") or repo_from_api_url(subject.get("url"))
			parts.append(f"{repo}: {subject.get('title', '')}")
		return _("New GitHub notification: {details}").format(details="; ".join(parts))
	if count == 1:
		return _("1 new GitHub notification")
	return _("{count} new GitHub notifications").format(count=count)


def newer_than(items, last_seen):
	"""Notifications updated after last_seen (an ISO string), oldest first."""
	fresh = [n for n in items if (n.get("updated_at") or "") > last_seen]
	return sorted(fresh, key=lambda n: n.get("updated_at") or "")


def new_since(items, seen):
	"""Notifications that are new or were updated since `seen` ({thread id: updated_at}), oldest first.

	Comparing each thread's own timestamp (instead of one "latest" time) also catches two updates that
	arrive within the same second.
	"""
	fresh = [n for n in items if n.get("id") is not None and seen.get(n["id"]) != n.get("updated_at")]
	return sorted(fresh, key=lambda n: n.get("updated_at") or "")


def matches_filter(notification, mode):
	"""True when the notification should be announced under the chosen filter."""
	return mode != NOTIFICATION_FILTER_DIRECT or notification.get("reason") in DIRECT_REASONS


class NotificationMonitor:
	"""Polls GitHub on a timer. Uses conditional requests, so a quiet inbox costs no API quota."""

	def __init__(self):
		self._timer = None
		self._seen = {}  # thread id -> updated_at of the last answer
		self._primed = False  # False until the first answer has set the baseline
		self._lock = threading.Lock()
		self._failures = 0
		self._etag = None
		self._stopped = True
		self._generation = 0
		self._epoch = 0  # bumped by reset(), so a request that started before it is ignored
		self._poll_hint = 0  # seconds GitHub asks us to wait between checks (X-Poll-Interval)
		self._warned = False  # the "cannot read notifications" problem was already spoken
		self.paused = False  # announcements are held back (checks go on, so nothing is announced late)

	def reset(self):
		"""Forget what was seen. Call when the token changes, so another account starts fresh."""
		with self._lock:
			self._epoch += 1
			self._seen = {}
			self._primed = False
			self._etag = None
			self._failures = 0
			self._poll_hint = 0
			self._warned = False

	def start(self):
		"""(Re)start according to the settings. Safe to call at any time."""
		self.stop()
		if notification_seconds() <= 0 or not has_token():
			return  # off, or nothing to check with: no timer keeps running for nothing
		self._stopped = False
		self._generation += 1
		self._schedule(_FIRST_DELAY_SECONDS, self._generation)

	def stop(self):
		self._stopped = True
		self._generation += 1
		timer, self._timer = self._timer, None
		if timer is not None:
			try:
				timer.cancel()
			except Exception:
				log.debugWarning("GitHub for NVDA: stopping the monitor timer failed", exc_info=True)

	@property
	def running(self):
		return not self._stopped

	def toggle_pause(self):
		"""Hold back or release the spoken announcements. Returns True when they are now paused."""
		self.paused = not self.paused
		return self.paused

	def _schedule(self, seconds, generation):
		if self._stopped or generation != self._generation:
			return
		timer = threading.Timer(seconds, self._tick, args=(generation,))
		timer.daemon = True
		self._timer = timer
		timer.start()

	def _interval_seconds(self):
		base = max(5, notification_seconds())
		# GitHub asks clients to wait at least X-Poll-Interval (normally 60 seconds) between checks. The
		# fast 15 and 30 second choices ignore that hint on purpose: every check that finds nothing new is
		# answered with "304 Not Modified", which does not count against the API rate limit.
		if base >= _FAST_SECONDS:
			base = max(base, self._poll_hint)
		# One failed check (a timeout, a hiccup) must not delay the next one. Back off only when
		# checks keep failing, and never by more than 8 times.
		backoff = 2 ** min(max(self._failures - 1, 0), 3)
		return min(base * backoff, 3600)

	def _tick(self, generation):
		if self._stopped or generation != self._generation:
			return
		try:
			self.check()
		except Exception:
			log.error("GitHub for NVDA: notification check failed", exc_info=True)
		finally:
			self._schedule(self._interval_seconds(), generation)

	def check_now(self):
		"""Check at once, from any thread's point of view, and always say what was found."""
		run_async(lambda: self.check(manual=True))

	def check(self, manual=False):
		"""One blocking check. Runs on a worker or the timer thread.

		manual: the person asked for this check, so the answer is spoken even when announcements are
		paused, and "nothing new" is spoken too.

		The lock only guards the small bits of state, never the network request: reset() and
		start() are called from NVDA's main thread and must not wait for a slow request.
		"""
		if not has_token():
			if manual:
				say(_("Set your GitHub token first."))
			return
		with self._lock:
			generation, epoch = self._generation, self._epoch
			etag = None if manual else self._etag  # a manual check always asks for the full list
		headers = {"If-None-Match": etag} if etag else None
		res = gh("GET", "/notifications", params={"per_page": 50}, headers=headers, retries=0, timeout=12)
		problem = None
		with self._lock:
			if not manual and (self._stopped or generation != self._generation):
				return  # stopped or restarted while the request ran: the answer is out of date
			if epoch != self._epoch:
				return  # the token changed while the request ran
			try:
				self._poll_hint = max(0, int((res.headers or {}).get("X-Poll-Interval") or 0))
			except (TypeError, ValueError):
				self._poll_hint = 0
			if res.code == 304:
				self._failures = 0
				return
			if not res.ok or not isinstance(res.data, list):
				limited = (
					res.code == 429 or str((res.headers or {}).get("X-RateLimit-Remaining", "")) == "0"
					or bool((res.headers or {}).get("Retry-After"))
				)
				if res.code in (401, 403, 404) and not limited:
					self._failures = 5  # token is bad or lacks the permission: check rarely until it changes
					if not self._warned or manual:
						self._warned = True
						problem = (
							_("GitHub for NVDA cannot read your notifications. Check that your token has the notifications permission.")
							if res.code != 401 else _("GitHub rejected your token, so notifications cannot be checked.")
						)
				else:
					self._failures += 1
					if manual:
						problem = _("Could not check notifications.") + " " + res.message
			else:
				self._failures = 0
				self._warned = False
				self._etag = (res.headers or {}).get("ETag")
				was_primed = self._primed
				fresh = new_since(res.data, self._seen) if was_primed else []
				self._seen = {n["id"]: n.get("updated_at") for n in res.data if n.get("id") is not None}
				self._primed = True
		if problem:
			say(problem)
			return
		if not isinstance(res.data, list) or not res.ok:
			return
		mode = get_pref("notificationFilter")
		wanted = [n for n in fresh if matches_filter(n, mode)]
		if manual:
			# Speak at once from this thread (say() is thread safe) instead of hopping through the GUI loop.
			if not was_primed:
				say(unread_message(len(res.data), res.has_next))
			elif wanted:
				self._announce(wanted, force=True)
			else:
				say(_("No new GitHub notifications.") + " " + unread_message(len(res.data), res.has_next))
			return
		if was_primed and wanted:
			self._announce(wanted)

	def _announce(self, fresh, force=False):
		if self._stopped and not force:
			return
		if self.paused and not force:
			return
		if get_pref("notificationBeep"):
			try:
				tones.beep(880, 80)
			except Exception:
				log.debugWarning("GitHub for NVDA: beep failed", exc_info=True)
		say(summarize(fresh, get_pref("notificationDetails")))


monitor = NotificationMonitor()


# ----------------------------------------------------------------------
# tokendialog.py: Helpers for the personal access token. The token itself is set in NVDA Settings > GitHub for NVDA.
# ----------------------------------------------------------------------

def recommended_scopes():
	return {
		"repo": _("repositories, issues and pull requests"),
		"notifications": _("notifications"),
		"gist": _("gists"),
		"workflow": _("re-running workflows"),
		"delete_repo": _("deleting repositories"),
		"user:email": _("changing email visibility"),
		"user": _("profile settings, blocking users and public organization membership"),
		"write:public_key": _("SSH keys"),
		"write:gpg_key": _("GPG keys"),
		"admin:ssh_signing_key": _("SSH signing keys"),
		"codespace:secrets": _("Codespaces secrets"),
	}


def missing_scopes(scopes_header):
	"""Names of recommended scopes a classic token lacks. None if the token reports no scopes."""
	if scopes_header is None:
		return None
	have = {x.strip() for x in scopes_header.split(",") if x.strip()}
	if "user" in have:
		have.add("user:email")  # the broader "user" scope includes it
	for broad, narrow in (("admin:public_key", "write:public_key"), ("admin:gpg_key", "write:gpg_key")):
		if broad in have:
			have.add(narrow)
	return [f"{k} ({v})" for k, v in recommended_scopes().items() if k not in have]


def require_token(parent=None):
	"""Return the token, or tell the user to set one and return ''."""
	token = load_token()
	if not token:
		inform(
			parent, _("Please set your GitHub Personal Access Token first, in NVDA Settings under GitHub for NVDA."),
			_("Token Required"),
		)
	return token


# ----------------------------------------------------------------------
# settings.py: The GitHub for NVDA category in NVDA's Settings dialog.
# ----------------------------------------------------------------------

# GitHub's page for making a classic token, with the permissions this add-on needs already ticked.
NEW_TOKEN_PAGE = (
	"https://github.com/settings/tokens/new?description=GitHub%20for%20NVDA"
	"&scopes=repo,notifications,gist,workflow,user,write:public_key,write:gpg_key,admin:ssh_signing_key,codespace:secrets"
)


def interval_label(seconds):
	if seconds == 0:
		return _("Off")
	if seconds < 60:
		return _("Every {seconds} seconds").format(seconds=seconds)
	if seconds == 60:
		return _("Every minute")
	if seconds == 3600:
		return _("Every hour")
	return _("Every {minutes} minutes").format(minutes=seconds // 60)


def nearest_interval(seconds):
	"""The choice in NOTIFICATION_INTERVALS closest to a saved value, never Off for a value above zero."""
	if seconds <= 0:
		return NOTIFICATION_INTERVALS[0]
	return min(NOTIFICATION_INTERVALS[1:], key=lambda choice: abs(choice - seconds))


class GitHubSettingsPanel(SettingsPanel):
	# Translators: the name of the settings category.
	title = _("GitHub for NVDA")

	def makeSettings(self, settingsSizer):
		helper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self._makeTokenControls(settingsSizer, helper)
		self.perPage = helper.addLabeledControl(
			_("Items shown on each &page of a list:"), wx.SpinCtrl, min=10, max=100, initial=items_per_page()
		)
		choices = [interval_label(m) for m in NOTIFICATION_INTERVALS]
		self.interval = helper.addLabeledControl(_("Check for new &notifications in the background:"), wx.Choice, choices=choices)
		self.interval.SetSelection(NOTIFICATION_INTERVALS.index(nearest_interval(notification_seconds())))
		self.notifyFilter = helper.addLabeledControl(
			_("Background announcements &filter:"), wx.Choice,
			choices=[_("All new notifications"), _("Only notifications that involve me directly")],
		)
		self.notifyFilter.SetSelection(
			NOTIFICATION_FILTER_DIRECT if get_pref("notificationFilter") == NOTIFICATION_FILTER_DIRECT
			else NOTIFICATION_FILTER_ALL
		)
		self.details = helper.addItem(wx.CheckBox(self, label=_("Say what the new notifications are &about")))
		self.details.SetValue(bool(get_pref("notificationDetails")))
		self.beep = helper.addItem(wx.CheckBox(self, label=_("&Beep when there are new notifications")))
		self.beep.SetValue(bool(get_pref("notificationBeep")))
		self.markRead = helper.addItem(wx.CheckBox(self, label=_("&Mark a notification as read when I open it")))
		self.markRead.SetValue(bool(get_pref("markReadOnView")))
		self.wrap = helper.addItem(wx.CheckBox(self, label=_("&Wrap long lines when viewing files")))
		self.wrap.SetValue(bool(get_pref("wrapFileText")))
		self.folder = helper.addLabeledControl(_("&Download folder (empty means your Downloads folder):"), wx.TextCtrl)
		self.folder.SetValue(str(get_pref("downloadFolder") or ""))
		browse = wx.Button(self, label=_("&Browse for folder..."))
		browse.Bind(wx.EVT_BUTTON, self.onBrowse)
		helper.addItem(browse)

	def _makeTokenControls(self, settingsSizer, helper):
		"""The personal access token: its text box, a check box that shows it, and a button that tests it.

		The box hides the token (nothing is read aloud) until "Show personal access token" is checked.
		A text box cannot change between hidden and shown, so there are two boxes that take turns."""
		self._savedToken = load_token()
		token = self._savedToken
		self.tokenLabel = wx.StaticText(self, label=_("Paste your personal access to&ken:"))
		self.tokenHidden = wx.TextCtrl(self, value=token, style=wx.TE_PASSWORD)
		self.tokenShown = wx.TextCtrl(self, value=token)
		self.tokenHidden.SetMinSize((360, -1))
		self.tokenShown.SetMinSize((360, -1))
		self.tokenShown.Hide()
		self.tokenHidden.Bind(wx.EVT_TEXT, self._onTokenText)
		self.tokenShown.Bind(wx.EVT_TEXT, self._onTokenText)
		helper.addItem(self.tokenLabel)
		self._tokenBoxes = wx.BoxSizer(wx.VERTICAL)
		self._tokenBoxes.Add(self.tokenHidden, 0, wx.EXPAND)
		self._tokenBoxes.Add(self.tokenShown, 0, wx.EXPAND)
		self._tokenBoxes.Hide(self.tokenShown)
		helper.addItem(self._tokenBoxes)
		self.showToken = helper.addItem(wx.CheckBox(self, label=_("&Show personal access token")))
		self.showToken.Bind(wx.EVT_CHECKBOX, self.onShowToken)
		self.testToken = wx.Button(self, label=_("&Test personal access token"))
		self.testToken.Bind(wx.EVT_BUTTON, self.onTestToken)
		helper.addItem(self.testToken)
		self.createToken = wx.Button(self, label=_("&Create new personal access token"))
		self.createToken.Bind(wx.EVT_BUTTON, lambda e: open_url(NEW_TOKEN_PAGE))
		helper.addItem(self.createToken)

	def _currentTokenBox(self):
		return self.tokenShown if self.showToken.GetValue() else self.tokenHidden

	def _tokenValue(self):
		return self._currentTokenBox().GetValue().strip()

	def _onTokenText(self, event):
		# Keep the two boxes the same, whichever one is being typed in.
		source = event.GetEventObject()
		other = self.tokenShown if source is self.tokenHidden else self.tokenHidden
		if other.GetValue() != source.GetValue():
			other.ChangeValue(source.GetValue())
		event.Skip()

	def onShowToken(self, event):
		show = self.showToken.GetValue()
		old, new = (self.tokenHidden, self.tokenShown) if show else (self.tokenShown, self.tokenHidden)
		new.ChangeValue(old.GetValue())
		self._tokenBoxes.Hide(old)
		self._tokenBoxes.Show(new)
		self._tokenBoxes.Layout()
		self.Layout()
		# The focus stays on the check box: ticking it must not move the cursor anywhere else.

	def onTestToken(self, event):
		"""Ask GitHub whether the token in the box works. The token is not saved by this."""
		token = self._tokenValue()
		if not token:
			ui.message(_("Paste your personal access token first."))
			self._currentTokenBox().SetFocus()
			return
		ui.message(_("Testing the token..."))
		self.testToken.Disable()

		def work():
			res = gh("GET", "/user", auth=False, headers={"Authorization": f"Bearer {token}"}, retries=1)
			scopes = (res.headers or {}).get("X-OAuth-Scopes")
			return Result(res.code, (res.data, scopes), res.headers)

		def done(res):
			try:
				self.testToken.Enable()
			except RuntimeError:
				return  # the settings window was closed while GitHub was answering
			data, scopes = res.data if res.ok else (None, None)
			if res.ok and isinstance(data, dict):
				message = _("The token works. It belongs to {name}.").format(name=data.get("login", ""))
				missing = missing_scopes(scopes)
				if missing:
					message += "\n\n" + _("This token does not include these optional permissions, so some features may be unavailable: {list}.").format(list=", ".join(missing))
				message += "\n\n" + _("Press OK or Apply in Settings to save it.") if token != self._savedToken else ""
			elif res.code == 0:
				message = _("Could not reach GitHub. Check your internet connection.")
			elif res.code == 401:
				message = _("GitHub rejected this token. Please check it and try again.")
			else:
				message = _("The token test failed.") + " " + res.message
			wx.MessageBox(message, _("Test personal access token"), wx.OK | wx.ICON_INFORMATION, self)
			try:
				self.testToken.SetFocus()
			except RuntimeError:
				pass

		run_async(work, done)

	def onBrowse(self, event):
		dialog = wx.DirDialog(self, _("Choose the download folder"), defaultPath=self.folder.GetValue() or "")
		try:
			if dialog.ShowModal() == wx.ID_OK:
				self.folder.SetValue(dialog.GetPath())
		finally:
			dialog.Destroy()

	def _saveToken(self):
		"""Save, replace or (when the box is empty) remove the token."""
		token = self._tokenValue()
		if token == self._savedToken:
			return
		if token:
			if not save_token(token):
				ui.message(_("The token could not be saved. See the NVDA log for details."))
				return
		else:
			if not delete_token():
				ui.message(_("The token could not be deleted. See the NVDA log for details."))
				return
			monitor.stop()
			ui.message(_("Token deleted."))
		self._savedToken = token
		forget_identity()
		monitor.reset()  # a new token may belong to another account

	def onSave(self):
		self._saveToken()
		set_pref("itemsPerPage", self.perPage.GetValue())
		set_pref("notificationSeconds", NOTIFICATION_INTERVALS[max(0, self.interval.GetSelection())])
		set_pref("notificationInterval", 0)  # the old setting, in minutes, is replaced by notificationSeconds
		set_pref("notificationFilter", max(0, self.notifyFilter.GetSelection()))
		set_pref("notificationDetails", self.details.GetValue())
		set_pref("notificationBeep", self.beep.GetValue())
		set_pref("markReadOnView", self.markRead.GetValue())
		set_pref("wrapFileText", self.wrap.GetValue())
		set_pref("downloadFolder", self.folder.GetValue().strip())
		monitor.start()
