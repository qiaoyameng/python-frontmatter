"""
Tests for front matter conversion with fidelity checks.

The source metadata here includes a timezone-aware datetime and a null,
which behave differently in JSON and TOML. The body must never change.
"""
import datetime

import pytest

import frontmatter
from frontmatter import (
    JSON,
    TOML,
    YAML,
    ConversionError,
    IncompatibilityReason,
    check_metadata,
    convert_metadata,
    convert_post,
    convert_text,
)
from frontmatter.default_handlers import JSONHandler, TOMLHandler, YAMLHandler


TZ = datetime.timezone(datetime.timedelta(hours=8))
PUBLISHED = datetime.datetime(2026, 10, 5, 13, 22, tzinfo=TZ)

SOURCE = """\
---
title: Migration test
published: 2026-10-05T13:22:00+08:00
reviewer: null
tags:
  - python
  - frontmatter
---

# Body heading

Everything after the closing delimiter, including this line, is fixed.
---
This triple dash is content, not a delimiter.
"""

# Everything from the newline ending the closing delimiter onward,
# including the blank line separator, is the preserved body.
_closing = SOURCE.index("\n---\n", SOURCE.index("\n", 4))
BODY = SOURCE[_closing + 1 + len("---") :]


def load_with(text, target):
    handler = {JSON: JSONHandler(), TOML: TOMLHandler(), YAML: YAMLHandler()}[target]
    return frontmatter.loads(text, handler=handler)


def test_source_parses_with_tz_datetime_and_null():
    post = frontmatter.loads(SOURCE)
    assert post["published"] == PUBLISHED
    assert post["published"].tzinfo is not None
    assert post["reviewer"] is None
    assert post.content.startswith("# Body heading")


def test_toml_reports_null_but_keeps_timezone_datetime():
    report = check_metadata(
        {"published": PUBLISHED, "reviewer": None, "title": "x"}, TOML
    )
    assert not report.compatible
    assert report.incompatible_paths() == [("reviewer",)]
    bad = report.incompatible[0]
    assert bad.reason is IncompatibilityReason.NULL_NOT_SUPPORTED
    assert bad.value is None
    assert "reviewer" in report.summary() and "null" in report.summary()


def test_toml_strict_conversion_is_blocked_before_output():
    with pytest.raises(ConversionError) as excinfo:
        convert_text(SOURCE, TOML)
    assert excinfo.value.report.incompatible_paths() == [("reviewer",)]


def test_toml_conversion_succeeds_with_approved_converter_and_reloads():
    def reviewer_converter(path, value):
        assert path == ("reviewer",)
        assert value is None
        return "unassigned"

    result = convert_text(SOURCE, TOML, converters={"reviewer": reviewer_converter})
    assert result.report.compatible
    assert result.report.converted_paths == [("reviewer",)]

    reloaded = load_with(result.text, TOML)
    assert reloaded.metadata == {
        "title": "Migration test",
        "published": PUBLISHED,
        "reviewer": "unassigned",
        "tags": ["python", "frontmatter"],
    }
    assert reloaded["published"].utcoffset() == datetime.timedelta(hours=8)
    assert result.body == BODY
    assert reloaded.content.strip() == BODY.strip()
    assert result.text.startswith("+++\n")
    assert result.text.endswith(BODY)


def test_toml_converter_result_is_rechecked_against_target_domain():
    # A converter that turns a field into null still fails for TOML.
    with pytest.raises(ConversionError) as excinfo:
        convert_metadata(
            {"reviewer": "already set"},
            TOML,
            converters={"reviewer": lambda path, value: None},
        )
    bad = excinfo.value.report.incompatible[0]
    assert bad.path == ("reviewer",)
    assert bad.reason is IncompatibilityReason.NULL_NOT_SUPPORTED


def test_json_reports_datetime_but_keeps_null():
    report = check_metadata(
        {"published": PUBLISHED, "reviewer": None, "title": "x"}, JSON
    )
    assert not report.compatible
    assert report.incompatible_paths() == [("published",)]
    bad = report.incompatible[0]
    assert bad.reason is IncompatibilityReason.DATE_TIME_NOT_SUPPORTED
    assert isinstance(bad.value, datetime.datetime)


def test_json_strict_conversion_is_blocked_before_output():
    with pytest.raises(ConversionError) as excinfo:
        convert_text(SOURCE, JSON)
    assert excinfo.value.report.incompatible_paths() == [("published",)]


def test_json_conversion_succeeds_with_approved_converter_and_reloads():
    approved = "2026-10-05T13:22:00+08:00"
    result = convert_text(
        SOURCE, JSON, converters={"published": lambda path, value: value.isoformat()}
    )
    assert result.report.compatible

    reloaded = load_with(result.text, JSON)
    assert reloaded.metadata == {
        "title": "Migration test",
        "published": approved,
        "reviewer": None,
        "tags": ["python", "frontmatter"],
    }
    assert reloaded["reviewer"] is None
    assert result.body == BODY
    assert reloaded.content.strip() == BODY.strip()
    assert result.text.startswith("{\n")
    assert result.text.endswith(BODY)


def test_non_strict_toml_returns_lossy_output_but_names_dropped_null():
    result = convert_text(SOURCE, TOML, strict=False)
    assert not result.report.compatible
    assert result.report.incompatible_paths() == [("reviewer",)]
    assert result.body == BODY
    # the document still parses; the null field is omitted
    reloaded = load_with(result.text, TOML)
    assert "reviewer" not in reloaded.metadata
    assert reloaded["published"] == PUBLISHED


def test_non_strict_json_without_honest_render_raises_with_report():
    # JSON cannot render a datetime without implicit stringification,
    # so non-strict mode still refuses and attaches the report.
    with pytest.raises(ConversionError) as excinfo:
        convert_text(SOURCE, JSON, strict=False)
    assert excinfo.value.report.incompatible_paths() == [("published",)]


def test_convert_post_preserves_content_and_handles_post_object():
    post = frontmatter.loads(SOURCE)
    result = convert_post(post, JSON, converters={"published": lambda p, v: v.isoformat()})
    assert result.report.compatible
    assert result.body == post.content
    reloaded = load_with(result.text, JSON)
    assert reloaded["published"] == "2026-10-05T13:22:00+08:00"
    assert reloaded["reviewer"] is None


@pytest.mark.parametrize(
    "metadata,target,reason",
    [
        ({"x": float("nan")}, JSON, IncompatibilityReason.NON_FINITE_FLOAT),
        ({"x": float("inf")}, JSON, IncompatibilityReason.NON_FINITE_FLOAT),
        ({"x": None}, TOML, IncompatibilityReason.NULL_NOT_SUPPORTED),
        ({"x": [1, "a"]}, TOML, IncompatibilityReason.MIXED_ARRAY),
        ({"x": [1, True]}, TOML, IncompatibilityReason.MIXED_ARRAY),
        ({"x": [1, 2.0]}, TOML, IncompatibilityReason.MIXED_ARRAY),
        ({"x": {1: "v"}}, JSON, IncompatibilityReason.NON_STRING_KEY),
        ({"x": {True: "v"}}, TOML, IncompatibilityReason.NON_STRING_KEY),
    ],
)
def test_explicit_fidelity_rules(metadata, target, reason):
    report = check_metadata(metadata, target)
    bad = report.incompatible[0]
    assert bad.reason is reason
    assert bad.path[0] == "x"


def test_aware_time_losing_offset_is_reported_for_toml():
    aware = datetime.time(13, 22, tzinfo=TZ)
    report = check_metadata({"t": aware}, TOML)
    assert report.incompatible[0].reason is (
        IncompatibilityReason.AWARE_TIME_NOT_SUPPORTED
    )


def test_unsupported_objects_are_never_stringified():
    class Custom:
        def __str__(self):
            return "should-not-appear"

    for target in (JSON, TOML):
        with pytest.raises(ConversionError) as excinfo:
            convert_metadata({"o": Custom()}, target)
        bad = excinfo.value.report.incompatible[0]
        assert bad.reason is IncompatibilityReason.UNSUPPORTED_TYPE
        assert "should-not-appear" not in excinfo.value.report.summary()


def test_nan_and_inf_are_natively_supported_by_toml_and_reload():
    for name, value in [("nan", float("nan")), ("inf", float("inf"))]:
        result = convert_text(
            f"---\nv: .{name}\n---\n\nbody {name}\n", TOML
        )
        reloaded = load_with(result.text, TOML)
        assert reloaded["v"] == value or value != value  # NaN != NaN
        assert reloaded.content == f"body {name}"


def test_yaml_target_round_trips_the_rich_metadata():
    result = convert_text(SOURCE, YAML)
    assert result.report.compatible
    reloaded = load_with(result.text, YAML)
    assert reloaded["published"] == PUBLISHED
    assert reloaded["reviewer"] is None
    assert result.body == BODY


def test_converter_failure_is_reported_not_hidden():
    def broken(path, value):
        raise RuntimeError("boom")

    with pytest.raises(ConversionError) as excinfo:
        convert_metadata({"d": PUBLISHED}, JSON, converters={"d": broken})
    bad = excinfo.value.report.incompatible[0]
    assert bad.reason is IncompatibilityReason.CONVERTER_FAILED
    assert "boom" in bad.detail


def test_string_target_names_and_tuple_paths_are_accepted():
    result = convert_metadata(
        {"a": {"d": PUBLISHED}},
        "json",
        converters={("a", "d"): lambda path, value: value.isoformat()},
    )
    assert result.metadata == {"a": {"d": "2026-10-05T13:22:00+08:00"}}


def test_round_trip_guard_detects_silent_serializer_changes():
    # A timezone-aware time silently loses its offset via toml.dumps;
    # even a hand-built "converted" value must not pass verification.
    aware = datetime.time(13, 22, tzinfo=TZ)
    with pytest.raises(ConversionError):
        convert_metadata({"t": aware}, TOML)


def test_non_strict_refuses_to_emit_unparseable_toml():
    # toml.dumps emits a mixed array it cannot reload. Even non-strict
    # mode must not hand back an invalid document.
    text = "---\nx: [1, 'a']\n---\n\nbody\n"
    with pytest.raises(ConversionError) as excinfo:
        convert_text(text, TOML, strict=False)
    report = excinfo.value.report
    assert any(
        field.reason is IncompatibilityReason.MIXED_ARRAY
        for field in report.incompatible
    )


def test_non_string_key_is_flagged_at_the_key_itself():
    report = check_metadata({"section": {7: "v"}}, JSON)
    assert report.incompatible[0].path == ("section", 7)
    assert report.incompatible[0].reason is (
        IncompatibilityReason.NON_STRING_KEY
    )
