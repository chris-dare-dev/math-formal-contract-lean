# Registry rejection fixtures

At least one per `R` rule, each of which **must** trip the rule it names. A rule
with no failing case is indistinguishable from a rule that is not wired up.

`R-04` has three, because it has three independent arms — `supersedes` /
`superseded_by`, `depends_on`, and `frontier[].discharged_by.key` — and a rule
whose arms are not separately fixtured is only as tested as its best-covered
one. The third arm went unexercised until `axis-without-evidence` existed.

These carry **no `$comment_fixture` key**, unlike the fixtures under
`artifacts/invalid/`. `registry-1.0.schema.json` sets
`additionalProperties: false`, so a marker key would get the document rejected
at schema validation and it would never reach the rule under test — passing the
suite while proving nothing. The rationale lives here instead, and every file
below is a document that validates against the schema and fails on exactly one
content rule.

Two rows below name **no rule**: `source-arxiv-unversioned` and
`key-is-chunk-id-shaped` are caught by the schema (`source.version`'s
`^v[0-9]+$` pattern, and `entries`'s `propertyNames`), and
`unresolved-without-reason` has no `R` rule at all — it is purely structural.
`SCHEMA_ENFORCED` in `test_rules_registry.py` asserts which layer catches which,
so a fixture cannot pass the suite while proving nothing.

| fixture | rule | what it smuggles |
|---|---|---|
| `asymmetric-supersede.json` | `R-07` | A one-sided supersession, so the superseded entry still looks current from the other side. |
| `axis-without-evidence.json` | `R-04` | A frontier item `discharged_by` a key that was never minted. `open_frontier` filters on `discharged_by is None`, so ANY non-null object there removes the item from the open frontier, from `mfc join`'s J-06 rollup, and from E-05's reading of whether an `exact` claim has anything outstanding. Not a broken cross-reference -- an open obligation laundered into a closed one. Until this fixture no document in the corpus carried a non-null `discharged_by` at all, so R-04's discharge arm had never once executed. |
| `cyclic-depends.json` | `R-04` | A depends_on cycle: nothing can be formalized first, so the plan has no entry point. |
| `key-is-chunk-id-shaped.json` | `R-06` (schema) | A corpus chunk id pasted in as a key. Chunk ids ROTATE on any re-parse and nothing forwards the old one, so the citation silently stops resolving. |
| `obligation-without-note.json` | `R-09` | An obligation with nothing saying what is owed -- indistinguishable from a theorem entry nobody got around to. |
| `placeholder-quote.json` | `R-03` | A placeholder quote still hashes, still validates, and still serves. |
| `quote-hash-mismatch.json` | `R-02` | A digest nobody recomputed. Every other check compares registry digests to each other; only R-02 compares one to the text it summarizes. |
| `registry-id-mismatch.json` | `R-05` | A key whose middle segment is another registry's id -- the entry belongs to a different repository. |
| `source-arxiv-unversioned.json` | `R-01` (schema) | A version that looks filled and is not. A bare arXiv id resolves to LATEST and drifts silently. |
| `unknown-frontier-label.json` | `R-08` | A free-text frontier label. Two entries naming one gap differently cannot be rolled up. |
| `unresolved-without-reason.json` | schema only | A null `mint_resolution` with nothing saying why. "Nobody asked the corpus" and "the corpus had no answer" are different facts, and neither may present as *matched*. |
| `unknown-key.json` | `R-04` | depends_on names a key that is not in the registry. |
| `interface-without-referent.json` | `R-10` (schema) | A `kind_class: interface` frontier item naming no external referent. At near-zero Mathlib coverage EVERY entry looks like this: `closed_lanes` forbids nothing because there is nothing in Mathlib to forbid, `statement_digest` faithfully detects drift in structures nobody outside the repo has seen, and axes 1-4 and 6 pass greenly about a repository that has formalized nothing anyone else would recognize. Bridgeland hides it by having an anchor that supplies real definitions. |
| `no-referent-without-note.json` | `R-10` (schema) | `no_referent: true` with no `referent_note`. Admitting an interface models nothing external is legitimate -- novel mathematics does -- but an admission with no reason is the empty field again, spelled louder. |
| `digest-only-without-reason.json` | `R-13` (schema) | `digest_only` on an arXiv source with no `quote_mode_reason`. arXiv permits inlining, so verbatim was available and was declined -- and the entry loses offline verification and falls back to `printed_number`, absent for 30 of 66 chunks on the flagship paper. Obligations are exempt: they have no statement minted yet, so they are not declining anything. Owner decision Q5/#163, 2026-08-18. |
