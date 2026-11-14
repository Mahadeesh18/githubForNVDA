import logic


def test_parse_github_link_kinds():
	assert logic.parse_github_link("nvaccess/nvda") == ("repo", "nvaccess", "nvda", None, ())
	assert logic.parse_github_link("https://github.com/nvaccess").kind == "user"
	assert logic.parse_github_link("https://github.com/o/r/issues/12")[:4] == ("issue", "o", "r", 12)
	assert logic.parse_github_link("https://github.com/o/r/pull/7/files").kind == "pull"
	assert logic.parse_github_link("git@github.com:o/r.git").repo == "r"
	assert logic.parse_github_link("https://github.com/o/r/actions/runs/99").number == 99
	assert logic.parse_github_link("https://github.com/o/r/blob/main/a/b.py").rest == ("main", "a", "b.py")
	assert logic.parse_github_link("https://github.com/settings/tokens") is None
	assert logic.parse_github_link("not a link") is None


def test_decode_and_encode_round_trip():
	data = "café\r\nline two\r\n".encode("utf-8")
	decoded = logic.decode_text(data)
	assert decoded.text == "café\nline two\n" and decoded.crlf and decoded.editable
	assert logic.encode_text(decoded.text, decoded.encoding, decoded.crlf) == data
	bom = b"\xef\xbb\xbfhello"
	decoded = logic.decode_text(bom)
	assert decoded.text == "hello" and logic.encode_text("hello", decoded.encoding) == bom
	assert logic.decode_text(b"\x00\x01binary") is None
	assert logic.decode_text("é".encode("cp1252")).editable is False


def test_access_keys_are_unique():
	labels = logic.resolve_access_keys(["&Open", "&Other", "&Back", "No key"], first=[2])
	keys = [logic.parse_label(label) for label in labels]
	letters = [plain[i].lower() for plain, i in keys if i >= 0]
	assert len(letters) == len(set(letters))
	assert labels[2] == "&Back" and labels[3] == "No key"


def test_error_text_405_and_rate_limit():
	assert "does not allow" in logic.error_text(405, {"message": "Merge commits are not allowed"})
	assert "rate limit" in logic.error_text(403, {}, {"X-RateLimit-Remaining": "0", "Retry-After": "30"})


def test_format_datetime_is_built_from_translatable_parts():
	assert logic.format_datetime("2026-10-09T13:05:00Z") == "09 October 2026 at 1:05 PM"
	assert logic.format_datetime("2026-01-01T00:00:00Z") == "01 January 2026 at 12:00 AM"
	assert logic.format_month("2026-03") == "March 2026"
	assert logic.weekday_name(0) == "Sunday"
