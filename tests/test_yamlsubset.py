import pytest

from yamlsubset import load_yaml, YamlError

FORM = """
name: Bug report
description: "Tell us: what broke?"
labels: [bug, triage]
body:
  - type: markdown
    attributes:
      value: |
        Thanks for reporting.
        Second line.
  - type: dropdown
    id: version
    attributes:
      label: Version
      options:
        - "1.0"
        - 2.0  # a comment
    validations:
      required: true
"""


def test_issue_form():
	data = load_yaml(FORM)
	assert data["name"] == "Bug report"
	assert data["description"] == "Tell us: what broke?"
	assert data["labels"] == ["bug", "triage"]
	assert data["body"][0]["attributes"]["value"] == "Thanks for reporting.\nSecond line.\n"
	assert data["body"][1]["attributes"]["options"] == ["1.0", 2.0]
	assert data["body"][1]["validations"]["required"] is True


def test_folded_and_flow():
	data = load_yaml("a: >-\n  one\n  two\nb: {x: 1, y: 'it''s'}\n")
	assert data == {"a": "one two", "b": {"x": 1, "y": "it's"}}


def test_bad_yaml_raises():
	with pytest.raises(YamlError):
		load_yaml("a: [1, 2")
