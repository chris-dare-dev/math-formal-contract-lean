"""Tests for `mfc seal` — the producer for the file conformance reads.

## What was missing until this module existed

The README stated it plainly rather than faking it: "Nothing assembles a
`bundle.json` ... so there is nothing for `conformance` to run against." Every
one of the twelve `C-` rules was exercised against a tree assembled inside
`test_conformance.py`, by the tests themselves. `test_conformance_passes_over_a_
sealed_bundle` closes that loop — the bundle under test is the one `mfc seal`
writes, so a producer that disagreed with the checker would now fail here
instead of passing separately at each end.

## The refusal is the feature

`test_seal_refuses_to_omit_the_build_predicate` matters more than any field
this module writes. `C-12` exists because a bundle missing `build/v1` satisfies
the other eleven rules; a producer willing to write that bundle would be
shipping the `C-12` rejection fixture as a product. There is deliberately no
`--force`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.mfc.cli import EXIT_OK, EXIT_USAGE, main
from contract.mfc.conformance import REQUIRED_PREDICATE_TYPES, short_type
from contract.mfc.digest import file_digest
from contract.mfc.seal import SealError, parse_provisional, seal

HERE = Path(__file__).resolve().parent
VALID_DIR = HERE.parent / "testdata" / "artifacts" / "valid"


def _fixture(name: str) -> dict:
    return json.loads((VALID_DIR / name).read_text(encoding="utf-8"))


def _write(path: Path, doc: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def sealable_tree(root: Path) -> dict[str, Path]:
    """An `attest/` tree whose cross-artifact links already hold.

    Only `declarations.emission_sha256` is recomputed — it is the one link a
    real pipeline derives rather than authors, and `C-11` compares it to the
    emission that ships. Everything else stays as the fixtures have it.
    """
    emission = _write(root / "attest" / "lean-emission.json",
                      _fixture("emission-1.0.json"))
    declarations = _fixture("declarations-1.0.json")
    declarations["emission_sha256"] = file_digest(emission)
    return {
        "root": root,
        "emission": emission,
        "environment": _write(root / "attest" / "environment.json",
                              _fixture("environment-1.0.json")),
        "declarations": _write(root / "attest" / "declarations.json", declarations),
        "build": _write(root / "attest" / "build.json", _fixture("build-1.0.json")),
        "resolution": _write(root / "attest" / "resolution.json",
                             _fixture("resolution-1.0.json")),
        "registry": _write(root / "registry" / "registry.json",
                           {"schema_version": "registry/1.0", "entries": {}}),
    }


def _seal(tree: dict[str, Path], **overrides):
    kw = dict(
        root=tree["root"],
        environment_path=tree["environment"],
        environment=json.loads(tree["environment"].read_text(encoding="utf-8")),
        registry_path=tree["registry"],
        declarations_path=tree["declarations"],
        build_path=tree["build"],
    )
    kw.update(overrides)
    return seal(**kw)


# --- the refusals ------------------------------------------------------------

def test_seal_refuses_to_omit_the_build_predicate(tmp_path: Path) -> None:
    tree = sealable_tree(tmp_path)
    with pytest.raises(SealError, match="build/v1 predicate"):
        _seal(tree, build_path=None)


def test_seal_refuses_to_omit_the_declarations_predicate(tmp_path: Path) -> None:
    tree = sealable_tree(tmp_path)
    with pytest.raises(SealError, match="declarations/v1 predicate"):
        _seal(tree, declarations_path=None)


def test_the_producer_and_the_checker_share_one_required_set() -> None:
    """Not a tautology: it is what stops the two lists drifting apart."""
    assert {short_type(t) for t in REQUIRED_PREDICATE_TYPES} == {
        "environment", "declarations", "build"}


def test_seal_refuses_a_dirty_worktree(tmp_path: Path) -> None:
    tree = sealable_tree(tmp_path)
    env = json.loads(tree["environment"].read_text(encoding="utf-8"))
    env["root_package"]["worktree_dirty"] = True
    with pytest.raises(SealError, match="uncommitted changes"):
        _seal(tree, environment=env)


def test_allow_dirty_seals_anyway(tmp_path: Path) -> None:
    tree = sealable_tree(tmp_path)
    env = json.loads(tree["environment"].read_text(encoding="utf-8"))
    env["root_package"]["worktree_dirty"] = True
    assert _seal(tree, environment=env, allow_dirty=True)["schema_version"] == "bundle/1.0"


def test_a_review_needs_a_named_human(tmp_path: Path) -> None:
    tree = sealable_tree(tmp_path)
    review = _write(tmp_path / "attest" / "review.json", _fixture("review-1.0.json"))
    with pytest.raises(SealError, match="no machine may write"):
        _seal(tree, review_path=review)


def test_a_predicate_outside_the_root_is_refused(tmp_path: Path) -> None:
    tree = sealable_tree(tmp_path / "repo")
    outside = _write(tmp_path / "elsewhere" / "build.json", _fixture("build-1.0.json"))
    with pytest.raises(SealError, match="outside the bundle root"):
        _seal(tree, build_path=outside)


def test_a_missing_artifact_is_refused(tmp_path: Path) -> None:
    tree = sealable_tree(tmp_path)
    with pytest.raises(SealError, match="no such build artifact"):
        _seal(tree, build_path=tmp_path / "attest" / "absent.json")


def test_provisional_requires_an_env_digest() -> None:
    """C-05 is the rule that makes provisional evidence mean anything."""
    with pytest.raises(SealError, match="64-hex"):
        parse_provisional("attest/t.json:arxmcp/lean_verify:not-a-digest")


# --- what it writes ----------------------------------------------------------

def test_every_predicate_sha256_is_the_file_on_disk(tmp_path: Path) -> None:
    tree = sealable_tree(tmp_path)
    for pred in _seal(tree)["predicates"]:
        assert pred["sha256"] == file_digest(tmp_path / pred["file"])


def test_the_subject_is_the_commit_the_environment_records(tmp_path: Path) -> None:
    tree = sealable_tree(tmp_path)
    env = json.loads(tree["environment"].read_text(encoding="utf-8"))
    [subject] = _seal(tree)["subject"]
    assert subject["digest"]["gitCommit"] == env["root_package"]["rev"]
    assert subject["digest"]["gitTag"] == env["root_package"]["tag"]


def test_an_ssh_remote_becomes_an_https_uri(tmp_path: Path) -> None:
    tree = sealable_tree(tmp_path)
    env = json.loads(tree["environment"].read_text(encoding="utf-8"))
    env["root_package"]["url"] = "git@github.com:chris-dare-dev/bridgeland-stab-lean.git"
    [subject] = _seal(tree, environment=env)["subject"]
    assert subject["uri"] == "https://github.com/chris-dare-dev/bridgeland-stab-lean"


def test_the_resolution_predicate_carries_a_null_env_digest(tmp_path: Path) -> None:
    """Not a mismatch and not a match — it is not produced in Lean at all."""
    tree = sealable_tree(tmp_path)
    resolution = json.loads(tree["resolution"].read_text(encoding="utf-8"))
    doc = _seal(tree, resolution_path=tree["resolution"], resolution=resolution)
    [pred] = [p for p in doc["predicates"] if short_type(p["predicateType"])
              == "corpus-resolution"]
    assert pred["env_digest"] is None
    assert pred["self_attested"] is False


def test_unrecognized_predicates_is_empty_because_mfc_emits_known_types_only(
        tmp_path: Path) -> None:
    assert _seal(sealable_tree(tmp_path))["unrecognized_predicates"] == []


# --- the loop that was open --------------------------------------------------

def test_conformance_passes_over_a_sealed_bundle(tmp_path: Path, capsys) -> None:
    """The producer and the twelve checker rules, end to end, for the first time."""
    tree = sealable_tree(tmp_path)
    bundle_path = tmp_path / "attest" / "bundle.json"
    assert main(["seal", "--root", str(tmp_path),
                 "--environment", str(tree["environment"]),
                 "--registry", str(tree["registry"]),
                 "--declarations", str(tree["declarations"]),
                 "--build", str(tree["build"]),
                 "--out", str(bundle_path)]) == EXIT_OK
    capsys.readouterr()

    assert main(["conformance", "--bundle", str(bundle_path),
                 "--emission", str(tree["emission"])]) == EXIT_OK
    out = capsys.readouterr().out
    assert "0 failed" in out
    # C-12 is the rule the refusal above protects; it must actually be ok here.
    assert "ok  C-12" in out


def test_cli_reports_a_refusal_as_usage_not_findings(tmp_path: Path) -> None:
    tree = sealable_tree(tmp_path)
    out = tmp_path / "attest" / "bundle.json"
    rc = main(["seal", "--root", str(tmp_path),
               "--environment", str(tree["environment"]),
               "--registry", str(tree["registry"]),
               "--declarations", str(tree["declarations"]),
               "--out", str(out)])
    assert rc == EXIT_USAGE
    assert not out.exists()
