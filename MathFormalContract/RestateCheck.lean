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
  match ← elabStatement? statementPp with
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

end MathFormalContract
