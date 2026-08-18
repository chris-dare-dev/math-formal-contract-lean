"""What the quote digest survives, as data. #162, red-team gap 11.

The design claimed `quote_sha256` survives "a chunker bump, an ar5iv
re-render, a LaTeXML upgrade, an HTML→MinerU migration". Only the first class
does. `testdata/rotations/quote-rotations.json` states each class with the two
outcomes that matter — does the digest survive, does containment survive — and
this file asserts the real functions against them.

The rigged-test problem is the reason the fixtures are shaped this way. The
original flagship test mutated **whitespace**, which `nfc-ws-collapse/1`
absorbs by construction: it proved the normalization works and was read as
proving the digest survives re-rendering. `rewrapped` is still here, now
labelled as the whitespace class it is, sitting beside two re-chunk cases that
show what the digest actually cannot do.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.mfc.containment import contains, survives
from contract.mfc.digest import quote_sha256

HERE = Path(__file__).resolve().parent
CASES = json.loads(
    (HERE.parent / "testdata" / "rotations" / "quote-rotations.json")
    .read_text(encoding="utf-8"))["cases"]
IDS = [c["id"] for c in CASES]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_each_rotation_class_behaves_as_recorded(case: dict) -> None:
    digest_ok, contained = survives(
        case["quote"], case["body_before"], case["body_after"])
    assert digest_ok is case["digest_survives"], case["why"]
    assert contained is case["containment_survives"], case["why"]


def test_a_chunker_bump_breaks_the_digest_exactly_as_it_breaks_chunk_id() -> None:
    """The claim the design got wrong, stated as an assertion.

    Re-chunking changes `body_text`. The digest is over `body_text`. There is
    no arrangement of normalization that survives that, which is why the fix
    is a new resolution rung and not a better hash.
    """
    merged = next(c for c in CASES if c["id"] == "chunks-merged")
    assert quote_sha256(merged["body_before"]) != quote_sha256(merged["body_after"])
    assert contains(merged["quote"], merged["body_after"]), \
        "the statement is still in the corpus; only the boundaries moved"


def test_containment_survives_a_merge_and_a_split() -> None:
    """The two fixtures #162 asks for, in one assertion each."""
    for case_id in ("chunks-merged", "chunk-split-around"):
        case = next(c for c in CASES if c["id"] == case_id)
        assert not survives(case["quote"], case["body_before"],
                            case["body_after"])[0]
        assert contains(case["quote"], case["body_after"])


def test_a_split_through_the_statement_is_not_recoverable() -> None:
    """The honest limit. Containment is not a rescue for every rotation.

    A resolver facing this must say `unresolvable`, not reach for a similarity
    score — which is why `fuzzy` may never read `current`.
    """
    case = next(c for c in CASES if c["id"] == "chunk-split-through")
    assert not contains(case["quote"], case["body_after"])


def test_the_whitespace_case_is_labelled_as_whitespace() -> None:
    """The rigged test, kept and named.

    It is a true statement about the normalization. It is not evidence about
    re-chunking, and the fixture now says which of the two it is.
    """
    case = next(c for c in CASES if c["id"] == "rewrapped")
    assert case["class"] == "whitespace"
    assert case["digest_survives"] is True


def test_containment_inherits_the_digests_insensitivities() -> None:
    """Both sides go through `norm_text`, so a re-wrapped body still contains
    a re-wrapped quote."""
    assert contains("Let  X\nbe a variety.", "Preamble. Let X be a variety. Rest.")


def test_containment_is_identity_not_similarity() -> None:
    """A near-miss is not containment, which is what lets this rung read
    `current` where `fuzzy` may not."""
    assert not contains("Let X be a smooth variety.", "Let X be a variety.")


def test_the_fixture_covers_both_outcomes_in_both_directions() -> None:
    """A corpus of cases that all agree proves nothing."""
    digest = {c["digest_survives"] for c in CASES}
    contained = {c["containment_survives"] for c in CASES}
    assert digest == {True, False}
    assert contained == {True, False}


# --------------------------------------------------------------------------
# The split: machine-owned `quote`, human-owned `quote_as_read`.
# --------------------------------------------------------------------------

def test_r12_rejects_a_quote_as_read_that_corrects_nothing() -> None:
    """The field records what the corpus got wrong. A copy records nothing
    while implying a human checked the text against the paper."""
    from contract.mfc.rules_registry import check as registry_check
    from contract.mfc.rules import Status as S

    doc = json.loads((HERE.parent / "testdata" / "artifacts" / "valid" /
                      "registry-1.0.json").read_text(encoding="utf-8"))
    key = next(k for k, e in doc["entries"].items() if e.get("quote"))
    doc["entries"][key]["quote_as_read"] = doc["entries"][key]["quote"]
    results = {r.rule: r for r in registry_check(doc,
                                                 frontier_kind_labels=["mathlib-gap"])}
    assert results["R-12"].status is S.FAIL


def test_r12_accepts_a_real_correction() -> None:
    from contract.mfc.rules_registry import check as registry_check
    from contract.mfc.rules import Status as S

    doc = json.loads((HERE.parent / "testdata" / "artifacts" / "valid" /
                      "registry-1.0.json").read_text(encoding="utf-8"))
    key = next(k for k, e in doc["entries"].items() if e.get("quote"))
    doc["entries"][key]["quote_as_read"] = doc["entries"][key]["quote"] + " (sic)"
    results = {r.rule: r for r in registry_check(doc,
                                                 frontier_kind_labels=["mathlib-gap"])}
    assert results["R-12"].status is S.PASS


def test_the_digest_still_comes_from_the_machine_owned_quote() -> None:
    """R-02 is what enforces the split; R-12 only guards the noise case.

    Hashing the human-corrected text instead of the chunk-equal text is the
    mistake that silently demotes an entry to `printed_number` — 36 of 66
    chunks even on the flagship paper — or to `unresolvable`.
    """
    from contract.mfc.rules_registry import check as registry_check
    from contract.mfc.rules import Status as S

    doc = json.loads((HERE.parent / "testdata" / "artifacts" / "valid" /
                      "registry-1.0.json").read_text(encoding="utf-8"))
    key = next(k for k, e in doc["entries"].items() if e.get("quote"))
    entry = doc["entries"][key]
    entry["quote_as_read"] = entry["quote"] + " corrected"
    entry["quote_sha256"] = quote_sha256(entry["quote_as_read"])
    results = {r.rule: r for r in registry_check(doc,
                                                 frontier_kind_labels=["mathlib-gap"])}
    assert results["R-02"].status is S.FAIL, \
        "hashing quote_as_read must fail; the digest is over the chunk-equal text"
