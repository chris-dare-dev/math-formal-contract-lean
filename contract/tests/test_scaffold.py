"""Tests for `mfc init`.

The claim under test is **not** "it writes files". It is that the tree it
writes passes the rest of `mfc` with no hand-editing, because an adopter whose
first CI run is red learns nothing about their own work and is most likely to
delete the workflow. `test_the_generated_repository_survives_the_whole_chain`
is the one that actually establishes it; everything above it guards a specific
way the scaffold could ship something broken.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from contract.mfc.cli import EXIT_OK, EXIT_USAGE, main
from contract.mfc.scaffold import (
    Answers,
    ScaffoldError,
    lib_from_topic,
    render,
    validate,
    write,
)

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
VALID_DIR = HERE.parent / "testdata" / "artifacts" / "valid"

SHA = "8a178386ffc0f5fef0b77738bb5449d50efeea95"
SHA2 = "9e48f23a382ba117b63076a33e0e775389fef1ba"


def _answers(**over) -> Answers:
    base = dict(
        topic="analytic-nt", lib="AnalyticNT",
        toolchain="leanprover/lean4:v4.29.0",
        mathlib_rev=SHA, contract_rev=SHA2,
        contract_url="https://github.com/chris-dare-dev/math-formal-contract-lean",
        anchor_name=None, anchor_url=None, anchor_rev=None,
    )
    base.update(over)
    return Answers(**base)


# --- pins are pins ------------------------------------------------------------

@pytest.mark.parametrize("field", ["mathlib_rev", "contract_rev"])
def test_a_branch_is_refused_where_a_commit_belongs(field: str) -> None:
    """`lake update` re-resolves a branch, so the pin would stop pinning."""
    with pytest.raises(ScaffoldError, match="40-hex"):
        validate(_answers(**{field: "main"}))


def test_a_short_sha_is_refused() -> None:
    with pytest.raises(ScaffoldError, match="40-hex"):
        validate(_answers(mathlib_rev=SHA[:12]))


def test_a_half_specified_anchor_is_refused() -> None:
    with pytest.raises(ScaffoldError, match="together or not at all"):
        validate(_answers(anchor_name="BridgelandStability"))


def test_a_complete_anchor_is_accepted_and_rendered() -> None:
    files = render(_answers(anchor_name="BridgelandStability",
                            anchor_url="https://github.com/mattrobball/BridgelandStability",
                            anchor_rev=SHA2))
    assert "BridgelandStability" in files["lakefile.toml"]
    assert files["lakefile.toml"].count("[[require]]") == 3
    assert "name: BridgelandStability" in files["formalization.yaml"]


def test_no_anchor_renders_two_requires_and_says_none() -> None:
    files = render(_answers())
    assert files["lakefile.toml"].count("[[require]]") == 2
    assert "anchor: none" in files["formalization.yaml"]


# --- names --------------------------------------------------------------------

def test_a_dotted_library_name_is_refused() -> None:
    """It would make the root module a submodule of a module that does not exist."""
    with pytest.raises(ScaffoldError, match="upper camel"):
        validate(_answers(lib="Analytic.NT"))


def test_a_bad_topic_slug_is_refused() -> None:
    with pytest.raises(ScaffoldError, match="--topic"):
        validate(_answers(topic="Analytic NT"))


def test_the_library_name_defaults_from_the_topic() -> None:
    assert lib_from_topic("analytic-nt") == "AnalyticNt"
    assert lib_from_topic("bridgeland-stability") == "BridgelandStability"


# --- nothing ships a template token ------------------------------------------

def test_no_rendered_file_carries_an_unsubstituted_token() -> None:
    """A surviving `@@LIB@@` would land in an adopter's lakefile."""
    for path, text in render(_answers()).items():
        assert not re.findall(r"@@[A-Z_]+@@", text), f"{path} has live tokens"


def test_the_renderer_refuses_rather_than_shipping_a_token() -> None:
    """The guard itself must work, so it is exercised on a doctored template."""
    import contract.mfc.scaffold as scaffold
    original = scaffold.GITIGNORE
    scaffold.GITIGNORE = original + "@@NOT_A_REAL_TOKEN@@\n"
    try:
        with pytest.raises(ScaffoldError, match="unsubstituted template tokens"):
            render(_answers())
    finally:
        scaffold.GITIGNORE = original


# --- the scaffold must be able to reach a green build -------------------------

def test_the_library_ships_a_real_declaration() -> None:
    """`constants: minItems 1` means an empty library cannot reach a green build.

    This is the constraint that makes the scaffold's content load-bearing rather
    than cosmetic, and it is easy to "tidy away" later without noticing.
    """
    files = render(_answers())
    basic = files["AnalyticNT/Basic.lean"]
    assert re.search(r"^theorem \w+", basic, re.M), (
        "the scaffold library has no declaration, so its first emission would "
        "have constants: [] and be rejected by emission-1.0.schema.json")
    assert "minItems" in basic, "the reason is not written down where it will be read"


def test_the_emitter_exe_declares_the_lakefiles_lean_options() -> None:
    """They cannot be read back out of the .olean, so a mismatch is silent."""
    files = render(_answers())
    for option in ("autoImplicit", "relaxedAutoImplicit"):
        assert option in files["lakefile.toml"]
        assert option in files["exe/Emit.lean"]


def test_the_emitter_exe_sets_support_interpreter() -> None:
    """Omitting it fails ONLY on Linux; it would pass every local check."""
    assert "supportInterpreter = true" in render(_answers())["lakefile.toml"]


def test_the_rendered_workflow_is_valid_yaml() -> None:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(render(_answers())[".github/workflows/contract.yml"])
    steps = doc["jobs"]["contract"]["steps"]
    names = [s.get("name", "") for s in steps]
    assert any("coverage" in n for n in names)
    # The absence check must come after the checks that read the emission.
    assert names.index("coverage") > names.index("lint")


def test_the_rendered_lakefile_is_valid_toml() -> None:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python 3.9/3.10
        tomllib = pytest.importorskip("tomli")
    doc = tomllib.loads(render(_answers())["lakefile.toml"])
    assert doc["name"] == "AnalyticNT"
    assert [r["name"] for r in doc["require"]] == ["mathlib", "MathFormalContract"]
    assert doc["lean_exe"][0]["supportInterpreter"] is True


# --- it does not create a repository ------------------------------------------

def test_it_writes_no_git_directory(tmp_path: Path) -> None:
    """No `git init`, no remote, no commit. A hand-initialised repository has
    none of the things that make a repository useful, and one that looks
    finished is worse than one that obviously is not."""
    write(render(_answers()), tmp_path)
    assert not (tmp_path / ".git").exists()
    assert not any(p.name == ".git" for p in tmp_path.rglob("*"))


def test_it_refuses_a_non_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "lakefile.toml").write_text("name = \"Mine\"\n", encoding="utf-8")
    with pytest.raises(ScaffoldError, match="not empty"):
        write(render(_answers()), tmp_path)
    assert (tmp_path / "lakefile.toml").read_text(encoding="utf-8") == 'name = "Mine"\n'


def test_force_overwrites_deliberately(tmp_path: Path) -> None:
    (tmp_path / "lakefile.toml").write_text("name = \"Mine\"\n", encoding="utf-8")
    write(render(_answers()), tmp_path, force=True)
    assert "AnalyticNT" in (tmp_path / "lakefile.toml").read_text(encoding="utf-8")


# --- the CLI ------------------------------------------------------------------

def _argv(dest: Path, **over) -> list[str]:
    args = {"--topic": "analytic-nt", "--toolchain": "leanprover/lean4:v4.29.0",
            "--mathlib-rev": SHA, "--contract-rev": SHA2, "--dest": str(dest)}
    args.update(over)
    return ["init", *[x for kv in args.items() for x in kv]]


def test_cli_writes_the_tree(tmp_path: Path) -> None:
    assert main(_argv(tmp_path)) == EXIT_OK
    assert (tmp_path / "lakefile.toml").is_file()
    assert (tmp_path / "AnalyticNt" / "Basic.lean").is_file()
    assert (tmp_path / ".github" / "workflows" / "contract.yml").is_file()


def test_cli_dry_run_writes_nothing(tmp_path: Path) -> None:
    assert main([*_argv(tmp_path), "--dry-run"]) == EXIT_OK
    assert not any(tmp_path.iterdir())


def test_cli_rejects_a_branch_pin_without_writing(tmp_path: Path) -> None:
    assert main(_argv(tmp_path, **{"--mathlib-rev": "main"})) == EXIT_USAGE
    assert not any(tmp_path.iterdir()), "a rejected render must leave no partial tree"


def test_cli_prints_what_it_did_not_do(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(_argv(tmp_path))
    err = capsys.readouterr().err
    assert "no `git init`" in err
    assert "Create the repository through whatever path provisions" in err


# --- the whole point ----------------------------------------------------------

#: Set in the CI job that has a Lean toolchain. Without it a machine with no
#: `lake` skips the end-to-end test, which is right locally and WRONG in CI --
#: a skipped check reads exactly like a passing one in a green run, which is the
#: confusion this whole package exists to prevent. So the skip is refusable.
REQUIRE_LAKE = "MFC_REQUIRE_LAKE"


def _lake_or_skip() -> None:
    if shutil.which("lake"):
        return
    if os.environ.get(REQUIRE_LAKE):
        pytest.fail(
            f"{REQUIRE_LAKE} is set but `lake` is not on PATH. This test is the "
            f"only thing that establishes the scaffold reaches a green build, "
            f"and skipping it in CI would look identical to passing it.")
    pytest.skip("lake is not installed; set MFC_REQUIRE_LAKE=1 to make this fatal")


def test_the_generated_repository_survives_the_whole_chain(tmp_path: Path) -> None:
    """Render, build, emit, and run every check that does not need a registry.

    Mathlib is swapped for a local path require to the contract package: this
    is about whether the *generated Lean* compiles and emits a schema-valid
    artifact, and downloading Mathlib would test lake's network code instead.
    Everything else — the emitter exe, the module scope, the emission, the
    coverage set-diff — is exactly what an adopter gets.
    """
    _lake_or_skip()
    dest = tmp_path / "topic"
    dest.mkdir()
    toolchain = (REPO / "lean-toolchain").read_text(encoding="utf-8").strip()
    assert main(_argv(dest, **{"--toolchain": toolchain})) == EXIT_OK

    lakefile = dest / "lakefile.toml"
    text = lakefile.read_text(encoding="utf-8")
    text = re.sub(r'\[\[require\]\]\nname = "mathlib".*?\n\n', "", text, flags=re.S)
    text = re.sub(r'\[\[require\]\]\nname = "MathFormalContract"\ngit = .*?\nrev = .*?\n',
                  f'[[require]]\nname = "MathFormalContract"\npath = "{REPO}"\n', text)
    lakefile.write_text(text, encoding="utf-8")
    shutil.copy(REPO / "lean-toolchain", dest / "lean-toolchain")

    env = {**os.environ, "PATH": f"{Path.home()}/.elan/bin:{os.environ['PATH']}"}
    build = subprocess.run(["lake", "build"], cwd=dest, env=env,
                           capture_output=True, text=True, timeout=600)
    assert build.returncode == 0, f"the generated repository does not build:\n{build.stderr}"

    emission = dest / "attest" / "lean-emission.json"
    emit = subprocess.run(["lake", "exe", "emit", "--out", str(emission)],
                          cwd=dest, env=env, capture_output=True, text=True, timeout=600)
    assert emit.returncode == 0, f"the generated emitter fails:\n{emit.stderr}"

    doc = json.loads(emission.read_text(encoding="utf-8"))
    assert doc["constants"], "the first emission is empty, so the schema will reject it"
    assert doc["root_lib"] == "AnalyticNt"

    environment = str(VALID_DIR / "environment-1.0.json")
    assert main(["validate", str(emission)]) == EXIT_OK
    assert main(["lint", "--emission", str(emission),
                 "--environment", environment]) == EXIT_OK
    assert main(["check-ilean-coverage", "--emission", str(emission),
                 "--build-dir", str(dest / ".lake" / "build" / "lib" / "lean")]) == EXIT_OK
    declarations = dest / "attest" / "declarations.json"
    assert main(["bundle", "--emission", str(emission), "--environment", environment,
                 "--out", str(declarations)]) == EXIT_OK
    assert main(["validate", str(declarations)]) == EXIT_OK
    assert main(["join", "--declarations", str(declarations)]) == EXIT_OK
