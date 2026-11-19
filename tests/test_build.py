import gettext
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import build  # noqa: E402


def test_po_to_mo_round_trip(tmp_path):
	po = tmp_path / "nvda.po"
	po.write_text(
		'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
		'msgid "Open"\nmsgstr "Abrir"\n\n'
		'#, fuzzy\nmsgid "Close"\nmsgstr "Cerrar"\n\n'
		'msgid ""\n"Two\\n"\n"lines"\nmsgstr "Dos\\nlíneas"\n',
		encoding="utf-8",
	)
	entries = build.read_po(str(po))
	assert entries["Open"] == "Abrir"
	assert "Close" not in entries  # fuzzy entries are not used
	mo = tmp_path / "nvda.mo"
	build.write_mo(entries, str(mo))
	with open(mo, "rb") as f:
		catalog = gettext.GNUTranslations(f)
	assert catalog.gettext("Open") == "Abrir"
	assert catalog.gettext("Two\nlines") == "Dos\nlíneas"


def test_placeholders():
	assert build.placeholders("{count} of {total}") == ["count", "total"]


def test_every_string_in_the_addon_is_extractable():
	found = build.extract()
	assert "GitHub for NVDA" in found
	assert len(found) > 500


def test_plural_entries_round_trip(tmp_path):
	po = tmp_path / "nvda.po"
	po.write_text(
		'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
		'msgid "{count} file"\nmsgid_plural "{count} files"\nmsgstr[0] "{count} archivo"\nmsgstr[1] "{count} archivos"\n',
		encoding="utf-8",
	)
	entries = build.read_po(str(po))
	assert entries["{count} file\0{count} files"] == "{count} archivo\0{count} archivos"
	mo = tmp_path / "nvda.mo"
	build.write_mo(entries, str(mo))
	with open(mo, "rb") as f:
		catalog = gettext.GNUTranslations(f)
	assert catalog.ngettext("{count} file", "{count} files", 1) == "{count} archivo"
	assert catalog.ngettext("{count} file", "{count} files", 3) == "{count} archivos"


def test_plural_messages_are_extracted():
	found = build.extract()
	assert ("{count} file", "{count} files") in found
