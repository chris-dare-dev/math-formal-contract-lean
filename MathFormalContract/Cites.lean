/-
Copyright (c) 2026 Chris Dare. All rights reserved.
Released under the MIT license.
-/
import Lean.Elab.Command

/-!
# The `@[cites]` attribute

Binds a Lean declaration to a statement in a paper, by a key that a human
minted and that contains **no corpus-derived bytes**.

```lean
@[cites "stmt:9f4c1a20b7d3:bridgeland2007.lem-8.2" (relation := one_way)
        (frontier := ["gltilde-universal-cover"])]
theorem foo : ... := ...
```

## Why the key looks like that

`stmt:<12 hex>:<label>`. The 12 hex digits are a registry id minted once per
topic repo; the label is chosen by the person writing the registry entry.

The paper coordinate — arXiv id, version, printed number — is **deliberately
not in the key**. It lives in typed fields of the registry entry, so that a
`textbook:` source with no arXiv version is expressible and no tokenizer can be
broken by an id that itself contains a colon (`math/0212237` is fine; a DOI is
not). Nothing a corpus computes appears here, which is what makes a citation
survive a re-ingest that rotates every chunk id.

## Why `relation` is mandatory

It records how strongly this declaration is claimed to relate to the cited
statement, and there is no safe default. A missing relation that defaulted to
anything would be an axis inferred from silence, which the trust model forbids.
Machine artifacts always spell it `relation_claimed`; only a dated, named human
review may promote a claim to confirmed.

## What this attribute does NOT do

It does not check the mathematics, does not look at the paper, and does not
know whether the registry key exists — the registry lives in the topic repo and
is validated separately. What it checks is that the key is *well formed*, so a
typo fails at compile time instead of surfacing as an unresolvable citation
much later.
-/

open Lean Elab

namespace MathFormalContract

/-- How strongly a declaration is claimed to relate to the statement it cites.

Ordered from strongest to weakest. Every value is a *claim* by the author, not
a verified fact; confirmation is a separate, human, dated act. -/
inductive Relation where
  /-- The declaration states the cited statement, with no added hypotheses. -/
  | exact
  /-- Provably interderivable with the cited statement in this environment. -/
  | equivalent
  /-- A special case of the cited statement. -/
  | specialization
  /-- The cited statement implies this one, but not conversely. -/
  | oneWay
  /-- Related, but no implication is claimed in either direction. -/
  | noClaim
  deriving BEq, Hashable, Repr, Inhabited

/-- The wire spelling of a `Relation`. Kept as an explicit total function
rather than derived, because these strings are a contract with the schemas and
must not change when a constructor is renamed. -/
def Relation.toString : Relation → String
  | .exact          => "exact"
  | .equivalent     => "equivalent"
  | .specialization => "specialization"
  | .oneWay         => "one_way"
  | .noClaim        => "no_claim"

instance : ToString Relation := ⟨Relation.toString⟩

/-- One `@[cites]` binding: a declaration, the statement key it claims, the
strength of that claim, and the assumption-frontier ids it leaves open. -/
structure CitesEntry where
  /-- The declaration carrying the attribute. -/
  declName : Name
  /-- The registry key, of the form `stmt:<12 hex>:<label>`. -/
  key : String
  /-- The claimed relation. Never defaulted. -/
  relation : Relation
  /-- Ids of frontier items this declaration does not discharge. -/
  frontier : Array String
  /-- Free-text qualification of the claim. Empty means absent, and the
  emitter writes `null` rather than `""` — a consumer must be able to tell
  "no note" from "a note that says nothing".

  Not decoration: `relation := no_claim` says two declarations are related
  without saying how, which is unreadable without one. The emission schema
  makes `note` a required key (nullable), and `mfc lint` rule `E-06` fails a
  `no_claim` binding whose note is null. That rule is deliberately left on the
  `mfc` side rather than enforced here, so the rejection fixture it names
  stays authorable. -/
  note : String
  deriving BEq, Hashable, Inhabited

/-! ## Key validation

Deliberately hand-rolled rather than regex-based: this package has **zero
dependencies** by design (it is the named exception to the consuming repo's
one-pin rule, and that exception is bounded by the leaf property), so there is
no regex engine available and none should be added. -/

/-- Twelve lowercase hex digits. -/
private def isRegistryId (s : String) : Bool :=
  s.length == 12 && s.all fun c => c.isDigit || ('a' ≤ c && c ≤ 'f')

/-- `^[a-z][a-z0-9._-]{0,63}$` -/
private def isLocalLabel (s : String) : Bool :=
  match s.toList with
  | [] => false
  | c :: rest =>
    ('a' ≤ c && c ≤ 'z')
      && rest.length ≤ 63
      && rest.all fun d =>
          ('a' ≤ d && d ≤ 'z') || d.isDigit || d == '.' || d == '_' || d == '-'

/-- Check a citation key, returning a specific complaint rather than a bare
`false` — a malformed key is almost always a typo, and the author should not
have to re-derive the grammar from a rejection. -/
def validateKey (key : String) : Except String Unit :=
  match key.splitOn ":" with
  | ["stmt", rid, label] =>
    if !isRegistryId rid then
      .error s!"registry id must be exactly 12 lowercase hex digits, got '{rid}'"
    else if !isLocalLabel label then
      .error s!"local label must be a lowercase letter followed by up to 63 of \
        [a-z0-9._-], got '{label}'"
    else
      .ok ()
  | "stmt" :: rest =>
    .error s!"expected exactly 3 colon-separated parts, got {rest.length + 1}. \
      The paper coordinate does not belong in the key -- it is a field of the registry entry."
  | _ =>
    .error "citation key must begin with 'stmt:' (form: stmt:<12 hex>:<label>)"

/-! ## The environment extension -/

/-- Storage for every `@[cites]` binding in the environment.

`addEntryFn`/`addImportedFn` are constant, matching the idiom Mathlib's
`stacks` attribute uses: `SimplePersistentEnvExtension` already retains local
entries in the state's first component, so recomputing a derived structure on
every declaration would be pure overhead. Read it with `citesEntries`. -/
initialize citesExt :
    SimplePersistentEnvExtension CitesEntry (Array (Array CitesEntry)) ←
  registerSimplePersistentEnvExtension {
    addImportedFn es := es
    addEntryFn es _ := es
  }

/-- Every `@[cites]` binding visible in `env`, imported and local.

**Sorted by `(key, declName)`.** Emission has to be byte-reproducible for the
contract's determinism gate, and traversal order here is not something to rely
on. Sorting at the read is cheaper to guarantee than sorting at every write. -/
def citesEntries (env : Environment) : Array CitesEntry :=
  let st := PersistentEnvExtension.getState citesExt env
  let all := st.2.flatten.appendList st.1
  all.qsort fun a b =>
    if a.key == b.key then a.declName.lt b.declName else a.key < b.key

/-- Record one binding in the environment. -/
def addCitesEntry {m : Type → Type} [MonadEnv m] (e : CitesEntry) : m Unit :=
  modifyEnv (citesExt.addEntry · e)

/-! ## Syntax -/

/-- The claimed relation, as attribute syntax. Its own category so the atoms
cannot collide with tactic names. -/
declare_syntax_cat citesRelation

/-- The declaration states the cited statement. -/
syntax "exact" : citesRelation
/-- Provably interderivable with the cited statement. -/
syntax "equivalent" : citesRelation
/-- A special case of the cited statement. -/
syntax "specialization" : citesRelation
/-- The cited statement implies this one, not conversely. -/
syntax "one_way" : citesRelation
/-- Related, but no implication claimed. -/
syntax "no_claim" : citesRelation

/-- Bind this declaration to a statement in a paper.

```lean
@[cites "stmt:9f4c1a20b7d3:bridgeland2007.lem-8.2" (relation := one_way)
        (frontier := ["gltilde-universal-cover"])
        (note := "Acts on PreStabilityCondition, not StabilityCondition.")]
```

`relation` is mandatory; there is no safe default. `frontier` and `note` are
optional, independently, and in either presence combination.

The `atomic(...)` wrappers are load-bearing, not decoration. Both optional
groups open with `(`, so without them the parser commits to `frontier` on
seeing the paren and then fails with *"unexpected identifier; expected
'frontier'"* on `(note := ...)` alone — making a note impossible without a
frontier. That is the common case, since `mfc lint` rule `E-06` requires a note
exactly when `relation := no_claim`, which usually has no frontier. `atomic`
lets the parser backtrack out of the frontier group and try the note group. -/
syntax (name := citesAttr)
  "cites" str "(" &"relation" ":=" citesRelation ")"
    (atomic("(" &"frontier" ":=") "[" str,* "]" ")")?
    (atomic("(" &"note" ":=") str ")")? : attr

private def parseRelation (stx : Syntax) : CoreM Relation := do
  match stx with
  | `(citesRelation| exact)          => return .exact
  | `(citesRelation| equivalent)     => return .equivalent
  | `(citesRelation| specialization) => return .specialization
  | `(citesRelation| one_way)        => return .oneWay
  | `(citesRelation| no_claim)       => return .noClaim
  | _ => throwErrorAt stx "unknown relation"

initialize Lean.registerBuiltinAttribute {
  name := `citesAttr
  descr := "Bind this declaration to a statement in a paper by registry key."
  add := fun decl stx _attrKind => do
    let `(attr| cites $key:str (relation := $rel:citesRelation)
                  $[(frontier := [$frontier,*])]? $[(note := $note:str)]?) := stx
      | throwUnsupportedSyntax
    let keyStr := key.getString
    match validateKey keyStr with
    | .error msg => throwErrorAt key s!"malformed citation key: {msg}"
    | .ok () => pure ()
    let relation ← parseRelation rel
    let frontierIds : Array String :=
      match frontier with
      | none => #[]
      | some ids => ids.getElems.map (·.getString)
    for id in frontierIds do
      if !isLocalLabel id then
        throwErrorAt key
          s!"frontier id must be a lowercase letter followed by up to 63 of \
            [a-z0-9._-], got '{id}'"
    let noteStr : String := match note with | none => "" | some s => s.getString
    addCitesEntry
      { declName := decl, key := keyStr, relation, frontier := frontierIds, note := noteStr }
}

/-! ## Inspection

The emitter is the real consumer, but a binding you cannot see is a binding you
cannot debug. -/

open Command in
/-- Print every `@[cites]` binding visible here, in emission order. -/
elab "#cites_dump" : command => do
  let env ← getEnv
  let entries := citesEntries env
  if entries.isEmpty then
    logInfo "no @[cites] bindings in scope"
  else
    let lines := entries.map fun e =>
      let front :=
        if e.frontier.isEmpty then ""
        else s!"  frontier=[{", ".intercalate e.frontier.toList}]"
      let nt := if e.note.isEmpty then "" else s!"  note={e.note}"
      s!"{e.key}  {e.relation}  {e.declName}{front}{nt}"
    logInfo ("\n".intercalate lines.toList)

end MathFormalContract
