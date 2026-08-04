/-
Copyright (c) 2026 Chris Dare. All rights reserved.
Released under the MIT license.
-/
import MathFormalContract

/-!
# The reference emitter binary, pointed at this package's library

This is simultaneously two things.

**A self-test.** This package's own library is a topic library as far as the
emitter is concerned, so sweeping it exercises the real code path — import,
module scope, axiom closure, JSON — with no topic repo and no Mathlib in sight.
The emitter is otherwise only reachable from a repo that takes hours to build,
because its anchor is not covered by `lake exe cache get`.

**The reference for the lines a topic repo generates.** A topic's
`scripts/Emit.lean` is this file with one name changed:

```lean
import MathFormalContract
def main (args : List String) : IO UInt32 :=
  MathFormalContract.emitMain
    (rootLib := `BridgelandStabLean)
    (leanOptions := [("autoImplicit", .bool false),
                     ("relaxedAutoImplicit", .bool false)])
    args
```

That is deliberately the *only* Lean the topic template renders, so a
`copier update` never three-way-merges a metaprogram.

Its `[[lean_exe]]` needs `supportInterpreter = true`, and the template must
render that too. `importModules` reaches `Init`/`Std`/`Lean` declarations with
no native implementation, and omitting the flag **fails only on Linux** —
macOS resolves the symbol dynamically, so the binary runs locally and dies in
CI with `Could not find native implementation of external declaration
'IO.getRandomBytes'`.

`leanOptions` must mirror the `[leanOptions]` block of the same repo's
`lakefile.toml` — here, `autoImplicit = false` and `relaxedAutoImplicit =
false`. It is declared rather than observed because elaboration options are
compile flags and are not recorded in the `.olean`; see `emitJson`. Copier
renders both files from the same answers, and `mfc lint` fails a mismatch.

Bindings tagged `@[cites]` live in the test library, not here, so this
binary's `cites[]` arrays are all empty by construction. `mfc_emit_selftest`
is the one that proves the attribute survives the import.
-/

def main (args : List String) : IO UInt32 :=
  MathFormalContract.emitMain
    (rootLib := `MathFormalContract)
    (leanOptions := [("autoImplicit", .bool false),
                     ("relaxedAutoImplicit", .bool false)])
    args
