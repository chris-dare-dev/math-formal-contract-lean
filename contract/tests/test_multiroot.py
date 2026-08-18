"""Multi-root scoping and foreign-module exclusion — found by #156.

Both of these were found by pointing the finished checker at a real topic
repo's real emission, and neither was visible from the fixtures.

## Multi-root

The emitter takes `additionalRoots` so a monorepo can keep a thin combined
umbrella while sweeping each constituent library. `derived-alg-geo-lean` uses
it: `rootLib := DerivedAlgGeoSweep`, `additionalRoots := [DerivedAlgGeo]`.

`check-ilean-coverage` took ONE root, and both single-root answers are wrong in
opposite directions — measured on that repo's 15,346-constant emission:

* root = `DerivedAlgGeoSweep` alone → 1 in-scope module, 0 built declarations,
  and `I-05` fails on every constant the library declares;
* root = `DerivedAlgGeo` alone → `I-04` and `I-05` fail on the sweep module.

Neither is a defect in the repository being checked. Both are a single-root
question asked of a two-root emission.

## Foreign modules

The same run surfaced 8 constants whose `module` was `Mathlib.*` while
`modules[]` listed no Mathlib module at all — auto-generated equation and
congruence lemmas (`.congr_simp`, `.eq_1`) that Lean attributes to the module
owning their parent. The artifact contradicted itself, and `I-05` was right to
fail. The emitter now excludes them and counts them in `counts.foreign`, so the
exclusion is visible rather than a silent shrink.
"""

from __future__ import annotations

import json
from pathlib import Path

from contract.mfc.ilean import Module, coverage, roots_of
from contract.mfc.ilean import check as ilean_check
from contract.mfc.rules import Status

HERE = Path(__file__).resolve().parent
VALID = HERE.parent / "testdata" / "artifacts" / "valid"


def _constant(name: str, module: str) -> dict:
    return {
        "name": name, "module": module, "scope": "topic", "kind": "theorem",
        "is_instance": False, "is_internal": False, "is_private": False,
        "is_reducible": False, "num_levels": 0,
        "type_pp": "True", "value_pp": None, "doc": None,
        "local_deps": [], "scc_members": [], "discharges": [], "axioms": [],
        "range": None, "cites": [],
    }


def _emission(constants: list[dict], modules: list[str], root: str) -> dict:
    base = json.loads((VALID / "emission-1.0.json").read_text(encoding="utf-8"))
    base["root_lib"] = root
    base["constants"] = constants
    base["modules"] = modules
    n = len(constants)
    base["counts"] = {"total": n, "in_scope": n, "internal": 0, "with_range": 0,
                      "instances": 0, "private": 0, "external": 0, "foreign": 0}
    return base


def _mod(name: str, *decls: str) -> Module:
    return Module(name=name, path=Path(f"{name}.ilean"), decls=frozenset(decls))


#: The shape derived-alg-geo-lean actually emits.
def _two_root_repo():
    emission = _emission(
        [_constant("Topic.thm", "Topic.Lattice"),
         _constant("Sweep.probe", "TopicSweep")],
        modules=["Topic", "Topic.Lattice", "TopicSweep"],
        root="TopicSweep")
    modules = [_mod("Topic"), _mod("Topic.Lattice", "Topic.thm"),
               _mod("TopicSweep", "Sweep.probe")]
    return emission, modules


def test_roots_of_defaults_to_the_declared_root() -> None:
    assert roots_of({"root_lib": "Topic"}) == ["Topic"]


def test_roots_of_accepts_one_or_many() -> None:
    assert roots_of({"root_lib": "X"}, "Topic") == ["Topic"]
    assert roots_of({"root_lib": "X"}, ["A", "B"]) == ["A", "B"]


def test_a_single_root_makes_the_coverage_gate_VACUOUS() -> None:
    """The real defect, and it is worse than a false failure.

    Scoped to the sweep module alone, `I-03` compares the emission against the
    declarations of ONE module that declares nothing — so it passes, having
    checked nothing. Measured on derived-alg-geo-lean: "1 in-scope module of
    488 built; 0 built declarations", green. With both roots the same run
    compares 6,169 built declarations.

    A gate that passes because it looked at nothing is precisely the failure
    `ilean.py` exists to prevent, one level up from where it was watching.
    """
    emission, modules = _two_root_repo()
    statuses = {r.rule: r.status for r in ilean_check(emission, modules)}
    assert statuses["I-03"] is Status.PASS, "it passes..."
    assert coverage(emission, modules).built_declarations == 1, \
        "...having compared only the sweep module's declarations"

    both = coverage(emission, modules, lib=["TopicSweep", "Topic"])
    assert both.built_declarations == 2, "both roots compare the real set"


def test_both_roots_together_are_clean() -> None:
    emission, modules = _two_root_repo()
    statuses = {r.rule: r.status
                for r in ilean_check(emission, modules, lib=["TopicSweep", "Topic"])}
    assert not [r for r, s in statuses.items() if s is Status.FAIL], statuses


def test_coverage_counts_both_roots() -> None:
    emission, modules = _two_root_repo()
    c = coverage(emission, modules, lib=["TopicSweep", "Topic"])
    assert c.in_scope_modules == 3
    assert c.missing == 0


def test_a_foreign_module_constant_still_fails_i05() -> None:
    """The emitter excludes these now, but the rule must keep catching them.

    A fix in the producer is not a reason to stop checking in the consumer —
    that is how a fixed bug becomes an unchecked assumption.
    """
    emission, modules = _two_root_repo()
    emission["constants"].append(
        _constant("CategoryTheory.Iso.symm.eq_1",
                  "Mathlib.CategoryTheory.Localization.Adjunction"))
    emission["counts"]["total"] += 1
    emission["counts"]["in_scope"] += 1
    statuses = {r.rule: r.status
                for r in ilean_check(emission, modules, lib=["TopicSweep", "Topic"])}
    assert statuses["I-05"] is Status.FAIL


def test_counts_carries_foreign() -> None:
    """Counted, never silently dropped."""
    schema = json.loads((HERE.parent / "mfc" / "schema" /
                         "emission-1.0.schema.json").read_text(encoding="utf-8"))
    counts = schema["properties"]["counts"]
    assert "foreign" in counts["properties"]
    assert "foreign" in counts["required"]
