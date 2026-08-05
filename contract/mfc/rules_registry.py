"""`mfc registry validate` — the R-01..R-09 rules over a `registry/1.0` document.

The registry is the only artifact here that is **hand-authored**. Everything
else is emitted by a program and then recomputed by another one; this one is
typed by a person, at 20–40 minutes an entry, and every rule below exists
because a specific kind of hand-authoring mistake is silent.

## What the schema already guarantees, so these do not restate it

`registry-1.0.schema.json` pins the shapes: the citation-key grammar, that an
arXiv source carries a version, that a `textbook` source carries none, that
only an `obligation` may lack a `mint_resolution`, and that `verbatim` mode
inlines both the text and its hash. The rules here are the ones JSON Schema
**cannot** express — recomputation (`R-02`), cross-entry graph properties
(`R-04`, `R-07`), and agreement between a value and its own container
(`R-05`).

## Two of these nine are already structural, and are kept as backstops

The design note tables these as *"rules over the registry that JSON Schema
cannot express."* That is true of seven of them. It is **not** true of `R-01`
and `R-06`, and running the fixtures through the CLI is what showed it:

* `R-01` — `source.version` carries `pattern: ^v[0-9]+$` at the property level,
  so `"v?"` and `""` are rejected for every scheme, not just `arxiv`.
* `R-06` — `entries` carries `propertyNames: {$ref: citationKey}`, so a pasted
  `arxiv:math/0212237:a82c3230040fd724` is rejected as a property name.

Both fixtures fail `mfc validate` before `mfc registry validate` ever reaches
the rule. That is the *stronger* arrangement and matches `E-08`: a structural
guarantee beats a rule a caller can skip. The rules stay as backstops for a
caller that reaches `check()` directly, and the schema constraints are pinned
by tests so that relaxing either becomes a visible decision rather than a
silent transfer of responsibility to a rule nothing runs.

## The two that matter most

`R-02` recomputes `quote_sha256` from the inline text. Without it the digest is
a number somebody typed, and the whole cross-repo freshness mechanism —
`registry_sha256` compared between the bundle and the resolution — rests on
digests that were never checked against what they claim to summarize.

`R-06` refuses a key shaped like a corpus id. A `chunk_id` is exactly what a
corpus hands you, so someone will paste one in; and chunk ids **rotate** on any
re-parse, with no alias table and no delete arm in the merge, so a citation to
one silently stops resolving while the old id stays addressable. The `@[cites]`
attribute rejects this shape at compile time too. Both, deliberately: the
attribute catches it for a Lean author, this catches it for a hand-edited YAML
file that no Lean ever sees.
"""

from __future__ import annotations

import re
from typing import Iterable

from .digest import quote_sha256
from .registry import RegistryShapeError, entries as registry_entries
from .rules import Finding, RuleResult, Status

#: A corpus chunk id (`arxiv:math/0212237:a82c3230040fd724`) or an equation id.
#: Refused as a citation key: these rotate on re-parse and nothing forwards the
#: old one.
CORPUS_ID_RE = re.compile(r"^arxiv:|^[a-z]+:[^:]+:[0-9a-f]{16}$")

#: Placeholder text a half-finished entry carries. `R-03` exists because an
#: entry with a placeholder quote still hashes, still validates, and still
#: serves.
PLACEHOLDER = "<<<PLACEHOLDER"

#: `source.version` values that look filled and are not.
EMPTY_VERSIONS = frozenset({"", "v?", "v", "vX", "vN"})


def check(
    registry: dict,
    *,
    frontier_kind_labels: list[str] | None = None,
) -> list[RuleResult]:
    """Run R-01..R-09. Raises `RegistryShapeError` if the document is unreadable."""
    entries = registry_entries(registry)
    registry_id = registry.get("registry_id")
    results: list[RuleResult] = []

    def add(rule: str, title: str, findings: list[Finding], reason: str = "") -> None:
        results.append(RuleResult(
            rule, title, Status.FAIL if findings else Status.PASS,
            tuple(findings), reason))

    def skip(rule: str, title: str, reason: str) -> None:
        results.append(RuleResult(rule, title, Status.NOT_RUN, (), reason))

    # R-01 -- a version that looks filled and is not. A bare arXiv id resolves
    # to LATEST and drifts silently; `v?` is what a half-filled entry carries.
    add("R-01", "source.version is not an empty placeholder",
        [Finding("R-01", key, f"source.version is {e['source'].get('version')!r}")
         for key, e in sorted(entries.items())
         if isinstance(e.get("source"), dict)
         and e["source"].get("version") in EMPTY_VERSIONS])

    # R-02 -- the digest is RECOMPUTED from the inline text. Everything
    # downstream compares registry digests to each other; this is the only
    # check that compares one to the thing it summarizes.
    r02: list[Finding] = []
    for key, e in sorted(entries.items()):
        if e.get("quote_mode") != "verbatim":
            continue
        quote, claimed = e.get("quote"), e.get("quote_sha256")
        if not isinstance(quote, str) or not isinstance(claimed, str):
            continue  # the schema's verbatim allOf covers this
        actual = quote_sha256(quote)
        if actual != claimed:
            r02.append(Finding("R-02", key,
                f"quote_sha256 is {claimed[:12]}..., but the inline quote hashes "
                f"to {actual[:12]}..."))
    add("R-02", "quote_sha256 recomputes from the inline quote", r02)

    # R-03 -- a placeholder quote still hashes, still validates, still serves.
    add("R-03", "no quote contains a placeholder marker",
        [Finding("R-03", key, f"quote contains {PLACEHOLDER!r}")
         for key, e in sorted(entries.items())
         if isinstance(e.get("quote"), str) and PLACEHOLDER in e["quote"]])

    # R-04 -- every internal reference resolves, and `depends_on` is acyclic.
    r04: list[Finding] = []
    for key, e in sorted(entries.items()):
        for field in ("supersedes", "superseded_by"):
            target = e.get(field)
            if isinstance(target, str) and target not in entries:
                r04.append(Finding("R-04", key, f"{field} names unknown key {target!r}"))
        for dep in e.get("depends_on") or []:
            if dep not in entries:
                r04.append(Finding("R-04", key, f"depends_on names unknown key {dep!r}"))
        for item in e.get("frontier") or []:
            discharged = item.get("discharged_by") if isinstance(item, dict) else None
            if isinstance(discharged, dict) and discharged.get("key") not in entries:
                r04.append(Finding("R-04", key,
                    f"frontier {item.get('id')!r} is discharged_by unknown key "
                    f"{discharged.get('key')!r}"))
    for cycle in _cycles({k: [d for d in (e.get("depends_on") or []) if d in entries]
                          for k, e in entries.items()}):
        r04.append(Finding("R-04", cycle[0],
            f"depends_on cycle: {' -> '.join(cycle)} -> {cycle[0]}"))
    add("R-04", "every internal reference resolves and depends_on is acyclic", r04)

    # R-05 -- the registry id inside each key equals the file's own.
    if not isinstance(registry_id, str):
        skip("R-05", "each key's registry id matches the file's",
             "the document carries no registry_id")
    else:
        add("R-05", "each key's registry id matches the file's",
            [Finding("R-05", key,
                     f"key's registry id is {key.split(':')[1]!r}, file declares "
                     f"{registry_id!r}")
             for key in sorted(entries)
             if len(key.split(":")) == 3 and key.split(":")[1] != registry_id])

    # R-06 -- a key shaped like a corpus id. See the module docstring.
    add("R-06", "no key is shaped like a corpus chunk id",
        [Finding("R-06", key,
                 "shaped like a corpus chunk/equation id. Those ROTATE on any "
                 "re-parse and nothing forwards the old one, so a citation to "
                 "one silently stops resolving")
         for key in sorted(entries) if CORPUS_ID_RE.match(key)])

    # R-07 -- supersession is symmetric. A one-sided link means one of the two
    # entries still looks current.
    add("R-07", "superseded_by is symmetric with the target's supersedes",
        [Finding("R-07", key,
                 f"superseded_by {target!r}, but that entry's supersedes is "
                 f"{entries[target].get('supersedes')!r}")
         for key, e in sorted(entries.items())
         for target in [e.get("superseded_by")]
         if isinstance(target, str) and target in entries
         and entries[target].get("supersedes") != key])

    # R-08 -- frontier labels come from the topic's declared allowlist. Free
    # text here means two entries name the same gap differently and no rollup
    # can group them.
    if frontier_kind_labels is None:
        skip("R-08", "every frontier kind_label is in the topic's allowlist",
             "no --frontier-kind-labels supplied; the allowlist is a per-topic "
             "configuration value, not a property of the contract")
    else:
        allowed = set(frontier_kind_labels)
        add("R-08", "every frontier kind_label is in the topic's allowlist",
            [Finding("R-08", key, f"frontier {item.get('id')!r} has kind_label "
                                  f"{item.get('kind_label')!r}, not in {sorted(allowed)}")
             for key, e in sorted(entries.items())
             for item in e.get("frontier") or []
             if isinstance(item, dict) and item.get("kind_label") is not None
             and item["kind_label"] not in allowed])

    # R-09 -- an obligation has no quote to ground it, so the note is the only
    # thing saying what is owed. Without one it is indistinguishable from a
    # `theorem` entry nobody got around to, which is the exact confusion #37
    # exists to remove.
    add("R-09", "an unquoted obligation carries a note",
        [Finding("R-09", key,
                 "kind=obligation with no quote_sha256 and no note: nothing says "
                 "what is owed, so it reads as an entry nobody got around to")
         for key, e in sorted(entries.items())
         if e.get("kind") == "obligation" and e.get("quote_sha256") is None
         and not (e.get("note") or "").strip()])

    return results


def _cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Every elementary cycle, each reported once from its smallest member.

    Iterative rather than recursive: a registry is hand-authored and small, but
    a `depends_on` chain is exactly the thing an author builds deep, and a
    RecursionError would be reported as a crash rather than as a finding.
    """
    found: list[list[str]] = []
    seen: set[frozenset[str]] = set()
    colour: dict[str, int] = {}

    for root in sorted(graph):
        if colour.get(root):
            continue
        stack = [(root, iter(graph.get(root, ())))]
        path = [root]
        colour[root] = 1
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                colour[node] = 2
                stack.pop()
                path.pop()
                continue
            if colour.get(child) == 1:              # back edge -> cycle
                cycle = path[path.index(child):]
                marker = frozenset(cycle)
                if marker not in seen:
                    seen.add(marker)
                    start = cycle.index(min(cycle))
                    found.append(cycle[start:] + cycle[:start])
            elif not colour.get(child):
                colour[child] = 1
                path.append(child)
                stack.append((child, iter(graph.get(child, ()))))
    return found


def mint_registry_id(randbits) -> str:
    """A 12-hex registry id, minted once per topic repository.

    Takes its randomness as an argument so a caller can pin it in a test — the
    id is a durable, git-tracked identifier, and an accidentally-reproducible
    one would collide across every repository the same code created.

    Not derived from the notebook slug, deliberately: slugs live in a
    machine-local, unauthenticated sqlite database with no global registry, so
    two adopters both choosing `number-theory` collide silently. A 12-hex value
    minted once and committed is unique in practice, and a collision is
    detectable and fixable.
    """
    return f"{randbits(48):012x}"
