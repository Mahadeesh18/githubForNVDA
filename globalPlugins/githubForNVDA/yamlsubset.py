# GitHub for NVDA
# Copyright (C) 2025-2026 Mahadeesh and contributors
# This file is covered by the GNU General Public License, version 2.
# See the file LICENSE for more details.

"""A small reader for the YAML subset used by GitHub issue templates. Pure Python, no NVDA modules."""

import json
import re


class YamlError(ValueError):
	pass


_BLOCK_HEADER = re.compile(r"^([|>])([+-]?)(\d?)([+-]?)$")
_INT = re.compile(r"^[-+]?\d+$")
_FLOAT = re.compile(r"^[-+]?(\d+\.\d*|\.\d+)([eE][-+]?\d+)?$")


def _strip_comment(text):
	"""Remove a trailing ' # comment' that is not inside quotes."""
	quote = None
	for index, char in enumerate(text):
		if quote:
			if char == quote:
				quote = None
		elif char in "'\"" and (index == 0 or text[index - 1] in " \t[{,:-"):
			quote = char
		elif char == "#" and (index == 0 or text[index - 1] in " \t"):
			return text[:index].rstrip()
	return text.rstrip()


def _unquote(text):
	if text[0] == "'":
		if len(text) < 2 or text[-1] != "'":
			raise YamlError("unterminated quote")
		return text[1:-1].replace("''", "'")
	if len(text) < 2 or text[-1] != '"':
		raise YamlError("unterminated quote")
	try:
		return json.loads(text)
	except ValueError:
		return text[1:-1].replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")


def _plain(text):
	if text in ("", "~") or text.lower() == "null":
		return None
	low = text.lower()
	if low == "true":
		return True
	if low == "false":
		return False
	if _INT.match(text):
		return int(text)
	if _FLOAT.match(text):
		return float(text)
	return text


def _flow(text, pos=0):
	"""Parse one flow value ([...], {...}, quoted or plain) starting at pos. Returns (value, new position)."""
	length = len(text)
	while pos < length and text[pos] in " \t":
		pos += 1
	if pos >= length:
		return None, pos
	char = text[pos]
	if char == "[":
		items, pos = [], pos + 1
		while True:
			while pos < length and text[pos] in " \t,":
				pos += 1
			if pos >= length:
				raise YamlError("unterminated [")
			if text[pos] == "]":
				return items, pos + 1
			value, pos = _flow(text, pos)
			items.append(value)
	if char == "{":
		result, pos = {}, pos + 1
		while True:
			while pos < length and text[pos] in " \t,":
				pos += 1
			if pos >= length:
				raise YamlError("unterminated {")
			if text[pos] == "}":
				return result, pos + 1
			key, pos = _flow_scalar(text, pos, ":}")
			while pos < length and text[pos] in " \t":
				pos += 1
			value = None
			if pos < length and text[pos] == ":":
				value, pos = _flow(text, pos + 1)
			result[str(key)] = value
	return _flow_scalar(text, pos, ",]}")


def _flow_scalar(text, pos, stops):
	length = len(text)
	if pos < length and text[pos] in "'\"":
		quote, end = text[pos], pos + 1
		while end < length:
			if text[end] == "\\" and quote == '"':
				end += 2
				continue
			if text[end] == quote:
				if quote == "'" and end + 1 < length and text[end + 1] == "'":
					end += 2
					continue
				break
			end += 1
		return _unquote(text[pos:end + 1]), end + 1
	end = pos
	while end < length and text[end] not in stops:
		end += 1
	return _plain(text[pos:end].strip()), end


def _split_key(content):
	"""'key: value' -> (key, value text). None when the line is not a mapping entry."""
	if not content or content[0] in "[{":
		return None
	if content[0] in "'\"":
		quote, end = content[0], 1
		while end < len(content) and content[end] != quote:
			end += 2 if content[end] == "\\" and quote == '"' else 1
		rest = content[end + 1:].lstrip()
		if end >= len(content) or not rest.startswith(":"):
			return None
		return _unquote(content[:end + 1]), rest[1:].strip()
	match = re.match(r"^(.*?[^\s]):(?:\s+(.*))?$", content)
	if not match or content.startswith("- "):
		return None
	return match.group(1).strip(), (match.group(2) or "").strip()


class _Reader:
	def __init__(self, text):
		text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
		self.lines = text.split("\n")
		self.i = 0

	def peek(self):
		"""(index, indent, content without comment) of the next meaningful line, or None."""
		j = self.i
		while j < len(self.lines):
			raw = self.lines[j]
			content = _strip_comment(raw.strip())
			if not content or (raw[:3] in ("---", "...") and raw.strip() in ("---", "...")):
				j += 1
				continue
			if content.startswith("%"):
				j += 1
				continue
			indent = len(raw) - len(raw.lstrip(" "))
			self.i = j
			return j, indent, content
		self.i = len(self.lines)
		return None

	def node(self, minimum):
		item = self.peek()
		if item is None or item[1] < minimum:
			return None
		_j, indent, content = item
		if content == "-" or content.startswith("- "):
			return self.sequence(indent)
		if _split_key(content):
			return self.mapping(indent)
		self.i += 1
		return self.inline(content)

	def inline(self, text):
		if text[0] in "[{":
			value, pos = _flow(text)
			if text[pos:].strip():
				raise YamlError("unexpected text after flow value")
			return value
		if text[0] in "'\"":
			return _unquote(text)
		return _plain(text)

	def block_scalar(self, header, parent_indent):
		match = _BLOCK_HEADER.match(header)
		style, chomp = match.group(1), (match.group(2) or match.group(4))
		lines = []
		block_indent = None
		while self.i < len(self.lines):
			raw = self.lines[self.i]
			if raw.strip() == "":
				lines.append("")
				self.i += 1
				continue
			indent = len(raw) - len(raw.lstrip(" "))
			if block_indent is None:
				if indent <= parent_indent:
					break
				block_indent = indent
			if indent < block_indent:
				break
			lines.append(raw[block_indent:])
			self.i += 1
		trailing = 0
		while lines and lines[-1] == "":
			lines.pop()
			trailing += 1
		if style == "|":
			text = "\n".join(lines)
		else:
			text, previous_blank = "", False
			for number, line in enumerate(lines):
				if number == 0:
					text = line
				elif line == "":
					text += "\n"
					previous_blank = True
				else:
					text += ("" if previous_blank else " ") + line
					previous_blank = False
		if not lines:
			return ""
		if chomp == "-":
			return text
		if chomp == "+":
			return text + "\n" * (trailing + 1)
		return text + "\n"

	def value(self, rest, indent):
		"""The value after 'key:' (rest) or after '- ' for an entry at the given indent."""
		if rest == "":
			item = self.peek()
			if item is None:
				return None
			_j, next_indent, content = item
			if next_indent > indent:
				return self.node(next_indent)
			if next_indent == indent and (content == "-" or content.startswith("- ")):
				return self.sequence(indent)
			return None
		if _BLOCK_HEADER.match(rest):
			return self.block_scalar(rest, indent)
		value = self.inline(rest)
		# A plain text that continues on the following, deeper lines.
		if isinstance(value, str) and rest[0] not in "'\"[{":
			while self.i < len(self.lines):
				raw = self.lines[self.i]
				content = _strip_comment(raw.strip())
				if not content or len(raw) - len(raw.lstrip(" ")) <= indent:
					break
				value += " " + content
				self.i += 1
		return value

	def mapping(self, indent):
		result = {}
		while True:
			item = self.peek()
			if item is None:
				break
			_j, current, content = item
			if current != indent:
				if current > indent:
					raise YamlError("bad indentation")
				break
			parts = _split_key(content)
			if not parts:
				break
			self.i += 1
			result[str(parts[0])] = self.value(parts[1], indent)
		return result

	def sequence(self, indent):
		items = []
		while True:
			item = self.peek()
			if item is None:
				break
			j, current, content = item
			if current != indent or not (content == "-" or content.startswith("- ")):
				if current > indent:
					raise YamlError("bad indentation")
				break
			after = content[1:].lstrip()
			offset = len(content) - len(after)
			if after and _split_key(after) and not _BLOCK_HEADER.match(after):
				# "- key: value": the entry is a mapping whose first key sits after the dash.
				raw = self.lines[j]
				self.lines[j] = " " * (indent + offset) + raw.strip()[offset:].lstrip()
				items.append(self.mapping(indent + offset))
				continue
			self.i += 1
			items.append(self.value(after, indent))
		return items


def load_yaml(text):
	"""Parse the YAML subset used by issue templates. Raises YamlError (a ValueError) when it cannot."""
	try:
		return _Reader(text).node(0)
	except YamlError:
		raise
	except Exception as e:
		raise YamlError(str(e))
