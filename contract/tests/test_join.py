"""Tests for `mfc join` — the J-01..J-06 rules and the claim table.

The claim under test is not "the join produces rows". It is that the join is
keyed on **statement digests rather than Lean names**, because a name-keyed
join fails silently: a rename leaves the digest matching while `decl` dangles,
so the review is either lost or floats onto the wrong declaration. Every test
below that touches `decl` is aiming at that.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.mfc.cli import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, main
from contract.mfc.digest import file_digest
from contract.mfc.join import (
    NOT_APPLICABLE,
    NOT_RUN,
    claim_table,
    coverage,
    workqueue,
)
from contract.mfc.join import check as join_check
from contract.mfc.rules import Status

HERE = Path(__file__).resolve().parent
VALID_DIR = HERE.parent / "testdata" / "artifacts" / "valid"
REVIEW_DIR = HERE.parent / "testdata" / "reviews"
SCHEMA_DIR = HERE.parent / "mfc" / "schema"

KEY_A = "stmt:9f4c1a20b7d3:bridgeland2007.lem-8.2"
KEY_B = "stmt:9f4c1a20b7d3:bridgeland2007.prop-8.1"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64

#: What `_one_review` attests against. The claim table renders a verdict only
#: when the environment it is handed agrees with this.
ENV_DIGEST = "d" * 64


def _decl(name: str, digest: str, cites: list[dict]) -> dict:
    return {
        "name": name, "module": "M", "kind": "theorem", "is_internal": False,
        "statement_digest": digest, "local_deps": [], "axioms": [],
        "axioms_disallowed": [], "contains_sorry_ax": False, "local_axioms": [],
        "range": None, "cites": cites,
    }


def _cite(key: str, relation: str = "one_way", frontier=(), note=None) -> dict:
    return {"key": key, "relation_claimed": relation,
            "frontier": list(frontier), "note": note}


def _declarations(decls: list[dict]) -> dict:
    base = json.loads((VALID_DIR / "declarations-1.0.json").read_text(encoding="utf-8"))
    base["declarations"] = decls
    cited = sum(1 for d in decls if d["cites"])
    base["counts"] = {"total": len(decls), "in_scope": len(decls),
                      "internal": 0, "cited": cited}
    return base


def _review(*entries: dict) -> dict:
    return {"schema_version": "review/1.0", "reviews": list(entries)}


def _one_review(key: str, decl: str, digest: str, *,
                faithfulness: str = "adequate",
                relation: str = "one_way") -> dict:
    return {
        "key": key, "decl": decl,
        "reviewer": {"name": "Chris Dare", "email": "chris.dare.bak@gmail.com"},
        "reviewed_at": "2026-08-11",
        "reviewed_statement_digest": digest,
        "reviewed_quote_sha256": "c" * 64,
        "reviewed_env_digest": "d" * 64,
        "faithfulness": faithfulness, "relation_confirmed": relation,
        "divergences": [], "note": None,
    }


def _environment(digest: str = ENV_DIGEST) -> dict:
    base = json.loads((VALID_DIR / "environment-1.0.json").read_text(encoding="utf-8"))
    base["env_digest"] = digest
    return base


def _resolution(*results: dict) -> dict:
    base = json.loads((VALID_DIR / "resolution-1.0.json").read_text(encoding="utf-8"))
    base["results"] = list(results)
    return base


def _result(key: str, resolution: str = "current") -> dict:
    return {"key": key, "resolution": resolution, "matched_by": "quote_sha256",
            "chunk_id": "arxiv:math/0212237:a82c3230040fd724",
            "matched_body_sha256": "e" * 64, "printed_number": "8.2",
            "similarity": None, "reason": None}


#: The ordinary case: one declaration citing one key, reviewed and resolved.
def _coherent() -> tuple[dict, dict, dict]:
    decls = _declarations([_decl("Topic.thm", DIGEST_A, [_cite(KEY_A)])])
    return (decls,
            _review(_one_review(KEY_A, "Topic.thm", DIGEST_A)),
            _resolution(_result(KEY_A)))


def _run(**over) -> dict[str, Status]:
    decls, review, resolution = _coherent()
    kw = {"review": review, "resolution": resolution}
    kw.update(over)
    return {r.rule: r.status for r in join_check(kw.pop("declarations", decls), **kw)}


# --- the coherent case ---------------------------------------------------------

def test_a_coherent_set_passes_every_runnable_rule() -> None:
    statuses = _run()
    assert not [r for r, s in statuses.items() if s is Status.FAIL]


def test_only_the_workqueue_is_not_run_when_both_sides_are_present() -> None:
    statuses = _run()
    assert [r for r, s in statuses.items() if s is Status.NOT_RUN] == ["J-06"]


# --- the join key is the digest, not the name ---------------------------------

def test_a_renamed_declaration_keeps_its_review() -> None:
    """The whole reason the join is keyed on the digest.

    A rename does not change a type, so the digest still matches. A name-keyed
    join would drop this review and silently revert the axis to `not_run`.
    """
    decls = _declarations([_decl("Topic.renamed", DIGEST_A, [_cite(KEY_A)])])
    review = _review(_one_review(KEY_A, "Topic.oldName", DIGEST_A))
    statuses = _run(declarations=decls, review=review)
    assert statuses["J-01"] is Status.PASS
    rows = claim_table(decls, review=review, environment=_environment())
    assert rows[0].faithfulness == "adequate", "the review was lost on rename"


def test_j01_a_restated_declaration_orphans_its_review(caplog) -> None:
    """decl still present, digest no longer matches: the statement changed."""
    decls = _declarations([_decl("Topic.thm", DIGEST_B, [_cite(KEY_A)])])
    review = _review(_one_review(KEY_A, "Topic.thm", DIGEST_A))
    results = join_check(decls, review=review)
    j01 = next(r for r in results if r.rule == "J-01")
    assert j01.status is Status.FAIL
    assert "RESTATED" in j01.findings[0].detail


def test_j01_rename_plus_restatement_needs_a_human() -> None:
    """Neither the digest nor the name resolves. Nothing mechanical can fix it."""
    decls = _declarations([_decl("Topic.renamed", DIGEST_B, [_cite(KEY_A)])])
    review = _review(_one_review(KEY_A, "Topic.gone", DIGEST_A))
    results = join_check(decls, review=review)
    j01 = next(r for r in results if r.rule == "J-01")
    assert j01.status is Status.FAIL
    assert "human must adjudicate" in j01.findings[0].detail


def test_j02_a_decl_hint_that_points_at_the_wrong_declaration() -> None:
    """A lenient name-keyed join would attach the review to `Topic.other`."""
    decls = _declarations([
        _decl("Topic.thm", DIGEST_A, [_cite(KEY_A)]),
        _decl("Topic.other", DIGEST_B, []),
    ])
    review = _review(_one_review(KEY_A, "Topic.other", DIGEST_A))
    statuses = _run(declarations=decls, review=review)
    assert statuses["J-01"] is Status.PASS, "the digest resolves fine"
    assert statuses["J-02"] is Status.FAIL, "but the hint disagrees with it"


# --- disagreement is not deduped ----------------------------------------------

def test_j03_two_reviewers_disagreeing_is_a_finding_not_a_merge() -> None:
    decls = _declarations([_decl("Topic.thm", DIGEST_A, [_cite(KEY_A)])])
    review = _review(
        _one_review(KEY_A, "Topic.thm", DIGEST_A, faithfulness="adequate"),
        _one_review(KEY_A, "Topic.thm", DIGEST_A, faithfulness="divergent"))
    statuses = _run(declarations=decls, review=review)
    assert statuses["J-03"] is Status.FAIL


def test_j03_two_reviewers_agreeing_is_not_a_finding() -> None:
    decls = _declarations([_decl("Topic.thm", DIGEST_A, [_cite(KEY_A)])])
    review = _review(_one_review(KEY_A, "Topic.thm", DIGEST_A),
                     _one_review(KEY_A, "Topic.thm", DIGEST_A))
    assert _run(declarations=decls, review=review)["J-03"] is Status.PASS


# --- the two sides must be looking at the same work ---------------------------

def test_j04_a_cited_key_nobody_resolved() -> None:
    decls = _declarations([_decl("Topic.thm", DIGEST_A,
                                 [_cite(KEY_A), _cite(KEY_B)])])
    assert _run(declarations=decls)["J-04"] is Status.FAIL


def test_j05_a_resolution_for_a_key_nothing_cites() -> None:
    assert _run(resolution=_resolution(_result(KEY_A), _result(KEY_B)))["J-05"] \
        is Status.FAIL


# --- the workqueue is blocked, and says on what -------------------------------

def test_j06_is_not_run_and_names_what_it_waits_on() -> None:
    results = join_check(*_coherent()[:1], review=None, resolution=None)
    j06 = next(r for r in results if r.rule == "J-06")
    assert j06.status is Status.NOT_RUN
    assert "sketch" in j06.reason and "size-ceiling" in j06.reason


def test_absent_evidence_is_not_run_never_pass() -> None:
    decls, _, _ = _coherent()
    statuses = {r.rule: r.status for r in join_check(decls)}
    for rule in ("J-01", "J-02", "J-03", "J-04", "J-05", "J-06"):
        assert statuses[rule] is Status.NOT_RUN


# --- the claim table ----------------------------------------------------------

def test_every_axis_keeps_its_own_column() -> None:
    """The row this table exists for: claimed exact, unreviewed, drifted.

    No single token can say that, which is why there are three columns and not
    a verdict.
    """
    decls = _declarations([_decl("Topic.thm", DIGEST_A,
                                 [_cite(KEY_A, "exact", ("gltilde-universal-cover",))])])
    rows = claim_table(decls, review=_review(), resolution=_resolution(
        _result(KEY_A, "drifted")))
    (row,) = rows
    assert row.claimed == "exact"
    assert row.faithfulness == NOT_RUN
    assert row.resolution == "drifted"
    assert row.frontier == "gltilde-universal-cover"


def test_an_absent_axis_prints_not_run_rather_than_blank() -> None:
    decls, _, _ = _coherent()
    (row,) = claim_table(decls)
    assert row.faithfulness == NOT_RUN
    assert row.relation_confirmed == NOT_RUN
    assert row.resolution == NOT_RUN


# --- applicability: a verdict about another environment is about another
# --- environment -----------------------------------------------------------

def test_a_foreign_env_review_renders_not_applicable_not_a_verdict() -> None:
    """The `foreign-env-attestation` case, from the shipped fixture.

    arXMCP's CLAUDE.md 4.10 rule 3 binds this across the seam: an axis whose
    environment digest differs from the record's renders `not_applicable` --
    never a pass, never a fail. Before this existed the claim table joined on
    `(key, statement_digest)` alone, so a review made against Lean v4.29
    rendered its verdict verbatim in a table about v4.31.

    The fixture attests `adequate`/`exact` -- the strongest pair in the
    vocabulary -- precisely so that a regression here is loud.
    """
    review = json.loads((REVIEW_DIR / "foreign-env-attestation.json")
                        .read_text(encoding="utf-8"))
    attested = review["reviews"][0]
    decls = _declarations([_decl(attested["decl"],
                                 attested["reviewed_statement_digest"],
                                 [_cite(attested["key"], "exact")])])

    (row,) = claim_table(decls, review=review,
                         environment=_environment("f" * 64))

    assert row.faithfulness == NOT_APPLICABLE
    assert row.relation_confirmed == NOT_APPLICABLE
    assert attested["faithfulness"] != row.faithfulness, "the verdict leaked"
    assert attested["relation_confirmed"] != row.relation_confirmed


def test_the_same_review_renders_its_verdict_in_its_own_environment() -> None:
    """The control. Without it the test above passes on a table that renders
    `not_applicable` unconditionally, which would be sound and useless."""
    review = json.loads((REVIEW_DIR / "foreign-env-attestation.json")
                        .read_text(encoding="utf-8"))
    attested = review["reviews"][0]
    decls = _declarations([_decl(attested["decl"],
                                 attested["reviewed_statement_digest"],
                                 [_cite(attested["key"], "exact")])])

    (row,) = claim_table(
        decls, review=review,
        environment=_environment(attested["reviewed_env_digest"]))

    assert row.faithfulness == attested["faithfulness"] == "adequate"
    assert row.relation_confirmed == attested["relation_confirmed"] == "exact"


def test_not_applicable_is_neither_reviewed_nor_folded_into_the_remainder() -> None:
    """It gets its own count. Folding it into `reviewed` restates the hole as a
    statistic; folding it into the unreviewed remainder loses that a human has
    already read these, which is what makes them the cheapest work in the queue.
    """
    decls = _declarations([
        _decl("Topic.a", DIGEST_A, [_cite(KEY_A)]),
        _decl("Topic.b", DIGEST_B, [_cite(KEY_B)]),
    ])
    review = _review(_one_review(KEY_A, "Topic.a", DIGEST_A))
    c = coverage(claim_table(decls, review=review,
                             environment=_environment("f" * 64)))

    assert c.reviewed == 0, "a foreign-env review must not count as reviewed"
    assert c.review_not_applicable == 1
    assert c.bindings == 2


def test_without_an_environment_no_review_verdict_is_rendered() -> None:
    """Applicability cannot be established, so the axis says so.

    The conservative direction is deliberate. Rendering the verdict here would
    reintroduce the exact silent pass this module now refuses, and `not_run` is
    the spelling this package already uses for "the check did not run".
    """
    decls, review, _ = _coherent()
    (row,) = claim_table(decls, review=review)
    assert row.faithfulness == NOT_RUN
    assert row.relation_confirmed == NOT_RUN


def test_a_foreign_env_review_is_not_a_finding() -> None:
    """20 is explicit: it must RENDER not_applicable, not be rejected.

    conformance's C-10 fails a bundle that ships a foreign-env review, and that
    stays right -- it is the producer's incoherence. This is the consumer side,
    where the same document is a legitimate thing to be handed and the only
    correct response is to say it does not apply here.
    """
    review = json.loads((REVIEW_DIR / "foreign-env-attestation.json")
                        .read_text(encoding="utf-8"))
    attested = review["reviews"][0]
    decls = _declarations([_decl(attested["decl"],
                                 attested["reviewed_statement_digest"],
                                 [_cite(attested["key"], "exact")])])

    statuses = {r.rule: r.status
                for r in join_check(decls, review=review)}
    assert statuses["J-01"] is Status.PASS
    assert statuses["J-02"] is Status.PASS
    assert statuses["J-03"] is Status.PASS


# --- the work queue: the same set J-06 reports, written down --------------------

def _registry_fixture() -> dict:
    return json.loads((VALID_DIR / "registry-1.0.json").read_text(encoding="utf-8"))


def _queue(decls: dict, registry: dict) -> dict:
    return workqueue(decls, registry, registry_sha256="a" * 64,
                     declarations_sha256="b" * 64)


def test_the_queue_is_exactly_the_uncited_entries() -> None:
    registry = _registry_fixture()
    cited = "stmt:9f4c1a20b7d3:bridgeland2007.lem-8.2"
    decls = _declarations([_decl("Topic.thm", DIGEST_A, [_cite(cited)])])

    q = _queue(decls, registry)
    queued = {e["key"] for lane in q["lanes"].values() for e in lane["entries"]}

    assert cited not in queued, "a cited entry is not owed"
    assert queued == set(registry["entries"]) - {cited}


def test_the_queue_carries_no_total_at_any_level() -> None:
    """J-06's rule, as a property of the artifact rather than of the report.

    90 in one lane and 10 in another is not "100 things to do" -- they are
    different work needing different people, and one number would license
    planning against the sum.
    """
    q = _queue(_declarations([_decl("Topic.thm", DIGEST_A, [])]), _registry_fixture())

    assert "count" not in q and "total" not in q and "counts" not in q
    assert all(set(lane) == {"count", "entries"} for lane in q["lanes"].values())
    assert all(lane["count"] == len(lane["entries"]) for lane in q["lanes"].values())


def test_the_queue_is_an_inventory_and_never_a_verdict() -> None:
    """Why `join` may write this when it deliberately writes nothing else."""
    q = _queue(_declarations([_decl("Topic.thm", DIGEST_A, [])]), _registry_fixture())
    banned = {"status", "verdict", "score", "trusted", "verified", "ok",
              "passed", "result", "clean", "confidence", "valid", "success"}

    assert not banned & set(q)
    for lane in q["lanes"].values():
        for entry in lane["entries"]:
            assert not banned & set(entry)


def test_lanes_are_not_a_second_copy_of_the_kind_enum(tmp_path: Path) -> None:
    """The whole reason this was not blocked on the size-ceiling decision.

    `lanes` is keyed by whatever kind appeared, with `propertyNames` a pattern
    rather than an enum of the ten `registry/1.0` knows. A future zero-axis
    `sketch` lane is then a one-schema change, and the two copies cannot
    disagree because there is only one.
    """
    schema = json.loads((SCHEMA_DIR / "workqueue-1.0.schema.json")
                        .read_text(encoding="utf-8"))
    lanes = schema["properties"]["lanes"]

    assert "enum" not in lanes["propertyNames"]
    assert lanes["propertyNames"]["pattern"]

    registry = _registry_fixture()
    (key, entry), = list(registry["entries"].items())[:1]
    entry["kind"] = "sketch"          # not in registry/1.0's enum today
    q = _queue(_declarations([_decl("Topic.thm", DIGEST_A, [])]), registry)

    assert "sketch" in q["lanes"], "an unknown kind must still get a lane"

    p = tmp_path / "workqueue.json"
    p.write_text(json.dumps(q, indent=2), encoding="utf-8")
    assert main(["validate", str(p)]) == EXIT_OK, (
        "a lane the kind enum does not know must still validate, or this "
        "schema is blocked on the size-ceiling decision after all")


def test_an_entry_can_be_uncited_and_blocked_at_once() -> None:
    """Two different queues to work from; the frontier is not folded away."""
    q = _queue(_declarations([_decl("Topic.thm", DIGEST_A, [])]), _registry_fixture())
    entries = {e["key"]: e for lane in q["lanes"].values() for e in lane["entries"]}

    blocked = entries["stmt:9f4c1a20b7d3:obl-stab-action"]
    assert blocked["frontier_open"] == ["gltilde-universal-cover"]
    assert entries["stmt:9f4c1a20b7d3:bridgeland2007.prop-8.1"]["frontier_open"] == []


def test_the_queue_is_byte_reproducible_from_its_inputs() -> None:
    """No timestamp, on purpose, so `git diff --exit-code attest/` means
    something. Lanes sorted so a lane emptying moves one hunk."""
    decls = _declarations([_decl("Topic.thm", DIGEST_A, [])])
    first = _queue(decls, _registry_fixture())
    second = _queue(decls, _registry_fixture())

    assert json.dumps(first) == json.dumps(second)
    assert list(first["lanes"]) == sorted(first["lanes"])


def test_cli_refuses_a_workqueue_without_a_registry(tmp_path: Path) -> None:
    """An empty queue and a queue nobody computed are different files, and only
    one of them means nothing is owed."""
    p = _write(tmp_path)
    rc = main(["join", "--declarations", str(p["declarations"]),
               "--workqueue-out", str(tmp_path / "workqueue.json")])

    assert rc == EXIT_USAGE
    assert not (tmp_path / "workqueue.json").exists(), "refused, not written empty"


def test_cli_writes_a_queue_that_validates(tmp_path: Path) -> None:
    p = _write(tmp_path)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps(_registry_fixture(), indent=2), encoding="utf-8")
    out = tmp_path / "attest" / "workqueue.json"

    main(["join", "--declarations", str(p["declarations"]),
          "--registry", str(registry), "--workqueue-out", str(out)])

    assert main(["validate", str(out)]) == EXIT_OK
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["registry_sha256"] == file_digest(registry)
    assert doc["declarations_sha256"] == file_digest(p["declarations"])


def test_no_column_is_a_verdict() -> None:
    decls, review, resolution = _coherent()
    rows = claim_table(decls, review=review, resolution=resolution)
    banned = {"verdict", "status", "score", "trusted", "verified", "ok", "passed"}
    assert not banned & set(rows[0]._fields)


def test_coverage_keeps_reviewed_and_resolved_apart() -> None:
    """Two different questions about different rows; no combined ratio."""
    decls = _declarations([
        _decl("Topic.a", DIGEST_A, [_cite(KEY_A)]),
        _decl("Topic.b", DIGEST_B, [_cite(KEY_B)]),
    ])
    c = coverage(claim_table(
        decls,
        review=_review(_one_review(KEY_A, "Topic.a", DIGEST_A)),
        resolution=_resolution(_result(KEY_B)),
        environment=_environment()))
    assert (c.bindings, c.keys, c.reviewed, c.resolved) == (2, 2, 1, 1)
    assert "score" not in c._fields and "ratio" not in c._fields


def test_a_declaration_citing_two_keys_yields_two_rows() -> None:
    decls = _declarations([_decl("Topic.thm", DIGEST_A,
                                 [_cite(KEY_A), _cite(KEY_B)])])
    assert len(claim_table(decls)) == 2


def test_rows_are_sorted_so_output_is_comparable_run_to_run() -> None:
    decls = _declarations([
        _decl("Topic.z", DIGEST_B, [_cite(KEY_B)]),
        _decl("Topic.a", DIGEST_A, [_cite(KEY_A)]),
    ])
    rows = claim_table(decls)
    assert [r.key for r in rows] == sorted(r.key for r in rows)


# --- the CLI -------------------------------------------------------------------

def _write(tmp_path: Path) -> dict[str, Path]:
    decls, review, resolution = _coherent()
    out = {}
    for name, doc in (("declarations", decls), ("review", review),
                      ("resolution", resolution)):
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        out[name] = p
    return out


def test_cli_passes_on_a_coherent_set(tmp_path: Path) -> None:
    p = _write(tmp_path)
    assert main(["join", "--declarations", str(p["declarations"]),
                 "--review", str(p["review"]),
                 "--resolution", str(p["resolution"])]) == EXIT_OK


def test_the_foreign_env_fixture_is_a_valid_review(tmp_path: Path) -> None:
    """It must VALIDATE. A fixture rejected at the schema never reaches the
    behaviour it was written for -- and it carries no marker key, because
    `additionalProperties: false` would reject the document for the marker
    rather than for anything under test."""
    p = tmp_path / "review.json"
    p.write_text((REVIEW_DIR / "foreign-env-attestation.json")
                 .read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["validate", str(p)]) == EXIT_OK


def test_cli_renders_not_applicable_and_counts_it_separately(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _write(tmp_path)
    env = tmp_path / "environment.json"
    env.write_text(json.dumps(_environment("f" * 64), indent=2), encoding="utf-8")

    rc = main(["join", "--declarations", str(p["declarations"]),
               "--review", str(p["review"]), "--environment", str(env)])
    out = capsys.readouterr().out

    assert rc == EXIT_OK, "not_applicable is a rendering, never a finding"
    assert NOT_APPLICABLE in out
    assert "reviewed against another environment" in out


def test_cli_says_why_the_review_columns_are_blank_without_an_environment(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`not_run` alone cannot distinguish "nobody reviewed" from "I was not
    told which environment to judge the reviews against"."""
    p = _write(tmp_path)
    main(["join", "--declarations", str(p["declarations"]),
          "--review", str(p["review"])])
    captured = capsys.readouterr()
    assert "--review supplied without --environment" in captured.err
    assert NOT_RUN in captured.out


def test_cli_prints_the_claim_table_and_the_five_counts(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _write(tmp_path)
    main(["join", "--declarations", str(p["declarations"]),
          "--review", str(p["review"]), "--resolution", str(p["resolution"])])
    out = capsys.readouterr().out
    assert "claims" in out
    for word in ("binding(s)", "reviewed", "resolved", "open frontier"):
        assert word in out


def test_cli_require_all_is_findings_because_j06_cannot_run(tmp_path: Path) -> None:
    p = _write(tmp_path)
    assert main(["join", "--declarations", str(p["declarations"]),
                 "--review", str(p["review"]),
                 "--resolution", str(p["resolution"]),
                 "--require-all"]) == EXIT_FINDINGS


def test_cli_fails_on_an_orphaned_review(tmp_path: Path) -> None:
    p = _write(tmp_path)
    doc = json.loads(p["review"].read_text(encoding="utf-8"))
    doc["reviews"][0]["reviewed_statement_digest"] = "9" * 64
    p["review"].write_text(json.dumps(doc), encoding="utf-8")
    assert main(["join", "--declarations", str(p["declarations"]),
                 "--review", str(p["review"])]) == EXIT_FINDINGS


def test_cli_validates_its_inputs(tmp_path: Path) -> None:
    p = _write(tmp_path)
    doc = json.loads(p["declarations"].read_text(encoding="utf-8"))
    doc["declarations"][0]["axioms"] = "not-a-list"
    p["declarations"].write_text(json.dumps(doc), encoding="utf-8")
    assert main(["join", "--declarations", str(p["declarations"])]) != EXIT_OK


def test_cli_rejects_a_missing_input(tmp_path: Path) -> None:
    assert main(["join", "--declarations", str(tmp_path / "nope.json")]) == EXIT_USAGE


def test_cli_validates_the_registry_it_is_given(tmp_path: Path) -> None:
    """Now that registry-1.0.schema.json exists, --registry is checked like any
    other input. It was passed through unvalidated while no schema existed, and
    the run said so; silently trusting it would have been the lie."""
    p = _write(tmp_path)
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"statements": []}), encoding="utf-8")
    assert main(["join", "--declarations", str(p["declarations"]),
                 "--registry", str(reg)]) != EXIT_OK


def test_cli_reports_an_empty_join_rather_than_printing_nothing(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Zero bindings is what a mis-scoped emission looks like, not a clean run."""
    p = tmp_path / "declarations.json"
    p.write_text(json.dumps(_declarations([_decl("Topic.thm", DIGEST_A, [])])),
                 encoding="utf-8")
    main(["join", "--declarations", str(p)])
    assert "nothing to join" in capsys.readouterr().err


# --- J-06: the work queue, against the real registry shape --------------------

def _registry(**entries: dict) -> dict:
    """`registry/1.0`. Entries keyed BY CITATION KEY, not a `statements[]` list."""
    return {"schema_version": "registry/1.0", "registry_id": "9f4c1a20b7d3",
            "entries": entries}


def _entry(kind: str = "lemma", frontier: list | None = None) -> dict:
    return {"kind": kind, "title": "t", "informal": "i", "frontier": frontier or []}


def _open(fid: str) -> dict:
    return {"id": fid, "kind_class": "open-problem", "statement": "s",
            "discharged_by": None}


def test_j06_lists_only_uncited_entries() -> None:
    decls, _, _ = _coherent()
    registry = _registry(**{KEY_A: _entry(), KEY_B: _entry(kind="obligation")})
    results = join_check(decls, registry=registry)
    j06 = next(r for r in results if r.rule == "J-06")
    assert j06.status is Status.FAIL
    assert [f.where for f in j06.findings] == [KEY_B], "KEY_A is cited"


def test_j06_passes_when_every_entry_is_cited() -> None:
    decls, _, _ = _coherent()
    statuses = {r.rule: r.status
                for r in join_check(decls, registry=_registry(**{KEY_A: _entry()}))}
    assert statuses["J-06"] is Status.PASS


def test_j06_counts_per_kind_and_never_totals() -> None:
    """A queue of 90 in one lane and 10 in another is not '100 things to do'.

    The whole value of this file is that an agent can plan against it, and a
    single total is the one number that makes it unplannable.
    """
    decls, _, _ = _coherent()
    registry = _registry(**{
        KEY_B: _entry(kind="obligation"),
        "stmt:9f4c1a20b7d3:c": _entry(kind="obligation"),
        "stmt:9f4c1a20b7d3:d": _entry(kind="conjecture"),
    })
    j06 = next(r for r in join_check(decls, registry=registry) if r.rule == "J-06")
    assert j06.reason == "conjecture: 1, obligation: 2"
    assert "3" not in j06.reason


def test_j06_rolls_up_the_open_frontier() -> None:
    """#37 asks for the frontier rolled up: it is what makes an entry actionable."""
    decls, _, _ = _coherent()
    registry = _registry(**{KEY_B: _entry(kind="obligation",
                                          frontier=[_open("gltilde-universal-cover")])})
    j06 = next(r for r in join_check(decls, registry=registry) if r.rule == "J-06")
    assert "gltilde-universal-cover" in j06.findings[0].detail


def test_j06_reports_an_unreadable_registry_as_not_run() -> None:
    """Reading it as empty would report a CLEAN work queue -- a vacuous pass."""
    decls, _, _ = _coherent()
    j06 = next(r for r in join_check(decls, registry={"statements": []})
               if r.rule == "J-06")
    assert j06.status is Status.NOT_RUN
    assert "not readable by this build" in j06.reason
