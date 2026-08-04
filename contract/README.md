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
| `mfc/schema/*.schema.json` | **all seven** artifact formats |
| `mfc/digest.py` | the four canonical digest functions — **frozen** |
| `mfc/lint.py` | the banned-property lint |
| `mfc/validate.py` | artifact validation |
| `mfc/bundle.py` | `declarations.json`, everything recomputed |
| `mfc/rules.py` | the E-01..E-10 content rules |
| `mfc/conformance.py` | the C-01..C-12 cross-artifact rules |
| `mfc/cli.py` | `mfc lint-schemas`, `validate`, `bundle`, `lint`, `conformance` |
| `pyproject.toml` | packaging, so a consumer can install it |
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

## Installing and running it

```bash
pip install ./contract            # from the repo root
pip install './contract[yaml]'    # adds attest/review.yaml support

mfc lint-schemas                  # works from any directory once installed
mfc validate attest/lean-emission.json
```

ADR-0009 made `mfc` a **shared** tool — "rule 7", which had confined it to the
corpus server's side, came from the document that does not exist. So a topic
repo installs this rather than vendoring schema copies with a checksum-drift
test, and the drift problem vendoring solved does not arise while the schemas
and the emitter share a repo and a commit.

Two placement notes, both deliberate:

* **`pyproject.toml` is under `contract/`, not at the repo root.** The root is a
  Lake package; a `pyproject.toml` beside `lakefile.toml` invites tooling on
  either side to treat the other's tree as its own.
* **The schemas live inside the package**, at `mfc/schema/`. An installed CLI
  that cannot find its schemas is useless. They are still plain JSON at a
  stable, greppable path for any non-Python consumer.

Neither adds a Lake dependency, so the zero-dependency invariant is untouched.

## `lint`: a rule that did not run is not a rule that passed

Schema validation says an artifact is well *formed*. The E-rules say whether
what it contains is *allowed* — a perfectly well-formed emission can still
launder a `sorry`, declare an undeclared axiom, or cite a key that does not
exist.

Two rules cannot run yet. `E-04` needs the statement registry, which is gated
on the open size-ceiling decision, and `E-09` needs the topic's `closed_lanes`
configuration. They report **`not_run`**, never `pass`, and every invocation
prints which rules did not run instead of folding them into a count:

```
7 passed, 0 failed, 3 not_run
note: E-04, E-05, E-09 did NOT run. This is not a pass -- nothing checked
      what they check.
```

That is the trust-axis rule applied one level down. A green lint with
`E-04: not_run` must not be readable as "the citations resolve". `--require-all`
turns any `not_run` into a failure, for the day the inputs exist.

`E-05` shows the other half of the idea: its emission half (a citation claiming
`exact` while carrying its own open frontier) runs now and *fails* now; only
the registry half is deferred. A missing input suppresses exactly what it must
and no more.

Every rule has a fixture in `testdata/emissions/invalid/` that must trip it.

**`E-07` earns its keep twice.** It was written so two different statements
cannot hash identically. It also turns out to select exactly the statements
that cannot be re-elaborated — measured at 339/339 round-trip on elision-free
statements and 0/61 on elided ones — so it is already the precondition
`--restate-check` needs.

## `conformance`: seven valid artifacts that do not describe the same thing

`validate` asks whether one artifact is well formed. `lint` asks whether one
emission contains anything forbidden. Neither can see the failure this command
exists for — a `build.json` measured before the last commit, a review performed
against an earlier environment, a `declarations.json` derived from an emission
other than the one shipped. Each file passes its own schema. The set is still a
lie.

Twelve rules, all of them a link between two artifacts rather than a property of
one. `C-02` (a predicate's `sha256` is the file's actual bytes) is the load
bearer: without it every other link is between things the bundle merely
asserts. `C-07` is the only rule that checks a digest against the data it
summarizes rather than against another digest.

### It writes no artifact, and that is the point

The obvious shape is to emit `conformance.json` with a verdict in it. It
deliberately does not, because there is nowhere honest to put one — a
`conformance.json` carrying a top-level verdict *is* the `aggregate-verdict`
rejection fixture, mechanically produced by our own tool.

So the output is a report and an exit code, and the reviewable object is an
evidence table:

```
evidence
  environment                attest/environment.json     mfc/1.0.0                   self-attested  this environment
  human-review               attest/review.json          human:Chris Dare            independent    this environment
  corpus-resolution          attest/resolution.json      arxmcp/statement_resolve..  independent    n/a (not a Lean measurement)
  provisional-self-reported  attest/lean-verify-tran..   arxmcp/lean_verify@v4.31.0  self-attested  OTHER environment (ffffffff...)

6 predicate(s); 2 not self-attested; 1 produced in another environment
```

Three counts, printed separately and never combined, because "6 predicates"
must not be readable as "6 independent measurements of this build". No column
of that table is a score, and `test_no_row_carries_a_verdict` keeps it that way.

### Out-of-environment evidence is labelled, not averaged in

The reference bundle carries a transcript produced by a **v4.31.0** toolchain
against a repo pinned to **v4.29.0**. That is legitimate and must stay visibly
separate, which is what the `provisional-self-reported` predicate type is for.
`C-05` makes the label binding: a `build/v1` predicate carrying a foreign
`env_digest` is a measurement of another build presented as a measurement of
this one.

There is one way to satisfy `C-05` without fixing anything — relabel the
predicate as provisional. That is the intended escape, not a hole: relabelling
*is* the retraction, and the table then shows a claim demoted to nothing.

### `C-12` and the vacuous pass, one level up

Every other rule checks what is *present*. A bundle that simply omits its
`build/v1` predicate therefore satisfies all eleven and reports "5
predicate(s)". `C-12` is the only rule that can see an absence, and
`test_c12_a_bundle_that_simply_omits_its_build_predicate` asserts that no other
rule notices — which is why it exists.

### Not yet in this repo's CI, and why that is stated rather than faked

CI here produces an emission and a `declarations.json`. Nothing assembles a
`bundle.json`, and there is no `environment.json` or `build.json`, so there is
nothing for `conformance` to run against. A CI step pointed at a file no step
produces would fail for the wrong reason; a step that tolerated its absence
would be the vacuous pass. It runs in pytest against a generated coherent tree
until a bundle exists.

### The fixture tree is built, not checked in

`C-02` compares a predicate's `sha256` to real bytes, so a checked-in `attest/`
tree would need its digests hand-maintained on every edit. The cost of
generating it is that a generator and a checker sharing a misconception agree
with each other — which is what the mutation tests are for. Each takes the
coherent tree, breaks exactly one link, and requires the matching rule to
notice.

Building that tree found a real defect in a checked-in fixture. `C-07`
recomputes the environment digest from the environment's own fields, and
`environment-1.0.json` declared a digest computed over **fourteen** packages
while listing **three** — the design note's instance, trimmed for readability,
with the real digest left in place. Rebuilt truthfully from the consuming
repo's `lake-manifest.json`, which also makes `digest.py`'s "nine of the
fourteen packages carry `inputRev` `main` or `master`" checkable against the
fixture instead of only asserted in prose.

## `bundle` recomputes; it does not carry anything across

The emission is produced by the topic repo's own emitter in the topic repo's
own build. It is an **input**, not a source of truth. So `axioms` is re-sorted
and re-deduped, `contains_sorry_ax` is recomputed from that list, `counts` are
recounted from `constants[]`, and `axioms_disallowed` is derived from the
environment's `axiom_policy` — a field the emission deliberately does not
report at all, so there is no value to disagree with.

This is not suspicion of the emitter. It is that a producer reporting its own
verdict has one failure mode no amount of care removes, and the contract exists
to keep that failure mode out. There is no code path here that emits a clean
`axioms_disallowed` for a declaration whose axioms contain `sorryAx`.

Both inputs are validated **before** anything is derived from them, and the
output is validated **before** it is written — `mfc` must not be able to emit
an artifact its own schema rejects.

## Digests are pinned by data, not by agreement with ourselves

`digest.py` is frozen: any change is a MAJOR bump on every artifact carrying a
digest. `tests/test_digest.py` reproduces the three values the design note
recorded as `[COMPUTED]` against the real repository at `f166a3d` — the
`env_digest` and both statement digests — so a refactor that changes key order,
separators, or the NFC-then-collapse order fails immediately.

That matters more here than usual. The fixture corpus lives inside one of the
implementations it referees, so "our code agrees with our code" proves nothing;
agreement with values computed before this code existed does. It is red-team
gap 16's mitigation.

The properties those digests encode, each with its own test:

* a **def's body is part of its statement** — two defs with one type and
  different bodies are different claims;
* a **theorem's proof is not** — folding it in would make every proof edit read
  as a statement change, destroying the signal review depends on;
* a **rename does not rotate** anything — citations anchor to a registry key;
* **re-wrapping does not rotate** anything — `type_pp` carries hard line breaks
  at the pinned `format.width`.

## `validate`, and why it does not take a `--schema`

Every artifact carries `schema_version` as its first key, and `validate` reads
it rather than accepting a flag. That makes the artifact's **self-description**
part of what is checked: a file called `build.json` that declares
`emission/1.0` is validated as an emission and fails, instead of being quietly
checked as a build. `--schema` exists only to override for debugging, and
needing it means something is already wrong.

A `schema_version` this build does not carry is a **hard failure**, never a
skip. Skipping would let an artifact from a future contract version pass
unchecked, which is exactly the tolerant mode the design forbids.

All errors are reported, not the first. For a cross-field `allOf` the first
error is often the least informative one, and a validator that stops early
turns fixing an artifact into N round trips.

```bash
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
