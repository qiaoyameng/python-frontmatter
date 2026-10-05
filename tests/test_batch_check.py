#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest

import frontmatter
from frontmatter import Post
from frontmatter.batch import check_posts


def make_post(**metadata):
    return Post("content", **metadata)


def codes(report, filename=None):
    issues = report.issues if filename is None else report.for_file(filename)
    return sorted(issue.code for issue in issues)


class BatchCheckTest(unittest.TestCase):
    "Cross-file checks over (filename, Post) pairs"

    def test_alias_chain_resolves_related_and_requires(self):
        "Relations may point at any alias in a chain of renames"
        posts = [
            ("a.md", make_post(id="guide", aliases=["old-guide", "legacy-guide"])),
            ("b.md", make_post(id="faq", related=["legacy-guide"])),
            ("c.md", make_post(id="intro", requires=["Old-Guide"])),
        ]
        report = check_posts(posts)
        self.assertTrue(report.ok, str(report))
        self.assertEqual(report.index.resolve("LEGACY-GUIDE").filename, "a.md")
        self.assertEqual(report.index.resolve("old-guide").kind, "alias")
        self.assertEqual(report.index.resolve("guide").kind, "id")

    def test_case_conflicts_on_ids_and_aliases(self):
        "Normalization catches ids and aliases differing only by case"
        posts = [
            ("a.md", make_post(id="Hello")),
            ("b.md", make_post(id="hello")),
            ("c.md", make_post(id="c", aliases=["HELLO"])),
            ("d.md", make_post(id="d", aliases=["Shared"])),
            ("e.md", make_post(id="e", aliases=["shared"])),
        ]
        report = check_posts(posts)
        self.assertFalse(report.ok)
        self.assertEqual(codes(report, "b.md"), ["duplicate-id"])
        self.assertEqual(codes(report, "c.md"), ["id-occupied"])
        self.assertEqual(codes(report, "e.md"), ["alias-conflict"])
        self.assertEqual(codes(report, "a.md"), [])
        self.assertEqual(codes(report, "d.md"), [])

    def test_related_missing_target_and_self_reference(self):
        posts = [
            ("a.md", make_post(id="a", aliases=["me"], related=["ghost", "me"])),
            ("b.md", make_post(id="b", related=["a"])),
        ]
        report = check_posts(posts)
        self.assertEqual(
            codes(report, "a.md"), ["missing-target", "self-reference"]
        )
        self.assertEqual(codes(report, "b.md"), [])
        by_code = {issue.code: issue for issue in report.for_file("a.md")}
        self.assertEqual(by_code["missing-target"].field, "related")
        self.assertEqual(by_code["missing-target"].value, "ghost")
        self.assertEqual(by_code["self-reference"].value, "me")

    def test_mixed_relations_only_requires_cycles(self):
        "Mutual related links are fine; mutual requires are a cycle"
        posts = [
            ("a.md", make_post(id="a", related=["b"], requires=["b"])),
            ("b.md", make_post(id="b", related=["a"], requires=["a"])),
            ("c.md", make_post(id="c", related=["a", "b"])),
        ]
        report = check_posts(posts)
        self.assertEqual(codes(report, "a.md"), ["dependency-cycle"])
        self.assertEqual(codes(report, "b.md"), ["dependency-cycle"])
        self.assertEqual(codes(report, "c.md"), [])
        cycle = report.for_file("a.md")[0]
        self.assertEqual(cycle.field, "requires")
        self.assertIn("a -> b", cycle.message)
        self.assertIn("b -> a", cycle.message)

    def test_requires_chain_cycle_through_alias(self):
        "A cycle that goes through an alias target is still found"
        posts = [
            ("a.md", make_post(id="a", requires=["old-b"])),
            ("b.md", make_post(id="b", aliases=["old-b"], requires=["c"])),
            ("c.md", make_post(id="c", requires=["a"])),
        ]
        report = check_posts(posts)
        self.assertEqual(
            codes(report),
            ["dependency-cycle", "dependency-cycle", "dependency-cycle"],
        )

    def test_missing_id_is_located(self):
        posts = [("a.md", make_post(related=["b"])), ("b.md", make_post(id="b"))]
        report = check_posts(posts)
        self.assertEqual(codes(report, "a.md"), ["missing-id"])
        issue = report.for_file("a.md")[0]
        self.assertEqual(issue.field, "id")

    def test_report_is_read_only(self):
        "Checking never modifies the posts it inspects"
        posts = [
            ("a.md", make_post(id="a", aliases=["x"], related=["nope"])),
            ("b.md", make_post(id="b", requires=["a"])),
        ]
        before = [dict(post.metadata) for _, post in posts]
        check_posts(posts)
        self.assertEqual([dict(post.metadata) for _, post in posts], before)

    def test_report_text_locates_file_and_field(self):
        posts = [
            ("posts/a.md", make_post(id="Hello")),
            ("posts/b.md", make_post(id="b", aliases=["hello"], related=["zzz"])),
        ]
        report = check_posts(posts)
        text = str(report)
        self.assertIn("posts/b.md", text)
        self.assertIn("field 'aliases'", text)
        self.assertIn("field 'related'", text)
        self.assertIn("id-occupied", text)
        self.assertIn("missing-target", text)

    def test_entry_point_takes_loaded_posts(self):
        "The batch entry accepts (filename, Post) pairs, loaded by the caller"
        post = frontmatter.loads("---\nid: a\nrelated: [b]\n---\nbody\n")
        report = frontmatter.check_posts([("a.md", post)])
        self.assertEqual(codes(report, "a.md"), ["missing-target"])


if __name__ == "__main__":
    unittest.main()
