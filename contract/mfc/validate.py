"""`mfc validate` — check an artifact against its schema.

## The schema is inferred, not asked for

Every contract artifact carries `schema_version` as its first key. `validate`
reads it and picks the schema from that, rather than taking a `--schema` flag.

That is deliberate: it makes the artifact's **self-description** part of what
is checked. An artifact claiming `emission/1.0` is validated against
`emission/1.0` and cannot be quietly checked against something laxer, and an
artifact whose `schema_version` names a version this build does not carry is a
hard failure rather than a silent skip — which is the
`ContractVersionUnsupported` behaviour the design calls for. There is no
tolerant mode.

`--schema` exists only to override for debugging, and saying so is the point:
if you have to pass it, something is already wrong.

## All errors, not the first

`jsonschema`'s `iter_errors` is used rather than `validate`. A validator that
stops at the first problem turns fixing a bad artifact into N round trips, and
for a cross-field `allOf` the first error is often the least informative one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NamedTuple

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"


class LoadError(Exception):
    """The artifact could not be read or parsed. Distinct from being invalid."""


class CapabilityError(LoadError):
    """This build cannot read the artifact — a missing optional dependency.

    A subclass of `LoadError` so every existing catch site keeps working, but
    distinguishable, because the two facts differ in the only way that matters:
    a malformed artifact is a **finding**, while a reader this build does not
    have means the check **did not run**. `mfc conformance` reports the second
    as `not_run` rather than failing a file it never managed to look at.
    """


def schema_path_for(version: str) -> Path:
    """`emission/1.0` -> `schema/emission-1.0.schema.json`."""
    if "/" not in version:
        raise LoadError(f"schema_version {version!r} is not of the form <name>/<major.minor>")
    name, _, ver = version.partition("/")
    return SCHEMA_DIR / f"{name}-{ver}.schema.json"


def load_artifact(path: Path) -> Any:
    """Parse a JSON or YAML artifact.

    `attest/review.yaml` is genuinely YAML, so YAML is supported when PyYAML is
    installed. When it is not, that is reported as a missing capability rather
    than as an invalid artifact — the two are different facts and a caller
    must be able to tell them apart.
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # noqa: PLC0415 - optional dependency, imported on demand
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise CapabilityError(
                f"{path.name} is YAML but PyYAML is not installed; "
                f"install the 'yaml' extra (pip install 'mfc[yaml]')"
            ) from exc
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise LoadError(f"invalid YAML: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LoadError(f"invalid JSON: {exc}") from exc


class Problem(NamedTuple):
    location: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.location}: {self.message}"


def validate_artifact(document: Any, schema: dict) -> list[Problem]:
    """Every way `document` fails `schema`, not just the first.

    A missing `jsonschema` raises `LoadError`, never propagates. An unhandled
    ImportError exits 1, and 1 means "this artifact is invalid" -- so a missing
    dependency would be reported as a bad artifact. "I could not check this"
    and "this is wrong" are different facts and must not share an exit code.
    """
    try:
        import jsonschema  # noqa: PLC0415 - keeps `mfc lint-schemas` import-light
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise LoadError(
            "jsonschema is not installed, so nothing was validated. "
            "Install it (pip install mfc) -- this is NOT a statement about the artifact."
        ) from exc

    validator = jsonschema.Draft202012Validator(schema)
    return [
        Problem(error.json_path, error.message)
        for error in sorted(validator.iter_errors(document), key=lambda e: list(e.path))
    ]
