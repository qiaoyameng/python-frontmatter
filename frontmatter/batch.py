# -*- coding: utf-8 -*-
"""
Batch consistency checks for a set of already-loaded posts.

The entry point is :py:func:`check_posts`, which takes ``(filename, Post)``
pairs supplied by the caller -- it never scans directories or reads files
itself, and it never modifies the posts it inspects.

Checks performed:

* every post has a primary ``id``, and normalized ids are unique
* ``aliases`` do not conflict with each other or occupy a primary id
* ``related`` targets exist and do not point at the post itself
* ``requires`` targets exist, and publish dependency cycles are reported
  (mutual ``related`` references are normal and are *not* cycles)

All names are normalized with :py:func:`normalize_name` before comparison,
so ``Hello``, ``hello`` and `` HELLO `` are considered the same identifier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Iterator

if TYPE_CHECKING:
    from . import Post

__all__ = [
    "check_posts",
    "normalize_name",
    "CheckReport",
    "Issue",
    "Identity",
    "IdentityIndex",
]


def normalize_name(value: object) -> str:
    "Normalize an id, alias or relation target for comparison"
    return str(value).strip().casefold()


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


@dataclass
class Identity:
    "A normalized name registered in the identity index"

    name: str
    raw: str
    kind: str  # "id" or "alias"
    filename: str


class IdentityIndex:
    """
    Explicit index of normalized identities, distinguishing primary ids
    from aliases. Built by :py:func:`check_posts` and exposed on the
    report so callers can resolve names themselves.
    """

    def __init__(self) -> None:
        self._entries: dict[str, Identity] = {}

    def register(self, identity: Identity) -> None:
        self._entries[identity.name] = identity

    def resolve(self, name: object) -> Identity | None:
        "Resolve an id, alias or relation target to its identity, or None"
        return self._entries.get(normalize_name(name))

    def __contains__(self, name: object) -> bool:
        return self.resolve(name) is not None

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[Identity]:
        return iter(self._entries.values())


@dataclass
class Issue:
    "A single problem, located by filename and metadata field"

    filename: str
    field: str
    code: str
    message: str
    value: object = None

    def __str__(self) -> str:
        return "{0}: field '{1}': {2} [{3}]".format(
            self.filename, self.field, self.message, self.code
        )


@dataclass
class CheckReport:
    "Result of :py:func:`check_posts`: issues plus the identity index"

    issues: list[Issue] = field(default_factory=list)
    index: IdentityIndex = field(default_factory=IdentityIndex)

    @property
    def ok(self) -> bool:
        "True when no issues were found"
        return not self.issues

    def for_file(self, filename: str) -> list[Issue]:
        "All issues located in one file"
        return [issue for issue in self.issues if issue.filename == filename]

    def __str__(self) -> str:
        if self.ok:
            return "no issues"
        return "\n".join(str(issue) for issue in self.issues)


def check_posts(posts: Iterable[tuple[str, "Post"]]) -> CheckReport:
    """
    Check a batch of ``(filename, Post)`` pairs for cross-file metadata
    problems. The caller decides which files to load; this function only
    reads the metadata of the posts it is given and changes nothing.

    Returns a :py:class:`CheckReport` whose issues each name the file and
    the metadata field (``id``, ``aliases``, ``related`` or ``requires``)
    that needs editing.
    """
    entries = [(str(filename), post) for filename, post in posts]
    issues: list[Issue] = []
    index = IdentityIndex()

    # pass 1: register primary ids
    for filename, post in entries:
        raw_id = post.metadata.get("id")
        if raw_id is None or not normalize_name(raw_id):
            issues.append(
                Issue(filename, "id", "missing-id", "post has no primary id")
            )
            continue
        existing = index.resolve(raw_id)
        if existing is not None:
            issues.append(
                Issue(
                    filename,
                    "id",
                    "duplicate-id",
                    "primary id {0!r} duplicates {1} {2!r} in {3}".format(
                        raw_id, existing.kind, existing.raw, existing.filename
                    ),
                    raw_id,
                )
            )
        else:
            index.register(Identity(normalize_name(raw_id), str(raw_id), "id", filename))

    # pass 2: register aliases against the ids
    for filename, post in entries:
        seen_here: set[str] = set()
        for alias in _as_list(post.metadata.get("aliases")):
            name = normalize_name(alias)
            if not name:
                continue
            if name in seen_here:
                issues.append(
                    Issue(
                        filename,
                        "aliases",
                        "alias-conflict",
                        "alias {0!r} is listed more than once".format(alias),
                        alias,
                    )
                )
                continue
            seen_here.add(name)
            existing = index.resolve(name)
            if existing is None:
                index.register(Identity(name, str(alias), "alias", filename))
            elif existing.kind == "id":
                issues.append(
                    Issue(
                        filename,
                        "aliases",
                        "id-occupied",
                        "alias {0!r} occupies primary id {1!r} of {2}".format(
                            alias, existing.raw, existing.filename
                        ),
                        alias,
                    )
                )
            else:
                issues.append(
                    Issue(
                        filename,
                        "aliases",
                        "alias-conflict",
                        "alias {0!r} conflicts with alias {1!r} in {2}".format(
                            alias, existing.raw, existing.filename
                        ),
                        alias,
                    )
                )

    # pass 3: resolve relations; only `requires` feeds the dependency graph
    requires_graph: dict[str, list[str]] = {filename: [] for filename, _ in entries}
    for filename, post in entries:
        for field_name in ("related", "requires"):
            for ref in _as_list(post.metadata.get(field_name)):
                if not normalize_name(ref):
                    continue
                target = index.resolve(ref)
                if target is None:
                    issues.append(
                        Issue(
                            filename,
                            field_name,
                            "missing-target",
                            "{0} target {1!r} matches no id or alias".format(
                                field_name, ref
                            ),
                            ref,
                        )
                    )
                    continue
                if target.filename == filename:
                    issues.append(
                        Issue(
                            filename,
                            field_name,
                            "self-reference",
                            "{0} target {1!r} resolves to this same post".format(
                                field_name, ref
                            ),
                            ref,
                        )
                    )
                    continue
                if field_name == "requires":
                    requires_graph[filename].append(target.filename)

    # pass 4: report publish dependency cycles (requires edges only)
    labels = {
        filename: (
            str(post.metadata.get("id"))
            if post.metadata.get("id") is not None
            else filename
        )
        for filename, post in entries
    }
    for component in _strongly_connected_components(requires_graph):
        is_cycle = len(component) > 1 or component[0] in requires_graph[component[0]]
        if not is_cycle:
            continue
        cycle_path = " -> ".join(
            labels[name] for name in _cycle_path(component, requires_graph)
        )
        for filename in component:
            issues.append(
                Issue(
                    filename,
                    "requires",
                    "dependency-cycle",
                    "publishing dependency cycle: {0}".format(cycle_path),
                )
            )

    return CheckReport(issues=issues, index=index)


def _strongly_connected_components(graph: dict[str, list[str]]) -> list[list[str]]:
    "Tarjan's algorithm, iterative to avoid recursion limits"
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    components: list[list[str]] = []

    for start in graph:
        if start in index_of:
            continue
        work = [(start, iter(graph[start]))]
        index_of[start] = lowlink[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, successors = work[-1]
            descended = False
            for nxt in successors:
                if nxt not in index_of:
                    index_of[nxt] = lowlink[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(graph[nxt])))
                    descended = True
                    break
                elif nxt in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[nxt])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
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


def _cycle_path(component: list[str], graph: dict[str, list[str]]) -> list[str]:
    "One concrete cycle inside a strongly connected component, for display"
    members = set(component)
    start = component[0]
    path = [start]
    visited = {start}

    def dfs(node: str) -> bool:
        for nxt in graph[node]:
            if nxt not in members:
                continue
            if nxt == start:
                return True
            if nxt in visited:
                continue
            visited.add(nxt)
            path.append(nxt)
            if dfs(nxt):
                return True
            path.pop()
            visited.discard(nxt)
        return False

    dfs(start)
    return path + [start]
