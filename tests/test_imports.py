"""Import every module of the add-on with stand-ins for NVDA and wxPython.

This does not test the windows; it catches mistakes such as a misspelled import or a circular import
before the add-on is installed.
"""

import importlib
import os
import sys
import types

import pytest

NVDA_MODULES = (
	"api", "config", "globalPluginHandler", "globalVars", "gui", "gui.guiHelper", "gui.settingsDialogs",
	"logHandler", "nvwave", "queueHandler", "requests", "scriptHandler", "tones", "ui", "wx",
)


class _Anything(types.ModuleType):
	"""A module whose every attribute is a harmless class (so it can be subclassed, called and used as a decorator)."""

	def __getattr__(self, name):
		if name.startswith("__"):
			raise AttributeError(name)
		value = type(name, (), {
			"__init__": lambda self, *args, **kwargs: None,
			"__call__": lambda self, *args, **kwargs: args[0] if args else None,
		})
		setattr(self, name, value)
		return value


@pytest.fixture
def addon(monkeypatch):
	"""The add-on package, imported with stand-ins for NVDA and wx."""
	for name in NVDA_MODULES:
		monkeypatch.setitem(sys.modules, name, _Anything(name))
	sys.modules["gui"].guiHelper = sys.modules["gui.guiHelper"]
	sys.modules["gui"].settingsDialogs = sys.modules["gui.settingsDialogs"]
	monkeypatch.syspath_prepend(os.path.join(os.path.dirname(__file__), "..", "globalPlugins"))
	for module in list(sys.modules):
		if module == "githubForNVDA" or module.startswith("githubForNVDA."):
			monkeypatch.delitem(sys.modules, module)
	return importlib.import_module("githubForNVDA")


def test_every_module_imports(addon):
	folder = os.path.dirname(addon.__file__)
	for file in sorted(os.listdir(folder)):
		if file.endswith(".py") and file != "__init__.py":
			importlib.import_module("githubForNVDA." + file[:-3])
	core = sys.modules["githubForNVDA.core"]
	problem = core.Problem("Too large.")
	assert problem.message == "Too large." and not problem.ok


def _pages(core, count, size=3):
	"""A fetch() that serves `count` pages of `size` numbers, with a Link header while more follow."""
	def fetch(page, selections):
		data = list(range((page - 1) * size, page * size))
		return core.Result(200, data, {"Link": '<x>; rel="next"'} if page < count else {})
	return fetch


def test_pages_are_handed_over_as_they_arrive(addon):
	core = importlib.import_module("githubForNVDA.core")
	seen = []
	res = core.fetch_every_page(_pages(core, 3), [], on_page=lambda data, more: seen.append((data, more)))
	assert seen == [([0, 1, 2], True), ([3, 4, 5], True), ([6, 7, 8], False)]
	assert res.data == list(range(9)) and not res.headers.get("X-Truncated")


def test_loading_stops_when_no_longer_wanted(addon):
	core = importlib.import_module("githubForNVDA.core")
	seen = []
	core.fetch_every_page(_pages(core, 5), [], on_page=lambda data, more: seen.append(data), keep_going=lambda: len(seen) < 2)
	assert seen == [[0, 1, 2], [3, 4, 5]]


def test_the_limit_cuts_the_last_page(addon):
	core = importlib.import_module("githubForNVDA.core")
	seen = []
	res = core.fetch_every_page(_pages(core, 5), [], limit=7, on_page=lambda data, more: seen.append((data, more)))
	assert seen[-1] == ([6], False)
	assert len(res.data) == 7 and res.headers.get("X-Truncated")


class _ListBox:
	"""Just enough of wx.ListBox for the list window's bookkeeping."""

	def __init__(self):
		self.strings, self.selection = [], -1

	def Set(self, strings):
		self.strings = list(strings)

	def SetString(self, index, text):
		self.strings[index] = text

	def Delete(self, index):
		del self.strings[index]

	def SetSelection(self, index):
		self.selection = index

	def GetSelection(self):
		return self.selection


def _list_window(core, items):
	"""A PagedListDialog without a real window, showing `items` with item 1 selected."""
	dlg = core.PagedListDialog.__new__(core.PagedListDialog)
	dlg.label_fn = lambda i: f"{i['id']} {'unread' if i['unread'] else 'read'}"
	dlg.actions, dlg.on_select, dlg._action_buttons, dlg._selected_key = [], None, {}, None
	dlg.items, dlg.shown = list(items), list(items)
	dlg._labels = [dlg.label_fn(i) for i in items]
	dlg._shown_labels = list(dlg._labels)
	dlg.listbox = _ListBox()
	dlg.listbox.Set(dlg._labels)
	dlg.listbox.SetSelection(1)
	return dlg


def test_updating_an_item_keeps_the_selection(addon):
	core = importlib.import_module("githubForNVDA.core")
	items = [{"id": n, "unread": True} for n in range(4)]
	dlg = _list_window(core, items)
	items[1]["unread"] = False
	dlg.update_item(items[1])
	assert dlg.listbox.strings[1] == "1 read" and dlg.listbox.selection == 1


def test_removing_an_item_selects_the_next_one(addon):
	core = importlib.import_module("githubForNVDA.core")
	items = [{"id": n, "unread": True} for n in range(3)]
	dlg = _list_window(core, items)
	following = dlg.remove_item(items[1])
	assert following is items[2] and dlg.listbox.selection == 1
	assert dlg.listbox.strings == ["0 unread", "2 unread"] and len(dlg.items) == 2
	assert dlg.remove_item(items[2]) is items[0]  # the last one: the one before it is selected
	assert dlg.remove_item(items[0]) is None and dlg.listbox.strings == []
