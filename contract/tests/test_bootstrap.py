"""Tests for the bootstrap flag — #159.

Three properties, and the third is the one that matters:

1. a brand-new topic repository, whose emission is empty, reaches a green
   build while the flag is set;
2. nothing is reported as *checked* while it is set — every rule reads
   `not_run`, never `pass`;
3. the flag burns down. `mfc lint` clears it the first time the emission is
   non-empty, and a record that has been cleared may never set it again.

Without (3) this is not a bootstrap, it is an `--allow-empty` flag with a
longer name, and the vacuous-pass guard is optional for anyone who reads the
source.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.mfc import bootstrap
from contract.mfc.cli import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, main
from contract.mfc.rules import RULE_TITLES, Status, check
from contract.mfc.validate import SCHEMA_DIR

HERE = Path(__file__).resolve().parent
VALID_DIR = HERE.parent / "testdata" / "artifacts" / "valid"
#: A repository that has nothing in it yet: no constants, all counts zero.
EMPTY = HERE.parent / "testdata" / "emissions" / "bootstrap" / "empty-repo.json"
#: A MIS-SCOPED emitter: it swept something and matched none of it. Fails the
#: same guard, and bootstrap must not excuse it.
MISSCOPED = HERE.parent / "testdata" / "emissions" / "invalid" / "empty-emission.json"

RECORD = """\
topic: bootstrap-fixture
lean_library: Fixture

bootstrap: true

status:
  sorry_count: 0
"""


def _repo(tmp_path: Path, *, emission: Path, record: str | None = RECORD) -> Path:
    """A directory holding an emission, an environment, and maybe a record."""
    (tmp_path / "attest").mkdir()
    (tmp_path / "attest" / "lean-emission.json").write_text(
        emission.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "attest" / "environment.json").write_text(
        (VALID_DIR / "environment-1.0.json").read_text(encoding="utf-8"),
        encoding="utf-8")
    if record is not None:
        (tmp_path / "formalization.yaml").write_text(record, encoding="utf-8")
    return tmp_path


def _lint(repo: Path, *extra: str) -> int:
    return main([
        "lint",
        "--emission", str(repo / "attest" / "lean-emission.json"),
        "--environment", str(repo / "attest" / "environment.json"),
        "--record", str(repo / "formalization.yaml"),
        *extra,
    ])


# --------------------------------------------------------------------------
# 1. The adopter's first build is reachable.
# --------------------------------------------------------------------------

def test_an_empty_emission_is_rejected_without_the_flag(tmp_path: Path) -> None:
    """The guard is unchanged for everyone who has not opted in."""
    repo = _repo(tmp_path, emission=EMPTY, record="topic: x\n")
    assert _lint(repo) == EXIT_FINDINGS  # the emission does not validate


def test_a_mis_scoped_emitter_is_not_excused_by_the_flag(tmp_path: Path) -> None:
    """The guard trips on two situations; bootstrap may only excuse one.

    An emitter that swept declarations and matched none of them against the
    root library is broken on day one exactly as it is on day one thousand.
    """
    repo = _repo(tmp_path, emission=MISSCOPED)
    assert _lint(repo) == EXIT_FINDINGS
    assert "bootstrap: true" in (repo / "formalization.yaml").read_text(), \
        "a write-once flag must not be burned on the strength of a broken emission"


def test_a_new_repo_lints_green_under_the_flag(tmp_path: Path) -> None:
    repo = _repo(tmp_path, emission=EMPTY)
    assert _lint(repo) == EXIT_OK


def test_validate_accepts_the_empty_emission_under_the_flag(tmp_path: Path) -> None:
    repo = _repo(tmp_path, emission=EMPTY)
    assert main(["validate", str(repo / "attest" / "lean-emission.json"),
                 "--record", str(repo / "formalization.yaml")]) == EXIT_OK


def test_bundle_accepts_the_empty_emission_under_the_flag(tmp_path: Path) -> None:
    repo = _repo(tmp_path, emission=EMPTY)
    assert main([
        "bundle",
        "--emission", str(repo / "attest" / "lean-emission.json"),
        "--environment", str(repo / "attest" / "environment.json"),
        "--out", str(repo / "attest" / "declarations.json"),
        "--record", str(repo / "formalization.yaml"),
    ]) == EXIT_OK
    out = json.loads((repo / "attest" / "declarations.json").read_text(encoding="utf-8"))
    assert out["counts"]["total"] == 0, "an honest zero, not an invented one"


# --------------------------------------------------------------------------
# 2. Green does not mean checked.
# --------------------------------------------------------------------------

def test_every_rule_reads_not_run_under_the_flag() -> None:
    emission = json.loads(EMPTY.read_text(encoding="utf-8"))
    environment = json.loads(
        (VALID_DIR / "environment-1.0.json").read_text(encoding="utf-8"))
    results = check(emission, environment, bootstrap=True)
    assert [r.status for r in results] == [Status.NOT_RUN] * len(RULE_TITLES)
    assert all(r.reason for r in results), "a not_run with no reason is unreadable"


def test_the_bootstrap_table_reports_the_same_rules_as_an_ordinary_run() -> None:
    """A rule missing from the table reads as a rule that does not exist."""
    emission = json.loads((VALID_DIR / "emission-1.0.json").read_text(encoding="utf-8"))
    environment = json.loads(
        (VALID_DIR / "environment-1.0.json").read_text(encoding="utf-8"))
    ordinary = check(emission, environment)
    assert [(r.rule, r.title) for r in ordinary] == list(RULE_TITLES)


def test_require_all_still_fails_under_the_flag(tmp_path: Path) -> None:
    """`--require-all` is how a repo says not_run is not good enough."""
    repo = _repo(tmp_path, emission=EMPTY)
    assert _lint(repo, "--require-all") == EXIT_FINDINGS


# --------------------------------------------------------------------------
# 3. The flag burns down.
# --------------------------------------------------------------------------

def test_lint_clears_the_flag_once_there_are_declarations(tmp_path: Path) -> None:
    repo = _repo(tmp_path, emission=VALID_DIR / "emission-1.0.json")
    assert _lint(repo) == EXIT_OK
    text = (repo / "formalization.yaml").read_text(encoding="utf-8")
    assert "bootstrap: false" in text
    assert "bootstrap_cleared_at:" in text


def test_a_cleared_record_may_never_set_the_flag_again(tmp_path: Path) -> None:
    repo = _repo(tmp_path, emission=EMPTY, record=(
        "topic: x\nbootstrap: true\nbootstrap_cleared_at: 2026-08-17\n"))
    assert _lint(repo) == EXIT_USAGE
    with pytest.raises(bootstrap.RecordError):
        bootstrap.read(repo / "formalization.yaml")


def test_clearing_is_idempotent_in_the_sense_that_matters(tmp_path: Path) -> None:
    """A second lint over a cleared record is an ordinary lint, not an error."""
    repo = _repo(tmp_path, emission=VALID_DIR / "emission-1.0.json")
    assert _lint(repo) == EXIT_OK
    assert _lint(repo) == EXIT_OK


def test_clear_text_preserves_the_comments_around_it() -> None:
    text = ("# a claim, not decoration\n"
            "topic: x\n"
            "bootstrap: true  # cleared by mfc lint\n"
            "status:\n  sorry_count: 0\n")
    out = bootstrap.clear_text(text, "2026-08-17")
    assert "# a claim, not decoration" in out
    assert "# cleared by mfc lint" in out
    assert "bootstrap: false" in out
    assert "bootstrap_cleared_at: 2026-08-17" in out
    assert out.endswith("status:\n  sorry_count: 0\n")


def test_clear_text_refuses_to_guess() -> None:
    with pytest.raises(bootstrap.RecordError):
        bootstrap.clear_text("topic: x\n", "2026-08-17")
    with pytest.raises(bootstrap.RecordError):
        bootstrap.clear_text("bootstrap: false\n", "2026-08-17")


# --------------------------------------------------------------------------
# The relaxation itself.
# --------------------------------------------------------------------------

def test_relax_drops_exactly_the_three_vacuity_constraints() -> None:
    schema = json.loads(
        (SCHEMA_DIR / "emission-1.0.schema.json").read_text(encoding="utf-8"))
    relaxed = bootstrap.relax(schema)

    def flatten(node, path=()):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from flatten(v, (*path, k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from flatten(v, (*path, str(i)))
        else:
            yield path, node

    before, after = dict(flatten(schema)), dict(flatten(relaxed))
    assert set(before) - set(after) == set(bootstrap.RELAXED)
    assert set(after) - set(before) == set()
    assert all(before[k] == after[k] for k in after), "no value was rewritten"


def test_relax_does_not_mutate_its_input() -> None:
    schema = json.loads(
        (SCHEMA_DIR / "emission-1.0.schema.json").read_text(encoding="utf-8"))
    bootstrap.relax(schema)
    assert schema["properties"]["constants"]["minItems"] == 1


def test_an_absent_record_is_not_bootstrapping(tmp_path: Path) -> None:
    """Opt-in. No record means no flag, and no flag means the guard is live."""
    state = bootstrap.read(tmp_path / "formalization.yaml")
    assert state.active is False and state.present is False
