/-
Copyright (c) 2026 Chris Dare. All rights reserved.
Released under the MIT license.
-/
import Lean
import MathFormalContract.Cites

/-!
# The emitter

Produces `emission/1.0` — a machine-readable inventory of everything a topic
library declares, with each declaration's full transitive axiom closure.

## It never parses Lean source, and that is the whole point

Every fact here is read out of `Environment.constants` and `Lean.collectAxioms`
in the environment that actually elaborated. No regex, no line scanning, no
declaration-site heuristics.

That is not a style preference. A source-parsing extractor is defeated by
declaration *syntax*: the legal forms

```lean
set_option maxHeartbeats 400000 in theorem sneaky : False := by sorry
open Classical in theorem sneaky : False := by sorry
```

are matched by neither the declaration-site nor the declaration-name regex in
the corpus server's Lean auditor — reproduced by executing them, which returned
a clean verdict for a sorry-backed proof. An environment sweep cannot fail that
way, because it never sees syntax. `sneaky` is a constant like any other, and
its axiom closure contains `sorryAx`.

## Scoping is by module, never by name prefix

A declaration at the root namespace, or under a foreign namespace, sitting
inside a topic module still lands in the `.olean` and is importable downstream.
A name-prefix scope misses it, and a *mis-set* prefix yields an empty `decls[]`
— a vacuous pass observationally identical to a clean build. Both failure modes
are closed by scoping to the module set and refusing to emit an empty scope.

## Determinism

Neither `Environment.constants` iteration order nor `collectAxioms` output
order is specified. Measured on this toolchain, `collectAxioms` returned
`[propext, Quot.sound, Classical.choice]` for one declaration and
`[Quot.sound, propext, Classical.choice]` for the next in the same run. Every
array this file writes is therefore sorted before serialization, and the
constant list is sorted by name. Two runs over an unchanged environment
produce byte-identical output except for `emitted_at`.

## No digests

Lean core at v4.29.0 ships no SHA-256, and Lake's `Hash` is a 64-bit
non-cryptographic value that is not portable across toolchains. All contract
digests are computed downstream by `mfc`, from these bytes, so canonicalization
lives in exactly one language.
-/

namespace MathFormalContract

open Lean Meta

/-! ## Wire encodings -/

/-- The wire spelling of a declaration kind. An explicit total function rather
than a derived one: these strings are a contract with the emission schema and
must not change when a Lean core constructor is renamed. -/
private def kindOf : ConstantInfo → String
  | .axiomInfo _  => "axiom"
  | .defnInfo _   => "def"
  | .thmInfo _    => "theorem"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _   => "quot"
  | .inductInfo _ => "inductive"
  | .ctorInfo _   => "ctor"
  | .recInfo _    => "rec"

/-- Pretty-printer options, pinned for digest stability.

`format.width` **must** be pinned. Measured `type_pp` on this toolchain
contains hard line breaks at the wrap width:

```
"∀ (C : Type u) [inst : CategoryTheory.Category.{v, u} C]\n  [inst_1 : ...]"
```

so an unpinned width silently rotates every downstream statement digest on a
toolchain that changes its default. `mfc` additionally whitespace-normalizes
before hashing; this is belt and braces.

Ambient options are *discarded* rather than extended, so a `set_option` in the
caller cannot reach the emitted text. -/
private def ppOpts : Options :=
  -- The width goes through the generic `Options.set`: `Options` is a structure
  -- with its own `setBool` but no `setNat` at this toolchain, and it is not a
  -- `KVMap`, so neither dot notation nor `KVMap.setNat` reaches a Nat.
  ({} : Options)
    |>.setBool `pp.fullNames true
    |>.setBool `pp.universes true
    |>.setBool `pp.explicit  false
    |>.setBool `pp.notation  true
    |>.set     `format.width (120 : Nat)

/-- The same options as data, for the `pp_options` record. Written from one
source so the artifact cannot claim a setting the emitter did not use. -/
private def ppOptsJson : Json :=
  Json.mkObj [
    ("pp.fullNames", Json.bool true),
    ("pp.universes", Json.bool true),
    ("pp.explicit",  Json.bool false),
    ("pp.notation",  Json.bool true),
    ("format.width", Json.num 120)]

/-! ## Timestamps -/

private def pad2 (n : Nat) : String :=
  if n < 10 then s!"0{n}" else toString n

/-- `emitted_at` as ISO-8601 UTC.

Hand-formatted from the field accessors rather than through a format string:
the emitter must not acquire a formatting dependency, and a wrong format
specifier fails at *runtime* in `Std.Time` where a wrong field access fails at
compile time. -/
def isoUtcNow : IO String := do
  let dt := (← Std.Time.Timestamp.now).toPlainDateTimeAssumingUTC
  return s!"{dt.date.year.toInt}-{pad2 dt.date.month.toNat}-{pad2 dt.date.day.toNat}\
T{pad2 dt.time.hour.toNat}:{pad2 dt.time.minute.toNat}:{pad2 dt.time.second.toNat}Z"

/-! ## The sweep -/

/-- Sort a `Name` array by its **string** spelling and drop duplicates.

String order, not `Name.lt`: the consumer sorts and compares the JSON strings,
so ordering by anything else would make "sorted" mean two different things on
the two sides of the seam. -/
private def sortedNames (ns : Array Name) : Array Name :=
  -- Adjacent-duplicate removal on the sorted array. Hand-rolled because core
  -- has no `Array.eraseDups` at this toolchain and this package may not add a
  -- dependency to get one -- see `lakefile.toml`.
  (ns.qsort (fun a b => a.toString < b.toString)).foldl (init := #[]) fun acc n =>
    if acc.back?.any (· == n) then acc else acc.push n

/-- Topic-local constants occurring in `e`.

"Topic-local" means declared in one of `mods`. External constants contribute
nothing to a statement digest — `environment.json` already pins every upstream
package by commit, so naming them again here would be a second, drifting copy
of the same fact. -/
private def localConstsIn (env : Environment) (mods : Std.HashSet Name)
    (e : Expr) : Array Name :=
  sortedNames <| e.getUsedConstants.filter fun n =>
    match env.getModuleIdxFor? n with
    | some i => mods.contains env.header.moduleNames[i.toNat]!
    | none   => false

/-- Running tallies over the sweep. `in_scope` counts declarations a human
wrote; `internal` counts compiler-generated detail (`.proof_1`, `.match_2`,
equation lemmas). Both are emitted, because the `.ilean` cross-check compares
against the full set. -/
private structure Stats where
  total     : Nat := 0
  inScope   : Nat := 0
  internal  : Nat := 0
  withRange : Nat := 0
  instances : Nat := 0
  privateN  : Nat := 0
  sorryN    : Nat := 0

/-- The module set in scope for `rootLib`: the root module itself and
everything under it. -/
def inScopeModules (env : Environment) (rootLib : Name) : Std.HashSet Name :=
  env.header.moduleNames.foldl (init := {}) fun s m =>
    if m == rootLib || rootLib.isPrefixOf m then s.insert m else s

/-- Build the `emission/1.0` document over the ambient environment.

Returns the document and the number of constants whose axiom closure contains
`sorryAx`. The caller decides what to do about that count; this function does
not throw on it, because the artifact must be written even when — especially
when — the answer is "this repo currently has three sorries".

Throws only on an **empty module scope**, which is not a state to report but a
misconfiguration: it is the observable signature of a mis-set `rootLib`, and it
must not be able to produce a passing artifact.

`leanOptions` is **declared by the caller, not observed**, and that is a
limitation worth stating rather than hiding. Elaboration options are compile
flags; they are not recorded in the `.olean`, so an emitter that imports a
module cannot recover the options that built it. Reading the ambient options
instead — which an earlier draft did — makes the artifact report the *emitter
process's* defaults: measured, this package emitted `autoImplicit: true` while
its own `lakefile.toml` sets it `false`. A trust record whose first job is to
stop false claims must not open with one. So the caller declares them, in the
same generated file that a topic's `lakefile.toml` is rendered beside, and
`mfc lint` cross-checks the two. -/
def emitJson (rootLib : Name) (leanOptions : List (String × Json))
    (emittedAt : String) : MetaM (Json × Nat) := do
  let env ← getEnv
  let allMods := env.header.moduleNames
  let mods := inScopeModules env rootLib
  if mods.isEmpty then
    throwError "mfc-emit: root library {rootLib} matched ZERO modules. An empty \
      scope is the observable signature of a misconfigured emitter and must not \
      produce a passing artifact. Modules present: {allMods.size}."
  -- Index by module rather than walking `env.constants`. Once a topic library
  -- imports Mathlib the constant map holds ~3e5 entries, and materializing it
  -- to filter afterwards costs more than the emission itself. `moduleData` is
  -- parallel to `moduleNames`, so the same index reads both.
  let mut names : Array Name := #[]
  for i in [0 : allMods.size] do
    if mods.contains allMods[i]! then
      names := names ++ env.header.moduleData[i]!.constNames
  let cites := citesEntries env
  -- Carried as `(name, json)` pairs so the final sort reads the name directly
  -- rather than projecting it back out of the JSON — `Json.getStr!` does not
  -- exist here, and a sort key recovered from a partial accessor would fail
  -- silently rather than loudly.
  let mut out : Array (String × Json) := #[]
  let mut st : Stats := {}
  for n in names do
    let some ci := env.find? n | continue
    let some idx := env.getModuleIdxFor? n | continue
    let modName := allMods[idx.toNat]!
    st := { st with total := st.total + 1 }
    -- SORT: `collectAxioms` output order is unspecified and observed unstable.
    let axs := (← collectAxioms n).map (·.toString) |>.qsort (· < ·)
    if axs.contains "sorryAx" then st := { st with sorryN := st.sorryN + 1 }
    let rng ← findDeclarationRanges? n
    let isInt := n.isInternalDetail
    let isInst ← isInstance n
    let isPriv := isPrivateName n
    let isRed ← isReducible n
    st := { st with
      internal  := if isInt then st.internal + 1 else st.internal
      inScope   := if isInt then st.inScope else st.inScope + 1
      withRange := if rng.isSome then st.withRange + 1 else st.withRange
      instances := if isInst then st.instances + 1 else st.instances
      privateN  := if isPriv then st.privateN + 1 else st.privateN }
    let tyStr ← withOptions (fun _ => ppOpts) do
      pure (toString (← PrettyPrinter.ppExpr ci.type))
    -- `value_pp` for `def` and `opaque` ONLY.
    --
    -- A def's body IS its statement — two defs with the same type and
    -- different bodies are different claims, and omitting the body would let
    -- one silently replace the other under an unchanged digest. A theorem's
    -- body is a *proof*, and folding it in would make every proof edit read as
    -- a statement change, which destroys the one signal review depends on.
    let valStr : Option String ← match ci with
      | .defnInfo v | .opaqueInfo v =>
        withOptions (fun _ => ppOpts) do
          pure (some (toString (← PrettyPrinter.ppExpr v.value)))
      | _ => pure none
    let deps := match ci with
      | .defnInfo v | .opaqueInfo v =>
        sortedNames (localConstsIn env mods ci.type ++ localConstsIn env mods v.value)
      | _ => localConstsIn env mods ci.type
    let myCites := cites.filter (·.declName == n)
    out := out.push <| (n.toString, Json.mkObj [
      ("name",         Json.str n.toString),
      ("module",       Json.str modName.toString),
      ("kind",         Json.str (kindOf ci)),
      ("is_instance",  Json.bool isInst),
      ("is_internal",  Json.bool isInt),
      ("is_private",   Json.bool isPriv),
      ("is_reducible", Json.bool isRed),
      ("num_levels",   Json.num ci.levelParams.length),
      ("type_pp",      Json.str tyStr),
      ("value_pp",     match valStr with | some s => Json.str s | none => Json.null),
      ("local_deps",   Json.arr (deps.map fun d => Json.str d.toString)),
      -- Mutual-recursion groups are not yet computed; the key is present and
      -- empty rather than absent, so a consumer never has to distinguish
      -- "no SCC" from "this emitter version did not say".
      ("scc_members",  Json.arr #[]),
      ("axioms",       Json.arr (axs.map Json.str)),
      ("range",        match rng with
                       | some r => Json.mkObj [
                           ("startLine", Json.num r.range.pos.line),
                           ("startCol",  Json.num r.range.pos.column),
                           ("endLine",   Json.num r.range.endPos.line),
                           ("endCol",    Json.num r.range.endPos.column)]
                       | none => Json.null),
      ("cites", Json.arr (myCites.map fun c => Json.mkObj [
                  ("key",              Json.str c.key),
                  -- Always `relation_claimed` in a machine artifact. An agent
                  -- reading this field reads the word "claimed"; only a dated,
                  -- named human review may write `relation_confirmed`, and that
                  -- key exists in `review/1.0` alone.
                  ("relation_claimed", Json.str c.relation.toString),
                  ("frontier",         Json.arr (c.frontier.map Json.str)),
                  ("note", if c.note.isEmpty then Json.null else Json.str c.note)]))])
  -- Iteration order over modules and their constant lists is not specified.
  let sortedOut := (out.qsort fun a b => a.1 < b.1).map (·.2)
  let doc := Json.mkObj [
    ("schema_version",  Json.str "emission/1.0"),
    ("emitter_version", Json.str "mfc-emit/1.0.0"),
    ("lean_version",    Json.str Lean.versionString),
    ("lean_githash",    Json.str Lean.githash),
    ("lean_options",    Json.mkObj leanOptions),
    ("pp_options",      ppOptsJson),
    ("root_lib",        Json.str rootLib.toString),
    ("modules",         Json.arr <| (mods.toArray.map (·.toString) |>.qsort (· < ·)).map Json.str),
    ("counts", Json.mkObj [
        ("total",      Json.num st.total),
        ("in_scope",   Json.num st.inScope),
        ("internal",   Json.num st.internal),
        ("with_range", Json.num st.withRange),
        ("instances",  Json.num st.instances),
        ("private",    Json.num st.privateN)]),
    ("constants",  Json.arr sortedOut),
    ("emitted_at", Json.str emittedAt)]
  return (doc, st.sorryN)

/-! ## Driver -/

/-- Import `rootLib`, sweep it, and write `emission/1.0` to `outPath`.

Returns the process exit code: `0` clean, `1` sorry-backed, `2` misconfigured.

**The file is written before the sorry check, always.** A mid-development repo
must still produce an honest artifact saying "three constants here depend on
`sorryAx`" — that record is most useful exactly when the build is not clean,
and refusing to write it would leave the obligation queue empty at the only
moment it matters. -/
private unsafe def emitToFileImpl (rootLib : Name) (leanOptions : List (String × Json))
    (outPath : System.FilePath) : IO UInt32 := do
  initSearchPath (← findSysroot)
  -- Lets imported modules' `initialize` blocks run. Required for a topic repo
  -- whose upstream registers attributes this binary does not statically link.
  enableInitializersExecution
  -- `loadExts := true` IS LOAD-BEARING, AND ITS DEFAULT IS `false`.
  --
  -- Environment extension state is not read out of the `.olean`s unless this
  -- is set, and the failure is silent: extensions arrive EMPTY rather than
  -- absent, so every consumer reads a well-formed "nothing here". Measured on
  -- this package before the fix: all 150 constants emitted `is_instance:
  -- false` — including `instToStringRelation`, which `Meta.isInstance` reports
  -- as `true` in an ordinary elaboration context — and all seven `@[cites]`
  -- bindings in the test library emitted `cites: []`, with exit code 0 and an
  -- artifact that validated.
  --
  -- An emission asserting that a repo cites nothing, produced by the tool
  -- whose entire purpose is to make vacuous passes impossible. The regression
  -- test for this is `mfc_emit_selftest`, which fails if `cites[]` is empty.
  let env ← importModules #[{ module := rootLib }] {}
    (trustLevel := 0) (loadExts := true)
  let emittedAt ← isoUtcNow
  let ctx : Core.Context := { fileName := "<mfc-emit>", fileMap := default, options := {} }
  let coreSt : Core.State := { env }
  let result ← try
      let ((doc, sorryN), _, _) ← (emitJson rootLib leanOptions emittedAt).toIO ctx coreSt
      pure (Except.ok (doc, sorryN))
    catch e => pure (Except.error (toString e))
  match result with
  | .error msg =>
    IO.eprintln s!"mfc-emit: {msg}"
    return 2
  | .ok (doc, sorryN) =>
    if let some parent := outPath.parent then
      IO.FS.createDirAll parent
    IO.FS.writeFile outPath (doc.pretty 120 ++ "\n")
    IO.eprintln s!"mfc-emit: wrote {outPath}"
    if sorryN != 0 then
      IO.eprintln s!"mfc-emit: {sorryN} constant(s) depend on sorryAx.\n\
        A sorry-backed declaration typechecks, gets imported, and launders an \
        unproved claim into everything downstream. Leave the result UNDECLARED \
        and mint a `kind: obligation` registry entry instead."
      return 1
    return 0

/-- Import `rootLib`, sweep it, and write `emission/1.0` to `outPath`.

`unsafe` is confined to the implementation rather than propagated to callers.
`enableInitializersExecution` is an unsafe primitive, and without it the sweep
reads empty environment extensions; forcing every consumer to write `unsafe def
main` would push that detail into the three lines a topic repo generates, which
are meant to be the one piece of Lean nobody has to think about. -/
@[implemented_by emitToFileImpl]
opaque emitToFile (rootLib : Name) (leanOptions : List (String × Json))
    (outPath : System.FilePath) : IO UInt32

private def usage : String :=
  "usage: <emitter> [--out <path>]\n  --out <path>  emission destination \
   (default: attest/lean-emission.json)"

/-- The entry point a topic repo calls, with its root library and its
elaboration options hard-coded.

Both are *parameters of the generated file*, not flags, on purpose. A `--root`
a caller can retype is a scope a caller can silently shrink, and a shrunk scope
is the vacuous pass this whole artifact exists to make impossible. A
`--lean-option` a caller can retype is a claim about the build that the build
did not make. Both are rendered from the same copier answers as the topic's
`lakefile.toml`, so the two files agree by construction and `mfc lint` fails
them if they ever stop agreeing. -/
def emitMain (rootLib : Name) (leanOptions : List (String × Json))
    (args : List String) : IO UInt32 := do
  match args with
  | [] => emitToFile rootLib leanOptions "attest/lean-emission.json"
  | ["--out", p] => emitToFile rootLib leanOptions p
  | _ => IO.eprintln usage; return 2

end MathFormalContract
