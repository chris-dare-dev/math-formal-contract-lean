# `digests/` — canonicalization pinned by data, not by code agreeing with itself

Red-team gap 16, and the reason it is a real gap:

> The design presents "all digesting is done by `mfc` in Python, on both sides"
> as eliminating the cross-language digest problem. It also eliminates the
> **check**: a canonicalization bug is symmetric and invisible to conformance,
> because both implementations *are* the implementation.

JSON-Schema-Test-Suite works because ~20 independent implementations run the
corpus. Here there is one. So the corpus carries the **exact bytes** that go
into `sha256`, written out, next to the digest of those bytes — and the tests
assert three separate things per case:

1. `sha256(canonical)` equals the recorded `sha256`. Stdlib `hashlib` only:
   this checks the datum is internally consistent and touches no `mfc` code.
2. `mfc`'s canonicalizer produces exactly `canonical`, compared **byte for
   byte**. This is the assertion that catches drift — key order, separators,
   `ensure_ascii`, the NFC-then-collapse order — and it names the bytes that
   changed rather than reporting two digests that differ.
3. `mfc`'s digest function returns the recorded `sha256` end to end.

If canonicalization drifts, (1) still passes and (2) fails. That is the whole
design: the datum is the referee, and it cannot move when the code moves.

## Adding a case — the one rule

**Write the `canonical` string by hand from the spec in `mfc/digest.py`, then
hash it with something that is not `mfc`.**

```bash
printf '%s' '{"deps":{},"kind":"def", ...}' > /tmp/case.bin
shasum -a 256 /tmp/case.bin
```

Pasting `canonical_json(...)`'s output, or `statement_digest(...)`'s return
value, produces a case that passes by construction and tests nothing — it
tests whoever ran the code. That is exactly the failure this directory exists
to prevent, and it is invisible in review unless someone asks how the value was
obtained.

Three of the nine cases (`d2-design-note`, `d3-leaf`, `d3-merkle`) reproduce
values the design note recorded as `[COMPUTED]` against the real repository at
`f166a3d`, months before this code existed. Those three are the strongest
evidence in the corpus: the hand-written canonical form was hashed with
`shasum` and matched a number nobody could have back-fitted.

## `canonical_encoding`

| value | what `sha256` is taken over |
|---|---|
| `utf8-text` | the normalized text itself — `quote_sha256` does **not** go through `canonical_json`, so non-ASCII stays raw |
| `canonical-json` | `json.dumps(..., sort_keys=True, separators=(",",":"), ensure_ascii=True)` — non-ASCII is `\uXXXX`-escaped |
| `raw-bytes` | the file's bytes, uncanonicalized |

The `utf8-text` / `canonical-json` split is not a detail. `d1-plain` keeps `ℂ`
raw and `d3-leaf` escapes `→` to `→`, in the same corpus, because the two
functions genuinely differ — and a reader who assumes one rule for both writes
a wrong case.

## `bytes/`

`d4-fixture.txt` exists to be hashed as bytes. Do not reformat it, do not strip
its trailing newline, and do not "fix" its wording: every one of those rotates
`d4-file-bytes`, which is the property under test.
