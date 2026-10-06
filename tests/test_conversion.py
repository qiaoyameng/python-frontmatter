"""
Tests for frontmatter.conversion: YAML -> TOML/JSON front matter conversion
with pre-write fidelity checks.
"""
import datetime

import pytest

import frontmatter
from frontmatter.conversion import (
    ConversionError,
    check_metadata,
    convert_metadata,
    convert_post,
    convert_text,
)
from frontmatter.default_handlers import TOMLHandler

# One metadata set containing a timezone-aware datetime and a null,
# converted to both target formats throughout these tests.
SOURCE_TEXT = """\
---
title: Hello, world!
published: 2024-03-01 12:30:00+08:00
summary:
tags:
  - python
  - frontmatter
---

Body *stays* exactly as it was.
"""

PUBLISHED = datetime.datetime(
    2024, 3, 1, 12, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=8))
)

# Approved conversion values: what each field must be after conversion.
APPROVED_JSON = {
    "title": "Hello, world!",
    "published": "2024-03-01T12:30:00+08:00",
    "summary": None,
    "tags": ["python", "frontmatter"],
}

APPROVED_TOML = {
    "title": "Hello, world!",
    "published": PUBLISHED,
    "summary": "",
    "tags": ["python", "frontmatter"],
}

JSON_CONVERTERS = {"published": lambda value: value.isoformat()}
TOML_CONVERTERS = {"summary": lambda value: ""}


def test_report_json_flags_datetime_but_not_null():
    metadata = frontmatter.parse(SOURCE_TEXT)[0]
    report = check_metadata(metadata, "json")
    assert not report.ok
    by_path = {issue.path: issue.rule for issue in report.issues}
    assert by_path == {"published": "datetime"}
    assert "published" in str(report)


def test_report_toml_flags_null_but_not_datetime():
    metadata = frontmatter.parse(SOURCE_TEXT)[0]
    report = check_metadata(metadata, "toml")
    assert not report.ok
    by_path = {issue.path: issue.rule for issue in report.issues}
    assert by_path == {"summary": "null"}
    assert "summary" in str(report)


def test_convert_text_to_json_roundtrips_approved_values():
    converted = convert_text(SOURCE_TEXT, "json", converters=JSON_CONVERTERS)
    post = frontmatter.loads(converted)
    assert post.metadata == APPROVED_JSON
    assert post.content == "Body *stays* exactly as it was."


def test_convert_text_to_toml_roundtrips_approved_values():
    converted = convert_text(SOURCE_TEXT, "toml", converters=TOML_CONVERTERS)
    post = frontmatter.loads(converted)
    assert post.metadata == APPROVED_TOML
    assert post.content == "Body *stays* exactly as it was."


def test_convert_text_leaves_body_untouched():
    body = "\nBody *stays* exactly as it was.\n"
    assert SOURCE_TEXT.endswith(body)
    for target, converters in (("json", JSON_CONVERTERS), ("toml", TOML_CONVERTERS)):
        converted = convert_text(SOURCE_TEXT, target, converters=converters)
        assert converted.endswith(body)


def test_convert_text_raises_before_writing():
    for target in ("json", "toml"):
        with pytest.raises(ConversionError) as excinfo:
            convert_text(SOURCE_TEXT, target)
        assert not excinfo.value.report.ok
        assert excinfo.value.report.target == target


def test_non_finite_float_rules():
    metadata = {"score": float("inf")}
    report = check_metadata(metadata, "json")
    assert not report.ok
    assert report.issues[0].rule == "non-finite-float"
    assert check_metadata(metadata, "toml").ok


def test_non_string_keys_rejected_for_both_targets():
    metadata = {1: "one"}
    for target in ("json", "toml"):
        report = check_metadata(metadata, target)
        assert not report.ok
        assert report.issues[0].rule == "non-string-key"


def test_mixed_array_rules():
    metadata = {"items": [1, "two"]}
    report = check_metadata(metadata, "toml")
    assert not report.ok
    assert report.issues[0].rule == "mixed-array"
    assert report.issues[0].path == "items"
    assert check_metadata(metadata, "json").ok


def test_unsupported_objects_are_never_stringified():
    class Thing:
        def __str__(self):
            return "looks fine"

    for target in ("json", "toml"):
        report = check_metadata({"thing": Thing()}, target)
        assert not report.ok
        assert report.issues[0].rule == "unsupported-type"
        with pytest.raises(ConversionError):
            convert_metadata({"thing": Thing()}, target)


def test_converter_results_are_checked_again():
    # a converter cannot smuggle in values the target cannot express
    bad = {"published": lambda value: object()}
    report = check_metadata({"published": PUBLISHED}, "json", converters=bad)
    assert not report.ok
    assert report.issues[0].rule == "unsupported-type"

    to_null = {"summary": lambda value: None}
    report = check_metadata({"summary": "x"}, "toml", converters=to_null)
    assert not report.ok
    assert report.issues[0].rule == "null"


def test_converters_apply_at_nested_paths():
    metadata = {"authors": [{"created": PUBLISHED}]}
    converters = {"authors.0.created": lambda value: value.isoformat()}
    converted = convert_metadata(metadata, "json", converters=converters)
    assert converted == {"authors": [{"created": "2024-03-01T12:30:00+08:00"}]}


def test_convert_post_switches_handler_and_keeps_content():
    post = frontmatter.loads(SOURCE_TEXT)
    converted = convert_post(post, "json", converters=JSON_CONVERTERS)
    assert converted.metadata == APPROVED_JSON
    assert converted.content == post.content
    reloaded = frontmatter.loads(frontmatter.dumps(converted))
    assert reloaded.metadata == APPROVED_JSON


@pytest.mark.skipif(TOMLHandler is None, reason="toml package not installed")
def test_toml_datetime_keeps_timezone_offset():
    converted = convert_text(SOURCE_TEXT, "toml", converters=TOML_CONVERTERS)
    post = frontmatter.loads(converted)
    assert post["published"] == PUBLISHED
    assert post["published"].utcoffset() == datetime.timedelta(hours=8)
