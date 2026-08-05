/-!
# `set_option … in` / `open … in` evasion — a fixture that must COMPILE

This file is not JSON on purpose. A JSON emission listing `sneaky` would pass
by construction and prove nothing: the claim under test is that sweeping
`Environment.constants` *cannot* miss a declaration, and only a real build can
establish that. Hand-writing the expected answer tests the fixture author.

## What it reproduces

arXMCP's `lean_verify` extracts declaration names by regex over source
(`server/handlers/lean_verify.py`, `_DECL_SITE_RE` / `_DECL_NAME_RE`). Neither
`set_option … in` nor `open … in` is in its keyword or modifier set, so such a
line increments **neither** its site count nor its name count — and the
`sites == len(names)` fail-safe therefore never fires.

`harmlessCompanion` is the load-bearing part of this fixture, not padding.
Alone, an evading declaration yields an empty name list and arXMCP abstains
honestly with `outcome: "unknown"`. It is only *beside an ordinary
declaration* that the sibling supplies a non-empty `names`, the abstention
never triggers, and `#print axioms` audits a set that silently excludes
`sneaky*` while the record reads `clean`. That pairing is the whole bug, and a
fixture without the companion would reproduce the safe case and call it the
dangerous one.

There is no text to hide in when the sweep reads the environment, so all four
declarations below must appear in the emission.

## Why the `sorry`s

Each evader is `False`. If any of these went unnoticed, what got laundered is a
proof of `False` — so the emission must both LIST them and mark them
`contains_sorry_ax`, and the emitter must exit non-zero. Listing a name while
reporting a clean axiom closure would be the same failure one layer along.
-/

namespace Evasion

/-- The ordinary declaration. Without it the evaders below are caught by
abstention rather than by measurement, and the fixture proves the wrong thing. -/
theorem harmlessCompanion : True := trivial

-- NOTE: plain comments, not `/-- … -/`. A doc comment must be followed by a
-- declaration keyword, and `set_option`/`open` are not one, so doc-commenting
-- these is a parse error -- which is itself a small demonstration that these
-- lines are not declarations to the parser, only to the environment.

-- Form 1 — `set_option … in` with the declaration on the following line.
set_option maxHeartbeats 400000 in
theorem sneakyMultiline : False := by sorry

-- Form 2 — the same, all on one line.
set_option maxHeartbeats 400000 in theorem sneakySingleLine : False := by sorry

-- Form 3 — `open … in`, a modifier of a different kind and likewise absent
-- from the regex extractor's vocabulary.
open Classical in
theorem sneakyOpenIn : False := by sorry

end Evasion
