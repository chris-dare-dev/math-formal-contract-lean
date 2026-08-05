"""Tests for `mfc registry` — the R-01..R-09 rules and the id mint.

Every rule has a fixture that must trip it. Two of them turn out to be trippable
only at the *schema* layer, and the tests below say which is which rather than
letting a fixture pass the suite while proving nothing — see
`SCHEMA_ENFORCED`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.mfc.cli import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, main
from contract.mfc.digest import quote_sha256
from contract.mfc.registry import RegistryShapeError
from contract.mfc.rules import Status
from contract.mfc.rules_registry import check, mint_registry_id

HERE = Path(__file__).resolve().parent
VALID = HERE.parent / "testdata" / "artifacts" / "valid" / "registry-1.0.json"
BAD_DIR = HERE.parent / "testdata" / "registries" / "invalid"
LABELS = ["mathlib-gap"]

#: fixture -> the rule it must trip when `check()` is called directly.
FIXTURES = {
    "source-arxiv-unversioned": "R-01",
    "quote-hash-mismatch": "R-02",
    "placeholder-quote": "R-03",
    "unknown-key": "R-04",
    "cyclic-depends": "R-04",
    "registry-id-mismatch": "R-05",
    "key-is-chunk-id-shaped": "R-06",
    "asymmetric-supersede": "R-07",
    "unknown-frontier-label": "R-08",
    "obligation-without-note": "R-09",
}

#: The two the schema already forbids, so `mfc registry validate` rejects them
#: before the rule runs. Kept as rules for a caller that skipped validation, and
#: listed here so the distinction is asserted rather than assumed.
SCHEMA_ENFORCED = {"source-arxiv-unversioned", "key-is-chunk-id-shaped"}


def _load(name: str) -> dict:
    return json.loads((BAD_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _valid() -> dict:
    return json.loads(VALID.read_text(encoding="utf-8"))


def _run(doc: dict, **kw) -> dict[str, Status]:
    kw.setdefault("frontier_kind_labels", LABELS)
    return {r.rule: r.status for r in check(doc, **kw)}


# --- every rule fires ---------------------------------------------------------

@pytest.mark.parametrize("fixture,rule", sorted(FIXTURES.items()))
def test_each_fixture_trips_its_rule(fixture: str, rule: str) -> None:
    assert _run(_load(fixture))[rule] is Status.FAIL, (
        f"{fixture} did not trip {rule}; a rule with no failing case is "
        f"indistinguishable from one that is not wired up")


def test_the_valid_registry_passes_every_rule() -> None:
    statuses = _run(_valid())
    assert not [r for r, s in statuses.items() if s is not Status.PASS]


def test_every_fixture_is_rejected_by_the_cli_somehow() -> None:
    """Whatever layer catches it, nothing in `invalid/` may exit 0."""
    for fixture in FIXTURES:
        rc = main(["registry", "validate", str(BAD_DIR / f"{fixture}.json"),
                   "--frontier-kind-labels", *LABELS])
        assert rc == EXIT_FINDINGS, f"{fixture} exited {rc}"


# --- which layer actually catches it ------------------------------------------

@pytest.mark.parametrize("fixture", sorted(SCHEMA_ENFORCED))
def test_the_schema_catches_these_before_any_rule_runs(fixture: str) -> None:
    """`R-01` and `R-06` are not rules JSON Schema cannot express — it does.

    `source.version` carries `pattern: ^v[0-9]+$`, and `entries` carries
    `propertyNames: {$ref: citationKey}`. Asserted so that relaxing either
    constraint is a visible decision rather than a silent handoff to a rule the
    CLI never reaches.
    """
    assert main(["validate", str(BAD_DIR / f"{fixture}.json")]) == EXIT_FINDINGS


@pytest.mark.parametrize("fixture",
                         sorted(set(FIXTURES) - SCHEMA_ENFORCED))
def test_the_others_are_schema_valid_and_fail_only_on_content(fixture: str) -> None:
    """A fixture rejected by the schema would never reach the rule under test."""
    assert main(["validate", str(BAD_DIR / f"{fixture}.json")]) == EXIT_OK


def test_no_fixture_carries_a_marker_key() -> None:
    """`additionalProperties: false` would reject the marker, not the defect."""
    for path in BAD_DIR.glob("*.json"):
        assert "$comment_fixture" not in json.loads(path.read_text(encoding="utf-8"))


# --- the rules that matter most -----------------------------------------------

def test_r02_recomputes_rather_than_trusting() -> None:
    """The only check comparing a registry digest to what it summarizes."""
    doc = _valid()
    key = next(iter(doc["entries"]))
    doc["entries"][key]["quote"] = doc["entries"][key]["quote"] + " Edited."
    assert _run(doc)["R-02"] is Status.FAIL


def test_r02_is_whitespace_insensitive_like_the_digest_it_uses() -> None:
    """A re-wrap must not rotate a quote digest, or every citation breaks on a
    reformat nobody made."""
    doc = _valid()
    key = next(iter(doc["entries"]))
    doc["entries"][key]["quote"] = doc["entries"][key]["quote"].replace(" ", "\n  ")
    assert _run(doc)["R-02"] is Status.PASS


def test_r02_skips_digest_only_entries() -> None:
    """There is no inline text to recompute from; the schema covers the rest."""
    doc = _valid()
    key = next(iter(doc["entries"]))
    doc["entries"][key].update(quote_mode="digest_only", quote=None,
                               quote_sha256=quote_sha256("anything"))
    assert _run(doc)["R-02"] is Status.PASS


def test_r04_reports_a_cycle_once_from_its_smallest_member() -> None:
    """Deterministic output: the same cycle must not be reported twice, or from
    a different node run to run."""
    results = check(_load("cyclic-depends"), frontier_kind_labels=LABELS)
    r04 = next(r for r in results if r.rule == "R-04")
    cycles = [f for f in r04.findings if "cycle" in f.detail]
    assert len(cycles) == 1
    assert cycles[0].where == min(_load("cyclic-depends")["entries"])


def test_r04_tolerates_a_deep_dependency_chain() -> None:
    """Iterative, not recursive: a deep chain must be a finding or a pass, never
    a RecursionError reported as a crash."""
    doc = _valid()
    base = dict(next(iter(doc["entries"].values())))
    entries = {}
    keys = [f"stmt:9f4c1a20b7d3:d{i:04d}" for i in range(2000)]
    for i, key in enumerate(keys):
        e = json.loads(json.dumps(base))
        e["depends_on"] = [keys[i + 1]] if i + 1 < len(keys) else []
        entries[key] = e
    doc["entries"] = entries
    assert _run(doc)["R-04"] is Status.PASS


def test_r08_is_not_run_without_the_topics_allowlist() -> None:
    """It is a per-topic configuration value, not a property of the contract."""
    assert check(_valid())[7].rule == "R-08"
    assert check(_valid())[7].status is Status.NOT_RUN


def test_r08_ignores_entries_with_no_label() -> None:
    doc = _valid()
    for entry in doc["entries"].values():
        for item in entry["frontier"]:
            item["kind_label"] = None
    assert _run(doc)["R-08"] is Status.PASS


# --- an unreadable document is not an empty one -------------------------------

def test_an_unreadable_registry_raises_rather_than_passing() -> None:
    with pytest.raises(RegistryShapeError):
        check({"statements": []})


def test_the_cli_reports_an_unreadable_registry_as_usage_not_findings(
        tmp_path: Path) -> None:
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"schema_version": "registry/1.0"}), encoding="utf-8")
    assert main(["registry", "validate", str(p)]) == EXIT_FINDINGS  # schema first


# --- the id mint --------------------------------------------------------------

def test_the_minted_id_is_twelve_lowercase_hex() -> None:
    for bits in (0, 1, 2 ** 48 - 1, 0x9F4C1A20B7D3):
        got = mint_registry_id(lambda _n, v=bits: v)
        assert len(got) == 12
        assert got == got.lower()
        int(got, 16)


def test_the_mint_pads_small_values() -> None:
    """`f"{1:x}"` is `'1'`. A 1-character registry id would fail every key."""
    assert mint_registry_id(lambda _n: 1) == "000000000000"[:-1] + "1"
    assert len(mint_registry_id(lambda _n: 1)) == 12


def test_the_mint_asks_for_48_bits() -> None:
    """12 hex digits is exactly 48 bits; asking for fewer silently narrows it."""
    seen = []
    mint_registry_id(lambda n: seen.append(n) or 0)
    assert seen == [48]


def test_cli_init_prints_a_usable_id(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["registry", "init"]) == EXIT_OK
    out = capsys.readouterr()
    minted = out.out.strip()
    assert len(minted) == 12 and int(minted, 16) >= 0
    assert "not derived from the notebook slug" in out.err


def test_cli_validate_rejects_a_missing_file(tmp_path: Path) -> None:
    assert main(["registry", "validate", str(tmp_path / "nope.json")]) == EXIT_USAGE


def test_cli_validate_passes_the_valid_fixture() -> None:
    assert main(["registry", "validate", str(VALID),
                 "--frontier-kind-labels", *LABELS]) == EXIT_OK


def test_cli_require_all_is_findings_without_the_allowlist() -> None:
    assert main(["registry", "validate", str(VALID)]) == EXIT_OK
    assert main(["registry", "validate", str(VALID), "--require-all"]) == EXIT_FINDINGS
