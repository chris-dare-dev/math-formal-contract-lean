"""`withdrawals/1.0` — #176, red-team gap 5.

Nobody in the design batch addressed this, and it is the worst failure the
contract can have: **a record a human has determined is not faithful, still
being served as evidence**.

`review.yaml`'s `faithfulness: divergent|inadequate` and the registry's
`superseded_by` both live in the producer's *next tag*, and a consumer pins one
tag and re-serves it verbatim. With `v0.1.0` pinned and `v0.2.0` marking an
entry inadequate, the pinned surface keeps serving the old record until a human
re-pins — and nothing tells them to.

The property that makes the forward channel safe is asymmetry, and
`test_a_withdrawal_can_only_remove_trust` is the test that states it: a
withdrawal turns a served record into a withheld one and can never do the
reverse. That is why this one file may be read from a newer tag than the pin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.mfc.cli import EXIT_FINDINGS, EXIT_OK, main
from contract.mfc.join import claim_table
from contract.mfc.rules import Status
from contract.mfc.withdrawals import WithdrawalsError, check, withdrawn_keys

HERE = Path(__file__).resolve().parent
VALID = HERE.parent / "testdata" / "artifacts" / "valid"
WITHDRAWALS = VALID / "withdrawals-1.0.json"


def _doc() -> dict:
    return json.loads(WITHDRAWALS.read_text(encoding="utf-8"))


def _registry() -> dict:
    return json.loads((VALID / "registry-1.0.json").read_text(encoding="utf-8"))


def _run(doc: dict, **kw):
    return {r.rule: r for r in check(doc, **kw)}


# --------------------------------------------------------------------------
# The rules.
# --------------------------------------------------------------------------

def test_the_fixture_passes_every_runnable_rule() -> None:
    results = _run(_doc(), registry=_registry())
    assert not [r for r in results.values() if r.status is Status.FAIL]


def test_w01_a_key_is_withdrawn_once_or_not_at_all() -> None:
    """Two entries for one key make 'when was this withdrawn' unanswerable."""
    doc = _doc()
    doc["withdrawals"].append(dict(doc["withdrawals"][0], withdrawn_at="2026-09-01"))
    assert _run(doc)["W-01"].status is Status.FAIL


def test_w02_withdrawing_a_key_that_was_never_minted_fails() -> None:
    """A typo here removes trust from nothing and leaves the real entry served."""
    doc = _doc()
    doc["withdrawals"][0]["key"] = "stmt:9f4c1a20b7d3:typo"
    assert _run(doc, registry=_registry())["W-02"].status is Status.FAIL


def test_w03_a_file_for_another_registry_fails() -> None:
    doc = _doc()
    doc["registry_id"] = "0123456789ab"
    for w in doc["withdrawals"]:
        w["key"] = "stmt:0123456789ab:x"
    assert _run(doc, registry=_registry())["W-03"].status is Status.FAIL


def test_w04_a_removed_withdrawal_is_a_finding() -> None:
    """Append-only, checked rather than intended. A withdrawal that could be
    deleted is one a consumer cannot rely on having seen."""
    previous = _doc()
    now = _doc()
    now["withdrawals"] = []
    result = _run(now, previous=previous)["W-04"]
    assert result.status is Status.FAIL
    assert "cannot be told to forget it" in result.findings[0].detail


def test_w04_an_edited_withdrawal_is_a_finding() -> None:
    previous = _doc()
    now = _doc()
    now["withdrawals"][0]["reason"] = "actually it was fine"
    result = _run(now, previous=previous)["W-04"]
    assert result.status is Status.FAIL
    assert "reason" in result.findings[0].detail


def test_w04_a_pure_append_passes() -> None:
    previous = _doc()
    now = _doc()
    now["withdrawals"].append({
        "key": "stmt:9f4c1a20b7d3:bridgeland2007.lem-8.2",
        "withdrawn_at": "2026-09-01", "reason": "Superseded by a corrected entry.",
        "withdrawn_by": "Chris Dare", "supersedes_review": None})
    assert _run(now, previous=previous)["W-04"].status is Status.PASS


def test_without_the_previous_revision_append_only_is_not_run() -> None:
    """It is the property a consumer relies on; not checking it is not a pass."""
    assert _run(_doc())["W-04"].status is Status.NOT_RUN


def test_without_a_registry_w02_and_w03_do_not_run() -> None:
    results = _run(_doc())
    assert results["W-02"].status is Status.NOT_RUN
    assert results["W-03"].status is Status.NOT_RUN


# --------------------------------------------------------------------------
# An unreadable list is not an empty one.
# --------------------------------------------------------------------------

def test_a_malformed_withdrawal_list_raises_rather_than_reading_as_empty() -> None:
    """The failure this guards is the whole point of the artifact: 'I could not
    read the withdrawal list' rendering as 'nothing is withdrawn'."""
    with pytest.raises(WithdrawalsError):
        withdrawn_keys({"schema_version": "withdrawals/1.0"})


def test_an_absent_file_is_a_legitimate_empty() -> None:
    """Most repositories have never withdrawn anything."""
    assert withdrawn_keys(None) == set()


# --------------------------------------------------------------------------
# The claim table stops serving it.
# --------------------------------------------------------------------------

KEY = "stmt:9f4c1a20b7d3:bridgeland2007.lem-8.3"


def _declarations() -> dict:
    """A declarations artifact with one real citation binding.

    The shipped fixture carries no bindings, so a claim table built from it is
    empty and would make every assertion below vacuously true.
    """
    base = json.loads((VALID / "declarations-1.0.json").read_text(encoding="utf-8"))
    base["declarations"] = [{
        "name": "Topic.thm", "module": "Topic", "kind": "theorem",
        "is_internal": False, "statement_digest": "a" * 64, "local_deps": [],
        "axioms": [], "axioms_disallowed": [], "contains_sorry_ax": False,
        "local_axioms": [], "range": None,
        "cites": [{"key": KEY, "relation_claimed": "one_way",
                   "frontier": [], "note": None}],
    }]
    base["counts"] = {"total": 1, "in_scope": 1, "internal": 0, "cited": 1}
    return base


def _withdrawing(key: str) -> dict:
    doc = _doc()
    doc["withdrawals"] = [dict(doc["withdrawals"][0], key=key)]
    return doc


def test_a_withdrawn_key_is_marked_in_the_claim_table() -> None:
    decls = _declarations()
    rows = claim_table(decls)
    assert rows and not any(r.withdrawn for r in rows), "premise: nothing withdrawn"
    marked = claim_table(decls, withdrawals=_withdrawing(rows[0].key))
    assert marked[0].withdrawn is True


def test_withdrawal_does_not_erase_the_other_columns() -> None:
    """A reader must be able to see WHAT was withdrawn, not merely that
    something was — so every other cell keeps whatever it said."""
    decls = _declarations()
    plain = claim_table(decls)
    marked = claim_table(decls, withdrawals=_withdrawing(plain[0].key))
    assert marked[0].claimed == plain[0].claimed
    assert marked[0].frontier == plain[0].frontier
    assert marked[0].faithfulness == plain[0].faithfulness


def test_a_withdrawal_can_only_remove_trust() -> None:
    """The asymmetry that makes the forward channel safe.

    Reading a NEWER withdrawal list against an older pin can turn a served
    record into a withheld one, and can never do the reverse. There is
    deliberately no reinstatement field: restoring trust goes back through a
    new registry entry and a new human review, which is the path that carries
    a reviewer and a date.
    """
    schema = json.loads((HERE.parent / "mfc" / "schema" /
                         "withdrawals-1.0.schema.json").read_text(encoding="utf-8"))
    item = schema["properties"]["withdrawals"]["items"]["properties"]
    assert not any(k in item for k in ("reinstated", "reinstated_at", "active")), \
        "a reinstatement field would make this channel able to grant trust"

    decls = _declarations()
    assert sum(r.withdrawn for r in claim_table(decls)) == 0
    rows = claim_table(decls, withdrawals=_withdrawing(claim_table(decls)[0].key))
    assert sum(r.withdrawn for r in rows) == 1


# --------------------------------------------------------------------------
# The CLI.
# --------------------------------------------------------------------------

def test_the_cli_passes_the_fixture() -> None:
    assert main(["check-withdrawals", str(WITHDRAWALS),
                 "--registry", str(VALID / "registry-1.0.json")]) == EXIT_OK


def test_the_cli_fails_a_removed_withdrawal(tmp_path: Path) -> None:
    previous = tmp_path / "previous.json"
    previous.write_text(WITHDRAWALS.read_text(encoding="utf-8"), encoding="utf-8")
    now = _doc()
    now["withdrawals"] = []
    current = tmp_path / "now.json"
    current.write_text(json.dumps(now), encoding="utf-8")
    assert main(["check-withdrawals", str(current),
                 "--previous", str(previous)]) == EXIT_FINDINGS
