# GitHub for NVDA
# Copyright (C) 2025-2026 Mahadeesh and contributors
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for more details.

"""Profiles: your profile, other users, profile extras and GitHub account settings."""

import addonHandler
import re
import time
import ui
import wx
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from logHandler import log

from .logic import (
	format_datetime,
	last_page,
)
from .core import (
	Result,
	current_login,
	forget_identity,
	gh,
	gh_paged,
	has_token,
	items_per_page,
	open_url,
)
from .dialogs import (
	AccessibleDialog,
	FormDialog,
	PAD,
	PagedListDialog,
	TextViewDialog,
	apply_access_keys,
	ask_form,
	confirm,
	copy_to_clipboard,
	local_fetch,
	make_label,
	prompt_text,
	show_modal,
)
from .repotools import (
	q,
	toggle_bookmark,
)

addonHandler.initTranslation()


# ----------------------------------------------------------------------
# profileextras.py: The extra parts of My GitHub Profile: pinned repositories, contribution graph, organizations,
# ----------------------------------------------------------------------

_WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


# ----------------------------------------------------------------------
# GraphQL
# ----------------------------------------------------------------------

def graphql(query, variables=None):
	"""Run a GraphQL query. Returns a Result whose data is the "data" object."""
	res = gh("POST", "/graphql", body={"query": query, "variables": variables or {}})
	if not res.ok:
		return res
	payload = res.data if isinstance(res.data, dict) else {}
	data = payload.get("data")
	errors = payload.get("errors")
	if errors and not data:
		return Result(422, {"errors": errors}, res.headers)
	return Result(200, data or {}, res.headers)


_SUMMARY_FIELDS = """
  status { emoji message expiresAt indicatesLimitedAvailability }
  pinnedItems(first: 6, types: [REPOSITORY]) { totalCount }
"""

_PINNED_FIELDS = """
  pinnedItems(first: 6, types: [REPOSITORY]) {
    nodes {
      ... on Repository {
        nameWithOwner name description url isPrivate isFork isArchived
        stargazerCount forkCount
        primaryLanguage { name }
      }
    }
  }
"""

_CONTRIBUTIONS_FIELDS = """
  contributionsCollection {
    contributionCalendar {
      totalContributions
      weeks { contributionDays { date contributionCount weekday } }
    }
  }
"""


def profile_query(fields, login="", own=True):
	"""Query fields on your own profile (viewer) or on another user. Result data is that user's object."""
	if own:
		res = graphql("query { viewer { " + fields + " } }")
		key = "viewer"
	else:
		res = graphql("query($login: String!) { user(login: $login) { " + fields + " } }", {"login": login})
		key = "user"
	if not res.ok:
		return res
	return Result(200, res.data.get(key) or {}, res.headers)


_STATUS_MUTATION = """
mutation($emoji: String, $message: String, $expires: DateTime, $busy: Boolean) {
  changeUserStatus(input: {emoji: $emoji, message: $message, expiresAt: $expires, limitedAvailability: $busy}) {
    status { message emoji }
  }
}
"""


# ----------------------------------------------------------------------
# Loading the summary shown in the profile text
# ----------------------------------------------------------------------

def load_extras(login, plan_name="", own=True):
	"""Fetch everything the profile text needs. Any part that fails is None (shown as unavailable)."""

	def summary():
		res = profile_query(_SUMMARY_FIELDS, login, own)
		return res.data if res.ok else None

	def orgs():
		res = gh_paged("/user/orgs", max_items=500) if own else Result(0, None, {})
		if not res.ok and login:
			res = gh_paged(f"/users/{q(login)}/orgs", max_items=500)
		return res.data if res.ok and isinstance(res.data, list) else None

	def social():
		res = gh_paged("/user/social_accounts", max_items=200) if own else Result(0, None, {})
		if not res.ok and login:
			res = gh_paged(f"/users/{q(login)}/social_accounts", max_items=200)
		return res.data if res.ok and isinstance(res.data, list) else None

	def achievements():
		return fetch_achievements(login, plan_name)

	extras = {}
	jobs = {"summary": summary, "orgs": orgs, "social": social, "achievements": achievements}
	with ThreadPoolExecutor(max_workers=4) as pool:
		futures = {name: pool.submit(job) for name, job in jobs.items()}
		for name, future in futures.items():
			try:
				extras[name] = future.result()
			except Exception:
				extras[name] = None
	return extras


def count_text(value):
	return str(value) if value is not None else _("unavailable")


def status_text(summary):
	"""One line describing the profile status, or None when it could not be read."""
	if summary is None:
		return None
	status = summary.get("status")
	if not status or not (status.get("message") or status.get("emoji")):
		return _("not set")
	parts = [p for p in (status.get("emoji"), status.get("message")) if p]
	text = " ".join(parts)
	notes = []
	if status.get("indicatesLimitedAvailability"):
		notes.append(_("busy"))
	if status.get("expiresAt"):
		notes.append(_("clears {when}").format(when=format_datetime(status["expiresAt"])))
	return text + (" (" + ", ".join(notes) + ")" if notes else "")


def extra_rows(extras):
	"""Profile text rows, in the same order as the buttons that follow the profile text."""
	extras = extras or {}
	summary = extras.get("summary")
	pinned = ((summary or {}).get("pinnedItems") or {}).get("totalCount") if summary else None
	orgs = extras.get("orgs")
	badges = extras.get("achievements")
	social = extras.get("social")
	return [
		(_("Pinned Repositories"), count_text(pinned)),
		(_("Organizations"), count_text(len(orgs) if orgs is not None else None)),
		(_("Achievements and Badges"), count_text(len(badges) if badges is not None else None)),
		(_("Status"), status_text(summary) or _("unavailable")),
		(_("Social Media Links"), count_text(len(social) if social is not None else None)),
	]


# ----------------------------------------------------------------------
# Pinned repositories
# ----------------------------------------------------------------------

def _pinned_as_rest(node):
	"""Reshape a GraphQL repository so the normal repository list and actions can use it."""
	return {
		"full_name": node.get("nameWithOwner"), "name": node.get("name"), "html_url": node.get("url"),
		"description": node.get("description"), "private": node.get("isPrivate"),
		"fork": node.get("isFork"), "archived": node.get("isArchived"),
		"stargazers_count": node.get("stargazerCount", 0), "forks_count": node.get("forkCount", 0),
		"language": (node.get("primaryLanguage") or {}).get("name"),
	}


def show_pinned(parent, login="", own=True):
	from .repos import open_repo
	from .repos import repo_label

	def fetch(page, selections):
		res = profile_query(_PINNED_FIELDS, login, own)
		if not res.ok:
			return res
		nodes = ((res.data.get("pinnedItems") or {}).get("nodes")) or []
		return Result(200, [_pinned_as_rest(n) for n in nodes if n and n.get("nameWithOwner")], {})

	def bookmark(dlg, repo):
		ui.message(_("Bookmark added") if toggle_bookmark(repo["full_name"]) else _("Bookmark removed"))

	actions = [
		(_("&Open"), lambda d, r: open_repo(d, r["full_name"])),
		(_("Open in &Browser"), lambda d, r: open_url(r.get("html_url"))),
		(_("Add or Remove &Bookmark"), bookmark),
		(_("Copy &Link"), lambda d, r: copy_to_clipboard(r.get("html_url", ""), _("Link copied."))),
	]
	show_modal(PagedListDialog(
		parent, _("Pinned Repositories") if own else _("Pinned Repositories of {name}").format(name=login), fetch, repo_label, actions,
		empty=_("You have no pinned repositories. Pin repositories from your GitHub profile page.") if own else _("This user has no pinned repositories."),
	))


# ----------------------------------------------------------------------
# Contribution graph
# ----------------------------------------------------------------------

def _plural(count, one, many):
	return one if count == 1 else many


def contribution_text(calendar):
	"""The contribution graph as plain text: a summary, month totals and one line per week."""
	weeks = [w.get("contributionDays") or [] for w in calendar.get("weeks") or []]
	days = [d for week in weeks for d in week]
	total = calendar.get("totalContributions", 0)
	lines = [_("Contributions in the last year: {count}").format(count=total), ""]
	if not days:
		return "\n".join(lines)

	active = sum(1 for d in days if d["contributionCount"] > 0)
	lines.append(_("Days with contributions: {active} of {total}").format(active=active, total=len(days)))
	best = max(days, key=lambda d: d["contributionCount"])
	if best["contributionCount"] > 0:
		lines.append(_("Busiest day: {date}, {count} contributions").format(date=best["date"], count=best["contributionCount"]))

	longest = run = 0
	for d in days:
		run = run + 1 if d["contributionCount"] > 0 else 0
		longest = max(longest, run)
	current = 0
	for index, d in enumerate(reversed(days)):
		if d["contributionCount"] > 0:
			current += 1
		elif index == 0:
			continue  # today may not have a contribution yet
		else:
			break
	lines.append(_("Longest streak: {count} days").format(count=longest))
	lines.append(_("Current streak: {count} days").format(count=current))

	lines += ["", _("Contributions by month:")]
	months = {}
	for d in days:
		months[d["date"][:7]] = months.get(d["date"][:7], 0) + d["contributionCount"]
	for key, count in months.items():
		try:
			name = datetime.strptime(key, "%Y-%m").strftime("%B %Y")
		except ValueError:
			name = key
		lines.append(f"{name}: {count}")

	lines += ["", _("Contributions by week:")]
	for week in weeks:
		if not week:
			continue
		count = sum(d["contributionCount"] for d in week)
		detail = ", ".join(
			f"{_WEEKDAYS[d.get('weekday', 0) % 7]} {d['contributionCount']}" for d in week
		)
		lines.append(_("Week of {date}: {count} contributions. {detail}").format(date=week[0]["date"], count=count, detail=detail))
	return "\n".join(lines)


def show_contributions(parent, login, own=True):
	def work():
		return profile_query(_CONTRIBUTIONS_FIELDS, login, own)

	def done(res):
		if not res.ok:
			ui.message(_("Could not load the contribution graph.") + " " + res.message)
			return
		calendar = (res.data.get("contributionsCollection") or {}).get("contributionCalendar")
		if not calendar:
			ui.message(_("GitHub returned no contribution data."))
			return
		show_modal(TextViewDialog(
			parent, _("Contribution Graph"), contribution_text(calendar),
			buttons=[
				(_("Copy &Text"), lambda d: copy_to_clipboard(d.text.GetValue(), _("Contribution graph copied."))),
				(_("Open in &Browser"), lambda d: open_url(f"https://github.com/{login}")),
			],
			label=_("Contribution &graph:"), wrap=False,
		))

	ui.message(_("Loading the contribution graph..."))
	parent.run(work, done)


# ----------------------------------------------------------------------
# Organizations
# ----------------------------------------------------------------------

def show_organizations(parent, login, own=True):
	from .repos import show_repo_list

	def fetch(page, selections):
		params = {"per_page": items_per_page(), "page": page}
		res = gh("GET", "/user/orgs", params=params) if own else Result(0, None, {})
		if not res.ok and login:
			res = gh("GET", f"/users/{q(login)}/orgs", params=params)
		return res

	def label(org):
		description = org.get("description")
		return org.get("login", "") + (", " + description if description else "")

	def repos(dlg, org):
		show_repo_list(dlg, _("{name}'s repositories").format(name=org["login"]), f"/orgs/{q(org['login'])}/repos", {"sort": "updated"})

	actions = [
		(_("View &Profile"), lambda d, o: show_user(d, o["login"])),
		(_("&View Repositories"), repos),
		(_("Open in &Browser"), lambda d, o: open_url(f"https://github.com/{o['login']}")),
	]
	show_modal(PagedListDialog(
		parent, _("My Organizations") if own else _("Organizations of {name}").format(name=login), fetch, label, actions,
		empty=_("You are not a member of any organizations that are visible to this token.") if own else _("This user has no public organization memberships."),
	))


# ----------------------------------------------------------------------
# Achievements and badges
# ----------------------------------------------------------------------

_ACHIEVEMENT_LINK = re.compile(r"[?&;]achievement=([a-z0-9-]+)", re.I)
_ACHIEVEMENT_ALT = re.compile(r"""<img\b[^>]*?\balt\s*=\s*["']\s*Achievement:\s*([^"']+?)\s*["']""", re.I)
_ACHIEVEMENT_ANCHOR = re.compile(r"<a\b[^>]*?[?&;]achievement=([a-z0-9-]+)[^>]*>(.*?)</a>", re.I | re.S)
_TIER_LABEL = re.compile(r"""achievement-tier-label[^>]*>\s*x\s*(\d+)""", re.I)
_PRO_LABEL = re.compile(r"""class\s*=\s*["'][^"']*\bLabel\b[^"']*["'][^>]*>\s*Pro\s*<""", re.I)
_STAFF_LABEL = re.compile(r"""class\s*=\s*["'][^"']*\bLabel\b[^"']*["'][^>]*>\s*Staff\s*<""", re.I)
_ACHIEVEMENT_TITLE = re.compile(r"""\btitle\s*=\s*["']\s*Achievement:\s*([^"']+?)\s*["']""", re.I)
_ACHIEVEMENT_ASSET = re.compile(r"""githubassets\.com/assets/([a-z0-9-]+?)-default-[0-9a-f]{6,}\.(?:png|webp|svg)""", re.I)
_KNOWN_ACHIEVEMENTS = (
	"arctic-code-vault-contributor", "pull-shark", "starstruck", "quickdraw", "yolo", "galaxy-brain",
	"pair-extraordinaire", "public-sponsor", "open-sourcerer", "heart-on-your-sleeve", "highlight",
	"mars-2020-contributor", "security-bug-bounty-hunter",
)
_PROFILE_MARKERS = re.compile(r"""p-nickname|vcard-names|user-profile|og:type["']\s+content=["']profile""", re.I)
_ACHIEVEMENT_NAMES = {"yolo": "YOLO", "github-pro": "GitHub Pro"}

# GitHub serves the website to browsers. A bare API-style request is often refused or throttled, so the
# profile page is requested the way a browser would, still identifying the add-on.
_PAGE_HEADERS = {
	"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 GitHubForNVDA",
	"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
	"Accept-Language": "en-US,en;q=0.9",
	"X-GitHub-Api-Version": None,  # an API-only header: drop it for website requests
}


def achievement_title(slug):
	return _ACHIEVEMENT_NAMES.get(slug) or " ".join(word.capitalize() for word in slug.split("-"))


def _key(text):
	return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def parse_achievements(html):
	"""Achievements shown on a profile page, in page order, as a list of {"name", "tier"}.

	Reads the achievement links (?achievement=slug), the "Achievement: Name" picture text and the
	tier label (x2, x3...), so it keeps working if GitHub changes one of them.
	"""
	import html as htmllib

	html = html or ""
	found, order = {}, []

	def add(name, tier=None):
		name = " ".join(htmllib.unescape(name or "").split())
		key = _key(name)
		if not key:
			return
		if key not in found:
			found[key] = {"name": name, "tier": tier}
			order.append(key)
		elif tier and not found[key]["tier"]:
			found[key]["tier"] = tier

	# Position-ordered scan so the result follows the page.
	events = []
	for m in _ACHIEVEMENT_ANCHOR.finditer(html):
		inner = m.group(2)
		alt = _ACHIEVEMENT_ALT.search(inner)
		tier = _TIER_LABEL.search(inner)
		events.append((m.start(), alt.group(1) if alt else achievement_title(m.group(1)), tier.group(1) if tier else None))
	for m in _ACHIEVEMENT_ALT.finditer(html):
		events.append((m.start(), m.group(1), None))
	for m in _ACHIEVEMENT_TITLE.finditer(html):
		events.append((m.start(), m.group(1), None))
	for m in _ACHIEVEMENT_LINK.finditer(html):
		events.append((m.start(), achievement_title(m.group(1)), None))
	if not events:
		# GitHub may rename the links and the picture text: the picture files still carry the achievement name.
		for m in _ACHIEVEMENT_ASSET.finditer(html):
			if m.group(1).lower() in _KNOWN_ACHIEVEMENTS:
				events.append((m.start(), achievement_title(m.group(1).lower()), None))
	for _pos, name, tier in sorted(events, key=lambda e: e[0]):
		add(name, tier)
	return [found[k] for k in order]


def _looks_like_profile(html, login):
	return bool(_PROFILE_MARKERS.search(html)) or (login.lower() in html[:20000].lower() and "<html" in html[:2000].lower())


def _page_flags(html):
	"""The badges that are not achievements: GitHub Pro, Developer Program Member and GitHub Staff."""
	lower = html.lower()
	return {
		"pro": bool(_PRO_LABEL.search(html)),
		"developer": "developer program member" in lower or "github developer program" in lower,
		"staff": bool(_STAFF_LABEL.search(html)),
	}


def _read_page(login, params):
	"""One profile page, read the way a browser would. Returns (html or None, problem, stop).

	stop is True when there is no point trying another page (the profile does not exist)."""
	res = None
	for attempt in range(2):
		res = gh(
			"GET", f"https://github.com/{q(login)}", params=params, accept=_PAGE_HEADERS["Accept"],
			auth=False, headers=_PAGE_HEADERS, retries=2,
		)
		if res.code in (403, 429) and attempt == 0:
			time.sleep(2.0)  # GitHub's website throttles for a moment: wait once and ask again
			continue
		break
	log.debug("GitHub for NVDA: profile page %s %s answered %s", login, params or "", res.code)
	if res.code in (403, 429):
		return None, _("GitHub is limiting requests to its website right now. Try again in a minute."), False
	if res.code == 404:
		return None, _("GitHub does not have a profile page for {name}.").format(name=login), True
	if not res.ok or not isinstance(res.data, str):
		return None, (res.message if res.code else _("GitHub could not be reached.")), False
	if not _looks_like_profile(res.data, login):
		return None, _("GitHub returned a page that is not a profile."), False
	return res.data, "", False


def load_achievements(login, plan_name=""):
	"""Returns (items, problem). items is a list of {"name", "kind", "tier"} or None; problem says why.

	No token is needed: both pages are public. The main profile page is read first, because GitHub shows the
	achievements there for everybody, and the Achievements tab is read next only if that page showed none."""
	if not login:
		return None, _("No user name is known for this profile.")
	parsed, flags, loaded, problem = [], {}, False, ""
	for params in (None, {"tab": "achievements"}):
		html, why, stop = _read_page(login, params)
		if html is None:
			problem = why or problem
			if stop:
				return None, why
			continue
		loaded = True
		flags = {k: flags.get(k, False) or v for k, v in _page_flags(html).items()}
		parsed = parse_achievements(html)
		if parsed:
			break
	if not loaded:
		return None, problem or _("GitHub's achievements page could not be read.")
	items = [{"name": a["name"], "kind": _("achievement"), "tier": a["tier"]} for a in parsed]
	if flags.get("pro") or (plan_name or "").lower() == "pro":
		items.append({"name": _("GitHub Pro"), "kind": _("badge"), "tier": None})
	if flags.get("developer"):
		items.append({"name": _("Developer Program Member"), "kind": _("badge"), "tier": None})
	if flags.get("staff"):
		items.append({"name": _("GitHub Staff"), "kind": _("badge"), "tier": None})
	return items, ""


def fetch_achievements(login, plan_name=""):
	"""Achievements and badges as a list of {"name", "kind", "tier"}, or None when they cannot be read.

	GitHub offers no API for these, so the public profile page is read.
	"""
	return load_achievements(login, plan_name)[0]


def achievement_label(item):
	parts = [item["name"], item["kind"]]
	if item.get("tier"):
		parts.append(_("level {tier}").format(tier=item["tier"]))
	return ", ".join(parts)


class _Problem(Result):
	"""A failed result that speaks its own explanation instead of a generic network error."""

	__slots__ = ()

	@property
	def message(self):
		return self.data


def show_achievements(parent, login, plan_name=""):
	def fetch(page, selections):
		items, problem = load_achievements(login, plan_name)
		if items is None:
			return _Problem(0, problem, {})
		return local_fetch(items)(page, selections)

	actions = [
		(_("Open in &Browser"), lambda d, a: open_url(f"https://github.com/{login}?tab=achievements")),
		(_("Copy &Name"), lambda d, a: copy_to_clipboard(a["name"], _("Name copied."))),
	]
	show_modal(PagedListDialog(
		parent, _("Achievements and Badges"), fetch, achievement_label, actions,
		empty=_("No achievements or badges were found. Earn them on GitHub by contributing."),
		info=_("GitHub has no API for achievements, so this list is read from the public profile page. Use Open in Browser if it looks incomplete."),
	))


# ----------------------------------------------------------------------
# Status
# ----------------------------------------------------------------------

def clear_after_options():
	"""(label, rule) pairs for the 'clear status after' drop-down. Built on demand so labels are translated."""
	return [
		(_("Never"), None),
		(_("30 minutes"), timedelta(minutes=30)),
		(_("1 hour"), timedelta(hours=1)),
		(_("4 hours"), timedelta(hours=4)),
		(_("Today"), "today"),
		(_("This week"), "week"),
	]


def expiry_time(choice, now=None):
	"""UTC time for a 'clear after' choice as GitHub expects it, or None for never."""
	options = clear_after_options()
	rule = options[choice][1] if 0 <= choice < len(options) else None
	if rule is None:
		return None
	now = now or datetime.now().astimezone()
	if rule == "today":
		moment = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
	elif rule == "week":
		moment = (now + timedelta(days=7 - ((now.weekday() + 1) % 7))).replace(hour=0, minute=0, second=0, microsecond=0)
	else:
		moment = now + rule
	return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def edit_status(parent, summary, done_callback):
	"""Ask for a new status and save it. done_callback() runs after a successful save."""
	current = (summary or {}).get("status") or {}
	dlg = FormDialog(
		parent, _("Edit Status"),
		[
			(_("Status &message (leave this and the emoji empty to clear your status):"), "message", False, current.get("message") or ""),
			(_("&Emoji (a short code such as :palm_tree: or an emoji character):"), "emoji", False, current.get("emoji") or ""),
		],
		checks=[(_("I am &busy (shows limited availability)"), "busy", bool(current.get("indicatesLimitedAvailability")))],
		choices=[(_("Clear status &after:"), "expires", [c[0] for c in clear_after_options()], 0)],
		ok_label=_("&Save Status"),
	)
	values = None
	try:
		if dlg.ShowModal() == wx.ID_OK:
			values = dlg.values
	finally:
		dlg.Destroy()
	if values is None:
		return
	message = (values.get("message") or "").strip()
	emoji = (values.get("emoji") or "").strip()
	clearing = not message and not emoji
	variables = {
		"message": message or None, "emoji": emoji or None,
		"expires": None if clearing else expiry_time(values.get("expires", 0)),
		"busy": False if clearing else bool(values.get("busy")),
	}

	def work():
		return graphql(_STATUS_MUTATION, variables)

	def done(res):
		if res.ok:
			ui.message(_("Status cleared.") if clearing else _("Status updated."))
			done_callback()
		else:
			ui.message(_("Could not update your status.") + " " + res.message + " " + _("This needs a token with the user permission."))

	ui.message(_("Saving your status..."))
	parent.run(work, done)


# ----------------------------------------------------------------------
# Social media links
# ----------------------------------------------------------------------

def social_label(account):
	provider = account.get("provider") or "generic"
	name = _("Link") if provider == "generic" else provider.capitalize()
	return f"{name}: {account.get('url', '')}"


def show_social_links(parent, login, own=True):
	def fetch(page, selections):
		params = {"per_page": items_per_page(), "page": page}
		res = gh("GET", "/user/social_accounts", params=params) if own else Result(0, None, {})
		if not res.ok and login:
			res = gh("GET", f"/users/{q(login)}/social_accounts", params=params)
		return res

	actions = [
		(_("Open in &Browser"), lambda d, a: open_url(a.get("url"))),
		(_("Copy &Link"), lambda d, a: copy_to_clipboard(a.get("url", ""), _("Link copied."))),
	]
	show_modal(PagedListDialog(
		parent, _("Social Media Links") if own else _("Social Media Links of {name}").format(name=login), fetch, social_label, actions,
		empty=_("You have no social media links. Use Edit Social Media Links in your profile to add some.") if own else _("This user has no social media links."),
	))


def edit_social_links(parent, accounts, done_callback):
	"""Edit the list of social media links as text, one web address per line."""
	if accounts is None:
		ui.message(_("Your social media links could not be loaded, so they cannot be edited now. Reload your profile and try again."))
		return
	old_urls = [a.get("url", "") for a in accounts if a.get("url")]
	dlg = FormDialog(
		parent, _("Edit Social Media Links"),
		[(_("Social media &links, one web address per line (GitHub allows up to four). Remove a line to delete that link:"), "links", True, "\n".join(old_urls))],
		ok_label=_("&Save Links"),
	)
	values = None
	try:
		if dlg.ShowModal() == wx.ID_OK:
			values = dlg.values
	finally:
		dlg.Destroy()
	if values is None:
		return
	new_urls = []
	for line in (values.get("links") or "").splitlines():
		line = line.strip()
		if not line:
			continue
		if not re.match(r"^https?://", line, re.IGNORECASE):
			line = "https://" + line
		if line not in new_urls:
			new_urls.append(line)
	to_delete = [u for u in old_urls if u not in new_urls]
	to_add = [u for u in new_urls if u not in old_urls]
	if not to_delete and not to_add:
		ui.message(_("No changes to save."))
		return

	def work():
		if to_delete:
			res = gh("DELETE", "/user/social_accounts", body={"account_urls": to_delete})
			if not res.ok:
				return res
		if to_add:
			res = gh("POST", "/user/social_accounts", body={"account_urls": to_add})
			if not res.ok:
				return res
		return Result(200, None, {})

	def done(res):
		if res.ok:
			ui.message(_("Social media links updated."))
		else:
			ui.message(_("Could not update your social media links.") + " " + res.message + " " + _("This needs a token with the user permission."))
		done_callback()

	ui.message(_("Saving your social media links..."))
	parent.run(work, done)


# ----------------------------------------------------------------------
# profile.py: Your own GitHub profile: view, edit, starred repositories, followers.
# ----------------------------------------------------------------------

_USERNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")


def valid_username(name):
	return bool(_USERNAME.fullmatch(name or ""))


def public_email(p, emails=None):
	"""The email address visible on the profile.

	GET /user only reports the address chosen as the public email. When the primary address was made public with the
	email visibility setting, /user can still leave it out, so the email list is checked as well.
	"""
	if p.get("email"):
		return p["email"]
	for entry in emails or []:
		if entry.get("visibility") == "public" and entry.get("email"):
			return entry["email"]
	return ""


def profile_text(p, starred, emails=None, extras=None):
	plan = ((p.get("plan") or {}).get("name") or _("free")).capitalize()
	rows = [
		(_("Username"), p.get("login")), (_("Account type"), p.get("type", "User")),
		(_("Name"), p.get("name") or _("not set")),
		(_("Email"), public_email(p, emails) or _("not public (use Edit Email to make it public)")),
		(_("Bio"), p.get("bio") or _("not set")),
		(_("Company"), p.get("company") or _("not set")),
		(_("Location"), p.get("location") or _("not set")),
		(_("Website"), p.get("blog") or _("not set")),
		(_("Public repositories"), p.get("public_repos", 0)),
		(_("Private repositories"), p.get("total_private_repos", p.get("owned_private_repos", 0))),
		(_("Starred repositories"), starred),
		(_("Public gists"), p.get("public_gists", 0)), (_("Private gists"), p.get("private_gists", 0)),
		(_("Followers"), p.get("followers", 0)), (_("Following"), p.get("following", 0)),
	] + extra_rows(extras) + [
		(_("GitHub plan"), plan), (_("Account created"), format_datetime(p.get("created_at"))),
	]
	return "\n".join(f"{k}: {v}" for k, v in rows)


class EmailVisibilityDialog(AccessibleDialog):
	"""Confirmation with a Cancel button and one button that flips email visibility."""

	def __init__(self, parent, message, action_label):
		super().__init__(parent, _("Edit Email"), (460, -1), resizable=False)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(make_label(self, message, wrap=420), 0, wx.ALL, PAD)
		row = wx.BoxSizer(wx.HORIZONTAL)
		cancel = wx.Button(self, wx.ID_CANCEL, _("&Cancel"))
		action = wx.Button(self, wx.ID_OK, action_label)
		action.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_OK))
		row.Add(cancel, 0, wx.ALL, PAD)
		row.Add(action, 0, wx.ALL, PAD)
		sizer.Add(row, 0, wx.ALIGN_CENTER)
		cancel.SetDefault()
		self.layout(sizer, fit=True, focus=cancel)


class ProfileDialog(AccessibleDialog):
	def __init__(self, parent):
		super().__init__(parent, _("My GitHub Profile"), (600, 580))
		self.profile, self.extras = {}, {}
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(self, label=_("Profile &details:")), 0, wx.ALL, PAD)
		self.text = wx.TextCtrl(self, value=_("Loading..."), style=wx.TE_MULTILINE | wx.TE_READONLY)
		sizer.Add(self.text, 1, wx.EXPAND | wx.ALL, PAD)
		row = wx.WrapSizer(wx.HORIZONTAL)
		# These come first, in the same order as their lines in the profile text, so Tab from the text reaches them.
		self.add_button(row, _("View &Pinned Repositories"), lambda e: show_pinned(self))
		self.add_button(row, _("View Contribution &Graph"), lambda e: show_contributions(self, self.profile.get("login", "")))
		self.add_button(row, _("View Organi&zations"), lambda e: show_organizations(self, self.profile.get("login", "")))
		self.add_button(row, _("View &Achievements and Badges"), self.on_achievements)
		self.add_button(row, _("Edit Sta&tus"), self.on_status)
		self.add_button(row, _("View Social &Media Links"), lambda e: show_social_links(self, self.profile.get("login", "")))
		self.add_button(row, _("Change &Username"), self.on_username)
		self.add_button(row, _("Edit &Name"), lambda e: self.edit("name", _("Name")))
		self.add_button(row, _("Edit &Bio"), lambda e: self.edit("bio", _("Bio"), True))
		self.add_button(row, _("Edit Compan&y"), lambda e: self.edit("company", _("Company")))
		self.add_button(row, _("Edit &Location"), lambda e: self.edit("location", _("Location")))
		self.add_button(row, _("Edit &Website"), lambda e: self.edit("blog", _("Website")))
		self.add_button(row, _("Edit &Email"), self.on_email)
		self.add_button(row, _("Edit Social Media Lin&ks"), self.on_edit_social)
		self.add_button(row, _("&Starred Repositories"), self.on_starred)
		self.add_button(row, _("&Followers"), lambda e: self.users("followers", _("Followers")))
		self.add_button(row, _("F&ollowing"), lambda e: self.users("following", _("Following")))
		self.add_button(row, _("Copy &Details"), lambda e: copy_to_clipboard(self.text.GetValue(), _("Profile details copied.")))
		self.add_button(row, _("Copy Lin&k"), self.on_copy_link)
		row.Add(self.close_button(), 0, wx.ALL, 3)
		sizer.Add(row, 0, wx.ALIGN_CENTER)
		self.layout(sizer, focus=self.text)
		self.load()

	def load(self):
		ui.message(_("Loading profile..."))

		def work():
			me = gh("GET", "/user")
			if not me.ok:
				return me
			stars = gh("GET", "/user/starred", params={"per_page": 1})
			fallback = len(stars.data) if stars.ok and isinstance(stars.data, list) else 0
			# Needs the user:email permission; without it the email falls back to what /user reports.
			emails = gh("GET", "/user/emails")
			listed = emails.data if emails.ok and isinstance(emails.data, list) else []
			plan = ((me.data.get("plan") or {}).get("name") or "") if isinstance(me.data, dict) else ""
			extras = load_extras(me.data.get("login", "") if isinstance(me.data, dict) else "", plan)
			return Result(200, (me.data, last_page(stars.headers, fallback), listed, extras), {})

		def done(res):
			if not res.ok:
				self.text.SetValue(_("Could not load your profile.") + " " + res.message)
				ui.message(res.message)
				return
			self.profile, starred, emails, self.extras = res.data
			self.text.SetValue(profile_text(self.profile, starred, emails, self.extras))
			self.text.SetInsertionPoint(0)
			self.text.SetFocus()
			ui.message(_("Profile loaded"))

		self.run(work, done)

	def edit(self, key, label, multiline=False):
		new = prompt_text(self, _("Edit {label}").format(label=label), _("New value:"), self.profile.get(key) or "", multiline)
		if new is None:
			return

		def done(res):
			if res.ok:
				ui.message(_("{label} updated.").format(label=label))
				self.load()
			else:
				ui.message(_("Could not update {label}.").format(label=label) + " " + res.message)

		self.run(lambda: gh("PATCH", "/user", body={key: new.strip()}), done)

	def on_username(self, event):
		"""GitHub's API cannot rename an account: check the name, then go to GitHub's settings."""
		current = self.profile.get("login", "")
		if not current:
			ui.message(_("Your profile has not loaded yet."))
			return
		new = prompt_text(
			self, _("Change Username"), _("Your username is {name}. &New username you want:").format(name=current), current
		)
		if new is None:
			return
		new = new.strip().lstrip("@")
		if new == current:
			ui.message(_("That is already your username."))
			return
		if not valid_username(new):
			ui.message(_("Usernames may only contain letters, numbers and single hyphens, cannot start or end with a hyphen, and can be at most 39 characters."))
			return

		def done(res):
			case_only = new.lower() == current.lower()
			if res.code == 200 and not case_only:
				ui.message(_("The username {name} is already taken. Try another one.").format(name=new))
				return
			if res.code not in (404, 200):
				ui.message(_("Could not check the username.") + " " + res.message)
				return
			message = _("GitHub only allows a username to be changed on its website, so this add-on cannot do it directly. Open GitHub's account settings now? Look for 'Change username' on that page.\n\nNote: links to your old username will stop working, and your token stays valid.")
			head = _("Only the capitalisation changes ({old} to {new}).") if case_only else _("The username {new} appears to be available.")
			if confirm(self, head.format(old=current, new=new) + "\n\n" + message, _("Change Username")):
				copy_to_clipboard(new, _("Opening GitHub settings. {name} copied to the clipboard.").format(name=new))
				open_url("https://github.com/settings/admin")

		ui.message(_("Checking whether that username is free..."))
		self.run(lambda: gh("GET", f"/users/{q(new)}", auth=False), done)

	def on_email(self, event):
		"""Show whether the primary email address is public, and offer to flip it."""
		ui.message(_("Checking your email visibility..."))

		def done(res):
			if not res.ok or not isinstance(res.data, list):
				ui.message(_("Could not read your email settings.") + " " + res.message + " " + _("This needs the user:email permission on your token."))
				return
			primary = next((e for e in res.data if e.get("primary")), None)
			if not primary:
				ui.message(_("No primary email address was found."))
				return
			address = primary.get("email", "")
			is_public = primary.get("visibility") == "public"
			message = (
				_("Your primary email address {email} is currently public. Everyone can see it on your profile. Make it private?")
				if is_public else
				_("Your primary email address {email} is currently private. Make it public so everyone can see it on your profile?")
			).format(email=address)
			label = _("Make &Private") if is_public else _("Make &Public")
			if not show_modal(EmailVisibilityDialog(self, message, label)) == wx.ID_OK:
				ui.message(_("Email visibility unchanged."))
				return
			target = "private" if is_public else "public"

			def changed(result):
				if result.ok:
					ui.message(_("Your email address is now private.") if target == "private" else _("Your email address is now public."))
					self.load()
				else:
					ui.message(_("Could not change email visibility.") + " " + result.message + " " + _("This needs the user:email permission on your token."))

			self.run(lambda: gh("PATCH", "/user/email/visibility", body={"visibility": target}), changed)

		self.run(lambda: gh("GET", "/user/emails"), done)

	def on_achievements(self, event):
		plan = (self.profile.get("plan") or {}).get("name") or ""
		show_achievements(self, self.profile.get("login", ""), plan)

	def on_edit_social(self, event):
		if not self.profile:
			ui.message(_("Your profile has not loaded yet."))
			return
		edit_social_links(self, self.extras.get("social"), self.load)

	def on_status(self, event):
		if not self.profile:
			ui.message(_("Your profile has not loaded yet."))
			return
		edit_status(self, self.extras.get("summary"), self.load)

	def on_copy_link(self, event):
		login = self.profile.get("login", "")
		link = self.profile.get("html_url") or (f"https://github.com/{login}" if login else "")
		if not link:
			ui.message(_("Your profile has not loaded yet."))
			return
		copy_to_clipboard(link, _("Link copied."))

	def on_starred(self, event):
		from .repos import show_repo_list
		show_repo_list(self, _("Starred Repositories"), "/user/starred", {"sort": "created"}, _("You have not starred any repositories."))

	def users(self, kind, title):
		show_user_list(self, self.profile.get("login", ""), kind, title)


def show_profile(parent=None):
	show_modal(ProfileDialog(parent))
	forget_identity()


# ----------------------------------------------------------------------
# users.py: Other people's profiles, follower lists and following.
# ----------------------------------------------------------------------

def user_text(u, extras=None, starred=None):
	kind = u.get("type", "User")
	rows = [
		(_("Username"), u.get("login")),
		(_("Account type"), kind),
		(_("Name"), u.get("name") or _("not set")),
		(_("Email"), u.get("email") or _("not public")),
		(_("Bio"), u.get("bio") or u.get("description") or _("not set")),
		(_("Company"), u.get("company") or _("not set")),
		(_("Location"), u.get("location") or _("not set")),
		(_("Website"), u.get("blog") or _("not set")),
	]
	if u.get("twitter_username"):
		rows.append((_("X (Twitter) username"), u["twitter_username"]))
	if u.get("hireable") is not None:
		rows.append((_("Available for hire"), _("yes") if u["hireable"] else _("no")))
	rows += [
		(_("Public repositories"), u.get("public_repos", 0)),
		(_("Public gists"), u.get("public_gists", 0)),
		(_("Followers"), u.get("followers", 0)),
		(_("Following"), u.get("following", 0)),
	]
	if starred is not None:
		rows.append((_("Starred repositories"), starred))
	if extras is not None:
		rows += extra_rows(extras)
	rows += [
		(_("Account created"), format_datetime(u.get("created_at"))),
		(_("Profile link"), u.get("html_url")),
	]
	return "\n".join(f"{k}: {v}" for k, v in rows)


def show_user_list(parent, username, kind, title):
	"""Followers or following of a user."""
	from .repos import show_repo_list

	def fetch(page, selections):
		return gh("GET", f"/users/{q(username)}/{kind}", params={"per_page": items_per_page(), "page": page})

	def repos(dlg, u):
		show_repo_list(dlg, _("{name}'s repositories").format(name=u["login"]), f"/users/{q(u['login'])}/repos", {"sort": "updated"})

	def profile(dlg, u):
		show_user(dlg, u["login"])

	def follow(dlg, u):
		if not has_token():
			ui.message(_("Set your token first to follow people."))
			return

		def done(res):
			ui.message(_("Now following {name}.").format(name=u["login"]) if res.ok else _("Could not follow.") + " " + res.message)

		dlg.run(lambda: gh("PUT", f"/user/following/{q(u['login'])}"), done)

	actions = [
		(_("View &Profile"), profile),
		(_("&View Repositories"), repos),
		(_("&Follow"), follow),
		(_("Open in &Browser"), lambda d, u: open_url(u.get("html_url"))),
	]
	show_modal(PagedListDialog(parent, title, fetch, lambda u: u["login"], actions, empty=_("No users found.")))


class UserDialog(AccessibleDialog):
	"""Profile of any user or organization."""

	def __init__(self, parent, login):
		super().__init__(parent, _("Profile: {name}").format(name=login), (600, 520))
		self.login, self.user = login, {}
		self.following = None
		self.starred = None
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(self, label=_("Profile &details:")), 0, wx.ALL, PAD)
		self.text = wx.TextCtrl(self, value=_("Loading..."), style=wx.TE_MULTILINE | wx.TE_READONLY)
		sizer.Add(self.text, 1, wx.EXPAND | wx.ALL, PAD)
		row = wx.WrapSizer(wx.HORIZONTAL)
		self.extras = None
		# Same order as the extra lines in the profile text, so Tab from the text reaches them in turn.
		self.btn_pinned = self.add_button(row, _("View &Pinned Repositories"), lambda e: show_pinned(self, self.login, own=False))
		self.btn_graph = self.add_button(row, _("View Contribution &Graph"), lambda e: show_contributions(self, self.login, own=False))
		self.btn_orgs = self.add_button(row, _("View Organi&zations"), lambda e: show_organizations(self, self.login, own=False))
		self.btn_badges = self.add_button(row, _("View &Achievements and Badges"), lambda e: show_achievements(self, self.login))
		self.btn_social = self.add_button(row, _("View Social &Media Links"), lambda e: show_social_links(self, self.login, own=False))
		self.add_button(row, _("&Repositories"), self.on_repos)
		self.btn_starred = self.add_button(row, _("Starred Repositor&ies"), self.on_starred)
		self.add_button(row, _("&Followers"), lambda e: show_user_list(self, self.login, "followers", _("Followers of {name}").format(name=self.login)))
		self.add_button(row, _("F&ollowing"), lambda e: show_user_list(self, self.login, "following", _("{name} is following").format(name=self.login)))
		self.btn_follow = self.add_button(row, _("Fol&low"), self.on_follow)
		self.btn_follow.Enable(False)
		self.add_button(row, _("Copy &Link"), lambda e: copy_to_clipboard(self.user.get("html_url", ""), _("Link copied.")))
		self.add_button(row, _("Open in &Browser"), lambda e: open_url(f"https://github.com/{self.login}"))
		row.Add(self.close_button(), 0, wx.ALL, 3)
		sizer.Add(row, 0, wx.ALIGN_CENTER)
		self.layout(sizer, focus=self.text)
		self.load()

	def load(self):
		ui.message(_("Loading profile..."))

		def work():
			res = gh("GET", f"/users/{q(self.login)}")
			if res.ok and res.data.get("type") == "User":
				self.extras = load_extras(self.login, "", own=False)
				stars = gh("GET", f"/users/{q(self.login)}/starred", params={"per_page": 1})
				if stars.ok and isinstance(stars.data, list):
					self.starred = last_page(stars.headers, len(stars.data))
				if has_token():
					code = gh("GET", f"/user/following/{q(self.login)}").code
					self.following = (code == 204) if code in (204, 404) else None
			return res

		def done(res):
			if not res.ok:
				self.text.SetValue(_("Could not load the profile.") + " " + res.message)
				ui.message(res.message)
				return
			self.user = res.data
			is_user = self.user.get("type") == "User"
			for button in (self.btn_pinned, self.btn_graph, self.btn_orgs, self.btn_badges, self.btn_social):
				button.Enable(is_user)  # organizations have none of these
			self.btn_starred.Enable(is_user)  # organizations do not star repositories
			self.text.SetValue(user_text(self.user, self.extras if is_user else None, self.starred if is_user else None))
			self.text.SetInsertionPoint(0)
			if self.following is not None:
				self.btn_follow.Enable(True)
				self._set_follow_label()
			self.text.SetFocus()
			ui.message(_("Profile loaded"))

		self.run(work, done)

	def _set_follow_label(self):
		"""Rename the Follow button and keep every Alt+letter in the window unique."""
		self.btn_follow.SetLabel(_("Unfol&low") if self.following else _("Fol&low"))
		apply_access_keys(self)

	def on_repos(self, event):
		from .repos import show_repo_list
		show_repo_list(self, _("{name}'s repositories").format(name=self.login), f"/users/{q(self.login)}/repos", {"sort": "updated"})

	def on_starred(self, event):
		from .repos import show_repo_list
		show_repo_list(
			self, _("Starred by {name}").format(name=self.login), f"/users/{q(self.login)}/starred",
			{"sort": "created"}, _("{name} has not starred any repositories.").format(name=self.login),
		)

	def on_follow(self, event):
		method = "DELETE" if self.following else "PUT"

		def done(res):
			if res.ok:
				self.following = not self.following
				self._set_follow_label()
				ui.message(_("Now following {name}.") .format(name=self.login) if self.following else _("Unfollowed {name}.").format(name=self.login))
			else:
				ui.message(_("That did not work.") + " " + res.message)

		self.run(lambda: gh(method, f"/user/following/{q(self.login)}"), done)


def show_user(parent, login):
	show_modal(UserDialog(parent, login))


# ----------------------------------------------------------------------
# accountsettings.py: GitHub Settings: the account settings of github.com, as lists and forms you can use with a screen reader.
# ----------------------------------------------------------------------

SETTINGS_URL = "https://github.com/settings"


def _need(permission):
	return " " + _("This needs a token with the {name} permission.").format(name=permission)


def _page_params(page, **extra):
	params = {"per_page": items_per_page(), "page": page}
	params.update(extra)
	return params


def _show_list(parent, title, fetch, label, actions, empty, info=""):
	show_modal(PagedListDialog(parent, title, fetch, label, actions, empty=empty, info=info))


def _do(dlg, work, ok_message, fail_message, permission="", after=None):
	"""Run one change on a worker thread, say what happened and refresh the list."""

	def done(res):
		if res.ok:
			ui.message(ok_message)
			if after:
				after()
			else:
				dlg.reload()
		else:
			ui.message(fail_message + " " + res.message + (_need(permission) if permission and res.code in (403, 404) else ""))

	dlg.run(work, done)


# ======================================================================
# Public profile
# ======================================================================

def edit_public_profile(parent):
	"""Everything on github.com/settings/profile that GitHub's API can change, in one form."""
	ui.message(_("Loading your profile settings..."))

	def work():
		me = gh("GET", "/user")
		if not me.ok:
			return me
		emails = gh("GET", "/user/emails")
		listed = emails.data if emails.ok and isinstance(emails.data, list) else []
		return Result(200, (me.data, listed), {})

	def done(res):
		if not res.ok:
			ui.message(_("Could not load your profile.") + " " + res.message)
			return
		profile, emails = res.data
		verified = [e["email"] for e in emails if e.get("verified") and e.get("email")]
		options = [_("Do not show my email address")] + verified
		current = profile.get("email") or ""
		selected = options.index(current) if current in options else 0
		values = ask_form(
			parent, _("Public Profile Settings"),
			[
				(_("&Name:"), "name", False, profile.get("name") or ""),
				(_("&Bio:"), "bio", True, profile.get("bio") or ""),
				(_("Compan&y:"), "company", False, profile.get("company") or ""),
				(_("&Location:"), "location", False, profile.get("location") or ""),
				(_("&Website:"), "blog", False, profile.get("blog") or ""),
				(_("&X (Twitter) username:"), "twitter_username", False, profile.get("twitter_username") or ""),
			],
			choices=[(_("Public &email address:"), "email", options, selected)] if emails else (),
			checks=[(_("Available for &hire"), "hireable", bool(profile.get("hireable")))],
			ok_label=_("&Save Settings"),
		)
		if values is None:
			return
		body = {}
		for key in ("name", "bio", "company", "location", "blog", "twitter_username"):
			new = (values.get(key) or "").strip()
			if new != (profile.get(key) or ""):
				body[key] = new
		if bool(values.get("hireable")) != bool(profile.get("hireable")):
			body["hireable"] = bool(values.get("hireable"))
		if emails:
			choice = options[values.get("email", 0)] if 0 <= values.get("email", 0) < len(options) else ""
			wanted = "" if choice == options[0] else choice
			if wanted != current:
				body["email"] = wanted
		if not body:
			ui.message(_("No changes to save."))
			return

		def saved(result):
			if result.ok:
				ui.message(_("Public profile settings saved."))
			else:
				ui.message(_("Could not save your profile settings.") + " " + result.message + _need("user"))

		ui.message(_("Saving..."))
		parent.run(lambda: gh("PATCH", "/user", body=body), saved)

	parent.run(work, done)


# ======================================================================
# Emails
# ======================================================================

def email_label(e):
	parts = [e.get("email", "")]
	if e.get("primary"):
		parts.append(_("primary"))
	parts.append(_("verified") if e.get("verified") else _("not verified"))
	if e.get("visibility"):
		parts.append(_("public") if e.get("visibility") == "public" else _("private"))
	return ", ".join(parts)


def show_emails(parent):
	def fetch(page, selections):
		return gh("GET", "/user/emails", params=_page_params(page))

	def add(dlg, _item):
		address = ask_form(dlg, _("Add Email Address"), [(_("&Email address to add:"), "email", False, "", True)], ok_label=_("&Add"))
		if not address:
			return
		new = address["email"].strip()
		_do(dlg, lambda: gh("POST", "/user/emails", body={"emails": [new]}),
			_("{email} added. GitHub sent a message to that address: open it to verify the address.").format(email=new),
			_("Could not add the email address."), "user:email")

	def delete(dlg, e):
		if e.get("primary"):
			ui.message(_("The primary email address cannot be deleted. Make another address primary on GitHub first."))
			return
		if confirm(dlg, _("Delete the email address {email}?").format(email=e["email"]), _("Delete Email Address"), warning=True, default_no=True):
			_do(dlg, lambda: gh("DELETE", "/user/emails", body={"emails": [e["email"]]}),
				_("Email address deleted."), _("Could not delete the email address."), "user:email")

	def visibility(dlg, e):
		if not e.get("primary"):
			ui.message(_("GitHub lets you choose the visibility of the primary email address only."))
			return
		target = "private" if e.get("visibility") == "public" else "public"
		_do(dlg, lambda: gh("PATCH", "/user/email/visibility", body={"visibility": target}),
			_("Your primary email address is now {state}.").format(state=_(target)),
			_("Could not change the email visibility."), "user:email")

	actions = [
		(_("&Add Email"), add, False),
		(_("&Delete Email"), delete),
		(_("Make Primary Public or Pri&vate"), visibility),
		(_("Make &Primary on GitHub"), lambda d, e: open_url(SETTINGS_URL + "/emails")),
		(_("&Copy Address"), lambda d, e: copy_to_clipboard(e.get("email", ""), _("Address copied."))),
	]
	_show_list(parent, _("Email Addresses"), fetch, email_label, actions,
		_("You have no email addresses."),
		_("Making another address primary and the options 'Keep my email addresses private' and 'Block command line pushes that expose my email' are only available on GitHub's website. Use Make Primary on GitHub."))


# ======================================================================
# SSH keys, signing keys and GPG keys
# ======================================================================

def _key_kinds():
	return {
		"ssh": {
			"title": _("SSH Keys"), "path": "/user/keys", "permission": "write:public_key",
			"label": lambda k: _("{title}, {type}, added {date}").format(
				title=k.get("title") or _("untitled"), type=(k.get("key") or "").split(" ")[0] or _("key"),
				date=format_datetime(k.get("created_at"))),
			"fields": [(_("&Title:"), "title", False, "", True), (_("&Key (starts with ssh-ed25519, ssh-rsa or ecdsa-):"), "key", True, "", True)],
			"body": lambda v: {"title": v["title"].strip(), "key": v["key"].strip()},
			"copy": lambda k: k.get("key", ""),
			"empty": _("You have no SSH keys."),
		},
		"signing": {
			"title": _("SSH Signing Keys"), "path": "/user/ssh_signing_keys", "permission": "admin:ssh_signing_key",
			"label": lambda k: _("{title}, {type}, added {date}").format(
				title=k.get("title") or _("untitled"), type=(k.get("key") or "").split(" ")[0] or _("key"),
				date=format_datetime(k.get("created_at"))),
			"fields": [(_("&Title:"), "title", False, "", True), (_("&Key (starts with ssh-ed25519, ssh-rsa or ecdsa-):"), "key", True, "", True)],
			"body": lambda v: {"title": v["title"].strip(), "key": v["key"].strip()},
			"copy": lambda k: k.get("key", ""),
			"empty": _("You have no SSH signing keys."),
		},
		"gpg": {
			"title": _("GPG Keys"), "path": "/user/gpg_keys", "permission": "admin:gpg_key",
			"label": lambda k: _("{name}, key ID {id}, {emails}, added {date}").format(
				name=k.get("name") or _("unnamed"), id=k.get("key_id", ""),
				emails=", ".join(e.get("email", "") for e in (k.get("emails") or [])) or _("no email"),
				date=format_datetime(k.get("created_at"))),
			"fields": [(_("&Name (optional):"), "name", False, ""), (_("&Public key (the text that starts with -----BEGIN PGP PUBLIC KEY BLOCK-----):"), "key", True, "", True)],
			"body": lambda v: {"name": v["name"].strip(), "armored_public_key": v["key"].strip()},
			"copy": lambda k: k.get("public_key", "") or k.get("key_id", ""),
			"empty": _("You have no GPG keys."),
		},
	}


def show_keys(parent, kind):
	cfg = _key_kinds()[kind]

	def fetch(page, selections):
		return gh("GET", cfg["path"], params=_page_params(page))

	def add(dlg, _item):
		values = ask_form(dlg, _("Add to {title}").format(title=cfg["title"]), cfg["fields"], ok_label=_("&Add Key"))
		if not values:
			return
		body = cfg["body"](values)
		_do(dlg, lambda: gh("POST", cfg["path"], body=body), _("Key added."), _("Could not add the key."), cfg["permission"])

	def delete(dlg, k):
		if confirm(dlg, _("Delete this key? Anything that uses it will stop working."), _("Delete Key"), warning=True, default_no=True):
			_do(dlg, lambda: gh("DELETE", f"{cfg['path']}/{k['id']}"), _("Key deleted."), _("Could not delete the key."), cfg["permission"])

	actions = [
		(_("&Copy Key"), lambda d, k: copy_to_clipboard(cfg["copy"](k), _("Key copied."))),
		(_("&Add Key"), add, False),
		(_("&Delete Key"), delete),
	]
	_show_list(parent, cfg["title"], fetch, cfg["label"], actions, cfg["empty"])


# ======================================================================
# Moderation: blocked users and interaction limits
# ======================================================================

def show_blocked_users(parent):

	def fetch(page, selections):
		return gh("GET", "/user/blocks", params=_page_params(page))

	def block(dlg, _item):
		values = ask_form(dlg, _("Block User"), [(_("&User name to block:"), "name", False, "", True)], ok_label=_("&Block"))
		if not values:
			return
		name = values["name"].strip().lstrip("@")
		_do(dlg, lambda: gh("PUT", f"/user/blocks/{q(name)}"),
			_("{name} is now blocked.").format(name=name), _("Could not block {name}.").format(name=name), "user")

	def unblock(dlg, u):
		_do(dlg, lambda: gh("DELETE", f"/user/blocks/{q(u['login'])}"),
			_("{name} is unblocked.").format(name=u["login"]), _("Could not unblock."), "user")

	actions = [
		(_("Open &Profile"), lambda d, u: show_user(d, u["login"])),
		(_("&Block User"), block, False),
		(_("&Unblock"), unblock),
	]
	_show_list(parent, _("Blocked Users"), fetch, lambda u: u.get("login", ""), actions, _("You have not blocked anybody."))


_LIMITS = ("", "existing_users", "contributors_only", "collaborators_only")
_EXPIRIES = ("one_day", "three_days", "one_week", "one_month", "six_months")


def edit_interaction_limits(parent):
	ui.message(_("Loading..."))

	def done(res):
		if not res.ok:
			ui.message(_("Could not read your interaction limit.") + " " + res.message + _need("user"))
			return
		data = res.data if isinstance(res.data, dict) else {}
		limit = data.get("limit") or ""
		if limit:
			intro = _("Currently only {who} can interact with your public repositories, until {when}.").format(
				who={"existing_users": _("existing users"), "contributors_only": _("prior contributors"), "collaborators_only": _("collaborators")}.get(limit, limit),
				when=format_datetime(data.get("expires_at")))
		else:
			intro = _("Currently there is no temporary interaction limit.")
		values = ask_form(
			parent, _("Temporary Interaction Limits"), [],
			choices=[
				(_("Who can &interact with your public repositories:"), "limit",
					[_("Everyone (remove the limit)"), _("Existing users"), _("Prior contributors"), _("Collaborators only")],
					_LIMITS.index(limit) if limit in _LIMITS else 0),
				(_("For how &long:"), "expiry", [_("24 hours"), _("3 days"), _("7 days"), _("1 month"), _("6 months")], 0),
			],
			ok_label=_("&Apply"), intro=intro + " " + _("Limits end by themselves after the time you choose."),
		)
		if values is None:
			return
		chosen = _LIMITS[values["limit"]]

		def applied(result):
			if result.ok:
				ui.message(_("Interaction limit removed.") if not chosen else _("Interaction limit set."))
			else:
				ui.message(_("Could not change the interaction limit.") + " " + result.message + _need("repo"))

		if chosen:
			parent.run(lambda: gh("PUT", "/user/interaction-limits", body={"limit": chosen, "expiry": _EXPIRIES[values["expiry"]]}), applied)
		else:
			parent.run(lambda: gh("DELETE", "/user/interaction-limits"), applied)

	parent.run(lambda: gh("GET", "/user/interaction-limits"), done)


# ======================================================================
# Organizations, invitations, watching, codespaces
# ======================================================================

def show_org_memberships(parent):
	def fetch(page, selections):
		res = gh("GET", "/user/memberships/orgs", params=_page_params(page))
		if not res.ok or not isinstance(res.data, list):
			return res
		login = current_login()
		public = gh("GET", f"/users/{q(login)}/orgs", params={"per_page": 100}) if login else None
		names = {o.get("login", "").lower() for o in (public.data if public and public.ok and isinstance(public.data, list) else [])}
		for m in res.data:
			m["_public"] = (m.get("organization") or {}).get("login", "").lower() in names
			m["_me"] = login
		return res

	def label(m):
		org = (m.get("organization") or {}).get("login", "")
		parts = [org, m.get("role", "")]
		if m.get("state") == "pending":
			parts.append(_("invitation waiting"))
		else:
			parts.append(_("membership is public") if m.get("_public") else _("membership is private"))
		return ", ".join(p for p in parts if p)

	def org(m):
		return (m.get("organization") or {}).get("login", "")

	def make_public(dlg, m):
		_do(dlg, lambda: gh("PUT", f"/orgs/{q(org(m))}/public_members/{q(m['_me'])}"),
			_("Your membership of {org} is now public.").format(org=org(m)), _("Could not change it."), "user")

	def make_private(dlg, m):
		_do(dlg, lambda: gh("DELETE", f"/orgs/{q(org(m))}/public_members/{q(m['_me'])}"),
			_("Your membership of {org} is now private.").format(org=org(m)), _("Could not change it."), "user")

	def accept(dlg, m):
		if m.get("state") != "pending":
			ui.message(_("There is no invitation waiting for this organization."))
			return
		_do(dlg, lambda: gh("PATCH", f"/user/memberships/orgs/{q(org(m))}", body={"state": "active"}),
			_("You joined {org}.").format(org=org(m)), _("Could not accept the invitation."), "user")

	def leave(dlg, m):
		if confirm(dlg, _("GitHub only lets you leave an organization on its website. Open the organization's people page now?"), _("Leave Organization")):
			open_url(f"https://github.com/orgs/{q(org(m))}/people")

	actions = [
		(_("Make Membership &Public"), make_public),
		(_("Make Membership Pri&vate"), make_private),
		(_("Accept &Invitation"), accept),
		(_("&Leave Organization"), leave),
		(_("Open in &Browser"), lambda d, m: open_url(f"https://github.com/{q(org(m))}")),
	]
	_show_list(parent, _("Organization Memberships"), fetch, label, actions, _("You are not a member of any organization."))


def show_repo_invitations(parent):
	def fetch(page, selections):
		return gh("GET", "/user/repository_invitations", params=_page_params(page))

	def label(i):
		repo = (i.get("repository") or {}).get("full_name", "")
		who = (i.get("inviter") or {}).get("login", "")
		return _("{repo}, invited by {who}, permission {perm}").format(repo=repo, who=who, perm=i.get("permissions", ""))

	actions = [
		(_("&Accept"), lambda d, i: _do(d, lambda: gh("PATCH", f"/user/repository_invitations/{i['id']}"),
			_("Invitation accepted."), _("Could not accept the invitation."), "repo")),
		(_("&Decline"), lambda d, i: _do(d, lambda: gh("DELETE", f"/user/repository_invitations/{i['id']}"),
			_("Invitation declined."), _("Could not decline the invitation."), "repo")),
		(_("Open in &Browser"), lambda d, i: open_url((i.get("repository") or {}).get("html_url"))),
	]
	_show_list(parent, _("Repository Invitations"), fetch, label, actions, _("You have no repository invitations."))


def show_watching(parent):
	def fetch(page, selections):
		return gh("GET", "/user/subscriptions", params=_page_params(page))

	def unwatch(dlg, r):
		if confirm(dlg, _("Stop watching {name}?").format(name=r["full_name"]), _("Unwatch"), default_no=True):
			_do(dlg, lambda: gh("DELETE", f"/repos/{r['full_name']}/subscription"), _("You no longer watch it."), _("Could not unwatch."), "notifications")

	actions = [
		(_("Open in &Browser"), lambda d, r: open_url(r.get("html_url"))),
		(_("&Unwatch"), unwatch),
	]
	_show_list(parent, _("Watched Repositories"), fetch, lambda r: r.get("full_name", ""), actions,
		_("You are not watching any repository."))


def show_codespaces_secrets(parent):
	def fetch(page, selections):
		res = gh("GET", "/user/codespaces/secrets", params=_page_params(page))
		if res.ok and isinstance(res.data, dict):
			return Result(res.code, res.data.get("secrets", []), res.headers)
		return res

	def label(s):
		return _("{name}, updated {date}, for {who}").format(
			name=s.get("name", ""), date=format_datetime(s.get("updated_at")), who=s.get("visibility", ""))

	def delete(dlg, s):
		if confirm(dlg, _("Delete the secret {name}?").format(name=s["name"]), _("Delete Secret"), warning=True, default_no=True):
			_do(dlg, lambda: gh("DELETE", f"/user/codespaces/secrets/{q(s['name'])}"), _("Secret deleted."), _("Could not delete the secret."), "codespace:secrets")

	actions = [
		(_("&Delete Secret"), delete),
		(_("&Add or Change Secrets on GitHub"), lambda d, s: open_url(SETTINGS_URL + "/codespaces"), False),
	]
	_show_list(parent, _("Codespaces Secrets"), fetch, label, actions, _("You have no Codespaces secrets."),
		_("Secret values are never shown. Adding a secret needs encryption that NVDA does not have, so use Add or Change Secrets on GitHub."))


# ======================================================================
# Account overview and API limits
# ======================================================================

def show_account_overview(parent):
	ui.message(_("Loading..."))

	def done(res):
		if not res.ok or not isinstance(res.data, dict):
			ui.message(_("Could not load your account.") + " " + res.message)
			return
		p = res.data
		plan = p.get("plan") or {}
		two = p.get("two_factor_authentication")
		rows = [
			(_("Username"), p.get("login")),
			(_("Account type"), p.get("type", "User")),
			(_("GitHub plan"), (plan.get("name") or _("free")).capitalize()),
			(_("Two-factor authentication"), _("on") if two else _("off") if two is not None else _("unknown")),
			(_("Disk space used"), _("{size} megabytes").format(size=round((p.get("disk_usage") or 0) / 1024.0, 1))),
			(_("Disk space in your plan"), _("{size} megabytes").format(size=round((plan.get("space") or 0) / 1024.0, 1))),
			(_("Private repositories you own"), p.get("owned_private_repos", 0)),
			(_("Private repositories in your plan"), plan.get("private_repos", _("unknown"))),
			(_("Collaborators allowed on private repositories"), plan.get("collaborators", _("unknown"))),
			(_("Public repositories"), p.get("public_repos", 0)),
			(_("Account created"), format_datetime(p.get("created_at"))),
			(_("Last changed"), format_datetime(p.get("updated_at"))),
			(_("GitHub staff account"), _("yes") if p.get("site_admin") else _("no")),
		]
		text = "\n".join(f"{k}: {v}" for k, v in rows)
		show_modal(TextViewDialog(
			parent, _("Account Overview"), text, label=_("Account &details:"),
			buttons=[
				(_("Copy &Details"), lambda d: copy_to_clipboard(text, _("Account details copied."))),
				(_("&Billing and Plans on GitHub"), lambda d: open_url(SETTINGS_URL + "/billing")),
				(_("&Security Settings on GitHub"), lambda d: open_url(SETTINGS_URL + "/security")),
			]))

	parent.run(lambda: gh("GET", "/user"), done)


def show_rate_limit(parent):
	ui.message(_("Loading..."))

	def done(res):
		if not res.ok or not isinstance(res.data, dict):
			ui.message(_("Could not read the limits.") + " " + res.message)
			return
		lines = [_("How many requests this add-on may still send to GitHub:")]
		for name, info in sorted((res.data.get("resources") or {}).items()):
			reset = datetime.fromtimestamp(info.get("reset", 0)).strftime("%I:%M %p") if info.get("reset") else "-"
			lines.append(_("{name}: {left} of {total} left, resets at {when}").format(
				name=name, left=info.get("remaining", 0), total=info.get("limit", 0), when=reset))
		text = "\n".join(lines)
		show_modal(TextViewDialog(parent, _("API Limits"), text, label=_("&Limits:"),
			buttons=[(_("Copy &Details"), lambda d: copy_to_clipboard(text, _("Copied.")))]))

	parent.run(lambda: gh("GET", "/rate_limit"), done)


# ======================================================================
# Pages that only exist on github.com
# ======================================================================

def website_pages():
	return [
		(_("Public profile (pronouns, profile picture, README)"), "/profile"),
		(_("Account: change username, export, delete account"), "/admin"),
		(_("Appearance: theme and display"), "/appearance"),
		(_("Accessibility: motion, keyboard shortcuts, link underlines"), "/accessibility"),
		(_("Notifications: where and how you are notified"), "/notifications"),
		(_("Billing and plans"), "/billing"),
		(_("Emails: primary address and privacy options"), "/emails"),
		(_("Password and authentication: password, two-factor, passkeys"), "/security"),
		(_("Sessions: devices signed in"), "/sessions"),
		(_("SSH and GPG keys"), "/keys"),
		(_("Organizations"), "/organizations"),
		(_("Enterprises"), "/enterprises"),
		(_("Moderation: blocked users, interaction limits, code review limits"), "/moderation/blocks"),
		(_("Repositories you can access"), "/repositories"),
		(_("Codespaces"), "/codespaces"),
		(_("Packages"), "/packages"),
		(_("GitHub Copilot"), "/copilot"),
		(_("Pages"), "/pages"),
		(_("Saved replies"), "/replies"),
		(_("Security and analysis"), "/security_analysis"),
		(_("Authorized OAuth apps and installed GitHub Apps"), "/applications"),
		(_("Scheduled reminders"), "/reminders"),
		(_("Security log"), "/security-log"),
		(_("Sponsorship log"), "/sponsors-log"),
		(_("Developer settings: GitHub Apps, OAuth apps, personal access tokens"), "/apps"),
		(_("Personal access tokens"), "/tokens"),
		(_("Education benefits"), "/education/benefits"),
	]


def show_website_pages(parent):
	items = [{"title": title, "url": SETTINGS_URL + path} for title, path in website_pages()]
	actions = [
		(_("Open in &Browser"), lambda d, i: open_url(i["url"])),
		(_("Copy &Link"), lambda d, i: copy_to_clipboard(i["url"], _("Link copied."))),
	]
	_show_list(parent, _("More Settings on GitHub.com"), local_fetch(items, per_page=100), lambda i: i["title"], actions, _("No pages."),
		_("These settings can only be changed on GitHub's website. Enter opens the page in your browser."))


# ======================================================================
# The settings menu
# ======================================================================

def _people(parent, kind):

	def done(login):
		title = _("Followers") if kind == "followers" else _("Following")
		show_user_list(parent, login, kind, title)

	def work():
		return current_login()

	def after(login):
		if login:
			done(login)
		else:
			ui.message(_("Could not find out who you are. Check your token."))

	parent.run(work, after)


class SettingsMenuDialog(AccessibleDialog):
	def __init__(self, parent):
		super().__init__(parent, _("GitHub Settings"), (520, 560), resizable=False)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(self, label=_("&Settings:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
		p = self
		self.entries = [
			(_("Public Profile"), lambda: edit_public_profile(p)),
			(_("Account Overview and Plan"), lambda: show_account_overview(p)),
			(_("Email Addresses"), lambda: show_emails(p)),
			(_("SSH Keys"), lambda: show_keys(p, "ssh")),
			(_("SSH Signing Keys"), lambda: show_keys(p, "signing")),
			(_("GPG Keys"), lambda: show_keys(p, "gpg")),
			(_("Organization Memberships"), lambda: show_org_memberships(p)),
			(_("Repository Invitations"), lambda: show_repo_invitations(p)),
			(_("Blocked Users"), lambda: show_blocked_users(p)),
			(_("Temporary Interaction Limits"), lambda: edit_interaction_limits(p)),
			(_("Followers"), lambda: _people(p, "followers")),
			(_("Following"), lambda: _people(p, "following")),
			(_("Watched Repositories"), lambda: show_watching(p)),
			(_("Codespaces Secrets"), lambda: show_codespaces_secrets(p)),
			(_("API Limits"), lambda: show_rate_limit(p)),
			(_("More Settings on GitHub.com"), lambda: show_website_pages(p)),
		]
		self.listbox = wx.ListBox(self, choices=[label for label, _action in self.entries], size=(480, 380))
		self.listbox.SetSelection(0)
		self.listbox.enter_action = self.choose
		self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.choose())
		sizer.Add(self.listbox, 1, wx.EXPAND | wx.ALL, 8)
		sizer.Add(self.close_button(_("&Back")), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
		self.layout(sizer, fit=True, focus=self.listbox)

	def choose(self):
		index = self.listbox.GetSelection()
		if index != wx.NOT_FOUND:
			self.entries[index][1]()


def show_settings_menu(parent):
	show_modal(SettingsMenuDialog(parent))
