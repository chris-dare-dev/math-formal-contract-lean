# Registry rejection fixtures

One per `R` rule, each of which **must** trip the rule it names. A rule with no
failing case is indistinguishable from a rule that is not wired up.

These carry **no `$comment_fixture` key**, unlike the fixtures under
`artifacts/invalid/`. `registry-1.0.schema.json` sets
`additionalProperties: false`, so a marker key would get the document rejected
at schema validation and it would never reach the rule under test — passing the
suite while proving nothing. The rationale lives here instead, and every file
below is a document that validates against the schema and fails on exactly one
content rule.

| fixture | rule | what it smuggles |
|---|---|---|
| `asymmetric-supersede.json` | `R-07` | A one-sided supersession, so the superseded entry still looks current from the other side. |
| `cyclic-depends.json` | `R-04` | A depends_on cycle: nothing can be formalized first, so the plan has no entry point. |
| `key-is-chunk-id-shaped.json` | `R-06` | A corpus chunk id pasted in as a key. Chunk ids ROTATE on any re-parse and nothing forwards the old one, so the citation silently stops resolving. |
| `obligation-without-note.json` | `R-09` | An obligation with nothing saying what is owed -- indistinguishable from a theorem entry nobody got around to. |
| `placeholder-quote.json` | `R-03` | A placeholder quote still hashes, still validates, and still serves. |
| `quote-hash-mismatch.json` | `R-02` | A digest nobody recomputed. Every other check compares registry digests to each other; only R-02 compares one to the text it summarizes. |
| `registry-id-mismatch.json` | `R-05` | A key whose middle segment is another registry's id -- the entry belongs to a different repository. |
| `source-arxiv-unversioned.json` | `R-01` | A version that looks filled and is not. A bare arXiv id resolves to LATEST and drifts silently. |
| `unknown-frontier-label.json` | `R-08` | A free-text frontier label. Two entries naming one gap differently cannot be rolled up. |
| `unknown-key.json` | `R-04` | depends_on names a key that is not in the registry. |
