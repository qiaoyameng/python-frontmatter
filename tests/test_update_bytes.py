"""
Tests for byte-oriented front matter updates (frontmatter.update).

The promise of this API: only the metadata region is re-serialized,
the body after the closing delimiter is preserved byte-for-byte.
"""
import hashlib
import io

import pytest

import frontmatter
from frontmatter.default_handlers import JSONHandler, TOMLHandler, YAMLHandler
from frontmatter.update import (
    FrontmatterUpdateError,
    MissingFrontmatterError,
    UnclosedFrontmatterError,
    UnsupportedEncodingError,
    update_metadata_bytes,
    update_metadata_file,
    update_metadata_stream,
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def body_of(data, delimiter=b"---"):
    "Return everything after the closing delimiter line."
    first = data.index(delimiter)
    second = data.index(delimiter, first + len(delimiter))
    eol = data.index(b"\n", second)
    return data[eol + 1 :]


CRLF_DOC = (
    b"---\r\n"
    b"title: Old title\r\n"
    b"published: false\r\n"
    b"---\r\n"
    b"\r\n"
    b"First line\r\n"
    b"Second line\r\n"
)

LF_DOC = (
    b"---\n"
    b"title: Old title\n"
    b"published: false\n"
    b"---\n"
    b"\n"
    b"First line\n"
    b"Second line\n"
)

TRAILING_BLANKS_DOC = b"---\ntitle: x\n---\n\nBody\n\n\n\n"

NON_ASCII_DOC = (
    "---\ntitle: 旧标题\n---\n\n正文第一行：你好，世界 🌍\n第二行。\n".encode("utf-8")
)


def test_crlf_body_preserved():
    result = update_metadata_bytes(CRLF_DOC, {"published": True})
    assert body_of(result) == body_of(CRLF_DOC)
    assert sha256(body_of(result)) == sha256(body_of(CRLF_DOC))
    # header was rewritten with CRLF newlines, not LF
    assert result.startswith(b"---\r\n")
    assert b"published: true\r\n" in result
    assert b"\r\n---\r\n" in result
    # no stray LF-only lines in the header
    header = result[: result.index(b"---", 3)]
    assert b"\n" not in header.replace(b"\r\n", b"")


def test_lf_body_preserved():
    result = update_metadata_bytes(LF_DOC, {"published": True})
    assert body_of(result) == body_of(LF_DOC)
    assert sha256(body_of(result)) == sha256(body_of(LF_DOC))
    assert b"published: true\n" in result
    assert b"\r" not in result


def test_trailing_blank_lines_preserved():
    result = update_metadata_bytes(TRAILING_BLANKS_DOC, {"published": True})
    assert body_of(result) == body_of(TRAILING_BLANKS_DOC)
    assert result.endswith(b"\n\n\n\n")


def test_non_ascii_body_preserved():
    result = update_metadata_bytes(NON_ASCII_DOC, {"published": True})
    assert body_of(result) == body_of(NON_ASCII_DOC)
    assert sha256(body_of(result)) == sha256(body_of(NON_ASCII_DOC))


def test_metadata_is_merged_not_replaced():
    result = update_metadata_bytes(LF_DOC, {"published": True})
    post = frontmatter.loads(result.decode("utf-8"))
    assert post["published"] is True
    assert post["title"] == "Old title"


def test_utf8_bom_preserved():
    doc = b"\xef\xbb\xbf" + LF_DOC
    result = update_metadata_bytes(doc, {"published": True})
    assert result.startswith(b"\xef\xbb\xbf---\n")
    assert body_of(result) == body_of(LF_DOC)


def test_utf16_bom_rejected():
    doc = "---\ntitle: x\n---\n".encode("utf-16")
    with pytest.raises(UnsupportedEncodingError):
        update_metadata_bytes(doc, {"published": True})


def test_utf32_bom_rejected():
    doc = codecs_bom_utf32() + "---\ntitle: x\n---\n".encode("utf-32-le")
    with pytest.raises(UnsupportedEncodingError):
        update_metadata_bytes(doc, {"published": True})


def codecs_bom_utf32():
    return b"\xff\xfe\x00\x00"


def test_unsupported_encoding_rejected():
    with pytest.raises(UnsupportedEncodingError):
        update_metadata_bytes(LF_DOC, {"a": 1}, encoding="utf-16")
    with pytest.raises(UnsupportedEncodingError):
        update_metadata_bytes(LF_DOC, {"a": 1}, encoding="no-such-codec")


def test_empty_frontmatter():
    doc = b"---\n---\n\nBody here\n"
    result = update_metadata_bytes(doc, {"published": True})
    assert b"published: true" in result
    assert body_of(result) == b"\nBody here\n"


def test_empty_frontmatter_blank_line():
    doc = b"---\n\n---\n\nBody here\n"
    result = update_metadata_bytes(doc, {"published": True})
    assert b"published: true" in result
    assert body_of(result) == b"\nBody here\n"


def test_unclosed_frontmatter_diagnosable():
    doc = b"---\ntitle: x\nThis line is body, not a delimiter\n"
    with pytest.raises(UnclosedFrontmatterError) as excinfo:
        update_metadata_bytes(doc, {"published": True})
    message = str(excinfo.value)
    assert "---" in message
    assert "offset" in message


def test_unclosed_frontmatter_with_bom_offset():
    doc = b"\xef\xbb\xbf---\ntitle: x\nno closing delimiter\n"
    with pytest.raises(UnclosedFrontmatterError) as excinfo:
        update_metadata_bytes(doc, {"published": True})
    assert "3" in str(excinfo.value)


def test_missing_frontmatter_refused_by_default():
    doc = b"No front matter here\nJust text\n"
    with pytest.raises(MissingFrontmatterError):
        update_metadata_bytes(doc, {"published": True})


def test_missing_frontmatter_insert_opt_in():
    doc = b"No front matter here\nJust text\n"
    result = update_metadata_bytes(doc, {"published": True}, insert=True)
    assert result.startswith(b"---\npublished: true\n---\n\n")
    assert result.endswith(doc)


def test_insert_uses_crlf_when_body_does():
    doc = b"First\r\nSecond\r\n"
    result = update_metadata_bytes(doc, {"published": True}, insert=True)
    assert result.startswith(b"---\r\npublished: true\r\n---\r\n\r\n")
    assert result.endswith(doc)


def test_toml_frontmatter():
    doc = b"+++\ntitle = \"old\"\n+++\n\nBody\n"
    result = update_metadata_bytes(doc, {"published": True})
    assert b"published = true" in result
    assert body_of(result, b"+++") == b"\nBody\n"


def test_json_frontmatter():
    doc = b'{\n    "title": "old"\n}\n\nBody\n'
    result = update_metadata_bytes(doc, {"published": True})
    assert b'"published": true' in result
    assert result.endswith(b"\nBody\n")


def test_explicit_handler_mismatch_refused():
    # YAML document, but we force the JSON handler: no JSON front matter
    with pytest.raises(MissingFrontmatterError):
        update_metadata_bytes(LF_DOC, {"a": 1}, handler=JSONHandler())


def test_non_mapping_frontmatter_refused():
    doc = b"---\n- just\n- a\n- list\n---\n\nBody\n"
    with pytest.raises(FrontmatterUpdateError):
        update_metadata_bytes(doc, {"published": True})


def test_stream_roundtrip():
    source = io.BytesIO(CRLF_DOC)
    target = io.BytesIO()
    written = update_metadata_stream(source, target, {"published": True})
    result = target.getvalue()
    assert written == len(result)
    assert body_of(result) == body_of(CRLF_DOC)
    # source was only read, never written
    assert source.getvalue() == CRLF_DOC


def test_stream_rejects_text_source():
    with pytest.raises(TypeError):
        update_metadata_stream(io.StringIO("---\n---\n"), io.BytesIO(), {"a": 1})


def test_file_update_atomic(tmp_path):
    path = tmp_path / "post.md"
    path.write_bytes(CRLF_DOC)
    update_metadata_file(path, {"published": True})
    result = path.read_bytes()
    assert b"published: true\r\n" in result
    assert body_of(result) == body_of(CRLF_DOC)
    # no temp files left behind
    assert list(tmp_path.iterdir()) == [path]


def test_file_update_failure_leaves_source_untouched(tmp_path):
    path = tmp_path / "broken.md"
    original = b"---\ntitle: x\nnever closed\n"
    path.write_bytes(original)
    with pytest.raises(UnclosedFrontmatterError):
        update_metadata_file(path, {"published": True})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_result_still_parseable_by_text_api():
    result = update_metadata_bytes(NON_ASCII_DOC, {"published": True})
    post = frontmatter.loads(result.decode("utf-8"))
    assert post["published"] is True
    assert "你好，世界" in post.content


def test_handler_instances_accepted():
    result = update_metadata_bytes(LF_DOC, {"a": 1}, handler=YAMLHandler())
    assert b"a: 1" in result
    if TOMLHandler is not None:
        doc = b"+++\na = 1\n+++\n\nBody\n"
        result = update_metadata_bytes(doc, {"b": 2}, handler=TOMLHandler())
        assert b"b = 2" in result
