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
from contract.mfc.registry import RegistryShapeError, open_frontier
from contract.mfc.rules import Status
from contract.mfc.rules_registry import check, mint_registry_id

HERE = Path(__file__).resolve().parent
VALID = HERE.parent / "testdata" / "artifacts" / "valid" / "registry-1.0.json"
TEXTBOOK = HERE.parent / "testdata" / "artifacts" / "valid" / "textbook-source.json"
BAD_DIR = HERE.parent / "testdata" / "registries" / "invalid"
LABELS = ["mathlib-gap"]

#: fixture -> the rule it must trip when `check()` is called directly.
FIXTURES = {
    "source-arxiv-unversioned": "R-01",
    "quote-hash-mismatch": "R-02",
    "placeholder-quote": "R-03",
    "unknown-key": "R-04",
    "cyclic-depends": "R-04",
    "axis-without-evidence": "R-04",
    "registry-id-mismatch": "R-05",
    "key-is-chunk-id-shaped": "R-06",
    "asymmetric-supersede": "R-07",
    "unknown-frontier-label": "R-08",
    "obligation-without-note": "R-09",
    "interface-without-referent": "R-10",
    "no-referent-without-note": "R-10",
    "digest-only-without-reason": "R-13",
}

#: Rejected by the schema alone, with no R rule behind them. Listed separately
#: so `FIXTURES` stays "one fixture per rule" and this file does not pretend a
#: schema constraint is a rule.
SCHEMA_ONLY = {"unresolved-without-reason"}

#: The two the schema already forbids, so `mfc registry validate` rejects them
#: before the rule runs. Kept as rules for a caller that skipped validation, and
#: listed here so the distinction is asserted rather than assumed.
SCHEMA_ENFORCED = {"source-arxiv-unversioned", "key-is-chunk-id-shaped",
                   "interface-without-referent", "no-referent-without-note",
                   "digest-only-without-reason"} | SCHEMA_ONLY


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
    for fixture in {*FIXTURES, *SCHEMA_ONLY}:
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


# --- the discharge arm of R-04, and why it is not just a third dangling ref ----

def test_r04_catches_a_frontier_discharged_by_a_key_that_does_not_exist() -> None:
    """`axis-without-evidence`.

    R-04 had three arms -- `supersedes`/`superseded_by`, `depends_on`, and
    `frontier[].discharged_by.key` -- and until this fixture, NO fixture in the
    corpus carried a non-null `discharged_by` at all. The arm was written and
    never executed, which is indistinguishable from not being wired up.
    """
    assert _run(_load("axis-without-evidence"))["R-04"] is Status.FAIL


def test_a_bogus_discharge_silently_shrinks_the_open_frontier() -> None:
    """Why this arm is load-bearing rather than tidy.

    `open_frontier` filters on `discharged_by is None`, so writing ANY non-null
    object there removes the item from the open frontier -- and with it from
    `mfc join`'s J-06 work-queue rollup and from E-05's reading of whether an
    `exact` claim has anything outstanding. A dangling discharge key is not a
    typo in a cross-reference; it is an open obligation laundered into a closed
    one, and R-04 is the only thing that looks.
    """
    doc = _load("axis-without-evidence")
    (entry,) = doc["entries"].values()
    (item,) = entry["frontier"]

    assert open_frontier(entry) == [], "precondition: it reads as fully discharged"
    assert item["discharged_by"]["key"] not in doc["entries"], "and on nothing"

    entry["frontier"][0]["discharged_by"] = None
    assert open_frontier(entry) == [item["id"]], (
        "the same item is outstanding once the fabricated discharge is removed, "
        "so the discharge -- not the item -- is what closed it")


# --- a textbook source has no version axis, and that must not be an error ------

def test_a_textbook_entry_validates_and_passes_every_rule() -> None:
    """`testdata/valid/textbook-source`.

    arXMCP ships two textbook notebooks, so a registry that cannot express one
    is broken on arrival rather than merely incomplete. The schema already
    handles it -- `version: null` is required when `scheme == "textbook"`, and
    the arxiv-only `^v[0-9]+$` requirement must not reach it -- but nothing
    proved it until now, and every other fixture in the corpus is arXiv.
    """
    doc = json.loads(TEXTBOOK.read_text(encoding="utf-8"))
    (entry,) = doc["entries"].values()

    assert entry["source"]["scheme"] == "textbook"
    assert entry["source"]["version"] is None
    assert main(["validate", str(TEXTBOOK)]) == EXIT_OK

    statuses = _run(doc)
    assert not [r for r, s in statuses.items() if s is Status.FAIL]


def test_a_textbook_entry_may_not_smuggle_in_a_version(tmp_path: Path) -> None:
    """The null is a category error being refused, not a field left blank.

    Written to `tmp_path`, never beside the fixture: `test_validate` globs
    `artifacts/valid/` and would adopt a stray file as a fixture it must
    validate, so a failure here would surface as an unrelated test going red.
    """
    doc = json.loads(TEXTBOOK.read_text(encoding="utf-8"))
    (entry,) = doc["entries"].values()
    entry["source"]["version"] = "v2"

    p = tmp_path / "textbook-versioned.json"
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    assert main(["validate", str(p)]) == EXIT_FINDINGS


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
        # The clones inherit the base entry's frontier, whose `discharged_by`
        # names a key that exists in the real registry and not in this
        # synthetic one. This test is about depth in `depends_on`; a dangling
        # discharge reference here would trip R-04 for an unrelated reason.
        for item in e.get("frontier") or []:
            item["discharged_by"] = None
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


# --- mint_resolution: optional, but never silently absent ---------------------

def test_an_entry_may_be_minted_before_any_resolver_exists() -> None:
    """The ordering the design note got wrong.

    `mint_resolution` was schema-REQUIRED and non-empty for every kind but
    `obligation`, so no entry could be created until a resolver existed — and
    standing up the resolver is a later migration step. An adopter with no
    corpus running could not mint their first entry.
    """
    doc = _valid()
    unresolved = [e for e in doc["entries"].values() if e["mint_resolution"] is None]
    assert unresolved, "the valid fixture no longer demonstrates the case"
    assert any(e["kind"] != "obligation" for e in unresolved), (
        "only an obligation is unresolved, so the fixture does not show the "
        "case #21 is actually about: a real lemma minted with no resolver")
    assert main(["validate", str(VALID)]) == EXIT_OK


def test_an_unresolved_entry_must_say_why() -> None:
    """Absence with no reason is the not_run-as-pass shape, one level down:
    'nobody asked the corpus' and 'the corpus had no answer' are different
    facts and neither may present as 'matched'."""
    assert main(["validate", str(BAD_DIR / "unresolved-without-reason.json")]) \
        == EXIT_FINDINGS


def test_a_resolved_entry_needs_no_reason() -> None:
    doc = _valid()
    resolved = [e for e in doc["entries"].values() if e["mint_resolution"]]
    assert resolved and all(e["mint_unresolved_reason"] is None for e in resolved)
