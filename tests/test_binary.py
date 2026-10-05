# -*- coding: utf-8 -*-

import hashlib
import io
import unittest

import frontmatter

class BinaryFrontmatterTest(unittest.TestCase):
    def test_update_preserves_mixed_line_endings_and_non_ascii_body(self):
        body = (
            "\r\nFirst CRLF line.\r\n"
            "Second LF line.\n"
            "Third CRLF line.\r\n"
            "中文\n\n"
        ).encode("utf-8")
        source = (
            b"---\r\n"
            b"title: old\r\n"
            b"published: false\r\n"
            b"---\r\n"
            + body
        )

        result = frontmatter.update_frontmatter(source, {"published": True})

        self.assertIsInstance(result, bytes)
        self.assertTrue(result.startswith(b"---\npublished: true\ntitle: old\n---\n"))
        self.assertEqual(result[len(result) - len(body) :], body)
        self.assertEqual(
            hashlib.sha256(result[len(result) - len(body) :]).digest(),
            hashlib.sha256(body).digest(),
        )

        post = frontmatter.loads(result.decode("utf-8-sig"))
        self.assertIs(post["published"], True)
        self.assertEqual(post["title"], "old")

    def test_update_preserves_utf8_bom_and_body(self):
        body = "Body with 中文.\n\n".encode("utf-8")
        source = (
            b"\xef\xbb\xbf---\n"
            b"published: false\n"
            b"---\n"
            + body
        )

        result = frontmatter.update_frontmatter(
            source, {"published": True}, encoding="utf-8-sig"
        )

        self.assertTrue(result.startswith(b"\xef\xbb\xbf---\n"))
        self.assertEqual(result[len(result) - len(body) :], body)

    def test_update_writes_to_a_new_binary_stream(self):
        source_data = b"---\r\npublished: false\r\n---\r\nbody\r\n"
        source = io.BytesIO(source_data)
        destination = io.BytesIO()

        result = frontmatter.update_frontmatter(
            source, {"published": True}, destination
        )

        self.assertIsNone(result)
        output = destination.getvalue()
        self.assertEqual(output[output.find(b"body") :], b"body\r\n")
        header = output[: output.find(b"body")].replace(b"\r\n", b"")
        self.assertNotIn(b"\r\n---\n", header)

    def test_insert_requires_explicit_choice(self):
        source = "Plain body with 中文.\n".encode("utf-8")

        with self.assertRaises(frontmatter.MissingFrontmatterError):
            frontmatter.update_frontmatter(source, {"published": True})

        result = frontmatter.update_frontmatter(
            source, {"published": True}, insert=True
        )

        self.assertEqual(
            result,
            b"---\npublished: true\n---\n\nPlain body with "
            + "中文.\n".encode("utf-8"),
        )
        self.assertTrue(result.endswith(source))

    def test_empty_frontmatter_can_be_updated(self):
        body = b"\r\nBody\r\n\r\n"
        source = b"---\r\n\r\n---\r\n" + body

        result = frontmatter.update_frontmatter(source, {"published": True})

        self.assertTrue(result.startswith(b"---\npublished: true\n---\n"))
        self.assertEqual(result[len(result) - len(body) :], body)

    def test_closing_boundary_is_allowed_at_eof(self):
        source = b"---\npublished: false\n---"

        result = frontmatter.update_frontmatter(source, {"published": True})

        self.assertEqual(result, b"---\npublished: true\n---\n")

    def test_insert_into_empty_or_blank_document(self):
        self.assertEqual(
            frontmatter.update_frontmatter(
                b"", {"published": True}, insert=True
            ),
            b"---\npublished: true\n---\n",
        )
        self.assertEqual(
            frontmatter.update_frontmatter(
                b"\nbody\n", {"published": True}, insert=True
            ),
            b"---\npublished: true\n---\n\nbody\n",
        )

    def test_unclosed_frontmatter_is_rejected_without_writing(self):
        source = b"---\ntitle: old\nbody without closing boundary\n"
        destination = io.BytesIO()

        with self.assertRaises(frontmatter.UnclosedFrontmatterError) as ctx:
            frontmatter.update_frontmatter(
                source, {"published": True}, destination
            )

        self.assertIn("not closed", str(ctx.exception))
        self.assertEqual(destination.getvalue(), b"")

    def test_unsupported_or_invalid_metadata_encoding_is_rejected(self):
        with self.assertRaises(frontmatter.UnsupportedEncodingError):
            frontmatter.update_frontmatter(
                b"---\npublished: false\n---\nbody\n",
                {"published": True},
                encoding="latin-1",
            )

        destination = io.BytesIO()
        with self.assertRaises(frontmatter.FrontmatterError):
            frontmatter.update_frontmatter(
                b"---\n\xff\n---\nbody\n",
                {"published": True},
                destination,
            )
        self.assertEqual(destination.getvalue(), b"")

    def test_unsupported_bom_is_rejected(self):
        for source in (
            b"\xff\xfe-\x00-\x00-\x00",
            b"\xfe\xff\x00-\x00-\x00-",
            b"\xff\xfe\x00\x00",
            b"\x00\x00\xfe\xff",
        ):
            with self.subTest(source=source):
                with self.assertRaises(frontmatter.UnsupportedEncodingError):
                    frontmatter.update_frontmatter(source, {"published": True})

    def test_invalid_utf8_bytes_after_boundary_are_preserved(self):
        body = b"\xff\xfe body\r\n"
        source = b"---\npublished: false\n---\n" + body

        result = frontmatter.update_frontmatter(source, {"published": True})

        self.assertEqual(result[len(result) - len(body) :], body)

    def test_json_and_toml_bodies_are_preserved(self):
        json_body = b"\r\nJSON body with \xe4\xb8\xad\xe6\x96\x87\r\n"
        json_source = (
            b'{\r\n"published": false,\r\n"title": "old"\r\n}\r\n' + json_body
        )
        json_result = frontmatter.update_frontmatter(
            json_source, {"published": True}
        )
        self.assertEqual(
            json_result[len(json_result) - len(json_body) :], json_body
        )
        self.assertIs(frontmatter.loads(json_result.decode("utf-8"))["published"], True)

        toml_body = b"\r\nTOML body\r\n"
        toml_source = (
            b'+++\r\npublished = false\r\ntitle = "old"\r\n+++\r\n' + toml_body
        )
        toml_result = frontmatter.update_frontmatter(
            toml_source, {"published": True}
        )
        self.assertEqual(
            toml_result[len(toml_result) - len(toml_body) :], toml_body
        )
        self.assertIs(frontmatter.loads(toml_result.decode("utf-8"))["published"], True)


if __name__ == "__main__":
    unittest.main()
