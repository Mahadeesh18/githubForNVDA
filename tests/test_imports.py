"""Import every module of the add-on with stand-ins for NVDA and wxPython.

This does not test the windows; it catches mistakes such as a misspelled import or a circular import
before the add-on is installed.
"""

import importlib
import os
import sys
import types

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


def test_every_module_imports(monkeypatch):
	for name in NVDA_MODULES:
		monkeypatch.setitem(sys.modules, name, _Anything(name))
	sys.modules["gui"].guiHelper = sys.modules["gui.guiHelper"]
	sys.modules["gui"].settingsDialogs = sys.modules["gui.settingsDialogs"]
	monkeypatch.syspath_prepend(os.path.join(os.path.dirname(__file__), "..", "globalPlugins"))
	for module in list(sys.modules):
		if module == "githubForNVDA" or module.startswith("githubForNVDA."):
			monkeypatch.delitem(sys.modules, module)
	package = importlib.import_module("githubForNVDA")
	folder = os.path.dirname(package.__file__)
	for file in sorted(os.listdir(folder)):
		if file.endswith(".py") and file != "__init__.py":
			importlib.import_module("githubForNVDA." + file[:-3])
	core = sys.modules["githubForNVDA.core"]
	problem = core.Problem("Too large.")
	assert problem.message == "Too large." and not problem.ok
