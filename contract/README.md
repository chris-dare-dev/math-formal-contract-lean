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
| `schema/*.schema.json` | **all seven** artifact formats |
| `mfc/lint.py` | the banned-property lint |
| `mfc/cli.py` | `mfc lint-schemas` |
| `testdata/schemas/invalid/` | schemas that must fail the lint |
| `testdata/artifacts/valid/` | one filled instance per schema |
| `testdata/artifacts/invalid/` | artifacts that must fail a cross-field rule |
| `tests/` | pytest |

### How much of each schema was given, and how much was authored

Only `emission` was fully copy-pasteable from the design note. This matters for
review: the rest are **authored**, and authoring can drift from intent in a way
transcription cannot.

| schema | source | check that it did not drift |
|---|---|---|
| `emission` | full JSON | fixture is trimmed **real emitter output** |
| `review` | near-full JSON, missing only the wrapper | accepts the note's instance |
| `resolution` | `$defs.result` full; envelope inferred from the instance | accepts the note's instance |
| `declarations` | instance + one `allOf` fragment | accepts the note's instance |
| `build` | instance + one `if/then` fragment | accepts the note's instance |
| `environment` | instance + prose bullets, no JSON | accepts the note's instance |
| `bundle` | instance + prose bullets, no JSON | accepts the note's instance |

### The rules that carry the trust model

Every `allOf` in these schemas encodes a trust rule, not a formatting
preference, and each has a fixture that must be **rejected** — a conditional
that never fires is indistinguishable from one that is not there:

| fixture | what it smuggles |
|---|---|
| `sorry-laundered` | `sorryAx` in `axioms` while `contains_sorry_ax` is false |
| `fuzzy-current` | a fuzzy match claiming `current` — similarity is not identity |
| `current-without-digest` | `current` without the recomputed body digest |
| `exact-with-divergence` | `relation_confirmed: exact` alongside a divergence |
| `divergent-without-divergence` | a divergent verdict that does not say how |
| `checker-pass-allow-sorry` | a checker that passed *while permitting* sorry |
| `aggregate-verdict` | an SLSA-style `verificationResult` on the bundle |

The last one is why `additionalProperties: false` is everywhere rather than
just tidy: it is what stops a single collapsed trust token being added to an
artifact at all.

### One judgement call worth flagging

The note's filled instances carry `$comment` keys as reader documentation.
Those are **not** in the schemas, so a real artifact may not contain them —
they were annotations for a human, not fields. If they were meant to survive
into the artifacts, the schemas need `$comment` allowed explicitly.

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
