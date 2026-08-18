"""`restate-check` — #188, and the rule it exists to hold.

`env_digest` includes every package revision, so `statement_stable` is
`not_applicable` across environments **by construction**: a single Mathlib or
anchor bump invalidates 100% of human review simultaneously, and recovery costs
the entire original review budget, per bump, at ~2h an entry.

The Lean half does the work — parse `reviewed_statement_pp`, elaborate it in
the current environment, `isDefEq` against the declaration's current type — and
is exercised in `test/MathFormalContractTest.lean`, where all three outcomes
are reached against a real compiled declaration and the logged-error case is
shown NOT to leak into the build output.

These tests hold the rule the Python half owns: **`not_checkable` is not
`changed`, and only `restated` carries a review forward.** The three send a
reviewer to three different places, and collapsing any pair hides either a
broken checker or an invalidated review.
"""

from __future__ import annotations

import json
from pathlib import Path

from contract.mfc.cli import EXIT_FINDINGS, EXIT_OK, main
from contract.mfc.restate import carried_forward
from contract.mfc.restate import check as restate_check
from contract.mfc.rules import Status

HERE = Path(__file__).resolve().parent
VALID = HERE.parent / "testdata" / "artifacts" / "valid"
BAD = HERE.parent / "testdata" / "artifacts" / "invalid"
REPORT = VALID / "restate-1.0.json"


def _report() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


def _review(**over) -> dict:
    base = json.loads((VALID / "review-1.0.json").read_text(encoding="utf-8"))
    return base


def _t(report: dict, review: dict | None = None) -> dict:
    return {r.rule: r for r in restate_check(report, review=review)}


# --------------------------------------------------------------------------
# Only `restated` carries a review forward.
# --------------------------------------------------------------------------

def test_only_restated_is_carried_forward() -> None:
    """The whole point. `changed` invalidates; `not_checkable` is unknown."""
    forward = carried_forward(_report())
    assert forward == {"stmt:9f4c1a20b7d3:bridgeland2007.lem-8.2"}


def test_not_checkable_is_not_counted_as_changed() -> None:
    """They are different obligations.

    `changed` says re-read this statement. `not_checkable` says the tool
    failed and the statement may be perfectly fine. A run that reported the
    second as the first would be conservative in its verdict and wrong in its
    diagnosis, hiding a broken checker behind apparently-invalidated reviews.
    """
    report = _report()
    outcomes = [r["outcome"] for r in report["results"]]
    assert outcomes.count("changed") == 1
    assert outcomes.count("not_checkable") == 1
    assert report["counts"]["changed"] == 1
    assert report["counts"]["not_checkable"] == 1
    # And no field anywhere sums them.
    assert "invalid" not in report["counts"]
    assert "failed" not in report["counts"]


def test_the_counts_carry_no_aggregate() -> None:
    """A single number here would be the token every schema in this contract
    refuses."""
    schema = json.loads((HERE.parent / "mfc" / "schema" /
                         "restate-1.0.schema.json").read_text(encoding="utf-8"))
    counts = schema["properties"]["counts"]["properties"]
    assert set(counts) == {"restated", "changed", "not_checkable"}


# --------------------------------------------------------------------------
# T-03: a not_checkable that cannot be acted on.
# --------------------------------------------------------------------------

def test_t03_flags_a_not_checkable_with_no_reason() -> None:
    report = _report()
    report["results"][2]["reason"] = "   "
    assert _t(report)["T-03"].status is Status.FAIL


def test_the_schema_rejects_it_first() -> None:
    """Structural before rule, the arrangement R-01 and R-06 record."""
    assert main(["validate", str(BAD / "not-checkable-without-reason.json")]) \
        == EXIT_FINDINGS


# --------------------------------------------------------------------------
# T-01 and T-02 need the review, and say so when they lack it.
# --------------------------------------------------------------------------

def test_without_a_review_the_join_rules_do_not_run() -> None:
    results = _t(_report())
    assert results["T-01"].status is Status.NOT_RUN
    assert results["T-02"].status is Status.NOT_RUN
    assert all(results[r].reason for r in ("T-01", "T-02"))


def test_t01_catches_a_reviewed_entry_the_run_skipped() -> None:
    """An omitted entry reads exactly like a checked one, because the counts
    are computed from the results that ARE there."""
    review = _review()
    review["reviews"][0]["key"] = "stmt:9f4c1a20b7d3:never-checked"
    assert _t(_report(), review)["T-01"].status is Status.FAIL


def test_t02_catches_a_changed_review_still_reading_as_current() -> None:
    report, review = _report(), _review()
    key = report["results"][1]["key"]          # the `changed` one
    review["reviews"][0]["key"] = key
    review["reviews"][0]["faithfulness"] = "faithful"
    result = _t(report, review)["T-02"]
    assert result.status is Status.FAIL
    assert "invalidated" in result.findings[0].detail


def test_t02_passes_when_the_invalidated_review_reverted() -> None:
    report, review = _report(), _review()
    key = report["results"][1]["key"]
    review["reviews"][0]["key"] = key
    review["reviews"][0]["faithfulness"] = "none"
    assert _t(report, review)["T-02"].status is Status.PASS


# --------------------------------------------------------------------------
# reviewed_statement_pp, and why it is not type_pp.
# --------------------------------------------------------------------------

def test_review_carries_the_statement_the_human_read() -> None:
    schema = json.loads((HERE.parent / "mfc" / "schema" /
                         "review-1.0.schema.json").read_text(encoding="utf-8"))
    props = schema["$defs"]["review"]["properties"]
    assert "reviewed_statement_pp" in props
    why = props["reviewed_statement_pp"]["$comment"]
    assert "pp.explicit" in why, "the field must say why it is not type_pp"


def test_the_cli_runs() -> None:
    assert main(["restate-check", str(REPORT)]) == EXIT_OK
