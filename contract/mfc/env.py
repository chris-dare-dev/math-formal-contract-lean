"""`mfc env` — produce `environment/1.0` from a topic repository's checkout.

## Why this exists at all

Nothing produced this artifact. Every other module in this package *consumes*
it — `bundle` stamps `env_digest` onto declarations, `conformance` compares
`C-05`/`C-10` against it, `join` renders a review `not_applicable` when it
disagrees — and the record itself was hand-written or, in this repository's own
CI, taken from `testdata/artifacts/valid/environment-1.0.json`. A **fixture**.
So the one artifact that is supposed to pin what the measurement was made in
was, in the only place it was used for real, describing a different machine.

## Observed, not asserted

Every field here is read out of the checkout — `lean-toolchain`,
`lake-manifest.json`, `lakefile.toml`, `git` — with two deliberate exceptions
that are *inputs* rather than observations:

* **`axiom_policy`** is a policy, not a property of a build. A default would be
  this package asserting a policy on a topic repo's behalf, so it is required.
  Same reasoning as `@[cites]`'s mandatory `relation`.
* **`lean_githash` / `lake_version`** come from running `lean --githash` and
  `lake --version`, and are overridable so a caller without a toolchain on PATH
  can supply what it measured elsewhere rather than get a fabricated value.

## `rev`, never `inputRev`

`packages[].rev` is the resolved 40-hex. `input_rev_is_branch` lists every
package whose `inputRev` is *not* a 40-hex — i.e. every package a `lake update`
would silently re-resolve. In the consuming repository that is nine of fifteen.
Emitting the list makes the drift visible in the record instead of leaving it to
be discovered, and it is why `env_digest` hashes `rev`.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from .digest import env_digest

#: `env_digest`'s input tuple, named in the artifact so a verifier knows what to
#: recompute rather than having to read this module.
DIGEST_ALGORITHM = (
    "sha256(canonical_json({lean_toolchain, lean_githash, lean_options, "
    "sorted [(name, rev)]}))"
)

HEX40 = 40


class EnvError(RuntimeError):
    """The checkout could not be read. Never a partial artifact."""


def _run(args: list[str], cwd: Path) -> str:
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EnvError(f"could not run {args[0]!r} in {cwd}: {exc}") from exc
    if p.returncode != 0:
        raise EnvError(f"{' '.join(args)} exited {p.returncode}: {p.stderr.strip()}")
    return p.stdout.strip()


def _git(args: list[str], repo: Path) -> str:
    return _run(["git", *args], repo)


def lean_options(repo: Path) -> dict[str, bool | int | str]:
    """The RESOLVED `[leanOptions]` table.

    Resolved rather than declared matters: `autoImplicit` is
    elaboration-affecting, so two builds differing only there are different
    environments and must not share a digest.
    """
    lakefile = repo / "lakefile.toml"
    if not lakefile.is_file():
        raise EnvError(f"no lakefile.toml in {repo}")
    try:
        data = tomllib.loads(lakefile.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise EnvError(f"lakefile.toml is not valid TOML: {exc}") from exc
    return dict(data.get("leanOptions") or {})


def packages(repo: Path) -> list[dict[str, Any]]:
    manifest = repo / "lake-manifest.json"
    if not manifest.is_file():
        raise EnvError(f"no lake-manifest.json in {repo}; run `lake update` first")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EnvError(f"lake-manifest.json is not valid JSON: {exc}") from exc

    out = []
    for p in data.get("packages", []):
        rev = p.get("rev")
        if not (isinstance(rev, str) and len(rev) == HEX40):
            # A package with no resolved rev cannot be pinned, and emitting it
            # with a placeholder would put an unpinnable dependency inside a
            # digest that claims to pin everything.
            raise EnvError(
                f"package {p.get('name')!r} has no resolved 40-hex rev "
                f"({rev!r}); the environment is not pinnable")
        out.append({
            "name": p["name"],
            "rev": rev,
            "url": p.get("url", ""),
            "input_rev": str(p.get("inputRev", "")),
            "inherited": bool(p.get("inherited", False)),
        })
    return sorted(out, key=lambda p: p["name"])


def branch_pinned(pkgs: list[dict[str, Any]]) -> list[str]:
    """Names whose `input_rev` is not a 40-hex — what `lake update` would move."""
    return sorted({
        p["name"] for p in pkgs
        if not (len(p["input_rev"]) == HEX40
                and all(c in "0123456789abcdef" for c in p["input_rev"]))
    })


def root_package(repo: Path) -> dict[str, Any]:
    name = tomllib.loads((repo / "lakefile.toml").read_text(encoding="utf-8")).get("name")
    if not name:
        raise EnvError("lakefile.toml declares no package name")
    try:
        url = _git(["remote", "get-url", "origin"], repo)
    except EnvError:
        url = ""
    try:
        tag = _git(["describe", "--tags", "--exact-match"], repo)
    except EnvError:
        # NOT an error. A null tag is a valid artifact and an invalid release,
        # which is the mechanical statement of "pins RELEASED formalizations".
        tag = None
    return {
        "name": str(name),
        "url": url,
        "rev": _git(["rev-parse", "HEAD"], repo),
        "tag": tag,
        "worktree_dirty": bool(_git(["status", "--porcelain"], repo)),
    }


def build(
    repo: Path,
    *,
    allowlist: list[str],
    additions: list[dict[str, str]] | None = None,
    contract_package: str = "MathFormalContract",
    lean_githash: str | None = None,
    lake_version: str | None = None,
    mfc_version: str,
    emitter_version: str,
) -> dict[str, Any]:
    """Read the checkout and return a complete `environment/1.0` document."""
    toolchain = (repo / "lean-toolchain")
    if not toolchain.is_file():
        raise EnvError(f"no lean-toolchain in {repo}")
    lean_toolchain = toolchain.read_text(encoding="utf-8").strip()

    githash = lean_githash or _run(["lean", "--githash"], repo)
    lakever = lake_version or _run(["lake", "--version"], repo).splitlines()[0]

    pkgs = packages(repo)
    opts = lean_options(repo)

    contract = next((p for p in pkgs if p["name"] == contract_package), None)
    if contract is None:
        raise EnvError(
            f"{contract_package!r} is not in lake-manifest.json, so the record "
            f"cannot say which contract package produced it; pass "
            f"--contract-package if it is required under another name")

    return {
        "schema_version": "environment/1.0",
        "env_digest": env_digest(
            lean_toolchain=lean_toolchain,
            lean_githash=githash,
            lean_options=opts,
            packages=[(p["name"], p["rev"]) for p in pkgs],
        ),
        "env_digest_algorithm": DIGEST_ALGORITHM,
        "lean_toolchain": lean_toolchain,
        "lean_githash": githash,
        "lean_options": opts,
        "lake_version": lakever,
        "packages": pkgs,
        "input_rev_is_branch": branch_pinned(pkgs),
        "root_package": root_package(repo),
        "axiom_policy": {
            "allowlist": sorted(set(allowlist)),
            "additions": list(additions or []),
        },
        "emitter_version": emitter_version,
        "mfc_version": mfc_version,
        "contract_repo": {"url": contract["url"], "rev": contract["rev"]},
    }
