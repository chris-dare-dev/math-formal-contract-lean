"""`mfc check-resolution` — the freshness gate. #172, red-team gap 1.

`registry_sha256` answers "was this resolution computed against THIS registry?"
It answers nothing about *when*, and the corpus is on the other side of a cold
seam. So a `resolution.json` produced on mint day stayed `pass` indefinitely:
the corpus could re-ingest, re-chunk, and rotate every chunk id underneath it,
and producer CI would go on reporting green about a document nobody had asked
the corpus about since.

Combined with #160 — where the one gate that survives the seam could never pass
— the axis the design points to as "a different system, a different program"
was a stale assertion about an unidentified document version.

## Staleness is not drift, and they must not share an exit

Two different facts, and collapsing them is the failure this module exists to
prevent:

* **drift** — the corpus moved. `corpus_manifest_content_hash` differs from the
  live manifest. Something that was `current` may no longer be.
* **staleness** — nobody has looked. `generated_at` is older than the topic's
  `resolution_max_age_days`. Nothing is known to have changed, and nothing is
  known not to have.

"We have not checked recently" is not "this drifted", and a CI job that printed
one for the other would teach its reader to ignore both. They are reported as
separate findings with separate rule ids, and a run can trip either, both, or
neither.

## This is the price of the cold seam, made visible

ADR-0001 forbids runtime coupling: neither repo calls the other, ever. So
freshness is bounded by how often somebody runs the resolver, and no gate here
can change that. What the gate does is make the bound **visible** — an
unchecked resolution says so, in CI, instead of aging quietly into a number
somebody trusts.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .rules import Finding, RuleResult, Status

#: The default only applies when a topic states none. Deliberately not zero and
#: not infinite: a fortnight is short enough that a re-ingest is noticed within
#: one sprint, and long enough that a quiet corpus does not redden CI daily.
DEFAULT_MAX_AGE_DAYS = 14


class FreshnessError(Exception):
    """The check could not run. Exit 2, never a finding."""


def _parse(stamp: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FreshnessError(
            f"generated_at {stamp!r} is not an ISO-8601 timestamp, so the age "
            f"of this resolution cannot be established. That is not the same "
            f"as it being fresh.") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def check(resolution: dict, *, registry_sha256: str | None = None,
          manifest_hash: str | None = None, now: datetime | None = None,
          max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> list[RuleResult]:
    """`F-01`..`F-03` over a `resolution/1.0` document.

    Every input this cannot obtain for itself yields `not_run` rather than a
    pass: the live manifest hash comes from the corpus side and a producer
    build may legitimately not have it, but "I could not ask" must never render
    as "nothing has changed".
    """
    results: list[RuleResult] = []

    # F-01 -- the resolution describes THIS registry.
    if registry_sha256 is None:
        results.append(RuleResult(
            "F-01", "the resolution was computed against this registry",
            Status.NOT_RUN,
            reason="no --registry supplied, so the registry bytes this "
                   "resolution claims were not compared to any"))
    else:
        claimed = resolution.get("registry_sha256")
        findings = ([] if claimed == registry_sha256 else
                    [Finding("F-01", "registry_sha256",
                             f"resolution was computed against "
                             f"{str(claimed)[:12]}… and the registry on disk is "
                             f"{registry_sha256[:12]}…; every result in this "
                             f"file is about a different document")])
        results.append(RuleResult(
            "F-01", "the resolution was computed against this registry",
            Status.FAIL if findings else Status.PASS, tuple(findings)))

    # F-02 -- DRIFT. The corpus moved.
    if manifest_hash is None:
        results.append(RuleResult(
            "F-02", "the corpus has not moved since this resolution",
            Status.NOT_RUN,
            reason="no --manifest-hash supplied. The live manifest lives on the "
                   "corpus side of a cold seam and a producer build may not "
                   "have it -- but not asking is not an answer"))
    else:
        claimed = resolution.get("corpus_manifest_content_hash")
        findings = ([] if claimed == manifest_hash else
                    [Finding("F-02", "corpus_manifest_content_hash",
                             f"resolved against corpus manifest "
                             f"{str(claimed)[:12]}…, live manifest is "
                             f"{manifest_hash[:12]}…: the corpus moved, so a "
                             f"result reading `current` may no longer be")])
        results.append(RuleResult(
            "F-02", "the corpus has not moved since this resolution",
            Status.FAIL if findings else Status.PASS, tuple(findings)))

    # F-03 -- STALENESS. Nobody has looked.
    stamp = resolution.get("generated_at")
    if not isinstance(stamp, str):
        raise FreshnessError("resolution carries no generated_at; its age "
                             "cannot be established")
    generated = _parse(stamp)
    now = now or datetime.now(timezone.utc)
    age_days = (now - generated).total_seconds() / 86400.0
    stale = age_days > max_age_days
    results.append(RuleResult(
        "F-03", "the resolution is younger than the topic's max age",
        Status.FAIL if stale else Status.PASS,
        (Finding("F-03", "generated_at",
                 f"resolved {age_days:.1f} days ago, limit is {max_age_days}. "
                 f"NOTHING IS KNOWN TO HAVE DRIFTED -- this says only that "
                 f"nobody has asked the corpus recently, which is a different "
                 f"fact and is why it is not F-02"),) if stale else (),
        reason="" if stale else f"{age_days:.1f} days old, limit {max_age_days}"))
    return results
