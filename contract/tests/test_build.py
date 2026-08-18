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

from contract.mfc.build import BuildError, build, parse_checker, parse_ndjson
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


def test_a_sorry_is_counted_while_lake_exits_zero() -> None:
    doc = build(SORRY_LOG, environment=_environment(),
                lake_build_exit=0, lake_build_jobs=1)
    assert doc["lake_build_exit"] == 0
    assert doc["sorry_diagnostic_count"] == 1


def test_a_sorry_message_not_tagged_hasSorry_is_a_hard_failure() -> None:
    """The tripwire. A renamed `kind` must raise, never count zero."""
    renamed = SORRY_LOG.replace('"kind":"hasSorry"', '"kind":"elab.hasSorry"')
    with pytest.raises(BuildError, match="not 'hasSorry'"):
        build(renamed, environment=_environment(),
              lake_build_exit=0, lake_build_jobs=1)


def test_a_sorry_message_with_no_kind_at_all_is_a_hard_failure() -> None:
    dropped = SORRY_LOG.replace('"kind":"hasSorry",', "")
    with pytest.raises(BuildError, match="sorry_diagnostic_count"):
        build(dropped, environment=_environment(),
              lake_build_exit=0, lake_build_jobs=1)


def test_an_ordinary_warning_does_not_trip_the_tripwire() -> None:
    ordinary = ('{"data":"unused variable `hb`","endPos":{"column":19,"line":3},'
                '"fileName":"B.lean","kind":"linter.unusedVariables",'
                '"pos":{"column":17,"line":3},"severity":"warning"}\n')
    doc = build(ordinary, environment=_environment(),
                lake_build_exit=0, lake_build_jobs=1)
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
    doc = build(line, environment=_environment(),
                lake_build_exit=0, lake_build_jobs=1)
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
        build(SORRY_LOG, environment={}, lake_build_exit=0, lake_build_jobs=1)


def test_negative_jobs_are_refused() -> None:
    with pytest.raises(BuildError, match="no fewer than zero"):
        build(SORRY_LOG, environment=_environment(),
              lake_build_exit=0, lake_build_jobs=-1)


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
    return env_path, log_path, tmp_path / "attest" / "build.json"


def test_cli_writes_a_schema_valid_build(tmp_path: Path) -> None:
    env_path, log_path, out = _write_tree(tmp_path, SORRY_LOG)
    rc = main(["build", "--ndjson", str(log_path), "--environment", str(env_path),
               "--lake-exit", "0", "--lake-jobs", "3428",
               "--checker", "leanchecker:bundled-4.32.1:pass:false",
               "--out", str(out)])
    assert rc == EXIT_OK
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema_version"] == "build/1.0"
    assert doc["sorry_diagnostic_count"] == 1
    assert doc["independent_checkers"][0]["name"] == "leanchecker"


def test_cli_reports_an_unreadable_log_as_usage_not_findings(tmp_path: Path) -> None:
    """Exit 2, never 1: 'could not measure' is not 'has findings'."""
    env_path, log_path, out = _write_tree(tmp_path, "not json at all\n")
    rc = main(["build", "--ndjson", str(log_path), "--environment", str(env_path),
               "--lake-exit", "0", "--lake-jobs", "1", "--out", str(out)])
    assert rc == EXIT_USAGE
    assert not out.exists()


def test_cli_refuses_an_environment_that_is_not_one(tmp_path: Path) -> None:
    env_path, log_path, out = _write_tree(tmp_path, SORRY_LOG)
    env_path.write_text(json.dumps({"schema_version": "build/1.0"}), encoding="utf-8")
    rc = main(["build", "--ndjson", str(log_path), "--environment", str(env_path),
               "--lake-exit", "0", "--lake-jobs", "1", "--out", str(out)])
    assert rc == EXIT_USAGE
