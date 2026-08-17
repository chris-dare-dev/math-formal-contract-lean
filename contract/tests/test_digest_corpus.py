"""The digest corpus — canonicalization pinned by data. #149, red-team gap 16.

`tests/test_digest.py` pins three values and a set of properties. This pins the
**bytes**: every case in `testdata/digests/canonicalization.json` carries the
exact input to `sha256`, written by hand from the spec in `mfc/digest.py` and
hashed with `shasum(1)`, alongside the digest of those bytes.

Three assertions per case, and the split is the point:

* `test_the_datum_is_internally_consistent` uses stdlib `hashlib` and touches
  no `mfc` code. It says the recorded pair is a real pair.
* `test_mfc_produces_the_recorded_canonical_form` compares `mfc`'s
  canonicalization to the recorded bytes **byte for byte**. This is the one
  that catches drift, and it fails with a diff of the bytes rather than with
  two hex strings that differ somewhere.
* `test_mfc_reproduces_the_recorded_digest` closes the loop end to end.

If canonicalization drifts, the first still passes and the second fails. A
corpus whose expectations are regenerated from the implementation would pass
all three forever, which is exactly the gap being closed.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path

import pytest

from contract.mfc.digest import (
    canonical_json,
    env_digest,
    file_digest,
    norm_text,
    quote_sha256,
    statement_digest,
)

HERE = Path(__file__).resolve().parent
TESTDATA = HERE.parent / "testdata"
CORPUS = TESTDATA / "digests" / "canonicalization.json"
VALID_DIR = TESTDATA / "artifacts" / "valid"

CASES = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]
IDS = [c["id"] for c in CASES]

#: The four digest functions. A corpus that covered three of them would leave
#: the fourth pinned by nothing while looking complete.
COVERED_FUNCTIONS = {"quote_sha256", "env_digest", "statement_digest", "file_digest"}


def _packages(case: dict) -> list[list[str]]:
    """`@environment-1.0.json` means: read the packages out of that fixture.

    Written as a reference rather than copied so the corpus and the fixture
    cannot drift apart — the design-note env_digest is the one the fixture
    claims, and if someone edits a rev in the fixture this case must move.
    """
    packages = case["input"]["packages"]
    if packages == "@environment-1.0.json":
        doc = json.loads((VALID_DIR / "environment-1.0.json").read_text(encoding="utf-8"))
        return [[p["name"], p["rev"]] for p in doc["packages"]]
    return packages


def _rebuild_canonical(case: dict) -> str:
    """What `mfc` says the canonical bytes are, for this case's input."""
    fn, inp = case["function"], case["input"]
    if fn == "quote_sha256":
        return norm_text(inp["quote"])
    if fn == "env_digest":
        return canonical_json({
            "lean_toolchain": inp["lean_toolchain"],
            "lean_githash": inp["lean_githash"],
            "lean_options": inp["lean_options"],
            "packages": [list(p) for p in sorted(_packages(case))],
        })
    if fn == "statement_digest":
        return canonical_json({
            "v": "statement-digest/1",
            "kind": inp["kind"],
            "pp": norm_text(inp["type_pp"]),
            "value_pp": norm_text(inp["value_pp"]) if inp["value_pp"] is not None else None,
            "deps": dict(sorted(inp["dep_digests"].items())),
        })
    raise AssertionError(f"no canonical form for {fn}")


def _digest(case: dict) -> str:
    """What `mfc` returns, end to end."""
    fn, inp = case["function"], case["input"]
    if fn == "quote_sha256":
        return quote_sha256(inp["quote"])
    if fn == "env_digest":
        return env_digest(inp["lean_toolchain"], inp["lean_githash"],
                          inp["lean_options"], [tuple(p) for p in _packages(case)])
    if fn == "statement_digest":
        return statement_digest(inp["name"], inp["kind"], inp["type_pp"],
                                inp["value_pp"], inp["dep_digests"])
    if fn == "file_digest":
        return file_digest(TESTDATA / inp["path"])
    raise AssertionError(f"unknown function {fn}")


# --------------------------------------------------------------------------
# 1. The datum, checked without mfc.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_datum_is_internally_consistent(case: dict) -> None:
    """stdlib only. If this fails the corpus is wrong, not the code."""
    if case["canonical_encoding"] == "raw-bytes":
        data = (TESTDATA / case["input"]["path"]).read_bytes()
        assert len(data) == case["byte_length"], \
            "the byte fixture was reformatted; see testdata/digests/README.md"
    else:
        data = case["canonical"].encode("utf-8")
    assert hashlib.sha256(data).hexdigest() == case["sha256"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_canonical_json_cases_are_actually_ascii(case: dict) -> None:
    """`ensure_ascii=True` is part of the canonicalization, so a
    `canonical-json` case containing a raw non-ASCII character is a case that
    was written by hand incorrectly — or pasted from something that is not
    this canonicalizer."""
    if case["canonical_encoding"] == "canonical-json":
        assert case["canonical"].isascii(), case["id"]


# --------------------------------------------------------------------------
# 2. mfc against the datum — bytes first, then the digest.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_mfc_produces_the_recorded_canonical_form(case: dict) -> None:
    if case["canonical_encoding"] == "raw-bytes":
        pytest.skip("D4 is uncanonicalized by construction")
    assert _rebuild_canonical(case) == case["canonical"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_mfc_reproduces_the_recorded_digest(case: dict) -> None:
    assert _digest(case) == case["sha256"]


# --------------------------------------------------------------------------
# 3. The corpus covers what it claims to.
# --------------------------------------------------------------------------

def test_every_digest_function_has_at_least_one_case() -> None:
    assert {c["function"] for c in CASES} == COVERED_FUNCTIONS


def test_ids_are_unique() -> None:
    assert len(IDS) == len(set(IDS))


def test_the_three_design_note_values_are_in_the_corpus() -> None:
    """The strongest cases: hand-written canonical forms whose `shasum` matched
    a number recorded against the real repository at `f166a3d`, months before
    this code existed. Nobody could have back-fitted them."""
    recorded = {
        "52b407ea4c1e8c51bfefe1d9a1f173e142729c6abf09a750a383869d5b160349",
        "c44dc5545999699041be0421a8767f82c45ae16d38a736db3dbf532a3d6a1acf",
        "bee014f3f5e761cfe1e329560ab0c5f26ebf3c6c24be8c85bcfed64b7cf72af2",
    }
    assert recorded <= {c["sha256"] for c in CASES}


def test_the_environment_fixture_carries_the_digest_of_its_own_contents() -> None:
    """A fixture whose declared digest is not the digest of its own fields is a
    worked example of the bug the digest exists to catch."""
    doc = json.loads((VALID_DIR / "environment-1.0.json").read_text(encoding="utf-8"))
    assert doc["env_digest"] == env_digest(
        doc["lean_toolchain"], doc["lean_githash"], doc["lean_options"],
        [(p["name"], p["rev"]) for p in doc["packages"]])


# --------------------------------------------------------------------------
# 4. The properties the corpus encodes, stated where the data cannot.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("drift,mutate", [
    ("unsorted keys", lambda o: json.dumps(o, sort_keys=False, separators=(",", ":"),
                                           ensure_ascii=True)),
    ("pretty separators", lambda o: json.dumps(o, sort_keys=True, ensure_ascii=True)),
    ("ensure_ascii off", lambda o: json.dumps(o, sort_keys=True, separators=(",", ":"),
                                              ensure_ascii=False)),
])
def test_the_corpus_discriminates_against_plausible_drifts(drift: str, mutate) -> None:
    """The assertions above are only worth having if they can fail.

    Each mutation is a canonicalization someone could plausibly write — and
    two of the three are what `json.dumps` does by DEFAULT, which is how this
    drift actually arrives. Every one must produce bytes the corpus rejects.
    """
    obj = {
        "v": "statement-digest/1", "kind": "def", "pp": "Type",
        "value_pp": "Fin 2 → ℤ", "deps": {},
    }
    by_id = {c["id"]: c for c in CASES}
    mutated = mutate(obj)
    assert hashlib.sha256(mutated.encode("utf-8")).hexdigest() != by_id["d3-leaf"]["sha256"], \
        f"{drift} produced the recorded digest, so the case pins nothing"


def test_the_rewrapped_case_shares_a_digest_with_the_plain_one() -> None:
    """Two cases, one digest — the whole point of whitespace insensitivity."""
    by_id = {c["id"]: c for c in CASES}
    assert by_id["d1-plain"]["sha256"] == by_id["d1-rewrapped"]["sha256"]
    assert by_id["d1-plain"]["input"] != by_id["d1-rewrapped"]["input"]


def test_the_decomposed_case_is_actually_decomposed() -> None:
    """If the input were already NFC this case would pin nothing."""
    by_id = {c["id"]: c for c in CASES}
    raw = by_id["d1-decomposed"]["input"]["quote"]
    assert raw != unicodedata.normalize("NFC", raw), \
        "the input must be in decomposed form or the case is vacuous"
    canonical = by_id["d1-decomposed"]["canonical"]
    # Stated as properties rather than by re-running `" ".join(x.split())`:
    # re-implementing norm_text here would just be the code agreeing with
    # itself one file further out, which is the thing being fixed.
    assert canonical == unicodedata.normalize("NFC", canonical), "canonical must be NFC"
    assert "  " not in canonical and canonical == canonical.strip(), \
        "whitespace runs must already be collapsed in the recorded canonical form"
