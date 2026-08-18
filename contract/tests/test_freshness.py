"""`mfc check-resolution` — #172, red-team gap 1 (second half).

`registry_sha256` answers "was this computed against THIS registry?" and
nothing about *when*. So a `resolution.json` produced on mint day stayed green
forever: the corpus could re-ingest, re-chunk and rotate every chunk id
underneath it while producer CI reported `pass`.

The property most of these tests defend is the SEPARATION. Drift and staleness
are different facts:

* drift — the corpus moved, and something that read `current` may not be;
* staleness — nobody has asked, and nothing is known either way.

A gate that reported one as the other would teach its reader to ignore both.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from contract.mfc.cli import EXIT_FINDINGS, EXIT_OK, main
from contract.mfc.freshness import FreshnessError
from contract.mfc.freshness import check as freshness_check
from contract.mfc.rules import Status

HERE = Path(__file__).resolve().parent
VALID = HERE.parent / "testdata" / "artifacts" / "valid" / "resolution-1.0.json"

#: Pinned, so these tests do not start failing on a calendar boundary.
GENERATED = datetime(2026, 8, 4, 8, 55, 10, tzinfo=timezone.utc)


def _doc() -> dict:
    return json.loads(VALID.read_text(encoding="utf-8"))


def _run(doc: dict, *, days_later: float = 1.0, **kw):
    kw.setdefault("now", GENERATED + timedelta(days=days_later))
    return {r.rule: r for r in freshness_check(doc, **kw)}


# --------------------------------------------------------------------------
# The separation.
# --------------------------------------------------------------------------

def test_a_moved_corpus_is_drift_and_only_drift() -> None:
    results = _run(_doc(), manifest_hash="9" * 64)
    assert results["F-02"].status is Status.FAIL
    assert results["F-03"].status is Status.PASS, \
        "a corpus that moved this morning is not a STALE resolution"


def test_an_old_resolution_is_stale_and_only_stale() -> None:
    doc = _doc()
    results = _run(doc, days_later=90,
                   manifest_hash=doc["corpus_manifest_content_hash"])
    assert results["F-03"].status is Status.FAIL
    assert results["F-02"].status is Status.PASS, \
        "nothing is known to have drifted; that is the whole point"


def test_the_stale_finding_says_nothing_drifted() -> None:
    """The wording is the feature. A reader who confuses the two stops
    believing either."""
    results = _run(_doc(), days_later=90)
    assert "NOTHING IS KNOWN TO HAVE DRIFTED" in results["F-03"].findings[0].detail


def test_both_can_fail_at_once() -> None:
    results = _run(_doc(), days_later=90, manifest_hash="9" * 64)
    assert results["F-02"].status is Status.FAIL
    assert results["F-03"].status is Status.FAIL


def test_neither_fails_on_a_fresh_matching_resolution() -> None:
    doc = _doc()
    results = _run(doc, manifest_hash=doc["corpus_manifest_content_hash"])
    assert results["F-02"].status is Status.PASS
    assert results["F-03"].status is Status.PASS


# --------------------------------------------------------------------------
# Not asking is not an answer.
# --------------------------------------------------------------------------

def test_no_manifest_hash_is_not_run_never_pass() -> None:
    """The live manifest is on the corpus side of a cold seam (ADR-0001), so a
    producer build may legitimately not have it — and must not report that as
    'the corpus has not moved'."""
    results = _run(_doc())
    assert results["F-02"].status is Status.NOT_RUN
    assert results["F-02"].reason


def test_no_registry_is_not_run() -> None:
    assert _run(_doc())["F-01"].status is Status.NOT_RUN


def test_a_registry_mismatch_fails_f01() -> None:
    results = _run(_doc(), registry_sha256="a" * 64)
    assert results["F-01"].status is Status.FAIL


def test_an_unparseable_timestamp_stops_the_check(tmp_path: Path) -> None:
    """'I cannot tell how old this is' is not 'it is fresh'."""
    doc = _doc()
    doc["generated_at"] = "last Tuesday"
    with pytest.raises(FreshnessError, match="not an ISO-8601"):
        freshness_check(doc, now=GENERATED)


def test_a_missing_timestamp_stops_the_check() -> None:
    doc = _doc()
    del doc["generated_at"]
    with pytest.raises(FreshnessError):
        freshness_check(doc, now=GENERATED)


# --------------------------------------------------------------------------
# The CLI.
# --------------------------------------------------------------------------

def test_the_cli_passes_a_fresh_resolution(tmp_path: Path) -> None:
    doc = _doc()
    doc["generated_at"] = datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z")
    p = tmp_path / "resolution.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert main(["check-resolution", str(p),
                 "--manifest-hash", doc["corpus_manifest_content_hash"]]) == EXIT_OK


def test_the_cli_fails_a_stale_one(tmp_path: Path) -> None:
    doc = _doc()
    doc["generated_at"] = "2020-01-01T00:00:00Z"
    p = tmp_path / "resolution.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert main(["check-resolution", str(p)]) == EXIT_FINDINGS


def test_the_cli_hashes_the_registry_bytes_not_a_field(tmp_path: Path) -> None:
    """F-01 must compare against what is on disk. A registry that says its own
    digest is whatever the resolution claims proves nothing."""
    doc = _doc()
    doc["generated_at"] = datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z")
    p = tmp_path / "resolution.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text('{"not": "the right bytes"}', encoding="utf-8")
    assert main(["check-resolution", str(p), "--registry", str(registry)]) \
        == EXIT_FINDINGS


def test_resolved_source_version_is_present_and_null() -> None:
    """Null is the only honest value until the corpus backfill lands: arXMCP
    records `arxiv_version` as '' for every row in every notebook."""
    for result in _doc()["results"]:
        assert "resolved_source_version" in result
        assert result["resolved_source_version"] is None
