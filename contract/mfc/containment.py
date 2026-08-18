"""`quote_containment` — the rung that survives a re-chunk. #162, gap 11.

## What the quote digest actually survives

The design claimed `quote_sha256` survives "a chunker bump, an ar5iv
re-render, a LaTeXML upgrade, an HTML→MinerU migration". **Only the first
class does**, and the distinction is not academic:

* an ingest that changes `preamble_text` and leaves `body_text` alone —
  `ingest-recover-preambles` — leaves the digest alone. Survived.
* a **chunker bump changes chunk boundaries**, which changes `body_text`,
  which changes the whitespace-collapsed NFC hash **exactly as it changes
  `chunk_id`**. Not survived, and not survivable: the digest is over the body,
  and the body is what re-chunking redefines.

The flagship test for the claim mutated *whitespace* — the one perturbation
`nfc-ws-collapse/1` absorbs by construction. It proved the normalization
works, and was read as proving the digest survives re-rendering.

## So containment, not equality

A merge concatenates two chunks; a split cuts one in half. In both cases the
statement's text is still *present* in the corpus — it is the boundaries that
moved. Normalized containment sees that, and byte-equality cannot:

    normalized(quote) ⊆ normalized(chunk_body)

This is an **identity** claim, not a similarity score, which is why it may read
`current` and `fuzzy` may not. The quote is either there or it is not; nothing
is being estimated.

## It is not in `digest.py`, deliberately

That file is frozen — every change rotates published digests. This computes no
digest and rotates nothing, so it lives here rather than acquiring the freeze's
review burden for a predicate.
"""

from __future__ import annotations

from .digest import norm_text


def contains(quote: str, body: str) -> bool:
    """Whether `body` contains `quote` under the registry's normalization.

    Both sides go through `norm_text`, so this inherits exactly the
    insensitivities the digest has — NFC composition and whitespace runs — and
    adds exactly one: where in the body the statement sits.
    """
    return norm_text(quote) in norm_text(body)


def survives(quote: str, before: str, after: str) -> tuple[bool, bool]:
    """`(digest_survives, containment_survives)` across a corpus change.

    The pair is the point. A rotation class where the first is False and the
    second is True is precisely the case `quote_containment` exists for, and
    `test_containment.py` uses this to state each class as data rather than as
    prose.
    """
    from .digest import quote_sha256
    return (quote_sha256(before) == quote_sha256(after), contains(quote, after))
