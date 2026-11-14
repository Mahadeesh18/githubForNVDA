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


def test_merge3_clean_and_conflict():
	base = ["a\n", "b\n", "c\n"]
	chunks = logic.merge3(base, ["A\n", "b\n", "c\n"], ["a\n", "b\n", "C\n"])
	assert chunks == [("ok", ["A\n", "b\n", "C\n"])]
	chunks = logic.merge3(base, ["a\n", "mine\n", "c\n"], ["a\n", "theirs\n", "c\n"])
	assert chunks == [("ok", ["a\n"]), ("conflict", ["b\n"], ["mine\n"], ["theirs\n"]), ("ok", ["c\n"])]
	assert logic.join_chunks(chunks, {0: "both\n"}) == "a\nboth\nc\n"


def test_merge_text_same_change_on_both_sides():
	chunks, count = logic.merge_text("x\ny\n", "x\nz\n", "x\nz\n")
	assert count == 0 and logic.join_chunks(chunks) == "x\nz\n"


def test_merge_text_added_file_on_both_sides():
	chunks, count = logic.merge_text("", "one\n", "two\n")
	assert count == 1


def test_three_way_trees():
	f = lambda sha: ("100644", "blob", sha)  # noqa: E731
	base = {"same": f("1"), "ours": f("2"), "theirs": f("3"), "both": f("4"), "gone": f("5")}
	ours = {"same": f("1"), "ours": f("2b"), "theirs": f("3"), "both": f("4a"), "gone": f("5")}
	theirs = {"same": f("1"), "ours": f("2"), "theirs": f("3b"), "both": f("4b"), "new": f("6")}
	take, conflicts = logic.three_way_trees(base, ours, theirs)
	assert take == {"theirs": f("3b"), "new": f("6"), "gone": None}
	assert conflicts == ["both"]


def test_done_notifications():
	done = {"1": {"updated_at": "2026-11-01T00:00:00Z", "done_at": 1000}}
	old = {"id": "1", "updated_at": "2026-11-01T00:00:00Z"}
	newer = {"id": "1", "updated_at": "2026-11-02T00:00:00Z"}
	other = {"id": "2", "updated_at": "2026-11-01T00:00:00Z"}
	assert logic.without_done([old, other], done) == [other]
	assert logic.without_done([newer], done) == [newer]  # new activity brings it back
	assert logic.prune_done(done, 1000 + logic.DONE_KEEP_SECONDS + 1) == {}


def test_checks_summary():
	runs = [{"name": "build", "conclusion": "success"}, {"name": "lint", "conclusion": "failure"}, {"name": "slow", "conclusion": None}]
	lines = logic.checks_summary(runs, [{"context": "ci/old", "state": "success"}])
	assert lines[0] == "Checks: 2 passed, 1 failed, 1 still running."
	assert lines[1] == "Failed checks: lint" and lines[2] == "Running checks: slow"
	assert logic.checks_summary([], []) == []


def test_numbered_patch():
	patch = "@@ -10,3 +10,3 @@ def f():\n a\n-b\n+B\n c"
	assert logic.numbered_patch(patch).split("\n") == [
		"Lines 10 to 12: def f():", "10: a", "11 removed: b", "11 added: B", "12: c",
	]
