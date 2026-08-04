/-
Copyright (c) 2026 Chris Dare. All rights reserved.
Released under Apache 2.0 license.
-/
import MathFormalContract.Cites

/-!
# MathFormalContract

The Lean half of a contract between a corpus of mathematical papers and a Lean
formalization of results from it.

This package contains exactly two things, and is deliberately hard to grow:

* `@[cites]` — bind a declaration to a statement in a paper, by a key that
  contains no corpus-derived bytes.
* (next) an emitter that sweeps `Environment.constants` and reports what was
  proved, from what, under which axioms.

Everything else in the contract — the JSON schemas, the fixture corpus, the
statement registry, the resolver — lives elsewhere and is not a Lean problem.

## The one invariant

**Zero dependencies. Core Lean only.** See `lakefile.toml`, which explains why
adding one is not a small change.
-/
