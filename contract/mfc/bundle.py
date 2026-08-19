"""`mfc bundle` — build `declarations/1.0` from an emission and an environment.

## Everything is recomputed. Nothing is carried across.

The emission is produced by the topic repo's own emitter, in the topic repo's
own build. It is an *input*, not a source of truth, and every field this module
adds is derived here:

* `axioms` is re-sorted and re-deduped, never trusted as ordered;
* `axioms_disallowed` is computed against the environment's `axiom_policy` —
  the emission does not report it at all, deliberately, so there is no value to
  disagree with;
* `contains_sorry_ax` is recomputed from the axiom list;
* `counts` are recounted from `constants[]` rather than copied.

The reason is not suspicion of the emitter, it is arithmetic: a producer that
reports its own verdict has one failure mode that no amount of care removes,
and the whole contract exists to keep that failure mode out. There is no code
path here that can emit a clean `axioms_disallowed` for a declaration whose
axiom list contains `sorryAx`.

## Inputs are validated before they are used

Both inputs are checked against their schemas first. The emission's own
`allOf` already enforces the rules this module would otherwise have to
re-check by hand — for instance that `value_pp` is non-null only for `def` and
`opaque` — so validating up front means the recomputation can rely on shape
without a second set of defensive branches.

## Scope

This produces `declarations.json` and nothing else. The three artifacts it
used to name as unbuilt now have producers of their own, each reading a
different source: `environment.json` from the checkout (`env.py`), `build.json`
from the `lake env lean --json` NDJSON (`build.py`), and `bundle.json` from
file digests over the finished set (`seal.py`). They stay separate modules
because they share no input with this one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .digest import file_digest, scc_placeholder, statement_digest, STATEMENT_DIGEST_V

#: `value_pp` is part of the statement for these kinds and for no others.
VALUE_BEARING_KINDS = frozenset({"def", "opaque"})


class BundleError(Exception):
    """The inputs are inconsistent. Never raised for a merely *invalid* artifact."""


def _order_constants(constants: dict[str, dict]) -> list[str]:
    """Topological order, dependencies first.

    A statement digest is a Merkle node over its topic-local dependencies, so
    every dependency must be hashed before its dependent. Members of one SCC
    are exempt — they contribute a name placeholder rather than a digest — but
    a cycle that is *not* declared as an SCC is an emitter defect and is raised
    rather than worked around, because silently breaking it would produce
    digests that depend on traversal order.
    """
    order: list[str] = []
    state: dict[str, int] = {}  # 0 = visiting, 1 = done

    def visit(name: str, stack: tuple[str, ...]) -> None:
        seen = state.get(name)
        if seen == 1:
            return
        if seen == 0:
            cycle = " -> ".join((*stack[stack.index(name):], name))
            raise BundleError(
                f"dependency cycle with no declared SCC: {cycle}. "
                f"The emitter must record scc_members[] on every member."
            )
        state[name] = 0
        node = constants[name]
        scc = set(node.get("scc_members") or ())
        for dep in node["local_deps"]:
            if dep in scc or dep == name:
                continue  # contributes a placeholder, not a digest
            if dep not in constants:
                raise BundleError(
                    f"{name} declares topic-local dependency {dep!r}, which is "
                    f"not in the emission. local_deps must be module-scoped."
                )
            visit(dep, (*stack, name))
        state[name] = 1
        order.append(name)

    for name in sorted(constants):
        visit(name, ())
    return order


def compute_statement_digests(emission: dict) -> dict[str, str]:
    """`name -> statement_digest` for every constant in the emission."""
    constants = {c["name"]: c for c in emission["constants"]}
    digests: dict[str, str] = {}
    for name in _order_constants(constants):
        c = constants[name]
        scc = set(c.get("scc_members") or ())
        deps: dict[str, Any] = {}
        for dep in c["local_deps"]:
            deps[dep] = scc_placeholder(dep) if (dep in scc or dep == name) else digests[dep]
        value_pp = c["value_pp"] if c["kind"] in VALUE_BEARING_KINDS else None
        digests[name] = statement_digest(name, c["kind"], c["type_pp"], value_pp, deps)
    return digests


def build_declarations(emission: dict, environment: dict, emission_path: Path) -> dict:
    """The `declarations/1.0` document."""
    # The artifact-level commit binding (derived-alg-geo-lean #628). Without
    # it, an emission generated at commit A re-bundled with a fresh
    # environment at commit B yields a bundle whose subject is B and whose
    # declaration content describes A -- every rule green. Ordering inside one
    # CI job was the only protection. Both fields optional-tolerant: an old
    # emission (no stamp) or an environment without root_package.rev simply
    # has nothing to compare, and seal's own 40-hex validation still applies
    # downstream.
    stamped = emission.get("source_git_commit")
    env_rev = (environment.get("root_package") or {}).get("rev")
    if stamped is not None and env_rev is not None and stamped != env_rev:
        raise BundleError(
            f"the emission was produced at commit {stamped} but the "
            f"environment describes {env_rev}; a bundle would attest one "
            f"commit with the other's declaration content. Regenerate the "
            f"emission at the current checkout.")
    policy = set(environment["axiom_policy"]["allowlist"]) | {
        a["axiom"] for a in environment["axiom_policy"]["additions"]
    }
    constants = emission["constants"]

    # Computed once over the whole emission, not per declaration: "which axioms
    # does THIS repo itself declare" is a property of the environment, and
    # recomputing it inside the loop would be quadratic and no more correct.
    local_axioms = sorted({c["name"] for c in constants if c["kind"] == "axiom"})

    digests = compute_statement_digests(emission)

    declarations = []
    for c in constants:
        axioms = sorted(set(c["axioms"]))
        declarations.append({
            "name": c["name"],
            "module": c["module"],
            "kind": c["kind"],
            "is_internal": c["is_internal"],
            "statement_digest": digests[c["name"]],
            "local_deps": sorted(set(c["local_deps"])),
            "axioms": axioms,
            "axioms_disallowed": sorted(set(axioms) - policy),
            "contains_sorry_ax": "sorryAx" in axioms,
            "local_axioms": local_axioms,
            "range": c["range"],
            "cites": c["cites"],
        })

    internal = sum(1 for d in declarations if d["is_internal"])
    return {
        "schema_version": "declarations/1.0",
        "env_digest": environment["env_digest"],
        "emission_sha256": file_digest(emission_path),
        "statement_digest_version": STATEMENT_DIGEST_V,
        "counts": {
            "total": len(declarations),
            "in_scope": len(declarations) - internal,
            "internal": internal,
            "cited": sum(1 for d in declarations if d["cites"]),
        },
        "declarations": declarations,
    }


def dumps(document: dict) -> str:
    """Serialize for writing to disk.

    Indented and newline-terminated so `git diff attest/` is readable — the
    artifact is committed and reviewed by humans, and `bundle.json` commits to
    these exact bytes via `file_digest`.
    """
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
