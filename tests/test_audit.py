"""
Tests for frontmatter.audit: batch, read-only checks across a set of posts.
"""
import copy

import frontmatter
from frontmatter import check_posts
from frontmatter.audit import (
    ALIAS_CLASHES_WITH_ID,
    ALIAS_CONFLICT,
    DUPLICATE_ID,
    INVALID_ID,
    MISSING_ID,
    REQUIRES_CYCLE,
    SELF_RELATED,
    UNRESOLVED_RELATED,
    UNRESOLVED_REQUIRES,
    IdentityEntry,
    IdentityIndex,
    normalize_name,
)


def make_post(**metadata):
    return frontmatter.Post("content", **metadata)


def codes(report):
    return sorted(issue.code for issue in report)


def test_normalize_name():
    assert normalize_name(" Hello World ") == "hello world"
    assert normalize_name("Hello-World") == "hello-world"
    assert normalize_name(42) == "42"


def test_identity_index_separates_ids_and_aliases():
    index = IdentityIndex()
    index.add(IdentityEntry("a", "a", "id", "a.md", "id"))
    index.add(IdentityEntry("B", "b", "alias", "b.md", "aliases"))

    assert "a" in index.ids and "a" not in index.aliases
    assert "b" in index.aliases and "b" not in index.ids
    assert index.resolve("A").kind == "id"
    assert index.resolve("b").kind == "alias"
    assert index.resolve("missing") is None


def test_identity_index_prefers_id_over_alias():
    index = IdentityIndex()
    index.add(IdentityEntry("x", "x", "alias", "a.md", "aliases"))
    index.add(IdentityEntry("x", "x", "id", "b.md", "id"))
    assert index.resolve("x").kind == "id"


def test_clean_set_passes():
    posts = [
        ("a.md", make_post(id="alpha", aliases=["first"], related=["beta"])),
        ("b.md", make_post(id="beta", related=["first"], requires=["alpha"])),
    ]
    report = check_posts(posts)
    assert report.ok
    assert bool(report)
    assert len(report) == 0
    assert "OK" in str(report)


def test_alias_chain_resolves_related_and_requires():
    "References may point at aliases; chains of them must all resolve"
    posts = [
        ("a.md", make_post(id="alpha", aliases=["old-alpha"])),
        ("b.md", make_post(id="beta", aliases=["old-beta"], related=["Old-Alpha"])),
        ("c.md", make_post(id="gamma", related=["old-beta"], requires=["OLD-BETA"])),
    ]
    report = check_posts(posts)
    assert report.ok, str(report)


def test_case_conflicting_ids_are_duplicates():
    posts = [
        ("a.md", make_post(id="Hello-World")),
        ("b.md", make_post(id="hello-world")),
    ]
    report = check_posts(posts)
    issues = report.by_code(DUPLICATE_ID)
    assert len(issues) == 2
    assert {issue.filename for issue in issues} == {"a.md", "b.md"}
    for issue in issues:
        assert issue.field == "id"
        assert "hello-world" in issue.message  # normalized form shown


def test_case_conflicting_aliases():
    posts = [
        ("a.md", make_post(id="alpha", aliases=["Old-Post"])),
        ("b.md", make_post(id="beta", aliases=["old-post"])),
    ]
    report = check_posts(posts)
    issues = report.by_code(ALIAS_CONFLICT)
    assert len(issues) == 2
    assert {issue.filename for issue in issues} == {"a.md", "b.md"}
    assert all(issue.field == "aliases" for issue in issues)


def test_alias_clashes_with_primary_id():
    posts = [
        ("a.md", make_post(id="alpha", aliases=["Getting-Started"])),
        ("b.md", make_post(id="getting-started")),
    ]
    report = check_posts(posts)
    issues = report.by_code(ALIAS_CLASHES_WITH_ID)
    assert len(issues) == 1
    assert issues[0].filename == "a.md"
    assert issues[0].field == "aliases"
    assert "b.md" in issues[0].message


def test_alias_matching_own_id_is_not_a_clash():
    posts = [("a.md", make_post(id="alpha", aliases=["Alpha"]))]
    report = check_posts(posts)
    assert report.by_code(ALIAS_CLASHES_WITH_ID) == []


def test_unresolved_related():
    posts = [("a.md", make_post(id="alpha", related=["ghost"]))]
    report = check_posts(posts)
    issues = report.by_code(UNRESOLVED_RELATED)
    assert len(issues) == 1
    assert issues[0].filename == "a.md"
    assert issues[0].field == "related"
    assert issues[0].value == "ghost"
    assert str(issues[0]).startswith("a.md: [unresolved-related] related:")


def test_self_related_by_id_and_by_own_alias():
    posts = [
        ("a.md", make_post(id="alpha", aliases=["first"], related=["alpha"])),
        ("b.md", make_post(id="beta", aliases=["second"], related=["Second"])),
    ]
    report = check_posts(posts)
    issues = report.by_code(SELF_RELATED)
    assert len(issues) == 2
    assert {issue.filename for issue in issues} == {"a.md", "b.md"}


def test_unresolved_requires():
    posts = [("a.md", make_post(id="alpha", requires=["ghost"]))]
    report = check_posts(posts)
    issues = report.by_code(UNRESOLVED_REQUIRES)
    assert len(issues) == 1
    assert issues[0].field == "requires"


def test_requires_cycle_is_reported_with_chain_and_files():
    posts = [
        ("a.md", make_post(id="alpha", requires=["beta"])),
        ("b.md", make_post(id="beta", requires=["gamma"])),
        ("c.md", make_post(id="gamma", requires=["alpha"])),
    ]
    report = check_posts(posts)
    issues = report.by_code(REQUIRES_CYCLE)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.field == "requires"
    # the message names the whole cycle and every file involved
    assert "->" in issue.message
    for name in ("alpha", "beta", "gamma"):
        assert repr(name) in issue.message
    for filename in ("a.md", "b.md", "c.md"):
        assert filename in issue.message


def test_requires_self_loop_is_a_cycle():
    posts = [("a.md", make_post(id="alpha", requires=["alpha"]))]
    report = check_posts(posts)
    issues = report.by_code(REQUIRES_CYCLE)
    assert len(issues) == 1
    assert issues[0].filename == "a.md"


def test_requires_cycle_through_alias():
    posts = [
        ("a.md", make_post(id="alpha", aliases=["start"], requires=["beta"])),
        ("b.md", make_post(id="beta", requires=["start"])),
    ]
    report = check_posts(posts)
    assert len(report.by_code(REQUIRES_CYCLE)) == 1


def test_mutual_related_is_not_a_cycle():
    "related is a cross-reference, not a dependency: mutual links are fine"
    posts = [
        ("a.md", make_post(id="alpha", related=["beta"])),
        ("b.md", make_post(id="beta", related=["alpha"])),
    ]
    report = check_posts(posts)
    assert report.by_code(REQUIRES_CYCLE) == []
    assert report.ok, str(report)


def test_mixed_related_and_requires():
    "a related cycle plus an acyclic requires chain is fine"
    posts = [
        ("a.md", make_post(id="alpha", related=["beta"], requires=["beta"])),
        ("b.md", make_post(id="beta", related=["alpha"], requires=["gamma"])),
        ("c.md", make_post(id="gamma", related=["alpha"])),
    ]
    report = check_posts(posts)
    assert report.ok, str(report)


def test_mixed_relations_report_only_requires_cycle():
    "the same topology is an error for requires but not for related"
    posts = [
        ("a.md", make_post(id="alpha", related=["gamma"], requires=["beta"])),
        ("b.md", make_post(id="beta", related=["alpha"], requires=["gamma"])),
        ("c.md", make_post(id="gamma", related=["beta"], requires=["alpha"])),
    ]
    report = check_posts(posts)
    assert codes(report) == [REQUIRES_CYCLE]


def test_missing_and_invalid_id():
    posts = [
        ("a.md", make_post()),
        ("b.md", make_post(id=["not", "scalar"])),
    ]
    report = check_posts(posts)
    assert codes(report) == [INVALID_ID, MISSING_ID]
    assert report.by_code(MISSING_ID)[0].filename == "a.md"
    assert report.by_code(INVALID_ID)[0].filename == "b.md"


def test_report_is_read_only():
    posts = [
        ("a.md", make_post(id="alpha", aliases=["one"], related=["ghost"], requires=["beta"])),
        ("b.md", make_post(id="beta", requires=["alpha"])),
    ]
    before = [(name, copy.deepcopy(post.metadata)) for name, post in posts]
    check_posts(posts)
    for (name, snapshot), (_, post) in zip(before, posts):
        assert post.metadata == snapshot, f"{name} metadata was modified"


def test_accepts_mapping_and_paths(tmp_path):
    posts = {
        tmp_path / "a.md": make_post(id="alpha"),
        tmp_path / "b.md": make_post(id="beta", related=["alpha"]),
    }
    report = check_posts(posts)
    assert report.ok, str(report)


def test_custom_field_names():
    posts = [
        ("a.md", make_post(slug="alpha", see_also=["beta"], depends_on=["beta"])),
        ("b.md", make_post(slug="beta", depends_on=["alpha"])),
    ]
    report = check_posts(
        posts,
        id_field="slug",
        related_field="see_also",
        requires_field="depends_on",
    )
    assert codes(report) == [REQUIRES_CYCLE]
    assert report.issues[0].field == "depends_on"


def test_report_helpers():
    posts = [
        ("a.md", make_post(id="alpha", related=["ghost"])),
        ("b.md", make_post(id="beta", requires=["nowhere"])),
    ]
    report = check_posts(posts)
    assert not report.ok
    assert not report
    assert len(report) == 2
    assert [issue.filename for issue in report] == ["a.md", "b.md"]
    assert len(report.for_file("a.md")) == 1
    assert report.for_file("missing.md") == []
    lines = str(report).splitlines()
    assert lines[0].startswith("a.md: [unresolved-related] related:")
    assert lines[1].startswith("b.md: [unresolved-requires] requires:")


def test_every_issue_points_to_file_and_field():
    "a mixed bag of problems: each issue locates its file and field"
    posts = [
        ("a.md", make_post(id="Alpha", aliases=["shared"], related=["nope"], requires=["beta"])),
        ("b.md", make_post(id="alpha", aliases=["Shared"])),
        ("c.md", make_post(id="beta", requires=["alpha"])),
    ]
    report = check_posts(posts)
    assert set(codes(report)) == {
        DUPLICATE_ID,
        ALIAS_CONFLICT,
        UNRESOLVED_RELATED,
        REQUIRES_CYCLE,
    }
    for issue in report:
        assert issue.filename in {"a.md", "b.md"}
        assert issue.field in {"id", "aliases", "related", "requires"}
        assert str(issue).startswith(f"{issue.filename}: [{issue.code}] {issue.field}:")
