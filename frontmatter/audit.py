# -*- coding: utf-8 -*-
"""
Batch auditing of frontmatter metadata across a set of posts.

These checks are meant to run before publishing a collection of articles,
where per-file parsing is not enough: identities (``id`` and ``aliases``)
must be unique across the whole set, ``related`` references must point at
real, other posts, and ``requires`` dependencies must not form cycles.

Everything here is read-only. Auditing never modifies posts or their
metadata, and nothing is scanned from disk: callers pass in the filenames
and already-loaded :py:class:`Post <frontmatter.Post>` objects themselves.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Iterator, Mapping

if TYPE_CHECKING:
    from . import Post

__all__ = [
    "check_posts",
    "normalize_name",
    "IdentityEntry",
    "IdentityIndex",
    "Issue",
    "CheckReport",
    "MISSING_ID",
    "INVALID_ID",
    "DUPLICATE_ID",
    "ALIAS_CONFLICT",
    "ALIAS_CLASHES_WITH_ID",
    "UNRESOLVED_RELATED",
    "SELF_RELATED",
    "UNRESOLVED_REQUIRES",
    "REQUIRES_CYCLE",
]

#: issue codes used in :py:class:`Issue` objects
MISSING_ID = "missing-id"
INVALID_ID = "invalid-id"
DUPLICATE_ID = "duplicate-id"
ALIAS_CONFLICT = "alias-conflict"
ALIAS_CLASHES_WITH_ID = "alias-clashes-with-id"
UNRESOLVED_RELATED = "unresolved-related"
SELF_RELATED = "self-related"
UNRESOLVED_REQUIRES = "unresolved-requires"
REQUIRES_CYCLE = "requires-cycle"


def normalize_name(value: object) -> str:
    """
    Normalize an id, alias or reference for comparison.

    Names are compared case-insensitively and ignore surrounding
    whitespace, so spelling differences like ``"Hello World"`` vs
    ``"hello world"`` do not hide conflicts.
    """
    return str(value).strip().casefold()


@dataclass(frozen=True)
class IdentityEntry:
    """
    One name claimed by one post, stored in an :py:class:`IdentityIndex`.
    """

    raw: str  #: the name exactly as written in metadata
    normalized: str  #: normalized form, used as the index key
    kind: str  #: "id" for a primary id, "alias" for an alias
    filename: str  #: file claiming this name
    field: str  #: metadata field the name came from


class IdentityIndex:
    """
    Explicit, normalized index of every identity claimed by a set of posts.

    Primary ids and aliases are indexed separately, so a name claimed as
    an alias is never silently treated as a primary id (or vice versa).
    References resolve against primary ids first, then aliases.
    """

    def __init__(self, entries: Iterable[IdentityEntry] = ()) -> None:
        self.ids: dict[str, list[IdentityEntry]] = {}
        self.aliases: dict[str, list[IdentityEntry]] = {}
        for entry in entries:
            self.add(entry)

    def add(self, entry: IdentityEntry) -> None:
        "Register one claimed name in the index"
        table = self.ids if entry.kind == "id" else self.aliases
        table.setdefault(entry.normalized, []).append(entry)

    def resolve(self, name: object) -> IdentityEntry | None:
        """
        Resolve a reference to the identity it points at, or None.

        Primary ids win over aliases when a name is claimed as both;
        that clash is reported separately by :py:func:`check_posts`.
        """
        key = normalize_name(name)
        if not key:
            return None
        claims = self.ids.get(key) or self.aliases.get(key)
        return claims[0] if claims else None


@dataclass(frozen=True)
class Issue:
    """
    A single problem found while auditing a set of posts.

    ``filename`` and ``field`` always point at the exact file and
    metadata field that need editing.
    """

    code: str  #: one of the module-level issue code constants
    filename: str  #: file containing the problem
    field: str  #: metadata field containing the problem
    message: str  #: human-readable explanation
    value: object = None  #: the offending value, when there is one

    def __str__(self) -> str:
        return f"{self.filename}: [{self.code}] {self.field}: {self.message}"


class CheckReport:
    """
    Result of auditing a set of posts with :py:func:`check_posts`.

    A report is truthy when no issues were found. Iterating over it
    yields :py:class:`Issue` objects, and ``str(report)`` renders one
    ``filename: [code] field: message`` line per issue.
    """

    def __init__(self, issues: Iterable[Issue] = ()) -> None:
        self.issues: list[Issue] = list(issues)

    @property
    def ok(self) -> bool:
        "True when no issues were found"
        return not self.issues

    def __bool__(self) -> bool:
        return self.ok

    def __len__(self) -> int:
        return len(self.issues)

    def __iter__(self) -> Iterator[Issue]:
        return iter(self.issues)

    def by_code(self, code: str) -> list[Issue]:
        "All issues with the given code"
        return [issue for issue in self.issues if issue.code == code]

    def for_file(self, filename: str) -> list[Issue]:
        "All issues located in the given file"
        return [issue for issue in self.issues if issue.filename == filename]

    def __str__(self) -> str:
        if self.ok:
            return "OK: no issues found"
        return "\n".join(str(issue) for issue in self.issues)


def check_posts(
    posts: "Iterable[tuple[str | os.PathLike[str], Post]] | Mapping[str, Post]",
    *,
    id_field: str = "id",
    aliases_field: str = "aliases",
    related_field: str = "related",
    requires_field: str = "requires",
) -> CheckReport:
    """
    Audit a set of posts before publishing and return a
    :py:class:`CheckReport`.

    ``posts`` is an iterable of ``(filename, post)`` pairs (or a mapping
    of filename to post) with posts already loaded, e.g. via
    :py:func:`frontmatter.load`. Nothing is read from disk and nothing
    is modified; the check is purely read-only.

    The audit builds a normalized :py:class:`IdentityIndex` separating
    primary ids from aliases, then reports:

    - missing or non-scalar primary ids (``missing-id``, ``invalid-id``)
    - duplicate primary ids, including case-only differences
      (``duplicate-id``)
    - aliases claimed by more than one post (``alias-conflict``)
    - aliases colliding with another post's primary id
      (``alias-clashes-with-id``)
    - ``related`` targets that resolve to nothing (``unresolved-related``)
      or back to the same post (``self-related``)
    - ``requires`` targets that resolve to nothing
      (``unresolved-requires``)
    - cycles in the ``requires`` dependency graph (``requires-cycle``)

    ``related`` is a plain cross-reference, not a dependency: mutual
    ``related`` links between posts are fine and are never reported as
    cycles. Only ``requires`` edges take part in cycle detection.

    Field names can be customized with the keyword arguments.

    .. doctest::

        >>> posts = [
        ...     ("intro.md", frontmatter.Post("Intro", id="intro", aliases=["start-here"])),
        ...     ("guide.md", frontmatter.Post("Guide", id="guide", related=["start-here"], requires=["intro"])),
        ... ]
        >>> report = frontmatter.check_posts(posts)
        >>> report.ok
        True

    Every issue points back to the file and field that need fixing:

    .. doctest::

        >>> posts = [
        ...     ("draft.md", frontmatter.Post("Draft", id="draft", related=["ghost"])),
        ... ]
        >>> report = frontmatter.check_posts(posts)
        >>> report.ok
        False
        >>> print(report)
        draft.md: [unresolved-related] related: related target 'ghost' does not match any known id or alias

    """
    items = _as_items(posts)
    issues: list[Issue] = []

    # 1. build the explicit, normalized identity index
    index = IdentityIndex()
    id_files: dict[str, str] = {}  # normalized primary id -> filename
    id_raw: dict[str, str] = {}  # normalized primary id -> raw spelling
    file_id: dict[str, str] = {}  # filename -> its normalized primary id

    for filename, post in items:
        raw_id = post.get(id_field)
        if raw_id is None or not normalize_name(raw_id):
            issues.append(
                Issue(
                    MISSING_ID,
                    filename,
                    id_field,
                    f"post has no usable {id_field!r} value",
                )
            )
        elif isinstance(raw_id, (list, tuple, dict, set)):
            issues.append(
                Issue(
                    INVALID_ID,
                    filename,
                    id_field,
                    f"{id_field!r} must be a string or number, "
                    f"got {type(raw_id).__name__}",
                    value=raw_id,
                )
            )
        else:
            key = normalize_name(raw_id)
            index.add(IdentityEntry(str(raw_id), key, "id", filename, id_field))
            id_files.setdefault(key, filename)
            id_raw.setdefault(key, str(raw_id))
            file_id[filename] = key

        for alias in _as_list(post.get(aliases_field)):
            key = normalize_name(alias)
            if not key:
                continue
            index.add(
                IdentityEntry(str(alias), key, "alias", filename, aliases_field)
            )

    # 2. identity collisions: duplicate ids, alias conflicts, alias/id clashes
    for key, claims in sorted(index.ids.items()):
        if len(claims) < 2:
            continue
        for claim in claims:
            others = "; ".join(
                f"{other.raw!r} in {other.filename}"
                for other in claims
                if other is not claim
            )
            issues.append(
                Issue(
                    DUPLICATE_ID,
                    claim.filename,
                    claim.field,
                    f"duplicate id {claim.raw!r} (normalized {key!r}); "
                    f"also claimed by {others}",
                    value=claim.raw,
                )
            )

    for key, claims in sorted(index.aliases.items()):
        if len(claims) < 2:
            continue
        for claim in claims:
            others = "; ".join(
                f"{other.raw!r} in {other.filename}"
                for other in claims
                if other is not claim
            )
            issues.append(
                Issue(
                    ALIAS_CONFLICT,
                    claim.filename,
                    claim.field,
                    f"alias {claim.raw!r} (normalized {key!r}) claimed by "
                    f"multiple posts; also claimed by {others}",
                    value=claim.raw,
                )
            )

    for key in sorted(set(index.ids) & set(index.aliases)):
        owners = sorted({claim.filename for claim in index.ids[key]})
        for claim in index.aliases[key]:
            if claim.filename in owners:
                continue  # a post aliasing its own id is redundant, not a clash
            issues.append(
                Issue(
                    ALIAS_CLASHES_WITH_ID,
                    claim.filename,
                    claim.field,
                    f"alias {claim.raw!r} (normalized {key!r}) clashes with "
                    f"primary id of {', '.join(owners)}",
                    value=claim.raw,
                )
            )

    # 3. references: related must resolve and not self-reference,
    #    requires must resolve and feeds the dependency graph
    requires_graph: dict[str, set[str]] = {key: set() for key in id_files}

    for filename, post in items:
        self_key = file_id.get(filename)

        for target in _as_list(post.get(related_field)):
            if not normalize_name(target):
                continue
            entry = index.resolve(target)
            if entry is None:
                issues.append(
                    Issue(
                        UNRESOLVED_RELATED,
                        filename,
                        related_field,
                        f"related target {target!r} does not match any "
                        f"known id or alias",
                        value=target,
                    )
                )
            elif _is_self(entry, self_key, filename):
                issues.append(
                    Issue(
                        SELF_RELATED,
                        filename,
                        related_field,
                        f"related target {target!r} points to this post "
                        f"itself (via {entry.kind} {entry.raw!r})",
                        value=target,
                    )
                )

        for target in _as_list(post.get(requires_field)):
            if not normalize_name(target):
                continue
            entry = index.resolve(target)
            if entry is None:
                issues.append(
                    Issue(
                        UNRESOLVED_REQUIRES,
                        filename,
                        requires_field,
                        f"requires target {target!r} does not match any "
                        f"known id or alias",
                        value=target,
                    )
                )
            elif self_key is not None:
                if entry.kind == "id":
                    owner_key: "str | None" = entry.normalized
                else:
                    owner_key = file_id.get(entry.filename)
                if owner_key is not None:
                    requires_graph[self_key].add(owner_key)

    # 4. publishing dependency cycles (requires edges only)
    for component in _strongly_connected_components(requires_graph):
        if len(component) == 1 and component[0] not in requires_graph[component[0]]:
            continue
        cycle = _cycle_path(set(component), requires_graph)
        chain = " -> ".join(repr(id_raw.get(key, key)) for key in cycle)
        files: list[str] = []
        for key in cycle[:-1]:
            filename = id_files.get(key)
            if filename is not None and filename not in files:
                files.append(filename)
        issues.append(
            Issue(
                REQUIRES_CYCLE,
                id_files.get(cycle[0], cycle[0]),
                requires_field,
                f"publishing dependency cycle: {chain} "
                f"(files: {', '.join(files)})",
                value=tuple(cycle),
            )
        )

    return CheckReport(issues)


def _as_items(
    posts: "Iterable[tuple[str | os.PathLike[str], Post]] | Mapping[str, Post]",
) -> "list[tuple[str, Post]]":
    "Normalize the posts argument into a list of (filename, post) pairs"
    if isinstance(posts, Mapping):
        pairs: "Iterable[tuple[object, Post]]" = posts.items()
    else:
        pairs = posts
    return [(os.fspath(name), post) for name, post in pairs]


def _as_list(value: object) -> list:
    "Treat scalars as a single item and None as empty"
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _is_self(entry: IdentityEntry, self_key: "str | None", filename: str) -> bool:
    "Check whether a resolved reference points back at its own post"
    if entry.kind == "id":
        return self_key is not None and entry.normalized == self_key
    return entry.filename == filename


def _strongly_connected_components(
    graph: dict[str, set[str]],
) -> list[list[str]]:
    "Tarjan's algorithm, iterative, deterministic node order"
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []
    counter = 0

    for start in sorted(graph):
        if start in index_of:
            continue
        todo = [(start, iter(sorted(graph[start])))]
        while todo:
            node, neighbors = todo[-1]
            if node not in index_of:
                index_of[node] = lowlink[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            descended = False
            for nxt in neighbors:
                if nxt not in graph:
                    continue
                if nxt not in index_of:
                    todo.append((nxt, iter(sorted(graph[nxt]))))
                    descended = True
                    break
                elif nxt in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[nxt])
            if descended:
                continue
            todo.pop()
            if todo:
                parent = todo[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index_of[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)

    return components


def _cycle_path(nodes: set, graph: dict[str, set[str]]) -> list[str]:
    "Extract one concrete cycle (a -> b -> ... -> a) inside a component"
    start = min(nodes)
    path = [start]
    seen = {start}
    current = start
    while True:
        nxt = min(n for n in graph[current] if n in nodes)
        if nxt in seen:
            return path[path.index(nxt):] + [nxt]
        path.append(nxt)
        seen.add(nxt)
        current = nxt
