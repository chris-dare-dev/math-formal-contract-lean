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

#: Pretty-printer elision markers. `E-07` exists because an elided `type_pp`
#: lets two different statements hash identically -- and, separately, because
#: an elided statement cannot be re-elaborated, which is what `--restate-check`
#: needs (measured: 339/339 round-trip on elision-free statements, 0/61 on
#: elided ones).
ELISION_MARKERS = ("⋯", "…", "...")


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
) -> list[RuleResult]:
    """Run every rule. Order is E-01..E-10 so output is comparable run to run."""
    constants = emission["constants"]
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

    # E-04 -- every cited key exists in the registry.
    if registry is None:
        results.append(RuleResult(
            "E-04", "every cites[].key exists in the registry", Status.NOT_RUN,
            reason="no registry supplied (--registry); the registry schema is "
                   "gated on the open size-ceiling decision"))
    else:
        known = {e["key"] for e in registry.get("statements", [])}
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
    if registry is None:
        results.append(RuleResult(
            "E-05", "relation_claimed=exact implies an empty frontier",
            Status.FAIL if e05 else Status.NOT_RUN, tuple(e05),
            reason="" if e05 else "emission half passed; the registry half "
                                  "(cited entry's own frontier) needs --registry"))
    else:
        known = {e["key"]: e for e in registry.get("statements", [])}
        for c in constants:
            for cite in c["cites"]:
                entry = known.get(cite["key"])
                if (cite["relation_claimed"] == "exact" and entry
                        and entry.get("frontier")):
                    e05.append(Finding("E-05", c["name"],
                        f"relation_claimed=exact but registry entry "
                        f"{cite['key']!r} has an open frontier"))
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

    # E-08 -- the vacuous pass. The `.ilean` half is `check-ilean-coverage`.
    e08 = ([] if emission["counts"]["in_scope"] >= 1
           else [Finding("E-08", "counts.in_scope",
                         "0 in-scope constants; an empty emission is the "
                         "signature of a mis-scoped emitter, not a clean build")])
    results.append(RuleResult(
        "E-08", "the emission is not vacuous",
        Status.FAIL if e08 else Status.PASS, tuple(e08),
        reason="" if e08 else "counts half only; the .ilean coverage half is "
                              "mfc check-ilean-coverage"))

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

    return results


def summarize(results: list[RuleResult]) -> tuple[int, int, int]:
    """(passed, failed, not_run)."""
    return (
        sum(1 for r in results if r.status is Status.PASS),
        sum(1 for r in results if r.status is Status.FAIL),
        sum(1 for r in results if r.status is Status.NOT_RUN),
    )
