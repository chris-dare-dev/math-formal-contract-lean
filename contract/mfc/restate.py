"""`mfc restate-check` — reading the Lean checker's verdict. #188.

The elaboration itself is **Lean's** — `MathFormalContract.RestateCheck` parses
`reviewed_statement_pp`, elaborates it in the current environment and asks
`isDefEq` against the declaration's current type. It cannot live here: Python
cannot elaborate Lean, and a reimplementation would be a second answer to the
question the first one already answers.

What lives here is what the rest of this package does everywhere else: read the
artifact, and say what it means for trust.

## The one rule this file exists to enforce

**`not_checkable` is not `changed`, and a review is only carried forward on
`restated`.** Three outcomes, three different obligations:

* `restated` — the statement is defeq to what the declaration says now. The
  review survives the bump; `faithfulness` may be carried forward.
* `changed` — it elaborates and is not defeq. The statement moved, so the
  review is invalidated and the entry's `human_review` reverts to `none`
  rather than staying green.
* `not_checkable` — it could not be parsed or elaborated. **Nobody knows**
  whether it changed. The review may not be carried forward, and the reason
  this is not simply `changed` is that the two send a reviewer to different
  places: `changed` says re-read this statement, `not_checkable` says the tool
  failed and the statement may be perfectly fine.

A run that reported `not_checkable` as `changed` would be conservative in the
verdict and wrong in the diagnosis, and it would hide a broken checker behind a
pile of apparently-invalidated reviews.
"""

from __future__ import annotations

from .rules import Finding, RuleResult, Status


class RestateError(Exception):
    """The check could not run. Exit 2, never a finding."""


def carried_forward(restate: dict) -> set[str]:
    """Keys whose review survives, i.e. `restated` and nothing else."""
    return {r["key"] for r in restate.get("results", [])
            if r.get("outcome") == "restated"}


def check(restate: dict, *, review: dict | None = None) -> list[RuleResult]:
    """`T-01`..`T-03` over a `restate/1.0` document."""
    results: list[RuleResult] = []
    entries = restate.get("results") or []
    by_key = {r.get("key"): r for r in entries}

    def add(rule: str, title: str, findings: list[Finding], reason: str = "") -> None:
        results.append(RuleResult(
            rule, title, Status.FAIL if findings else Status.PASS,
            tuple(findings), reason))

    # T-01 -- every reviewed entry was actually checked.
    #
    # A restate run that silently omits an entry looks exactly like a run where
    # that entry was fine, and the omission is invisible in the counts because
    # the counts are computed from the results that ARE there.
    if review is None:
        results.append(RuleResult(
            "T-01", "every reviewed entry appears in the restate run",
            Status.NOT_RUN,
            reason="no review supplied (--review); an entry missing from the "
                   "restate results cannot be distinguished from one that was "
                   "never reviewed"))
    else:
        reviewed = {r.get("key") for r in review.get("reviews", [])}
        add("T-01", "every reviewed entry appears in the restate run",
            [Finding("T-01", key,
                     "was reviewed, but the restate run does not mention it; "
                     "an omitted entry reads exactly like a checked one")
             for key in sorted(reviewed - set(by_key))])

    # T-02 -- a `changed` entry's review must not still read as current.
    if review is None:
        results.append(RuleResult(
            "T-02", "no invalidated review is still presented as current",
            Status.NOT_RUN, reason="no review supplied (--review)"))
    else:
        changed = {k for k, r in by_key.items() if r.get("outcome") == "changed"}
        add("T-02", "no invalidated review is still presented as current",
            [Finding("T-02", r.get("key"),
                     f"restate says `changed`, so this review is invalidated: "
                     f"its faithfulness verdict describes a statement the "
                     f"declaration no longer makes. It must revert to none "
                     f"rather than stay {r.get('faithfulness')!r}")
             for r in review.get("reviews", [])
             if r.get("key") in changed and r.get("faithfulness") not in (None, "none")])

    # T-03 -- not_checkable is reported as itself, and carries a reason.
    #
    # The schema already forbids a null reason; this is the backstop, and it
    # also states the rule the schema cannot: that these are NOT to be counted
    # as `changed` anywhere downstream.
    add("T-03", "every not_checkable says why, and is not counted as changed",
        [Finding("T-03", r.get("key"),
                 "outcome not_checkable with no reason: 'parse failed' and "
                 "'elaboration logged an error' send a reviewer to different "
                 "places, and neither means the statement changed")
         for r in entries
         if r.get("outcome") == "not_checkable" and not (r.get("reason") or "").strip()],
        reason="not_checkable means NOBODY KNOWS whether the statement changed; "
               "it is never evidence that it did")
    return results
