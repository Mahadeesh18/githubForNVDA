# GitHub for NVDA
# Copyright (C) 2025-2026 Mahadeesh and contributors
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for more details.

"""Core services: add-on settings, token storage, the GitHub API client and background work."""

import addonHandler
import base64
import config
import ctypes
import globalVars
import os
import queueHandler
import requests
import threading
import time
import ui
import webbrowser
import wx
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from logHandler import log

from .logic import (
	error_text,
	has_next_page,
	parse_link_header,
)

addonHandler.initTranslation()


# ----------------------------------------------------------------------
# tokenstore.py: Encrypted storage for the personal access token.
# ----------------------------------------------------------------------

_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
	_fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


_dlls = {}


def _windll(name):
	"""Load a Windows DLL once. Declared argument types keep this safe on 64 bit NVDA."""
	if name not in _dlls:
		_dlls[name] = ctypes.WinDLL(name, use_last_error=True)
	return _dlls[name]


def _dpapi(data, protect):
	crypt32 = _windll("crypt32")
	kernel32 = _windll("kernel32")
	kernel32.LocalFree.argtypes = [ctypes.c_void_p]
	kernel32.LocalFree.restype = ctypes.c_void_p
	blob_pointer = ctypes.POINTER(_DataBlob)
	if protect:
		function = crypt32.CryptProtectData
		function.argtypes = [
			blob_pointer, ctypes.c_wchar_p, blob_pointer, ctypes.c_void_p, ctypes.c_void_p,
			ctypes.c_uint32, blob_pointer,
		]
	else:
		function = crypt32.CryptUnprotectData
		function.argtypes = [
			blob_pointer, ctypes.c_void_p, blob_pointer, ctypes.c_void_p, ctypes.c_void_p,
			ctypes.c_uint32, blob_pointer,
		]
	function.restype = ctypes.c_int
	buffer = ctypes.create_string_buffer(data, len(data))
	source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
	target = _DataBlob()
	if not function(
		ctypes.byref(source), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(target)
	):
		raise OSError(ctypes.get_last_error(), "Windows data protection call failed")
	try:
		return ctypes.string_at(target.pbData, target.cbData)
	finally:
		kernel32.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


def dpapi_protect(data):
	return _dpapi(data, True)


def dpapi_unprotect(data):
	return _dpapi(data, False)


class TokenStore:
	"""Load, save and delete the token. Thread safe."""

	def __init__(self, directory, protect=dpapi_protect, unprotect=dpapi_unprotect):
		self.directory = directory
		self.path = os.path.join(directory, "gh_token.dat")
		self.legacy_path = os.path.join(directory, "gh_token.txt")
		self._protect = protect
		self._unprotect = unprotect
		self._cache = None
		self._lock = threading.RLock()
		self._warned = False

	def load(self):
		"""Return the saved token, or an empty string."""
		with self._lock:
			if self._cache is not None:
				return self._cache
			token = ""
			try:
				if os.path.exists(self.path):
					with open(self.path, "r", encoding="ascii") as f:
						raw = base64.b64decode(f.read().strip())
					token = self._unprotect(raw).decode("utf-8").strip()
				elif os.path.exists(self.legacy_path):
					with open(self.legacy_path, "r", encoding="utf-8") as f:
						token = f.read().strip()
					if token and self.save(token):
						self._remove(self.legacy_path)
			except Exception:
				if not self._warned:
					self._warned = True
					log.error("GitHub for NVDA: could not read the stored token", exc_info=True)
				return ""  # do not cache failures: a later attempt may succeed
			self._cache = token
			return token

	def save(self, token):
		token = str(token or "").strip()
		if not token:
			return False
		with self._lock:
			try:
				os.makedirs(self.directory, exist_ok=True)
				encrypted = self._protect(token.encode("utf-8"))
				temporary = self.path + ".tmp"
				with open(temporary, "w", encoding="ascii") as f:
					f.write(base64.b64encode(encrypted).decode("ascii"))
				os.replace(temporary, self.path)
				self._cache = token
				self._warned = False
				return True
			except Exception:
				log.error("GitHub for NVDA: could not save the token", exc_info=True)
				return False

	def delete(self):
		with self._lock:
			removed_main = self._remove(self.path)
			removed_legacy = self._remove(self.legacy_path)
			ok = removed_main and removed_legacy
			# If a file could not be removed, read it again next time instead of pretending it is gone.
			self._cache = "" if ok else None
			return ok

	@staticmethod
	def _remove(path):
		try:
			if os.path.exists(path):
				os.remove(path)
			return True
		except OSError:
			log.error("GitHub for NVDA: could not remove %s", path, exc_info=True)
			return False


# ----------------------------------------------------------------------
# prefs.py: Add-on settings, stored in the NVDA configuration (Settings > GitHub for NVDA).
# ----------------------------------------------------------------------

SECTION = "githubForNVDA"

CONFIG_SPEC = {
	"itemsPerPage": "integer(min=10, max=100, default=30)",
	"notificationInterval": "integer(min=0, max=1440, default=0)",
	"notificationSeconds": "integer(min=0, max=86400, default=0)",
	"notificationFilter": "integer(min=0, max=1, default=0)",
	"notificationDetails": "boolean(default=False)",
	"notificationBeep": "boolean(default=True)",
	"markReadOnView": "boolean(default=True)",
	"wrapFileText": "boolean(default=False)",
	"downloadFolder": "string(default='')",
}

DEFAULTS = {
	"itemsPerPage": 30,
	"notificationInterval": 0,
	"notificationSeconds": 0,
	"notificationFilter": 0,
	"notificationDetails": False,
	"notificationBeep": True,
	"markReadOnView": True,
	"wrapFileText": False,
	"downloadFolder": "",
}

# Seconds offered in the settings panel for background notification checks. 15 and 30 seconds are the
# fast choices: they cost no API quota while nothing changes (GitHub answers "304 Not Modified").
NOTIFICATION_INTERVALS = (0, 15, 30, 60, 120, 300, 600, 900, 1800, 3600)

# Which new notifications are announced: everything, or only the ones that involve you directly.
NOTIFICATION_FILTER_ALL, NOTIFICATION_FILTER_DIRECT = 0, 1


def register_prefs():
	"""Declare the settings. Called once when the plugin loads."""
	config.conf.spec[SECTION] = CONFIG_SPEC


def get_pref(key):
	try:
		return config.conf[SECTION][key]
	except Exception:
		return DEFAULTS[key]


def set_pref(key, value):
	try:
		config.conf[SECTION][key] = value
	except Exception:
		log.error("GitHub for NVDA: could not save setting %s", key, exc_info=True)


def notification_seconds():
	"""Seconds between background notification checks, 0 when they are off.

	Older versions stored whole minutes in "notificationInterval"; that value is still honoured until
	the settings are saved once.
	"""
	try:
		seconds = int(get_pref("notificationSeconds") or 0)
		if seconds > 0:
			return seconds
		return max(0, int(get_pref("notificationInterval") or 0)) * 60
	except (TypeError, ValueError):
		return 0


def items_per_page():
	try:
		return max(10, min(100, int(get_pref("itemsPerPage"))))
	except (TypeError, ValueError):
		return DEFAULTS["itemsPerPage"]


def downloads_dir():
	"""The folder for downloads: the configured one if usable, else Downloads."""
	configured = str(get_pref("downloadFolder") or "").strip()
	if configured:
		try:
			os.makedirs(configured, exist_ok=True)
			return configured
		except OSError:
			log.debugWarning("Configured download folder is not usable: %s", configured)
	folder = os.path.join(os.path.expanduser("~"), "Downloads")
	try:
		os.makedirs(folder, exist_ok=True)
	except OSError:
		folder = os.path.expanduser("~")
	return folder


# ----------------------------------------------------------------------
# auth.py: Where the add-on keeps its files, and the saved token.
# ----------------------------------------------------------------------

# The folder name is unchanged from earlier versions so that tokens and bookmarks
# survive the update.
_FOLDER = "github_for_nvda"

_store = None


def config_dir():
	"""The add-on's folder inside the NVDA user configuration. Created on demand."""
	path = os.path.join(globalVars.appArgs.configPath, _FOLDER)
	try:
		os.makedirs(path, exist_ok=True)
	except OSError:
		log.error("GitHub for NVDA: cannot create %s", path, exc_info=True)
	return path


def _token_store():
	global _store
	if _store is None:
		_store = TokenStore(config_dir())
	return _store


def load_token():
	return _token_store().load()


def save_token(token):
	return _token_store().save(token)


def delete_token():
	return _token_store().delete()


def has_token():
	return bool(load_token())


# ----------------------------------------------------------------------
# ghclient.py: A small GitHub REST API client.
# ----------------------------------------------------------------------

API_ROOT = "https://api.github.com"
USER_AGENT = "GitHubForNVDA"
API_VERSION = "2022-11-28"
_RETRY_STATUS = frozenset({502, 503, 504})

# The most recent rate limit figures GitHub reported, per resource ("core", "search"...).
rate_limits = {}


class Result(namedtuple("Result", "code data headers")):
	"""Outcome of an API call. code 0 means the network request itself failed."""

	__slots__ = ()

	@property
	def ok(self):
		return 200 <= self.code < 300

	@property
	def message(self):
		return error_text(self.code, self.data, self.headers)

	@property
	def has_next(self):
		return has_next_page(self.headers)


# ----------------------------------------------------------------------
# Connection
# ----------------------------------------------------------------------

_session = None
_session_lock = threading.Lock()


def get_session():
	"""One shared session: connections are reused, which makes lists much faster."""
	global _session
	with _session_lock:
		if _session is None:
			session = requests.Session()
			adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=10)
			session.mount("https://", adapter)
			_session = session
		return _session


def close_session():
	global _session
	with _session_lock:
		if _session is not None:
			try:
				_session.close()
			except Exception:
				log.debugWarning("Closing the GitHub session failed", exc_info=True)
			_session = None


def auth_headers(token=None, accept=None):
	"""Headers for a request. token=None uses the saved token, '' sends none."""
	headers = {
		"User-Agent": USER_AGENT,
		"Accept": accept or "application/vnd.github+json",
		"X-GitHub-Api-Version": API_VERSION,
	}
	token = load_token() if token is None else token
	token = (token or "").strip()
	if token:
		headers["Authorization"] = f"Bearer {token}"
	return headers


def _remember_rate_limit(headers):
	try:
		resource = headers.get("X-RateLimit-Resource")
		if resource and headers.get("X-RateLimit-Limit"):
			rate_limits[resource] = {
				"limit": int(headers.get("X-RateLimit-Limit")),
				"remaining": int(headers.get("X-RateLimit-Remaining", 0)),
				"reset": int(headers.get("X-RateLimit-Reset", 0)),
			}
	except (TypeError, ValueError):
		pass


def gh(
	method, url, params=None, body=None, accept=None, auth=True, timeout=30,
	stream=False, binary=False, data=None, headers=None, retries=None,
):
	"""Send one request and return a Result. Never raises for network problems."""
	method = method.upper()
	if not url.startswith("http"):
		url = API_ROOT + url
	# The token is only ever sent to the GitHub API, never to another web address.
	send_token = auth and url.startswith(API_ROOT + "/")
	request_headers = auth_headers(None if send_token else "", accept)
	if headers:
		request_headers.update(headers)
	attempts = (2 if method in ("GET", "HEAD") else 0) if retries is None else retries
	response = None
	for attempt in range(attempts + 1):
		try:
			response = get_session().request(
				method, url, headers=request_headers, params=params,
				json=body if data is None else None, data=data, timeout=timeout, stream=stream,
			)
		except requests.exceptions.Timeout:
			if attempt < attempts:
				time.sleep(0.6 * (attempt + 1))
				continue
			return Result(0, "Timed out", {})
		except requests.exceptions.RequestException as e:
			if attempt < attempts:
				time.sleep(0.6 * (attempt + 1))
				continue
			return Result(0, str(e), {})
		if response.status_code in _RETRY_STATUS and attempt < attempts:
			response.close()
			time.sleep(0.6 * (attempt + 1))
			continue
		break
	if response is None:  # pragma: no cover - the loop always sets it or returns
		return Result(0, "No response", {})
	_remember_rate_limit(response.headers)
	if stream and response.status_code < 400:
		return Result(response.status_code, response, response.headers)
	if binary and response.status_code < 400:
		return Result(response.status_code, response.content, response.headers)
	try:
		payload = response.json() if response.content else None
	except ValueError:
		payload = response.text
	return Result(response.status_code, payload, response.headers)


def gh_paged(url, params=None, max_items=1000, per_page=100, unwrap=None):
	"""GET every page of a list endpoint (follows Link headers).

	unwrap: key holding the list inside a dict payload, for endpoints such as
	/actions/runs that return {"total_count": n, "workflow_runs": [...]}.
	The returned headers contain "X-Truncated": "1" when max_items cut the list.
	"""
	params = dict(params or {})
	params.setdefault("per_page", per_page)
	items = []
	next_url = url
	first = True
	last = None
	truncated = False
	while next_url:
		res = gh("GET", next_url, params=params if first else None)
		first = False
		if not res.ok:
			return res
		if isinstance(res.data, dict):
			items.extend(res.data.get(unwrap or "items", []))
		elif isinstance(res.data, list):
			items.extend(res.data)
		last = res
		link = (res.headers or {}).get("Link", "")
		next_url = parse_link_header(link).get("next")
		if next_url and len(items) >= max_items:
			truncated = True
			break
	headers = dict(last.headers) if last else {}
	if truncated:
		headers["X-Truncated"] = "1"
	return Result(200, items[:max_items], headers)


class FilteredPager:
	"""Page through a list endpoint while hiding some items.

	GitHub's issues endpoint also returns pull requests. Filtering them out of
	a fixed-size page can leave the page empty while more pages exist. This
	class skips ahead to the next API page that has something to show, and
	remembers where each page of the dialog starts.
	"""

	def __init__(self, url, keep, base_params=None):
		self.url = url
		self.keep = keep
		self.base_params = dict(base_params or {})
		self._starts = {}

	def fetch(self, page, per_page, key=(), params=None):
		api_page = self._starts.get((key, page), page)
		visible = []
		last = None
		more = False
		while True:
			query = dict(self.base_params)
			query.update(params or {})
			query.update(per_page=per_page, page=api_page)
			res = gh("GET", self.url, params=query)
			if not res.ok or not isinstance(res.data, list):
				return res
			last = res
			visible = [item for item in res.data if self.keep(item)]
			more = res.has_next
			if visible or not more:
				break
			api_page += 1
		if more:
			self._starts[(key, page + 1)] = api_page + 1
		return Result(last.code, visible, last.headers)


# ----------------------------------------------------------------------
# Who am I
# ----------------------------------------------------------------------

_identity = {"login": ""}


def current_login():
	"""Login name of the token's owner (cached). Blocks, use from a worker."""
	if _identity["login"]:
		return _identity["login"]
	res = gh("GET", "/user")
	if res.ok and isinstance(res.data, dict):
		_identity["login"] = res.data.get("login", "") or ""
	return _identity["login"]


def forget_identity():
	_identity["login"] = ""


# ----------------------------------------------------------------------
# tasks.py: Background work helpers.
# ----------------------------------------------------------------------

_executor = None
_executor_lock = threading.Lock()


def _pool():
	global _executor
	with _executor_lock:
		if _executor is None:
			_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="githubForNVDA")
		return _executor


def shutdown():
	"""Stop accepting work. Called when the plugin terminates."""
	global _executor
	with _executor_lock:
		pool, _executor = _executor, None
	if pool is not None:
		pool.shutdown(wait=False, cancel_futures=True)


def say(message):
	"""Speak and braille a message. Safe to call from any thread."""
	try:
		queueHandler.queueFunction(queueHandler.eventQueue, ui.message, message)
	except Exception:
		log.debugWarning("GitHub for NVDA: say() failed", exc_info=True)


def _deliver(callback, *args):
	try:
		callback(*args)
	except RuntimeError as e:
		# The window was closed while the request was running: harmless.
		if "deleted" not in str(e).lower():
			log.error("GitHub for NVDA: callback failed", exc_info=True)
	except Exception:
		log.error("GitHub for NVDA: callback failed", exc_info=True)


def run_async(func, callback=None):
	"""Run func() on a worker thread, then callback(result) on the GUI thread."""

	def worker():
		try:
			result = func()
		except Exception as e:
			log.error("GitHub for NVDA: background task failed", exc_info=True)
			result = Result(0, str(e), {})
		if callback:
			wx.CallAfter(_deliver, callback, result)

	try:
		_pool().submit(worker)
	except RuntimeError:
		log.debugWarning("GitHub for NVDA: worker pool is shut down")


def ask_yes_no(parent, message, title=None):
	"""Ask a yes/no question from a worker thread. Blocks until answered."""
	finished = threading.Event()
	answer = [False]

	def show():
		try:
			answer[0] = wx.MessageBox(
				message, title or _("Confirm"), wx.YES_NO | wx.ICON_QUESTION, parent
			) == wx.YES
		except Exception:
			log.debugWarning("GitHub for NVDA: question could not be shown", exc_info=True)
		finally:
			finished.set()

	wx.CallAfter(show)
	while not finished.wait(0.5):
		if _executor is None:  # the add-on is shutting down
			return False
	return answer[0]


def open_url(url):
	"""Open a web address in the default browser. Only http(s) is allowed."""
	if url and str(url).lower().startswith(("https://", "http://")):
		webbrowser.open(url)
		say(_("Opening in your browser"))
	else:
		say(_("There is no web address to open."))
