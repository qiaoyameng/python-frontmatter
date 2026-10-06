# -*- coding: utf-8 -*-
"""
Convert front matter metadata between formats (YAML -> TOML or JSON)
without silently changing values.

Swapping handlers is not enough: YAML values like timezone-aware
datetimes, ``null``, non-string keys, mixed arrays or non-finite floats
do not map cleanly onto every target format. This module checks the
metadata against the target format's value domain *before* anything is
written out, and reports exactly which fields are incompatible.

Fidelity rules
--------------

The following rules are applied when checking metadata against a
target format (``"toml"`` or ``"json"``):

- ``datetime`` / ``date`` / ``time``: supported natively by TOML
  (timezone offsets are preserved). Not representable in JSON; provide
  an explicit converter (e.g. ``datetime.isoformat``) to convert them.
- Non-finite floats (``inf``, ``-inf``, ``nan``): valid TOML floats.
  Not valid JSON (``json.dumps`` would emit non-standard ``Infinity``
  / ``NaN`` tokens), so they are rejected for JSON.
- Non-string keys: rejected for both targets. JSON would silently
  coerce them to strings and TOML keys are always strings; both lose
  fidelity, so an explicit converter is required.
- Mixed arrays (elements of different types): valid JSON, but rejected
  for TOML, whose arrays must be homogeneous (``int`` and ``float``
  count as different types, as do ``bool`` and ``int``).
- ``null`` / ``None``: valid JSON. TOML has no null value, so it is
  rejected for TOML; convert or drop it explicitly.
- Anything else (arbitrary Python objects): rejected for both targets.
  Unsupported objects are never coerced with ``str()``; silently
  stringifying them would count data loss as success.

Callers may pass ``converters``, a mapping of dot-separated paths
(e.g. ``"published"`` or ``"authors.0.created"``) to callables. The
callable's return value replaces the original and is then checked
again against the target value domain, so a converter cannot smuggle
in values the target cannot express.

Only the front matter header is converted; the body is passed through
unchanged.
"""
from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .default_handlers import JSONHandler, TOMLHandler, YAMLHandler, BaseHandler
from .util import u

__all__ = [
    "FidelityIssue",
    "ConversionReport",
    "ConversionError",
    "check_metadata",
    "convert_metadata",
    "convert_post",
    "convert_text",
]

TARGETS = ("toml", "json")

#: path -> callable replacing the value at that path
ConverterMap = Mapping[str, Callable[[Any], Any]]


@dataclass
class FidelityIssue:
    "A single metadata value the target format cannot express."

    path: str
    rule: str
    message: str
    value: Any = None

    def __str__(self) -> str:
        return f"{self.path}: [{self.rule}] {self.message}"


@dataclass
class ConversionReport:
    "Result of checking metadata against a target format's value domain."

    target: str
    issues: list[FidelityIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def __bool__(self) -> bool:
        return self.ok

    def __str__(self) -> str:
        if self.ok:
            return f"metadata is fully expressible as {self.target}"
        lines = [f"metadata cannot be fully expressed as {self.target}:"]
        lines.extend(f"  {issue}" for issue in self.issues)
        return "\n".join(lines)


class ConversionError(ValueError):
    "Raised when metadata cannot be faithfully expressed in the target format."

    def __init__(self, report: ConversionReport):
        self.report = report
        super().__init__(str(report))


def _target_handler(target: str) -> BaseHandler:
    if target == "json":
        return JSONHandler()
    if target == "toml":
        if TOMLHandler is None:
            raise ConversionError(
                ConversionReport(
                    target,
                    [
                        FidelityIssue(
                            path="",
                            rule="missing-dependency",
                            message="the 'toml' package is not installed",
                        )
                    ],
                )
            )
        return TOMLHandler()
    raise ValueError(f"unknown target format {target!r}; expected one of {TARGETS}")


def _array_category(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return "datetime"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "table"
    return "other"


def _check_value(value: Any, path: str, target: str, issues: list[FidelityIssue]) -> None:
    """
    Check a single node against the target format's value domain.
    This does not recurse; ``_transform`` walks the tree and calls this
    for every node.
    """
    if value is None:
        if target == "toml":
            issues.append(
                FidelityIssue(
                    path=path,
                    rule="null",
                    message="TOML has no null value; convert or drop this field",
                    value=value,
                )
            )
        return

    if isinstance(value, bool) or isinstance(value, (str, int)):
        return

    if isinstance(value, float):
        if target == "json" and not math.isfinite(value):
            issues.append(
                FidelityIssue(
                    path=path,
                    rule="non-finite-float",
                    message="JSON cannot represent inf/nan; convert this value explicitly",
                    value=value,
                )
            )
        return

    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        if target == "json":
            issues.append(
                FidelityIssue(
                    path=path,
                    rule="datetime",
                    message="JSON has no datetime type; convert this value explicitly "
                    "(e.g. with datetime.isoformat)",
                    value=value,
                )
            )
        return

    if isinstance(value, (list, tuple)):
        if target == "toml":
            categories = {_array_category(item) for item in value}
            if len(categories) > 1:
                issues.append(
                    FidelityIssue(
                        path=path,
                        rule="mixed-array",
                        message="TOML arrays must be homogeneous; this array mixes "
                        + ", ".join(sorted(categories)),
                        value=value,
                    )
                )
        return

    if isinstance(value, dict):
        for key in value:
            child_path = f"{path}.{key}" if path else str(key)
            if not isinstance(key, str):
                issues.append(
                    FidelityIssue(
                        path=child_path,
                        rule="non-string-key",
                        message=f"{target} keys must be strings, got "
                        f"{type(key).__name__}; convert this key explicitly",
                        value=key,
                    )
                )
        return

    issues.append(
        FidelityIssue(
            path=path,
            rule="unsupported-type",
            message=f"values of type {type(value).__name__} cannot be expressed "
            f"as {target} and are never coerced with str(); "
            "convert this value explicitly",
            value=value,
        )
    )


def _transform(
    value: Any,
    path: str,
    target: str,
    converters: ConverterMap,
    issues: list[FidelityIssue],
) -> Any:
    """
    Apply any converter registered for ``path``, then check the result
    against the target value domain and recurse. Returns the (possibly
    converted) value.
    """
    if path in converters:
        value = converters[path](value)

    if isinstance(value, dict):
        converted = {}
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            converted[key] = _transform(item, child_path, target, converters, issues)
        _check_value(converted, path, target, issues)
        return converted

    if isinstance(value, (list, tuple)):
        converted_list = [
            _transform(item, f"{path}.{index}", target, converters, issues)
            for index, item in enumerate(value)
        ]
        _check_value(converted_list, path, target, issues)
        return converted_list

    _check_value(value, path, target, issues)
    return value


def check_metadata(
    metadata: Mapping[Any, Any],
    target: str,
    converters: ConverterMap | None = None,
) -> ConversionReport:
    """
    Check whether ``metadata`` can be faithfully expressed in ``target``
    (``"toml"`` or ``"json"``), applying ``converters`` first. Returns a
    :py:class:`ConversionReport` listing every incompatible field.
    """
    _target_handler(target)  # validates target and dependencies
    issues: list[FidelityIssue] = []
    _transform(dict(metadata), "", target, converters or {}, issues)
    return ConversionReport(target, issues)


def convert_metadata(
    metadata: Mapping[Any, Any],
    target: str,
    converters: ConverterMap | None = None,
) -> dict[Any, Any]:
    """
    Return a copy of ``metadata`` with ``converters`` applied, ready to be
    exported as ``target``. Raises :py:class:`ConversionError` if any value
    cannot be faithfully expressed in the target format.
    """
    _target_handler(target)
    issues: list[FidelityIssue] = []
    converted = _transform(dict(metadata), "", target, converters or {}, issues)
    report = ConversionReport(target, issues)
    if not report.ok:
        raise ConversionError(report)
    return converted


def convert_post(
    post: Any,
    target: str,
    converters: ConverterMap | None = None,
) -> Any:
    """
    Return a new :py:class:`Post <frontmatter.Post>` whose metadata is
    converted for ``target`` and whose handler matches the target format.
    The content is carried over unchanged. Raises
    :py:class:`ConversionError` on incompatible metadata.
    """
    from frontmatter import Post

    converted = convert_metadata(post.metadata, target, converters=converters)
    return Post(post.content, handler=_target_handler(target), **converted)


def convert_text(
    text: str | bytes,
    target: str,
    handler: BaseHandler | None = None,
    converters: ConverterMap | None = None,
    encoding: str = "utf-8",
    **export_kwargs: Any,
) -> str:
    """
    Convert the front matter of ``text`` to ``target`` (``"toml"`` or
    ``"json"``) and return the full document. Only the header is
    converted; the body is passed through unchanged.

    The source format is detected with ``handler`` (YAML by default).
    Raises :py:class:`ConversionError` before writing anything if the
    metadata cannot be faithfully expressed in the target format.
    """
    text = u(text, encoding)
    handler = handler or YAMLHandler()
    if not handler.detect(text):
        raise ValueError("no front matter found in text")

    fm, content = handler.split(text)
    metadata = handler.load(fm) or {}
    if not isinstance(metadata, dict):
        raise ValueError("front matter did not parse to a dictionary")

    converted = convert_metadata(metadata, target, converters=converters)
    exported = _target_handler(target).export(converted, **export_kwargs)

    if target == "json":
        header = exported
    else:
        header = f"+++\n{exported}\n+++"

    # `content` still carries its original leading newline(s)
    return header + "\n" + content
