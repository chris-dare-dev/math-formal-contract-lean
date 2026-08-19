"""`mfc conformance` — the C-01..C-12 rules over a *set* of artifacts.

`validate` asks whether one artifact is well formed. `lint` asks whether one
emission contains anything forbidden. Neither can see the failure this command
exists for: **seven individually perfect artifacts that do not describe the same
thing.** A `build.json` measured before the last commit, a `review.yaml`
performed against an earlier environment, a `declarations.json` derived from an
emission other than the one shipped — each file passes its own schema, and the
set is still a lie.

## It writes no artifact, and that is the design

The obvious shape for this command is to emit `conformance.json` carrying a
verdict. It deliberately does not, because there is nowhere honest to put one.
Every schema here sets `additionalProperties: false` precisely so that a single
collapsed trust token cannot be added to an artifact — the `aggregate-verdict`
rejection fixture is an SLSA-style `verificationResult` on the bundle, and it
must fail. A `conformance.json` with a top-level verdict would be that fixture,
mechanically produced by our own tool.

So the output is a **report and an exit code**. The report is the reviewable
object: a per-rule table plus an evidence table naming, for each measurement,
who produced it and in which environment. A reader gets the shape of the
evidence rather than a score standing in for it.

## Out-of-environment evidence is labelled, never averaged in

`C-05` is the rule that makes the evidence table mean something. A predicate
whose `env_digest` differs from the bundle's was produced somewhere else, and
its verdict does not transfer here. The contract already anticipates this — the
`provisional-self-reported` predicate type exists for exactly that case, and the
reference bundle carries one produced by a **v4.31.0** toolchain against a repo
pinned to **v4.29.0**. That is legitimate, and it must stay visibly separate. A
`build/v1` predicate carrying a foreign `env_digest` is not legitimate: it is a
measurement of another build being presented as a measurement of this one.

`env_digest: null` is a third, distinct case — the corpus resolution is not
produced in a Lean environment at all. Null is not a mismatch and not a match.

There is one way to satisfy `C-05` without fixing anything: relabel the
offending predicate as `provisional-self-reported`. That is not a hole, it is
the intended escape. Relabelling *is* the retraction — the evidence table then
reports the measurement as self-attested, from another environment, with no
schema this contract can check it against, and the reader sees a claim that has
been publicly demoted to nothing. The rule is about labelling; a bundle that
labels honestly and claims little is exactly what it is supposed to produce.

## A rule whose input is absent reports `not_run`

Same discipline as `lint`, for the same reason. A bundle with no review
predicate has nothing for `C-10` to check; reporting `pass` would make "no
review exists" indistinguishable from "the review is current".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

from .digest import env_digest as recompute_env_digest, file_digest
from .rules import Finding, RuleResult, Status
from .validate import (
    CapabilityError,
    LoadError,
    load_artifact,
    schema_path_for,
    validate_artifact,
)

#: The predicate-type namespace is the **contract's**, not the adopter's. An
#: in-toto `predicateType` names a format, and these formats are owned by this
#: repo, so every adopter's bundle carries these same URIs. Hardcoding them is
#: therefore correct rather than a portability bug.
PREDICATE_NS = (
    "https://github.com/chris-dare-dev/math-formal-contract-lean/predicate"
)

#: Recognized predicate type -> the `schema_version` its payload must declare.
#: `None` means this contract recognizes the type but defines no schema for its
#: payload, so `C-03` cannot check it — `provisional-self-reported` wraps
#: another system's format on purpose.
KNOWN_PREDICATE_TYPES: dict[str, str | None] = {
    f"{PREDICATE_NS}/environment/v1": "environment/1.0",
    f"{PREDICATE_NS}/declarations/v1": "declarations/1.0",
    f"{PREDICATE_NS}/build/v1": "build/1.0",
    f"{PREDICATE_NS}/human-review/v1": "review/1.0",
    f"{PREDICATE_NS}/corpus-resolution/v1": "resolution/1.0",
    f"{PREDICATE_NS}/provisional-self-reported/v1": None,
}

#: The only types permitted to carry an `env_digest` that is neither the
#: bundle's nor null. The name is the label; `C-05` is what makes it binding.
PROVISIONAL_TYPES = frozenset({f"{PREDICATE_NS}/provisional-self-reported/v1"})

#: Types produced outside the Lean environment, for which `env_digest: null` is
#: the correct value rather than an omission.
ENVIRONMENT_FREE_TYPES = frozenset({f"{PREDICATE_NS}/corpus-resolution/v1"})

#: Types without which the bundle attests nothing about a build (`C-12`).
#:
#: Every other rule checks what is *present*, so a bundle that simply omits its
#: `build/v1` predicate satisfies all eleven others and reports "5 predicate(s)" — the
#: vacuous pass in bundle form, and the exact shape of the emitter bug that
#: `E-08` exists for one level down.
#:
#: `human-review` and `corpus-resolution` are deliberately NOT here. A repo with
#: no reviews yet is an ordinary state, and its absence is already visible as
#: `C-10: not_run` rather than as a pass.
REQUIRED_PREDICATE_TYPES = frozenset({
    f"{PREDICATE_NS}/environment/v1",
    f"{PREDICATE_NS}/declarations/v1",
    f"{PREDICATE_NS}/build/v1",
})


def short_type(predicate_type: str) -> str:
    """`.../predicate/build/v1` -> `build`. Falls back to the whole URI."""
    parts = predicate_type.rstrip("/").split("/")
    return parts[-2] if len(parts) >= 2 and parts[-1].startswith("v") else predicate_type


class Evidence(NamedTuple):
    """One predicate, resolved against the filesystem.

    Loading happens once, up front, so twelve rules do not each re-read and
    re-parse the same six files — and so a file that could not be read is one
    fact the rules agree on rather than twelve separate guesses.
    """

    predicate: dict
    kind: str
    path: Path
    exists: bool
    actual_sha256: str | None
    document: Any
    #: Set when the artifact is present but malformed. A **finding**.
    parse_error: str | None
    #: Set when this build has no reader for the artifact. **Not** a finding —
    #: the file may be perfect; we could not look.
    blocked: str | None

    @property
    def readable(self) -> bool:
        return self.document is not None and self.parse_error is None


def gather(bundle: dict, root: Path) -> list[Evidence]:
    """Resolve every predicate in `bundle` against files under `root`."""
    out: list[Evidence] = []
    for pred in bundle.get("predicates", []):
        path = root / pred["file"]
        exists = path.is_file()
        actual = file_digest(path) if exists else None
        document: Any = None
        parse_error: str | None = None
        blocked: str | None = None
        if exists:
            try:
                document = load_artifact(path)
            except CapabilityError as exc:
                blocked = str(exc)
            except LoadError as exc:
                parse_error = str(exc)
            except OSError as exc:
                parse_error = f"unreadable: {exc}"
        out.append(Evidence(
            predicate=pred,
            kind=short_type(pred["predicateType"]),
            path=path,
            exists=exists,
            actual_sha256=actual,
            document=document,
            parse_error=parse_error,
            blocked=blocked,
        ))
    return out


def _by_kind(evidence: list[Evidence], kind: str) -> Evidence | None:
    for e in evidence:
        if e.kind == kind:
            return e
    return None


def check(
    bundle: dict,
    evidence: list[Evidence],
    *,
    emission_path: Path | None = None,
    restate: dict | None = None,
) -> list[RuleResult]:
    """Run C-01..C-12. Order is fixed so output is comparable run to run."""
    results: list[RuleResult] = []
    bundle_env = bundle.get("env_digest")

    def add(rule: str, title: str, findings: list[Finding], reason: str = "") -> None:
        results.append(RuleResult(
            rule, title, Status.FAIL if findings else Status.PASS,
            tuple(findings), reason))

    def skip(rule: str, title: str, reason: str) -> None:
        results.append(RuleResult(rule, title, Status.NOT_RUN, (), reason))

    env = _by_kind(evidence, "environment")
    environment = env.document if env and env.readable else None

    # C-01 -- every file the bundle names is on disk. A bundle listing a
    # measurement that does not exist is claiming evidence it does not have.
    add("C-01", "every predicate file exists",
        [Finding("C-01", e.predicate["file"], "named by the bundle, absent on disk")
         for e in evidence if not e.exists])

    # C-02 -- the bytes are the bytes. Without this the bundle is decoration:
    # every other link is between things it merely asserts.
    add("C-02", "every predicate sha256 matches the file on disk",
        [Finding("C-02", e.predicate["file"],
                 f"bundle says {e.predicate['sha256'][:12]}..., file is "
                 f"{e.actual_sha256[:12]}...")
         for e in evidence
         if e.exists and e.actual_sha256 != e.predicate["sha256"]])

    # C-03 -- each payload validates against the schema its type implies. Also
    # catches a predicate pointing at the wrong file, which C-01 and C-02
    # cannot: a real file with a correct digest can still be the wrong artifact.
    c03: list[Finding] = []
    c03_blocked: list[str] = []
    c03_checked = 0
    for e in evidence:
        if not e.exists:
            continue
        if e.blocked:
            c03_blocked.append(f"{e.predicate['file']} ({e.blocked})")
            continue
        if e.parse_error:
            c03.append(Finding("C-03", e.predicate["file"], e.parse_error))
            continue
        want = KNOWN_PREDICATE_TYPES.get(e.predicate["predicateType"], ...)
        if want is ... or want is None:
            continue  # unrecognized (C-04's job) or no schema by design
        declared = e.document.get("schema_version") if isinstance(e.document, dict) else None
        if declared != want:
            c03.append(Finding("C-03", e.predicate["file"],
                f"predicate type implies {want!r} but the file declares {declared!r}"))
            continue
        try:
            problems = validate_artifact(e.document, _schema_for(want))
        except LoadError as exc:
            c03_blocked.append(f"{e.predicate['file']} ({exc})")
            continue
        c03_checked += 1
        c03.extend(Finding("C-03", e.predicate["file"], str(p)) for p in problems)
    if c03:
        add("C-03", "every payload validates against its declared schema", c03)
    elif c03_checked == 0:
        skip("C-03", "every payload validates against its declared schema",
             "nothing validatable was readable: " + ("; ".join(c03_blocked) or "no files"))
    else:
        add("C-03", "every payload validates against its declared schema", [],
            reason=f"{c03_checked} validated"
                   + (f"; NOT checked: {'; '.join(c03_blocked)}" if c03_blocked else ""))

    # C-04 -- an unknown predicate type must be moved to `unrecognized_predicates`,
    # never left in `predicates[]`. Silently ignoring evidence you cannot read is
    # how a verdict gets issued over something nobody looked at.
    add("C-04", "no unrecognized predicate type sits in predicates[]",
        [Finding("C-04", e.predicate["file"],
                 f"unrecognized predicateType {e.predicate['predicateType']!r}; "
                 f"it belongs in unrecognized_predicates[]")
         for e in evidence if e.predicate["predicateType"] not in KNOWN_PREDICATE_TYPES])

    # C-05 -- out-of-environment evidence is labelled, not averaged in.
    c05: list[Finding] = []
    for e in evidence:
        declared = e.predicate.get("env_digest")
        ptype = e.predicate["predicateType"]
        if declared is None:
            if ptype not in ENVIRONMENT_FREE_TYPES and ptype in KNOWN_PREDICATE_TYPES:
                c05.append(Finding("C-05", e.predicate["file"],
                    f"env_digest is null, but {short_type(ptype)} is produced in the "
                    f"Lean environment and must say which"))
            continue
        if declared != bundle_env and ptype not in PROVISIONAL_TYPES:
            c05.append(Finding("C-05", e.predicate["file"],
                f"env_digest {declared[:12]}... differs from the bundle's "
                f"{str(bundle_env)[:12]}..., but {short_type(ptype)} is not a "
                f"provisional type -- this is a measurement of another build "
                f"presented as a measurement of this one"))
    add("C-05", "out-of-environment evidence is labelled provisional", c05)

    # C-06 -- the bundle's label for a file agrees with the file's own claim.
    c06 = [Finding("C-06", e.predicate["file"],
                   f"bundle labels it env_digest {str(e.predicate.get('env_digest'))[:12]}..."
                   f" but the artifact says {str(e.document.get('env_digest'))[:12]}...")
           for e in evidence
           if e.readable and isinstance(e.document, dict)
           and "env_digest" in e.document
           and e.document.get("env_digest") != e.predicate.get("env_digest")]
    add("C-06", "each artifact's env_digest matches its predicate's", c06)

    # C-07 -- the environment's digest RECOMPUTES. Everything above compares
    # self-asserted digests to each other; this is the one rule that checks a
    # digest against the data it claims to summarize.
    if environment is None:
        skip("C-07", "the environment digest recomputes from its own fields",
             "no readable environment predicate in the bundle")
    else:
        try:
            got = recompute_env_digest(
                environment["lean_toolchain"], environment["lean_githash"],
                environment["lean_options"],
                [(p["name"], p["rev"]) for p in environment["packages"]])
        except (KeyError, TypeError) as exc:
            add("C-07", "the environment digest recomputes from its own fields",
                [Finding("C-07", "environment", f"cannot recompute: {exc}")])
        else:
            add("C-07", "the environment digest recomputes from its own fields",
                [] if got == environment.get("env_digest") else
                [Finding("C-07", "environment",
                    f"declares {str(environment.get('env_digest'))[:12]}... but its "
                    f"{len(environment['packages'])} packages, toolchain and options "
                    f"hash to {got[:12]}...")])

    # C-08 -- the corpus side and the Lean side name the same registry.
    res = _by_kind(evidence, "corpus-resolution")
    if res is None or not res.readable:
        skip("C-08", "registry_sha256 agrees between bundle and resolution",
             "no readable corpus-resolution predicate in the bundle")
    else:
        theirs = res.document.get("registry_sha256")
        add("C-08", "registry_sha256 agrees between bundle and resolution",
            [] if theirs == bundle.get("registry_sha256") else
            [Finding("C-08", res.predicate["file"],
                f"resolution resolved against registry {str(theirs)[:12]}..., bundle "
                f"attests {str(bundle.get('registry_sha256'))[:12]}...")])

    # C-09 -- the bundle's subject is the commit the environment was measured at.
    if environment is None:
        skip("C-09", "the bundle subject is the commit the environment records",
             "no readable environment predicate in the bundle")
    else:
        subject_commits = {s["digest"]["gitCommit"] for s in bundle.get("subject", [])}
        rev = environment.get("root_package", {}).get("rev")
        add("C-09", "the bundle subject is the commit the environment records",
            [] if rev in subject_commits else
            [Finding("C-09", "subject[].digest.gitCommit",
                f"bundle attests {sorted(c[:10] for c in subject_commits)}, environment "
                f"was measured at {str(rev)[:10]}")])

    # C-10 -- a review of another environment is stale. Its verdict was about a
    # statement that may since have changed, and re-dating it is the one thing a
    # human review must never do silently.
    rev_e = _by_kind(evidence, "human-review")
    if rev_e is None:
        skip("C-10", "every human review is of this environment",
             "no human-review predicate in the bundle")
    elif not rev_e.readable:
        skip("C-10", "every human review is of this environment",
             f"review predicate not readable: {rev_e.blocked or rev_e.parse_error}")
    elif environment is None:
        skip("C-10", "every human review is of this environment",
             "no readable environment predicate to compare against")
    else:
        # A review of ANOTHER environment is stale unless a restate run says the
        # statement is unchanged. Without `restate` this is the strict digest
        # comparison it has always been; with it, `restated` -- AND ONLY
        # `restated` -- carries a review forward.
        #
        # #646: env_digest hashes every package rev, so one dependency bump
        # invalidates every review at once while the mathematics sits still. It
        # did exactly that to the first review ever recorded. Carrying forward
        # on evidence is the difference between that costing a re-read per
        # review per bump and costing nothing.
        want = environment.get("env_digest")
        by_key = {r.get("key"): r for r in (restate or {}).get("results", [])}
        c10, carried = [], 0
        for r in rev_e.document.get("reviews", []):
            if r.get("reviewed_env_digest") == want:
                continue
            where = f"{rev_e.predicate['file']}:{r.get('key')}"
            drift = (f"reviewed against env {str(r.get('reviewed_env_digest'))[:12]}..., "
                     f"this environment is {str(want)[:12]}...")
            if restate is None:
                c10.append(Finding("C-10", where, drift))
                continue
            entry = by_key.get(r.get("key"))
            outcome = (entry or {}).get("outcome")
            if outcome == "restated":
                # Carried forward. Counted in the reason rather than passing
                # silently: the reader must be able to tell a review performed
                # here from one inherited across a bump.
                carried += 1
            elif outcome == "changed":
                c10.append(Finding("C-10", where,
                    f"{drift} and restate says `changed`: the statement moved, so "
                    f"the review describes something the declaration no longer says"))
            elif outcome == "not_checkable":
                # NOT phrased as `changed`. The two send a reviewer to different
                # places, and conflating them hides a broken checker behind a
                # pile of apparently-invalidated reviews.
                c10.append(Finding("C-10", where,
                    f"{drift} and restate says `not_checkable` "
                    f"({(entry or {}).get('reason') or 'no reason given'}): NOBODY "
                    f"KNOWS whether the statement changed, so the review cannot be "
                    f"carried forward -- this is not evidence that it did change"))
            else:
                c10.append(Finding("C-10", where,
                    f"{drift} and the restate run does not mention this key; an "
                    f"omitted entry reads exactly like a checked one"))
        add("C-10", "every human review is of this environment", c10,
            reason=(f"{carried} review(s) carried forward on a restate `restated`, "
                    f"not performed in this environment" if carried else ""))

    # C-11 -- declarations describe the emission that shipped, not another one.
    dec = _by_kind(evidence, "declarations")
    if emission_path is None:
        skip("C-11", "declarations.emission_sha256 matches the emission",
             "no emission supplied (--emission); the emission is an input to the "
             "bundle rather than a predicate in it")
    elif not emission_path.is_file():
        skip("C-11", "declarations.emission_sha256 matches the emission",
             f"no such emission: {emission_path}")
    elif dec is None or not dec.readable:
        skip("C-11", "declarations.emission_sha256 matches the emission",
             "no readable declarations predicate in the bundle")
    else:
        actual = file_digest(emission_path)
        claimed = dec.document.get("emission_sha256")
        add("C-11", "declarations.emission_sha256 matches the emission",
            [] if claimed == actual else
            [Finding("C-11", dec.predicate["file"],
                f"derived from emission {str(claimed)[:12]}..., but {emission_path.name} "
                f"is {actual[:12]}...")])

    # C-12 -- the counterpart to C-04, in the other direction. C-04 catches a
    # type that should not be there; this catches one that is not there at all,
    # which no rule above can see because they all check what is present.
    present = {e.predicate["predicateType"] for e in evidence}
    add("C-12", "every required predicate type is present",
        [Finding("C-12", short_type(t), "required, but the bundle carries no "
                                        "predicate of this type")
         for t in sorted(REQUIRED_PREDICATE_TYPES - present)])

    return results


def _schema_for(version: str) -> dict:
    import json  # noqa: PLC0415 - local, so the module imports cheaply

    path = schema_path_for(version)
    if not path.is_file():
        raise LoadError(f"unsupported contract version {version!r}")
    return json.loads(path.read_text(encoding="utf-8"))


class EvidenceRow(NamedTuple):
    kind: str
    file: str
    produced_by: str
    attestation: str
    environment: str


def evidence_table(bundle: dict, evidence: list[Evidence]) -> list[EvidenceRow]:
    """The reviewable object this command produces instead of a verdict.

    One row per measurement: what it is, who produced it, whether the party that
    wrote the code also produced the measurement, and whether it was taken in
    the environment the bundle is about. A reader can see the shape of the
    evidence; no cell of this table is a score, and there is no total row.
    """
    bundle_env = bundle.get("env_digest")
    rows = []
    for e in evidence:
        declared = e.predicate.get("env_digest")
        if declared is None:
            where = "n/a (not a Lean measurement)"
        elif declared == bundle_env:
            where = "this environment"
        else:
            where = f"OTHER environment ({declared[:8]}...)"
        rows.append(EvidenceRow(
            kind=e.kind,
            file=e.predicate["file"],
            produced_by=e.predicate.get("produced_by", "?"),
            attestation="self-attested" if e.predicate.get("self_attested") else "independent",
            environment=where,
        ))
    return rows
