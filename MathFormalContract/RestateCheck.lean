/-
Copyright (c) 2026 Chris Dare. All rights reserved.
Released under the MIT license.
-/
import Lean
import MathFormalContract.Emit

/-!
# `restate-check` — does a human review survive a Mathlib bump?

A review is made against one environment. `env_digest` includes every package
revision, so `statement_stable` is `not_applicable` across environments **by
construction** — which means a single Mathlib or anchor bump invalidates 100%
of human review simultaneously, and recovery costs the entire original review
budget, per bump, at roughly two hours an entry.

This is the only thing standing between that and re-reviewing everything.

## What it does

For each reviewed entry: take the statement **as the human read it**
(`reviewed_statement_pp`), parse it, elaborate it in the current environment,
and ask whether it is definitionally equal to what that declaration says
*now*. If it is, the review survives the bump.

## Three outcomes, and the third is not the second

* `restated` — elaborates and is defeq. The review survives.
* `changed` — elaborates, is not defeq. The statement moved; the review is
  invalidated and must be redone.
* `not_checkable` — could not be parsed or elaborated at all.

**`not_checkable` must never be collapsed into `changed`.** They differ in what
a human must do: `changed` means re-review this statement, `not_checkable`
means nobody knows whether it changed, and reporting the second as the first
would send a reviewer to re-read a statement that may be perfectly fine while
hiding that the tool failed.

## `reviewed_statement_pp` is NOT `type_pp`

Reusing the emission's `type_pp` here is the trap this file exists to avoid.
The emitter pins `pp.explicit := false`; under those options 5 of 76
declarations fail to elaborate with unresolved metavariables, and with
implicits printed, 0. A check built on `type_pp` would report `not_checkable`
on statements that are perfectly fine.

## The message log is load-bearing

`elabTerm` **logs** errors rather than throwing them. A `try`/`catch` alone
therefore returns success on a failed elaboration *and* leaks the error into
the enclosing build's output. Observed directly while writing the spike.

So each attempt snapshots `Core.State.messages`, treats `hasErrors` as failure,
and restores the log afterwards. Without this the check is both wrong and
noisy.

## Transparency is deliberately not touched

`withTransparency .all` rescues exactly zero cases — 339 both ways in the
spike. Reaching for it would widen what counts as "the same statement" for no
measured gain, and every widening here is a way for a review to survive a
change it should not have survived.
-/

namespace MathFormalContract

open Lean Elab Term Meta

/-- What one reviewed statement did against the current environment. -/
inductive RestateOutcome where
  /-- Elaborated, and definitionally equal to the declaration's current type. -/
  | restated
  /-- Elaborated, and NOT defeq. The statement moved. -/
  | changed
  /-- Could not be parsed or elaborated. Nobody knows whether it changed. -/
  | notCheckable
  deriving Inhabited, DecidableEq

def RestateOutcome.toString : RestateOutcome → String
  | .restated     => "restated"
  | .changed      => "changed"
  | .notCheckable => "not_checkable"

/-- One entry's verdict, with the reason a `not_checkable` was not checkable.

The reason is not decoration: "parse failed" and "elaboration logged an error"
send a reviewer to different places, and an outcome with no reason is one
nobody can act on. -/
structure RestateResult where
  key : String
  decl : Name
  outcome : RestateOutcome
  reason : String := ""
  deriving Inhabited

/-- Elaborate `s` as a term in the current environment, with the message log
sandboxed.

Returns `none` when the statement could not be parsed or elaborated, together
with why. The snapshot/restore around the attempt is what stops a logged
elaboration error from both being mistaken for success AND appearing in the
enclosing build's output. -/
def elabStatement? (s : String) : TermElabM (Except String Expr) := do
  let env ← getEnv
  match Parser.runParserCategory env `term s "<restate-check>" with
  | .error msg => return .error s!"parse failed: {msg}"
  | .ok stx =>
    -- SNAPSHOT. `elabTerm` logs rather than throws; without this the next two
    -- facts are both lost: whether this elaboration failed, and whose build
    -- output the error lands in.
    let saved ← Core.getMessageLog
    Core.setMessageLog {}
    let result ←
      try
        withoutErrToSorry do
          let e ← elabTerm stx none
          Term.synthesizeSyntheticMVarsNoPostponing
          let e ← instantiateMVars e
          pure (Except.ok e)
      catch ex =>
        pure (Except.error s!"elaboration threw: {← ex.toMessageData.toString}")
    let logged ← Core.getMessageLog
    Core.setMessageLog saved
    if logged.hasErrors then
      let msgs ← logged.toList.filterMapM fun m => do
        if m.severity == .error then pure (some (← m.data.toString)) else pure none
      return .error s!"elaboration logged an error: {msgs.head?.getD "(no message)"}"
    return result

/-- Check one reviewed statement against the declaration's current type. -/
def restateOne (key : String) (decl : Name) (statementPp : String) :
    TermElabM RestateResult := do
  let env ← getEnv
  let some ci := env.find? decl
    | return { key, decl, outcome := .notCheckable,
               reason := s!"declaration {decl} is not in this environment" }
  -- BIND THE DECLARATION'S UNIVERSE PARAMETERS FIRST.
  --
  -- A universe-polymorphic statement mentions its level parameters by name --
  -- `∀ {C : Type u_2} [inst : Category.{u_1, u_2} C] ...` -- and `elabTerm`
  -- resolves those against `Term.Context.levelNames`, which is EMPTY for a
  -- string parsed out of a review. Without this, every such statement returns
  -- `not_checkable: elaboration threw: unknown universe level`, and the review
  -- silently cannot be carried forward.
  --
  -- Found the only way it could be: the first real declaration put through this
  -- was universe-polymorphic, and the fixture that had been exercising the path
  -- since #188 was `∀ (n : Nat), n + 0 = n`, which has no universes at all.
  --
  -- The names come from the DECLARATION, not from the statement. A statement
  -- inventing a level name the declaration does not have should not elaborate:
  -- that is a different statement, and `isDefEq` is not the place to discover
  -- it.
  match ← withLevelNames ci.levelParams (elabStatement? statementPp) with
  | .error why => return { key, decl, outcome := .notCheckable, reason := why }
  | .ok e =>
    -- No `withTransparency .all`: it rescued zero cases in the spike, and every
    -- widening here is a way for a review to survive a change it should not.
    if ← isDefEq e ci.type then
      return { key, decl, outcome := .restated }
    else
      return { key, decl, outcome := .changed,
               reason := "elaborates, but is not defeq to the current type" }

/-- The `restate/1.0` document. -/
def restateJson (results : Array RestateResult) (checkedAt : String) : Json :=
  let counts : Nat × Nat × Nat :=
    results.foldl (init := (0, 0, 0)) fun (r, c, n) x =>
      match x.outcome with
      | .restated => (r + 1, c, n)
      | .changed => (r, c + 1, n)
      | .notCheckable => (r, c, n + 1)
  Json.mkObj [
    ("schema_version", Json.str "restate/1.0"),
    ("lean_version", Json.str Lean.versionString),
    ("checked_at", Json.str checkedAt),
    -- Per outcome, NEVER totalled into a verdict. `mfc join`'s J-06 gives the
    -- reason: a count of entries is not a count of trust, and a single number
    -- here would be the aggregate token every schema in this contract refuses.
    ("counts", Json.mkObj [
      ("restated", Json.num counts.1),
      ("changed", Json.num counts.2.1),
      ("not_checkable", Json.num counts.2.2)]),
    ("results", Json.arr <| (results.map fun r => Json.mkObj [
      ("key", Json.str r.key),
      ("decl", Json.str r.decl.toString),
      ("outcome", Json.str r.outcome.toString),
      ("reason", if r.reason.isEmpty then Json.null else Json.str r.reason)])
      |>.qsort (fun a b => (a.getObjValD "key").compress < (b.getObjValD "key").compress))]

/-! ## The producer

`restateOne` is the check. This is what runs it over a work-list and writes the
`restate/1.0` artifact — the piece that was missing, and without which the
check was reachable only from this package's own tests.

### The work-list arrives as JSON, and that is not an accident

The reviews live in `attest/review.yaml`. **Lean cannot read YAML**, and it is
not going to learn: this package's zero-dependency property is the argued
exception that lets it into a topic repo's environment at all, and spending it
on a YAML parser would end the argument.

So `mfc restate-plan` reads the reviews and writes `[{key, decl,
statement_pp}]`. That is the same split the contract makes everywhere else —
elaboration is Lean's, reading the verdict is `mfc`'s — and it keeps this file
free of a format it has no business parsing.

### A missing statement is `not_checkable`, never a missing row

An entry whose `statement_pp` is null still appears in the results, as
`not_checkable` with a reason. Dropping it would be `T-01`'s hole exactly: a
run that silently omits an entry looks identical to one where that entry was
fine, and the omission is invisible in the counts because the counts are
computed from the rows that are there.

### `maxHeartbeats` is finite on purpose

An explicit rendering of a real statement runs to several kilobytes, so the
default budget is raised. It is not set to `0`: an unbounded elaboration that
hangs stops CI with no artifact and no diagnosis, while an exhausted budget
surfaces through `elabStatement?` as `not_checkable` with the reason attached —
which is a worse verdict and a far better failure. -/

/-- One row of the work-list `mfc restate-plan` writes. -/
private structure PlanEntry where
  key : String
  decl : Name
  statementPp : Option String
  deriving Inhabited

private def planError (path : System.FilePath) (why : String) : String :=
  s!"mfc-restate: {path}: {why}"

/-- Read the work-list. Every failure is fatal: a plan that could not be read
is not an empty plan, and writing `restate/1.0` with zero results would report
that nothing needed checking. -/
private def readPlan (path : System.FilePath) : IO (Array PlanEntry) := do
  let text ← IO.FS.readFile path
  let json ← IO.ofExcept <| (Json.parse text).mapError fun e =>
    planError path s!"invalid JSON: {e}"
  let arr ← IO.ofExcept <| json.getArr?.mapError fun _ =>
    planError path "expected a JSON array of plan entries"
  arr.mapM fun j => do
    let key ← IO.ofExcept <| (j.getObjValAs? String "key").mapError fun _ =>
      planError path "every entry needs a string `key`"
    let decl ← IO.ofExcept <| (j.getObjValAs? String "decl").mapError fun _ =>
      planError path s!"entry {key} needs a string `decl`"
    -- Absent or null both mean "not captured". `.toOption` collapses them,
    -- which is right: the outcome is the same and the reason names it.
    pure { key, decl := decl.toName,
           statementPp := (j.getObjValAs? String "statement_pp").toOption }

private unsafe def restateToFileImpl (rootLib : Name) (additionalRoots : List Name)
    (planPath : System.FilePath) (outPath : System.FilePath) : IO UInt32 := do
  let plan ← readPlan planPath
  initSearchPath (← findSysroot)
  -- Both flags are load-bearing here for the same reasons `Emit` documents at
  -- length, and one of them harder: elaborating a statement full of instance
  -- binders needs the instance extension, and `loadExts := false` would supply
  -- it EMPTY rather than absent. Every entry would come back `not_checkable`,
  -- with a reason that looked like a real elaboration failure.
  enableInitializersExecution
  let imports := (rootLib :: additionalRoots).toArray.map
    fun m => ({ module := m } : Import)
  let env ← importModules imports {} (trustLevel := 0) (loadExts := true)
  let checkedAt ← isoUtcNow
  let work : TermElabM (Array RestateResult) := plan.mapM fun e =>
    match e.statementPp with
    | none => pure { key := e.key, decl := e.decl, outcome := .notCheckable,
                     reason := "no reviewed_statement_pp recorded" }
    | some pp => restateOne e.key e.decl pp
  let ctx : Core.Context :=
    { fileName := "<restate-check>", fileMap := default, maxHeartbeats := 1000000 }
  -- `run'` twice: the Term and Meta states are scratch here, and naming
  -- them only to discard them invites the nesting to drift.
  let (results, _) ← work.run'.run'.toIO ctx { env }
  if let some parent := outPath.parent then
    IO.FS.createDirAll parent
  IO.FS.writeFile outPath ((restateJson results checkedAt).pretty ++ "\n")
  -- EXIT 0 WHATEVER THE OUTCOMES. This produces the record; `mfc
  -- restate-check` is the gate that reads it. A producer that also judged
  -- would put the verdict in the half of the seam that cannot be audited by
  -- the other, and `changed` is not an error here -- it is the finding.
  return 0

/-- Import, check every reviewed statement, write `restate/1.0`.

`opaque` over an `unsafe` implementation for the same reason `emitToFileForRoots`
is: `importModules` is unsafe, and `unsafe def main` would push that into the
handful of lines a topic repo generates. -/
@[implemented_by restateToFileImpl]
opaque restateToFile (rootLib : Name) (additionalRoots : List Name)
    (planPath : System.FilePath) (outPath : System.FilePath) : IO UInt32


/-! ## Capturing the statement a reviewer records

`reviewed_statement_pp` is the string a review's survival depends on, and every
way of getting it wrong is SILENT: the field looks captured, `mfc validate`
passes, and the failure surfaces at the next dependency bump as
`not_checkable`, which reads like a broken tool rather than a bad capture.

Capturing the first one by hand took three attempts, with the schema, this
file, and the emitter all open:

* **Elision.** `pp.explicit` alone left a `⋯` inside a proof argument.
  Unparseable. `pp.proofs`, `pp.deepTerms` and a raised `pp.maxSteps` close it
  — which is why this builds on `Emit.ppOpts` rather than its own list.
* **Invented universe names.** Rendering via `#check @Decl` instantiates the
  constant with FRESH universe metavariables and prints them `u_1, u_2, u_3`.
  The declaration's `levelParams` were `[w, u, u']`, so `restateOne` reported
  `unknown universe level u_2`. `ci.type` carries the declaration's own names;
  the term does not.
* **A verification easier than the real check.** Both bad renderings were
  checked with a `universe u_1 u_2 u_3` command above an `example`, which
  supplies by hand the level context `restateOne` does not have.

### So the capture proves itself

`captureStatement` renders, then puts the result through `restateOne` — the
same parse, the same `elabTerm`, the same `isDefEq`, the same
`withLevelNames` — and returns the string ONLY on `restated`. All three
failures above produce a rendering that fails that check, so none of them can
reach `review.yaml` through this path.

A capture that cannot pass the check it exists to feed is not a capture. -/

/-- `Emit.ppOpts` with `pp.explicit := true`.

The one deliberate difference. The emitter renders at `pp.explicit := false`
because `type_pp` is for reading; a reviewer's statement is for RE-ELABORATING,
and implicit arguments that a human infers an elaborator must be told. -/
def capturePpOpts : Options := ppOpts.setBool `pp.explicit true

/-- Render `decl`'s type as a reviewer would record it, and verify it. -/
def captureStatement (decl : Name) : TermElabM (Except String String) := do
  let some ci := (← getEnv).find? decl
    | return .error s!"declaration {decl} is not in this environment"
  -- `ci.type`, NEVER `#check @decl`: the type carries the declaration's own
  -- level parameters, and the term carries fresh metavariables printed under
  -- invented names that no level context can bind.
  let rendered := toString (← withOptions (fun _ => capturePpOpts) (Meta.ppExpr ci.type))
  -- The self-check. Not a second opinion -- literally the function the review
  -- will be judged by, so a capture cannot pass here and fail there.
  let verdict ← restateOne "stmt:000000000000:capture" decl rendered
  match verdict.outcome with
  | .restated => return .ok rendered
  | outcome =>
      return .error s!"capture did not verify: {outcome.toString} ({verdict.reason}).\n\
        The rendering is NOT emitted. A statement that cannot be re-elaborated \
        here cannot carry a review across an environment bump either."

private unsafe def captureToFileImpl (rootLib : Name) (additionalRoots : List Name)
    (decl : Name) (outPath : Option System.FilePath) : IO UInt32 := do
  initSearchPath (← findSysroot)
  enableInitializersExecution
  let imports := (rootLib :: additionalRoots).toArray.map
    fun m => ({ module := m } : Import)
  let env ← importModules imports {} (trustLevel := 0) (loadExts := true)
  let ctx : Core.Context :=
    { fileName := "<capture>", fileMap := default, maxHeartbeats := 1000000 }
  let (result, _) ← (captureStatement decl).run'.run'.toIO ctx { env }
  match result with
  | .error why => IO.eprintln s!"mfc-capture: {why}"; return 1
  | .ok s =>
    match outPath with
    | none => IO.println s
    | some p =>
      if let some parent := p.parent then IO.FS.createDirAll parent
      IO.FS.writeFile p (s ++ "\n")
      IO.eprintln s!"mfc-capture: wrote {p} -- {s.length} chars, verified restated"
    return 0

@[implemented_by captureToFileImpl]
opaque captureToFile (rootLib : Name) (additionalRoots : List Name)
    (decl : Name) (outPath : Option System.FilePath) : IO UInt32

private def restateUsage : String :=
  "usage: <restate> --plan <path> [--out <path>]\n" ++
  "       <restate> --capture <decl> [--out <path>]\n" ++
  "  --plan <path>     work-list from `mfc restate-plan`\n" ++
  "  --capture <decl>  render one declaration's statement as a reviewer would\n" ++
  "                    record it, verified by the same check that will judge\n" ++
  "                    it; prints to stdout unless --out is given\n" ++
  "  --out <path>      destination (default: attest/restate.json, or stdout\n" ++
  "                    for --capture)"

/-- The entry point a topic repo calls. Mirrors `emitMainForRoots`. -/
def restateMainForRoots (rootLib : Name) (additionalRoots : List Name)
    (args : List String) : IO UInt32 := do
  let mut out : Option System.FilePath := none
  let mut plan : Option System.FilePath := none
  let mut capture : Option Name := none
  let mut rest := args
  repeat
    match rest with
    | "--plan" :: p :: tl => plan := some p; rest := tl
    | "--capture" :: d :: tl => capture := some d.toName; rest := tl
    | "--out" :: p :: tl => out := some p; rest := tl
    | [] => break
    | _ => IO.eprintln restateUsage; return 2
  match plan, capture with
  -- Both would be two jobs sharing one exit code, and the failure of one would
  -- be indistinguishable from the failure of the other.
  | some _, some _ => IO.eprintln restateUsage; return 2
  | some p, none => restateToFile rootLib additionalRoots p (out.getD "attest/restate.json")
  | none, some d => captureToFile rootLib additionalRoots d out
  | none, none => IO.eprintln restateUsage; return 2

/-- Single-root topic repos. -/
def restateMain (rootLib : Name) (args : List String) : IO UInt32 :=
  restateMainForRoots rootLib [] args

end MathFormalContract
