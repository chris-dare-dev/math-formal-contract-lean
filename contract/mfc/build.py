"""`mfc build` — produce `build/1.0` from what the build actually reported.

## The exit code is not the measurement

`lake env lean --json` on a file whose only defect is a `sorry` **exits 0**.
Measured on `leanprover/lean4:v4.32.1`, the toolchain this package is pinned to:

```
{"kind":"hasSorry","severity":"warning","data":"declaration uses `sorry`",...}
EXIT=0
```

So a gate written against `lake_build_exit` is green on a sorry-backed proof.
`lake_build_exit` is therefore **recorded and never consulted**, and the gate is
`sorry_diagnostic_count`, counted from records whose `kind` is `hasSorry`. This
is the same failure the emitter avoids by sweeping `Environment.constants`
instead of parsing source — one level down, in the build log rather than in the
declaration.

## A `kind` this build does not recognize is a hard failure

Counting by `kind` has one way to go quietly wrong: if a future toolchain
renames `hasSorry` or drops the field, every sorry becomes an uncounted warning
and `sorry_diagnostic_count` reads `0`. That is the vacuous pass in build-log
form. `_assert_sorry_kind_still_works` therefore cross-checks the *text*: a
diagnostic that reads like a sorry but is not tagged `hasSorry` raises, rather
than being silently excluded from the count it belongs in.

The text check is a tripwire, never the counter. Counting from prose would
break the moment Lean rewords the message, which is precisely why the `kind`
field exists.

## A clean build and an unmeasured build are the same document

`error_count`, `warning_count` and `sorry_diagnostic_count` are all `0` whether
the build was spotless or the NDJSON covered one module of 494. Nothing else in
`build/1.0` can tell those apart, which makes an unmeasured build the most
flattering artifact this package could emit — the same absence
`check-ilean-coverage` exists for, one level down in the build log.

`measured` is the field that separates them. Its numerator is **declared** by
the caller, because a module that elaborated cleanly leaves no trace in the
NDJSON and cannot be recovered from it. Its denominator is **read from the
emission**, never from the caller, so the one number that could be shrunk to
flatter the other cannot be. A declared module absent from the emission's
`modules[]` is a hard failure: coverage may be narrow, and it may not be
invented.

No ratio is stored. A reader divides two integers; a single `coverage: 100%`
would be the collapsed trust token every schema here refuses, and it would be
the first thing a tired reader believed.

## Nothing here is inferred from a partial stream

A line that is not a JSON object, or an object missing a field the schema
requires, raises. A diagnostic that could not be parsed is a diagnostic that
would not be counted, and an undercount is indistinguishable from a clean
build. Feed this the NDJSON stream alone — `lake build`'s human-readable
progress output on the same handle is a caller error, and it is reported as
one.

## `end_pos` when Lean reports none

`endPos` is nullable in Lean's message JSON while `build/1.0` requires
`end_pos`. A message with no end is recorded as a zero-width range at `pos`
rather than dropped or invented wider: the position is real, the extent is
simply not something the compiler said.
"""

from __future__ import annotations

import json
from typing import Any

#: The `kind` Lean tags every "declaration uses `sorry`" warning with. Load
#: bearing: `sorry_diagnostic_count` is counted from this and nothing else.
SORRY_KIND = "hasSorry"

#: Substrings that mean "this message is about a sorry" in prose. Used ONLY to
#: detect that `SORRY_KIND` has stopped matching what it is supposed to match.
#: Both quoting styles, because Lean has used each.
SORRY_TEXT_MARKERS = ("uses `sorry`", "uses 'sorry'", 'uses "sorry"')

#: Severities that feed the two counts. Anything else (`information`, and
#: whatever a later toolchain adds) is kept in `diagnostics` and counted in
#: neither, rather than being folded into `warning_count` to make the totals
#: add up.
ERROR_SEVERITY = "error"
WARNING_SEVERITY = "warning"

#: `--checker name:version:value:allow_sorry`.
CHECKER_FIELDS = 4

_VALUES = frozenset({"pass", "fail", "not_run", "not_applicable"})


class BuildError(RuntimeError):
    """The build log could not be read. Never a partial artifact."""


def _pos(raw: Any, *, line_no: int, field: str) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise BuildError(f"line {line_no}: {field} is {type(raw).__name__}, not an object")
    try:
        return {"line": int(raw["line"]), "column": int(raw["column"])}
    except (KeyError, TypeError, ValueError) as exc:
        raise BuildError(f"line {line_no}: {field} has no usable line/column: {exc}") from exc


def _assert_sorry_kind_still_works(kind: str, data: str, *, line_no: int) -> None:
    """Raise when a message reads like a sorry but is not tagged as one.

    The count is by `kind`. This exists so that a toolchain which renames or
    drops that tag fails loudly here instead of reporting zero sorries.
    """
    if kind == SORRY_KIND:
        return
    if any(marker in data for marker in SORRY_TEXT_MARKERS):
        raise BuildError(
            f"line {line_no}: a diagnostic reads {data.strip()!r} but its kind is "
            f"{kind!r}, not {SORRY_KIND!r}. sorry_diagnostic_count is counted from "
            f"kind alone, so this toolchain would report zero sorries for a build "
            f"that has one. Update SORRY_KIND before trusting this build.json")


def parse_ndjson(text: str) -> list[dict[str, Any]]:
    """One `build/1.0` diagnostic per non-blank line of Lean's `--json` output."""
    out: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BuildError(
                f"line {line_no} is not JSON ({exc}). This reads the NDJSON stream "
                f"from `lake env lean --json` alone; lake's progress output on the "
                f"same handle has to be separated out first") from exc
        if not isinstance(raw, dict):
            raise BuildError(f"line {line_no} is a {type(raw).__name__}, not an object")

        try:
            file_name, severity = str(raw["fileName"]), str(raw["severity"])
        except KeyError as exc:
            raise BuildError(f"line {line_no}: no {exc.args[0]}") from exc
        data = raw.get("data")
        if not isinstance(data, str):
            raise BuildError(f"line {line_no}: data is {type(data).__name__}, not a string")

        # Absent rather than empty is the honest reading of a message that
        # declared no kind; it is not silently treated as a sorry either way.
        kind = str(raw.get("kind") or "")
        _assert_sorry_kind_still_works(kind, data, line_no=line_no)

        pos = _pos(raw.get("pos"), line_no=line_no, field="pos")
        end_raw = raw.get("endPos")
        end_pos = pos if end_raw is None else _pos(end_raw, line_no=line_no, field="endPos")

        out.append({
            "file_name": file_name,
            "severity": severity,
            "kind": kind,
            "pos": pos,
            "end_pos": end_pos,
            "data": data,
        })
    return out


def parse_checker(spec: str) -> dict[str, Any]:
    """`name:version:value:allow_sorry` -> one `independent_checkers[]` entry."""
    parts = spec.split(":")
    if len(parts) != CHECKER_FIELDS:
        raise BuildError(
            f"--checker {spec!r}: expected name:version:value:allow_sorry, "
            f"got {len(parts)} field(s)")
    name, version, value, allow = (p.strip() for p in parts)
    if value not in _VALUES:
        raise BuildError(f"--checker {spec!r}: value must be one of "
                         f"{', '.join(sorted(_VALUES))}, not {value!r}")
    if allow not in ("true", "false"):
        raise BuildError(f"--checker {spec!r}: allow_sorry must be true or false, "
                         f"not {allow!r}")
    if not name:
        raise BuildError(f"--checker {spec!r}: name is empty")
    # The schema enforces this too. Saying it here names the reason, which a
    # schema violation on `allow_sorry: const false` does not.
    if value == "pass" and allow == "true":
        raise BuildError(
            f"--checker {spec!r}: a checker that passed WHILE PERMITTING sorry has "
            f"not checked the thing this axis is about; record it as not_applicable")
    return {"name": name, "version": version, "value": value,
            "allow_sorry": allow == "true"}


def measured_scope(
    emission: dict[str, Any],
    *,
    covers: list[str] | None,
    covers_all: bool,
) -> dict[str, Any]:
    """The `measured` block: what the NDJSON covered, out of what exists.

    `covers_all` is a claim, not an observation — no tool here can watch which
    modules a log was produced over. It is spelled as its own flag so that
    claiming total coverage is a deliberate act rather than the default that
    happens when nobody passes anything.
    """
    in_scope = emission.get("modules")
    if not (isinstance(in_scope, list) and in_scope):
        raise BuildError(
            "the emission carries no modules[]; without it there is no honest "
            "denominator for what this build measured")

    if covers_all:
        covered = sorted(set(in_scope))
    else:
        if not covers:
            raise BuildError(
                "nothing declared as covered. A build.json whose diagnostics "
                "cover no module reports a spotless build having measured "
                "nothing; pass --covers, or --covers-all if the log really is "
                "over every in-scope module")
        unknown = sorted(set(covers) - set(in_scope))
        if unknown:
            raise BuildError(
                f"declared as covered but not in the emission's modules[]: "
                f"{', '.join(unknown)}. Coverage may be narrow; it may not be "
                f"invented")
        covered = sorted(set(covers))

    return {"modules": covered, "in_scope_modules": len(set(in_scope))}


def build(
    ndjson: str,
    *,
    environment: dict[str, Any],
    emission: dict[str, Any],
    lake_build_exit: int,
    lake_build_jobs: int,
    covers: list[str] | None = None,
    covers_all: bool = False,
    independent_checkers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read a build log and return a complete `build/1.0` document."""
    env_digest = environment.get("env_digest")
    if not isinstance(env_digest, str):
        raise BuildError("environment.json carries no env_digest; the build cannot "
                         "say which environment it measured")
    if lake_build_jobs < 0:
        raise BuildError(f"lake_build_jobs is {lake_build_jobs}; a build ran no "
                         f"fewer than zero jobs")

    measured = measured_scope(emission, covers=covers, covers_all=covers_all)
    diagnostics = parse_ndjson(ndjson)
    return {
        "schema_version": "build/1.0",
        "env_digest": env_digest,
        "lake_build_exit": lake_build_exit,
        "lake_build_jobs": lake_build_jobs,
        "measured": measured,
        "diagnostics": diagnostics,
        "error_count": sum(1 for d in diagnostics if d["severity"] == ERROR_SEVERITY),
        "warning_count": sum(1 for d in diagnostics if d["severity"] == WARNING_SEVERITY),
        "sorry_diagnostic_count": sum(1 for d in diagnostics if d["kind"] == SORRY_KIND),
        "independent_checkers": list(independent_checkers or []),
    }
