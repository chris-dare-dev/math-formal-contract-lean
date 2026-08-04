# MathFormalContract

The Lean half of a contract between a corpus of mathematical papers and a Lean
formalization of results from it.

It contains two things.

**`@[cites]`** binds a Lean declaration to a statement in a paper.

```lean
import MathFormalContract

@[cites "stmt:9f4c1a20b7d3:bridgeland2007.lem-8.2" (relation := one_way)
        (frontier := ["gltilde-universal-cover"])
        (note := "Acts on PreStabilityCondition, not StabilityCondition.")]
theorem myResult : ... := ...
```

**`MathFormalContract.Emit`** sweeps the environment and reports what was
proved, from what, and under which axioms, as `emission/1.0`.

## The emitter never parses Lean source

Every fact it writes comes from `Environment.constants` and
`Lean.collectAxioms` in the environment that actually elaborated.

That is the difference between catching this and not:

```lean
set_option maxHeartbeats 400000 in theorem sneaky : False := by sorry
open Classical in theorem sneaky : False := by sorry
```

A source-parsing extractor is defeated by declaration *syntax* — the corpus
server's Lean auditor matches neither the declaration site nor the declaration
name in either form, and returns a clean verdict for a sorry-backed proof. An
environment sweep cannot fail that way, because it never sees syntax. Measured
against a compiled fixture carrying all three evasion forms: three constants
emitted with `sorryAx` in their axiom closure, exit code 1.

Two further properties are load-bearing, and both are enforced by tests rather
than by care:

* **Module scope, never name prefix.** A declaration at the root namespace or
  under a foreign namespace, sitting inside a topic module, still lands in the
  `.olean` and is importable downstream. A prefix scope misses it, and a
  *mis-set* prefix sweeps nothing and passes.
* **Byte-reproducible.** Neither constant iteration order nor `collectAxioms`
  output order is specified — `collectAxioms` was measured returning
  `[propext, Quot.sound, Classical.choice]` for one declaration and
  `[Quot.sound, propext, Classical.choice]` for the next in the same run. Every
  array is sorted before serialization, so two runs differ only in
  `emitted_at`.

A topic repo generates one small file and nothing else:

```lean
import MathFormalContract
def main (args : List String) : IO UInt32 :=
  MathFormalContract.emitMain
    (rootLib := `BridgelandStabLean)
    (leanOptions := [("autoImplicit", .bool false),
                     ("relaxedAutoImplicit", .bool false)])
    args
```

`leanOptions` is **declared, not observed**, and the artifact should be read
that way. Elaboration options are compile flags and are not recorded in the
`.olean`, so an emitter that imports a module cannot recover the options that
built it. Reading the ambient options instead makes the record report the
*emitter process's* defaults — measured, this package emitted
`autoImplicit: true` while its own `lakefile.toml` sets it `false`. Copier
renders that file and `lakefile.toml` from the same answers, and `mfc lint`
fails a mismatch.

The emission carries **no digests**. Lean core at v4.29.0 ships no SHA-256, and
Lake's `Hash` is a 64-bit non-cryptographic value that is not portable across
toolchains, so all canonicalization lives downstream in one language.

## Zero dependencies, and it is load-bearing

`lakefile.toml` has no `[[require]]` block and must never get one. Not Mathlib,
not `Batteries`, not anything.

This package is the single **named exception** to the consuming repo's one-pin
rule, and the exception is argued *from* the leaf property: a package with no
transitive dependencies cannot drag anything into a topic repo's environment
and cannot disagree with that repo's anchor about a Mathlib revision. Add a
dependency and the exception lapses — meaning the dependency comes out of every
topic repo that took this one.

That is why `Cites.lean` hand-rolls its key validation instead of reaching for
a regex. If you want a helper, write the helper.

## Why the key looks like that

```
stmt:<12 lowercase hex>:<label>
```

The hex is a registry id minted once per topic repo; the label is chosen by
whoever writes the registry entry.

**The paper coordinate is deliberately not in the key.** No arXiv id, no
version, no printed number — those are typed fields of the registry entry. Two
reasons:

1. A `textbook:` source has no arXiv version, and a DOI contains characters
   that break positional parsing. Typed fields express both; a colon-delimited
   key does not.
2. Nothing a corpus *computes* appears in the key. Corpus chunk ids are content
   hashes that **rotate** on any re-parse, with no forwarding — so a citation
   built from one silently stops resolving. A key nothing rotates cannot.

`@[cites]` refuses a chunk-id-shaped key explicitly, because it is exactly what
a corpus hands you and therefore exactly what someone will paste in.

## Why `relation` is mandatory

There is no safe default. A missing relation that defaulted to anything would
be a trust axis inferred from silence.

| value | meaning |
|---|---|
| `exact` | states the cited statement, no added hypotheses |
| `equivalent` | interderivable with it in this environment |
| `specialization` | a special case of it |
| `one_way` | it implies this, not conversely |
| `no_claim` | related; no implication claimed |

Every value is a **claim by the author**, never a verified fact. Machine
artifacts always spell it `relation_claimed`; only a dated, named human review
may promote a claim to confirmed.

`note` is optional and free text, and the emitter writes `null` rather than
`""` when it is absent — a consumer must be able to tell "no note" from "a note
that says nothing". It is not decoration: `relation := no_claim` says two
statements are related without saying how, which is unreadable without one.
`mfc lint` rule `E-06` fails a `no_claim` binding whose note is null. That rule
lives on the `mfc` side rather than here, deliberately, so the rejection
fixture it names stays authorable.

## What this does not do

It does not check the mathematics, read the paper, or know whether the registry
key exists — the registry lives in the topic repo and is validated there. What
it checks is that the key is *well formed*, so a typo fails at compile time
instead of surfacing much later as a citation that resolves to nothing.

## Build

```bash
lake build
```

Builds the library, its tests, and both emitter binaries — all in
`defaultTargets` on purpose: a file no target builds is a file that rots.

Run the emitter against this package:

```bash
lake exe mfc_emit_self --out attest/lean-emission.json
```

`mfc_emit_selftest` is the same emitter pointed at the test library, which is
the only module tree here carrying `@[cites]`. It exists because extension
state nearly did not survive the round trip: `importModules` takes
`loadExts := false` by default, and with it unset every extension arrives
**empty rather than absent**. Measured before the fix — all 150 constants
reported `is_instance: false`, all seven `@[cites]` bindings emitted
`cites: []`, exit code 0, artifact valid. An emission asserting that a repo
cites nothing, produced by the tool whose purpose is to make vacuous passes
impossible.

Inspect what is tagged in scope:

```lean
#cites_dump
```

## Testing conventions

No `native_decide`, anywhere. It introduces `Lean.ofReduceBool` into the proof
term — an axiom outside the `propext` / `Classical.choice` / `Quot.sound`
allowlist this contract exists to enforce, and a package that polices axiom
hygiene must not launder one into its own build. Checks run at elaboration time
in `run_cmd` and throw on failure instead.

## Design record

The decisions behind this package are recorded in the first consuming repo,
under `.claude/decisions/` — principally **ADR-0008** (why this is a shared
dependency rather than vendored, and why it does not live inside the corpus
server's repo) and **ADR-0002** (why identity is minted in the topic repo).
