"""Build GitHub for NVDA with nothing but Python 3.

    python tools/build.py           builds githubForNVDA-<version>.nvda-addon
    python tools/build.py pot       writes githubForNVDA.pot, the template translators start from
    python tools/build.py check     checks every locale/<lang>/LC_MESSAGES/nvda.po for broken placeholders

Translations live in locale/<lang>/LC_MESSAGES/nvda.po. They are compiled to nvda.mo inside the package,
so .mo files never need to be committed. A translated manifest goes in locale/<lang>/manifest.ini
(summary and description only) and a translated user guide in doc/<lang>/readme.html.
"""

import ast
import io
import os
import re
import struct
import sys
import tokenize
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NAME = "githubForNVDA"
INCLUDE = ("manifest.ini", "globalPlugins", "doc", "locale")
SKIP_PARTS = {"__pycache__"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".po", ".pot", ".mo")


def manifest_version():
	with open(os.path.join(ROOT, "manifest.ini"), encoding="utf-8") as f:
		match = re.search(r"^version\s*=\s*(\S+)", f.read(), re.M)
	return match.group(1) if match else "dev"


# --- .po and .mo files -------------------------------------------------------

def read_po(path):
	"""{msgid: msgstr} of the translated entries that are not marked fuzzy. The header has the msgid ""."""
	entries = {}
	current, key, fuzzy = {}, None, False

	def flush():
		if "msgid" in current and current.get("msgstr") and not fuzzy:
			entries[current["msgid"]] = current["msgstr"]

	with open(path, encoding="utf-8") as f:
		for raw in f:
			line = raw.strip()
			if not line:
				continue
			if line.startswith("#"):
				if line.startswith("#,") and "fuzzy" in line:
					flush()
					current, key, fuzzy = {}, None, True
				continue
			if line.startswith("msgid "):
				if "msgstr" in current:
					flush()
					current, fuzzy = {}, False
				key = "msgid"
				current[key] = ast.literal_eval(line[6:])
			elif line.startswith("msgstr "):
				key = "msgstr"
				current[key] = ast.literal_eval(line[7:])
			elif line.startswith('"') and key:
				current[key] += ast.literal_eval(line)
	flush()
	return entries


def write_mo(entries, path):
	"""Write a GNU .mo file that Python's gettext (and so NVDA) can read."""
	entries = dict(entries)
	entries.setdefault("", "Content-Type: text/plain; charset=UTF-8\n")
	items = sorted((k.encode("utf-8"), v.encode("utf-8")) for k, v in entries.items())
	count = len(items)
	ids = b"".join(k + b"\0" for k, _v in items)
	strs = b"".join(v + b"\0" for _k, v in items)
	data_start = 7 * 4 + 16 * count
	key_table, value_table = b"", b""
	id_offset, str_offset = data_start, data_start + len(ids)
	for k, v in items:
		key_table += struct.pack("<ii", len(k), id_offset)
		value_table += struct.pack("<ii", len(v), str_offset)
		id_offset += len(k) + 1
		str_offset += len(v) + 1
	header = struct.pack("<Iiiiiii", 0x950412DE, 0, count, 7 * 4, 7 * 4 + count * 8, 0, 0)
	with open(path, "wb") as f:
		f.write(header + key_table + value_table + ids + strs)


# --- extracting the strings to translate ----------------------------------------

def translator_comments(source):
	"""{line: text} for '# Translators:' comments; following comment lines are joined to them."""
	comments, last = {}, None
	for token in tokenize.generate_tokens(io.StringIO(source).readline):
		if token.type != tokenize.COMMENT:
			continue
		text = token.string.lstrip("#").strip()
		row = token.start[0]
		if text.startswith("Translators:"):
			comments[row] = text
			last = row
		elif last is not None and row == last + 1:
			comments[row] = comments.pop(last) + " " + text
			last = row
		else:
			last = None
	return comments


def python_files():
	for folder, dirs, files in os.walk(os.path.join(ROOT, "globalPlugins")):
		dirs[:] = sorted(d for d in dirs if d not in SKIP_PARTS)
		for name in sorted(files):
			if name.endswith(".py"):
				yield os.path.join(folder, name)


def extract():
	"""{msgid: {"where": [(file, line)], "comment": text}} in the order the strings appear."""
	found = {}
	for path in python_files():
		with open(path, encoding="utf-8") as f:
			source = f.read()
		comments = translator_comments(source)
		rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
		calls = [
			node for node in ast.walk(ast.parse(source, path))
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_"
		]
		for node in sorted(calls, key=lambda n: (n.lineno, n.col_offset)):
			if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
				print(f"warning: {rel}:{node.lineno}: _() of something that is not a plain string", file=sys.stderr)
				continue
			text = node.args[0].value
			comment = next((comments[r] for r in range(node.lineno, node.lineno - 4, -1) if r in comments), "")
			entry = found.setdefault(text, {"where": [], "comment": ""})
			entry["where"].append((rel, node.lineno))
			if comment and comment not in entry["comment"]:
				entry["comment"] = (entry["comment"] + " " + comment).strip()
	return found


def po_string(text):
	escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
	lines = escaped.split("\n")
	if len(lines) == 1:
		return f'"{lines[0]}"'
	parts = [f'"{line}\\n"' for line in lines[:-1]] + ([f'"{lines[-1]}"'] if lines[-1] else [])
	return '""\n' + "\n".join(parts)


def make_pot():
	found = extract()
	out = [
		'msgid ""', 'msgstr ""',
		f'"Project-Id-Version: {NAME} {manifest_version()}\\n"',
		'"MIME-Version: 1.0\\n"', '"Content-Type: text/plain; charset=UTF-8\\n"',
		'"Content-Transfer-Encoding: 8bit\\n"', "",
	]
	for text, entry in found.items():
		if entry["comment"]:
			out.append("#. " + entry["comment"])
		out.append("#: " + " ".join(f"{f}:{n}" for f, n in entry["where"]))
		if "{" in text:
			out.append("#, python-brace-format")
		out += ["msgid " + po_string(text), 'msgstr ""', ""]
	path = os.path.join(ROOT, NAME + ".pot")
	with open(path, "w", encoding="utf-8", newline="\n") as f:
		f.write("\n".join(out))
	print(f"Wrote {len(found)} strings to {path}")


# --- checking translations ------------------------------------------------------

def placeholders(text):
	return sorted(set(re.findall(r"\{(\w*)\}", text)))


def po_files():
	base = os.path.join(ROOT, "locale")
	if not os.path.isdir(base):
		return
	for lang in sorted(os.listdir(base)):
		path = os.path.join(base, lang, "LC_MESSAGES", "nvda.po")
		if os.path.isfile(path):
			yield lang, path


def check():
	problems = 0
	for lang, path in po_files():
		for msgid, msgstr in read_po(path).items():
			if msgid and placeholders(msgid) != placeholders(msgstr):
				problems += 1
				print(f"{lang}: the {{placeholders}} differ in {msgid!r}")
	print("Translations: no problems found." if not problems else f"Translations: {problems} problems found.")
	return problems == 0


# --- packaging ---------------------------------------------------------------------

def build():
	if not check():
		sys.exit(1)
	target = os.path.join(ROOT, f"{NAME}-{manifest_version()}.nvda-addon")
	with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
		for item in INCLUDE:
			path = os.path.join(ROOT, item)
			if os.path.isfile(path):
				archive.write(path, item)
				continue
			for folder, dirs, files in os.walk(path):
				dirs[:] = sorted(d for d in dirs if d not in SKIP_PARTS)
				for name in sorted(files):
					if not name.endswith(SKIP_SUFFIXES):
						full = os.path.join(folder, name)
						archive.write(full, os.path.relpath(full, ROOT).replace(os.sep, "/"))
		for lang, po in po_files():
			mo = os.path.join(ROOT, "build", lang, "nvda.mo")
			os.makedirs(os.path.dirname(mo), exist_ok=True)
			write_mo(read_po(po), mo)
			archive.write(mo, f"locale/{lang}/LC_MESSAGES/nvda.mo")
	print(f"Built {target}")


if __name__ == "__main__":
	commands = {"build": build, "pot": make_pot, "check": lambda: sys.exit(0 if check() else 1)}
	commands[sys.argv[1] if len(sys.argv) > 1 else "build"]()
