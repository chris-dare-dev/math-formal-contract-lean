"""Tests for `mfc conformance` — the C-01..C-12 cross-artifact rules.

## Why the fixture tree is built rather than checked in

Every other fixture corpus here is checked in. This one cannot be: `C-02`
compares a predicate's `sha256` to the file's actual bytes, so a checked-in
`attest/` tree would need its digests hand-maintained on every edit, and the
first time someone forgot, the fixture would fail for a reason having nothing
to do with the rule under test.

The cost of building it is the obvious one — a generator and a checker that
share a misconception agree with each other. That is what the mutation tests
below are for: each takes the *coherent* tree, breaks exactly one link, and
requires the matching rule to notice. A rule that no mutation can trip is
indistinguishable from a rule that is not wired up.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.mfc.cli import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, main
from contract.mfc.conformance import (
    KNOWN_PREDICATE_TYPES,
    PREDICATE_NS,
    REQUIRED_PREDICATE_TYPES,
    Status,
    check,
    evidence_table,
    gather,
)
from contract.mfc.digest import file_digest

HERE = Path(__file__).resolve().parent
VALID_DIR = HERE.parent / "testdata" / "artifacts" / "valid"

#: predicate short name -> (fixture file, path inside the tree)
LAYOUT = {
    "environment": ("environment-1.0.json", "attest/environment.json"),
    "declarations": ("declarations-1.0.json", "attest/declarations.json"),
    "build": ("build-1.0.json", "attest/build.json"),
    "human-review": ("review-1.0.json", "attest/review.json"),
    "corpus-resolution": ("resolution-1.0.json", "attest/resolution.json"),
}


def _fixture(name: str) -> dict:
    return json.loads((VALID_DIR / name).read_text(encoding="utf-8"))


def coherent_tree(root: Path) -> Path:
    """Write an `attest/` tree in which every cross-artifact link holds.

    Derived from the checked-in per-schema fixtures, with the *linking* values
    — the ones a real pipeline computes rather than authors — recomputed here:
    each predicate's `sha256`, `declarations.emission_sha256`, and the shared
    `registry_sha256`. Everything a human would write stays as authored.
    """
    bundle = _fixture("bundle-1.0.json")
    (root / "attest").mkdir(parents=True, exist_ok=True)

    # One registry digest, agreed by both sides (C-08).
    registry_sha = "e" * 64
    bundle["registry_sha256"] = registry_sha

    # The emission is an input to the bundle, not a predicate in it (C-11).
    emission_path = root / "attest" / "lean-emission.json"
    emission_path.write_text(
        (VALID_DIR / "emission-1.0.json").read_text(encoding="utf-8"), encoding="utf-8")

    for kind, (fixture_name, rel) in LAYOUT.items():
        doc = _fixture(fixture_name)
        if kind == "corpus-resolution":
            doc["registry_sha256"] = registry_sha
        if kind == "declarations":
            doc["emission_sha256"] = file_digest(emission_path)
        (root / rel).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    # The provisional predicate wraps another system's format on purpose, so
    # this contract has no schema for it -- any JSON will do.
    (root / "attest" / "lean-verify-transcript.json").write_text(
        json.dumps({"tool": "arxmcp/lean_verify", "toolchain": "v4.31.0"}) + "\n",
        encoding="utf-8")

    for pred in bundle["predicates"]:
        short = pred["predicateType"].rstrip("/").split("/")[-2]
        if short in LAYOUT:
            pred["file"] = LAYOUT[short][1]
        pred["sha256"] = file_digest(root / pred["file"])

    bundle_path = root / "attest" / "bundle.json"
    bundle_path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    return bundle_path


def _run(bundle_path: Path, **kw) -> dict[str, Status]:
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    root = bundle_path.parent.parent
    results = check(bundle, gather(bundle, root), **kw)
    return {r.rule: r.status for r in results}


def _edit(bundle_path: Path, mutate) -> None:
    """Mutate the bundle in place.

    Note what this does NOT do: it never re-stamps anything the mutation
    invalidated. A mutation test that repaired the links it broke would be
    testing nothing. Where a test needs the *other* links intact — because it is
    aiming at one specific rule — it re-stamps explicitly and visibly.
    """
    doc = json.loads(bundle_path.read_text(encoding="utf-8"))
    mutate(doc)
    bundle_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def _edit_artifact(root: Path, rel: str, mutate) -> None:
    p = root / rel
    doc = json.loads(p.read_text(encoding="utf-8"))
    mutate(doc)
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    return coherent_tree(tmp_path)


# --- the coherent tree ---------------------------------------------------------

def test_the_coherent_tree_passes_every_runnable_rule(tree: Path) -> None:
    statuses = _run(tree, emission_path=tree.parent / "lean-emission.json")
    failed = [r for r, s in statuses.items() if s is Status.FAIL]
    assert not failed, f"coherent tree failed {failed}"


def test_every_rule_runs_when_every_input_is_present(tree: Path) -> None:
    """No rule may be permanently `not_run` — that would hide it forever."""
    statuses = _run(tree, emission_path=tree.parent / "lean-emission.json")
    assert not [r for r, s in statuses.items() if s is Status.NOT_RUN]
    assert len(statuses) == 12


# --- one mutation per rule -----------------------------------------------------

def test_c01_a_named_file_that_does_not_exist(tree: Path) -> None:
    (tree.parent / "build.json").unlink()
    assert _run(tree)["C-01"] is Status.FAIL


def test_c02_a_file_edited_after_the_bundle_was_built(tree: Path) -> None:
    """The single most important rule: without it every other link is hearsay."""
    _edit_artifact(tree.parent.parent, "attest/build.json",
                   lambda d: d.update(lake_build_jobs=9999))
    assert _run(tree)["C-02"] is Status.FAIL


def test_c03_a_payload_that_does_not_match_its_own_schema(tree: Path) -> None:
    root = tree.parent.parent
    _edit_artifact(root, "attest/build.json", lambda d: d.update(error_count="lots"))
    _edit(tree, lambda b: [p.update(sha256=file_digest(root / p["file"]))
                           for p in b["predicates"]])
    assert _run(tree)["C-03"] is Status.FAIL


def test_c03_catches_a_predicate_pointing_at_the_wrong_artifact(tree: Path) -> None:
    """A real file with a correct digest can still be the wrong file.

    C-01 and C-02 both pass here, which is the point: they check that the bytes
    are what the bundle says, not that they are what the bundle *means*.
    """
    root = tree.parent.parent
    _edit(tree, lambda b: [p.update(file="attest/environment.json",
                                    sha256=file_digest(root / "attest/environment.json"))
                           for p in b["predicates"]
                           if p["predicateType"].endswith("/build/v1")])
    statuses = _run(tree)
    assert statuses["C-01"] is Status.PASS
    assert statuses["C-02"] is Status.PASS
    assert statuses["C-03"] is Status.FAIL


def test_c04_an_unknown_predicate_left_in_predicates(tree: Path) -> None:
    _edit(tree, lambda b: b["predicates"][0].update(
        predicateType="https://example.invalid/predicate/who-knows/v1"))
    assert _run(tree)["C-04"] is Status.FAIL


def test_c05_a_build_measured_in_another_environment(tree: Path) -> None:
    """A `build/v1` carrying a foreign env_digest is another build's result."""
    _edit(tree, lambda b: [p.update(env_digest="a" * 64) for p in b["predicates"]
                           if p["predicateType"].endswith("/build/v1")])
    assert _run(tree)["C-05"] is Status.FAIL


def test_c05_permits_a_provisional_predicate_from_another_environment(tree: Path) -> None:
    """The reference bundle's v4.31.0 transcript against a v4.29.0 repo.

    Legitimate and it must stay legitimate — the rule is about labelling, not
    about forbidding foreign evidence.
    """
    statuses = _run(tree)
    assert statuses["C-05"] is Status.PASS
    rows = {r.kind: r for r in evidence_table(
        json.loads(tree.read_text(encoding="utf-8")),
        gather(json.loads(tree.read_text(encoding="utf-8")), tree.parent.parent))}
    assert rows["provisional-self-reported"].environment.startswith("OTHER")


def test_c05_rejects_a_null_env_digest_on_a_lean_measurement(tree: Path) -> None:
    _edit(tree, lambda b: [p.update(env_digest=None) for p in b["predicates"]
                           if p["predicateType"].endswith("/declarations/v1")])
    assert _run(tree)["C-05"] is Status.FAIL


def test_c06_the_bundle_mislabels_an_artifacts_environment(tree: Path) -> None:
    root = tree.parent.parent
    _edit_artifact(root, "attest/build.json", lambda d: d.update(env_digest="b" * 64))
    _edit(tree, lambda b: [p.update(sha256=file_digest(root / p["file"]))
                           for p in b["predicates"]])
    assert _run(tree)["C-06"] is Status.FAIL


def test_c07_an_environment_digest_that_does_not_recompute(tree: Path) -> None:
    """The one rule that checks a digest against the data it summarizes.

    Everything else here compares self-asserted digests to each other, which a
    consistently-wrong producer would satisfy.
    """
    root = tree.parent.parent
    _edit_artifact(root, "attest/environment.json",
                   lambda d: d["packages"].pop())
    _edit(tree, lambda b: [p.update(sha256=file_digest(root / p["file"]))
                           for p in b["predicates"]])
    assert _run(tree)["C-07"] is Status.FAIL


def test_c08_the_resolution_used_a_different_registry(tree: Path) -> None:
    root = tree.parent.parent
    _edit_artifact(root, "attest/resolution.json",
                   lambda d: d.update(registry_sha256="c" * 64))
    _edit(tree, lambda b: [p.update(sha256=file_digest(root / p["file"]))
                           for p in b["predicates"]])
    assert _run(tree)["C-08"] is Status.FAIL


def test_c09_the_bundle_attests_a_different_commit(tree: Path) -> None:
    _edit(tree, lambda b: b["subject"][0]["digest"].update(gitCommit="0" * 40))
    assert _run(tree)["C-09"] is Status.FAIL


def test_c10_a_review_carried_over_from_an_earlier_environment(tree: Path) -> None:
    """Re-dating a stale review is the one thing a human review must not do."""
    root = tree.parent.parent
    _edit_artifact(root, "attest/review.json",
                   lambda d: d["reviews"][0].update(reviewed_env_digest="d" * 64))
    _edit(tree, lambda b: [p.update(sha256=file_digest(root / p["file"]))
                           for p in b["predicates"]])
    assert _run(tree)["C-10"] is Status.FAIL


def test_c11_declarations_derived_from_a_different_emission(tree: Path) -> None:
    root = tree.parent.parent
    _edit_artifact(root, "attest/declarations.json",
                   lambda d: d.update(emission_sha256="f" * 64))
    _edit(tree, lambda b: [p.update(sha256=file_digest(root / p["file"]))
                           for p in b["predicates"]])
    assert _run(tree, emission_path=root / "attest" / "lean-emission.json")["C-11"] \
        is Status.FAIL


def test_c12_a_bundle_that_simply_omits_its_build_predicate(tree: Path) -> None:
    """The vacuous pass in bundle form.

    Every other rule checks what is present, so dropping the build predicate
    entirely satisfies all eleven of them and reports "5 predicate(s)". Only a
    rule that knows what MUST be there can see it.
    """
    _edit(tree, lambda b: b.__setitem__("predicates", [
        p for p in b["predicates"] if not p["predicateType"].endswith("/build/v1")]))
    statuses = _run(tree, emission_path=tree.parent / "lean-emission.json")
    assert statuses["C-12"] is Status.FAIL
    assert not [r for r, s in statuses.items() if s is Status.FAIL and r != "C-12"], (
        "no other rule notices the omission -- which is why C-12 exists")


def test_c12_does_not_require_an_optional_predicate(tree: Path) -> None:
    """A repo with no reviews yet is ordinary; C-10 already reports not_run."""
    _edit(tree, lambda b: b.__setitem__("predicates", [
        p for p in b["predicates"] if not p["predicateType"].endswith("/human-review/v1")]))
    statuses = _run(tree)
    assert statuses["C-12"] is Status.PASS
    assert statuses["C-10"] is Status.NOT_RUN


def test_required_types_are_a_subset_of_known_types() -> None:
    assert REQUIRED_PREDICATE_TYPES <= set(KNOWN_PREDICATE_TYPES)


# --- absent input is not_run, never pass ---------------------------------------

def test_c11_is_not_run_without_an_emission(tree: Path) -> None:
    assert _run(tree)["C-11"] is Status.NOT_RUN


def test_rules_needing_a_missing_predicate_report_not_run(tree: Path) -> None:
    """Dropping the review predicate must not silently satisfy C-10."""
    _edit(tree, lambda b: b.__setitem__("predicates", [
        p for p in b["predicates"] if not p["predicateType"].endswith("/human-review/v1")]))
    assert _run(tree)["C-10"] is Status.NOT_RUN


def test_an_unreadable_environment_does_not_pass_the_rules_that_need_it(tree: Path) -> None:
    (tree.parent / "environment.json").unlink()
    statuses = _run(tree)
    for rule in ("C-07", "C-09", "C-10"):
        assert statuses[rule] is Status.NOT_RUN, f"{rule} passed without an environment"
    assert statuses["C-01"] is Status.FAIL


# --- the evidence table --------------------------------------------------------

def test_the_evidence_table_separates_the_two_axes(tree: Path) -> None:
    """`self_attested` and `environment` are different questions.

    Collapsing them is the failure this whole package exists to prevent, so the
    table must never let one stand in for the other.
    """
    bundle = json.loads(tree.read_text(encoding="utf-8"))
    rows = evidence_table(bundle, gather(bundle, tree.parent.parent))
    by_kind = {r.kind: r for r in rows}
    # Independent, and produced outside any Lean environment.
    assert by_kind["corpus-resolution"].attestation == "independent"
    assert by_kind["corpus-resolution"].environment.startswith("n/a")
    # Self-attested, and produced in a DIFFERENT environment. Both true at once.
    assert by_kind["provisional-self-reported"].attestation == "self-attested"
    assert by_kind["provisional-self-reported"].environment.startswith("OTHER")
    # Self-attested, and produced here.
    assert by_kind["build"].attestation == "self-attested"
    assert by_kind["build"].environment == "this environment"


def test_no_row_carries_a_verdict(tree: Path) -> None:
    """The table reports provenance. It must not acquire a score column."""
    bundle = json.loads(tree.read_text(encoding="utf-8"))
    rows = evidence_table(bundle, gather(bundle, tree.parent.parent))
    banned = {"verdict", "result", "status", "score", "trusted", "verified", "ok"}
    assert not banned & set(rows[0]._fields)


def test_every_layout_kind_is_a_recognized_predicate_type() -> None:
    """Guards the fixture against drifting from the contract's type list."""
    known = {t.rstrip("/").split("/")[-2] for t in KNOWN_PREDICATE_TYPES}
    assert set(LAYOUT) <= known
    assert f"{PREDICATE_NS}/build/v1" in KNOWN_PREDICATE_TYPES


# --- the CLI -------------------------------------------------------------------

def test_cli_passes_on_the_coherent_tree(tree: Path) -> None:
    assert main(["conformance", "--bundle", str(tree),
                 "--emission", str(tree.parent / "lean-emission.json")]) == EXIT_OK


def test_cli_fails_on_a_broken_link(tree: Path) -> None:
    _edit(tree, lambda b: b["subject"][0]["digest"].update(gitCommit="0" * 40))
    assert main(["conformance", "--bundle", str(tree)]) == EXIT_FINDINGS


def test_cli_require_all_turns_not_run_into_failure(tree: Path) -> None:
    """Without --emission, C-11 does not run; --require-all makes that fatal."""
    assert main(["conformance", "--bundle", str(tree)]) == EXIT_OK
    assert main(["conformance", "--bundle", str(tree), "--require-all"]) == EXIT_FINDINGS


def test_cli_rejects_a_bundle_with_no_predicates(tree: Path) -> None:
    """A bundle attesting nothing is a usage error, not a clean run."""
    _edit(tree, lambda b: b.__setitem__("predicates", []))
    assert main(["conformance", "--bundle", str(tree)]) == EXIT_USAGE


def test_cli_rejects_a_wrong_artifact_kind(tree: Path) -> None:
    assert main(["conformance", "--bundle", str(VALID_DIR / "build-1.0.json")]) \
        != EXIT_OK


def test_cli_prints_the_evidence_table_and_the_two_counts(
        tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["conformance", "--bundle", str(tree)])
    out = capsys.readouterr().out
    assert "evidence" in out
    assert "not self-attested" in out and "produced in another environment" in out
    assert "OTHER environment" in out


def test_cli_reports_unrecognized_predicates_as_unchecked(
        tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Ingested-but-not-understood must be stated, not silently dropped."""
    _edit(tree, lambda b: b["unrecognized_predicates"].append({
        "predicateType": "https://example.invalid/predicate/future/v1",
        "file": "attest/future.json", "sha256": "0" * 64,
        "produced_by": "someone/1.0", "produced_at": "2026-08-04T00:00:00Z",
        "env_digest": None, "self_attested": False}))
    main(["conformance", "--bundle", str(tree)])
    err = capsys.readouterr().err
    assert "NOT checked" in err
