"""`mfc lint` — the E-01..E-10 content rules over an emission.

Schema validation says an artifact is well *formed*. These rules say whether
what it contains is *allowed*. They are separate on purpose: a perfectly
well-formed emission can still declare an undeclared axiom, cite a key that
does not exist, or launder a `sorry`.

## A rule that did not run is not a rule that passed

Two rules need inputs that do not exist yet — `E-04` needs the statement
registry (gated on the open size-ceiling decision) and `E-09` needs the topic's
`closed_lanes` configuration. Those report **`not_run`**, never `pass`, and
`not_run` is printed on every invocation rather than folded into a summary
count.

This is the same rule the trust axes follow, applied one level down. A green
`mfc lint` with `E-04: not_run` must not be readable as "the citations
resolve", because nothing checked that they do.

`--require-all` turns any `not_run` into a failure, for the day the inputs
exist and their absence should stop the build.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, NamedTuple

from .registry import RegistryShapeError, open_frontier
from .registry import entries as registry_entries

#: Pretty-printer elision markers. `E-07` exists because an elided `type_pp`
#: lets two different statements hash identically -- and, separately, because
#: an elided statement cannot be re-elaborated, which is what `--restate-check`
#: needs (measured: 339/339 round-trip on elision-free statements, 0/61 on
#: elided ones).
ELISION_MARKERS = ("⋯", "…", "...")

#: Every rule, in order, with the title it reports under. Used by the bootstrap
#: path, which must report the same ten rules as an ordinary run rather than a
#: shorter list -- a rule missing from the table reads as a rule that does not
#: exist, where `not_run` reads as one that exists and did not fire.
#: `test_rules.py` asserts this table against an ordinary run, so the two
#: cannot drift.
RULE_TITLES: tuple[tuple[str, str], ...] = (
    ("E-01", "no declaration depends on sorryAx"),
    ("E-02", "every locally declared axiom is declared in axiom_policy"),
    ("E-03", "every axiom closure is within the declared policy"),
    ("E-04", "every cites[].key exists in the registry"),
    ("E-05", "relation_claimed=exact implies an empty frontier"),
    ("E-06", "relation_claimed=no_claim carries a note"),
    ("E-07", "no type_pp or value_pp is elided"),
    ("E-08", "the emission is not vacuous"),
    ("E-09", "no declaration reaches into a closed lane"),
    ("E-10", "axioms and local_deps are sorted ascending"),
    ("E-11", "no unfinished declaration claims forbidden vocabulary"),
)


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_RUN = "not_run"


class Finding(NamedTuple):
    rule: str
    where: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.rule} {self.where}: {self.detail}"


class RuleResult(NamedTuple):
    rule: str
    title: str
    status: Status
    findings: tuple[Finding, ...] = ()
    reason: str = ""


def _policy(environment: dict) -> set[str]:
    return set(environment["axiom_policy"]["allowlist"]) | {
        a["axiom"] for a in environment["axiom_policy"]["additions"]
    }


def _declared_additions(environment: dict) -> set[str]:
    return {a["axiom"] for a in environment["axiom_policy"]["additions"]}


def _sorted_ascending(xs: Iterable[str]) -> bool:
    xs = list(xs)
    return xs == sorted(xs)


def check(
    emission: dict,
    environment: dict,
    *,
    registry: dict | None = None,
    closed_lanes: list[dict] | None = None,
    bootstrap: bool = False,
) -> list[RuleResult]:
    """Run every rule. Order is E-01..E-10 so output is comparable run to run.

    `bootstrap` is the one state in which an emission may be empty (see
    `mfc.bootstrap`). It reports every rule `not_run` rather than `pass`: an
    empty emission satisfies E-01..E-10 vacuously, and a green table over it
    would say "no declaration depends on sorryAx" about a repository that has
    no declarations at all. It also reports every rule rather than a subset,
    so the table's shape does not change between a bootstrapping repository
    and a working one.
    """
    constants = emission["constants"]
    if bootstrap and not constants:
        return [
            RuleResult(rule, title, Status.NOT_RUN,
                       reason="bootstrap: the emission is empty, so this rule "
                              "has nothing to check -- it did not pass")
            for rule, title in RULE_TITLES
        ]
    policy = _policy(environment)
    additions = _declared_additions(environment)
    results: list[RuleResult] = []

    def add(rule: str, title: str, findings: list[Finding]) -> None:
        results.append(RuleResult(
            rule, title, Status.FAIL if findings else Status.PASS, tuple(findings)))

    # E-01 -- no sorryAx anywhere.
    add("E-01", "no declaration depends on sorryAx",
        [Finding("E-01", c["name"], "axiom closure contains sorryAx")
         for c in constants if "sorryAx" in c["axioms"]])

    # E-02 -- a local axiom must be declared in the environment, with a reason.
    add("E-02", "every locally declared axiom is declared in axiom_policy",
        [Finding("E-02", c["name"], "declares an axiom absent from "
                                    "axiom_policy.additions[].axiom")
         for c in constants
         if c["kind"] == "axiom" and c["name"] not in additions])

    # E-03 -- axiom closure within policy, recomputed set-wise.
    e03: list[Finding] = []
    for c in constants:
        extra = sorted(set(c["axioms"]) - policy)
        if extra:
            e03.append(Finding("E-03", c["name"], f"axioms outside policy: {extra}"))
    add("E-03", "every axiom closure is within the declared policy", e03)

    # The registry is read ONCE, and a shape this build does not recognise
    # stops both rules that need it rather than silently emptying them.
    known: dict[str, dict] | None = None
    registry_problem: str | None = None
    if registry is not None:
        try:
            known = registry_entries(registry)
        except RegistryShapeError as exc:
            registry_problem = str(exc)

    # E-04 -- every cited key exists in the registry.
    if known is None:
        results.append(RuleResult(
            "E-04", "every cites[].key exists in the registry", Status.NOT_RUN,
            reason=registry_problem or
                   "no registry supplied (--registry); the registry schema is "
                   "gated on the open size-ceiling decision"))
    else:
        add("E-04", "every cites[].key exists in the registry",
            [Finding("E-04", c["name"], f"cites unknown key {cite['key']!r}")
             for c in constants for cite in c["cites"] if cite["key"] not in known])

    # E-05 -- `exact` may not carry an open frontier.
    #
    # The registry half (the cited ENTRY's frontier must also be empty) needs
    # the registry; the emission half is checkable now, so it runs and the
    # split is stated rather than silently half-done.
    e05 = [Finding("E-05", c["name"],
                   f"relation_claimed=exact with a non-empty frontier "
                   f"{cite['frontier']}")
           for c in constants for cite in c["cites"]
           if cite["relation_claimed"] == "exact" and cite["frontier"]]
    if known is None:
        results.append(RuleResult(
            "E-05", "relation_claimed=exact implies an empty frontier",
            Status.FAIL if e05 else Status.NOT_RUN, tuple(e05),
            reason="" if e05 else
                   (registry_problem or
                    "emission half passed; the registry half (cited entry's own "
                    "frontier) needs --registry")))
    else:
        for c in constants:
            for cite in c["cites"]:
                entry = known.get(cite["key"])
                if entry is None or cite["relation_claimed"] != "exact":
                    continue
                # OPEN items only. An entry whose frontier is fully discharged
                # has nothing outstanding, and refusing `exact` there would
                # penalise the one thing a frontier is meant to reward.
                still_open = open_frontier(entry)
                if still_open:
                    e05.append(Finding("E-05", c["name"],
                        f"relation_claimed=exact but registry entry "
                        f"{cite['key']!r} leaves {still_open} undischarged"))
        add("E-05", "relation_claimed=exact implies an empty frontier", e05)

    # E-06 -- `no_claim` without a note says two things are related without
    # saying how, which is unreadable.
    add("E-06", "relation_claimed=no_claim carries a note",
        [Finding("E-06", c["name"], f"no_claim on {cite['key']!r} with no note")
         for c in constants for cite in c["cites"]
         if cite["relation_claimed"] == "no_claim" and not (cite["note"] or "").strip()])

    # E-07 -- no elided pretty-printed text.
    e07: list[Finding] = []
    for c in constants:
        for field in ("type_pp", "value_pp"):
            text = c.get(field)
            if not isinstance(text, str):
                continue
            hit = next((m for m in ELISION_MARKERS if m in text), None)
            if hit is not None:
                e07.append(Finding("E-07", c["name"],
                    f"{field} contains the elision marker {hit!r}; two different "
                    f"statements can hash identically, and it cannot be re-elaborated"))
    add("E-07", "no type_pp or value_pp is elided", e07)

    # E-08 -- the vacuous pass, restated.
    #
    # Worth knowing before relying on it: this rule is UNREACHABLE through any
    # `mfc` subcommand. The emission schema sets `constants: minItems 1` and
    # `counts.total/in_scope: minimum 1`, so an empty emission is not a
    # representable artifact, and every subcommand validates before it reads --
    # the `empty-emission` fixture is rejected by `mfc validate`, not by this.
    #
    # It is kept, and kept honest, for a caller that reaches `check()` directly.
    # The property is enforced structurally, which is the stronger arrangement;
    # a rule that a CLI path can skip is the weaker one, and it should not be
    # what the vacuous-pass guarantee rests on. The `.ilean` half -- a PARTIAL
    # sweep, which no `minItems` can see -- is `mfc check-ilean-coverage`.
    e08 = ([] if emission["counts"]["in_scope"] >= 1
           else [Finding("E-08", "counts.in_scope",
                         "0 in-scope constants; an empty emission is the "
                         "signature of a mis-scoped emitter, not a clean build")])
    results.append(RuleResult(
        "E-08", "the emission is not vacuous",
        Status.FAIL if e08 else Status.PASS, tuple(e08),
        reason="" if e08 else "counts half only, and structurally guaranteed by "
                              "the schema before this runs; the half that finds a "
                              "PARTIAL sweep is mfc check-ilean-coverage"))

    # E-09 -- closed lanes, mechanised.
    if closed_lanes is None:
        results.append(RuleResult(
            "E-09", "no declaration reaches into a closed lane", Status.NOT_RUN,
            reason="no closed_lanes configuration supplied (--closed-lanes)"))
    else:
        e09: list[Finding] = []
        for lane in closed_lanes:
            prefixes = tuple(lane.get("forbidden_module_prefixes", ()))
            forbidden = tuple(lane.get("forbidden_constants", ()))
            for c in constants:
                if prefixes and c["module"].startswith(prefixes):
                    e09.append(Finding("E-09", c["name"],
                        f"module {c['module']!r} is in closed lane {lane['name']!r}"))
                for bad in forbidden:
                    if bad in c["local_deps"] or bad in c["type_pp"]:
                        e09.append(Finding("E-09", c["name"],
                            f"reaches {bad!r}, closed by lane {lane['name']!r}"))
        add("E-09", "no declaration reaches into a closed lane", e09)

    # E-10 -- sorted arrays, which is what makes the emission reproducible.
    e10: list[Finding] = []
    for c in constants:
        for field in ("axioms", "local_deps"):
            if not _sorted_ascending(c[field]):
                e10.append(Finding("E-10", c["name"], f"{field} is not sorted ascending"))
    add("E-10", "axioms and local_deps are sorted ascending", e10)

    # E-11 -- CLAUDE.md section 3, mechanised.
    #
    # E-09 catches section 4: a declaration that REACHES INTO a closed lane, by
    # module prefix or by constant. Section 3 is the other failure and nothing
    # caught it:
    #
    #     abbrev NumLattice : Type := Fin 2 -> Z
    #
    # imports nothing forbidden, reaches no closed lane, and passes E-01..E-10.
    # A doc-comment calling it the numerical Grothendieck group of a Kuznetsov
    # component is the entire claim, and until the emitter carried `doc` there
    # was no artifact in which that text appeared.
    #
    # SCOPED TO DECLARATIONS WITH AN OPEN FRONTIER, which is what makes this a
    # rule about overclaiming rather than a banned-words list. A declaration
    # that genuinely has Chern classes and cites a statement with no open
    # frontier may say so. The one that says so while ALSO recording that the
    # supporting work is unfinished is the one making a claim it cannot back,
    # and that pairing -- vocabulary plus open frontier -- is the finding.
    #
    # Names AND doc-comments, per the issue. `type_pp` is deliberately not
    # linted: a type mentioning a Mathlib name it genuinely uses is not a
    # claim, and E-09's `forbidden_constants` already reads it.
    lanes_with_vocab = [] if closed_lanes is None else [
        lane for lane in closed_lanes if lane.get("forbidden_vocabulary")]
    if closed_lanes is None:
        results.append(RuleResult(
            "E-11", "no unfinished declaration claims forbidden vocabulary",
            Status.NOT_RUN,
            reason="no closed_lanes configuration supplied (--closed-lanes)"))
    elif not lanes_with_vocab:
        results.append(RuleResult(
            "E-11", "no unfinished declaration claims forbidden vocabulary",
            Status.NOT_RUN,
            reason="no lane declares forbidden_vocabulary[]; section 3 is not "
                   "mechanised for this topic, only asserted"))
    else:
        e11: list[Finding] = []
        for c in constants:
            if not any(cite["frontier"] for cite in c["cites"]):
                continue
            haystacks = [("name", c["name"])]
            if c.get("doc"):
                haystacks.append(("doc-comment", c["doc"]))
            for lane in lanes_with_vocab:
                for word in lane["forbidden_vocabulary"]:
                    for where, text in haystacks:
                        if word.casefold() in text.casefold():
                            e11.append(Finding(
                                "E-11", c["name"],
                                f"{where} claims {word!r}, closed by lane "
                                f"{lane['name']!r}, while its own citation "
                                f"records an open frontier"))
        add("E-11", "no unfinished declaration claims forbidden vocabulary", e11)

    return results


def summarize(results: list[RuleResult]) -> tuple[int, int, int]:
    """(passed, failed, not_run)."""
    return (
        sum(1 for r in results if r.status is Status.PASS),
        sum(1 for r in results if r.status is Status.FAIL),
        sum(1 for r in results if r.status is Status.NOT_RUN),
    )
