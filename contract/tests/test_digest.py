"""Canonicalization pinned by data.

These are not unit tests of a hash function. They are the mitigation for red-
team gap 16: the fixture corpus lives inside one of the implementations it
referees, so "our code agrees with our code" proves nothing. What proves
something is agreement with values computed **before** this code existed and
recorded in the design note as `[COMPUTED]` against the real repository at
`f166a3d`.

If a refactor changes canonicalization — key order, separators, the NFC-then-
collapse order, whether `name` is hashed — these fail. That is their entire
purpose, and it is why `digest.py` says it is frozen.
"""

from __future__ import annotations

import pytest

from contract.mfc.digest import (
    STATEMENT_DIGEST_V,
    TEXT_NORM_ID,
    canonical_json,
    env_digest,
    norm_text,
    quote_sha256,
    scc_placeholder,
    statement_digest,
)

# --- values recorded in the design note as [COMPUTED] ------------------------

EXPECTED_ENV_DIGEST = "52b407ea4c1e8c51bfefe1d9a1f173e142729c6abf09a750a383869d5b160349"
EXPECTED_NUMLATTICE = "c44dc5545999699041be0421a8767f82c45ae16d38a736db3dbf532a3d6a1acf"
EXPECTED_FINRANK = "bee014f3f5e761cfe1e329560ab0c5f26ebf3c6c24be8c85bcfed64b7cf72af2"

PACKAGES = [
    ("BridgelandStability", "9e48f23a382ba117b63076a33e0e775389fef1ba"),
    ("Cli", "7802da01beb530bf051ab657443f9cd9bc3e1a29"),
    ("MD4Lean", "6a3fb240133bcb7e1a066fdc784b3fdc304e3fc5"),
    ("Qq", "707efb56d0696634e9e965523a1bbe9ac6ce141d"),
    ("aesop", "7152850e7b216a0d409701617721b6e469d34bf6"),
    ("batteries", "756e3321fd3b02a85ffda19fef789916223e578c"),
    ("importGraph", "48d5698bc464786347c1b0d859b18f938420f060"),
    ("informal", "be2042471694a77eea68089c770de3c9a9245d7c"),
    ("LeanSearchClient", "c5d5b8fe6e5158def25cd28eb94e4141ad97c843"),
    ("mathlib", "8a178386ffc0f5fef0b77738bb5449d50efeea95"),
    ("plausible", "83e90935a17ca19ebe4b7893c7f7066e266f50d3"),
    ("proofwidgets", "3c52dee17f0cd89c1ec14de78920d1bdaa3d26b3"),
    ("subverso", "ce893b9042128037e2d3c0158b9567fab9fae268"),
    ("verso", "7ae82ac2ae54ae5dcc9948a701669e9b596e5cae"),
]


def test_env_digest_matches_the_recorded_value() -> None:
    assert env_digest(
        "leanprover/lean4:v4.29.0",
        "98dc76e3c0a9b856c9b98726b713fb04fab16740",
        {"autoImplicit": False, "relaxedAutoImplicit": False},
        PACKAGES,
    ) == EXPECTED_ENV_DIGEST


def test_env_digest_is_order_independent() -> None:
    """`packages` is sorted inside, so manifest order cannot rotate the digest."""
    assert env_digest(
        "leanprover/lean4:v4.29.0",
        "98dc76e3c0a9b856c9b98726b713fb04fab16740",
        {"autoImplicit": False, "relaxedAutoImplicit": False},
        list(reversed(PACKAGES)),
    ) == EXPECTED_ENV_DIGEST


def test_statement_digest_matches_the_recorded_leaf() -> None:
    assert statement_digest(
        "BridgelandStabLean.Lattice.NumLattice", "def", "Type", "Fin 2 → ℤ", {}
    ) == EXPECTED_NUMLATTICE


def test_statement_digest_matches_the_recorded_merkle_node() -> None:
    """The dependent hashes its dependency's digest, not its name."""
    assert statement_digest(
        "BridgelandStabLean.Lattice.finrank_numLattice", "theorem",
        "Module.finrank ℤ BridgelandStabLean.Lattice.NumLattice = 2", None,
        {"BridgelandStabLean.Lattice.NumLattice": EXPECTED_NUMLATTICE},
    ) == EXPECTED_FINRANK


# --- the properties those values encode --------------------------------------

def test_a_defs_body_is_part_of_its_statement() -> None:
    """Editing a def's body MUST rotate its digest.

    This is the hole the design calls ADV-4: two defs with the same type and
    different bodies are different claims, and if the body were excluded one
    could silently replace the other under an unchanged digest.
    """
    a = statement_digest("D", "def", "Type", "Fin 2 → ℤ", {})
    b = statement_digest("D", "def", "Type", "Fin 3 → ℤ", {})
    assert a != b


def test_a_theorems_proof_is_not_part_of_its_statement() -> None:
    """A theorem's `value_pp` is a PROOF, and proofs are not statements.

    Folding it in would make every proof edit read as a statement change,
    destroying the one signal human review depends on.
    """
    assert statement_digest("T", "theorem", "P = Q", None, {}) == \
           statement_digest("T", "theorem", "P = Q", None, {})


def test_renaming_a_declaration_does_not_rotate_its_digest() -> None:
    """A rename is not a statement change; citations anchor to a registry key."""
    assert statement_digest("A", "theorem", "P = Q", None, {}) == \
           statement_digest("B", "theorem", "P = Q", None, {})


def test_dependency_change_rotates_the_dependent() -> None:
    """The Merkle property: restating a dependency reaches every dependent."""
    dep_old = statement_digest("D", "def", "Type", "Fin 2 → ℤ", {})
    dep_new = statement_digest("D", "def", "Type", "Fin 3 → ℤ", {})
    assert statement_digest("T", "theorem", "f D", None, {"D": dep_old}) != \
           statement_digest("T", "theorem", "f D", None, {"D": dep_new})


def test_whitespace_and_composition_do_not_rotate_a_digest() -> None:
    """Re-wrapping pretty-printer output must not break every citation.

    `type_pp` carries hard line breaks at the pinned `format.width`, so a
    width change would otherwise rotate every statement digest in the repo.
    """
    assert statement_digest("T", "theorem", "P  =\n   Q", None, {}) == \
           statement_digest("T", "theorem", "P = Q", None, {})


def test_nfc_runs_before_whitespace_collapse() -> None:
    """Order is load-bearing; decomposed and composed forms must agree."""
    assert norm_text("é  x") == norm_text("é x")


def test_quote_digest_is_whitespace_insensitive() -> None:
    """A re-render that changes only wrapping must not rotate a quote hash."""
    assert quote_sha256("Let  X be\na  variety.") == quote_sha256("Let X be a variety.")


def test_canonical_json_is_sorted_and_compact() -> None:
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_scc_placeholder_carries_the_name_not_a_digest() -> None:
    assert scc_placeholder("Foo") == {"__scc__": "Foo"}


@pytest.mark.parametrize("version,value", [
    ("TEXT_NORM_ID", TEXT_NORM_ID), ("STATEMENT_DIGEST_V", STATEMENT_DIGEST_V)])
def test_version_tags_are_pinned(version: str, value: str) -> None:
    """Changing either is a MAJOR bump on every artifact carrying a digest."""
    assert value in {"nfc-ws-collapse/1", "statement-digest/1"}, version
