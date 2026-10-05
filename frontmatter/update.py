# -*- coding: utf-8 -*-
"""
Byte-oriented front matter updates.

The text API (:py:func:`frontmatter.load` / :py:func:`frontmatter.dumps`)
normalizes newlines and strips whitespace, which rewrites the whole
document. The functions in this module exist for callers that only want
to touch metadata -- for example, flipping a ``published`` flag -- while
leaving the body of the document byte-for-byte identical.

Only the front matter region is decoded, re-serialized and replaced.
Everything after the closing delimiter is copied through untouched:
no newline conversion, no stripping, no re-encoding.

Nothing is ever guessed. If the encoding is unsupported, the front
matter is missing (and ``insert`` was not requested) or unclosed, a
diagnosable error is raised and the input is left alone.
"""
from __future__ import annotations

import codecs
import os
import pathlib
import re
import tempfile
from typing import TYPE_CHECKING, BinaryIO, Iterable

from .default_handlers import YAMLHandler

if TYPE_CHECKING:
    from .default_handlers import BaseHandler


__all__ = [
    "FrontmatterUpdateError",
    "UnsupportedEncodingError",
    "MissingFrontmatterError",
    "UnclosedFrontmatterError",
    "update_metadata_bytes",
    "update_metadata_stream",
    "update_metadata_file",
]


UTF8_BOM = b"\xef\xbb\xbf"

# BOMs we can recognize but not work with, longest first so that
# the UTF-32-LE prefix is not mistaken for UTF-16-LE.
_UNSUPPORTED_BOMS = [
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
]


class FrontmatterUpdateError(ValueError):
    """
    Base class for errors raised while updating front matter in place.
    """


class UnsupportedEncodingError(FrontmatterUpdateError):
    """
    The document (or requested encoding) is not something that can be
    safely updated byte-wise. Only ASCII-compatible encodings are
    supported; UTF-16/UTF-32 documents are rejected, not guessed at.
    """


class MissingFrontmatterError(FrontmatterUpdateError):
    """
    No front matter was found at the start of the document and
    ``insert`` was not requested.
    """


class UnclosedFrontmatterError(FrontmatterUpdateError):
    """
    An opening delimiter was found but no closing delimiter follows it.
    """


def _check_encoding(encoding: str) -> None:
    "Reject encodings we cannot safely process at the byte level."
    try:
        codecs.lookup(encoding)
    except LookupError:
        raise UnsupportedEncodingError(f"Unknown encoding: {encoding!r}")
    # Delimiters and newlines are matched as raw ASCII bytes, which is
    # only safe for ASCII-compatible encodings.
    if "---\n".encode(encoding) != b"---\n":
        raise UnsupportedEncodingError(
            f"Encoding {encoding!r} is not ASCII-compatible; "
            "refusing to update front matter at the byte level"
        )


def _split_bom(data: bytes) -> tuple[bytes, bytes]:
    "Split off a BOM, rejecting encodings we do not support."
    for bom, name in _UNSUPPORTED_BOMS:
        if data.startswith(bom):
            raise UnsupportedEncodingError(
                f"Document starts with a {name} byte order mark; "
                "only UTF-8 (with or without BOM) is supported"
            )
    if data.startswith(UTF8_BOM):
        return UTF8_BOM, data[len(UTF8_BOM) :]
    return b"", data


def _delimiter_tokens(handler: BaseHandler) -> tuple[bytes, bytes]:
    "Return (start, end) delimiter tokens for a handler, as bytes."
    start = handler.START_DELIMITER or ""
    end = handler.END_DELIMITER or ""
    if not start and not end:
        # JSONHandler: metadata is a bare JSON object, so the
        # delimiters are the braces on their own lines.
        start, end = "{", "}"
    return start.encode("ascii"), end.encode("ascii")


def _boundary_pattern(token: bytes) -> re.Pattern[bytes]:
    """
    Build a line-anchored byte pattern for a delimiter token.

    Unlike the text-mode patterns, trailing whitespace is limited to
    spaces/tabs plus an optional CR, so a match can never swallow the
    newline itself (or blank lines belonging to the body).
    """
    if token and len(set(token)) == 1 and token[:1] not in b"{}":
        # e.g. --- or +++: allow three or more, like the text patterns
        body = re.escape(token[:1]) + rb"{3,}"
    else:
        body = re.escape(token)
    return re.compile(rb"^" + body + rb"[ \t]*\r?$", re.MULTILINE)


def _line_end(data: bytes, pos: int) -> int:
    "Advance past a trailing newline (the CR is consumed by the pattern)."
    if data[pos : pos + 1] == b"\n":
        return pos + 1
    return pos


def _sniff_newline(data: bytes) -> str:
    "Detect the newline style used in a region, defaulting to LF."
    index = data.find(b"\n")
    if index > 0 and data[index - 1 : index] == b"\r":
        return "\r\n"
    return "\n"


def _detect_handler(
    rest: bytes, candidates: Iterable[BaseHandler]
) -> BaseHandler | None:
    "Find a handler whose start delimiter opens the document."
    for handler in candidates:
        start_token, _ = _delimiter_tokens(handler)
        if _boundary_pattern(start_token).match(rest):
            return handler
    return None


def _serialize_header(
    handler: BaseHandler,
    metadata: dict[str, object],
    newline: str,
    encoding: str,
    **kwargs: object,
) -> bytes:
    "Serialize a complete front matter block, including delimiters."
    exported = handler.export(metadata, **kwargs)
    # handlers export with LF; match the document's own newline style
    exported = exported.replace("\r\n", "\n").replace("\n", newline)
    start = handler.START_DELIMITER or ""
    end = handler.END_DELIMITER or ""
    if start or end:
        text = start + newline + exported + newline + end + newline
    else:
        # JSON: exported text already includes the braces
        text = exported + newline
    return text.encode(encoding)


def update_metadata_bytes(
    data: bytes,
    metadata: dict[str, object],
    encoding: str = "utf-8",
    handler: BaseHandler | None = None,
    insert: bool = False,
    **kwargs: object,
) -> bytes:
    """
    Update front matter metadata in ``data`` and return new bytes.

    ``data`` is the full document as bytes. ``metadata`` is merged into
    the existing metadata (new keys are added, existing keys replaced).
    Only the front matter region is re-serialized; the body after the
    closing delimiter is preserved byte-for-byte.

    A UTF-8 BOM, if present, is preserved. Documents in UTF-16/UTF-32
    or other non-ASCII-compatible encodings raise
    :py:class:`UnsupportedEncodingError`.

    If the document has no front matter, ``insert=True`` adds a new
    block (YAML by default, or ``handler`` if given); otherwise
    :py:class:`MissingFrontmatterError` is raised. An opening delimiter
    without a closing one raises :py:class:`UnclosedFrontmatterError`.
    """
    if isinstance(data, str):
        raise TypeError("update_metadata_bytes expects bytes, not str")
    if not isinstance(metadata, dict):
        raise TypeError("metadata must be a dict")

    _check_encoding(encoding)
    bom, rest = _split_bom(data)

    if handler is None:
        from frontmatter import handlers as default_handlers

        handler = _detect_handler(rest, default_handlers)

    if handler is None:
        if not insert:
            raise MissingFrontmatterError(
                "No front matter found at the start of the document; "
                "pass insert=True to add a new block"
            )
        handler = YAMLHandler()
        newline = _sniff_newline(rest)
        header = _serialize_header(handler, metadata, newline, encoding, **kwargs)
        # blank line between the new header and the untouched body
        return bom + header + newline.encode("ascii") + rest

    start_token, end_token = _delimiter_tokens(handler)
    start_pattern = _boundary_pattern(start_token)
    end_pattern = _boundary_pattern(end_token)

    start_match = start_pattern.match(rest)
    if start_match is None:
        if not insert:
            raise MissingFrontmatterError(
                f"No front matter found at the start of the document for "
                f"{type(handler).__name__}; pass insert=True to add a new block"
            )
        newline = _sniff_newline(rest)
        header = _serialize_header(handler, metadata, newline, encoding, **kwargs)
        return bom + header + newline.encode("ascii") + rest

    fm_start = _line_end(rest, start_match.end())
    end_match = end_pattern.search(rest, fm_start)
    if end_match is None:
        raise UnclosedFrontmatterError(
            f"Front matter opens at byte offset {len(bom)} with delimiter "
            f"{start_token.decode('ascii')!r} but no closing delimiter "
            f"{end_token.decode('ascii')!r} was found"
        )

    body = rest[_line_end(rest, end_match.end()) :]

    try:
        header_text = rest[: _line_end(rest, end_match.end())].decode(encoding)
    except UnicodeDecodeError as exc:
        raise UnsupportedEncodingError(
            f"Front matter is not valid {encoding}: {exc}"
        ) from exc

    # let the handler split its own front matter (JSONHandler, for
    # example, wraps the region back in braces before parsing)
    try:
        fm_text, _ = handler.split(header_text)
    except ValueError:
        raise UnclosedFrontmatterError(
            f"Front matter opens at byte offset {len(bom)} with delimiter "
            f"{start_token.decode('ascii')!r} but no closing delimiter "
            f"{end_token.decode('ascii')!r} was found"
        )

    existing = handler.load(fm_text) or {}
    if not isinstance(existing, dict):
        raise FrontmatterUpdateError(
            f"Front matter parsed as {type(existing).__name__}, "
            "expected a mapping; refusing to update"
        )
    existing.update(metadata)

    newline = _sniff_newline(rest[: end_match.end()])
    header = _serialize_header(handler, existing, newline, encoding, **kwargs)
    return bom + header + body


def update_metadata_stream(
    source: BinaryIO,
    target: BinaryIO,
    metadata: dict[str, object],
    encoding: str = "utf-8",
    handler: BaseHandler | None = None,
    insert: bool = False,
    **kwargs: object,
) -> int:
    """
    Read a document from binary stream ``source``, update its front
    matter and write the result to binary stream ``target``.

    The source is only read, never written. The target receives the
    complete updated document in one write, after the update has
    succeeded. Returns the number of bytes written.
    """
    data = source.read()
    if isinstance(data, str):
        raise TypeError("source must be a binary stream, not text")
    result = update_metadata_bytes(
        data, metadata, encoding=encoding, handler=handler, insert=insert, **kwargs
    )
    target.write(result)
    return len(result)


def update_metadata_file(
    path: str | pathlib.Path,
    metadata: dict[str, object],
    encoding: str = "utf-8",
    handler: BaseHandler | None = None,
    insert: bool = False,
    **kwargs: object,
) -> None:
    """
    Update the front matter of ``path`` in place, atomically.

    The updated document is written to a temporary file in the same
    directory and moved over the original only once it is complete, so
    the source file is never replaced by a partial result. If the
    update fails, the file on disk is left untouched.
    """
    file_path = pathlib.Path(path)
    data = file_path.read_bytes()
    result = update_metadata_bytes(
        data, metadata, encoding=encoding, handler=handler, insert=insert, **kwargs
    )

    fd, tmp_name = tempfile.mkstemp(
        dir=file_path.parent, prefix=file_path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(result)
        os.replace(tmp_name, file_path)
    except BaseException:
        os.unlink(tmp_name)
        raise
