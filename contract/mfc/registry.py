"""Reading a `registry/1.0` document.

One small module because three rules in two files need the same access, and
because they previously each guessed — `E-04`, the registry half of `E-05`, and
`J-06` all read `registry["statements"]` as a list of `{key, frontier}`. That
shape never existed. The specified document keys `entries` **by citation key**:

```yaml
schema_version: "registry/1.0"
registry_id: "9f4c1a20b7d3"
entries:
  "stmt:9f4c1a20b7d3:bridgeland2007.lem-8.2":
    kind: lemma
    frontier:
      - id: gltilde-universal-cover
        kind_class: missing-library
        statement: "..."
        discharged_by: null          # <- null means OPEN
```

Two consequences the invented shape hid:

* the keys are **property names**, not a field, so a key-existence check is a
  membership test on a dict rather than a projection over a list;
* `frontier[]` holds **objects**, and an item is open only when
  `discharged_by` is `null`. A non-empty `frontier` is therefore not the same
  as an open frontier — an entry whose items are all discharged has nothing
  outstanding, and `E-05` was previously refusing `exact` on those.

## An unrecognised registry reports `not_run`, never a finding

There is no `registry-1.0.schema.json` in this build — the registry is gated on
the open size-ceiling decision — so nothing validates the file before these
rules read it. That makes the failure mode specific: a document without an
`entries` object would yield an empty key set, and `E-04` would then report
every citation in the repository as unknown while `J-06` reported a clean
work queue over zero entries. A flood of false findings and a vacuous pass from
the same bad input.

So a shape this build does not recognise raises, and the caller turns it into
`not_run` with the reason. Same rule as an unrecognised `.ilean` layout.

## Why this is safe to write before the decision lands

The open question is the registry **size ceiling** and whether to add a
`kind: sketch` lane. Adding an enum member is a MINOR bump that flows through
`kind` untouched, and a ceiling is a policy rather than a shape. Neither moves
`entries{}` or `frontier[]`, so this accessor is decision-independent.
"""

from __future__ import annotations

from typing import Any


class RegistryShapeError(Exception):
    """This build does not recognise the document as a registry."""


def entries(registry: Any) -> dict[str, dict]:
    """`{citation key: entry}`, or raise.

    Raises rather than returning `{}` so an unreadable registry cannot present
    as an empty one — see the module docstring.
    """
    if not isinstance(registry, dict):
        raise RegistryShapeError(
            f"expected a registry object, got {type(registry).__name__}")
    if "entries" not in registry:
        hint = ""
        if "statements" in registry:
            hint = (" (it has `statements`; registry/1.0 keys `entries` by "
                    "citation key, and no released schema ever had `statements`)")
        raise RegistryShapeError(
            f"no `entries` object{hint}. Refusing to read it as an empty "
            f"registry: that would report every citation as unknown AND an "
            f"empty work queue as clean, from the same bad input")
    got = registry["entries"]
    if not isinstance(got, dict):
        raise RegistryShapeError(
            f"`entries` must be an object keyed by citation key, got "
            f"{type(got).__name__}")
    for key, entry in got.items():
        if not isinstance(entry, dict):
            raise RegistryShapeError(
                f"entry {key!r} is {type(entry).__name__}, not an object")
    return got


def open_frontier(entry: dict) -> list[str]:
    """Ids of this entry's frontier items that are **not** discharged.

    `discharged_by: null` is the open state. A non-empty `frontier` whose items
    are all discharged leaves nothing outstanding, which is why this is a
    filter rather than a length check.
    """
    return [item["id"] for item in entry.get("frontier") or []
            if isinstance(item, dict) and item.get("discharged_by") is None]


def kind_of(entry: dict) -> str:
    """The entry's `kind`, or `unknown` — never guessed from anything else."""
    kind = entry.get("kind")
    return kind if isinstance(kind, str) else "unknown"
