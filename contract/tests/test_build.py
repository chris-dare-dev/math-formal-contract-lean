"""Tests for `mfc build` — `build/1.0` from what the build actually reported.

## The one test that justifies the whole artifact

`test_a_sorry_is_counted_while_lake_exits_zero` is the reason `build/1.0`
carries `sorry_diagnostic_count` at all. On `leanprover/lean4:v4.32.1` a file
whose only defect is a `sorry` produces a `hasSorry` warning and **exit 0**, so
a gate written against `lake_build_exit` is green on a sorry-backed proof. The
NDJSON in `SORRY_LOG` is not invented: it is the literal output of `lake env
lean --json` over `theorem probeSorry : True := by sorry`.

## And the one that keeps it honest as Lean changes

Counting by `kind` has exactly one silent failure mode — a toolchain that
renames or drops the tag turns every sorry into an uncounted warning and the
count reads `0`. `test_a_sorry_message_not_tagged_hasSorry_is_a_hard_failure`
pins the tripwire that turns that into an exception. Without it this module
would degrade into the vacuous pass it exists to prevent, and no other test
here would notice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.mfc.build import (
    BuildError,
    build,
    measured_scope,
    parse_checker,
    parse_ndjson,
)
from contract.mfc.cli import EXIT_OK, EXIT_USAGE, main

HERE = Path(__file__).resolve().parent
VALID_DIR = HERE.parent / "testdata" / "artifacts" / "valid"

#: Verbatim `lake env lean --json` output at v4.32.1. Exit code was 0.
SORRY_LOG = (
    '{"caption":"","data":"declaration uses `sorry`","endPos":{"column":18,'
    '"line":1},"fileName":"NdjsonProbe.lean","isSilent":false,'
    '"keepFullRange":false,"kind":"hasSorry","pos":{"column":8,"line":1},'
    '"severity":"warning"}\n'
)


def _environment() -> dict:
    return json.loads((VALID_DIR / "environment-1.0.json").read_text(encoding="utf-8"))


def _emission() -> dict:
    return json.loads((VALID_DIR / "emission-1.0.json").read_text(encoding="utf-8"))


def _build(ndjson: str, **kw):
    """`build` with the two coverage inputs defaulted to 'all of it'."""
    kw.setdefault("environment", _environment())
    kw.setdefault("emission", _emission())
    kw.setdefault("covers_all", True)
    kw.setdefault("lake_build_exit", 0)
    kw.setdefault("lake_build_jobs", 1)
    return build(ndjson, **kw)


def test_a_sorry_is_counted_while_lake_exits_zero() -> None:
    doc = _build(SORRY_LOG)
    assert doc["lake_build_exit"] == 0
    assert doc["sorry_diagnostic_count"] == 1


def test_a_sorry_message_not_tagged_hasSorry_is_a_hard_failure() -> None:
    """The tripwire. A renamed `kind` must raise, never count zero."""
    renamed = SORRY_LOG.replace('"kind":"hasSorry"', '"kind":"elab.hasSorry"')
    with pytest.raises(BuildError, match="not 'hasSorry'"):
        _build(renamed)


def test_a_sorry_message_with_no_kind_at_all_is_a_hard_failure() -> None:
    dropped = SORRY_LOG.replace('"kind":"hasSorry",', "")
    with pytest.raises(BuildError, match="sorry_diagnostic_count"):
        _build(dropped)


def test_an_ordinary_warning_does_not_trip_the_tripwire() -> None:
    ordinary = ('{"data":"unused variable `hb`","endPos":{"column":19,"line":3},'
                '"fileName":"B.lean","kind":"linter.unusedVariables",'
                '"pos":{"column":17,"line":3},"severity":"warning"}\n')
    doc = _build(ordinary)
    assert doc["sorry_diagnostic_count"] == 0
    assert doc["warning_count"] == 1


def test_end_pos_null_becomes_a_zero_width_range_at_pos() -> None:
    """`endPos` is nullable in Lean; `end_pos` is required by the schema."""
    line = ('{"data":"x","endPos":null,"fileName":"B.lean","kind":"",'
            '"pos":{"column":17,"line":3},"severity":"warning"}\n')
    [diag] = parse_ndjson(line)
    assert diag["end_pos"] == diag["pos"] == {"line": 3, "column": 17}


def test_information_severity_is_kept_but_counted_in_neither() -> None:
    line = ('{"data":"note","endPos":null,"fileName":"B.lean","kind":"",'
            '"pos":{"column":0,"line":1},"severity":"information"}\n')
    doc = _build(line)
    assert len(doc["diagnostics"]) == 1
    assert doc["error_count"] == doc["warning_count"] == 0


def test_a_line_that_is_not_json_names_the_line() -> None:
    """Lake's progress output on the same handle is a caller error, said so."""
    with pytest.raises(BuildError, match="line 2 is not JSON"):
        parse_ndjson(SORRY_LOG + "Build completed successfully (3428 jobs).\n")


def test_a_non_object_line_is_rejected() -> None:
    with pytest.raises(BuildError, match="line 1 is a list"):
        parse_ndjson("[1,2,3]\n")


def test_a_diagnostic_missing_a_required_field_is_rejected() -> None:
    with pytest.raises(BuildError, match="no fileName"):
        parse_ndjson('{"severity":"error","data":"x","pos":{"line":1,"column":0}}\n')


def test_blank_lines_are_skipped() -> None:
    assert parse_ndjson("\n\n" + SORRY_LOG + "\n") == parse_ndjson(SORRY_LOG)


def test_an_environment_without_a_digest_is_refused() -> None:
    with pytest.raises(BuildError, match="no env_digest"):
        _build(SORRY_LOG, environment={})


def test_negative_jobs_are_refused() -> None:
    with pytest.raises(BuildError, match="no fewer than zero"):
        _build(SORRY_LOG, lake_build_jobs=-1)


# --- independent_checkers ----------------------------------------------------

def test_a_checker_that_passed_while_permitting_sorry_is_refused() -> None:
    """Enforced by the schema too; here the message names the reason."""
    with pytest.raises(BuildError, match="has not checked the thing"):
        parse_checker("leanchecker:4.32.1:pass:true")


def test_a_checker_may_be_not_applicable_while_permitting_sorry() -> None:
    assert parse_checker("nanoda:0.1:not_applicable:true")["allow_sorry"] is True


@pytest.mark.parametrize("spec", [
    "leanchecker:4.32.1:pass",
    "leanchecker:4.32.1:maybe:false",
    "leanchecker:4.32.1:pass:yes",
    ":4.32.1:pass:false",
])
def test_malformed_checker_specs_are_refused(spec: str) -> None:
    with pytest.raises(BuildError):
        parse_checker(spec)


# --- the CLI -----------------------------------------------------------------

def _write_tree(tmp_path: Path, log: str) -> tuple[Path, Path, Path]:
    env_path = tmp_path / "environment.json"
    env_path.write_text(json.dumps(_environment(), indent=2), encoding="utf-8")
    log_path = tmp_path / "log.ndjson"
    log_path.write_text(log, encoding="utf-8")
    (tmp_path / "lean-emission.json").write_text(
        json.dumps(_emission()), encoding="utf-8")
    return env_path, log_path, tmp_path / "attest" / "build.json"


def _cli(tmp_path: Path, env_path: Path, log_path: Path, out: Path,
         *extra: str) -> list[str]:
    return ["build", "--ndjson", str(log_path), "--environment", str(env_path),
            "--emission", str(tmp_path / "lean-emission.json"),
            "--lake-exit", "0", "--lake-jobs", "3428",
            "--out", str(out), *extra]


def test_cli_writes_a_schema_valid_build(tmp_path: Path) -> None:
    env_path, log_path, out = _write_tree(tmp_path, SORRY_LOG)
    rc = main(_cli(tmp_path, env_path, log_path, out, "--covers-all",
                   "--checker", "leanchecker:bundled-4.32.1:pass:false"))
    assert rc == EXIT_OK
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema_version"] == "build/1.0"
    assert doc["sorry_diagnostic_count"] == 1
    assert doc["independent_checkers"][0]["name"] == "leanchecker"


def test_cli_reports_an_unreadable_log_as_usage_not_findings(tmp_path: Path) -> None:
    """Exit 2, never 1: 'could not measure' is not 'has findings'."""
    env_path, log_path, out = _write_tree(tmp_path, "not json at all\n")
    rc = main(_cli(tmp_path, env_path, log_path, out, "--covers-all"))
    assert rc == EXIT_USAGE
    assert not out.exists()


def test_cli_refuses_an_environment_that_is_not_one(tmp_path: Path) -> None:
    env_path, log_path, out = _write_tree(tmp_path, SORRY_LOG)
    env_path.write_text(json.dumps({"schema_version": "build/1.0"}), encoding="utf-8")
    rc = main(_cli(tmp_path, env_path, log_path, out, "--covers-all"))
    assert rc == EXIT_USAGE


# --- `measured`: the field that separates clean from unmeasured ---------------

def test_a_clean_build_and_an_unmeasured_one_differ_only_here() -> None:
    """The whole reason the block exists, asserted rather than described."""
    spotless = _build("")
    assert spotless["error_count"] == spotless["warning_count"] == 0
    assert spotless["sorry_diagnostic_count"] == 0
    narrow = _build("", covers_all=False, covers=["MathFormalContractTest"],
                    emission={"modules": ["MathFormalContractTest", "Other.Mod"]})
    # Every count is identical. Only `measured` tells the reader which is which.
    for field in ("error_count", "warning_count", "sorry_diagnostic_count"):
        assert spotless[field] == narrow[field]
    assert spotless["measured"] != narrow["measured"]
    assert narrow["measured"] == {"modules": ["MathFormalContractTest"],
                                  "in_scope_modules": 2}


def test_coverage_may_be_narrow_but_not_invented() -> None:
    with pytest.raises(BuildError, match="may not be\ninvented|not be invented"):
        measured_scope({"modules": ["A"]}, covers=["A", "B"], covers_all=False)


def test_declaring_nothing_is_refused() -> None:
    with pytest.raises(BuildError, match="having measured nothing"):
        measured_scope({"modules": ["A"]}, covers=[], covers_all=False)


def test_an_emission_with_no_modules_has_no_honest_denominator() -> None:
    with pytest.raises(BuildError, match="no honest"):
        measured_scope({}, covers=None, covers_all=True)


def test_the_denominator_comes_from_the_emission_not_the_caller() -> None:
    """`--covers-all` cannot shrink the denominator to flatter itself."""
    scope = measured_scope({"modules": ["A", "B", "C"]}, covers=None, covers_all=True)
    assert scope == {"modules": ["A", "B", "C"], "in_scope_modules": 3}


def test_covers_all_over_a_duplicated_module_list_counts_once() -> None:
    scope = measured_scope({"modules": ["A", "A", "B"]}, covers=None, covers_all=True)
    assert scope == {"modules": ["A", "B"], "in_scope_modules": 2}


def test_cli_says_partial_coverage_out_loud(tmp_path: Path, capsys) -> None:
    env_path, log_path, out = _write_tree(tmp_path, SORRY_LOG)
    emission = _emission()
    emission["modules"] = ["MathFormalContractTest", "Another.Module"]
    (tmp_path / "lean-emission.json").write_text(json.dumps(emission), encoding="utf-8")
    rc = main(_cli(tmp_path, env_path, log_path, out,
                   "--covers", "MathFormalContractTest"))
    assert rc == EXIT_OK
    assert "cover 1 of 2 in-scope module(s)" in capsys.readouterr().err


def test_cli_requires_a_coverage_claim(tmp_path: Path) -> None:
    """Neither flag is not a default; argparse refuses before anything is read."""
    env_path, log_path, out = _write_tree(tmp_path, SORRY_LOG)
    with pytest.raises(SystemExit):
        main(_cli(tmp_path, env_path, log_path, out))
