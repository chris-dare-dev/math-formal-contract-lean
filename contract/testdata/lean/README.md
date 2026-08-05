# Lean fixtures — the ones that have to compile

Everything else in `testdata/` is JSON: a document handed to a schema or a rule.
These are Lean source, and the difference is the point.

A fixture asserting "the emission lists `sneaky`" in JSON **passes by
construction and tests nothing** — it tests whoever typed the expected answer.
The claim these fixtures exist to establish is that sweeping
`Environment.constants` *cannot* miss a declaration, and only a real build can
establish that. So each file here is copied into a scaffolded topic repo by
`test_scaffold.py`, compiled by `lake`, and swept by the real emitter.

They are **not** part of any `lean_lib` in this repo's `lakefile.toml`. That is
deliberate: they contain `sorry`, and a `sorry` inside the shipped library would
be caught by the `build` and `lean4checker` CI jobs — correctly, and for the
wrong reason. `testdata/lean/` is not a `srcDir`, so `lake build` at the repo
root never sees them.

| fixture | issue | what it establishes |
|---|---|---|
| `set-option-evasion.lean` | #28 | `set_option … in` and `open … in`, in all three legal spellings, cannot hide a declaration from an environment sweep. Each evader is a `sorry`-backed proof of `False`, so the emission must both LIST it and carry `sorryAx` in its measured axiom closure, and the emitter must exit non-zero. |

## Why `set-option-evasion.lean` ships a harmless declaration too

`harmlessCompanion` is load-bearing, not padding.

arXMCP's regex-over-source extractor recognises a declaration by the keyword
starting its line; `set_option` and `open` are in neither its keyword nor its
modifier set, so an evading line increments **neither** its site count nor its
name count — and the `sites == len(names)` fail-safe therefore never fires.

Alone, an evader yields an *empty* name list, and arXMCP abstains honestly with
`outcome: "unknown"`. It is only beside an ordinary declaration — which supplies
a non-empty name list and so defeats the empty-names abstention — that
`#print axioms` audits a set silently excluding the evader while the record
reads `clean`. A fixture without the companion would reproduce the safe case and
call it the dangerous one.

## A parse detail worth keeping

The evaders carry plain `--` comments rather than `/-- … -/` doc comments. A doc
comment must be followed by a declaration keyword, and `set_option` / `open` are
not one, so doc-commenting them is a parse error — which is a small
demonstration of the very thing under test: to the parser these lines are not
declarations at all. To the environment they are.
