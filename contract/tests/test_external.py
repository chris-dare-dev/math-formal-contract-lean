"""`external_decls[]` — #166, red-team gap 10.

Emission is module-scoped to the topic library, correctly (#142), which means
`@[cites]` can never be attached to `Mathlib.…`. For a high-coverage topic the
natural target of a paper's Lemma 3.7 is frequently **already in Mathlib** — so
the adopter restates it, and then the restatement's `statement_digest`, axiom
closure and kernel replay describe *the wrapper*. The wrapper's faithfulness to
the paper is a different question from Mathlib's, and nothing in the record
says which one is being attested.

Mathlib's own `docs/1000.yaml` solves this in one line (`decl: <name>`).

Without it the architecture is useful only in the middle of the coverage
range — which is exactly where Bridgeland sits, and exactly why nobody
noticed.

The end-to-end proof is not here: it is that the real emitter, run against real
`Init` constants with `--externals`, produced `scope: external` rows carrying
real axiom closures which validate against the schema. These tests pin the
properties that make those rows safe.
"""

from __future__ import annotations

import json
from pathlib import Path

from contract.mfc.cli import EXIT_OK, main
from contract.mfc.ilean import Module, coverage
from contract.mfc.ilean import check as ilean_check
from contract.mfc.rules import Status
from contract.mfc.rules_registry import check as registry_check
from contract.mfc.rules_registry import external_decls

HERE = Path(__file__).resolve().parent
VALID = HERE.parent / "testdata" / "artifacts" / "valid"
REGISTRY = VALID / "registry-1.0.json"


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _constant(name: str, module: str, scope: str = "topic") -> dict:
    return {
        "name": name, "module": module, "scope": scope, "kind": "theorem",
        "is_instance": False, "is_internal": False, "is_private": False,
        "is_reducible": False, "num_levels": 0,
        "type_pp": "True", "value_pp": None, "doc": None,
        "local_deps": [], "scc_members": [], "axioms": [], "range": None,
        "cites": [],
    }


def _emission(constants: list[dict], modules: list[str]) -> dict:
    base = json.loads((VALID / "emission-1.0.json").read_text(encoding="utf-8"))
    base["root_lib"] = "Topic"
    base["constants"] = constants
    base["modules"] = modules
    topic = [c for c in constants if c["scope"] == "topic"]
    base["counts"] = {
        "total": len(constants), "in_scope": len(topic), "internal": 0,
        "with_range": 0, "instances": 0, "private": 0,
        "external": len(constants) - len(topic), "foreign": 0}
    return base


# --------------------------------------------------------------------------
# The registry side.
# --------------------------------------------------------------------------

def test_the_registry_fixture_binds_an_external_constant() -> None:
    bindings = external_decls(_registry()["entries"])
    assert [b["name"] for b in bindings] == ["List.length_append"]
    assert bindings[0]["cited_by"], "a binding nobody cites is not evidence"


def test_a_binding_pins_the_environment_it_was_made_against() -> None:
    """A constant NAME does not identify a statement.

    Mathlib restates and generalises; the same name at a later rev can be a
    different theorem. Without `env_digest` the binding silently follows
    whichever one is current.
    """
    binding = external_decls(_registry()["entries"])[0]
    assert len(binding["env_digest"]) == 64


def test_the_extraction_command_writes_a_plain_name_list(tmp_path: Path) -> None:
    """What the emitter reads. A list of strings and nothing else."""
    out = tmp_path / "ext.json"
    assert main(["registry", "external-decls", str(REGISTRY), "--out", str(out)]) \
        == EXIT_OK
    assert json.loads(out.read_text(encoding="utf-8")) == ["List.length_append"]


def test_r11_fails_when_bindings_disagree_about_the_environment() -> None:
    doc = _registry()
    keys = list(doc["entries"])
    doc["entries"][keys[1]]["external_decls"] = [
        {"name": "Nat.succ_le_of_lt", "env_digest": "b" * 64, "note": None}]
    results = {r.rule: r for r in registry_check(doc, frontier_kind_labels=["mathlib-gap"])}
    assert results["R-11"].status is Status.FAIL


def test_r11_passes_when_they_agree() -> None:
    doc = _registry()
    keys = list(doc["entries"])
    digest = doc["entries"][keys[0]]["external_decls"][0]["env_digest"]
    doc["entries"][keys[1]]["external_decls"] = [
        {"name": "Nat.succ_le_of_lt", "env_digest": digest, "note": None}]
    results = {r.rule: r for r in registry_check(doc, frontier_kind_labels=["mathlib-gap"])}
    assert results["R-11"].status is Status.PASS


# --------------------------------------------------------------------------
# What makes an external row safe: it cannot raise this repo's numbers.
# --------------------------------------------------------------------------

def test_an_external_row_does_not_count_as_coverage() -> None:
    """The whole safety argument in one assertion.

    If `external` counted toward `in_scope`, a topic could raise its own
    formalization numbers by citing Mathlib — the vacuous pass with extra
    steps.
    """
    emission = _emission(
        [_constant("Topic.thm", "Topic"),
         _constant("List.length_append", "Init.Data.List.Basic", "external")],
        modules=["Topic"])
    assert emission["counts"]["in_scope"] == 1
    assert emission["counts"]["external"] == 1

    modules = [Module(name="Topic", path=Path("Topic.ilean"),
                      decls=frozenset({"Topic.thm"}))]
    c = coverage(emission, modules)
    assert c.emitted_constants == 1, "external rows are not coverage"
    assert c.missing == 0


def test_an_external_module_is_not_reported_as_out_of_scope() -> None:
    """`I-04` compares the emission's modules against the built tree.

    Without the exclusion it would report `Init.Data.List.Basic` as a module
    the emitter swept but Lake never built for this repo — a finding about the
    emitter doing exactly what it was told.
    """
    emission = _emission(
        [_constant("Topic.thm", "Topic"),
         _constant("List.length_append", "Init.Data.List.Basic", "external")],
        modules=["Topic"])
    modules = [Module(name="Topic", path=Path("Topic.ilean"),
                      decls=frozenset({"Topic.thm"}))]
    statuses = {r.rule: r.status for r in ilean_check(emission, modules)}
    assert not [r for r, s in statuses.items() if s is Status.FAIL], statuses


def test_a_repo_of_only_external_rows_does_not_validate() -> None:
    """`in_scope: minimum 1` still bites.

    A repository that has formalized nothing and cites ten Mathlib theorems has
    formalized nothing, and the vacuous-pass guard must still say so.
    """
    emission = _emission(
        [_constant("List.length_append", "Init.Data.List.Basic", "external")],
        modules=["Topic"])
    assert emission["counts"]["in_scope"] == 0
    schema = json.loads((HERE.parent / "mfc" / "schema" /
                         "emission-1.0.schema.json").read_text(encoding="utf-8"))
    import jsonschema
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(emission))
    assert any("in_scope" in str(e.json_path) for e in errors), \
        "an emission with zero in-scope constants must not validate"
