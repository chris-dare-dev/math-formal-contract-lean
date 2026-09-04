"""Tests for `mfc lint-schemas`.

The load-bearing test is `test_shallow_walk_would_miss_nested_fixture`. A lint
with no failing case is not evidence, and a lint whose failing case a naive
implementation would also catch is not evidence that the traversal is deep.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.mfc.cli import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, main
from contract.mfc.lint import FORBIDDEN_PROPERTY_NAMES, lint_schema

HERE = Path(__file__).resolve().parent
SCHEMA_DIR = HERE.parent / "mfc" / "schema"
INVALID_DIR = HERE.parent / "testdata" / "schemas" / "invalid"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- the shipped schemas pass ------------------------------------------------

def test_shipped_schemas_are_clean() -> None:
    paths = sorted(SCHEMA_DIR.glob("*.schema.json"))
    assert paths, "no schemas found -- an empty sweep is not a pass"
    for path in paths:
        assert lint_schema(_load(path)) == [], f"{path.name} declares a forbidden name"


def test_cli_passes_on_shipped_schemas(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["lint-schemas"]) == EXIT_OK
    assert "ok:" in capsys.readouterr().out


# --- the lint actually fires -------------------------------------------------

@pytest.mark.parametrize("fixture", ["aggregate-status", "nested-verdict"])
def test_rejection_fixtures_are_rejected(fixture: str) -> None:
    findings = lint_schema(_load(INVALID_DIR / f"{fixture}.schema.json"))
    assert findings, f"{fixture} must be rejected; a lint that never fires is not a gate"


def test_cli_reports_findings_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "bad.schema.json").write_text(
        (INVALID_DIR / "aggregate-status.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert main(["lint-schemas", "--schema-dir", str(tmp_path)]) == EXIT_FINDINGS
    assert "status" in capsys.readouterr().err


# --- depth: the reason the traversal is not doc["properties"] ----------------

def test_shallow_walk_would_miss_nested_fixture() -> None:
    """The nested fixture is invisible to the traversal the design note describes.

    The note specifies "walking every `properties` key of every schema". This
    asserts that reading is inadequate — the naive walk finds nothing, and the
    real walk finds every buried name.
    """
    document = _load(INVALID_DIR / "nested-verdict.schema.json")

    naive = [n for n in document.get("properties", {}) if n in FORBIDDEN_PROPERTY_NAMES]
    assert naive == [], "fixture no longer tests depth -- it is now shallow-catchable"

    found = {f.name for f in lint_schema(document)}
    assert found == {"verdict", "confidence"}


def test_finds_names_in_defs_and_allof_and_items() -> None:
    document = {
        "$defs": {"a": {"properties": {"score": {}}}},
        "allOf": [{"properties": {"trusted": {}}}],
        "items": {"properties": {"passed": {}}},
        "patternProperties": {"^x$": {"properties": {"clean": {}}}},
        "if": {"properties": {"ok": {}}},
    }
    assert {f.name for f in lint_schema(document)} == {
        "score", "trusted", "passed", "clean", "ok",
    }


def test_definition_names_are_not_property_names() -> None:
    """A `$defs` entry *called* `status` declares no property called `status`."""
    assert lint_schema({"$defs": {"status": {"type": "string"}}}) == []


def test_additional_properties_false_is_not_a_schema() -> None:
    """`additionalProperties: false` is a boolean; the walk must not recurse it."""
    assert lint_schema({"additionalProperties": False, "properties": {}}) == []


# --- the vacuous pass --------------------------------------------------------

def test_empty_schema_dir_is_usage_error_not_success(tmp_path: Path) -> None:
    """A mis-pointed --schema-dir must not be indistinguishable from clean."""
    assert main(["lint-schemas", "--schema-dir", str(tmp_path)]) == EXIT_USAGE


def test_missing_schema_dir_is_usage_error(tmp_path: Path) -> None:
    assert main(["lint-schemas", "--schema-dir", str(tmp_path / "nope")]) == EXIT_USAGE


def test_unreadable_schema_is_usage_error_not_findings(tmp_path: Path) -> None:
    """Malformed JSON means the check did not run — exit 2, never 0."""
    (tmp_path / "broken.schema.json").write_text("{ not json", encoding="utf-8")
    assert main(["lint-schemas", "--schema-dir", str(tmp_path)]) == EXIT_USAGE


# ---------------------------------------------------------------------------
# The installed package, against the source tree it was built from.
# ---------------------------------------------------------------------------

def test_the_installed_schemas_are_the_source_schemas() -> None:
    """A wheel that ships a stale schema validates the wrong contract.

    ADR-0009 makes `mfc` a SHARED tool: the topic repo and arXMCP both install
    it, and what they install is the only copy they see. CI already installs
    the package rather than running it from the tree, for exactly this reason
    -- but "it installed" is a weaker claim than "it installed THIS".

    The failure is not hypothetical. `contract/build/` was in `.gitignore`
    from the commit that introduced packaging, and eleven files under it were
    committed in that same commit, before the ignore could take effect.
    `.gitignore` does not untrack what is already tracked, so a fresh checkout
    carried a `build/lib/` holding 2026-era copies of `cli.py`, `lint.py`,
    `validate.py` and five schemas -- and setuptools' `build_py` copies a
    source file into `build/lib` only when it is NEWER, which on a fresh
    checkout it need not be. Observed: a `pip install ./contract` that shipped
    a `resolution-1.0.schema.json` predating `not_applicable`, so a resolver
    emitting a valid document was told its own output was invalid.

    Byte-identity, not "both parse": a schema can differ by a `$comment` that
    changes nothing and by an enum member that changes everything, and only
    one of those is visible to a diff of parsed structure.
    """
    import mfc  # the INSTALLED package

    installed = Path(mfc.__file__).resolve().parent / "schema"
    source = Path(__file__).resolve().parent.parent / "mfc" / "schema"
    if installed == source:
        pytest.skip(
            "`mfc` resolves to the source tree, so there is no installed copy "
            "to compare against; CI installs the package and does the real run"
        )
    src_files = sorted(p.name for p in source.glob("*.schema.json"))
    inst_files = sorted(p.name for p in installed.glob("*.schema.json"))
    assert src_files == inst_files, "the wheel shipped a different schema SET"
    stale = [
        name for name in src_files
        if (installed / name).read_bytes() != (source / name).read_bytes()
    ]
    assert not stale, (
        f"the installed mfc ships stale schema(s): {stale}. Delete "
        f"contract/build/ and reinstall; if that fixes it, something is "
        f"tracking build output again."
    )
