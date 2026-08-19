"""Tests for `mfc bundle`.

The claim under test is not "it produces output". It is **"nothing is carried
across from the emission"** — so every test here feeds `bundle` an emission
that asserts something false about itself and checks that the output disagrees.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.mfc.bundle import BundleError, build_declarations, compute_statement_digests
from contract.mfc.cli import EXIT_OK, EXIT_USAGE, main

HERE = Path(__file__).resolve().parent
VALID_DIR = HERE.parent / "testdata" / "artifacts" / "valid"


def _emission() -> dict:
    return json.loads((VALID_DIR / "emission-1.0.json").read_text(encoding="utf-8"))


def _environment() -> dict:
    return json.loads((VALID_DIR / "environment-1.0.json").read_text(encoding="utf-8"))


def _constant(name: str, **over) -> dict:
    base = {
        "name": name, "module": "M", "kind": "theorem",
        "is_instance": False, "is_internal": False, "is_private": False,
        "is_reducible": False, "num_levels": 0,
        "type_pp": "True", "value_pp": None,
        "local_deps": [], "scc_members": [], "axioms": [],
        "range": None, "cites": [],
    }
    base.update(over)
    return base


def _synthetic(constants: list[dict]) -> dict:
    em = _emission()
    em["constants"] = constants
    n = len(constants)
    em["counts"] = {"total": n, "in_scope": n, "internal": 0,
                    "with_range": 0, "instances": 0, "private": 0}
    return em


# --- nothing is carried across ------------------------------------------------

def test_counts_are_recounted_not_copied() -> None:
    """An emission that lies about its own counts must not be believed."""
    em = _synthetic([_constant("A"), _constant("B", is_internal=True)])
    em["counts"]["total"] = 999
    em["counts"]["internal"] = 999
    out = build_declarations(em, _environment(), VALID_DIR / "emission-1.0.json")
    assert out["counts"] == {"total": 2, "in_scope": 1, "internal": 1, "cited": 0}


def test_sorry_ax_cannot_be_laundered() -> None:
    """`contains_sorry_ax` is recomputed, so the emission cannot understate it.

    The emitter has no field to lie with here — it does not report
    `contains_sorry_ax` at all — but a hand-edited or third-party emission
    could omit the axiom from one place. The flag comes only from `axioms`.
    """
    em = _synthetic([_constant("Sneaky", axioms=["sorryAx", "propext"])])
    out = build_declarations(em, _environment(), VALID_DIR / "emission-1.0.json")
    d = out["declarations"][0]
    assert d["contains_sorry_ax"] is True
    assert "sorryAx" in d["axioms_disallowed"]


def test_axioms_disallowed_comes_from_the_environment_policy() -> None:
    """The emission never reports this field; it is computed against policy."""
    em = _synthetic([_constant("A", axioms=["propext", "Lean.ofReduceBool"])])
    out = build_declarations(em, _environment(), VALID_DIR / "emission-1.0.json")
    assert out["declarations"][0]["axioms_disallowed"] == ["Lean.ofReduceBool"]


def test_declared_axiom_additions_widen_the_policy() -> None:
    env = _environment()
    env["axiom_policy"]["additions"] = [
        {"axiom": "Lean.ofReduceBool", "reason": "native_decide, declared and argued"}]
    em = _synthetic([_constant("A", axioms=["propext", "Lean.ofReduceBool"])])
    out = build_declarations(em, env, VALID_DIR / "emission-1.0.json")
    assert out["declarations"][0]["axioms_disallowed"] == []


def test_axioms_are_re_sorted_and_deduped() -> None:
    em = _synthetic([_constant("A", axioms=["propext", "Quot.sound", "propext"])])
    out = build_declarations(em, _environment(), VALID_DIR / "emission-1.0.json")
    assert out["declarations"][0]["axioms"] == ["Quot.sound", "propext"]


# --- the Merkle order ---------------------------------------------------------

def test_dependencies_are_hashed_before_dependents() -> None:
    """Declaration order in the emission must not affect any digest."""
    a = _constant("A", kind="def", type_pp="Type", value_pp="Nat")
    b = _constant("B", type_pp="P A", local_deps=["A"])
    forward = compute_statement_digests(_synthetic([a, b]))
    reverse = compute_statement_digests(_synthetic([b, a]))
    assert forward == reverse


def test_missing_local_dep_is_an_error_not_a_guess() -> None:
    em = _synthetic([_constant("B", local_deps=["A"])])
    with pytest.raises(BundleError, match="not in the emission"):
        compute_statement_digests(em)


def test_undeclared_cycle_is_an_error() -> None:
    """A cycle with no `scc_members` would make digests traversal-dependent."""
    em = _synthetic([_constant("A", local_deps=["B"]), _constant("B", local_deps=["A"])])
    with pytest.raises(BundleError, match="dependency cycle"):
        compute_statement_digests(em)


def test_declared_scc_is_resolved_with_a_placeholder() -> None:
    em = _synthetic([
        _constant("A", local_deps=["B"], scc_members=["A", "B"]),
        _constant("B", local_deps=["A"], scc_members=["A", "B"])])
    digests = compute_statement_digests(em)
    assert set(digests) == {"A", "B"}


def test_self_reference_does_not_recurse() -> None:
    em = _synthetic([_constant("A", local_deps=["A"])])
    assert set(compute_statement_digests(em)) == {"A"}


# --- value_pp is honoured per kind -------------------------------------------

def test_value_pp_is_ignored_for_a_theorem() -> None:
    """Even if an emission supplies one, a proof is not part of a statement."""
    plain = _constant("T", type_pp="P")
    with_value = _constant("T", type_pp="P", value_pp="some proof term")
    assert (compute_statement_digests(_synthetic([plain]))["T"]
            == compute_statement_digests(_synthetic([with_value]))["T"])


def test_value_pp_is_used_for_a_def() -> None:
    one = _constant("D", kind="def", type_pp="Type", value_pp="Nat")
    two = _constant("D", kind="def", type_pp="Type", value_pp="Int")
    assert (compute_statement_digests(_synthetic([one]))["D"]
            != compute_statement_digests(_synthetic([two]))["D"])


# --- the CLI ------------------------------------------------------------------

def test_cli_writes_a_valid_artifact(tmp_path: Path) -> None:
    out = tmp_path / "declarations.json"
    rc = main(["bundle",
               "--emission", str(VALID_DIR / "emission-1.0.json"),
               "--environment", str(VALID_DIR / "environment-1.0.json"),
               "--out", str(out)])
    assert rc == EXIT_OK
    assert main(["validate", str(out)]) == EXIT_OK


def test_cli_rejects_a_wrong_input_kind(tmp_path: Path) -> None:
    """Passing build.json where an emission belongs must fail, not half-work."""
    rc = main(["bundle",
               "--emission", str(VALID_DIR / "build-1.0.json"),
               "--environment", str(VALID_DIR / "environment-1.0.json"),
               "--out", str(tmp_path / "d.json")])
    assert rc == EXIT_USAGE


def test_cli_validates_the_emission_before_using_it(tmp_path: Path) -> None:
    """A malformed emission fails rather than yielding well-formed nonsense."""
    em = _emission()
    em["constants"][0]["axioms"] = "not-a-list"
    bad = tmp_path / "bad-emission.json"
    bad.write_text(json.dumps(em), encoding="utf-8")
    rc = main(["bundle", "--emission", str(bad),
               "--environment", str(VALID_DIR / "environment-1.0.json"),
               "--out", str(tmp_path / "d.json")])
    assert rc != EXIT_OK
    assert not (tmp_path / "d.json").exists(), "no output on invalid input"


# --- the commit binding (derived-alg-geo-lean #628) ---------------------------

def test_source_commit_mismatch_is_refused() -> None:
    """An emission stamped at commit A must not bundle against commit B."""
    em = _synthetic([_constant("A")])
    em["source_git_commit"] = "a" * 40
    env = _environment()
    env["root_package"]["rev"] = "b" * 40
    with pytest.raises(BundleError, match="produced at commit"):
        build_declarations(em, env, VALID_DIR / "emission-1.0.json")


def test_source_commit_match_bundles() -> None:
    em = _synthetic([_constant("A")])
    em["source_git_commit"] = "c" * 40
    env = _environment()
    env["root_package"]["rev"] = "c" * 40
    out = build_declarations(em, env, VALID_DIR / "emission-1.0.json")
    assert out["counts"]["total"] == 1


def test_source_commit_absent_is_tolerated() -> None:
    """A pre-1.1.0 emission carries no stamp; there is nothing to compare."""
    em = _synthetic([_constant("A")])
    em.pop("source_git_commit", None)
    out = build_declarations(em, _environment(), VALID_DIR / "emission-1.0.json")
    assert out["counts"]["total"] == 1
