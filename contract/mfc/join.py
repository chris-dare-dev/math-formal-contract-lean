"""`mfc join` — the J-01..J-06 rules, and the claim table.

`conformance` asks whether the artifacts describe the same *build*. This asks
whether they describe the same *claims*: a review, a resolution and a citation
each name a statement, and nothing so far checks that the three ever meet.

## The claim table is the output

One row per `(registry key, declaration)` citation binding, carrying what each
of the three axes says about it:

* what the **author** claimed — `relation_claimed`, and the frontier left open
* what the **reviewer** found — `faithfulness` and `relation_confirmed`
* what the **corpus** resolved — `current`, `drifted`, `unresolvable`, …

Kept as three columns, never reduced to one. A row whose author says `exact`,
whose reviewer has not looked, and whose corpus resolution is `drifted` is a
perfectly ordinary row, and no single token can say that. Absent evidence
prints `not_run`, which is why the table has no empty cells.

## Reviews join by digest, not by name

`review.decl` records a Lean name, and joining on it is wrong in a way that
fails silently: a rename leaves `reviewed_statement_digest` matching (renaming
does not change a type) while `decl` dangles, so a name-keyed join either loses
the review or — worse, if it is lenient — floats it onto something else.

So the join key is `reviewed_statement_digest`, and `decl` is demoted to a hint
whose disagreement is itself reported (`J-02`). The two failures are kept
apart because their remedies differ:

* digest misses, `decl` **present** — the declaration is still there but its
  statement changed. The review is of something that no longer exists.
* digest misses, `decl` **absent** — rename *and* restatement at once. Nothing
  mechanical can tell which declaration was meant; a human must adjudicate.

## The work queue is now a file, not only a rule

`J-06` reports every registry entry with **zero inbound citations**, partitioned
by `kind`. `workqueue()` writes that same set as `workqueue/1.0` — the file an
agent plans against, rather than a line in a run nobody keeps.

This was blocked on the open size-ceiling decision (is there a zero-axis
`sketch` lane?) on the reasoning that partitioning by `kind` would bake an
unmade decision into a schema. It does not have to: `lanes` is keyed by
whatever `kind` actually appeared, with `propertyNames` constrained to a
*pattern* rather than an enum of the ten `registry/1.0` knows. Adding a member
there is then a one-schema change, and the two copies have no way to disagree
because there is only one.

`J-06` itself still reports `not_run` without `--registry`, which is unchanged:
no registry means no queue to compute, and that is not the same as an empty one.
"""

from __future__ import annotations

from typing import Any, Iterable, NamedTuple

from .registry import RegistryShapeError, kind_of, open_frontier
from .registry import entries as registry_entries
from .rules import Finding, RuleResult, Status
from .withdrawals import withdrawn_keys

#: The issue whose resolution unblocks `J-06`. Named in the `not_run` reason so
#: a reader learns *what* is undecided rather than only that something is.
WORKQUEUE_BLOCKER = (
    "the registry does not exist yet: its `kind` set is what the open registry "
    "size-ceiling decision settles (is there a `sketch` lane?), and guessing it "
    "would bake an unmade decision into an artifact"
)

#: Printed in a claim-table cell when an axis has no evidence for that row.
#: Deliberately the same spelling as the rule status: "nobody looked" is one
#: fact with one name everywhere in this package.
NOT_RUN = "not_run"

#: Printed when someone DID look -- but at a different environment.
#:
#: Kept apart from `not_run` because the remedies differ: `not_run` is closed by
#: doing a review, `not_applicable` by re-reviewing against this environment, and
#: an operator who cannot tell them apart cannot plan. It is never `pass` and
#: never `fail`: a verdict about env Y is not weak evidence about env X, it is no
#: evidence about env X. That rule is not local taste -- arXMCP's `CLAUDE.md`
#: §4.10 rule 3 binds it across the seam, and `#20` names this exact fixture as
#: one that must RENDER this value rather than be rejected.
#:
#: `conformance`'s C-10 is a different question and rightly still FAILs: a bundle
#: that ships a foreign-env review beside this environment is incoherent at the
#: producer. This is the consumer's question -- given a record I did not build,
#: what does it tell me about the environment I hold? -- and its answer is
#: "nothing about this one".
NOT_APPLICABLE = "not_applicable"


class Binding(NamedTuple):
    """One `(key, declaration)` citation binding — the claim table's row key."""

    key: str
    decl: str
    statement_digest: str
    relation_claimed: str
    frontier: tuple[str, ...]
    note: str | None


class Row(NamedTuple):
    key: str
    decl: str
    #: The author's claim, from `@[cites]`.
    claimed: str
    #: The reviewer's finding, or `not_run`.
    faithfulness: str
    relation_confirmed: str
    #: The corpus's answer, or `not_run`.
    resolution: str
    #: Frontier ids the author left open. Never merged into a verdict.
    frontier: str
    #: True when a human has RETRACTED this record. It is not a verdict and it
    #: does not replace one: every other column keeps whatever it said, because
    #: a reader must be able to see WHAT was withdrawn, not merely that
    #: something was. What it does is stop the row reading as live evidence.
    withdrawn: bool = False


def bindings(declarations: dict) -> list[Binding]:
    """Every citation binding in a `declarations/1.0` artifact, sorted."""
    out = [
        Binding(
            key=cite["key"],
            decl=d["name"],
            statement_digest=d["statement_digest"],
            relation_claimed=cite["relation_claimed"],
            frontier=tuple(cite.get("frontier") or ()),
            note=cite.get("note"),
        )
        for d in declarations["declarations"]
        for cite in d["cites"]
    ]
    return sorted(out, key=lambda b: (b.key, b.decl))


def _by_digest(declarations: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for d in declarations["declarations"]:
        out.setdefault(d["statement_digest"], []).append(d)
    return out


def check(
    declarations: dict,
    *,
    review: dict | None = None,
    resolution: dict | None = None,
    registry: dict | None = None,
) -> list[RuleResult]:
    """Run J-01..J-06. Order is fixed so output is comparable run to run."""
    results: list[RuleResult] = []
    binds = bindings(declarations)
    cited_keys = {b.key for b in binds}
    names = {d["name"] for d in declarations["declarations"]}
    by_digest = _by_digest(declarations)

    def add(rule: str, title: str, findings: list[Finding], reason: str = "") -> None:
        results.append(RuleResult(
            rule, title, Status.FAIL if findings else Status.PASS,
            tuple(findings), reason))

    def skip(rule: str, title: str, reason: str) -> None:
        results.append(RuleResult(rule, title, Status.NOT_RUN, (), reason))

    reviews = review.get("reviews", []) if review else None

    # J-01 -- a review must attach to a statement that still exists.
    if reviews is None:
        skip("J-01", "every review joins to a declaration by statement digest",
             "no review supplied (--review)")
    else:
        j01: list[Finding] = []
        for r in reviews:
            if r.get("reviewed_statement_digest") in by_digest:
                continue
            where = f"{r.get('key')}/{r.get('decl')}"
            if r.get("decl") in names:
                j01.append(Finding("J-01", where,
                    "reviewed_statement_digest matches no declaration, but decl "
                    "still exists -- the statement was RESTATED under the review, "
                    "so the review is of something that is no longer there"))
            else:
                j01.append(Finding("J-01", where,
                    "reviewed_statement_digest matches no declaration AND decl is "
                    "absent -- rename plus restatement at once. Nothing mechanical "
                    "can tell which declaration was meant; a human must adjudicate"))
        add("J-01", "every review joins to a declaration by statement digest", j01)

    # J-02 -- the `decl` hint must agree with the digest that did the joining.
    # A hint that points elsewhere is how a review floats onto the wrong thing.
    if reviews is None:
        skip("J-02", "every review's decl hint agrees with its digest",
             "no review supplied (--review)")
    else:
        j02: list[Finding] = []
        for r in reviews:
            matched = by_digest.get(r.get("reviewed_statement_digest"))
            if not matched:
                continue  # J-01's finding, not this one's
            hinted = r.get("decl")
            if hinted in names and hinted not in {d["name"] for d in matched}:
                j02.append(Finding("J-02", f"{r.get('key')}/{hinted}",
                    f"decl hint names {hinted!r}, but the digest selects "
                    f"{sorted(d['name'] for d in matched)} -- a name-keyed join "
                    f"would have attached this review to the wrong declaration"))
        add("J-02", "every review's decl hint agrees with its digest", j02)

    # J-03 -- no two reviews of the same statement may disagree in silence.
    # Deduping them would pick a winner; the point is that a human must.
    if reviews is None:
        skip("J-03", "no two reviews of one statement disagree",
             "no review supplied (--review)")
    else:
        seen: dict[tuple[str, str], list[dict]] = {}
        for r in reviews:
            seen.setdefault(
                (r.get("key"), r.get("reviewed_statement_digest")), []).append(r)
        j03 = [
            Finding("J-03", f"{key}",
                    f"{len(group)} reviews of the same statement disagree: "
                    f"{sorted({(x.get('faithfulness'), x.get('relation_confirmed')) for x in group})}")
            for (key, _digest), group in sorted(seen.items())
            if len({(x.get("faithfulness"), x.get("relation_confirmed")) for x in group}) > 1
        ]
        add("J-03", "no two reviews of one statement disagree", j03)

    # J-04 -- every cited key was put to the corpus. A key with no result is a
    # key nobody asked about, which must not read the same as one that resolved.
    if resolution is None:
        skip("J-04", "every cited key has a resolution result",
             "no resolution supplied (--resolution)")
    else:
        resolved = {r["key"] for r in resolution.get("results", [])}
        add("J-04", "every cited key has a resolution result",
            [Finding("J-04", k, "cited, but the resolution carries no result for it")
             for k in sorted(cited_keys - resolved)])

    # J-05 -- and nothing resolved a key that nothing cites. A dangling result is
    # a registry entry the code dropped, or a resolution run against a different
    # revision; either way the two sides are not looking at the same work.
    if resolution is None:
        skip("J-05", "no resolution result is for an uncited key",
             "no resolution supplied (--resolution)")
    else:
        add("J-05", "no resolution result is for an uncited key",
            [Finding("J-05", r["key"],
                     f"resolved {r.get('resolution')!r}, but no @[cites] names it")
             for r in sorted(resolution.get("results", []), key=lambda x: x["key"])
             if r["key"] not in cited_keys])

    # J-06 -- the work queue: every registry entry with zero inbound citations,
    # partitioned by `kind`, with each entry's open frontier rolled up.
    if registry is None:
        skip("J-06", "the obligation lane: registry entries with no citation",
             WORKQUEUE_BLOCKER)
    else:
        try:
            entries = registry_entries(registry)
        except RegistryShapeError as exc:
            # Never a finding. An unreadable registry would otherwise report a
            # clean work queue over zero entries -- a vacuous pass.
            skip("J-06", "the obligation lane: registry entries with no citation",
                 f"registry not readable by this build: {exc}")
        else:
            uncited = {k: e for k, e in sorted(entries.items())
                       if k not in cited_keys}
            findings = []
            for key, entry in uncited.items():
                still_open = open_frontier(entry)
                rolled = f"; open frontier {still_open}" if still_open else ""
                findings.append(Finding("J-06", key,
                    f"kind={kind_of(entry)!r}, zero inbound cites{rolled}"))
            # Counted PER KIND and never totalled. A queue of 90 entries in one
            # lane and 10 in another is not "100 things to do", and the whole
            # value of this file is that an agent can plan against it.
            by_kind: dict[str, int] = {}
            for entry in uncited.values():
                by_kind[kind_of(entry)] = by_kind.get(kind_of(entry), 0) + 1
            add("J-06", "the obligation lane: registry entries with no citation",
                findings,
                reason=(", ".join(f"{k}: {n}" for k, n in sorted(by_kind.items()))
                        or f"all {len(entries)} entries are cited"))

    return results


def claim_table(
    declarations: dict,
    *,
    review: dict | None = None,
    resolution: dict | None = None,
    environment: dict | None = None,
    withdrawals: dict | None = None,
) -> list[Row]:
    """One row per citation binding, with each axis kept in its own column.

    `environment` is what makes the review columns mean anything. A review
    carries the `reviewed_env_digest` it was made against; without an
    environment to compare it to, this function cannot establish that the
    verdict is about the build the caller is holding, so the review columns
    render `not_run` -- absent input says so out loud, as everywhere else in
    this package. Supply it and a foreign-env review renders `not_applicable`.

    Passing no `environment` therefore never renders a verdict. That is the
    conservative direction on purpose: the failure it forecloses is a review of
    Lean v4.29 reading as a pass for a table about v4.31, which is silent, and
    the cost is an operator having to pass `--environment`, which is loud.
    """
    by_digest_key: dict[tuple[str, str], dict] = {}
    for r in (review or {}).get("reviews", []):
        by_digest_key[(r.get("key"), r.get("reviewed_statement_digest"))] = r
    by_key = {r["key"]: r for r in (resolution or {}).get("results", [])}
    want = (environment or {}).get("env_digest")
    # A withdrawal is a trust-REMOVING act, so an unreadable withdrawal list
    # must not read as "nothing is withdrawn" -- `withdrawn_keys` raises rather
    # than returning an empty set, and the caller reports that as a run that
    # did not happen.
    retracted = withdrawn_keys(withdrawals)

    def review_cells(rev: dict | None) -> tuple[str, str]:
        """`(faithfulness, relation_confirmed)` for one row, applicability first.

        Order matters. Applicability is decided BEFORE the verdict is read, so
        there is no code path on which a foreign verdict is fetched and then
        overwritten -- the value simply never leaves the record.
        """
        if review is None or rev is None:
            return NOT_RUN, NOT_RUN
        if want is None:
            return NOT_RUN, NOT_RUN
        if rev.get("reviewed_env_digest") != want:
            return NOT_APPLICABLE, NOT_APPLICABLE
        return (rev.get("faithfulness", NOT_RUN),
                rev.get("relation_confirmed", NOT_RUN))

    rows = []
    for b in bindings(declarations):
        faithfulness, relation_confirmed = review_cells(
            by_digest_key.get((b.key, b.statement_digest)))
        res = by_key.get(b.key)
        rows.append(Row(
            withdrawn=b.key in retracted,
            key=b.key,
            decl=b.decl,
            claimed=b.relation_claimed,
            faithfulness=faithfulness,
            relation_confirmed=relation_confirmed,
            resolution=(res or {}).get("resolution", NOT_RUN) if resolution else NOT_RUN,
            frontier=",".join(b.frontier) or "-",
        ))
    return rows


def workqueue(declarations: dict, registry: dict, *,
              registry_sha256: str, declarations_sha256: str) -> dict:
    """Build `workqueue/1.0` — every registry entry with zero inbound cites.

    The same set `J-06` reports, written down. `J-06` says *that* the queue is
    non-empty in a run nobody keeps; this is the file an agent plans against.

    **Why `join` may write this when it deliberately writes nothing else.**
    `join` and `conformance` emit no artifact because a file carrying a
    top-level verdict is exactly the `aggregate-verdict` rejection fixture our
    own tooling would refuse. A work queue is an inventory: it records what is
    owed, judges nothing, and has no axis to collapse. The rule was never "no
    files" — it was "no aggregate verdicts" — and this respects it.

    Lanes are keyed by whatever `kind` actually appeared, never by a re-declared
    enum, so the open size-ceiling decision (does a zero-axis `sketch` lane
    exist?) changes `registry/1.0` alone and leaves this schema untouched.

    Counted per lane and never totalled, for the reason `J-06` gives.
    """
    cited = {b.key for b in bindings(declarations)}
    lanes: dict[str, dict] = {}

    for key, entry in sorted(registry_entries(registry).items()):
        if key in cited:
            continue
        lane = lanes.setdefault(kind_of(entry), {"count": 0, "entries": []})
        lane["count"] += 1
        lane["entries"].append({
            "key": key,
            "title": entry.get("title") or key,
            "note": entry.get("note"),
            "frontier_open": open_frontier(entry),
            "depends_on": list(entry.get("depends_on") or []),
        })

    return {
        "schema_version": "workqueue/1.0",
        "registry_id": registry.get("registry_id"),
        "registry_sha256": registry_sha256,
        "declarations_sha256": declarations_sha256,
        # Sorted, like every other collection this package emits (E-10's rule
        # for `axioms` and `local_deps`). Insertion order here would already be
        # deterministic, so this is for the diff rather than for reproducibility:
        # a lane appearing or emptying should move one hunk, not reshuffle the
        # file.
        "lanes": {kind: lanes[kind] for kind in sorted(lanes)},
    }


class Coverage(NamedTuple):
    bindings: int
    keys: int
    reviewed: int
    resolved: int
    frontier_open: int
    #: Rows carrying a review of a DIFFERENT environment. Its own count because
    #: folding it into `reviewed` would restate the soundness hole as a
    #: statistic, and folding it into the unreviewed remainder would lose the
    #: fact that a reviewer has already read these -- the cheapest work in the
    #: queue is re-confirming them, and that is invisible if they read as never
    #: looked at.
    review_not_applicable: int


def coverage(rows: Iterable[Row]) -> Coverage:
    """Counts, kept apart.

    Never a ratio and never a single number: "9 of 12 reviewed" and "9 of 12
    resolved" are different facts about different rows, and a combined score
    would let one stand in for the other.
    """
    rows = list(rows)
    return Coverage(
        bindings=len(rows),
        keys=len({r.key for r in rows}),
        reviewed=sum(1 for r in rows
                     if r.faithfulness not in (NOT_RUN, NOT_APPLICABLE)),
        resolved=sum(1 for r in rows if r.resolution != NOT_RUN),
        frontier_open=sum(1 for r in rows if r.frontier != "-"),
        review_not_applicable=sum(1 for r in rows
                                  if r.faithfulness == NOT_APPLICABLE),
    )
