"""The four canonical digest functions.

**This file is frozen.** Any change is a MAJOR schema bump on every artifact
that carries a digest, because every digest already published would rotate.

Transcribed from the design note rather than re-derived, and pinned by data:
`tests/test_digest.py` reproduces the three values the note recorded as
`[COMPUTED]` against the real repository at `f166a3d`. If a refactor here
changes canonicalization, those three assertions fail — which is the point.
That is red-team gap 16's mitigation ("hand-computed expected digests checked
into the fixtures"), and it is the only check that survives the fixture corpus
living inside one of the implementations it referees.

Digests are computed **here, in Python, once**. Lean core at v4.29.0 ships no
SHA-256, and Lake's `Hash` is a 64-bit non-cryptographic value that is not
portable across toolchains, so the emitter deliberately carries no digests at
all and canonicalization lives in exactly one language.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any

#: Names the normalization, so a future change is a visible version bump rather
#: than a silent rotation of every quote digest.
TEXT_NORM_ID = "nfc-ws-collapse/1"

#: Names the Merkle construction, and is hashed INTO every statement digest.
STATEMENT_DIGEST_V = "statement-digest/1"


def norm_text(s: str) -> str:
    """NFC-normalize, then collapse Unicode whitespace runs to one U+0020, strip.

    **Order is load-bearing: NFC first, then split/join.** NFC can introduce or
    remove characters that affect what counts as a whitespace run, so doing it
    second would make the result depend on the input's original composition.
    """
    return " ".join(unicodedata.normalize("NFC", s).split())


def canonical_json(obj: Any) -> str:
    """The repo-wide canonicalization.

    Byte-identical to arXMCP's `corpus_manifest.py::compute_manifest_hash` and
    `test_server_tool_schema.py::_serialize_tools`. Keeping the three the same
    is what lets a digest computed on either side of the seam be compared.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def quote_sha256(quote: str) -> str:
    """D1 — a statement's verbatim text as printed in the source.

    Deliberately whitespace-insensitive: a re-render (ar5iv to MinerU, a
    LaTeXML upgrade) that changes only wrapping must NOT rotate this, or every
    citation would break on a reformat nobody made.
    """
    return sha256_hex(norm_text(quote).encode("utf-8"))


def env_digest(
    lean_toolchain: str,
    lean_githash: str,
    lean_options: dict[str, bool | int | str],
    packages: list[tuple[str, str]],
) -> str:
    """D2 — fingerprint of the Lean environment.

    `packages` is `[(name, rev)]`, sorted. **`rev`, never `inputRev`**: nine of
    the fourteen packages in the consuming repo carry `inputRev` `main` or
    `master`, which a `lake update` would silently re-resolve — so hashing
    `inputRev` would make the digest stable across a real environment change.

    `lean_options` must be the RESOLVED `[leanOptions]` table, because
    `autoImplicit` is elaboration-affecting.
    """
    return sha256_hex(canonical_json({
        "lean_toolchain": lean_toolchain,
        "lean_githash": lean_githash,
        "lean_options": lean_options,
        "packages": [list(p) for p in sorted(packages)],
    }).encode("utf-8"))


def statement_digest(
    name: str,
    kind: str,
    type_pp: str,
    value_pp: str | None,
    dep_digests: dict[str, Any],
) -> str:
    """D3 — a Merkle node over topic-local constants.

    `dep_digests` maps each topic-local constant occurring in this constant's
    type (and, for `def`/`opaque`, in its value) to that constant's
    `statement_digest`. **External constants contribute nothing** — `env_digest`
    already pins Mathlib and the anchor by commit, so re-hashing them here would
    only duplicate that.

    `value_pp` is non-null ONLY for `def` and `opaque`. That is the whole repair
    for "edit a def's body and every dependent theorem's digest is unchanged":
    a def's body *is* its statement, while a theorem's body is a proof, and
    folding a proof in would make every proof edit look like a statement change.

    `name` is deliberately NOT hashed. A rename is not a statement change, and
    the registry key — not the Lean name — is what a citation is anchored to.
    """
    del name  # documented above; kept in the signature to mirror the spec
    return sha256_hex(canonical_json({
        "v": STATEMENT_DIGEST_V,
        "kind": kind,
        "pp": norm_text(type_pp),
        "value_pp": norm_text(value_pp) if value_pp is not None else None,
        "deps": dict(sorted(dep_digests.items())),
    }).encode("utf-8"))


def scc_placeholder(name: str) -> dict[str, str]:
    """What an in-SCC dependency contributes instead of a digest.

    Members of one mutual or inductive block cannot each hash the other without
    a cycle, so an in-SCC dependency contributes its *name* under `__scc__`.
    The emitter records `scc_members[]` on every member so this is decidable
    without re-deriving the block.
    """
    return {"__scc__": name}


def file_digest(path: str | Path) -> str:
    """D4 — raw sha256 of file BYTES.

    Not canonicalized: `bundle.json` commits to the exact bytes on disk, and
    `git diff --exit-code attest/` is what keeps them honest. Canonicalizing
    here would let the committed bytes drift from the attested ones.
    """
    return sha256_hex(Path(path).read_bytes())
