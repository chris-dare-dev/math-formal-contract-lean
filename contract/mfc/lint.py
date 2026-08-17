"""`mfc lint-schemas` — the banned-property lint.

arXMCP's `CLAUDE.md` §4.9 forbids any single token that collapses distinct
trust questions into one. A schema that declares a property named `status` or
`verified` hands a producer that token back. This module is what turns that
policy from a review habit into a machine constraint: a schema declaring a
banned name fails, in CI, without anyone having to notice.

## The traversal is deep, and that is not gold-plating

The design note specifies this check as "walking every `properties` key of
every schema in `schema/`". That is the shallow reading, and it is inadequate
against the very first schema it would be run on: `emission/1.0` puts
`constant` and `cite` in `$defs`, so a top-level `doc["properties"]` walk never
sees `relation_claimed`, `axioms`, `type_pp`, or any of the other names that
actually matter.

A banned name can be declared under any of:

    properties, patternProperties, $defs, definitions,
    allOf/anyOf/oneOf branches, items, prefixItems, contains,
    additionalProperties/unevaluatedProperties when they are schemas,
    if/then/else, not, dependentSchemas, propertyNames

so the walk recurses through all of them. `$ref` is deliberately NOT followed —
a `$ref` points at a definition this walk already visits directly, and
following it would double-report and can cycle.

## Why 13 names and not 6

The design note's literal `FORBIDDEN_PROPERTY_NAMES` is 13 names; GitHub
issues #19 and #21 quote 6. The 13-name set is a strict superset and is the
literal artifact the design specifies, so it is what is implemented. The
discrepancy is recorded here rather than silently resolved, because this list
*is* the rule being mechanised — if the shorter list was intended, this is the
one place that has to change.
"""

from __future__ import annotations

from typing import Any, Iterator, NamedTuple

#: Property names a contract schema may never declare. See the module docstring
#: for why this is the 13-name set rather than the 6-name one.
FORBIDDEN_PROPERTY_NAMES = frozenset({
    "status", "verified", "ok", "passed", "pass", "trusted", "result",
    "verdict", "score", "confidence", "valid", "success", "clean",
})

#: Keywords whose value is a *map of name to schema*. The keys are declared
#: property names and are what the lint judges.
_PROPERTY_MAPS = ("properties", "patternProperties", "dependentSchemas")

#: Keywords whose value is a *map of name to schema*, where the keys are
#: definition names rather than property names — recursed into, not judged.
_SCHEMA_MAPS = ("$defs", "definitions")

#: Keywords whose value is a *list of schemas*.
_SCHEMA_LISTS = ("allOf", "anyOf", "oneOf", "prefixItems")

#: Keywords whose value is a *single schema*.
_SCHEMA_VALUES = (
    "items", "contains", "not", "if", "then", "else",
    "additionalProperties", "unevaluatedProperties", "propertyNames",
    "additionalItems", "unevaluatedItems",
)


class Finding(NamedTuple):
    """One banned property name, and where it was declared."""

    path: str
    """JSON-pointer-ish location, e.g. `$defs.cite.properties.status`."""
    name: str
    """The offending property name."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.path}: declares forbidden property name {self.name!r}"


def _walk(node: Any, path: str) -> Iterator[Finding]:
    """Yield a Finding for every banned property name declared under `node`."""
    if isinstance(node, list):
        for i, item in enumerate(node):
            yield from _walk(item, f"{path}[{i}]")
        return
    if not isinstance(node, dict):
        return

    for keyword in _PROPERTY_MAPS:
        block = node.get(keyword)
        if isinstance(block, dict):
            for name, subschema in block.items():
                if name in FORBIDDEN_PROPERTY_NAMES:
                    yield Finding(f"{path}{keyword}.{name}", name)
                yield from _walk(subschema, f"{path}{keyword}.{name}.")

    for keyword in _SCHEMA_MAPS:
        block = node.get(keyword)
        if isinstance(block, dict):
            for name, subschema in block.items():
                yield from _walk(subschema, f"{path}{keyword}.{name}.")

    for keyword in _SCHEMA_LISTS:
        block = node.get(keyword)
        if isinstance(block, list):
            for i, subschema in enumerate(block):
                yield from _walk(subschema, f"{path}{keyword}[{i}].")

    for keyword in _SCHEMA_VALUES:
        subschema = node.get(keyword)
        # `additionalProperties: false` is a boolean, not a schema; skip it.
        if isinstance(subschema, (dict, list)):
            yield from _walk(subschema, f"{path}{keyword}.")


def lint_schema(document: Any) -> list[Finding]:
    """Every banned property name declared anywhere in `document`."""
    return list(_walk(document, ""))


# ---------------------------------------------------------------------------
# The volatile-property lint — #160.
# ---------------------------------------------------------------------------
#
# `git diff --exit-code attest/` is one of four claimed sorry-gates and the
# only one that survives the seam. It asserts that the committed attestations
# are byte-identical to the ones this run produced, which is what makes them
# evidence rather than decoration.
#
# A `produced_at` inside a committed artifact makes that gate red on every
# no-op commit, and a gate that is red on no-op commits is deleted within the
# week — by someone who is right that it is broken and wrong about which half
# to remove. So the field is not zeroed, and it is not tolerated: it moves to
# `run/1.0`, which is not committed.
#
# This lint is what stops it coming back.

#: Property names that change on a run CI performs automatically, and so may
#: not appear in an artifact CI both regenerates and commits.
VOLATILE_PROPERTY_NAMES = frozenset({
    "produced_at", "emitted_at", "generated_at", "built_at",
    "timestamp", "started_at", "finished_at",
    "run_url", "run_id", "job_id", "runner",
})

#: The artifacts CI regenerates on EVERY commit and commits the result of.
#: This is the whole of the rule, and the reason it is a set of four rather
#: than "every schema":
#:
#: * `emission` is regenerated every run but is NOT committed, so its
#:   `emitted_at` costs nothing;
#: * `run` is where the volatile fields were moved TO;
#: * `review` carries `reviewed_at` and `resolution` carries `generated_at`.
#:   Both are committed — and both are produced by a deliberate human or
#:   offline act, not by CI on every commit. Their timestamps are *content*:
#:   the date a person reviewed something is the fact being recorded. Banning
#:   them would delete evidence to protect a gate they do not threaten.
CI_REGENERATED_ARTIFACTS = frozenset({
    "declarations", "environment", "bundle", "build",
})


def artifact_name(schema_filename: str) -> str:
    """`bundle-1.0.schema.json` -> `bundle`. `''` when the name says nothing."""
    stem = schema_filename.split(".", 1)[0]
    return stem.rsplit("-", 1)[0] if "-" in stem else ""


def lint_volatile(document: Any, schema_filename: str) -> list[Finding]:
    """Volatile property names declared by a committed, CI-regenerated artifact.

    Keyed on the schema's filename because that is what names the artifact.
    A schema whose filename does not identify one of `CI_REGENERATED_ARTIFACTS`
    is not judged here at all — this lint has one job and does not creep into
    being an opinion about timestamps in general.
    """
    if artifact_name(schema_filename) not in CI_REGENERATED_ARTIFACTS:
        return []
    return [f for f in _walk_names(document, "", VOLATILE_PROPERTY_NAMES)]


def _walk_names(node: Any, path: str, names: frozenset[str]) -> Iterator[Finding]:
    """`_walk`, over an arbitrary name set.

    `_walk` is left keyed to `FORBIDDEN_PROPERTY_NAMES` rather than
    parameterised, because that lint is quoted by name in two repos' rules and
    its signature is part of what they quote.
    """
    if isinstance(node, list):
        for i, item in enumerate(node):
            yield from _walk_names(item, f"{path}[{i}]", names)
        return
    if not isinstance(node, dict):
        return

    for keyword in _PROPERTY_MAPS:
        block = node.get(keyword)
        if isinstance(block, dict):
            for name, subschema in block.items():
                if name in names:
                    yield Finding(f"{path}{keyword}.{name}", name)
                yield from _walk_names(subschema, f"{path}{keyword}.{name}.", names)

    for keyword in _SCHEMA_MAPS:
        block = node.get(keyword)
        if isinstance(block, dict):
            for name, subschema in block.items():
                yield from _walk_names(subschema, f"{path}{keyword}.{name}.", names)

    for keyword in _SCHEMA_LISTS:
        block = node.get(keyword)
        if isinstance(block, list):
            for i, subschema in enumerate(block):
                yield from _walk_names(subschema, f"{path}{keyword}[{i}].", names)

    for keyword in _SCHEMA_VALUES:
        subschema = node.get(keyword)
        if isinstance(subschema, (dict, list)):
            yield from _walk_names(subschema, f"{path}{keyword}.", names)
