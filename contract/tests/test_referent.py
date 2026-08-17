"""`R-10` and `interface_ratio` — #167, red-team gap 9.

The failure this closes is not a wrong value; it is a **green record about a
private fiction**. At near-zero Mathlib coverage a topic repo consists entirely
of self-declared interface structures. `closed_lanes` forbids nothing useful
because there is nothing in Mathlib to forbid, every entry is `one_way` or
`no_claim` with a hand-written `interface` frontier, and `statement_digest`
then faithfully detects drift in structures nobody outside the repository has
ever seen — while axes 1–4 and 6 all pass, greenly.

Bridgeland hides it, which is why nobody noticed: its upstream anchor supplies
real `Slicing` and `PreStabilityCondition` definitions, so `relation_claimed`
has something external to relate to.

An interface must now either name that something or say out loud that it has
none. `no_referent: true` is a legitimate answer — genuinely novel mathematics
models nothing — and it is legitimate *because* it is stated.
"""

from __future__ import annotations

import json
from pathlib import Path

from contract.mfc.cli import EXIT_FINDINGS, EXIT_OK, main
from contract.mfc.rules import Status
from contract.mfc.rules_registry import check, interface_ratio

HERE = Path(__file__).resolve().parent
VALID = HERE.parent / "testdata" / "artifacts" / "valid" / "registry-1.0.json"
BAD = HERE.parent / "testdata" / "registries" / "invalid"
LABELS = ["mathlib-gap"]


def _valid() -> dict:
    return json.loads(VALID.read_text(encoding="utf-8"))


def _r10(doc: dict):
    return {r.rule: r for r in check(doc, frontier_kind_labels=LABELS)}["R-10"]


def _item(**over) -> dict:
    item = {"id": "iface", "kind_class": "interface", "kind_label": "mathlib-gap",
            "statement": "An interface structure.", "discharged_by": None}
    item.update(over)
    return item


def _with_frontier(items: list[dict]) -> dict:
    doc = _valid()
    key = next(iter(doc["entries"]))
    doc["entries"][key]["frontier"] = items
    return doc


# --------------------------------------------------------------------------
# The rule.
# --------------------------------------------------------------------------

def test_an_interface_naming_a_referent_passes() -> None:
    doc = _with_frontier([_item(referent="CategoryTheory.Triangulated.Slicing")])
    assert _r10(doc).status is Status.PASS


def test_an_interface_naming_nothing_fails() -> None:
    result = _r10(_with_frontier([_item()]))
    assert result.status is Status.FAIL
    assert "names no referent" in result.findings[0].detail


def test_an_admitted_absence_passes_when_it_gives_a_reason() -> None:
    """`no_referent: true` is legitimate. Novel mathematics models nothing."""
    doc = _with_frontier([_item(no_referent=True,
                                referent_note="Genuinely novel; no analogue exists.")])
    assert _r10(doc).status is Status.PASS


def test_an_admission_with_no_reason_fails() -> None:
    result = _r10(_with_frontier([_item(no_referent=True)]))
    assert result.status is Status.FAIL
    assert "empty field again" in result.findings[0].detail


def test_a_blank_reason_is_not_a_reason() -> None:
    result = _r10(_with_frontier([_item(no_referent=True, referent_note="   ")]))
    assert result.status is Status.FAIL


def test_a_blank_referent_is_not_a_referent() -> None:
    result = _r10(_with_frontier([_item(referent="   ")]))
    assert result.status is Status.FAIL


def test_other_kind_classes_are_not_judged() -> None:
    """`missing-library` is a gap in Mathlib, not a claim about a structure."""
    doc = _with_frontier([_item(kind_class="missing-library", id="gap")])
    assert _r10(doc).status is Status.PASS


# --------------------------------------------------------------------------
# The schema rejects both shapes before the rule ever runs.
# --------------------------------------------------------------------------

def test_the_schema_rejects_an_interface_without_a_referent() -> None:
    assert main(["validate", str(BAD / "interface-without-referent.json")]) \
        == EXIT_FINDINGS


def test_the_schema_rejects_an_admission_without_a_note() -> None:
    assert main(["validate", str(BAD / "no-referent-without-note.json")]) \
        == EXIT_FINDINGS


def test_the_valid_registry_still_validates() -> None:
    """It now carries BOTH legal shapes, so the conditional is exercised in the
    passing direction rather than only the failing one."""
    assert main(["validate", str(VALID)]) == EXIT_OK
    items = [i for e in _valid()["entries"].values() for i in e.get("frontier") or []]
    interfaces = [i for i in items if i["kind_class"] == "interface"]
    assert any(i.get("referent") for i in interfaces)
    assert any(i.get("no_referent") for i in interfaces)


# --------------------------------------------------------------------------
# The ratio.
# --------------------------------------------------------------------------

def test_interface_ratio_counts_interfaces_over_all_frontier_items() -> None:
    doc = _with_frontier([_item(referent="X"), _item(id="b", kind_class="open-problem"),
                          _item(id="c", kind_class="missing-library")])
    # The other entries in the fixture carry frontier items too.
    interfaces, total = interface_ratio(doc["entries"])
    assert interfaces >= 1 and total >= 3 and interfaces <= total


def test_interface_ratio_is_reported_not_thresholded() -> None:
    """There is no fraction of interfaces that is *wrong*.

    A young topic at low Mathlib coverage is supposed to be mostly interfaces.
    A rule that failed above some fraction would be inventing a policy the
    contract has no basis for — so the figure is printed and the rule table is
    left alone.
    """
    doc = _with_frontier([_item(referent="X"), _item(id="b", referent="Y")])
    results = {r.rule: r for r in check(doc, frontier_kind_labels=LABELS)}
    assert results["R-10"].status is Status.PASS
    assert not any(r.rule.startswith("R-1") and r.status is Status.FAIL
                   for r in results.values())


def test_a_registry_with_no_frontier_items_reports_no_ratio() -> None:
    """Zero over zero is not zero percent, and must not be printed as one."""
    doc = _with_frontier([])
    for e in doc["entries"].values():
        e["frontier"] = []
    assert interface_ratio(doc["entries"]) == (0, 0)
