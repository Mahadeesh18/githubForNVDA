# GitHub for NVDA
# Copyright (C) 2025-2026 Mahadeesh and contributors
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for more details.

"""Notifications, the background monitor, and the add-on's category in NVDA's Settings dialog."""

import addonHandler
import datetime
import gui
import nvwave
import os
import re
import threading
import time
import ui
import wx
from gui import guiHelper
from gui.settingsDialogs import SettingsPanel
from logHandler import log

from .core import (
	AccessibleDialog,
	ask_form,
	config_dir,
	confirm,
	confirm_typed,
	copy_to_clipboard,
	delete_token,
	escape_label,
	forget_identity,
	format_date,
	get_clipboard_text,
	get_pref,
	gh,
	has_token,
	inform,
	items_per_page,
	load_token,
	NOTIFICATION_FILTER_ALL,
	NOTIFICATION_FILTER_DIRECT,
	NOTIFICATION_INTERVALS,
	notification_seconds,
	open_url,
	PAD,
	PagedListDialog,
	parse_github_link,
	prompt_text,
	repo_from_api_url,
	Result,
	run_async,
	save_token,
	say,
	set_pref,
	show_modal,
	TextViewDialog,
)
from .repos import (
	open_link,
	show_bookmarks,
	show_my_repos,
)
from .conversation import (
	show_conversation,
	show_my_work,
)
from .profile import (
	show_profile,
	show_settings_menu,
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
		from .repos import show_commit
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
				dlg.forget(n)
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


SOUNDS_DIR = os.path.join(os.path.dirname(__file__), "sounds")
NOTIFICATION_SOUND = os.path.join(SOUNDS_DIR, "newNotification.wav")

# The sounds that can be chosen in Settings: (saved name, file inside the sounds folder or None).
SOUND_DEFAULT, SOUND_SPARE, SOUND_CUSTOM = "default", "spare", "custom"
SOUND_CHOICES = (
	(SOUND_DEFAULT, "newNotification.wav"),
	(SOUND_SPARE, "spare.wav"),
	(SOUND_CUSTOM, None),
)


def sound_choice_labels():
	return [
		_("Default notification sound"),
		_("Alternative notification sound"),
		_("My own sound file (WAV)"),
	]


def usable_sound_file(path):
	"""True when path is an existing .wav file."""
	path = (path or "").strip()
	return bool(path) and path.lower().endswith(".wav") and os.path.isfile(path)


def sound_path(choice=None, custom_file=None):
	"""The file to play. A missing or unusable custom file falls back to the default sound."""
	choice = get_pref("notificationSound") if choice is None else choice
	custom_file = get_pref("notificationSoundFile") if custom_file is None else custom_file
	if choice == SOUND_CUSTOM and usable_sound_file(custom_file):
		return custom_file.strip()
	for name, filename in SOUND_CHOICES:
		if name == choice and filename:
			candidate = os.path.join(SOUNDS_DIR, filename)
			if os.path.isfile(candidate):
				return candidate
	return NOTIFICATION_SOUND


def play_notification_sound(choice=None, custom_file=None):
	"""Play the new-notification sound chosen in Settings, without blocking."""
	path = sound_path(choice, custom_file)
	try:
		nvwave.playWaveFile(path, asynchronous=True)
	except Exception:
		log.debugWarning("GitHub for NVDA: notification sound failed", exc_info=True)
		if path != NOTIFICATION_SOUND:
			try:
				nvwave.playWaveFile(NOTIFICATION_SOUND, asynchronous=True)
			except Exception:
				log.debugWarning("GitHub for NVDA: default notification sound failed", exc_info=True)


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
			play_notification_sound()
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
		self.beep = helper.addItem(wx.CheckBox(self, label=_("&Play a sound when there are new notifications")))
		self.beep.SetValue(bool(get_pref("notificationBeep")))
		self.beep.Bind(wx.EVT_CHECKBOX, lambda e: self._updateSoundControls())
		self.sound = helper.addLabeledControl(
			_("Notification so&und:"), wx.Choice, choices=sound_choice_labels()
		)
		saved = get_pref("notificationSound")
		self.sound.SetSelection(next((i for i, (name, f) in enumerate(SOUND_CHOICES) if name == saved), 0))
		self.sound.Bind(wx.EVT_CHOICE, lambda e: self._updateSoundControls())
		self.soundFile = helper.addLabeledControl(_("Your own sound fi&le path:"), wx.TextCtrl)
		self.soundFile.SetValue(str(get_pref("notificationSoundFile") or ""))
		self.soundBrowse = wx.Button(self, label=_("Ch&oose sound file..."))
		self.soundBrowse.Bind(wx.EVT_BUTTON, self.onBrowseSound)
		helper.addItem(self.soundBrowse)
		self.soundPreview = wx.Button(self, label=_("Pre&view the sound"))
		self.soundPreview.Bind(wx.EVT_BUTTON, self.onPreviewSound)
		helper.addItem(self.soundPreview)
		self._updateSoundControls()
		self.markRead = helper.addItem(wx.CheckBox(self, label=_("&Mark a notification as read when I open it")))
		self.markRead.SetValue(bool(get_pref("markReadOnView")))
		self.wrap = helper.addItem(wx.CheckBox(self, label=_("&Wrap long lines when viewing files")))
		self.wrap.SetValue(bool(get_pref("wrapFileText")))
		self.askCommit = helper.addItem(wx.CheckBox(self, label=_("&Ask me for a commit message when I change files")))
		self.askCommit.SetValue(bool(get_pref("askCommitMessage")))
		self.uploadBeep = helper.addItem(wx.CheckBox(self, label=_("Play a &rising beep while files upload")))
		self.uploadBeep.SetValue(bool(get_pref("uploadProgressBeep")))
		self.folder = helper.addLabeledControl(_("&Download folder (empty means your Downloads folder):"), wx.TextCtrl)
		self.folder.SetValue(str(get_pref("downloadFolder") or ""))
		browse = wx.Button(self, label=_("&Browse for folder..."))
		browse.Bind(wx.EVT_BUTTON, self.onBrowse)
		helper.addItem(browse)

	def _soundChoice(self):
		return SOUND_CHOICES[max(0, self.sound.GetSelection())][0]

	def _updateSoundControls(self):
		"""The sound controls only matter when the sound is on, and the file box only for your own file."""
		on = self.beep.GetValue()
		custom = on and self._soundChoice() == SOUND_CUSTOM
		self.sound.Enable(on)
		self.soundFile.Enable(custom)
		self.soundBrowse.Enable(custom)
		self.soundPreview.Enable(on)

	def onBrowseSound(self, event):
		current = self.soundFile.GetValue().strip()
		dialog = wx.FileDialog(
			self, _("Choose a notification sound"),
			defaultDir=os.path.dirname(current) if current and os.path.isdir(os.path.dirname(current)) else "",
			wildcard=_("WAV sound files (*.wav)") + "|*.wav", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
		)
		try:
			if dialog.ShowModal() == wx.ID_OK:
				self.soundFile.SetValue(dialog.GetPath())
				play_notification_sound(SOUND_CUSTOM, dialog.GetPath())
		finally:
			dialog.Destroy()

	def onPreviewSound(self, event):
		choice = self._soundChoice()
		custom = self.soundFile.GetValue()
		if choice == SOUND_CUSTOM and not usable_sound_file(custom):
			ui.message(_("That sound file was not found or is not a WAV file, so the default sound plays."))
		play_notification_sound(choice, custom)

	def isValid(self):
		"""Do not save a custom sound that cannot be played."""
		if self.beep.GetValue() and self._soundChoice() == SOUND_CUSTOM and not usable_sound_file(self.soundFile.GetValue()):
			wx.MessageBox(
				_("Your own sound file was not found, or it is not a WAV file. Choose an existing .wav file, or pick another sound."),
				_("Notification sound"), wx.OK | wx.ICON_WARNING, self,
			)
			self.soundFile.SetFocus()
			return False
		return True

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
		set_pref("notificationSeconds", NOTIFICATION_INTERVALS[max(0, self.interval.GetSelection())])
		set_pref("notificationInterval", 0)  # the old setting, in minutes, is replaced by notificationSeconds
		set_pref("notificationFilter", max(0, self.notifyFilter.GetSelection()))
		set_pref("notificationDetails", self.details.GetValue())
		set_pref("notificationBeep", self.beep.GetValue())
		set_pref("notificationSound", self._soundChoice())
		set_pref("notificationSoundFile", self.soundFile.GetValue().strip())
		set_pref("markReadOnView", self.markRead.GetValue())
		set_pref("wrapFileText", self.wrap.GetValue())
		set_pref("askCommitMessage", self.askCommit.GetValue())
		set_pref("uploadProgressBeep", self.uploadBeep.GetValue())
		set_pref("downloadFolder", self.folder.GetValue().strip())
		monitor.start()


# ----------------------------------------------------------------------
# explore.py: Search Public Repositories: search repositories, users, issues and code (works without a token,
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
		super().__init__(parent, _("Search Public Repositories"), (760, 620))
		self.results, self.total, self.kind = [], 0, 0
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
		self.query.Bind(wx.EVT_TEXT_ENTER, lambda e: self.search())
		sizer.Add(self.query, 0, wx.EXPAND | wx.ALL, PAD)
		search_row = wx.BoxSizer(wx.HORIZONTAL)
		self.add_button(search_row, _("Searc&h"), lambda e: self.search())
		sizer.Add(search_row, 0, wx.LEFT, PAD)
		sizer.Add(wx.StaticText(self, label=_("&Results:")), 0, wx.LEFT | wx.TOP, PAD)
		self.listbox = wx.ListBox(self)
		self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, self.on_open)
		self.listbox.Bind(wx.EVT_KEY_DOWN, self.on_key)
		self.listbox.enter_action = lambda: self.on_open(None)
		sizer.Add(self.listbox, 1, wx.EXPAND | wx.ALL, PAD)
		row = wx.WrapSizer(wx.HORIZONTAL)
		row.Add(self.close_button(), 0, wx.ALL, 3)
		sizer.Add(row, 0, wx.ALIGN_CENTER)
		self.layout(sizer, focus=self.query)
		self._search_id = 0
		if initial_query:
			wx.CallAfter(self.search)

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

	def search(self, page=1):
		"""Search and show every result (up to SEARCH_MAX_RESULTS) in one list. `page` is ignored."""
		query = self.query.GetValue().strip()
		if not query:
			ui.message(_("Type something to search for."))
			self.query.SetFocus()
			return
		from .repos import open_link
		if parse_github_link(query) and open_link(self, query):
			return
		kind = self.kind_choice.GetSelection()
		if kind == 3 and not has_token():
			ui.message(_("GitHub only allows code search when you are signed in. Set your token first."))
			return
		endpoint = ["/search/repositories", "/search/users", "/search/issues", "/search/code"][kind]
		options = sorts(kind)
		sort = options[max(0, min(self.sort.GetSelection(), len(options) - 1))][1]
		params = {"q": query, "per_page": 100}
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
			self.kind, self.results = kind, items
			self.total = res.data.get("total_count", 0)
			self.listbox.Set([self._label(r) for r in items])
			if items:
				self.listbox.SetSelection(0)
				self.listbox.SetFocus()
				if self.total > len(items):
					ui.message(_("{count} of {total} results").format(count=len(items), total=self.total))
				else:
					ui.message(_("{count} results").format(count=len(items)))
			else:
				ui.message(_("Nothing found."))

		def one(number):
			"""One page of results: Result(data={"items", "total", "more"})."""
			if kind == 2:
				from .conversation import search_issues
				res = search_issues(query, number, 100, sort)
				if not res.ok:
					return res
				return Result(200, {"items": res.data, "total": None, "more": res.has_next}, {})
			res = gh("GET", endpoint, params=dict(params, page=number))
			if not res.ok or not isinstance(res.data, dict):
				return res
			total = res.data.get("total_count", 0)
			return Result(200, {"items": res.data.get("items", []), "total": total,
				"more": number * 100 < min(total, SEARCH_MAX_RESULTS)}, {})

		def work():
			items, total, number = [], None, 1
			while True:
				res = one(number)
				if not res.ok or not isinstance(res.data, dict):
					if items:
						break  # keep what was found so far
					return res
				got = res.data["items"]
				items.extend(got)
				if res.data["total"] is not None:
					total = res.data["total"]
				if not got or not res.data["more"] or len(items) >= SEARCH_MAX_RESULTS:
					break
				number += 1
			items = items[:SEARCH_MAX_RESULTS]
			return Result(200, {"items": items, "total_count": total if total is not None else len(items)}, {})

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
				dlg.forget(g)
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
# mainmenu.py: The main menu window.
# ----------------------------------------------------------------------

def user_guide_path():
	"""The user guide, doc/en/readme.html inside the add-on. It opens in the browser."""
	path = addonHandler.getCodeAddon().path
	for language in ("en",):
		candidate = os.path.join(path, "doc", language, "readme.html")
		if os.path.isfile(candidate):
			return candidate
	return ""


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
			(_("Search Public Repositories"), lambda: show_explore(self)),
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
		# The options are one list: Up/Down to move, Enter to open. Exit (Alt+E) is a separate button after it (Tab).
		self.listbox = wx.ListBox(self, choices=[label for label, _action in entries], size=(400, 300))
		self.listbox.SetSelection(0)
		self.listbox.enter_action = self.choose
		self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.choose())
		sizer.Add(self.listbox, 1, wx.EXPAND | wx.ALL, 8)
		sizer.Add(self.close_button(_("&Exit")), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
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
