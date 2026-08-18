"""Tests for `mfc check-ilean-coverage` — the I-01..I-05 rules.

The claim under test is that this check is **not circular**. Every other rule
in the package reads the emission; this one has to find what the emission left
out, so it must derive the in-scope module set from the filesystem rather than
from the `modules[]` array the emitter computed. `test_i04_...` is the one that
proves it: a scope bug that drops a module from `modules[]` *and* from
`constants[]` is invisible to a self-comparison and must still be caught.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.mfc.cli import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, main
from contract.mfc.ilean import IleanError, Module, is_under, load_modules
from contract.mfc.ilean import check as ilean_check
from contract.mfc.ilean import coverage as ilean_coverage
from contract.mfc.rules import Status

HERE = Path(__file__).resolve().parent
VALID_DIR = HERE.parent / "testdata" / "artifacts" / "valid"

ROOT = "Topic"


def _constant(name: str, module: str) -> dict:
    return {
        "name": name, "module": module, "scope": "topic", "kind": "theorem",
        "is_instance": False, "is_internal": False, "is_private": False,
        "is_reducible": False, "num_levels": 0,
        "type_pp": "True", "value_pp": None, "doc": None,
        "local_deps": [], "scc_members": [], "discharges": [], "axioms": [],
        "range": None, "cites": [],
    }


def _emission(constants: list[dict], *, root_lib: str = ROOT,
              modules: list[str] | None = None) -> dict:
    base = json.loads((VALID_DIR / "emission-1.0.json").read_text(encoding="utf-8"))
    base["root_lib"] = root_lib
    base["constants"] = constants
    base["modules"] = (modules if modules is not None
                       else sorted({c["module"] for c in constants}))
    n = len(constants)
    base["counts"] = {"total": n, "in_scope": n, "internal": 0,
                      "with_range": 0, "instances": 0, "private": 0, "external": 0,
                      "foreign": 0}
    return base


def _module(name: str, *decls: str) -> Module:
    return Module(name=name, path=Path(f"{name}.ilean"), decls=frozenset(decls))


def _coherent() -> tuple[dict, list[Module]]:
    """Two in-scope modules, one sibling library, one executable."""
    constants = [_constant("Topic.A.thm", "Topic.A"), _constant("Topic.B.thm", "Topic.B")]
    modules = [
        _module("Topic"),
        _module("Topic.A", "Topic.A.thm"),
        _module("Topic.B", "Topic.B.thm"),
        _module("TopicTest", "TopicTest.spec"),
        _module("Main", "main"),
    ]
    return _emission(constants, modules=["Topic", "Topic.A", "Topic.B"]), modules


def _run(emission: dict, modules: list[Module], **kw) -> dict[str, Status]:
    return {r.rule: r.status for r in ilean_check(emission, modules, **kw)}


# --- component-wise prefixes --------------------------------------------------

def test_a_sibling_name_is_not_a_submodule() -> None:
    """`"TopicTest".startswith("Topic")` is True; the module relation is not.

    A string prefix would pull an unrelated library into scope and then fail
    I-03 on declarations the emitter is correct to exclude.
    """
    assert is_under("Topic.A", "Topic")
    assert is_under("Topic", "Topic")
    assert not is_under("TopicTest", "Topic")
    assert not is_under("TopicTests.Deep.Module", "Topic")


def test_the_test_library_is_out_of_scope_and_that_is_not_a_finding() -> None:
    emission, modules = _coherent()
    statuses = _run(emission, modules)
    assert not [r for r, s in statuses.items() if s is Status.FAIL]


def test_coverage_counts_only_in_scope_modules() -> None:
    emission, modules = _coherent()
    c = ilean_coverage(emission, modules)
    assert (c.in_scope_modules, c.built_declarations, c.missing) == (3, 2, 0)


# --- the set-diff -------------------------------------------------------------

def test_i03_a_module_the_emitter_skipped() -> None:
    """Lake built it; the emitter did not sweep it. A bug, not a policy choice."""
    emission, modules = _coherent()
    emission["constants"] = [c for c in emission["constants"]
                             if c["module"] != "Topic.B"]
    assert _run(emission, modules)["I-03"] is Status.FAIL


def test_i03_reports_the_true_total_when_it_truncates(
        capsys: pytest.CaptureFixture[str]) -> None:
    """A truncated list that did not say so reads as '50 missing' when 300 are."""
    modules = [_module("Topic", *[f"Topic.d{i}" for i in range(300)])]
    emission = _emission([_constant("Topic.d0", "Topic")], modules=["Topic"])
    results = ilean_check(emission, modules)
    i03 = next(r for r in results if r.rule == "I-03")
    assert i03.status is Status.FAIL
    assert len(i03.findings) == 50
    assert "of 299" in i03.reason


# --- the vacuous pass, and the bootstrap that must not be confused with it ----

def test_i02_an_empty_emission_over_a_non_empty_build_fails() -> None:
    """The mis-scoped emitter: everything downstream would be valid and empty."""
    _, modules = _coherent()
    emission = _emission([], modules=[])
    statuses = _run(emission, modules)
    assert statuses["I-02"] is Status.FAIL
    assert statuses["I-03"] is Status.FAIL


def test_i02_a_genuinely_empty_repository_passes() -> None:
    """A first build has nothing in it, and a guard that failed here would mean
    no adopter could ever reach a first green build."""
    modules = [_module("Topic")]
    emission = _emission([], modules=["Topic"])
    statuses = _run(emission, modules)
    assert statuses["I-02"] is Status.PASS
    assert statuses["I-03"] is Status.PASS


def test_the_empty_repository_says_nothing_was_checked() -> None:
    """Passing is not the same as having checked something, and it must say so."""
    modules = [_module("Topic")]
    results = ilean_check(_emission([], modules=["Topic"]), modules)
    i02 = next(r for r in results if r.rule == "I-02")
    assert "BOTH are empty" in i02.reason
    assert "nothing has been checked" in i02.reason


def test_no_ilean_files_at_all_is_not_a_pass(tmp_path: Path) -> None:
    """A mis-pointed build directory must not be reportable as full coverage."""
    with pytest.raises(IleanError, match="did NOT run"):
        load_modules(tmp_path)


def test_a_missing_build_directory_is_not_a_pass(tmp_path: Path) -> None:
    with pytest.raises(IleanError, match="no such build directory"):
        load_modules(tmp_path / "nope")


def test_an_unrecognised_ilean_layout_stops_the_check(tmp_path: Path) -> None:
    """If Lake changes the format, guessing would reduce coverage to nothing."""
    (tmp_path / "a.ilean").write_text(json.dumps({"version": 99, "module": "Topic"}),
                                      encoding="utf-8")
    with pytest.raises(IleanError, match="unrecognised .ilean layout"):
        load_modules(tmp_path)


# --- non-circularity ----------------------------------------------------------

def test_i04_catches_a_scope_bug_that_a_self_comparison_cannot() -> None:
    """The point of the whole file.

    A scope bug drops `Topic.B` from `modules[]` AND from `constants[]`, so the
    emission agrees with itself perfectly. Only re-deriving the module set from
    the filesystem can see it.
    """
    _, modules = _coherent()
    emission = _emission([_constant("Topic.A.thm", "Topic.A")],
                         modules=["Topic", "Topic.A"])
    # The emission is internally consistent: every module it lists is a module
    # its constants came from, and vice versa.
    assert {c["module"] for c in emission["constants"]} <= set(emission["modules"])
    statuses = _run(emission, modules)
    assert statuses["I-04"] is Status.FAIL, "a self-consistent scope bug got through"
    assert statuses["I-03"] is Status.FAIL


def test_lib_overrides_the_emissions_declared_root() -> None:
    """`--lib` comes from the caller, which is what keeps the scope external."""
    _, modules = _coherent()
    emission = _emission([_constant("TopicTest.spec", "TopicTest")],
                         root_lib="Topic", modules=["TopicTest"])
    assert _run(emission, modules)["I-03"] is Status.FAIL
    assert _run(emission, modules, lib="TopicTest")["I-03"] is Status.PASS


def test_i01_fails_when_there_is_no_root_to_scope_to() -> None:
    emission, modules = _coherent()
    del emission["root_lib"]
    results = ilean_check(emission, modules)
    assert results[0].rule == "I-01" and results[0].status is Status.FAIL
    assert len(results) == 1, "no later rule may run without a scope"


# --- staleness ----------------------------------------------------------------

def test_i05_an_emission_from_a_module_lake_did_not_build() -> None:
    """A stale emission checked in against a newer tree looks exactly like this."""
    emission, modules = _coherent()
    emission["constants"].append(_constant("Topic.Gone.thm", "Topic.Gone"))
    emission["modules"] = [*emission["modules"], "Topic.Gone"]
    assert _run(emission, modules)["I-05"] is Status.FAIL


# --- the real repository ------------------------------------------------------

def test_this_repository_is_fully_covered() -> None:
    """Runs against the tree the tests are running in, when it has been built.

    Skipped rather than faked when there is no build: a check that silently
    passes because it found nothing is the exact failure under test.
    """
    build_dir = HERE.parent.parent / ".lake" / "build" / "lib" / "lean"
    emission_path = HERE.parent.parent / "attest" / "lean-emission.json"
    if not build_dir.is_dir() or not emission_path.is_file():
        pytest.skip("no lake build output or emission in this tree")
    emission = json.loads(emission_path.read_text(encoding="utf-8"))
    statuses = _run(emission, load_modules(build_dir))
    assert not [r for r, s in statuses.items() if s is Status.FAIL]


# --- the CLI -------------------------------------------------------------------

def _tree(tmp_path: Path, modules: list[Module]) -> Path:
    build = tmp_path / "build"
    build.mkdir(parents=True, exist_ok=True)
    for m in modules:
        (build / f"{m.name}.ilean").write_text(
            json.dumps({"version": 5, "module": m.name,
                        "decls": {d: [0, 0, 0, 0] for d in sorted(m.decls)},
                        "directImports": [], "references": []}),
            encoding="utf-8")
    return build


def test_cli_passes_on_a_covered_tree(tmp_path: Path) -> None:
    emission, modules = _coherent()
    build = _tree(tmp_path, modules)
    p = tmp_path / "emission.json"
    p.write_text(json.dumps(emission), encoding="utf-8")
    assert main(["check-ilean-coverage", "--emission", str(p),
                 "--build-dir", str(build)]) == EXIT_OK


def test_cli_fails_on_a_skipped_module(tmp_path: Path) -> None:
    emission, modules = _coherent()
    emission["constants"] = [c for c in emission["constants"]
                             if c["module"] != "Topic.B"]
    build = _tree(tmp_path, modules)
    p = tmp_path / "emission.json"
    p.write_text(json.dumps(emission), encoding="utf-8")
    assert main(["check-ilean-coverage", "--emission", str(p),
                 "--build-dir", str(build)]) == EXIT_FINDINGS


def test_cli_exits_usage_when_nothing_was_built(tmp_path: Path) -> None:
    """Exit 2, not 1 and never 0: the check did not run."""
    emission, _ = _coherent()
    p = tmp_path / "emission.json"
    p.write_text(json.dumps(emission), encoding="utf-8")
    (tmp_path / "empty").mkdir()
    assert main(["check-ilean-coverage", "--emission", str(p),
                 "--build-dir", str(tmp_path / "empty")]) == EXIT_USAGE


def test_the_cli_never_reaches_the_empty_case_because_the_schema_bars_it(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The bootstrap is solved a level up, by the schema, not by this rule.

    `emission-1.0.schema.json` sets `constants: minItems 1`, so an empty
    emission is not a representable artifact. Every subcommand validates before
    it reads, so `I-02`'s both-empty branch is defence in depth for a library
    caller and is unreachable through `mfc`. Asserted rather than assumed,
    because a later relaxation of the schema would silently change which
    mechanism is doing the work.
    """
    build = _tree(tmp_path, [_module("Topic")])
    p = tmp_path / "emission.json"
    p.write_text(json.dumps(_emission([], modules=["Topic"])), encoding="utf-8")
    assert main(["check-ilean-coverage", "--emission", str(p),
                 "--build-dir", str(build)]) != EXIT_OK
    assert "should be non-empty" in capsys.readouterr().err


def test_cli_validates_the_emission_first(tmp_path: Path) -> None:
    emission, modules = _coherent()
    emission["constants"][0]["axioms"] = "not-a-list"
    p = tmp_path / "emission.json"
    p.write_text(json.dumps(emission), encoding="utf-8")
    assert main(["check-ilean-coverage", "--emission", str(p),
                 "--build-dir", str(_tree(tmp_path, modules))]) != EXIT_OK
