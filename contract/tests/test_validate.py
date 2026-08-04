"""Tests for `mfc validate`.

The interesting cases are not "does a good artifact pass". They are the three
ways a validator can be worse than useless: validating against the wrong
schema, reporting one error when there are six, and treating "I could not
check this" as "this is fine".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.mfc.cli import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, main
from contract.mfc.validate import LoadError, schema_path_for, validate_artifact

HERE = Path(__file__).resolve().parent
SCHEMA_DIR = HERE.parent / "mfc" / "schema"
VALID_DIR = HERE.parent / "testdata" / "artifacts" / "valid"
INVALID_DIR = HERE.parent / "testdata" / "artifacts" / "invalid"

ARTIFACTS = sorted(p.stem for p in VALID_DIR.glob("*.json"))


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


# --- the happy path, for all seven -------------------------------------------

@pytest.mark.parametrize("name", ARTIFACTS)
def test_every_valid_artifact_validates(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(VALID_DIR / f"{name}.json")]) == EXIT_OK
    assert "ok:" in capsys.readouterr().out


# --- the schema is inferred from the artifact's own claim --------------------

def test_schema_is_inferred_from_schema_version() -> None:
    assert schema_path_for("emission/1.0").name == "emission-1.0.schema.json"
    assert schema_path_for("review/1.0").name == "review-1.0.schema.json"


def test_inference_uses_the_declared_version_not_the_filename(tmp_path: Path) -> None:
    """Renaming a file must not change which schema it is checked against.

    The artifact's self-description is part of what is validated; a file called
    `build.json` that declares `emission/1.0` is checked as an emission and
    fails, rather than being quietly checked as a build.
    """
    doc = _load(VALID_DIR / "build-1.0.json")
    doc["schema_version"] = "emission/1.0"
    p = tmp_path / "build-1.0.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert main(["validate", str(p)]) == EXIT_FINDINGS


def test_unknown_contract_version_is_a_hard_failure(tmp_path: Path) -> None:
    """There is no tolerant mode.

    An artifact from a contract version this build does not carry must fail,
    never be skipped -- skipping would let a future version pass unchecked.
    """
    doc = _load(VALID_DIR / "emission-1.0.json")
    doc["schema_version"] = "emission/9.9"
    p = tmp_path / "future.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert main(["validate", str(p)]) == EXIT_USAGE


def test_missing_schema_version_cannot_be_guessed(tmp_path: Path) -> None:
    p = tmp_path / "anon.json"
    p.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    assert main(["validate", str(p)]) == EXIT_USAGE


def test_malformed_schema_version_is_reported(tmp_path: Path) -> None:
    with pytest.raises(LoadError):
        schema_path_for("nonsense-without-a-slash")


# --- all errors, not the first -----------------------------------------------

def test_reports_every_error_not_just_the_first() -> None:
    """A validator that stops at the first problem costs N round trips."""
    doc = _load(VALID_DIR / "environment-1.0.json")
    doc["env_digest"] = "not-a-digest"
    doc["lean_githash"] = "also-not"
    del doc["lake_version"]
    schema = _load(SCHEMA_DIR / "environment-1.0.schema.json")
    problems = validate_artifact(doc, schema)
    assert len(problems) >= 3, f"expected several problems, got {problems}"


# --- the rejection fixtures fail through the CLI too -------------------------

@pytest.mark.parametrize("name", sorted(p.stem for p in INVALID_DIR.glob("*.json")))
def test_rejection_fixtures_fail_the_cli(name: str, tmp_path: Path) -> None:
    doc = _load(INVALID_DIR / f"{name}.json")
    doc.pop("$comment_fixture", None)
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert main(["validate", str(p)]) == EXIT_FINDINGS


# --- "could not check" is never "fine" ---------------------------------------

def test_missing_file_is_usage_error(tmp_path: Path) -> None:
    assert main(["validate", str(tmp_path / "nope.json")]) == EXIT_USAGE


def test_malformed_json_is_usage_not_findings(tmp_path: Path) -> None:
    p = tmp_path / "broken.json"
    p.write_text("{ not json", encoding="utf-8")
    assert main(["validate", str(p)]) == EXIT_USAGE


def test_missing_validator_is_usage_error_not_invalid(monkeypatch: pytest.MonkeyPatch,
                                                      tmp_path: Path) -> None:
    """A missing jsonschema must exit 2, never 1.

    Exit 1 means "this artifact is invalid". Reporting an absent dependency
    that way would be a false statement about the artifact — and it is what an
    unhandled ImportError does, since Python exits 1 on a traceback. Caught by
    actually installing the wheel without jsonschema and running it.
    """
    import builtins

    real_import = builtins.__import__

    def no_jsonschema(name, *args, **kwargs):
        if name == "jsonschema":
            raise ImportError("simulated: jsonschema absent")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_jsonschema)
    assert main(["validate", str(VALID_DIR / "bundle-1.0.json")]) == EXIT_USAGE
