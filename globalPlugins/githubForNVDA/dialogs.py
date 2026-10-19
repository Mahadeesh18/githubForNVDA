# GitHub for NVDA
# Copyright (C) 2025-2026 Mahadeesh and contributors
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for more details.

"""Reusable accessible windows: base dialog, text viewer, forms and paged lists."""

import addonHandler
import api
import gui
import ui
import wx
from logHandler import log

from .logic import (
	escape_label,
	resolve_access_keys,
	strip_mnemonic,
)
from .core import (
	Result,
	items_per_page,
	run_async,
)

addonHandler.initTranslation()


# ----------------------------------------------------------------------
# dialogs.py: Reusable accessible windows.
# ----------------------------------------------------------------------

PAD = 5


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------

_open_modals = 0


def open_window_count():
	"""How many of the add-on's modal windows are showing right now."""
	return _open_modals


def show_modal(dialog):
	"""Show a dialog modally and always destroy it afterwards."""
	global _open_modals
	_open_modals += 1
	top_level = dialog.GetParent() is gui.mainFrame
	if top_level:
		gui.mainFrame.prePopup()  # lets NVDA keep focus handling right while the window is up
	try:
		return dialog.ShowModal()
	finally:
		_open_modals -= 1
		if top_level:
			gui.mainFrame.postPopup()
		dialog.Destroy()


def copy_to_clipboard(text, message=None):
	try:
		if api.copyToClip(str(text)) is False:
			ui.message(_("Could not copy to the clipboard."))
		else:
			ui.message(message or _("Copied to the clipboard."))
	except Exception as e:
		ui.message(_("Failed to copy: {error}").format(error=e))


def get_clipboard_text():
	"""Text currently on the clipboard, or an empty string."""
	try:
		text = api.getClipData()
		if isinstance(text, str):
			return text
	except Exception:
		log.debugWarning("GitHub for NVDA: NVDA clipboard read failed", exc_info=True)
	text = ""
	try:
		if wx.TheClipboard.Open():
			try:
				data = wx.TextDataObject()
				if wx.TheClipboard.GetData(data):
					text = data.GetText()
			finally:
				wx.TheClipboard.Close()
	except Exception:
		log.debugWarning("GitHub for NVDA: clipboard read failed", exc_info=True)
	return text


def confirm(parent, message, title=None, warning=False, default_no=False):
	style = wx.YES_NO | (wx.ICON_WARNING if warning else wx.ICON_QUESTION)
	if default_no:
		style |= wx.NO_DEFAULT
	return wx.MessageBox(message, title or _("Confirm"), style, parent) == wx.YES


def inform(parent, message, title=None):
	wx.MessageBox(message, title or _("GitHub for NVDA"), wx.OK | wx.ICON_INFORMATION, parent)


def ask_choice(parent, title, message, options, selection=0):
	"""Let the user pick one entry. Returns its index, or None if cancelled."""
	dlg = wx.SingleChoiceDialog(parent, message, title, list(options))
	try:
		if 0 <= selection < len(options):
			dlg.SetSelection(selection)
		if dlg.ShowModal() == wx.ID_OK:
			return dlg.GetSelection()
	finally:
		dlg.Destroy()
	return None


def ask_multi_choice(parent, title, message, options, selected=()):
	"""Let the user tick any number of entries. Returns a list of indexes or None."""
	dlg = wx.MultiChoiceDialog(parent, message, title, list(options))
	try:
		dlg.SetSelections([i for i in selected if 0 <= i < len(options)])
		if dlg.ShowModal() == wx.ID_OK:
			return list(dlg.GetSelections())
	finally:
		dlg.Destroy()
	return None


# ----------------------------------------------------------------------
# Access keys
# ----------------------------------------------------------------------

def _walk(window):
	for child in window.GetChildren():
		yield child
		yield from _walk(child)


def apply_access_keys(window):
	"""Make Alt+letter shortcuts unique across everything in a window."""
	kinds = (wx.Button, wx.StaticText, wx.CheckBox, wx.RadioButton)
	widgets = [w for w in _walk(window) if isinstance(w, kinds) and w.IsShown()]
	labels = [w.GetLabel() for w in widgets]
	for widget, old, new in zip(widgets, labels, resolve_access_keys(labels)):
		if old != new:
			widget.SetLabel(new)


def make_label(parent, text, wrap=0):
	"""A StaticText that is safe to use with arbitrary text."""
	label = wx.StaticText(parent, label=text)
	if wrap and len(strip_mnemonic(text)) > 70:
		label.Wrap(wrap)
	return label


# ----------------------------------------------------------------------
# Base window
# ----------------------------------------------------------------------

class AccessibleDialog(wx.Dialog):
	"""Base class for every window in the add-on."""

	def __init__(self, parent, title, size=wx.DefaultSize, resizable=True):
		style = wx.DEFAULT_DIALOG_STYLE | (wx.RESIZE_BORDER if resizable else 0)
		super().__init__(parent, title=title, size=size, style=style)
		self._dead = False
		self._close_approved = False
		self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)
		self.Bind(wx.EVT_CLOSE, self._on_close_window)
		self.Bind(wx.EVT_BUTTON, self._on_cancel_button, id=wx.ID_CANCEL)
		self.Bind(wx.EVT_CHAR_HOOK, self._on_list_char_hook)

	def _on_list_char_hook(self, event):
		"""Make Enter and Delete work in lists.

		A dialog handles Enter itself before a list sees the key, so lists that set
		`enter_action` (and `delete_action`) get them here, ahead of that handling.
		"""
		code = event.GetKeyCode()
		focus = wx.Window.FindFocus()
		if (
			isinstance(focus, wx.ListBox) and not event.HasModifiers()
			and (self.IsDescendant(focus) if hasattr(self, "IsDescendant") else True)
		):
			if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
				action = getattr(focus, "enter_action", None)
			elif code in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
				action = getattr(focus, "delete_action", None)
			else:
				action = None
			if action:
				action()
				return
		event.Skip()

	# --- life cycle --------------------------------------------------
	def _on_destroy(self, event):
		if event.GetEventObject() is self:
			self._dead = True
		event.Skip()

	@property
	def alive(self):
		return not self._dead

	def can_close(self):
		"""Override to veto closing, for example to protect unsaved work."""
		return True

	def _may_close(self):
		"""Ask can_close() once per close attempt, however many events wx sends."""
		if self._close_approved:
			return True
		self._close_approved = bool(self.can_close())
		return self._close_approved

	def request_close(self):
		if self._may_close():
			self.EndModal(wx.ID_CANCEL)
		else:
			self._close_approved = False

	def _on_close_window(self, event):
		if event.CanVeto() and not self._may_close():
			self._close_approved = False
			event.Veto()
			return
		event.Skip()

	def _on_cancel_button(self, event):
		self.request_close()

	# --- helpers -----------------------------------------------------
	def run(self, work, done=None):
		"""Run work() off the GUI thread. done(result) runs only if this window still exists."""

		def guarded(result):
			if not self._dead:
				done(result)

		run_async(work, guarded if done else None)

	def layout(self, sizer, fit=False, focus=None):
		if fit:
			self.SetSizerAndFit(sizer)
		else:
			self.SetSizer(sizer)
		apply_access_keys(self)
		self.Centre()
		if focus is not None:
			focus.SetFocus()

	def close_button(self, label=None):
		return wx.Button(self, wx.ID_CANCEL, label or _("&Close"))

	def add_button(self, row, label, handler, flags=wx.ALL, border=3):
		button = wx.Button(self, label=label)
		button.Bind(wx.EVT_BUTTON, handler)
		row.Add(button, 0, flags, border)
		return button

	def popup_menu(self, entries, position=None):
		"""Show a menu.

		entries: list of (label, handler) or None for a separator. A handler that is
		a list of entries makes a submenu.
		"""
		menu = self._build_menu(entries)
		try:
			if position is None:
				self.PopupMenu(menu)
			else:
				self.PopupMenu(menu, position)
		finally:
			menu.Destroy()

	def menu_position(self, event, widget):
		"""Where to show a context menu: at the mouse, or next to the widget for the Applications key."""
		position = event.GetPosition()
		if position == wx.DefaultPosition:
			position = widget.ClientToScreen(wx.Point(20, 20))
		return self.ScreenToClient(position)

	def _build_menu(self, entries):
		menu = wx.Menu()
		real = [e for e in entries if e is not None]
		labels = iter(resolve_access_keys([e[0] for e in real]))
		for entry in entries:
			if entry is None:
				menu.AppendSeparator()
				continue
			label = next(labels)
			if isinstance(entry[1], (list, tuple)):
				menu.AppendSubMenu(self._build_menu(entry[1]), label)
				continue
			item = menu.Append(wx.ID_ANY, label)
			menu.Bind(wx.EVT_MENU, lambda e, h=entry[1]: wx.CallAfter(h), item)
		return menu


# ----------------------------------------------------------------------
# Text viewer
# ----------------------------------------------------------------------

class TextViewDialog(AccessibleDialog):
	"""Read-only text with action buttons. Focus starts in the text."""

	def __init__(self, parent, title, text="", buttons=None, label=None, wrap=True, size=(740, 560)):
		super().__init__(parent, title, size)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(self, label=label or _("&Content:")), 0, wx.ALL, PAD)
		style = wx.TE_MULTILINE | wx.TE_READONLY | (wx.TE_BESTWRAP if wrap else wx.TE_DONTWRAP | wx.HSCROLL)
		self.text = wx.TextCtrl(self, value=text, style=style)
		sizer.Add(self.text, 1, wx.EXPAND | wx.ALL, PAD)
		row = wx.WrapSizer(wx.HORIZONTAL)
		self.buttons = []
		for button_label, handler in buttons or []:
			button = wx.Button(self, label=button_label)
			button.Bind(wx.EVT_BUTTON, lambda e, h=handler: h(self))
			row.Add(button, 0, wx.ALL, 3)
			self.buttons.append(button)
		row.Add(self.close_button(), 0, wx.ALL, 3)
		sizer.Add(row, 0, wx.ALIGN_CENTER)
		self.layout(sizer, focus=self.text)
		self.text.SetInsertionPoint(0)

	def set_text(self, text):
		self.text.SetValue(text)
		self.text.SetInsertionPoint(0)


# ----------------------------------------------------------------------
# Forms
# ----------------------------------------------------------------------

class FormDialog(AccessibleDialog):
	"""A simple labelled form.

	fields:  (label, key, multiline=False, default="", required=False)
	choices: (label, key, options, selected_index=0)   value is the index
	checks:  (label, key, default)
	Ctrl+Enter submits from anywhere, which helps in multi-line boxes.
	"""

	def __init__(self, parent, title, fields, checks=(), choices=(), ok_label=None, intro=""):
		super().__init__(parent, title, (580, -1))
		self.values = {}
		self._controls = {}
		self._required = {}
		sizer = wx.BoxSizer(wx.VERTICAL)
		if intro:
			sizer.Add(make_label(self, intro, wrap=540), 0, wx.ALL, PAD)
		for field in fields:
			label, key = field[0], field[1]
			multiline = field[2] if len(field) > 2 else False
			default = field[3] if len(field) > 3 else ""
			required = field[4] if len(field) > 4 else False
			sizer.Add(make_label(self, label, wrap=540), 0, wx.LEFT | wx.TOP, PAD)
			control = wx.TextCtrl(
				self, value=default or "", style=wx.TE_MULTILINE if multiline else 0,
				size=(-1, 150 if multiline else -1),
			)
			sizer.Add(control, 1 if multiline else 0, wx.EXPAND | wx.ALL, PAD)
			self._controls[key] = control
			self._required[key] = (required, label)
		for choice in choices:
			label, key, options = choice[0], choice[1], choice[2]
			selected = choice[3] if len(choice) > 3 else 0
			sizer.Add(make_label(self, label), 0, wx.LEFT | wx.TOP, PAD)
			control = wx.Choice(self, choices=list(options))
			control.SetSelection(selected if 0 <= selected < len(options) else 0)
			sizer.Add(control, 0, wx.ALL, PAD)
			self._controls[key] = control
		for label, key, default in checks:
			control = wx.CheckBox(self, label=label)
			control.SetValue(bool(default))
			sizer.Add(control, 0, wx.ALL, PAD)
			self._controls[key] = control
		row = wx.BoxSizer(wx.HORIZONTAL)
		ok = wx.Button(self, wx.ID_OK, ok_label or _("&OK"))
		ok.SetDefault()
		ok.Bind(wx.EVT_BUTTON, self._on_ok)
		row.Add(ok, 0, wx.ALL, PAD)
		row.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancel")), 0, wx.ALL, PAD)
		sizer.Add(row, 0, wx.ALIGN_RIGHT)
		self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
		first = fields[0][1] if fields else None
		self.layout(sizer, fit=True, focus=self._controls[first] if first else None)

	def _on_char_hook(self, event):
		if event.ControlDown() and event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			self._on_ok(None)
		else:
			event.Skip()

	def _on_ok(self, event):
		values = {}
		for key, control in self._controls.items():
			if isinstance(control, wx.Choice):
				values[key] = control.GetSelection()
			else:
				values[key] = control.GetValue()
		for key, (required, label) in self._required.items():
			if required and not str(values.get(key, "")).strip():
				name = strip_mnemonic(label).strip().rstrip(":").strip()
				ui.message(_("{field} is required.").format(field=name))
				self._controls[key].SetFocus()
				return
		self.values = values
		self.EndModal(wx.ID_OK)


def ask_form(parent, title, fields, **kwargs):
	"""Show a FormDialog and return its values dict, or None if cancelled."""
	dlg = FormDialog(parent, title, fields, **kwargs)
	try:
		if dlg.ShowModal() == wx.ID_OK:
			return dlg.values
	finally:
		dlg.Destroy()
	return None


def prompt_text(parent, title, label, value="", multiline=False):
	"""Ask for one text value. Returns None if cancelled."""
	values = ask_form(parent, title, [(label, "value", multiline, value)])
	return None if values is None else values["value"]


def confirm_typed(parent, title, message, word):
	"""Guard for dangerous actions: the user has to type `word` to go on."""
	values = ask_form(
		parent, title, [(_('Type "{word}" to confirm:').format(word=escape_label(word)), "value", False, "")],
		ok_label=_("&Confirm"), intro=escape_label(message),
	)
	return bool(values) and values["value"].strip() == word


# ----------------------------------------------------------------------
# Paged lists
# ----------------------------------------------------------------------

class PagedListDialog(AccessibleDialog):
	"""A list loaded page by page, with a filter box, actions and paging.

	fetch(page, selections) -> Result    runs on a worker thread; data is a list
	label(item) -> str                   one line per item, what NVDA reads
	actions: list of (button label, fn(dialog, item), needs_item=True).
	         Enter or double click runs the first one. The Applications key
	         opens a menu with all of them. F5 refreshes, Ctrl+F filters.
	choices: [(label, [options])] drop-downs; changing one reloads page 1.
	"""

	def __init__(self, parent, title, fetch, label, actions, empty=None, choices=None, info="",
				size=(780, 600)):
		super().__init__(parent, title, size)
		self.fetch, self.label_fn, self.actions = fetch, label, actions
		self.empty = empty or _("No items found.")
		self.page, self.items, self.shown = 1, [], []
		self.has_next = False
		self._labels, self._shown_labels = [], []
		self._request = 0
		self._keep = None
		self._filter_timer = None
		self._choice_controls = []

		sizer = wx.BoxSizer(wx.VERTICAL)
		if info:
			sizer.Add(make_label(self, info, wrap=700), 0, wx.ALL, PAD)
		for choice_label, options in choices or []:
			row = wx.BoxSizer(wx.HORIZONTAL)
			row.Add(make_label(self, choice_label), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, PAD)
			control = wx.Choice(self, choices=list(options))
			control.SetSelection(0)
			control.Bind(wx.EVT_CHOICE, lambda e: self.load(1))
			row.Add(control, 0, wx.ALL, PAD)
			sizer.Add(row, 0)
			self._choice_controls.append(control)
		filter_row = wx.BoxSizer(wx.HORIZONTAL)
		filter_row.Add(
			wx.StaticText(self, label=_("Fil&ter this page:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, PAD
		)
		self.filter = wx.TextCtrl(self)
		self.filter.Bind(wx.EVT_TEXT, self._on_filter_text)
		filter_row.Add(self.filter, 1, wx.ALL, PAD)
		sizer.Add(filter_row, 0, wx.EXPAND)
		sizer.Add(wx.StaticText(self, label=_("&Items:")), 0, wx.LEFT, PAD)
		self.listbox = wx.ListBox(self)
		self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.run_default())
		self.listbox.enter_action = self.run_default
		self.listbox.Bind(wx.EVT_KEY_DOWN, self._on_key)
		self.listbox.Bind(wx.EVT_CONTEXT_MENU, self._on_context_menu)
		sizer.Add(self.listbox, 1, wx.EXPAND | wx.ALL, PAD)

		row = wx.WrapSizer(wx.HORIZONTAL)
		for action in actions:
			if strip_mnemonic(action[0]).strip() == _("Open"):
				continue  # opening is Enter, double click or the Applications key
			self.add_button(row, action[0], lambda e, a=action: self.run_action(a))
		self.btn_prev = self.add_button(row, _("&Previous Page"), lambda e: self.load(self.page - 1))
		self.btn_next = self.add_button(row, _("&Next Page"), lambda e: self.load(self.page + 1))
		self.add_button(row, _("&Refresh"), lambda e: self.load(self.page))
		row.Add(self.close_button(), 0, wx.ALL, 3)
		sizer.Add(row, 0, wx.ALIGN_CENTER)
		self.layout(sizer)
		self._update_paging()
		self.load(1)

	# --- helpers -----------------------------------------------------
	def selections(self):
		return [c.GetSelection() for c in self._choice_controls]

	def selected(self):
		i = self.listbox.GetSelection()
		return self.shown[i] if 0 <= i < len(self.shown) else None

	def _on_key(self, event):
		code = event.GetKeyCode()
		if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			self.run_default()
		elif code == wx.WXK_F5:
			self.load(self.page)
		elif code == ord("F") and event.ControlDown():
			self.filter.SetFocus()
		else:
			event.Skip()

	def _on_context_menu(self, event):
		entries = [(a[0], lambda a=a: self.run_action(a)) for a in self.actions]
		if entries:
			self.popup_menu(entries)

	def run_default(self):
		if self.actions:
			self.run_action(self.actions[0])

	def run_action(self, action):
		needs_item = action[2] if len(action) > 2 else True
		item = self.selected()
		if needs_item and item is None:
			ui.message(_("Select an item first."))
			return
		action[1](self, item)

	def _update_paging(self):
		self.btn_prev.Enable(self.page > 1)
		self.btn_next.Enable(self.has_next)

	# --- filtering ---------------------------------------------------
	def _on_filter_text(self, event):
		self.apply_filter()
		if self._filter_timer is not None:
			self._filter_timer.Stop()
		self._filter_timer = wx.CallLater(700, self._announce_filter)

	def _announce_filter(self):
		if not self._dead:
			ui.message(_("{count} matching items").format(count=len(self.shown)))

	def apply_filter(self):
		words = self.filter.GetValue().lower().split()
		pairs = [
			(item, label) for item, label in zip(self.items, self._labels)
			if all(word in label.lower() for word in words)
		]
		self.shown = [p[0] for p in pairs]
		self._shown_labels = [p[1] for p in pairs]
		self.listbox.Set(self._shown_labels)
		index = 0
		if self._keep in self._shown_labels:
			index = self._shown_labels.index(self._keep)
		self._keep = None
		if self.shown:
			self.listbox.SetSelection(index)

	# --- loading -----------------------------------------------------
	def load(self, page=None, keep_focus=False):
		page = self.page if page is None else page
		if page < 1:
			return
		self._request += 1
		request = self._request
		if page == self.page:
			item = self.selected()
			self._keep = self.label_fn(item) if item is not None else None
		else:
			self._keep = None
		ui.message(_("Loading..."))
		selections = self.selections()

		def done(res):
			if request != self._request:
				return  # a newer request has replaced this one
			if not isinstance(res, Result) or not res.ok:
				ui.message(res.message if isinstance(res, Result) else _("Could not load the list."))
				return
			self.items = res.data if isinstance(res.data, list) else []
			self._labels = [self.label_fn(i) for i in self.items]
			self.page = page
			self.has_next = res.has_next
			self.filter.ChangeValue("")
			self.apply_filter()
			self._update_paging()
			if self.items:
				ui.message(_("Page {page}, {count} items").format(page=page, count=len(self.items)))
				if not keep_focus:
					self.listbox.SetFocus()
			else:
				ui.message(self.empty)

		self.run(lambda: self.fetch(page, selections), done)

	def reload(self):
		self.load(self.page)


def local_fetch(items, per_page=None):
	"""Build a fetch() for PagedListDialog over an in-memory list."""

	def fetch(page, selections):
		size = per_page or items_per_page()
		chunk = items[(page - 1) * size: page * size]
		more = len(items) > page * size
		return Result(200, chunk, {"Link": '<local>; rel="next"'} if more else {})

	return fetch


__all__ = [
	"AccessibleDialog", "FormDialog", "PagedListDialog", "TextViewDialog", "apply_access_keys",
	"ask_choice", "ask_form", "ask_multi_choice", "confirm", "confirm_typed", "copy_to_clipboard",
	"escape_label", "get_clipboard_text", "inform", "local_fetch", "make_label", "prompt_text", "show_modal",
]
