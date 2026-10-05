# -*- coding: utf-8 -*-
"""Byte-preserving front matter updates."""
from __future__ import annotations

import codecs
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO

from .default_handlers import JSONHandler

if TYPE_CHECKING:
    from .default_handlers import BaseHandler


UTF8_BOM = b"\xef\xbb\xbf"
UTF16_LE_BOM = b"\xff\xfe"
UTF16_BE_BOM = b"\xfe\xff"
UTF32_LE_BOM = b"\xff\xfe\x00\x00"
UTF32_BE_BOM = b"\x00\x00\xfe\xff"


class FrontmatterError(ValueError):
    """Base class for byte-safe front matter update failures."""


class MissingFrontmatterError(FrontmatterError):
    """Raised when no front matter is found and insertion was not requested."""


class UnclosedFrontmatterError(FrontmatterError):
    """Raised when an opening front matter boundary has no closing boundary."""


class UnsupportedEncodingError(FrontmatterError):
    """Raised when an input encoding cannot be used for a byte-safe update."""


@dataclass(frozen=True)
class _Boundary:
    handler: BaseHandler
    bom_size: int
    frontmatter_start: int
    frontmatter_end: int
    body_start: int


def update_frontmatter(
    source: bytes | bytearray | memoryview | BinaryIO,
    metadata: object = None,
    destination: BinaryIO | None = None,
    *,
    encoding: str = "utf-8",
    handler: BaseHandler | None = None,
    insert: bool = False,
    **kwargs: object,
) -> bytes | None:
    """
    Update front matter while preserving every byte after its closing boundary.

    Metadata is merged into the existing metadata. A ``Post`` may also be passed,
    in which case its complete metadata mapping is used. When no front matter is
    present, ``insert=True`` is required to add one.

    The result is fully built before anything is written to ``destination``.
    If ``destination`` is omitted and ``source`` is bytes, the result is
    returned as bytes.
    """
    if destination is not None and source is destination:
        raise ValueError("source and destination must be different streams")

    data = _read_binary_source(source)
    result = _update_frontmatter_bytes(
        data,
        metadata,
        encoding=encoding,
        handler=handler,
        insert=insert,
        **kwargs,
    )

    if destination is None:
        if isinstance(source, (bytes, bytearray, memoryview)):
            return result
        raise TypeError("destination is required when source is a binary stream")

    if not callable(getattr(destination, "write", None)):
        raise TypeError("destination must be a binary stream")
    destination.write(result)
    return None


def _read_binary_source(source: bytes | bytearray | memoryview | BinaryIO) -> bytes:
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source)

    read = getattr(source, "read", None)
    if not callable(read):
        raise TypeError("source must be bytes or a binary stream")

    data = read()
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("source must be a binary stream")
    return bytes(data)


def _update_frontmatter_bytes(
    data: bytes,
    metadata: object,
    *,
    encoding: str,
    handler: BaseHandler | None,
    insert: bool,
    **kwargs: object,
) -> bytes:
    output_encoding = _supported_utf8_encoding(encoding)
    bom_size = _detect_bom_size(data)
    boundary = _find_boundary(data, [handler] if handler else None, bom_size)

    if boundary is None:
        if not insert:
            raise MissingFrontmatterError(
                "No front matter found; pass insert=True to add one"
            )
        insert_handler = handler or _post_handler(metadata) or _default_yaml_handler()
        new_metadata = _metadata_from_input(metadata)
        header = insert_handler.export_header(
            new_metadata, encoding=output_encoding, **kwargs
        )
        body = data[bom_size:]
        separator = b""
        if body and not body.startswith((b"\n", b"\r")):
            separator = b"\n"
        return data[:bom_size] + header + separator + body

    source_metadata = _load_boundary_metadata(data, boundary, output_encoding)
    new_metadata = _merge_metadata(source_metadata, metadata)
    header = boundary.handler.export_header(
        new_metadata, encoding=output_encoding, **kwargs
    )
    return data[:bom_size] + header + data[boundary.body_start:]


def _supported_utf8_encoding(encoding: str) -> str:
    try:
        codec_name = codecs.lookup(encoding).name
    except LookupError as exc:
        raise UnsupportedEncodingError(f"Unsupported encoding: {encoding!r}") from exc

    if codec_name not in {"utf-8", "utf-8-sig"}:
        raise UnsupportedEncodingError("Only UTF-8 encoded documents are supported")
    return "utf-8"


def _detect_bom_size(data: bytes) -> int:
    if data.startswith(UTF8_BOM):
        return len(UTF8_BOM)

    if data.startswith((UTF32_LE_BOM, UTF32_BE_BOM)):
        raise UnsupportedEncodingError("Only UTF-8 encoded documents are supported")

    if data.startswith((UTF16_LE_BOM, UTF16_BE_BOM)):
        raise UnsupportedEncodingError("Only UTF-8 encoded documents are supported")

    return 0


def _find_boundary(
    data: bytes,
    handlers: list[BaseHandler] | None,
    bom_size: int,
) -> _Boundary | None:
    if handlers is None:
        from . import handlers as default_handlers

        handlers = list(default_handlers)

    opening_start = bom_size
    opening, _, opening_end, _ = _read_line(data, opening_start)

    matching = [
        candidate
        for candidate in handlers
        if _line_matches_boundary(candidate, opening)
    ]
    if not matching:
        return None
    if len(matching) > 1:
        raise FrontmatterError("More than one front matter format matched")

    matched_handler = matching[0]
    position = opening_end

    while True:
        line, _, line_end, line_start = _read_line(data, position)
        if line_end == len(data) and not line:
            raise UnclosedFrontmatterError(
                "Front matter opening boundary is not closed"
            )

        if _is_closing_line(matched_handler, line):
            return _Boundary(
                handler=matched_handler,
                bom_size=bom_size,
                frontmatter_start=opening_end,
                frontmatter_end=line_start,
                body_start=line_end,
            )

        position = line_end


def _read_line(data: bytes, start: int) -> tuple[bytes, bytes, int, int]:
    cr = data.find(b"\r", start)
    lf = data.find(b"\n", start)

    if cr == -1 and lf == -1:
        return data[start:], b"", len(data), start

    if cr != -1 and (lf == -1 or cr < lf):
        if cr + 1 < len(data) and data[cr + 1] == 10:
            return data[start:cr], b"\r\n", cr + 2, start
        return data[start:cr], b"\r", cr + 1, start

    return data[start:lf], b"\n", lf + 1, start


def _line_matches_boundary(handler: BaseHandler, line: bytes) -> bool:
    pattern = getattr(handler, "FM_BOUNDARY", None)
    if pattern is None:
        return False
    try:
        return pattern.match(line.decode("utf-8")) is not None
    except UnicodeDecodeError:
        return False


def _is_closing_line(handler: BaseHandler, line: bytes) -> bool:
    if isinstance(handler, JSONHandler):
        return line == b"}"
    return _line_matches_boundary(handler, line)


def _load_boundary_metadata(
    data: bytes,
    boundary: _Boundary,
    encoding: str,
) -> dict[str, object]:
    frontmatter_bytes = data[boundary.frontmatter_start:boundary.frontmatter_end]
    try:
        frontmatter_text = frontmatter_bytes.decode(encoding)
        if isinstance(boundary.handler, JSONHandler):
            loaded = boundary.handler.load("{" + frontmatter_text + "}")
        else:
            loaded = boundary.handler.load(frontmatter_text)
    except Exception as exc:
        raise FrontmatterError("Front matter metadata could not be parsed") from exc

    if loaded is None and not frontmatter_text.strip():
        loaded = {}
    if not isinstance(loaded, dict):
        raise FrontmatterError("Front matter metadata must be a mapping")
    return loaded


def _merge_metadata(source: dict[str, object], updates: object) -> dict[str, object]:
    if updates is None:
        return source.copy()

    from . import Post

    if isinstance(updates, Post):
        return dict(updates.metadata)
    if isinstance(updates, Mapping):
        merged = source.copy()
        merged.update(updates)
        return dict(merged)
    raise TypeError("metadata must be a mapping or a Post")


def _metadata_from_input(updates: object) -> dict[str, object]:
    if updates is None:
        return {}

    from . import Post

    if isinstance(updates, Post):
        return dict(updates.metadata)
    if isinstance(updates, Mapping):
        return dict(updates)
    raise TypeError("metadata must be a mapping or a Post")


def _post_handler(metadata: object) -> BaseHandler | None:
    from . import Post

    if isinstance(metadata, Post):
        return metadata.handler
    return None


def _default_yaml_handler() -> BaseHandler:
    from .default_handlers import YAMLHandler

    return YAMLHandler()
