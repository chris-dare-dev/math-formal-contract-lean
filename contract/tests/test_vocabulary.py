"""`E-11`, the forbidden-vocabulary lint — the §3 half of #165.

ADR-0005 records that the architecture claimed to mechanise `CLAUDE.md` §3 and
did not:

    abbrev NumLattice : Type := Fin 2 → ℤ

imports nothing forbidden, reaches no closed lane, and passes `E-01..E-10`. A
doc-comment calling it `K_num(Ku(X))` is the entire false claim, and no
artifact carried that text until the emitter grew a `doc` field.

`testdata/emissions/invalid/vocabulary-overclaim.json` is that exact example,
and `test_the_adr_0005_example_is_caught` is the test that would have failed
before this rule existed.

The scoping is the subtle part and most of these tests are about it: `E-11`
fires on vocabulary **plus an open frontier**, never on vocabulary alone. A
declaration that genuinely has Chern classes may say so; the one that says so
while its own citation records unfinished supporting work is overclaiming.
"""

from __future__ import annotations

import json
from pathlib import Path

from contract.mfc.rules import RULE_TITLES, Status, check

HERE = Path(__file__).resolve().parent
VALID = HERE.parent / "testdata" / "artifacts" / "valid"
BAD = HERE.parent / "testdata" / "emissions" / "invalid"
LANES = json.loads((HERE.parent / "testdata" / "closed-lanes.json")
                   .read_text(encoding="utf-8"))["closed_lanes"]

ENV = json.loads((VALID / "environment-1.0.json").read_text(encoding="utf-8"))


def _e11(emission: dict, lanes=LANES):
    results = {r.rule: r for r in check(emission, ENV, closed_lanes=lanes)}
    return results["E-11"]


def _constant(**over) -> dict:
    base = {
        "name": "Topic.Thing", "module": "Topic", "kind": "def",
        "is_instance": False, "is_internal": False, "is_private": False,
        "is_reducible": False, "num_levels": 0,
        "type_pp": "Type", "value_pp": "Fin 2 → ℤ", "doc": None,
        "local_deps": [], "scc_members": [], "axioms": [], "range": None,
        "cites": [],
    }
    base.update(over)
    return base


def _emission(constants: list[dict]) -> dict:
    return {
        "schema_version": "emission/1.0", "root_lib": "Topic",
        "modules": ["Topic"], "constants": constants,
        "counts": {"total": len(constants), "in_scope": len(constants),
                   "internal": 0, "with_range": 0, "instances": 0, "private": 0,
                   "external": 0, "foreign": 0},
    }


def _cite(frontier: list[str], relation: str = "specialization") -> dict:
    return {"key": "stmt:0123456789ab:x", "relation_claimed": relation,
            "frontier": frontier, "note": None}


# --------------------------------------------------------------------------
# The example ADR-0005 is written about.
# --------------------------------------------------------------------------

def test_the_adr_0005_example_is_caught() -> None:
    emission = json.loads((BAD / "vocabulary-overclaim.json")
                          .read_text(encoding="utf-8"))
    result = _e11(emission)
    assert result.status is Status.FAIL
    assert "Kuznetsov" in result.findings[0].detail
    assert result.findings[0].where == "Topic.Lattice.NumLattice"


def test_that_example_passes_every_other_rule() -> None:
    """The point of the gap: it was not that §3 was weakly enforced."""
    emission = json.loads((BAD / "vocabulary-overclaim.json")
                          .read_text(encoding="utf-8"))
    others = [r for r in check(emission, ENV, closed_lanes=LANES)
              if r.rule != "E-11"]
    assert not [r for r in others if r.status is Status.FAIL], \
        [r.rule for r in others if r.status is Status.FAIL]


# --------------------------------------------------------------------------
# Vocabulary alone is not a finding. The pairing is.
# --------------------------------------------------------------------------

def test_vocabulary_without_an_open_frontier_is_allowed() -> None:
    """A declaration that genuinely has the mathematics may name it."""
    c = _constant(doc="The Chern character of a coherent sheaf.",
                  cites=[_cite([], relation="exact")])
    assert _e11(_emission([c])).status is Status.PASS


def test_an_open_frontier_without_vocabulary_is_allowed() -> None:
    """Unfinished work is normal; that is what a frontier is for."""
    c = _constant(doc="A rank-two lattice.", cites=[_cite(["some-gap"])])
    assert _e11(_emission([c])).status is Status.PASS


def test_a_declaration_with_no_citation_at_all_is_not_judged() -> None:
    """No citation means no claim about a paper statement to overclaim."""
    c = _constant(doc="The Kuznetsov component.", cites=[])
    assert _e11(_emission([c])).status is Status.PASS


# --------------------------------------------------------------------------
# Both surfaces the issue names.
# --------------------------------------------------------------------------

def test_the_name_is_linted() -> None:
    c = _constant(name="Topic.KuznetsovComponent", cites=[_cite(["gap"])])
    result = _e11(_emission([c]))
    assert result.status is Status.FAIL and "name" in result.findings[0].detail


def test_the_doc_comment_is_linted() -> None:
    c = _constant(doc="The Kuznetsov component, as a lattice.",
                  cites=[_cite(["gap"])])
    result = _e11(_emission([c]))
    assert result.status is Status.FAIL
    assert "doc-comment" in result.findings[0].detail


def test_matching_is_case_insensitive() -> None:
    """Doc-comments are prose. `kuznetsov` is the same claim as `Kuznetsov`."""
    c = _constant(doc="a kuznetsov component", cites=[_cite(["gap"])])
    assert _e11(_emission([c])).status is Status.FAIL


def test_a_null_doc_is_not_a_crash_and_not_a_match() -> None:
    c = _constant(doc=None, cites=[_cite(["gap"])])
    assert _e11(_emission([c])).status is Status.PASS


# --------------------------------------------------------------------------
# not_run, in the two situations that differ.
# --------------------------------------------------------------------------

def test_no_configuration_is_not_run() -> None:
    c = _constant(doc="Kuznetsov", cites=[_cite(["gap"])])
    results = {r.rule: r for r in check(_emission([c]), ENV)}
    assert results["E-11"].status is Status.NOT_RUN


def test_lanes_without_vocabulary_are_not_run_not_pass() -> None:
    """A topic that configured lanes but no vocabulary has NOT mechanised §3.

    Reporting `pass` there would say "nothing overclaims" on the strength of a
    list nobody wrote.
    """
    lanes = [{"name": "geometry", "forbidden_module_prefixes": ["Mathlib.X."]}]
    c = _constant(doc="Kuznetsov", cites=[_cite(["gap"])])
    result = _e11(_emission([c]), lanes)
    assert result.status is Status.NOT_RUN
    assert "forbidden_vocabulary" in result.reason


def test_e11_is_in_the_rule_table() -> None:
    """The bootstrap path reports from RULE_TITLES; a rule missing there is
    invisible rather than `not_run`."""
    assert ("E-11", "no unfinished declaration claims forbidden vocabulary") \
        in RULE_TITLES


def test_the_table_still_matches_an_ordinary_run() -> None:
    emission = json.loads((VALID / "emission-1.0.json").read_text(encoding="utf-8"))
    ordinary = check(emission, ENV)
    assert [(r.rule, r.title) for r in ordinary] == list(RULE_TITLES)
