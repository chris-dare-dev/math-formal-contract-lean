"""`mfc seal` — assemble `bundle/1.0`, the in-toto Statement over a finished set.

## Why nothing assembled this before

`conformance` has twelve rules and, until now, nothing in either repository
produced the file it reads. The README said so rather than faking it: "Nothing
assembles a `bundle.json`, and there is no `environment.json` or `build.json`,
so there is nothing for `conformance` to run against." Every rule was exercised
against a tree built inside pytest. This is the producer that lets the same
twelve rules run over a real release.

## The producer must not be able to manufacture the vacuous pass

`C-12` exists because every other rule checks what is *present*: a bundle that
simply omits its `build/v1` predicate satisfies all eleven others and reports
"5 predicate(s)". A tool that happily wrote that bundle would be shipping the
`C-12` fixture as a product, so `seal` **refuses** when a required predicate
type is absent. The checker and the producer import
`REQUIRED_PREDICATE_TYPES` from the same module, so the two cannot drift.

The same reasoning is why there is no `--force` for it. A release missing its
build measurement is not a release with a caveat.

## Digests are over bytes, and the tree must be the commit

`sha256` per predicate is `file_digest` — raw bytes, uncanonicalized — because
`C-02` compares it to what is actually on disk and `git diff --exit-code
attest/` is what keeps the committed bytes and the attested bytes the same.

A **dirty worktree is refused** for the same reason `C-09` exists. The subject
attests a `gitCommit`; if the tree carries uncommitted changes, the
measurements describe bytes that commit does not contain, and every digest in
the bundle is then a true statement about a tree nobody can fetch.
`--allow-dirty` exists for local experimentation and says so on stderr.

## What `self_attested` is for

`true` means the party that wrote the code also produced the measurement. In a
solo-operated repository that is the honest label for `environment`,
`declarations` and `build`, and stating it is worth more than the appearance of
independence. Human review and the corpus resolution are produced elsewhere and
are labelled `false`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .conformance import (
    ENVIRONMENT_FREE_TYPES,
    PREDICATE_NS,
    REQUIRED_PREDICATE_TYPES,
    short_type,
)
from .digest import file_digest

IN_TOTO_STATEMENT_V1 = "https://in-toto.io/Statement/v1"

#: `--provisional FILE:PRODUCED_BY:ENV_DIGEST`. Three fields, and the digest is
#: the point: a provisional predicate exists to carry evidence from ANOTHER
#: environment, so omitting it would defeat `C-05`.
PROVISIONAL_FIELDS = 3

HEX64 = 64


class SealError(RuntimeError):
    """The bundle could not be assembled honestly. Never a partial artifact."""


def _https_url(url: str) -> str:
    """`git@host:owner/repo.git` -> `https://host/owner/repo`.

    `root_package.url` is whatever `git remote get-url origin` printed, which is
    frequently SSH. `subject[].uri` wants a URI. Rewriting the transport does
    not change which repository is named, and the alternative — publishing
    `git@github.com:...` as a URI — is not one.
    """
    u = url.strip()
    if u.startswith("git@") and ":" in u:
        host, _, path = u[len("git@"):].partition(":")
        u = f"https://{host}/{path}"
    return u.removesuffix(".git")


def _hex64(value: object, *, field: str) -> str:
    if not (isinstance(value, str) and len(value) == HEX64
            and all(c in "0123456789abcdef" for c in value)):
        raise SealError(f"{field} is {value!r}, not a 64-hex sha256")
    return value


def _predicate(kind: str, path: Path, root: Path, *, produced_by: str,
               env_digest: str | None, self_attested: bool) -> dict[str, Any]:
    if not path.is_file():
        raise SealError(f"no such {kind} artifact: {path}")
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        # `gather()` resolves every predicate as `root / pred["file"]`, so a
        # path outside the root would produce a bundle whose own checker cannot
        # find its files.
        raise SealError(
            f"{path} is outside the bundle root {root}; every predicate file is "
            f"resolved relative to the root, so it would be unreadable") from exc
    return {
        "predicateType": f"{PREDICATE_NS}/{kind}/v1",
        "file": rel.as_posix(),
        "sha256": file_digest(path),
        "produced_by": produced_by,
        "env_digest": env_digest,
        "self_attested": self_attested,
    }


def parse_provisional(spec: str) -> tuple[str, str, str]:
    """`file:produced_by:env_digest` -> its three parts."""
    parts = spec.split(":")
    if len(parts) != PROVISIONAL_FIELDS:
        raise SealError(
            f"--provisional {spec!r}: expected file:produced_by:env_digest, "
            f"got {len(parts)} field(s)")
    file_part, produced_by, env = (p.strip() for p in parts)
    if not file_part or not produced_by:
        raise SealError(f"--provisional {spec!r}: file and produced_by are required")
    _hex64(env, field=f"--provisional {spec!r} env_digest")
    return file_part, produced_by, env


def seal(
    *,
    root: Path,
    environment_path: Path,
    environment: dict[str, Any],
    registry_path: Path,
    declarations_path: Path | None = None,
    build_path: Path | None = None,
    review_path: Path | None = None,
    review_produced_by: str | None = None,
    resolution_path: Path | None = None,
    resolution: dict[str, Any] | None = None,
    provisional: list[tuple[str, str, str]] | None = None,
    allow_dirty: bool = False,
) -> dict[str, Any]:
    """Assemble a complete `bundle/1.0` over the artifacts that exist."""
    env_digest = _hex64(environment.get("env_digest"), field="environment.env_digest")
    rootpkg = environment.get("root_package")
    if not isinstance(rootpkg, dict):
        raise SealError("environment.json carries no root_package; the bundle has "
                        "no subject to attest")
    commit = rootpkg.get("rev")
    if not (isinstance(commit, str) and len(commit) == 40
            and all(c in "0123456789abcdef" for c in commit)):
        raise SealError(f"root_package.rev is {commit!r}, not a 40-hex commit; "
                        f"C-09 compares the subject against it")
    if rootpkg.get("worktree_dirty") and not allow_dirty:
        raise SealError(
            "the worktree has uncommitted changes, so these measurements describe "
            f"bytes that {commit[:10]} does not contain. Commit them, or pass "
            "--allow-dirty for a bundle that is explicitly not a release")

    contract_repo = environment.get("contract_repo")
    if not isinstance(contract_repo, dict) or "url" not in contract_repo \
            or "rev" not in contract_repo:
        raise SealError("environment.json carries no contract_repo{url, rev}; the "
                        "bundle cannot say which contract version produced it")

    mfc_version = environment.get("mfc_version") or "unknown"
    emitter_version = environment.get("emitter_version") or "unknown"
    lake_version = environment.get("lake_version") or "unknown"

    predicates: list[dict[str, Any]] = [
        _predicate("environment", environment_path, root,
                   produced_by=f"mfc/{mfc_version}",
                   env_digest=env_digest, self_attested=True),
    ]
    if declarations_path is not None:
        predicates.append(_predicate(
            "declarations", declarations_path, root,
            produced_by=f"{emitter_version} + mfc/{mfc_version}",
            env_digest=env_digest, self_attested=True))
    if build_path is not None:
        predicates.append(_predicate(
            "build", build_path, root,
            produced_by=f"lake {lake_version} + mfc/{mfc_version}",
            env_digest=env_digest, self_attested=True))
    if review_path is not None:
        if not review_produced_by:
            raise SealError(
                "--review needs --review-produced-by: review/1.0 is the one "
                "artifact no machine may write, and the bundle has to name the "
                "human who wrote it")
        predicates.append(_predicate(
            "human-review", review_path, root,
            produced_by=review_produced_by,
            env_digest=env_digest, self_attested=False))
    if resolution_path is not None:
        resolver = (resolution or {}).get("resolver_version")
        if not resolver:
            raise SealError(f"{resolution_path.name} carries no resolver_version; "
                            f"the predicate cannot say what produced it")
        predicates.append(_predicate(
            "corpus-resolution", resolution_path, root,
            produced_by=f"arxmcp/{resolver}",
            # NOT a mismatch and NOT a match: the corpus resolution is not
            # produced in a Lean environment at all.
            env_digest=None, self_attested=False))
    for file_part, produced_by, env in provisional or []:
        predicates.append(_predicate(
            "provisional-self-reported", root / file_part, root,
            produced_by=produced_by, env_digest=env, self_attested=True))

    present = {p["predicateType"] for p in predicates}
    missing = REQUIRED_PREDICATE_TYPES - present
    if missing:
        raise SealError(
            "refusing to write a bundle with no "
            + ", ".join(f"{short_type(t)}/v1" for t in sorted(missing))
            + " predicate. Every conformance rule but C-12 checks what is present, "
              "so this bundle would pass eleven of twelve while attesting nothing "
              "about the build")

    # Belt and braces on the one label C-05 rests on. A required predicate that
    # somehow carried a foreign digest would be out-of-environment evidence
    # presented as a measurement of this build.
    for p in predicates:
        if p["predicateType"] in REQUIRED_PREDICATE_TYPES and p["env_digest"] != env_digest:
            raise SealError(f"{p['file']} would be sealed with a foreign env_digest")
        if p["predicateType"] in ENVIRONMENT_FREE_TYPES and p["env_digest"] is not None:
            raise SealError(f"{p['file']} is produced outside any Lean environment; "
                            f"its env_digest must be null")

    return {
        "schema_version": "bundle/1.0",
        "_type": IN_TOTO_STATEMENT_V1,
        "subject": [{
            "name": str(rootpkg.get("name") or ""),
            "uri": _https_url(str(rootpkg.get("url") or "")),
            "digest": {"gitCommit": commit, "gitTag": rootpkg.get("tag")},
        }],
        "contract_repo": {"url": str(contract_repo["url"]),
                          "rev": str(contract_repo["rev"])},
        "env_digest": env_digest,
        "registry_sha256": file_digest(registry_path),
        "predicates": predicates,
        # mfc only ever emits types it recognizes. This list is populated on the
        # consumer side, where an unknown predicateType is INGESTED, NEVER
        # SERVED -- which is what makes the URI extension point safe.
        "unrecognized_predicates": [],
    }
