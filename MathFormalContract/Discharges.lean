/-
Copyright (c) 2026 Chris Dare. All rights reserved.
Released under the MIT license.
-/
import Lean.Elab.Command

/-!
# The `@[discharges]` attribute

Anchors a frontier discharge in `Environment.constants` rather than in prose.

```lean
@[discharges "gltilde-universal-cover"]
theorem universalCoverData : ... := ...
```

## The rule this exists to satisfy

The design states its own axis-independence rule precisely: *"two axes are
distinct only if they have distinct evidence-producing programs"*, and uses it
to kill a competing design's three-views-of-one-closure axes.

**Axis 6, `frontier_discharged`, broke that rule.** `frontier[].discharged_by`
is hand-edited YAML and `mfc` only *aggregates* it. Nothing checked that the
named discharger proves anything, and — unlike the faithfulness axis, the other
human one — it carried no reviewer and no date. Two of seven axes were human,
and one of them was unattributed while presenting as computed.

With this attribute the edge is in the environment: `mfc lint` rule `E-12`
requires the declaration named by `discharged_by` to exist in the emission
**and** to carry `@[discharges "<that id>"]`. Axis 6 becomes genuinely computed
over emission ∪ registry.

## What it still does not do

It does not check that the declaration proves the frontier item. Nothing
mechanical can: a frontier item is prose about a gap in the world, and the
claim that a particular theorem closes it is a mathematical judgement. What
this removes is the weaker failure — a `discharged_by` naming a declaration
that does not exist, or that never claimed to discharge anything, which was
indistinguishable from a real discharge in every artifact.

The attribute is the author asserting the edge *in code that has to compile*,
next to the proof, where a reviewer reading the theorem sees the claim. That is
strictly more than YAML somewhere else, and strictly less than a verified
implication. Both halves of that are worth saying out loud.
-/

open Lean Elab

namespace MathFormalContract

/-- One `@[discharges]` binding. -/
structure DischargesEntry where
  /-- The declaration carrying the attribute. -/
  declName : Name
  /-- The frontier item id it claims to discharge. -/
  frontierId : String
  deriving Inhabited

/-- `^[a-z][a-z0-9._-]{0,63}$` — the frontier-id grammar, matching `@[cites]`'s
`frontier := [...]` ids so the two sides of an edge cannot disagree about what
a legal id looks like. -/
private def isFrontierId (s : String) : Bool :=
  match s.toList with
  | [] => false
  | c :: rest =>
    ('a' ≤ c && c ≤ 'z')
      && rest.length ≤ 63
      && rest.all fun d =>
          ('a' ≤ d && d ≤ 'z') || d.isDigit || d == '.' || d == '_' || d == '-'

/-- Storage for every `@[discharges]` binding, following `citesExt` exactly. -/
initialize dischargesExt :
    SimplePersistentEnvExtension DischargesEntry (Array (Array DischargesEntry)) ←
  registerSimplePersistentEnvExtension {
    addImportedFn es := es
    addEntryFn es _ := es
  }

/-- Every `@[discharges]` binding visible in `env`, imported and local.

Sorted by `(frontierId, declName)`, for the reason `citesEntries` gives: the
emission has to be byte-reproducible and traversal order is not something to
rely on. -/
def dischargesEntries (env : Environment) : Array DischargesEntry :=
  let st := PersistentEnvExtension.getState dischargesExt env
  let all := st.2.flatten.appendList st.1
  all.qsort fun a b =>
    if a.frontierId == b.frontierId then a.declName.lt b.declName
    else a.frontierId < b.frontierId

/-- Record one binding in the environment. -/
def addDischargesEntry {m : Type → Type} [MonadEnv m] (e : DischargesEntry) : m Unit :=
  modifyEnv (dischargesExt.addEntry · e)

/-- One or more frontier ids. Several is normal: a single construction often
closes more than one recorded gap, and forcing one attribute per id would make
the common case read as several unrelated claims. -/
syntax (name := dischargesAttr) "discharges" str,* : attr

initialize Lean.registerBuiltinAttribute {
  name := `dischargesAttr
  descr := "Claim that this declaration discharges the named frontier item(s)."
  add := fun decl stx _attrKind => do
    let `(attr| discharges $ids,*) := stx | throwUnsupportedSyntax
    for id in ids.getElems do
      let s := id.getString
      if !isFrontierId s then
        throwErrorAt id
          s!"frontier id must be a lowercase letter followed by up to 63 of \
            [a-z0-9._-], got '{s}'"
      addDischargesEntry { declName := decl, frontierId := s }
}

open Command in
/-- Print every `@[discharges]` binding visible here. -/
elab "#discharges_dump" : command => do
  let env ← getEnv
  for e in dischargesEntries env do
    logInfo m!"{e.frontierId} <- {e.declName}"

end MathFormalContract
