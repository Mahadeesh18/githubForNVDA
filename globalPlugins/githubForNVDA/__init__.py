# GitHub for NVDA
# Copyright (C) 2025-2026 Mahadeesh and contributors
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for more details.

"""GitHub for NVDA: manage GitHub with your screen reader."""

import time

import addonHandler
import globalPluginHandler
import gui
import scriptHandler
import ui
import wx
from gui.settingsDialogs import NVDASettingsDialog
from logHandler import log

addonHandler.initTranslation()

from .core import close_session, has_token, register_prefs, shutdown  # noqa: E402
from .logic import parse_github_link  # noqa: E402


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	# Translators: the category of this add-on's commands in the Input gestures dialog.
	scriptCategory = _("GitHub for NVDA")

	def __init__(self):
		super().__init__()
		self._open = False
		self._quiet_until = 0.0
		register_prefs()
		from .notify import GitHubSettingsPanel
		NVDASettingsDialog.categoryClasses.append(GitHubSettingsPanel)
		self._panel = GitHubSettingsPanel
		from .notify import monitor
		self._monitor = monitor
		monitor.start()

	def terminate(self):
		try:
			self._monitor.stop()
			if self._panel in NVDASettingsDialog.categoryClasses:
				NVDASettingsDialog.categoryClasses.remove(self._panel)
			shutdown()
			close_session()
		except Exception:
			log.error("GitHub for NVDA: error while shutting down", exc_info=True)
		super().terminate()

	def _guard(self, function):
		"""Only one add-on window tree at a time."""
		from .dialogs import open_window_count
		if self._open or open_window_count() or time.monotonic() < self._quiet_until:
			ui.message(_("GitHub for NVDA is already open."))
			return
		self._open = True
		started = time.monotonic()
		try:
			function()
			# Some openers look something up first and show their window a moment later, so they
			# return at once. Block a second press only for those: after a window that was open
			# for a while and then closed, the command must work again immediately.
			if time.monotonic() - started < 1.0:
				self._quiet_until = time.monotonic() + 2.0
		except Exception:
			log.error("GitHub for NVDA: window failed", exc_info=True)
			ui.message(_("Something went wrong. See the NVDA log for details."))
		finally:
			self._open = False

	@scriptHandler.script(
		# Translators: input help for the command that opens the main menu.
		description=_("Opens the GitHub for NVDA main menu"),
	)
	def script_openMainMenu(self, gesture):
		from .explore import open_main_dialog
		wx.CallAfter(self._guard, open_main_dialog)

	@scriptHandler.script(
		# Translators: input help.
		description=_("Announces how many unread GitHub notifications you have"),
	)
	def script_unreadNotifications(self, gesture):
		if not has_token():
			ui.message(_("Set your GitHub token first."))
			return
		from .notify import announce_unread
		announce_unread()

	@scriptHandler.script(
		# Translators: input help for the command that checks for new notifications right now.
		description=_("Checks for new GitHub notifications now and says what is new"),
	)
	def script_checkNotificationsNow(self, gesture):
		if not has_token():
			ui.message(_("Set your GitHub token first."))
			return
		self._monitor.check_now()

	@scriptHandler.script(
		# Translators: input help for the command that pauses background notification announcements.
		description=_("Pauses or resumes the spoken announcements of new GitHub notifications"),
	)
	def script_togglePauseNotifications(self, gesture):
		if not self._monitor.running:
			ui.message(_("Background notification checks are off. Turn them on in NVDA Settings under GitHub for NVDA."))
			return
		if self._monitor.toggle_pause():
			ui.message(_("GitHub notification announcements paused."))
		else:
			ui.message(_("GitHub notification announcements resumed."))

	@scriptHandler.script(
		# Translators: input help.
		description=_("Opens your GitHub notifications"),
	)
	def script_openNotifications(self, gesture):
		if not has_token():
			ui.message(_("Set your GitHub token first."))
			return
		from .notify import show_notifications
		wx.CallAfter(self._guard, lambda: show_notifications(gui.mainFrame))

	@scriptHandler.script(
		# Translators: input help.
		description=_("Opens your GitHub work: items assigned to you and review requests"),
	)
	def script_openMyWork(self, gesture):
		if not has_token():
			ui.message(_("Set your GitHub token first."))
			return
		from .conversation import show_my_work
		wx.CallAfter(self._guard, lambda: show_my_work(gui.mainFrame))

	@scriptHandler.script(
		# Translators: input help.
		description=_("Opens the GitHub link or owner/repo that is on the clipboard"),
	)
	def script_openFromClipboard(self, gesture):
		from .dialogs import get_clipboard_text
		text = get_clipboard_text().strip()
		if not text or len(text) > 500 or parse_github_link(text) is None:
			ui.message(_("The clipboard does not hold a GitHub link or owner/repo."))
			return
		from .repos import open_link
		wx.CallAfter(self._guard, lambda: open_link(gui.mainFrame, text))
