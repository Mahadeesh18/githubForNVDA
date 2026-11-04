# GitHub for NVDA
# Copyright (C) 2025-2026 Mahadeesh and contributors
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for more details.

"""Pure helper functions: no network, no GUI. Everything here can be unit tested outside NVDA."""

import addonHandler
import api
import base64
import codecs
import config
import ctypes
import globalVars
import gui
import os
import queueHandler
import re
import requests
import threading
import time
import ui
import webbrowser
import wx
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from logHandler import log


addonHandler.initTranslation()


# ----------------------------------------------------------------------
# logic.py: Pure helper functions: no network, no GUI.
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Formatting
# ----------------------------------------------------------------------

def format_size(num):
	"""Return a human readable size such as '1.5 MB'."""
	try:
		value = float(num or 0)
	except (TypeError, ValueError):
		value = 0.0
	if value < 1024:
		count = int(value)
		return _("1 byte") if count == 1 else _("{count} bytes").format(count=count)
	for unit in ("KB", "MB", "GB"):
		value /= 1024
		if value < 1024 or unit == "GB":
			return f"{value:.1f} {unit}"
	return f"{value:.1f} GB"  # pragma: no cover - loop always returns


def format_date(iso_str):
	"""'2026-10-09T10:20:30Z' -> '2026-10-09'. Empty input gives 'unknown'."""
	return (iso_str or "")[:10] or _("unknown")


def format_datetime(iso_str):
	"""Return a long date such as '09 October 2026 at 10:20 AM' (UTC)."""
	if not iso_str:
		return _("unknown")
	try:
		moment = datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ")
	except ValueError:
		return str(iso_str)
	return moment.strftime("%d %B %Y at %I:%M %p")


def truncate(text, limit, note=""):
	"""Cut text to `limit` characters and append `note` when shortened."""
	text = text or ""
	if len(text) <= limit:
		return text
	return text[:limit] + note


def tail(text, limit, note=""):
	"""Keep the last `limit` characters (useful for build logs)."""
	text = text or ""
	if len(text) <= limit:
		return text
	return note + text[-limit:]


# ----------------------------------------------------------------------
# File names
# ----------------------------------------------------------------------

_RESERVED_NAMES = {
	"CON", "PRN", "AUX", "NUL",
	*(f"COM{i}" for i in range(1, 10)),
	*(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(name):
	"""Make a string usable as a Windows file name."""
	name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .")
	if not name:
		return "download"
	if name.split(".")[0].upper() in _RESERVED_NAMES:
		name = "_" + name
	return name


def unique_path(folder, name):
	"""Return folder/name, adding ' (1)', ' (2)'... if it already exists."""
	base, ext = os.path.splitext(name)
	path = os.path.join(folder, name)
	n = 1
	while os.path.exists(path):
		path = os.path.join(folder, f"{base} ({n}){ext}")
		n += 1
	return path


# ----------------------------------------------------------------------
# GitHub links
# ----------------------------------------------------------------------

GitHubRef = namedtuple("GitHubRef", "kind owner repo number rest")
GitHubRef.__doc__ = """A parsed GitHub address.

kind is one of: repo, user, issue, pull, commit, blob, tree, run.
number holds the issue/pull/run number or the commit hash.
rest holds the path segments after blob/tree (branch and file path).
"""
GitHubRef.full_name = property(lambda self: f"{self.owner}/{self.repo}" if self.repo else self.owner)

# First path segments on github.com that are not user or organisation names.
RESERVED_OWNERS = frozenset({
	"about", "apps", "codespaces", "collections", "contact", "customer-stories", "events", "explore",
	"features", "issues", "login", "logout", "marketplace", "new", "notifications", "orgs", "pricing",
	"pulls", "search", "security", "sessions", "settings", "signup", "site", "sponsors", "topics",
	"trending", "users", "enterprise", "organizations", "readme", "team", "git-guides",
})

_NAME = r"[A-Za-z0-9_.-]+"


def _clean_repo_name(name):
	return re.sub(r"\.git$", "", name)


def parse_github_link(text):
	"""Understand what a pasted GitHub address or 'owner/repo' points at.

	Returns a GitHubRef or None.
	"""
	text = (text or "").strip().strip("<>\"'")
	if not text:
		return None
	ssh = re.fullmatch(rf"git@github\.com:({_NAME})/({_NAME})", text)
	if ssh:
		return GitHubRef("repo", ssh.group(1), _clean_repo_name(ssh.group(2)), None, ())
	web = re.match(r"(?:git\+)?(?:https?://)?(?:www\.)?github\.com/([^?#\s]*)", text, re.IGNORECASE)
	if web:
		parts = [p for p in web.group(1).split("/") if p]
	else:
		bare = re.fullmatch(rf"({_NAME})/({_NAME})", text)
		if not bare:
			return None
		parts = [bare.group(1), bare.group(2)]
	if not parts:
		return None
	owner = parts[0]
	if owner.lower() in RESERVED_OWNERS or not re.fullmatch(_NAME, owner):
		return None
	if len(parts) == 1:
		return GitHubRef("user", owner, "", None, ())
	repo = _clean_repo_name(parts[1])
	if not repo or repo in (".", ".."):
		return None
	section = parts[2].lower() if len(parts) > 2 else ""
	tail_parts = tuple(parts[3:])
	if section in ("issues", "pull", "pulls") and tail_parts and tail_parts[0].isdigit():
		kind = "issue" if section == "issues" else "pull"
		return GitHubRef(kind, owner, repo, int(tail_parts[0]), ())
	if section == "commit" and tail_parts:
		return GitHubRef("commit", owner, repo, tail_parts[0], ())
	if section in ("blob", "tree") and tail_parts:
		return GitHubRef(section, owner, repo, None, tail_parts)
	if section == "actions" and len(tail_parts) >= 2 and tail_parts[0] == "runs" and tail_parts[1].isdigit():
		return GitHubRef("run", owner, repo, int(tail_parts[1]), ())
	return GitHubRef("repo", owner, repo, None, ())


def parse_repo_reference(text):
	"""'owner/repo' or any github.com link inside a repository -> 'owner/repo'."""
	ref = parse_github_link(text)
	if ref and ref.kind != "user":
		return ref.full_name
	return None


def repo_from_api_url(url):
	"""'https://api.github.com/repos/o/r/issues/3' -> 'o/r'."""
	match = re.search(r"/repos/([^/]+/[^/?#]+)", url or "")
	return match.group(1) if match else ""


# ----------------------------------------------------------------------
# Paging
# ----------------------------------------------------------------------

def parse_link_header(header):
	"""Parse an HTTP Link header into {rel: url}."""
	links = {}
	for match in re.finditer(r'<([^>]+)>\s*;\s*rel="([^"]+)"', header or ""):
		links[match.group(2)] = match.group(1)
	return links


def has_next_page(headers):
	return "next" in parse_link_header((headers or {}).get("Link", ""))


def last_page(headers, fallback=None):
	"""Number of the last page according to the Link header."""
	url = parse_link_header((headers or {}).get("Link", "")).get("last")
	if url:
		match = re.search(r"[?&]page=(\d+)", url)
		if match:
			return int(match.group(1))
	return fallback


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------

def error_text(code, data=None, headers=None):
	"""Turn a failed API call into a short sentence that is pleasant to hear."""
	headers = headers or {}
	detail = ""
	if isinstance(data, dict):
		detail = str(data.get("message") or "")
		errors = data.get("errors")
		if isinstance(errors, list) and errors:
			parts = []
			for item in errors:
				if isinstance(item, dict):
					parts.append(str(item.get("message") or item.get("code") or item))
				else:
					parts.append(str(item))
			detail = (detail + ": " + "; ".join(parts)).strip(": ")
	elif isinstance(data, str) and code in (0, -1):
		detail = data
	if code == 0:
		return _("Could not reach GitHub. Check your internet connection and try again.")
	if code == 401:
		return _("GitHub rejected your token. Set a new token in NVDA Settings under GitHub for NVDA.")
	if code in (403, 429):
		retry_after = str(headers.get("Retry-After", "")).strip()
		exhausted = (
			str(headers.get("X-RateLimit-Remaining", "")) == "0"
			or code == 429
			or "rate limit" in detail.lower()
		)
		if exhausted:
			if retry_after.isdigit():
				return _("GitHub rate limit reached. Try again in {seconds} seconds.").format(seconds=retry_after)
			return _("GitHub rate limit reached. Wait a few minutes, or set a token to get a higher limit.")
		if headers.get("X-GitHub-SSO"):
			return _(
				"Your organization requires single sign-on. Open github.com/settings/tokens "
				"and authorize this token for the organization."
			)
		return (_("Permission denied. Your token may be missing a required permission.") + " " + detail).strip()
	if code == 404:
		return (_("Not found, or your token cannot access it.") + " " + detail).strip()
	if code == 409:
		return (
			_("Conflict. The item changed on GitHub or is in a state that does not allow this.")
			+ " " + detail
		).strip()
	if code == 410:
		return _("This feature is turned off for this repository.")
	if code == 422:
		return (_("GitHub could not accept that.") + " " + detail).strip()
	if code in (500, 502, 503, 504):
		return _("GitHub is having problems right now. Try again in a moment.")
	return _("GitHub error {code}.").format(code=code) + (" " + detail if detail else "")


# ----------------------------------------------------------------------
# Access keys (Alt+letter)
# ----------------------------------------------------------------------

def parse_label(label):
	"""Split a wx label into (plain text, index of the access key or -1).

	'&&' is a literal ampersand and ends up as '&' in the plain text.
	"""
	plain = []
	mark = -1
	i = 0
	label = label or ""
	while i < len(label):
		char = label[i]
		if char == "&" and i + 1 < len(label):
			nxt = label[i + 1]
			if nxt == "&":
				plain.append("&")
				i += 2
				continue
			if mark == -1:
				mark = len(plain)
			i += 1
			continue
		plain.append(char)
		i += 1
	return "".join(plain), mark


def build_label(plain, mark=-1):
	"""Inverse of parse_label."""
	out = []
	for index, char in enumerate(plain):
		if char == "&":
			out.append("&&")
			continue
		if index == mark:
			out.append("&")
		out.append(char)
	return "".join(out)


def escape_label(text):
	"""Escape '&' so arbitrary text is not mistaken for an access key."""
	return str(text).replace("&", "&&")


def strip_mnemonic(label):
	"""Return a label without access key markers, ready to be spoken."""
	return parse_label(label)[0]


def _pick_key(plain, used):
	candidates = []
	for index, char in enumerate(plain):
		if not char.isalnum() or not char.isascii():
			continue
		starts_word = index == 0 or not plain[index - 1].isalnum()
		candidates.append((0 if starts_word else 1, index))
	for _priority, index in sorted(candidates):
		key = plain[index].lower()
		if key not in used:
			used.add(key)
			return index
	return -1


def resolve_access_keys(labels):
	"""Make the Alt+letter shortcuts in a group of labels unique.

	The first label that asks for a letter keeps it. Later labels that ask for
	the same letter get another unused letter from their own text, or lose the
	shortcut if none is left. Labels without a shortcut are left alone.
	"""
	parsed = [parse_label(label) for label in labels]
	used = set()
	chosen = [None] * len(parsed)
	for n, (plain, mark) in enumerate(parsed):
		if mark >= 0:
			key = plain[mark].lower()
			if key not in used:
				used.add(key)
				chosen[n] = mark
	for n, (plain, mark) in enumerate(parsed):
		if mark >= 0 and chosen[n] is None:
			chosen[n] = _pick_key(plain, used)
	result = []
	for n, (plain, mark) in enumerate(parsed):
		if mark < 0:
			result.append(labels[n])
		else:
			result.append(build_label(plain, chosen[n]))
	return result


# ----------------------------------------------------------------------
# Text files
# ----------------------------------------------------------------------

Decoded = namedtuple("Decoded", "text encoding crlf editable")


def decode_text(data):
	"""Decode file bytes for display. Returns Decoded or None for binary data."""
	if not data:
		return Decoded("", "utf-8", False, True)
	sample = data[:8192]
	if b"\x00" in sample:
		return None
	controls = sum(1 for b in sample if b < 9 or 13 < b < 32)
	if controls / len(sample) > 0.1:
		return None
	crlf = b"\r\n" in data
	try:
		text = data.decode("utf-8")
		encoding = "utf-8-sig" if data.startswith(codecs.BOM_UTF8) else "utf-8"
		if encoding == "utf-8-sig":
			text = text[1:] if text.startswith("﻿") else text
		return Decoded(text.replace("\r\n", "\n").replace("\r", "\n"), encoding, crlf, True)
	except UnicodeDecodeError:
		pass
	try:
		text = data.decode("cp1252")
	except UnicodeDecodeError:
		return None
	return Decoded(text.replace("\r\n", "\n").replace("\r", "\n"), "cp1252", crlf, False)


def encode_text(text, encoding="utf-8", crlf=False):
	"""Inverse of decode_text for editable encodings."""
	text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
	if crlf:
		text = text.replace("\n", "\r\n")
	if encoding == "utf-8-sig":
		return codecs.BOM_UTF8 + text.encode("utf-8")
	return text.encode(encoding)


def normalize_newlines(text):
	return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def split_names(text):
	"""'a, b ,,c' -> ['a', 'b', 'c'] without duplicates, order kept."""
	seen = set()
	names = []
	for part in re.split(r"[,\n]", text or ""):
		part = part.strip()
		if part and part.lower() not in seen:
			seen.add(part.lower())
			names.append(part)
	return names


def clean_branch_name(name):
	"""Make a typed branch name acceptable to git."""
	name = re.sub(r"\s+", "-", (name or "").strip())
	name = re.sub(r"[~^:?*\[\\]+", "", name)
	name = re.sub(r"\.{2,}", ".", name)
	name = name.strip("/.")
	return name.removesuffix(".lock")


def line_count(text):
	return (text or "").count("\n") + 1 if text else 0


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
	"notificationSound": "string(default='default')",
	"notificationSoundFile": "string(default='')",
	"whatsNewSeenVersion": "string(default='')",
	"markReadOnView": "boolean(default=True)",
	"wrapFileText": "boolean(default=False)",
	"askCommitMessage": "boolean(default=False)",
	"downloadFolder": "string(default='')",
}

DEFAULTS = {
	"itemsPerPage": 30,
	"notificationInterval": 0,
	"notificationSeconds": 0,
	"notificationFilter": 0,
	"notificationDetails": False,
	"notificationBeep": True,
	"notificationSound": "default",
	"notificationSoundFile": "",
	"whatsNewSeenVersion": "",
	"markReadOnView": True,
	"wrapFileText": False,
	"askCommitMessage": False,
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
UPLOADS_ROOT = "https://uploads.github.com"  # release files are uploaded here
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
	# The token is only ever sent to the GitHub API (and its upload host), never to another web address.
	send_token = auth and url.startswith((API_ROOT + "/", UPLOADS_ROOT + "/"))
	request_headers = auth_headers(None if send_token else "", accept)
	if method in ("GET", "HEAD"):
		# Ask for fresh data so a list shows a change made a moment ago.
		request_headers["Cache-Control"] = "no-cache"
		request_headers["Pragma"] = "no-cache"
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
	radios:  (label, key, options, selected_index=0)   value is the index
	Ctrl+Enter submits from anywhere, which helps in multi-line boxes.
	"""

	def __init__(self, parent, title, fields, checks=(), choices=(), ok_label=None, intro="", extra=None, radios=()):
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
			# A wide box (not the tiny default width) keeps many words on each line when reading by line.
			control = wx.TextCtrl(
				self, value=default or "", style=(wx.TE_MULTILINE | wx.TE_BESTWRAP) if multiline else 0,
				size=(540 if multiline else -1, 200 if multiline else -1),
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
		for radio in radios:
			label, key, options = radio[0], radio[1], radio[2]
			selected = radio[3] if len(radio) > 3 else 0
			control = wx.RadioBox(self, label=label, choices=list(options), majorDimension=1, style=wx.RA_SPECIFY_COLS)
			control.SetSelection(selected if 0 <= selected < len(options) else 0)
			sizer.Add(control, 0, wx.ALL, PAD)
			self._controls[key] = control
		for label, key, default in checks:
			control = wx.CheckBox(self, label=label)
			control.SetValue(bool(default))
			sizer.Add(control, 0, wx.ALL, PAD)
			self._controls[key] = control
		self.files = []
		if extra is not None:
			extra(self, sizer)  # extra controls, such as the Add Files button of a new release
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
		self.SetMinSize((580, -1))
		if self.GetSize().GetWidth() < 580:
			self.SetSize((580, self.GetSize().GetHeight()))
			self.Centre()

	def _on_char_hook(self, event):
		if event.ControlDown() and event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			self._on_ok(None)
		else:
			event.Skip()

	def _on_ok(self, event):
		values = {}
		for key, control in self._controls.items():
			if isinstance(control, (wx.Choice, wx.RadioBox)):
				values[key] = control.GetSelection()
			else:
				values[key] = control.GetValue()
		for key, (required, label) in self._required.items():
			if required and not str(values.get(key, "")).strip():
				name = strip_mnemonic(label).strip().rstrip(":").strip()
				ui.message(_("{field} is required.").format(field=name))
				self._controls[key].SetFocus()
				return
		values["files"] = list(self.files)
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


def ask_commit_message(parent, default):
	"""The commit message for a file change.

	By default no window opens and the suggested message is used, so saving, uploading, renaming and
	deleting need no typing. The window only appears when "Ask me for a commit message" is ticked in
	the add-on settings. Returns None if that window is cancelled."""
	if not get_pref("askCommitMessage"):
		return default
	return prompt_text(parent, _("Commit Message"), _("&Commit message:"), default)


def confirm_typed(parent, title, message, word=None):
	"""Guard for dangerous actions: a clear Yes/No question that starts on No.

	The old version asked the user to type the name ("Type ... to confirm: edit blank"), which was
	confusing with a screen reader. `word` is kept only so existing callers keep working.
	"""
	text = message.rstrip() + " " + _("Do you want to continue?")
	return confirm(parent, text, title, warning=True, default_no=True)


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
	         The label may be a function label(item) -> str, for a toggle button such as
	         Follow / Unfollow: it is called again whenever the selection changes or
	         refresh_actions() is called.
	on_select: optional fn(dialog, item) called when the selected item changes.
	choices: [(label, [options])] drop-downs; changing one reloads page 1.
	"""

	def __init__(self, parent, title, fetch, label, actions, empty=None, choices=None, info="",
				size=(780, 600), on_select=None):
		super().__init__(parent, title, size)
		self.fetch, self.label_fn, self.actions = fetch, label, actions
		self.on_select = on_select
		self._action_buttons = {}
		self._selected_key = None
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
		self.listbox.Bind(wx.EVT_LISTBOX, self._on_selection_changed)
		self.listbox.enter_action = self.run_default
		self.listbox.Bind(wx.EVT_KEY_DOWN, self._on_key)
		self.listbox.Bind(wx.EVT_CONTEXT_MENU, self._on_context_menu)
		sizer.Add(self.listbox, 1, wx.EXPAND | wx.ALL, PAD)

		row = wx.WrapSizer(wx.HORIZONTAL)
		for action in actions:
			if strip_mnemonic(self._action_text(action)).strip() == _("Open"):
				continue  # opening is Enter, double click or the Applications key
			button = self.add_button(row, self._action_text(action), lambda e, a=action: self.run_action(a))
			if callable(action[0]):
				self._action_buttons[id(action)] = (action, button)
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

	def _action_text(self, action, item=None):
		"""The label of an action: a plain string, or a function of the selected item."""
		return action[0](item) if callable(action[0]) else action[0]

	def refresh_actions(self):
		"""Update the labels of toggle buttons (Follow / Unfollow, Star / Unstar) for the selected item."""
		item = self.selected()
		changed = False
		for action, button in self._action_buttons.values():
			text = self._action_text(action, item)
			if button.GetLabel() != text:
				button.SetLabel(text)
				changed = True
		if changed:
			apply_access_keys(self)
			self.Layout()

	def _on_selection_changed(self, event):
		event.Skip()
		self._after_selection()

	def _after_selection(self):
		item = self.selected()
		key = self.listbox.GetSelection() if item is not None else None
		self.refresh_actions()
		if self.on_select is not None and item is not None and (key, id(item)) != self._selected_key:
			self._selected_key = (key, id(item))
			try:
				self.on_select(self, item)
			except Exception:
				log.error("GitHub for NVDA: selection handler failed", exc_info=True)

	def _on_context_menu(self, event):
		entries = [(self._action_text(a, self.selected()), lambda a=a: self.run_action(a)) for a in self.actions]
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
		self._selected_key = None
		if self.shown:
			self.listbox.SetSelection(index)
		self._after_selection()

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
			self.items = self._without_forgotten(self.items)
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

	def forget(self, item):
		"""Hide an item that was just deleted or marked done. GitHub can keep listing it for a few seconds."""
		key = item.get("id") if isinstance(item, dict) else None
		if key is not None:
			self.__dict__.setdefault("_forgotten", {})[key] = time.time()

	def _without_forgotten(self, items):
		forgotten = self.__dict__.get("_forgotten")
		if not forgotten:
			return items
		now = time.time()
		listed = {i.get("id") for i in items if isinstance(i, dict)}
		for key in list(forgotten):
			if key not in listed or now - forgotten[key] > 60:
				forgotten.pop(key, None)
		return [i for i in items if not (isinstance(i, dict) and i.get("id") in forgotten)]

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


def current_version():
	"""The installed version, read from manifest.ini (the only place the version number is written)."""
	try:
		return str(addonHandler.getCodeAddon().version)
	except Exception:
		log.debugWarning("GitHub for NVDA: could not read the add-on version", exc_info=True)
		return "unknown"


def whats_new_text():
	return _(
		"""What's new in GitHub for NVDA

This window opens the first time you open GitHub for NVDA after it is installed or updated, and the main menu opens right after you close it. Press Tab to reach the buttons. "Don't show again" hides this window until the next update. OK closes it for now, so it opens again the next time. Either way the main menu opens next. You can read this again whenever you like: use the command "Opens the What's New page" in NVDA menu, Preferences, Input gestures, GitHub for NVDA.


FILES AND FOLDERS

- The Delete key now deletes a selected folder too. Before, it only worked on files, and a folder could only be deleted from the Applications key menu. You are asked a Yes/No question first, and it starts on No.


MORE OPTIONS

- The Applications key menu on a file or folder no longer has a second "More Options" submenu. More Options is now in one place only: the "More Options" button, which you reach with Tab in the repository window.


Thank you for using GitHub for NVDA. Questions and bug reports: Mahadeesh18@gmail.com or https://github.com/Mahadeesh18/githubForNVDA"""
	)


class WhatsNewDialog(AccessibleDialog):
	"""Read-only text. Tab goes to "Don't show again", then OK."""

	def __init__(self, parent):
		super().__init__(parent, _("What's New in GitHub for NVDA"), (760, 600))
		self.dont_show = False
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(self, label=_("&What's new:")), 0, wx.ALL, 5)
		self.text = wx.TextCtrl(
			self, value=whats_new_text(), style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_BESTWRAP,
		)
		sizer.Add(self.text, 1, wx.EXPAND | wx.ALL, 5)
		row = wx.BoxSizer(wx.HORIZONTAL)
		never = wx.Button(self, label=_("&Don't show again"))
		never.Bind(wx.EVT_BUTTON, self._on_never)
		row.Add(never, 0, wx.ALL, 5)
		ok = wx.Button(self, wx.ID_OK, _("&OK"))
		ok.SetDefault()
		ok.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_OK))
		row.Add(ok, 0, wx.ALL, 5)
		sizer.Add(row, 0, wx.ALIGN_RIGHT)
		self.SetEscapeId(wx.ID_OK)
		self.layout(sizer, focus=self.text)
		self.text.SetInsertionPoint(0)

	def _on_never(self, event):
		self.dont_show = True
		self.EndModal(wx.ID_OK)


def show_whats_new(parent=None):
	"""Show the window. "Don't show again" remembers this version, so it stays hidden until the next update."""
	dialog = WhatsNewDialog(parent or gui.mainFrame)
	show_modal(dialog)
	if dialog.dont_show:
		set_pref("whatsNewSeenVersion", current_version())


def should_show():
	return get_pref("whatsNewSeenVersion") != current_version()
