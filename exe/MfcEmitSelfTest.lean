/-
Copyright (c) 2026 Chris Dare. All rights reserved.
Released under the MIT license.
-/
import MathFormalContractTest

/-!
# The emitter, pointed at the test library

The test library is the only module tree here carrying `@[cites]` bindings, so
it is the only one that can answer the question that matters: **does a
`SimplePersistentEnvExtension` written at elaboration time survive the trip
through an `.olean` and back out of `importModules`?**

It nearly did not. Extension state arrives empty rather than absent when
`enableInitializersExecution` is missing, and empty is indistinguishable from
"this repo cites nothing" in the emitted artifact — a silent vacuous pass
inside the very tool built to make vacuous passes impossible. Measured before
the fix: every `is_instance` read `false` and every `cites[]` was `[]`, with a
clean exit code.

A second binary rather than a `--root` flag on the first, for the reason given
in `emitMain`: a root a caller can retype is a scope a caller can silently
shrink.
-/

def main (args : List String) : IO UInt32 :=
  MathFormalContract.emitMain
    (rootLib := `MathFormalContractTest)
    (leanOptions := [("autoImplicit", .bool false),
                     ("relaxedAutoImplicit", .bool false)])
    args
