"""Run the add-on's pure modules outside NVDA.

Only logic.py and yamlsubset.py are tested here. They need nothing from NVDA except addonHandler's
initTranslation, which is replaced by a stub that installs an identity _() function.
"""

import builtins
import os
import sys
import types

ADDON = os.path.join(os.path.dirname(__file__), "..", "globalPlugins", "githubForNVDA")
sys.path.insert(0, os.path.abspath(ADDON))

builtins._ = lambda text: text
stub = types.ModuleType("addonHandler")
stub.initTranslation = lambda: None
sys.modules.setdefault("addonHandler", stub)
