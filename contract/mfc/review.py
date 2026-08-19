"""`mfc check-review` — the rules `review/1.0` cannot state about itself.

`reviewed_statement_pp` is nullable in the schema, and that is correct: null is
the honest way to record "not captured", and forbidding it would leave a
reviewer with no way to say so. It is also the value that makes a review
**silently un-carryable**.

## The failure this exists for

`MathFormalContract.RestateCheck` parses that string to elaborate the statement
and ask `isDefEq` against the declaration's current type. With nothing to
parse, the outcome is `not_checkable`, and `not_checkable` carries nothing
forward — so at the next dependency bump `C-10` fails and the review must be
performed again.

Nothing about the review looks wrong in the meantime. It has a reviewer, a
date, a faithfulness verdict, three digests, and it passes `mfc validate`.
Nothing distinguishes it from a review that will survive, and the distinction
only becomes visible when it is expensive: #136 costed recovery at roughly two
hours an entry, per bump.

## Why a rule and not a schema change

`minLength: 1` on the field would forbid recording "not captured" honestly, and
it would be a `review/1.0` amendment — which stopped being free when `v0.1.0`
was tagged. The schema describes shape; this describes a property of a review
that is *usable*, which is a different question.

## Why it gates rather than warns

The reason a null slips through is that nothing stops it. A warning nobody
reads stops nothing, and the whole failure mode here is a thing that looks fine
until it is costly. `mfc capture` (#654) makes the value cheap to obtain, so
the rule asks for something the tooling now supplies.
"""

from __future__ import annotations

from .rules import Finding, RuleResult, Status


def check(review: dict) -> list[RuleResult]:
    """`RV-01` over a `review/1.0` document."""
    reviews = review.get("reviews") or []
    if not reviews:
        # Not a finding. A repository with no reviews yet is an ordinary state,
        # and reporting it as a pass would be the vacuous one.
        return [RuleResult(
            "RV-01", "every review records the statement it was made against",
            Status.NOT_RUN, (),
            "the review carries no entries; there is nothing to check, which is "
            "not the same as nothing being wrong")]

    findings = [
        Finding("RV-01", f"{r.get('key')}:{r.get('decl')}",
                "reviewed_statement_pp is null, so `mfc restate-check` returns "
                "not_checkable for this entry and the review cannot be carried "
                "across an environment bump. It will look complete until the "
                "next dependency change, then cost a full re-read. Capture it "
                "with `--capture` on the topic repo's restate executable")
        for r in reviews
        if not (r.get("reviewed_statement_pp") or "").strip()
    ]
    return [RuleResult(
        "RV-01", "every review records the statement it was made against",
        Status.FAIL if findings else Status.PASS, tuple(findings),
        "" if findings else f"{len(reviews)} review(s), all carryable")]
