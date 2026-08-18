"""Tests for `mfc lint` — the E-01..E-10 content rules.

Every rule has a fixture that must trip it. A rule with no failing case is
indistinguishable from a rule that is not wired up, and these rules are where
the trust model is enforced rather than merely described.

The other half of the suite is about `not_run`: a rule whose input is absent
must never report `pass`, and its absence must be visible in the output rather
than folded into a count.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.mfc.cli import EXIT_FINDINGS, EXIT_OK, main
from contract.mfc.rules import Status, check, summarize

HERE = Path(__file__).resolve().parent
VALID_DIR = HERE.parent / "testdata" / "artifacts" / "valid"
BAD_DIR = HERE.parent / "testdata" / "emissions" / "invalid"
CLOSED_LANES = HERE.parent / "testdata" / "closed-lanes.json"

#: fixture -> the rule it must trip.
FIXTURES = {
    "sorry-laundered": "E-01",
    "local-axiom-undeclared": "E-02",
    "axiom-injected": "E-03",
    "relation-exact-with-frontier": "E-05",
    "no-claim-without-note": "E-06",
    "elided-type-pp": "E-07",
    "empty-emission": "E-08",
    "closed-lane-breach": "E-09",
    "unsorted-axioms": "E-10",
    "vocabulary-overclaim": "E-11",
}


def _load(p: Path) -> dict:
    d = json.loads(p.read_text(encoding="utf-8"))
    d.pop("$comment_fixture", None)
    return d


def _env() -> dict:
    return _load(VALID_DIR / "environment-1.0.json")


def _lanes() -> list[dict]:
    return json.loads(CLOSED_LANES.read_text(encoding="utf-8"))["closed_lanes"]


def _run(emission: dict, **kw) -> dict[str, Status]:
    return {r.rule: r.status for r in check(emission, _env(), **kw)}


# --- every rule fires ---------------------------------------------------------

@pytest.mark.parametrize("fixture,rule", sorted(FIXTURES.items()))
def test_each_fixture_trips_its_rule(fixture: str, rule: str) -> None:
    statuses = _run(_load(BAD_DIR / f"{fixture}.json"), closed_lanes=_lanes())
    assert statuses[rule] is Status.FAIL, (
        f"{fixture} did not trip {rule}; a rule with no failing case is "
        f"indistinguishable from one that is not wired up"
    )


def test_the_clean_emission_passes_every_runnable_rule() -> None:
    statuses = _run(_load(VALID_DIR / "emission-1.0.json"), closed_lanes=_lanes())
    failed = [r for r, s in statuses.items() if s is Status.FAIL]
    assert not failed, f"clean emission failed {failed}"


# --- not_run is never pass ----------------------------------------------------

def test_registry_rules_report_not_run_without_a_registry() -> None:
    """E-04 has no way to run without the registry, so it must say so."""
    statuses = _run(_load(VALID_DIR / "emission-1.0.json"))
    assert statuses["E-04"] is Status.NOT_RUN


def test_closed_lane_rule_reports_not_run_without_config() -> None:
    statuses = _run(_load(VALID_DIR / "emission-1.0.json"))
    assert statuses["E-09"] is Status.NOT_RUN


def test_a_not_run_rule_is_never_counted_as_passed() -> None:
    results = check(_load(VALID_DIR / "emission-1.0.json"), _env())
    passed, failed, not_run = summarize(results)
    assert not_run >= 2
    assert passed + failed + not_run == len(results)
    assert all(r.status is not Status.PASS
               for r in results if r.rule in {"E-04", "E-09"})


def test_e05_still_fails_on_the_emission_half_without_a_registry() -> None:
    """A missing registry must not suppress the half that IS checkable."""
    statuses = _run(_load(BAD_DIR / "relation-exact-with-frontier.json"))
    assert statuses["E-05"] is Status.FAIL


def _registry(**entries: dict) -> dict:
    """A `registry/1.0` document. Entries are keyed BY CITATION KEY.

    Spelled out here rather than in a helper module because getting this shape
    wrong is precisely what these tests exist to prevent: `E-04` and `E-05`
    previously read a `statements[]` list that no released schema ever had.
    """
    return {"schema_version": "registry/1.0", "registry_id": "9f4c1a20b7d3",
            "entries": entries}


def _entry(kind: str = "lemma", frontier: list | None = None) -> dict:
    return {"kind": kind, "title": "t", "informal": "i", "frontier": frontier or []}


def _frontier(fid: str, *, discharged: bool = False) -> dict:
    return {"id": fid, "kind_class": "missing-library", "statement": "s",
            "discharged_by": ({"key": "stmt:9f4c1a20b7d3:x",
                               "discharged_at": "2026-08-04",
                               "discharged_by_reviewer": "Chris Dare"}
                              if discharged else None)}


def test_e04_runs_when_a_registry_is_supplied() -> None:
    emission = _load(VALID_DIR / "emission-1.0.json")
    keys = {c["key"] for con in emission["constants"] for c in con["cites"]}
    registry = _registry(**{k: _entry() for k in keys})
    assert _run(emission, registry=registry)["E-04"] is Status.PASS


def test_e04_fails_on_a_key_absent_from_the_registry() -> None:
    emission = _load(VALID_DIR / "emission-1.0.json")
    assert _run(emission, registry=_registry())["E-04"] is Status.FAIL


def test_the_invented_statements_shape_is_refused_by_name() -> None:
    """It was read as a list of `{key, frontier}` for three rules. It never existed.

    The failure mode is the reason this is a hard error rather than a fallback:
    an empty key set makes E-04 report EVERY citation as unknown while J-06
    reports a clean work queue over zero entries -- a finding flood and a
    vacuous pass out of the same bad input.
    """
    emission = _load(VALID_DIR / "emission-1.0.json")
    results = check(emission, _env(), registry={"statements": [{"key": "x"}]})
    e04 = next(r for r in results if r.rule == "E-04")
    assert e04.status is Status.NOT_RUN
    assert "statements" in e04.reason and "no released schema" in e04.reason


def test_e05_permits_exact_when_the_frontier_is_fully_discharged() -> None:
    """An entry with nothing outstanding must not be penalised for having had
    a frontier. Length is not the predicate; `discharged_by: null` is."""
    emission = _load(BAD_DIR / "relation-exact-with-frontier.json")
    key = emission["constants"][0]["cites"][0]["key"]
    emission["constants"][0]["cites"][0]["frontier"] = []   # clear the emission half
    registry = _registry(**{key: _entry(frontier=[_frontier("a", discharged=True)])})
    assert _run(emission, registry=registry)["E-05"] is Status.PASS


def test_e05_refuses_exact_when_a_frontier_item_is_open() -> None:
    emission = _load(BAD_DIR / "relation-exact-with-frontier.json")
    key = emission["constants"][0]["cites"][0]["key"]
    emission["constants"][0]["cites"][0]["frontier"] = []
    registry = _registry(**{key: _entry(frontier=[_frontier("a", discharged=True),
                                                 _frontier("b")])})
    results = check(emission, _env(), registry=registry)
    e05 = next(r for r in results if r.rule == "E-05")
    assert e05.status is Status.FAIL
    assert "'b'" in e05.findings[0].detail and "'a'" not in e05.findings[0].detail


# --- the CLI ------------------------------------------------------------------

def test_cli_passes_on_the_clean_emission() -> None:
    assert main(["lint",
                 "--emission", str(VALID_DIR / "emission-1.0.json"),
                 "--environment", str(VALID_DIR / "environment-1.0.json")]) == EXIT_OK


def test_cli_fails_on_a_rejection_fixture(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(_load(BAD_DIR / "sorry-laundered.json")), encoding="utf-8")
    assert main(["lint", "--emission", str(p),
                 "--environment", str(VALID_DIR / "environment-1.0.json")]) == EXIT_FINDINGS


def test_require_all_turns_not_run_into_failure() -> None:
    """For the day the inputs exist and their absence should stop the build."""
    assert main(["lint",
                 "--emission", str(VALID_DIR / "emission-1.0.json"),
                 "--environment", str(VALID_DIR / "environment-1.0.json"),
                 "--require-all"]) == EXIT_FINDINGS


def test_not_run_is_reported_on_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    """Visible on every invocation, so a green lint cannot be misread."""
    main(["lint",
          "--emission", str(VALID_DIR / "emission-1.0.json"),
          "--environment", str(VALID_DIR / "environment-1.0.json")])
    err = capsys.readouterr().err
    assert "did NOT run" in err and "E-04" in err
