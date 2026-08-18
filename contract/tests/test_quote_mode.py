"""`quote_mode` — the owner decision on Q5 / #163, 2026-08-18.

**Required from v1, with `verbatim` the default for arXiv sources.**

The first half was already true. The second half needed a mechanism, because a
*required* field has no unspecified case — so "default" cannot mean "what you
get when it is omitted". It is expressed instead as **what deviating costs**:
an arXiv source permits inlining (Bridgeland 2007 is perpetual
non-exclusive), so `digest_only` there is a choice, and an expensive one:

* offline verification is gone — the topic repo can no longer recompute its own
  hash from the inline quote, which is the property that makes the contract
  work with arXMCP deleted from the machine;
* resolution degrades to `printed_number`, the field most likely to be
  **absent**: `_extract_printed_number` lives only in the ar5iv/LaTeXML
  chunker, it is 36 of 66 chunks even on the flagship paper, and the textbook
  and MinerU paths never populate it.

Two exemptions, and both are the rule working rather than holes in it — a
licence that forbids inlining is not a choice, and an obligation has nothing to
inline yet.
"""

from __future__ import annotations

import json
from pathlib import Path

from contract.mfc.cli import EXIT_FINDINGS, EXIT_OK, main
from contract.mfc.rules import Status
from contract.mfc.rules_registry import check

HERE = Path(__file__).resolve().parent
VALID = HERE.parent / "testdata" / "artifacts" / "valid" / "registry-1.0.json"
BAD = HERE.parent / "testdata" / "registries" / "invalid"
LABELS = ["mathlib-gap"]


def _registry() -> dict:
    return json.loads(VALID.read_text(encoding="utf-8"))


def _r13(doc: dict):
    return {r.rule: r for r in check(doc, frontier_kind_labels=LABELS)}["R-13"]


def _downgrade(doc: dict, key: str, **over) -> dict:
    e = doc["entries"][key]
    e["quote_mode"] = "digest_only"
    e["quote"] = None
    e["quote_sha256"] = None
    e.update(over)
    return doc


def _a_theorem(doc: dict) -> str:
    return next(k for k, e in doc["entries"].items() if e["kind"] != "obligation")


# --------------------------------------------------------------------------
# Required from v1 — the half that was already true.
# --------------------------------------------------------------------------

def test_quote_mode_is_required() -> None:
    """So the two grounding strengths are always distinguishable in the served
    record, rather than inferred from whether a quote happens to be present."""
    schema = json.loads((HERE.parent / "mfc" / "schema" /
                         "registry-1.0.schema.json").read_text(encoding="utf-8"))
    assert "quote_mode" in schema["$defs"]["entry"]["required"]


def test_the_shipped_registry_is_verbatim_for_its_arxiv_statements() -> None:
    """The default, as practised."""
    doc = _registry()
    statements = [e for e in doc["entries"].values()
                  if e["kind"] != "obligation" and e["source"]["scheme"] == "arxiv"]
    assert statements
    assert all(e["quote_mode"] == "verbatim" for e in statements)


# --------------------------------------------------------------------------
# Deviating costs a stated reason.
# --------------------------------------------------------------------------

def test_digest_only_on_arxiv_without_a_reason_fails() -> None:
    doc = _registry()
    assert _r13(_downgrade(doc, _a_theorem(doc))).status is Status.FAIL


def test_digest_only_on_arxiv_with_a_reason_passes() -> None:
    doc = _registry()
    key = _a_theorem(doc)
    _downgrade(doc, key, quote_mode_reason=(
        "The publisher's licence for this version does not permit "
        "redistribution of the statement text."))
    assert _r13(doc).status is Status.PASS


def test_a_blank_reason_is_not_a_reason() -> None:
    doc = _registry()
    _downgrade(doc, _a_theorem(doc), quote_mode_reason="   ")
    assert _r13(doc).status is Status.FAIL


def test_the_schema_rejects_it_before_the_rule_runs() -> None:
    """Structural first, rule second — the arrangement R-01 and R-06 record."""
    assert main(["validate", str(BAD / "digest-only-without-reason.json")]) \
        == EXIT_FINDINGS


# --------------------------------------------------------------------------
# The two exemptions, each for its own reason.
# --------------------------------------------------------------------------

def test_a_non_arxiv_source_is_not_asked_to_justify_itself() -> None:
    """A licence that forbids inlining is not a choice.

    This is the case #163 is actually about: an adopter whose source is not
    arXiv-licensed. Demanding a justification there would be demanding an
    apology for the law.
    """
    doc = _registry()
    key = _a_theorem(doc)
    _downgrade(doc, key)
    doc["entries"][key]["source"] = {"scheme": "textbook", "id": "some-book",
                                     "version": None, "printed_number": "5.1",
                                     "edition": "2nd", "isbn": None}
    assert _r13(doc).status is Status.PASS


def test_an_obligation_is_not_declining_verbatim() -> None:
    """It is work that is owed, with no statement minted yet.

    The shipped registry contains exactly this case, and it is why the
    conditional caught something real the moment it was written.
    """
    doc = _registry()
    obligations = [e for e in doc["entries"].values() if e["kind"] == "obligation"]
    assert obligations, "premise: the fixture carries an obligation"
    assert any(e["quote_mode"] == "digest_only" for e in obligations)
    assert _r13(doc).status is Status.PASS
    assert main(["validate", str(VALID)]) == EXIT_OK
