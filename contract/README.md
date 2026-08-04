# `contract/` — the schemas, and the tooling that polices them

The machine-readable half of the contract between a corpus of papers and a Lean
formalization of results from it. The Lean half — `@[cites]` and the emitter —
is the rest of this repo.

## Why it lives here

[`ADR-0009`](https://github.com/chris-dare-dev/bridgeland-stab-lean/blob/main/.claude/decisions/ADR-0009-contract-package-lives-with-the-emitter.md),
which replaced ADR-0007 after the latter's cited evidence was found not to
exist. Short version: this is the only candidate location whose justification
survives contact with the disk, arXMCP's own constitution says it does not host
formalization work, and the schema for `emission/1.0` now sits in the same
commit as the emitter that produces it.

**The zero-dependency invariant is untouched.** It is about the *Lake* package —
`lakefile.toml` carries no `[[require]]`, and CI enforces that by grepping that
file. JSON and Python appear in neither. If anyone ever adds a Lake `require`
to serve this directory, the named exception in the consuming repo's
`CLAUDE.md` §1 lapses and the dependency comes out of every topic repo.

## What is here today

| path | what |
|---|---|
| `schema/emission-1.0.schema.json` | the emitter's output format |
| `mfc/lint.py` | the banned-property lint |
| `mfc/cli.py` | `mfc lint-schemas` |
| `testdata/schemas/invalid/` | rejection fixtures |
| `tests/` | pytest |

Six schemas are still to come — `environment`, `declarations`, `review`,
`build`, `bundle`, `resolution`. Only `emission` was fully specified in the
design note; two of the others are near-full and four are filled instances plus
prose, so they are authoring work rather than transcription.

## Running it

```bash
python3 -m contract.mfc.cli lint-schemas     # from the repo root
python3 -m pytest contract/tests -q
```

Exit codes are the contract with CI and are fixed: `0` clean, `1` findings,
`2` usage or environment error. The gap between 1 and 2 is load-bearing — a job
that reads "the linter crashed" as "the linter passed" is the vacuous pass this
project exists to prevent, which is why an empty or mis-pointed `--schema-dir`
is an error rather than a silent success.

There is no `pyproject.toml` yet. `mfc` is invoked as a module. ADR-0009 makes
it a *shared* tool, so it will need to be installable before the consuming repo
can use it; that is a follow-up, not an oversight.

## `lint-schemas`, and why the traversal is deep

arXMCP's `CLAUDE.md` §4.9 forbids any single token that collapses distinct
trust questions. A schema declaring a property named `status` or `verified`
hands a producer that token back, so declaring one fails the build.

The design note specifies this as "walking every `properties` key of every
schema". That reading is inadequate against the first schema it would be run
on: `emission/1.0` declares `constant` and `cite` under `$defs`, so a top-level
`doc["properties"]` walk never sees `relation_claimed`, `axioms`, `type_pp` or
anything else that matters. The walk therefore recurses through `$defs`,
`allOf`/`anyOf`/`oneOf`, `items`, `patternProperties`, `if`/`then`/`else` and
the rest. `$ref` is deliberately not followed — it points at a definition the
walk already visits, and following it would double-report and can cycle.

`testdata/schemas/invalid/nested-verdict.schema.json` exists to hold that line:
every forbidden name in it is buried where the naive walk cannot see it, and a
test asserts the naive walk finds **nothing** in it. If that test ever starts
passing for the wrong reason, the fixture has stopped testing depth.

**The banned set is 13 names, not 6.** The design note's literal
`FORBIDDEN_PROPERTY_NAMES` is 13; issues #19 and #21 quote 6. The 13-name set
is a strict superset and is the literal artifact specified, so it is what
ships. The discrepancy is recorded rather than silently resolved, because this
list *is* the rule being mechanised.
