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


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	# Translators: the category of this add-on's commands in the Input gestures dialog.
	scriptCategory = _("GitHub for NVDA")

	def __init__(self):
		super().__init__()
		self._open = False
		self._quiet_until = 0.0
		self._prefsItem = None
		register_prefs()
		from .notify import GitHubSettingsPanel
		NVDASettingsDialog.categoryClasses.append(GitHubSettingsPanel)
		self._panel = GitHubSettingsPanel
		from .notify import monitor
		self._monitor = monitor
		monitor.start()
		self._addPreferencesMenuItem()

	def _addPreferencesMenuItem(self):
		"""NVDA menu, Preferences, GitHub for NVDA: opens the add-on's main menu."""
		try:
			tray = gui.mainFrame.sysTrayIcon
			# Translators: the item in NVDA's Preferences menu that opens the add-on's main menu.
			self._prefsItem = tray.preferencesMenu.Append(wx.ID_ANY, _("GitHub for NVDA"))
			tray.Bind(wx.EVT_MENU, self._onPreferencesMenuItem, self._prefsItem)
		except Exception:
			self._prefsItem = None
			log.debugWarning("GitHub for NVDA: could not add the Preferences menu item", exc_info=True)

	def _removePreferencesMenuItem(self):
		if self._prefsItem is None:
			return
		try:
			tray = gui.mainFrame.sysTrayIcon
			tray.Unbind(wx.EVT_MENU, source=self._prefsItem)
			tray.preferencesMenu.Remove(self._prefsItem)
		except Exception:
			log.debugWarning("GitHub for NVDA: could not remove the Preferences menu item", exc_info=True)
		self._prefsItem = None

	def _onPreferencesMenuItem(self, event):
		wx.CallAfter(self._guard, self._open_main_menu)

	def terminate(self):
		try:
			self._removePreferencesMenuItem()
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
		from .core import open_window_count
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

	def _open_main_menu(self):
		"""After an install or update, show What's New first. OK, Don't show again and Escape all go on to the menu."""
		from .notify import open_main_dialog
		try:
			from .core import should_show, show_whats_new
			if should_show():
				show_whats_new(gui.mainFrame)
		except Exception:
			log.error("GitHub for NVDA: the What's New window failed", exc_info=True)
		open_main_dialog()

	@scriptHandler.script(
		# Translators: input help for the command that opens the main menu.
		description=_("Opens the GitHub for NVDA main menu"),
	)
	def script_openMainMenu(self, gesture):
		wx.CallAfter(self._guard, self._open_main_menu)

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
		# Translators: input help for the command that opens the What's New page.
		description=_("Opens the What's New page"),
	)
	def script_showWhatsNew(self, gesture):
		from .core import show_whats_new
		wx.CallAfter(self._guard, lambda: show_whats_new(gui.mainFrame))
