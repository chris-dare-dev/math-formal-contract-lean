# MathFormalContract

The Lean half of a contract between a corpus of mathematical papers and a Lean
formalization of results from it.

Today it contains one thing: the **`@[cites]` attribute**, which binds a Lean
declaration to a statement in a paper.

```lean
import MathFormalContract

@[cites "stmt:9f4c1a20b7d3:bridgeland2007.lem-8.2" (relation := one_way)
        (frontier := ["gltilde-universal-cover"])]
theorem myResult : ... := ...
```

An emitter that sweeps `Environment.constants` and reports what was proved,
from what, and under which axioms lands next.

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

## What this does not do

It does not check the mathematics, read the paper, or know whether the registry
key exists — the registry lives in the topic repo and is validated there. What
it checks is that the key is *well formed*, so a typo fails at compile time
instead of surfacing much later as a citation that resolves to nothing.

## Build

```bash
lake build
```

Builds the library and its tests, which are in `defaultTargets` on purpose: a
test file no target builds is a test file that rots.

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
