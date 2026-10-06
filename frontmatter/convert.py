# -*- coding: utf-8 -*-
"""
Convert front matter between YAML, JSON and TOML, checking that the
target format can actually represent every field before anything is
written out. The document body is never touched.

Naively swapping the handler is unsafe. Each of these formats has a
different value domain, and the serializers hide the gaps:

- ``json.dumps`` silently turns non-string keys (``1``, ``true`` ...)
  into strings, and emits non-standard ``NaN``/``Infinity`` tokens that
  strict JSON readers reject.
- ``toml.dumps`` drops keys whose value is ``None``, stringifies any
  object it does not recognize, serializes a ``set`` or ``bytes`` as an
  array of arbitrary contents, drops the offset of a timezone-aware
  ``datetime.time``, and happily writes mixed-type arrays it cannot
  reload itself.
- Both JSON and TOML require string mapping keys.

Fidelity rules
--------------

==============================  =============  =============
Value                           JSON           TOML
==============================  =============  =============
``str``, ``int``, ``bool``      supported      supported
finite ``float``                supported      supported
non-finite ``float``            incompatible   supported
(``nan`` / ``inf`` / ``-inf``) (no strict      (``nan``,
                                JSON token)    ``inf``, ``-inf``)
``None``                        supported      incompatible
                                (``null``)     (key is dropped
                                               on dump)
``datetime.datetime``           incompatible   supported, but
(all kinds)                                    only with ``tzinfo``
                                               ``None`` or a fixed
                                               offset; reloaded as
                                               an equal value
``datetime.date``               incompatible   supported
``datetime.time``               incompatible   supported only when
                                               naive; a timezone-aware
                                               time loses its offset
``tuple``                       normalized     normalized
                                to list        to list
``list``                        supported;     supported only when
                                any element    homogeneous, using the
                                types allowed  installed ``toml``
                                               library's categories
                                               (``int`` and ``float``
                                               are distinct; arrays
                                               of arrays are each
                                               checked independently)
``dict``                        supported;     supported; all keys
                                all keys must  must be ``str``
                                be ``str``
``set``, ``bytes``, custom or   incompatible   incompatible
otherwise unsupported object    (never         (never implicitly
                                 ``str()``)     ``str()``-ed)
==============================  =============  =============

YAML's safe representation covers everything above except arbitrary
custom objects, so converting *to* YAML generally loses nothing; the
same pre-write check still runs.

Callers may provide an explicit converter for a specific path. A
converter receives the path (a tuple of mapping keys) and the original
value, and returns the replacement value. The result is checked again
against the target value domain -- a converter that turns something
into ``None`` for TOML is still reported as incompatible. Converters
are matched by exact mapping-key paths, given as dotted strings
(``"published.at"``) or tuples (``("published", "at")``); list
positions are skipped when matching.

Typical usage::

    >>> import frontmatter
    >>> import datetime
    >>> from frontmatter.convert import convert_text, TOML
    >>> text = '''---
    ... title: Hello
    ... published: 2026-10-05T13:22:00+08:00
    ... reviewer: null
    ... ---
    ...
    ... Body stays exactly as written.
    ... '''
    >>> converted = convert_text(text, TOML, strict=False)
    >>> converted.report.compatible
    False
    >>> [f.path for f in converted.report.incompatible]
    [('reviewer',)]
    >>> converted.metadata['published']
    datetime.datetime(2026, 10, 5, 13, 22, tzinfo=datetime.timezone(datetime.timedelta(seconds=28800)))

"""
from __future__ import annotations

import datetime as _dt
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Mapping

from .default_handlers import (
    BaseHandler,
    JSONHandler,
    TOMLHandler,
    YAMLHandler,
)

if TYPE_CHECKING:
    from . import Post


__all__ = [
    "TargetFormat",
    "JSON",
    "TOML",
    "YAML",
    "IncompatibilityReason",
    "IncompatibleField",
    "ConversionReport",
    "ConversionError",
    "Converter",
    "PathConverters",
    "ConvertedMetadata",
    "ConvertedText",
    "check_metadata",
    "convert_metadata",
    "convert_post",
    "convert_text",
]


class TargetFormat(Enum):
    """The three front matter formats this package can convert to."""

    JSON = "json"
    TOML = "toml"
    YAML = "yaml"


JSON = TargetFormat.JSON
TOML = TargetFormat.TOML
YAML = TargetFormat.YAML


class IncompatibilityReason(Enum):
    """Why a field cannot be represented faithfully in the target."""

    NON_FINITE_FLOAT = "non-finite float has no strict representation"
    NULL_NOT_SUPPORTED = "null is not supported and the key would be dropped"
    DATE_TIME_NOT_SUPPORTED = "date/time has no native representation"
    AWARE_TIME_NOT_SUPPORTED = (
        "timezone-aware time loses its offset when serialized"
    )
    NON_STRING_KEY = "mapping keys must be strings"
    MIXED_ARRAY = "array mixes value types and cannot be reloaded"
    UNSUPPORTED_TYPE = "value type is not supported and must not be stringified"
    CONVERTER_FAILED = "explicit converter raised an exception"


Converter = Callable[[tuple[Any, ...], Any], Any]
PathConverters = Mapping[str | tuple[Any, ...], Converter]


@dataclass(frozen=True)
class IncompatibleField:
    """A single metadata field that cannot be represented in the target."""

    path: tuple[Any, ...]
    reason: IncompatibilityReason
    value: Any
    detail: str = ""

    @property
    def path_string(self) -> str:
        "Human-readable dotted path, using ``repr`` for non-string keys."
        parts = []
        for part in self.path:
            if isinstance(part, str) and part.isidentifier():
                parts.append(part)
            else:
                parts.append(repr(part))
        return ".".join(parts) if parts else "<root>"


@dataclass
class ConversionReport:
    """Result of checking metadata against a target format."""

    target: TargetFormat
    incompatible: list[IncompatibleField] = field(default_factory=list)
    converted_paths: list[tuple[Any, ...]] = field(default_factory=list)

    @property
    def compatible(self) -> bool:
        "True when no incompatible fields were found."
        return not self.incompatible

    def incompatible_paths(self) -> list[tuple[Any, ...]]:
        "Paths of every incompatible field, in traversal order."
        return [item.path for item in self.incompatible]

    def summary(self) -> str:
        "Multi-line report naming each incompatible field and why."
        if self.compatible:
            return f"All metadata is representable as {self.target.value.upper()}."
        lines = [
            f"Cannot represent these fields as {self.target.value.upper()}:"
        ]
        for item in self.incompatible:
            detail = f": {item.detail}" if item.detail else ""
            lines.append(
                f"  - {item.path_string}: {item.reason.value}{detail}"
            )
        return "\n".join(lines)


class ConversionError(ValueError):
    """Raised in strict mode when metadata is not fully representable."""

    def __init__(self, report: ConversionReport) -> None:
        self.report = report
        super().__init__(report.summary())


def _toml_category(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, _dt.datetime):
        return "date"
    if isinstance(value, _dt.date):
        return "date"
    if isinstance(value, _dt.time):
        return "time"
    if isinstance(value, float):
        return "float"
    if isinstance(value, int):
        return "int"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "inline_object"
    return None


def _is_non_finite(value: Any) -> bool:
    return isinstance(value, float) and not math.isfinite(value)


def _normalized_converters(
    converters: PathConverters | None,
) -> dict[tuple[Any, ...], Converter]:
    normalized: dict[tuple[Any, ...], Converter] = {}
    if not converters:
        return normalized
    for path, converter in converters.items():
        if isinstance(path, tuple):
            key = path
        else:
            key = tuple(part for part in path.split(".") if part)
        normalized[key] = converter
    return normalized


def _mapping_path(path: tuple[Any, ...]) -> tuple[Any, ...]:
    "Path with list positions removed, for converter lookup."
    return tuple(part for part in path if not isinstance(part, int))


def _add_incompatible(
    report: ConversionReport,
    path: tuple[Any, ...],
    reason: IncompatibilityReason,
    value: Any,
    detail: str = "",
) -> None:
    report.incompatible.append(
        IncompatibleField(path=path, reason=reason, value=value, detail=detail)
    )


def _check_and_convert(
    value: Any,
    path: tuple[Any, ...],
    target: TargetFormat,
    converters: dict[tuple[Any, ...], Converter],
    report: ConversionReport,
    apply_converter: bool,
) -> Any:
    """
    Return the converted value at ``path``, recording every
    incompatibility in ``report``. Containers are rebuilt, so the
    original metadata is never mutated.
    """
    converter = converters.get(_mapping_path(path))
    if converter is not None and apply_converter:
        try:
            value = converter(path, value)
        except Exception as exc:  # converter bugs must not be hidden
            _add_incompatible(
                report,
                path,
                IncompatibilityReason.CONVERTER_FAILED,
                value,
                detail=f"{type(exc).__name__}: {exc}",
            )
            return value
        report.converted_paths.append(path)
        # The converter result is checked against the same value domain
        # below, so a converter that returns None for TOML still fails.

    if value is None:
        if target is TOML:
            _add_incompatible(
                report, path, IncompatibilityReason.NULL_NOT_SUPPORTED, value
            )
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, (str, int)):
        return value

    if isinstance(value, float):
        if _is_non_finite(value):
            if target is JSON:
                _add_incompatible(
                    report,
                    path,
                    IncompatibilityReason.NON_FINITE_FLOAT,
                    value,
                    detail=repr(value),
                )
            return value
        return value

    if isinstance(value, dict):
        converted: dict[Any, Any] = {}
        for key, item in value.items():
            if target is not YAML and not isinstance(key, str):
                _add_incompatible(
                    report,
                    path + (key,),
                    IncompatibilityReason.NON_STRING_KEY,
                    key,
                    detail=f"{type(key).__name__} key {key!r}",
                )
            converted[key] = _check_and_convert(
                item,
                path + (key,),
                target,
                converters,
                report,
                apply_converter,
            )
        return converted

    if isinstance(value, (list, tuple)):
        sequence = list(value)
        converted_list = [
            _check_and_convert(
                item,
                path + (index,),
                target,
                converters,
                report,
                apply_converter,
            )
            for index, item in enumerate(sequence)
        ]
        if target is TOML:
            categories = {
                category
                for item in converted_list
                if (category := _toml_category(item)) is not None
            }
            if len(categories) > 1:
                names = sorted(categories)
                _add_incompatible(
                    report,
                    path,
                    IncompatibilityReason.MIXED_ARRAY,
                    value,
                    detail="array mixes " + ", ".join(names),
                )
        return converted_list

    if target is YAML:
        # yaml.SafeDumper handles date/time, set, bytes, tuples-as-lists
        # and non-finite floats; only truly unknown objects fail.
        if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
            return value
        if isinstance(value, (set, frozenset)):
            return set(
                _check_and_convert(
                    item,
                    path + (index,),
                    target,
                    converters,
                    report,
                    apply_converter,
                )
                for index, item in enumerate(value)
            )
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        _add_incompatible(
            report,
            path,
            IncompatibilityReason.UNSUPPORTED_TYPE,
            value,
            detail=f"{type(value).__module__}.{type(value).__name__}",
        )
        return value

    if isinstance(value, _dt.time):
        if target is TOML:
            if value.tzinfo is not None:
                _add_incompatible(
                    report,
                    path,
                    IncompatibilityReason.AWARE_TIME_NOT_SUPPORTED,
                    value,
                    detail="the UTC offset would be discarded",
                )
            return value
        _add_incompatible(
            report,
            path,
            IncompatibilityReason.DATE_TIME_NOT_SUPPORTED,
            value,
            detail=value.isoformat(),
        )
        return value

    if isinstance(value, (_dt.datetime, _dt.date)):
        if target is TOML:
            return value
        _add_incompatible(
            report,
            path,
            IncompatibilityReason.DATE_TIME_NOT_SUPPORTED,
            value,
            detail=value.isoformat(),
        )
        return value

    # Anything else (set, bytes, pathlib, custom objects...) is not
    # representable. It is never implicitly str()-ed into success.
    _add_incompatible(
        report,
        path,
        IncompatibilityReason.UNSUPPORTED_TYPE,
        value,
        detail=f"{type(value).__module__}.{type(value).__name__}",
    )
    return value


def check_metadata(
    metadata: Mapping[str, Any],
    target: TargetFormat | str,
) -> ConversionReport:
    """
    Check whether ``metadata`` can be represented in ``target`` without
    loss. No converters are applied, so a field that only becomes
    representable through an explicit converter is still reported.

    Return a :class:`ConversionReport` listing every incompatible field.
    """
    target = TargetFormat(target)
    report = ConversionReport(target=target)
    _check_and_convert(
        dict(metadata), (), target, {}, report, apply_converter=False
    )
    return report


@dataclass(frozen=True)
class ConvertedMetadata:
    """Converted metadata plus the report describing its fidelity."""

    metadata: dict[str, Any]
    report: ConversionReport


def _handler_for(target: TargetFormat) -> BaseHandler:
    if target is JSON:
        return JSONHandler()
    if target is YAML:
        return YAMLHandler()
    if TOMLHandler is None:  # pragma: no cover - toml is a test dep
        raise RuntimeError(
            "The toml package is required for TOML front matter. "
            "Install python-frontmatter[toml]."
        )
    return TOMLHandler()


def _export(metadata: Mapping[str, Any], target: TargetFormat) -> str:
    if target is JSON:
        return _export_json(metadata, strict=True)
    if target is YAML:
        return YAMLHandler().export(dict(metadata))
    assert TOMLHandler is not None
    return TOMLHandler().export(dict(metadata))


def _export_json(metadata: Mapping[str, Any], *, strict: bool) -> str:
    # With allow_nan=False non-finite floats fail loudly instead of
    # emitting non-standard NaN/Infinity tokens. Non-strict output keeps
    # the serializer default so a document is still produced while the
    # report names the offending field.
    return json.dumps(metadata, indent=4, allow_nan=not strict)


def _values_equal(left: Any, right: Any) -> bool:
    """
    Structural equality that treats all NaN payloads as equal, matching
    what a round trip through a format that supports NaN means.
    """
    if isinstance(left, float) and isinstance(right, float):
        if math.isnan(left) and math.isnan(right):
            return True
        return left == right
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left.keys()) != set(right.keys()):
            return False
        return all(_values_equal(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            _values_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def convert_metadata(
    metadata: Mapping[str, Any],
    target: TargetFormat | str,
    *,
    converters: PathConverters | None = None,
    strict: bool = True,
) -> ConvertedMetadata:
    """
    Build a target-safe copy of ``metadata`` and report its fidelity.

    Fields with a matching entry in ``converters`` are replaced by the
    converter's return value, then re-checked against the target value
    domain. When ``strict`` is true (the default),
    :class:`ConversionError` is raised if any field remains
    incompatible; otherwise the converted metadata is still returned
    alongside the report.

    On success the exported metadata is loaded back and compared to the
    converted values, guarding against serializer behavior the static
    check does not cover.
    """
    target = TargetFormat(target)
    report = ConversionReport(target=target)
    normalized = _normalized_converters(converters)
    converted = _check_and_convert(
        dict(metadata), (), target, normalized, report, apply_converter=True
    )
    assert isinstance(converted, dict)

    if strict and not report.compatible:
        raise ConversionError(report)

    if report.compatible:
        # Final guard: serialize and reload using the real handler, and
        # make sure nothing was silently changed.
        handler = _handler_for(target)
        exported = _export(converted, target)
        reloaded = handler.load(exported)
        if not isinstance(reloaded, dict) or not _values_equal(
            converted, reloaded
        ):
            raise ConversionError(
                ConversionReport(
                    target=target,
                    incompatible=[
                        IncompatibleField(
                            path=(),
                            reason=IncompatibilityReason.UNSUPPORTED_TYPE,
                            value=converted,
                            detail=(
                                "round trip through "
                                f"{target.value.upper()} changed the metadata"
                            ),
                        )
                    ],
                )
            )

    return ConvertedMetadata(metadata=converted, report=report)


def _detect_source(text: str) -> BaseHandler | None:
    for handler in (YAMLHandler(), TOMLHandler(), JSONHandler()):
        if handler.detect(text):
            return handler
    return None


def _split_body(text: str, handler: BaseHandler) -> tuple[str, str]:
    """
    Split into front matter text and the exact, unmodified body, keeping
    every byte after the closing delimiter (including blank lines).
    """
    if isinstance(handler, JSONHandler):
        match = re.match(
            r"\A\{\n(?P<fm>.*?)^\}(?P<body>.*)\Z",
            text,
            re.S | re.M,
        )
        if match is None:
            raise ValueError("Could not split JSON front matter")
        return "{" + match.group("fm") + "}", match.group("body")

    if isinstance(handler, TOMLHandler):
        boundary = r"\+{3,}[ \t]*"
    else:
        boundary = r"-{3,}[ \t]*"
    # The closing match stops just before the newline that ends the
    # delimiter line, so ``body`` keeps that newline and is preserved
    # byte-for-byte.
    opening = re.match(rf"\A{boundary}\n", text)
    if opening is None:
        raise ValueError("Could not find an opening front matter delimiter")
    closing = re.compile(rf"^{boundary}$", re.MULTILINE).search(
        text, opening.end()
    )
    if closing is None:
        raise ValueError("Could not find a closing front matter delimiter")
    fm = text[opening.end() : closing.start()]
    body = text[closing.end() :]
    return fm, body


@dataclass(frozen=True)
class ConvertedText:
    """A converted document, its body and the conversion report."""

    text: str
    metadata: dict[str, Any]
    body: str
    report: ConversionReport


def _render_front_matter(
    exported: str, target: TargetFormat, body: str
) -> str:
    if target is JSON:
        # JSON body begins right after the closing brace, exactly as the
        # raw input did.
        return exported + body
    delimiter = "---" if target is YAML else "+++"
    if target is TOML:
        # toml.dumps ends with a trailing newline; the extra newline
        # matches the standard front matter template.
        return f"{delimiter}\n{exported}\n{delimiter}{body}"
    # YAML export has no trailing newline
    return f"{delimiter}\n{exported}\n{delimiter}{body}"


def convert_post(
    post: Post,
    target: TargetFormat | str,
    *,
    converters: PathConverters | None = None,
    strict: bool = True,
) -> ConvertedText:
    """
    Convert a loaded :class:`Post <frontmatter.Post>` to ``target``.

    The post's ``content`` is used verbatim in the rendered result.
    """
    target = TargetFormat(target)
    result = convert_metadata(
        post.metadata, target, converters=converters, strict=strict
    )
    if result.report.compatible:
        exported = _export(result.metadata, target)
    else:
        exported = _render_lossy_or_raise(
            result.metadata, target, result.report
        )
    rendered = _render_front_matter(exported, target, "\n\n" + post.content)
    return ConvertedText(
        text=rendered,
        metadata=result.metadata,
        body=post.content,
        report=result.report,
    )


def _best_effort_export(
    metadata: Mapping[str, Any],
    target: TargetFormat,
) -> str:
    """
    Render something useful for a failed, non-strict conversion without
    pretending it is valid. Null TOML fields are omitted so the output
    parses; other incompatibilities render through the normal handler.
    """
    if target is TOML:
        safe = _drop_toml_nulls(metadata)
        return _export(safe, target)
    try:
        return _export_json(metadata, strict=False)
    except (TypeError, ValueError):
        # Some rejected fields (dates, custom objects) have no textual
        # form at all; surface the report rather than stringifying.
        report = check_metadata(metadata, target)
        raise ConversionError(report)


def _drop_toml_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_toml_nulls(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_drop_toml_nulls(item) for item in value]
    return value


def _render_lossy_or_raise(
    metadata: Mapping[str, Any],
    target: TargetFormat,
    report: ConversionReport,
) -> str:
    """
    Render a best-effort (lossy) document for a failed conversion and
    confirm it at least parses in the target format. Raises with
    ``report`` when no honest, parseable rendering exists.
    """
    exported = _best_effort_export(metadata, target)
    try:
        reparsed = _handler_for(target).load(exported)
    except Exception as exc:
        report.incompatible.append(
            IncompatibleField(
                path=(),
                reason=IncompatibilityReason.UNSUPPORTED_TYPE,
                value=metadata,
                detail=(
                    "the rendered output is not valid "
                    f"{target.value.upper()}: {type(exc).__name__}: {exc}"
                ),
            )
        )
        raise ConversionError(report)
    if not isinstance(reparsed, dict):
        raise ConversionError(report)
    return exported


def convert_text(
    text: str,
    target: TargetFormat | str,
    *,
    converters: PathConverters | None = None,
    strict: bool = True,
    source_handler: BaseHandler | None = None,
    encoding: str = "utf-8",
) -> ConvertedText:
    """
    Convert only the front matter of ``text`` to ``target``; the body is
    preserved byte-for-byte.

    The source format is detected automatically unless ``source_handler``
    is given. With ``strict=False`` a result is returned even when
    fields are incompatible, and :attr:`ConvertedText.report` names
    them; otherwise :class:`ConversionError` is raised before any output
    is produced.

    Successful results have been written and reloaded with the target
    handler, and the reloaded metadata equals the converted values.
    """
    from .util import u

    target = TargetFormat(target)
    text = u(text, encoding)

    handler = source_handler or _detect_source(text)
    if handler is None:
        raise ValueError("No front matter detected in the given text")

    fm_text, raw_body = _split_body(text, handler)
    loaded = handler.load(fm_text)
    metadata = loaded if isinstance(loaded, dict) else {}

    result = convert_metadata(
        metadata, target, converters=converters, strict=strict
    )

    if not result.report.compatible:
        # In non-strict mode still return a document, but make the loss
        # visible in the report; never claim success.
        # Even a lossy document must parse; otherwise refuse.
        exported = _render_lossy_or_raise(
            result.metadata, target, result.report
        )
    else:
        exported = _export(result.metadata, target)

    converted_text = _render_front_matter(exported, target, raw_body)
    return ConvertedText(
        text=converted_text,
        metadata=result.metadata,
        body=raw_body,
        report=result.report,
    )
