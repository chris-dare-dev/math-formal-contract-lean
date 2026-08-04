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
