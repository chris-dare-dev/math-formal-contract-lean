"""`@[discharges]` and `E-12` — #168, red-team gap 7.

The design states its own rule precisely: *"two axes are distinct only if they
have distinct evidence-producing programs"*, and uses it to kill a competing
design's three-views-of-one-closure axes.

**Axis 6 broke that rule.** `frontier[].discharged_by` was hand-edited YAML
that `mfc` only *aggregated*. Nothing checked that the named discharger proved
anything — so a discharge that existed only in prose was indistinguishable from
a real one in every artifact.

What this closes, and what it does not, both matter:

* it does NOT check that the declaration proves the item. Nothing mechanical
  can — a frontier item is prose about a gap in the world;
* it DOES require the named declaration to exist and to claim, in code that had
  to compile, to discharge *this* id.

The end-to-end evidence is outside this file: the real emitter, run over the
test library, emitted `dischargesOne -> ["gltilde-universal-cover"]` and
`dischargesTwo -> ["first-gap", "second-gap"]` read back out of the `.olean`.
"""

from __future__ import annotations

import json
from pathlib import Path

from contract.mfc.rules import RULE_TITLES, Status, check

HERE = Path(__file__).resolve().parent
VALID = HERE.parent / "testdata" / "artifacts" / "valid"
ENV = json.loads((VALID / "environment-1.0.json").read_text(encoding="utf-8"))
ANCHOR = "MathFormalContractTest.dischargesOne"
ITEM_ID = "gltilde-universal-cover"


def _registry() -> dict:
    return json.loads((VALID / "registry-1.0.json").read_text(encoding="utf-8"))


def _constant(name: str, discharges: list[str]) -> dict:
    return {
        "name": name, "module": "Topic", "scope": "topic", "kind": "theorem",
        "is_instance": False, "is_internal": False, "is_private": False,
        "is_reducible": False, "num_levels": 0,
        "type_pp": "True", "value_pp": None, "doc": None,
        "local_deps": [], "scc_members": [], "discharges": discharges,
        "axioms": [], "range": None, "cites": [],
    }


def _emission(constants: list[dict]) -> dict:
    base = json.loads((VALID / "emission-1.0.json").read_text(encoding="utf-8"))
    base["constants"] = constants
    base["counts"] = {"total": len(constants), "in_scope": len(constants),
                      "internal": 0, "with_range": 0, "instances": 0,
                      "private": 0, "external": 0}
    return base


def _e12(constants: list[dict], registry: dict | None = None):
    registry = registry if registry is not None else _registry()
    results = {r.rule: r for r in check(_emission(constants), ENV, registry=registry)}
    return results["E-12"]


def test_an_anchored_discharge_passes() -> None:
    assert _e12([_constant(ANCHOR, [ITEM_ID])]).status is Status.PASS


def test_a_discharge_naming_a_declaration_that_does_not_exist_fails() -> None:
    """The commonest form: a rename, or a declaration that was never written."""
    result = _e12([_constant("Topic.somethingElse", [ITEM_ID])])
    assert result.status is Status.FAIL
    assert "not in the emission" in result.findings[0].detail


def test_a_declaration_that_never_claimed_the_discharge_fails() -> None:
    """The subtle form, and the one that was invisible before.

    The declaration exists and the registry says it discharges the item. The
    declaration itself has never said so — the edge lives only in the YAML.
    """
    result = _e12([_constant(ANCHOR, [])])
    assert result.status is Status.FAIL
    assert "@[discharges" in result.findings[0].detail


def test_discharging_a_different_id_is_not_discharging_this_one() -> None:
    result = _e12([_constant(ANCHOR, ["some-other-gap"])])
    assert result.status is Status.FAIL


def test_an_undischarged_frontier_is_not_a_finding() -> None:
    """Open work is normal. E-12 judges CLAIMS of discharge, not gaps."""
    registry = _registry()
    for entry in registry["entries"].values():
        for item in entry.get("frontier") or []:
            item["discharged_by"] = None
    assert _e12([_constant(ANCHOR, [])], registry).status is Status.PASS


def test_without_a_registry_the_rule_does_not_run() -> None:
    """The edges are in the registry and the anchors in the emission; with one
    side missing, nothing was checked — and that is not a pass."""
    results = {r.rule: r for r in check(_emission([_constant(ANCHOR, [ITEM_ID])]), ENV)}
    assert results["E-12"].status is Status.NOT_RUN
    assert results["E-12"].reason


def test_e12_is_in_the_rule_table() -> None:
    assert ("E-12", "every frontier discharge is anchored in the environment") \
        in RULE_TITLES


def test_the_schema_requires_the_declaration_anchor() -> None:
    """A `discharged_by` with a reviewer and a date but no declaration is the
    state #168 describes: attributed prose, still unanchored."""
    schema = json.loads((HERE.parent / "mfc" / "schema" /
                         "registry-1.0.schema.json").read_text(encoding="utf-8"))
    obj = schema["$defs"]["frontierItem"]["properties"]["discharged_by"]["oneOf"][1]
    assert "declaration" in obj["required"]
    # The reviewer-and-date half of gap 7 had already landed; this records that.
    assert {"discharged_at", "discharged_by_reviewer"} <= set(obj["required"])
