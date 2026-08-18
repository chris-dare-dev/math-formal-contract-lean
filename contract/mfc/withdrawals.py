"""`withdrawals/1.0` — retracting a published record. #176, red-team gap 5.

Nobody in the design batch addressed this, and it is the worst failure the
contract can have: **a record a human has determined is not faithful, still
being served as evidence.**

Both existing mechanisms live inside the producer's *next tag*.
`review.yaml`'s `faithfulness: divergent|inadequate` and the registry's
`superseded_by` both say "this one is bad" in a version the consumer is not
pinned to. arXMCP pins one tag and re-serves it verbatim, so with `v0.1.0`
pinned and `v0.2.0` marking an entry inadequate, the pinned surface keeps
serving the old record until a human re-pins — and nothing tells them to.

## Why this file may travel forward in time when nothing else may

The whole architecture is built on a consumer pinning one producer tag and
re-deriving everything from it. A consumer that reached forward to the newest
tag for *anything else* could be handed a claim the pinned review never
covered.

Withdrawals are the exception, and the argument is one line: **they can only
remove trust, never grant it.** Reading the newest withdrawal list against an
older pin can turn a served record into a withheld one and can never do the
reverse. That asymmetry is what makes the forward channel safe, and it is why
this file carries no reinstatement field — restoring trust goes back through a
new registry entry and a new human review, which is the path that has a
reviewer and a date on it.

## Append-only, and what that means here

A withdrawal is never edited and never deleted. A withdrawal that could be
removed is one a consumer cannot rely on having seen, and the entire value of
the channel is that a consumer who read it once cannot be talked out of it.
`W-04` compares against a previous revision of the file when one is supplied,
which is what makes "append-only" a check rather than an intention.
"""

from __future__ import annotations

from typing import Any

from .rules import Finding, RuleResult, Status


class WithdrawalsError(Exception):
    """The check could not run. Exit 2, never a finding."""


def withdrawn_keys(document: dict | None) -> set[str]:
    """Every key this document withdraws. Empty when there is no document.

    An absent withdrawals file is a legitimate state — most repositories have
    never withdrawn anything — and is deliberately not an error. What is an
    error is a *malformed* one, because "I could not read the withdrawal list"
    must never render as "nothing is withdrawn".
    """
    if document is None:
        return set()
    items = document.get("withdrawals")
    if not isinstance(items, list):
        raise WithdrawalsError(
            "withdrawals document has no withdrawals[] array; refusing to "
            "treat an unreadable withdrawal list as an empty one")
    return {w["key"] for w in items if isinstance(w, dict) and w.get("key")}


def check(document: dict, *, registry: dict | None = None,
          previous: dict | None = None) -> list[RuleResult]:
    """`W-01`..`W-04` over a `withdrawals/1.0` document."""
    results: list[RuleResult] = []
    items: list[Any] = document.get("withdrawals") or []

    def add(rule: str, title: str, findings: list[Finding], reason: str = "") -> None:
        results.append(RuleResult(
            rule, title, Status.FAIL if findings else Status.PASS,
            tuple(findings), reason))

    # W-01 -- one withdrawal per key. A second entry for the same key is either
    # a duplicate or an edit-by-append, and both make "when was this withdrawn"
    # unanswerable.
    seen: dict[str, int] = {}
    w01: list[Finding] = []
    for i, w in enumerate(items):
        key = w.get("key")
        if key in seen:
            w01.append(Finding("W-01", key,
                               f"withdrawn twice (entries {seen[key]} and {i}); "
                               f"a key has one withdrawal date or none"))
        else:
            seen[key] = i
    add("W-01", "each key is withdrawn at most once", w01)

    # W-02 -- a withdrawal names a key the registry actually has. Withdrawing a
    # key that was never minted removes trust from nothing and hides a typo
    # that leaves the real entry still being served.
    if registry is None:
        results.append(RuleResult(
            "W-02", "every withdrawn key exists in the registry", Status.NOT_RUN,
            reason="no --registry supplied; a withdrawal for a key that was "
                   "never minted cannot be distinguished from one for a key "
                   "that was"))
    else:
        known = set((registry.get("entries") or {}))
        add("W-02", "every withdrawn key exists in the registry",
            [Finding("W-02", w.get("key"),
                     "withdrawn, but no such key in the registry: the entry "
                     "this was meant to retract is still being served")
             for w in items if w.get("key") not in known])

    # W-03 -- the registry id matches. A withdrawal file applied to the wrong
    # registry silently removes trust from keys it was never about.
    if registry is None:
        results.append(RuleResult(
            "W-03", "the withdrawals belong to this registry", Status.NOT_RUN,
            reason="no --registry supplied"))
    else:
        mine, theirs = document.get("registry_id"), registry.get("registry_id")
        add("W-03", "the withdrawals belong to this registry",
            [] if mine == theirs else
            [Finding("W-03", "registry_id",
                     f"withdrawals are for registry {mine!r} and this registry "
                     f"is {theirs!r}")])

    # W-04 -- APPEND-ONLY, checked rather than intended.
    if previous is None:
        results.append(RuleResult(
            "W-04", "no previously published withdrawal was removed or edited",
            Status.NOT_RUN,
            reason="no --previous supplied. Append-only is the property a "
                   "consumer relies on; without the earlier revision this run "
                   "did not verify it"))
    else:
        before = {w.get("key"): w for w in (previous.get("withdrawals") or [])
                  if isinstance(w, dict)}
        now = {w.get("key"): w for w in items if isinstance(w, dict)}
        w04: list[Finding] = []
        for key, old in sorted(before.items(), key=lambda kv: str(kv[0])):
            if key not in now:
                w04.append(Finding("W-04", key,
                                   "was published as withdrawn and is now "
                                   "absent; a consumer that read it cannot be "
                                   "told to forget it"))
            elif now[key] != old:
                changed = sorted(k for k in set(old) | set(now[key])
                                 if old.get(k) != now[key].get(k))
                w04.append(Finding("W-04", key,
                                   f"was published as withdrawn and has been "
                                   f"edited since ({', '.join(changed)})"))
        add("W-04", "no previously published withdrawal was removed or edited", w04)

    return results
