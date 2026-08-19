"""Tests for `mfc check-review` — RV-01, the rule review/1.0 cannot state.

`reviewed_statement_pp` is nullable in the schema and that is correct: null is
the honest way to record "not captured". It is also the value that makes a
review silently un-carryable, because `restate-check` returns `not_checkable`
with nothing to parse and `not_checkable` carries nothing forward.

The reason this needs a rule rather than a schema change is that both readings
of null are legitimate. The schema describes shape; RV-01 describes whether a
review is *usable*, and only the second question has a wrong answer.
"""

from __future__ import annotations

import json
from pathlib import Path

from contract.mfc.cli import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, main
from contract.mfc.review import check
from contract.mfc.rules import Status

HERE = Path(__file__).resolve().parent
VALID = HERE.parent / "testdata" / "artifacts" / "valid" / "review-1.0.json"


def _review(**over) -> dict:
    doc = json.loads(VALID.read_text(encoding="utf-8"))
    doc["reviews"][0].update(over)
    return doc


def _rv01(doc: dict):
    return next(r for r in check(doc) if r.rule == "RV-01")


def test_a_captured_statement_passes() -> None:
    assert _rv01(_review(reviewed_statement_pp="∀ (n : Nat), n = n")).status is Status.PASS


def test_a_null_statement_fails() -> None:
    assert _rv01(_review(reviewed_statement_pp=None)).status is Status.FAIL


def test_whitespace_is_not_a_statement() -> None:
    """`" "` is the shape of a capture and the substance of a null."""
    assert _rv01(_review(reviewed_statement_pp="   \n  ")).status is Status.FAIL


def test_the_finding_names_the_entry_and_the_remedy() -> None:
    detail = _rv01(_review(reviewed_statement_pp=None)).findings[0]
    assert "not_checkable" in detail.detail
    assert "--capture" in detail.detail


def test_no_reviews_is_not_run_rather_than_a_pass() -> None:
    """A repo with no reviews yet is ordinary; calling it a pass is the vacuous one."""
    doc = json.loads(VALID.read_text(encoding="utf-8"))
    doc["reviews"] = []
    assert _rv01(doc).status is Status.NOT_RUN


def test_one_bad_entry_among_good_ones_still_fails(tmp_path: Path) -> None:
    doc = json.loads(VALID.read_text(encoding="utf-8"))
    good = dict(doc["reviews"][0], reviewed_statement_pp="∀ (n : Nat), n = n")
    bad = dict(doc["reviews"][0], key="stmt:9f4c1a20b7d3:other",
               reviewed_statement_pp=None)
    doc["reviews"] = [good, bad]
    result = _rv01(doc)
    assert result.status is Status.FAIL
    assert len(result.findings) == 1


# --- the CLI -----------------------------------------------------------------

def _write(tmp_path: Path, doc: dict) -> Path:
    p = tmp_path / "review.json"
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return p


def test_cli_passes_a_captured_review(tmp_path: Path) -> None:
    p = _write(tmp_path, _review(reviewed_statement_pp="∀ (n : Nat), n = n"))
    assert main(["check-review", str(p)]) == EXIT_OK


def test_cli_fails_a_null_review(tmp_path: Path) -> None:
    p = _write(tmp_path, _review(reviewed_statement_pp=None))
    assert main(["check-review", str(p)]) == EXIT_FINDINGS


def test_cli_validates_before_linting(tmp_path: Path) -> None:
    p = tmp_path / "review.json"
    p.write_text(json.dumps({"schema_version": "review/1.0",
                             "reviews": [{"key": "nope"}]}), encoding="utf-8")
    assert main(["check-review", str(p)]) != EXIT_OK


def test_cli_refuses_a_missing_file(tmp_path: Path) -> None:
    assert main(["check-review", str(tmp_path / "absent.yaml")]) == EXIT_USAGE
