# GitHub for NVDA
# Copyright (C) 2025-2026 Mahadeesh and contributors
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for more details.

"""Pure helper functions: no network, no GUI. Everything here can be unit tested outside NVDA."""

import addonHandler
import codecs
import os
import re
from collections import namedtuple
from datetime import datetime

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
