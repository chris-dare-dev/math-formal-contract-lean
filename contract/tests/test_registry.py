"""Tests for reading a `registry/1.0` document.

These exist because the accessor was wrong for three rules at once. `E-04`, the
registry half of `E-05`, and `J-06` all read `registry["statements"]` as a list
of `{key, frontier}` — a shape no released schema ever had. The specified
document keys `entries` by citation key, and `frontier[]` holds objects whose
open state is `discharged_by: null`.

The tests below pin both halves of that: the shape, and the refusal to treat an
unrecognised document as an empty one.
"""

from __future__ import annotations

import pytest

from contract.mfc.registry import (
    RegistryShapeError,
    entries,
    kind_of,
    open_frontier,
)


def _doc(**e: dict) -> dict:
    return {"schema_version": "registry/1.0", "registry_id": "9f4c1a20b7d3",
            "entries": e}


def _item(fid: str, *, discharged: bool) -> dict:
    return {"id": fid, "kind_class": "missing-library", "statement": "s",
            "discharged_by": ({"key": "stmt:9f4c1a20b7d3:x",
                               "discharged_at": "2026-08-04",
                               "discharged_by_reviewer": "Chris Dare"}
                              if discharged else None)}


# --- the keys are property names ----------------------------------------------

def test_entries_are_keyed_by_citation_key() -> None:
    key = "stmt:9f4c1a20b7d3:bridgeland2007.lem-8.2"
    assert list(entries(_doc(**{key: {"kind": "lemma"}}))) == [key]


def test_an_empty_registry_is_readable() -> None:
    """Zero entries is a legitimate state; it is *unreadability* that is not."""
    assert entries(_doc()) == {}


# --- an unrecognised document is refused, not emptied -------------------------

def test_the_invented_statements_shape_is_named_in_the_error() -> None:
    with pytest.raises(RegistryShapeError, match="no released schema"):
        entries({"statements": [{"key": "x"}]})


def test_a_document_with_no_entries_is_refused() -> None:
    """Returning {} here would report every citation unknown AND the work queue
    clean, from one bad input."""
    with pytest.raises(RegistryShapeError, match="Refusing to read it as an empty"):
        entries({"schema_version": "registry/1.0"})


def test_entries_must_be_an_object_not_a_list() -> None:
    with pytest.raises(RegistryShapeError, match="keyed by citation key"):
        entries({"entries": [{"key": "x"}]})


def test_a_non_object_entry_is_refused() -> None:
    with pytest.raises(RegistryShapeError, match="not an object"):
        entries(_doc(**{"stmt:9f4c1a20b7d3:a": "lemma"}))


def test_a_non_object_registry_is_refused() -> None:
    with pytest.raises(RegistryShapeError, match="got list"):
        entries([])


# --- open means undischarged, not merely present ------------------------------

def test_a_fully_discharged_frontier_is_not_open() -> None:
    """The distinction E-05 was getting wrong: length is not the predicate."""
    assert open_frontier({"frontier": [_item("a", discharged=True)]}) == []


def test_only_undischarged_items_are_returned() -> None:
    entry = {"frontier": [_item("a", discharged=True), _item("b", discharged=False)]}
    assert open_frontier(entry) == ["b"]


def test_a_missing_or_null_frontier_is_empty() -> None:
    assert open_frontier({}) == []
    assert open_frontier({"frontier": None}) == []


# --- kind is read, never inferred ---------------------------------------------

def test_kind_is_reported_unknown_rather_than_guessed() -> None:
    assert kind_of({"kind": "obligation"}) == "obligation"
    assert kind_of({}) == "unknown"
    assert kind_of({"kind": 7}) == "unknown"


def test_an_unseen_kind_passes_through() -> None:
    """A `sketch` lane would be a MINOR enum addition; nothing here filters kinds."""
    assert kind_of({"kind": "sketch"}) == "sketch"
