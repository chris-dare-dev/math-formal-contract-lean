/-
Copyright (c) 2026 Chris Dare. All rights reserved.
Released under the MIT license.
-/
import MathFormalContract

/-!
# The reference restate producer, pointed at this package's test library

The same two jobs `MfcEmitSelf` has.

**A self-test.** `restateOne` is exercised by `run_cmd` inside the test library,
which proves the check. It does not prove the *producer*: importing a library
at runtime, standing up a term-elaboration context over it, and writing
`restate/1.0`. That path is only reachable from a topic repo otherwise, and a
topic repo takes hours to build. `MathFormalContractTest.restateSubject` is a
real declaration in a real imported environment, so this exercises it for the
price of an interpreted run.

**The reference for the lines a topic repo generates.** A topic's
`exe/Restate.lean` is this file with one name changed:

```lean
import MathFormalContract
def main (args : List String) : IO UInt32 :=
  MathFormalContract.restateMain (rootLib := `DerivedAlgGeo) args
```

Its `[[lean_exe]]` needs `supportInterpreter = true` for the reason
`MfcEmitSelf` documents: omitting it fails ONLY on Linux, so it passes locally
on macOS and dies in CI.
-/

def main (args : List String) : IO UInt32 :=
  MathFormalContract.restateMain (rootLib := `MathFormalContractTest) args
