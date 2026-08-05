"""Tests for `mfc env` — the record every other check consumed and none produced.

The claim under test is not "it writes a file". It is that **`env_digest` moves
when the environment moves and not otherwise** — specifically that it follows
`rev` and ignores `inputRev`, because thirteen of the fifteen packages in the
consuming repository carry a branch `inputRev` that a `lake update` would
re-resolve silently.

No test here creates a git repository. `_git` is monkeypatched instead, which
is also the honest boundary: what `build()` does with git output is ours to
test, what git does is not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract.mfc import env as envmod
from contract.mfc.cli import EXIT_OK, EXIT_USAGE, main
from contract.mfc.digest import env_digest
from contract.mfc.env import EnvError, branch_pinned, build, packages

HEX_A = "a" * 40
HEX_B = "b" * 40
HEX_C = "c" * 40

LAKEFILE = """\
name = "TopicRepo"
version = "0.1.0"

[leanOptions]
autoImplicit = false
relaxedAutoImplicit = false

[[require]]
name = "MathFormalContract"
git = "https://example.invalid/mfc"
rev = "%s"
""" % HEX_C


def _manifest(*pkgs: dict) -> dict:
    return {"version": "1.1.0", "packagesDir": ".lake/packages",
            "name": "TopicRepo", "lakeDir": ".lake", "packages": list(pkgs)}


def _pkg(name: str, rev: str, input_rev: str, *, inherited: bool = False) -> dict:
    return {"name": name, "rev": rev, "url": f"https://example.invalid/{name}",
            "inputRev": input_rev, "inherited": inherited, "type": "git"}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "lean-toolchain").write_text("leanprover/lean4:v4.29.0\n", encoding="utf-8")
    (tmp_path / "lakefile.toml").write_text(LAKEFILE, encoding="utf-8")
    (tmp_path / "lake-manifest.json").write_text(json.dumps(_manifest(
        _pkg("MathFormalContract", HEX_C, HEX_C),
        _pkg("mathlib", HEX_A, "v4.29.0", inherited=True),
        _pkg("batteries", HEX_B, "main", inherited=True),
    )), encoding="utf-8")
    return tmp_path


@pytest.fixture
def no_git(monkeypatch: pytest.MonkeyPatch):
    """A clean, untagged checkout at a known rev."""
    def fake(args: list[str], repo: Path) -> str:
        if args[:2] == ["remote", "get-url"]:
            return "https://example.invalid/topic"
        if args[0] == "rev-parse":
            return HEX_A
        if args[0] == "describe":
            raise EnvError("no tag")
        if args[0] == "status":
            return ""
        raise AssertionError(f"unexpected git call: {args}")
    monkeypatch.setattr(envmod, "_git", fake)


def _build(repo: Path, **over):
    kw = dict(allowlist=["propext", "Quot.sound", "Classical.choice"],
              lean_githash=HEX_B, lake_version="Lake version 5.0.0",
              mfc_version="0.1.0", emitter_version="mfc-emit/1.0.0")
    kw.update(over)
    return build(repo, **kw)


# --- the digest follows rev, never inputRev -----------------------------------

def test_env_digest_recomputes_from_its_four_declared_inputs(repo, no_git) -> None:
    doc = _build(repo)
    assert doc["env_digest"] == env_digest(
        lean_toolchain="leanprover/lean4:v4.29.0",
        lean_githash=HEX_B,
        lean_options={"autoImplicit": False, "relaxedAutoImplicit": False},
        packages=[(p["name"], p["rev"]) for p in doc["packages"]])


def test_changing_input_rev_alone_does_not_move_the_digest(repo, no_git) -> None:
    """`inputRev` is what a caller ASKED for; `rev` is what was resolved.

    Hashing the ask would make the digest stable across a real environment
    change, which is the failure this choice exists to prevent.
    """
    before = _build(repo)["env_digest"]
    m = json.loads((repo / "lake-manifest.json").read_text(encoding="utf-8"))
    for p in m["packages"]:
        if p["name"] == "batteries":
            p["inputRev"] = "some-other-branch"
    (repo / "lake-manifest.json").write_text(json.dumps(m), encoding="utf-8")
    assert _build(repo)["env_digest"] == before


def test_changing_a_resolved_rev_does_move_the_digest(repo, no_git) -> None:
    before = _build(repo)["env_digest"]
    m = json.loads((repo / "lake-manifest.json").read_text(encoding="utf-8"))
    for p in m["packages"]:
        if p["name"] == "batteries":
            p["rev"] = "d" * 40
    (repo / "lake-manifest.json").write_text(json.dumps(m), encoding="utf-8")
    assert _build(repo)["env_digest"] != before


def test_lean_options_are_in_the_digest_because_they_affect_elaboration(
        repo, no_git) -> None:
    """`autoImplicit` changes what elaborates. Two builds differing only there
    are different environments and must not share a digest."""
    before = _build(repo)["env_digest"]
    (repo / "lakefile.toml").write_text(
        LAKEFILE.replace("autoImplicit = false", "autoImplicit = true"),
        encoding="utf-8")
    assert _build(repo)["env_digest"] != before


# --- what the record must make visible ----------------------------------------

def test_branch_pinned_names_every_package_lake_update_would_move(repo, no_git) -> None:
    doc = _build(repo)
    assert doc["input_rev_is_branch"] == ["batteries", "mathlib"]
    assert "MathFormalContract" not in doc["input_rev_is_branch"], (
        "a 40-hex inputRev is pinned and must not be reported as drifting")


def test_an_unresolvable_package_is_refused_not_placeholdered(repo, no_git) -> None:
    """Emitting it with a placeholder would put an unpinnable dependency inside
    a digest that claims to pin everything."""
    m = json.loads((repo / "lake-manifest.json").read_text(encoding="utf-8"))
    m["packages"].append(_pkg("halfbaked", "", "main"))
    (repo / "lake-manifest.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(EnvError, match="not pinnable"):
        _build(repo)


def test_a_missing_tag_is_a_valid_artifact_and_not_an_error(repo, no_git) -> None:
    """Null tag = valid artifact, invalid release. That is the mechanical
    statement of 'pins RELEASED formalizations'."""
    assert _build(repo)["root_package"]["tag"] is None


def test_a_dirty_worktree_is_recorded_rather_than_refused(
        repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recorded, because refusing would make the tool unusable mid-work and
    silently dropping it would let a digest describe a tree not in git.
    `mfc lint` is where dirty becomes fatal, in CI mode."""
    def dirty(args: list[str], r: Path) -> str:
        if args[0] == "status":
            return " M something.lean"
        if args[0] == "describe":
            raise EnvError("no tag")
        if args[0] == "rev-parse":
            return HEX_A
        return "https://example.invalid/topic"

    monkeypatch.setattr(envmod, "_git", dirty)
    assert _build(repo)["root_package"]["worktree_dirty"] is True


def test_the_contract_package_must_be_in_the_manifest(repo, no_git) -> None:
    """Without it the record cannot say which contract package produced it."""
    m = json.loads((repo / "lake-manifest.json").read_text(encoding="utf-8"))
    m["packages"] = [p for p in m["packages"] if p["name"] != "MathFormalContract"]
    (repo / "lake-manifest.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(EnvError, match="not in lake-manifest"):
        _build(repo)


def test_packages_are_sorted_so_the_digest_is_order_independent(repo, no_git) -> None:
    assert [p["name"] for p in _build(repo)["packages"]] == \
        sorted(p["name"] for p in _build(repo)["packages"])


# --- the artifact, and the CLI -------------------------------------------------

def test_the_document_validates_against_its_own_schema(
        repo, no_git, tmp_path: Path) -> None:
    out = tmp_path / "out" / "environment.json"
    out.parent.mkdir()
    out.write_text(json.dumps(_build(repo), indent=2), encoding="utf-8")
    assert main(["validate", str(out)]) == EXIT_OK


def test_cli_writes_and_reports_the_drift(
        repo, no_git, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "attest" / "environment.json"
    rc = main(["env", "--repo", str(repo), "--out", str(out),
               "--axiom-allowlist", "propext,Quot.sound,Classical.choice",
               "--emitter-version", "mfc-emit/1.0.0",
               "--lean-githash", HEX_B, "--lake-version", "Lake version 5.0.0"])
    captured = capsys.readouterr()

    assert rc == EXIT_OK
    assert out.is_file()
    assert "NOT PINNED" in captured.out and "mathlib" in captured.out
    assert "UNTAGGED" in captured.out


def test_cli_refuses_an_empty_allowlist(repo, no_git, tmp_path: Path) -> None:
    """An empty allowlist permits nothing and is never what a caller means."""
    assert main(["env", "--repo", str(repo), "--out", str(tmp_path / "e.json"),
                 "--axiom-allowlist", " , ",
                 "--emitter-version", "x"]) == EXIT_USAGE


def test_cli_exits_2_not_1_when_the_checkout_cannot_be_read(tmp_path: Path) -> None:
    """"Could not read the environment" must not be reportable as "the
    environment has findings"."""
    assert main(["env", "--repo", str(tmp_path / "nope"),
                 "--out", str(tmp_path / "e.json"),
                 "--axiom-allowlist", "propext",
                 "--emitter-version", "x"]) == EXIT_USAGE


def test_a_manifest_with_no_packages_cannot_name_a_contract_repo(
        repo, no_git) -> None:
    """This is why `mfc env` refuses to run against the contract package
    itself: a leaf has no manifest packages, so it is not a topic repo."""
    (repo / "lake-manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    with pytest.raises(EnvError, match="not in lake-manifest"):
        _build(repo)


def test_packages_reads_url_and_inherited_through(repo) -> None:
    pkgs = packages(repo)
    mathlib = next(p for p in pkgs if p["name"] == "mathlib")
    assert mathlib["inherited"] is True
    assert mathlib["url"] == "https://example.invalid/mathlib"
    assert branch_pinned(pkgs) == ["batteries", "mathlib"]
