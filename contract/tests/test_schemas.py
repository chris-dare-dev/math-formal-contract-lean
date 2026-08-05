"""Tests for the seven contract schemas.

Three things are asserted, and the third is the one that matters:

1. Each schema is itself a valid JSON Schema 2020-12 document. A schema that
   does not compile validates nothing and would otherwise pass silently.
2. Each schema **accepts** its filled instance. Five of the seven were authored
   from a filled instance plus prose rather than transcribed, so "does it
   accept the instance it was derived from" is the only check that the
   authoring did not drift.
3. Each cross-field rule **rejects** something. A conditional that never fires
   is indistinguishable from one that is not there, and every `allOf` in these
   schemas encodes a trust rule rather than a formatting preference.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

HERE = Path(__file__).resolve().parent
SCHEMA_DIR = HERE.parent / "mfc" / "schema"
VALID_DIR = HERE.parent / "testdata" / "artifacts" / "valid"
INVALID_DIR = HERE.parent / "testdata" / "artifacts" / "invalid"

#: Which schema each rejection fixture is aimed at, and the rule it must trip.
REJECTIONS = {
    "sorry-laundered": ("declarations-1.0",
        "axioms contains sorryAx while contains_sorry_ax is false"),
    "fuzzy-current": ("resolution-1.0",
        "a fuzzy match may never be `current`"),
    "current-without-digest": ("resolution-1.0",
        "`current` requires a recomputed body digest"),
    "exact-with-divergence": ("review-1.0",
        "`exact` confirmed alongside a stated divergence"),
    "divergent-without-divergence": ("review-1.0",
        "a divergent verdict with no divergence written down"),
    "checker-pass-allow-sorry": ("build-1.0",
        "a checker that passed while permitting sorry"),
    "aggregate-verdict": ("bundle-1.0",
        "an SLSA-style aggregate verificationResult"),
}

SCHEMAS = sorted(p.name.replace(".schema.json", "") for p in SCHEMA_DIR.glob("*.schema.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema(name: str) -> dict:
    return _load(SCHEMA_DIR / f"{name}.schema.json")


def test_every_schema_is_present() -> None:
    """Nine.

    `registry-1.0` joined the original seven once the accessor work showed the
    schema was fully specified in the design note and blocked on nothing — the
    open size-ceiling decision adds an enum member and a policy, neither of
    which moves a shape.

    `workqueue-1.0` is the ninth, and it was blocked on that same decision until
    the blocker was read closely: partitioning by `kind` only bakes in an unmade
    decision if the kinds are RE-DECLARED here. They are not — `lanes` keys on a
    pattern, so a future `sketch` lane is a one-schema change. It is the only
    artifact `join` writes, and it may be written because it carries no verdict
    to aggregate.
    """
    assert SCHEMAS == [
        "build-1.0", "bundle-1.0", "declarations-1.0", "emission-1.0",
        "environment-1.0", "registry-1.0", "resolution-1.0", "review-1.0",
        "workqueue-1.0",
    ]


@pytest.mark.parametrize("name", SCHEMAS)
def test_schema_compiles(name: str) -> None:
    jsonschema.Draft202012Validator.check_schema(_schema(name))


@pytest.mark.parametrize("name", SCHEMAS)
def test_schema_forbids_extra_properties_at_top_level(name: str) -> None:
    """`additionalProperties: false` is what makes the banned-name lint bite.

    Without it a producer can add `"status": "verified"` to an artifact and
    have it validate, and the lint on the schema would never see it.
    """
    assert _schema(name).get("additionalProperties") is False


@pytest.mark.parametrize("name", SCHEMAS)
def test_schema_accepts_its_filled_instance(name: str) -> None:
    fixture = VALID_DIR / f"{name}.json"
    assert fixture.exists(), f"{name} has no filled instance to check against"
    errors = sorted(
        jsonschema.Draft202012Validator(_schema(name)).iter_errors(_load(fixture)),
        key=lambda e: list(e.path),
    )
    assert not errors, f"{name} rejects its own instance: {errors[0].json_path}: {errors[0].message}"


@pytest.mark.parametrize("fixture,expected", sorted(REJECTIONS.items()))
def test_rejection_fixture_is_rejected(fixture: str, expected: tuple[str, str]) -> None:
    schema_name, rule = expected
    doc = _load(INVALID_DIR / f"{fixture}.json")
    doc.pop("$comment_fixture", None)
    errors = list(jsonschema.Draft202012Validator(_schema(schema_name)).iter_errors(doc))
    assert errors, f"{schema_name} accepted {fixture!r}; the rule is not firing: {rule}"


def test_emission_fixture_is_real_emitter_output() -> None:
    """The emission fixture is trimmed real output, not hand-written.

    It carries `@[cites]` bindings, which is the part that silently emptied
    when `importModules` ran without `loadExts := true`.
    """
    doc = _load(VALID_DIR / "emission-1.0.json")
    assert doc["lean_version"] == "4.29.0"
    assert any(c["cites"] for c in doc["constants"]), "fixture lost its cites[]"


def test_no_schema_declares_an_aggregate_token() -> None:
    """Belt and braces over `mfc lint-schemas`, at the level that matters.

    The lint is the gate; this asserts the specific property the whole trust
    model rests on, so a regression names the right thing.
    """
    from contract.mfc.lint import lint_schema

    for name in SCHEMAS:
        assert lint_schema(_schema(name)) == [], f"{name} declares a forbidden name"


def test_an_empty_emission_is_not_a_representable_artifact() -> None:
    """The vacuous-pass guard is STRUCTURAL, not a rule.

    `constants: minItems 1` plus `counts.total/in_scope: minimum 1` mean an
    emission over zero declarations cannot be written down, so no consumer has
    to remember to check for one. `E-08` and `check-ilean-coverage`'s `I-02`
    restate the same property for a caller that skipped validation; this is the
    line that actually enforces it, and it is asserted here so that relaxing the
    schema is a visible decision rather than a silent transfer of
    responsibility to a rule that every CLI path skips.
    """
    schema = json.loads(
        (SCHEMA_DIR / "emission-1.0.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["constants"]["minItems"] == 1
    counts = schema["properties"]["counts"]["properties"]
    assert counts["total"]["minimum"] == 1
    assert counts["in_scope"]["minimum"] == 1
