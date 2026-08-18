/-
Copyright (c) 2026 Chris Dare. All rights reserved.
Released under the MIT license.
-/
import MathFormalContract
import MathFormalContractTest.MultiRootFixture

/-!
# Tests for `@[cites]`

Built by `lake build` (this library is in `defaultTargets`), so a break is a
build failure rather than something someone remembers to run.

## No `native_decide`, and no `decide`, anywhere in here

The obvious way to test `validateKey` is `example : validateKey k = .ok () := by
native_decide`. **Do not.** `native_decide` introduces `Lean.ofReduceBool` into
the proof term — an axiom outside the `[propext, Classical.choice, Quot.sound]`
allowlist this whole contract exists to enforce. A package that polices axiom
hygiene must not launder one into its own build, even in a library nothing
downstream imports.

`decide` avoids the axiom but does not reduce here anyway: `String.all` and
friends are `@[extern]`, so kernel reduction stalls.

So the checks below run at **elaboration time** in `run_cmd` and throw on
failure. Axiom-free, and a failure names the case that broke instead of
reporting a type mismatch.
-/

open Lean Elab Command MathFormalContract

namespace MathFormalContractTest

/-! ## Key validation -/

run_cmd do
  let accept : Array String := #[
    "stmt:9f4c1a20b7d3:bridgeland2007.lem-8.2",
    "stmt:000000000000:a",
    "stmt:abcdef012345:x_y-z.0",
    "stmt:ffffffffffff:z9"
  ]
  for k in accept do
    match validateKey k with
    | .ok () => pure ()
    | .error m => throwError "should have accepted {k}, but: {m}"

  let reject : Array (String × String) := #[
    -- registry id: exactly 12 lowercase hex
    ("stmt:9F4C1A20B7D3:label",  "uppercase hex"),
    ("stmt:9f4c1a20b7d:label",   "11 digits"),
    ("stmt:9f4c1a20b7d3a:label", "13 digits"),
    ("stmt:9f4c1a20b7g3:label",  "'g' is not hex"),
    ("stmt::label",              "empty registry id"),
    -- local label
    ("stmt:9f4c1a20b7d3:2lemma", "label starts with a digit"),
    ("stmt:9f4c1a20b7d3:Lemma",  "uppercase in label"),
    ("stmt:9f4c1a20b7d3:lem 8.2","space in label"),
    ("stmt:9f4c1a20b7d3:",       "empty label"),
    -- shape
    ("9f4c1a20b7d3:label",       "no scheme"),
    ("chunk:9f4c1a20b7d3:label", "wrong scheme"),
    ("stmt:9f4c1a20b7d3",        "two parts"),
    ("",                         "empty string"),
    -- THE ONE THAT MATTERS MOST. A chunk id is shaped arxiv:<paper>:<16 hex>
    -- and is exactly what a corpus hands you, so someone will paste one in.
    -- It must be refused: chunk ids ROTATE. A re-ingest can mint a new one for
    -- the same statement and nothing forwards the old, so a citation to one
    -- silently stops resolving.
    ("arxiv:math/0212237:a82c3230040fd724", "a chunk id"),
    -- The paper coordinate is a field of the registry entry, not part of the
    -- key. Someone will try this too.
    ("stmt:9f4c1a20b7d3:arxiv:math/0212237:lem-8.2", "coordinate in the key")
  ]
  for (k, why) in reject do
    match validateKey k with
    | .error _ => pure ()
    | .ok () => throwError "should have rejected {k} ({why}), but accepted it"

/-! ## The attribute -/

/-- Minimal: mandatory relation, no frontier. -/
@[cites "stmt:9f4c1a20b7d3:test.trivial" (relation := exact)]
theorem tagged_exact : True := trivial

/-- With a frontier. `one_way` plus an open frontier is the honest shape for a
declaration whose name promises more than its proof delivers. -/
@[cites "stmt:9f4c1a20b7d3:test.oneway" (relation := one_way)
        (frontier := ["some-open-fact", "another.open-fact"])]
theorem tagged_one_way : True := trivial

/-- Every relation spelling parses. -/
@[cites "stmt:9f4c1a20b7d3:test.equivalent" (relation := equivalent)]
theorem tagged_equivalent : True := trivial

@[cites "stmt:9f4c1a20b7d3:test.specialization" (relation := specialization)]
theorem tagged_specialization : True := trivial

-- `no_claim` carries a note, because `mfc lint` rule E-06 requires one and
-- this repo's own emission must pass its own lint. Saying two statements are
-- related without saying how is unreadable, which is the point of the rule.
@[cites "stmt:9f4c1a20b7d3:test.noclaim" (relation := no_claim)
        (note := "Fixture for the no_claim spelling; no implication is claimed.")]
theorem tagged_no_claim : True := trivial

/-- One declaration may cite more than one statement. Note these are two
entries in **one** attribute list — `@[a] @[b] theorem` is not valid Lean. -/
@[cites "stmt:9f4c1a20b7d3:test.multi.a" (relation := exact),
 cites "stmt:9f4c1a20b7d3:test.multi.b" (relation := specialization)]
theorem tagged_twice : True := trivial

/-- The attribute is not restricted to theorems. -/
@[cites "stmt:9f4c1a20b7d3:test.ondef" (relation := no_claim)
        (note := "Fixture proving the attribute lands on a def, not only a theorem.")]
def taggedDef : Nat := 0

/-! ## What landed in the extension

`#cites_dump` is the human-readable view; this checks the machine-readable one,
including the ordering the emitter's determinism gate will depend on. -/

run_cmd do
  let env ← getEnv
  let ours := (citesEntries env).filter fun e =>
    e.key.startsWith "stmt:9f4c1a20b7d3:test."

  unless ours.size == 8 do
    throwError "expected 8 test bindings, got {ours.size}"

  -- Values round-trip.
  let some ow := ours.find? (·.key == "stmt:9f4c1a20b7d3:test.oneway")
    | throwError "one_way binding missing"
  unless ow.relation == .oneWay do
    throwError "relation did not round-trip: got {ow.relation.toString}"
  unless ow.frontier == #["some-open-fact", "another.open-fact"] do
    throwError "frontier did not round-trip: got {ow.frontier}"
  unless ow.declName == ``tagged_one_way do
    throwError "declName did not round-trip: got {ow.declName}"

  -- Empty frontier is the default, not an error.
  let some ex := ours.find? (·.key == "stmt:9f4c1a20b7d3:test.trivial")
    | throwError "exact binding missing"
  unless ex.frontier.isEmpty do
    throwError "expected empty frontier by default"

  -- Both halves of the two-key declaration point at that declaration.
  for k in ["stmt:9f4c1a20b7d3:test.multi.a", "stmt:9f4c1a20b7d3:test.multi.b"] do
    let some e := ours.find? (·.key == k) | throwError "missing {k}"
    unless e.declName == ``tagged_twice do
      throwError "{k} bound to {e.declName}, expected tagged_twice"

  -- Sorted by key. This is what makes emission reproducible run to run.
  let keys := (ours.map (·.key)).toList
  unless keys == keys.mergeSort (· ≤ ·) do
    throwError "citesEntries is not sorted by key: {keys}"

  -- Every relation spelling is distinct and stable. These strings are a
  -- contract with the schemas; a constructor rename must not move them.
  let spellings := [Relation.exact, .equivalent, .specialization, .oneWay, .noClaim]
    |>.map Relation.toString
  unless spellings == ["exact", "equivalent", "specialization", "one_way", "no_claim"] do
    throwError "relation wire spellings changed: {spellings}"

/-! ## The package audits its own axioms

A package that polices axiom hygiene had better pass its own check.

This runs at **build time over `Environment.constants`**, not as a grep over
source. That distinction is the whole thesis of the contract this package
serves: the corpus server's Lean surface audits declarations by regex over
source text, and that regex does not recognise `set_option … in theorem` as a
declaration site, so a sorry-backed proof rides through inside a `clean`
verdict. A source grep for `native_decide` here would fail the same way — and
would false-positive on the paragraph above that names it in prose.

Sweeping the environment cannot miss a declaration, because there is no text
for one to hide in. It is also a first sketch of what the emitter does. -/

run_cmd do
  let allow : List Name := [``propext, ``Classical.choice, ``Quot.sound]
  let env ← getEnv
  let mut checked := 0
  let mut bad : Array (Name × Array Name) := #[]
  for (name, _) in env.constants.toList do
    -- Our own declarations only; not Lean core's, and not compiler-internal
    -- names, which are not what anyone is claiming anything about.
    unless (`MathFormalContract).isPrefixOf name && !name.isInternal do
      continue
    checked := checked + 1
    let axs ← collectAxioms name
    let off := axs.filter fun a => !allow.contains a
    unless off.isEmpty do
      bad := bad.push (name, off)
  if checked == 0 then
    -- A sweep that checks nothing passes trivially. That is the vacuous-pass
    -- failure the contract's own CI guards against; guard it here too.
    throwError "axiom audit swept 0 declarations -- the filter is wrong"
  unless bad.isEmpty do
    let lines := bad.toList.map fun (n, axs) => s!"  {n}: {axs}"
    throwError "declarations outside the axiom allowlist:\n{"\n".intercalate lines}"
  logInfo s!"axiom audit: {checked} declarations, all within \
    [propext, Classical.choice, Quot.sound]"

/-! ## The emitter is byte-reproducible

`emission/1.0` is the input to every downstream digest, so two runs over an
unchanged environment must differ in nothing but `emitted_at`. That is not free:
neither `Environment.constants` iteration order nor `collectAxioms` output order
is specified, and `collectAxioms` was measured returning
`[propext, Quot.sound, Classical.choice]` for one declaration and
`[Quot.sound, propext, Classical.choice]` for the next in the same run.

Run here rather than as a shell comparison of two emitted files, so a change
that breaks it fails `lake build` on the machine that made it. -/

run_cmd Lean.Elab.Command.liftTermElabM do
  let opts := [("autoImplicit", Lean.Json.bool false)]
  let (a, sorryA) ← MathFormalContract.emitJson `MathFormalContract opts "FIXED"
  let (b, sorryB) ← MathFormalContract.emitJson `MathFormalContract opts "FIXED"
  unless a.pretty 120 == b.pretty 120 do
    throwError "emission is not byte-reproducible across two runs"
  unless sorryA == 0 && sorryB == 0 do
    throwError "this package's own emission reports {sorryA} sorry-backed constant(s)"

/-! ## The emitter is module-scoped, and the scope is not empty

Two failures in one check. A name-prefix scope would sweep this test file's
`MathFormalContractTest.*` declarations, because they sit under no module the
root library owns but are perfectly ordinary constants — and a *mis-set* scope
would sweep nothing at all and pass, which is the vacuous pass the whole
artifact exists to make impossible. -/

run_cmd Lean.Elab.Command.liftTermElabM do
  let env ← Lean.getEnv
  let mods := MathFormalContract.inScopeModules env `MathFormalContract
  if mods.isEmpty then
    throwError "in-scope module set is empty"
  if mods.contains `MathFormalContractTest then
    throwError "module scope leaked into the test library"
  let (doc, _) ← MathFormalContract.emitJson `MathFormalContract [] "FIXED"
  let .ok cs := (doc.getObjValD "constants").getArr? | throwError "no constants[]"
  if cs.isEmpty then
    throwError "emission swept 0 constants -- the vacuous pass"
  for c in cs do
    let .ok m := (c.getObjValD "module").getStr? | throwError "constant without module"
    unless mods.contains m.toName do
      throwError "constant emitted from out-of-scope module {m}"
  logInfo s!"emission: {cs.size} constants across {mods.size} in-scope modules"

/-! ## A monorepo scope is the union of its constituent module roots -/

run_cmd Lean.Elab.Command.liftTermElabM do
  let env ← Lean.getEnv
  let mods := MathFormalContract.inScopeModulesForRoots env
    [`MathFormalContract, `MathFormalContractTest]
  unless mods.contains `MathFormalContract &&
      mods.contains `MathFormalContractTest.MultiRootFixture do
    throwError "multi-root module scope omitted a constituent root"
  let (doc, sorryN) ← MathFormalContract.emitJsonForRoots
    `MathFormalContract [`MathFormalContractTest] #[] [] "FIXED"
  let .ok cs := (doc.getObjValD "constants").getArr? | throwError "no constants[]"
  if cs.isEmpty then
    throwError "multi-root emission swept 0 constants -- the vacuous pass"
  unless sorryN == 0 do
    throwError "multi-root emission reports {sorryN} sorry-backed constant(s)"

/-! ## `@[discharges]` binds a frontier id to a declaration that compiles -/

/-- A construction claiming to close a recorded gap. The claim lives here,
next to the proof, rather than in hand-edited YAML somewhere else. -/
@[discharges "gltilde-universal-cover"]
theorem dischargesOne : True := trivial

/-- One construction, two recorded gaps. Normal, and why the attribute takes a
list rather than forcing two attributes that read as unrelated claims. -/
@[discharges "first-gap", "second-gap"]
theorem dischargesTwo : True := trivial

run_cmd Lean.Elab.Command.liftTermElabM do
  let env ← Lean.getEnv
  let es := MathFormalContract.dischargesEntries env
  unless es.any (fun e => e.frontierId == "gltilde-universal-cover"
      && e.declName == `MathFormalContractTest.dischargesOne) do
    throwError "the discharges extension lost a binding"
  let two := es.filter (·.declName == `MathFormalContractTest.dischargesTwo)
  unless two.size == 2 do
    throwError "a two-id attribute recorded {two.size} binding(s), expected 2"

#cites_dump

end MathFormalContractTest
