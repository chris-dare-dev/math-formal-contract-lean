/-
Copyright (c) 2026 Chris Dare. All rights reserved.
Released under the MIT license.
-/
import MathFormalContract.Cites
import MathFormalContract.Discharges
import MathFormalContract.Emit

/-!
# MathFormalContract

The Lean half of a contract between a corpus of mathematical papers and a Lean
formalization of results from it.

This package contains exactly two things, and is deliberately hard to grow:

* `@[cites]` — bind a declaration to a statement in a paper, by a key that
  contains no corpus-derived bytes.
* `@[discharges]` — claim, in code that has to compile, that a declaration
  closes a named frontier item. It is what stops axis 6 looking computed while
  being asserted in hand-edited YAML.
* `MathFormalContract.Emit` — an emitter that sweeps the environment and
  reports what was proved, from what, under which axioms. It reads
  `Environment.constants` and `Lean.collectAxioms`; it never parses source.

Everything else in the contract — the JSON schemas, the fixture corpus, the
statement registry, the resolver — lives elsewhere and is not a Lean problem.

## The one invariant

**Zero dependencies. Core Lean only.** See `lakefile.toml`, which explains why
adding one is not a small change.
-/
