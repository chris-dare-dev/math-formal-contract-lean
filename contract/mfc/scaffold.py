"""`mfc init` — render a topic repository that reaches a green build on run one.

The measure of this command is not that it produces files. It is that the tree
it produces **passes the rest of `mfc` immediately**, with no hand-editing. An
adopter whose first CI run is red learns nothing about their own work, and the
most likely thing they do next is delete the workflow.

That is a sharper constraint than it looks. `emission-1.0.schema.json` sets
`constants: minItems 1`, so an emission over zero declarations is not a
representable artifact — which means **the scaffold must ship a real Lean
declaration**, not an empty library. `test_the_scaffold_would_survive_its_own_
emission_schema` is what holds that.

## It does not create a repository

No `git init`, no remote, no first commit. A hand-initialised repository has no
remote, no CI permissions, no branch protection and no provisioning, and a tool
that quietly produced one would be handing the adopter something that looks
finished and is not. `mfc init` renders files into a directory and prints what
remains to be provisioned, by whatever path owns that tree.

It also refuses to write into a non-empty directory without `--force`, because
the first thing it would overwrite is a `lakefile.toml` someone wrote.

## Rendered with the standard library, not copier

The architecture note specifies copier, whose `.copier-answers.yml._commit`
would pin the template by exact commit and whose `copier update` is the stated
migration path for a MAJOR schema bump across N topic repos.

That is a real benefit with, today, **no users**: one topic repo exists and it
was not created from a template, so `copier update` has nothing to update. Set
against it, copier pulls jinja2, pydantic, plumbum and questionary into a
package whose README argues its own placement decisions from dependency
discipline, and whose Lake half is the *named exception* to a consuming repo's
one-pin rule specifically because it is a leaf.

So the templates below are plain text with `@@TOKEN@@` substitution — chosen
over `str.format` and `string.Template` because Lean uses both `{}` and `$`,
and over a regex because a scaffolder that mangles the file it writes is worse
than no scaffolder.

**Reversal condition, so this is a decision rather than a habit:** adopt copier
when a second topic repo exists *and* was created by this command *and* a MAJOR
schema bump is due. At that point `copier update` is doing work no one can do
by hand, and these same files become the template with the tokens rewritten to
`{{ }}`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

#: A 40-hex git commit. Branches are refused everywhere a pin is taken: a
#: branch re-resolves under `lake update` and the pin stops pinning.
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: `leanprover/lean4:v4.29.0`, or a nightly.
TOOLCHAIN_RE = re.compile(r"^[A-Za-z0-9._/-]+:[A-Za-z0-9._-]+$")

#: A topic slug. Same shape arXMCP notebooks use, so the two can share one.
TOPIC_RE = re.compile(r"^[a-z][a-z0-9-]{2,30}$")

#: A Lean library name: upper camel, no dots. Dots would make the root module
#: a submodule of something that does not exist.
LIB_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")


class ScaffoldError(Exception):
    """The scaffold could not be rendered. Exit 2 — nothing was written."""


class Answers(NamedTuple):
    topic: str
    lib: str
    toolchain: str
    mathlib_rev: str
    contract_rev: str
    contract_url: str
    anchor_name: str | None
    anchor_url: str | None
    anchor_rev: str | None

    @property
    def has_anchor(self) -> bool:
        return bool(self.anchor_name and self.anchor_url and self.anchor_rev)


def lib_from_topic(topic: str) -> str:
    """`analytic-nt` -> `AnalyticNt`. A default, never a silent override."""
    return "".join(part.capitalize() for part in topic.split("-") if part)


def validate(answers: Answers) -> None:
    """Every check that must happen BEFORE a byte is written.

    Rendering half a tree and then failing leaves the adopter worse off than
    not running the command, so validation is total and up front.
    """
    if not TOPIC_RE.match(answers.topic):
        raise ScaffoldError(
            f"--topic {answers.topic!r} must match {TOPIC_RE.pattern} -- the same "
            f"shape an arXMCP notebook slug takes, so one can name the other")
    if not LIB_RE.match(answers.lib):
        raise ScaffoldError(
            f"--lib {answers.lib!r} must match {LIB_RE.pattern}: upper camel case, "
            f"no dots. A dotted name would make the root module a submodule of a "
            f"module that does not exist")
    if not TOOLCHAIN_RE.match(answers.toolchain):
        raise ScaffoldError(
            f"--toolchain {answers.toolchain!r} must look like "
            f"'leanprover/lean4:v4.29.0'")
    for flag, value in (("--mathlib-rev", answers.mathlib_rev),
                        ("--contract-rev", answers.contract_rev)):
        if not SHA_RE.match(value):
            raise ScaffoldError(
                f"{flag} must be a 40-hex commit, got {value!r}. A branch name is "
                f"refused deliberately: `lake update` re-resolves it, so the pin "
                f"would stop pinning without anything changing in the repo")
    anchor_given = [x for x in (answers.anchor_name, answers.anchor_url,
                                answers.anchor_rev) if x]
    if anchor_given and len(anchor_given) != 3:
        raise ScaffoldError(
            "--anchor-name, --anchor-url and --anchor-rev must be given together "
            "or not at all; a half-specified anchor cannot be pinned")
    if answers.anchor_rev and not SHA_RE.match(answers.anchor_rev):
        raise ScaffoldError(
            f"--anchor-rev must be a 40-hex commit, got {answers.anchor_rev!r}")


LEAN_TOOLCHAIN = "@@TOOLCHAIN@@\n"

GITIGNORE = """\
/.lake/
__pycache__/

# attest/ is NOT ignored wholesale, and the difference is the gate.
#
# `git diff --exit-code attest/` in the workflow asserts that the committed
# attestations are byte-identical to the ones this run produced. Ignoring
# `/attest/*.json` -- which this file used to do -- made that assertion compare
# nothing: it could not fail, so it was not a gate, it was a green step.
#
# Two files are ignored, each for a stated reason:
#
#   * lean-emission.json is the emitter's raw output. It carries `emitted_at`
#     and is an INPUT to the bundle rather than an attestation; the committed
#     declarations.json is what it is evidence for.
#   * run.json is run/1.0 -- the timestamp, the CI run URL, the runner. Every
#     field that changes per run lives there precisely so that nothing which
#     changes per run lives in a committed file. It ships as a release asset.
#
# Everything else under attest/ is committed and must be reproducible.
/attest/lean-emission.json
/attest/run.json
"""

LAKEFILE = '''\
name = "@@LIB@@"
version = "0.1.0"
defaultTargets = ["@@LIB@@", "emit"]

# ONE PIN, and it is the anchor's.
#
# Mathlib is required directly here so the revision is visible and reviewable,
# but it must be the SAME revision the anchor resolves to. Two independent
# Mathlib pins in one environment is not a version conflict Lake reports -- it
# is a silent re-resolution, and the first symptom is a proof that stops
# compiling for no reason anyone can see in the diff.
#
# math-formal-contract is the single NAMED EXCEPTION, and the exception is
# argued from its leaf property: zero transitive dependencies, core Lean only,
# no Mathlib and no anchor. It cannot drag anything into this environment and
# cannot disagree with the anchor about a Mathlib revision. If it ever grows a
# dependency, the exception lapses and this block has to be re-argued.

[leanOptions]
autoImplicit = false
relaxedAutoImplicit = false

[[require]]
name = "mathlib"
git = "https://github.com/leanprover-community/mathlib4"
rev = "@@MATHLIB_REV@@"

@@ANCHOR_REQUIRE@@[[require]]
name = "MathFormalContract"
git = "@@CONTRACT_URL@@"
rev = "@@CONTRACT_REV@@"

[[lean_lib]]
name = "@@LIB@@"

# The emitter, pointed at this library. These are the only generated Lean
# lines in the repository, which is what keeps a contract upgrade from
# three-way-merging a metaprogram.
[[lean_exe]]
name = "emit"
root = "Emit"
srcDir = "exe"
# Required by any binary that loads an environment with `importModules`.
#
# Omitting it FAILS ONLY ON LINUX: macOS resolves the missing symbol
# dynamically, so the binary runs on a laptop and dies in CI with "Could not
# find native implementation of external declaration 'IO.getRandomBytes'".
supportInterpreter = true
'''

ANCHOR_REQUIRE = '''[[require]]
name = "@@ANCHOR_NAME@@"
git = "@@ANCHOR_URL@@"
rev = "@@ANCHOR_REV@@"

'''

ROOT_MODULE = """\
import @@LIB@@.Basic
"""

BASIC = '''\
/-!
# @@LIB@@

The scaffold ships one real declaration on purpose.

`emission-1.0.schema.json` sets `constants: minItems 1`, so an emission over
zero declarations is not a representable artifact and `mfc validate` refuses
it. A library with nothing in it therefore cannot reach a green build, and the
adopter's first CI run would be red for a reason that has nothing to do with
their work. Replace this with real content; do not delete it and leave the
library empty.
-/

namespace @@LIB@@

/-- Placeholder. Replace with the first statement this topic formalizes. -/
theorem scaffold_placeholder : True := trivial

end @@LIB@@
'''

EMIT_EXE = '''\
import MathFormalContract

/-!
# The emitter, pointed at this repository's library

`leanOptions` is **declared here, not observed**. Elaboration options are
compile flags and are not recorded in the `.olean`, so the emitter cannot read
them back out of the environment -- reporting the process defaults instead
would make the artifact claim a setting the build did not use.

It must therefore mirror the `[leanOptions]` block of `lakefile.toml`
character for character. `mfc lint` fails a mismatch.
-/

def main (args : List String) : IO UInt32 :=
  MathFormalContract.emitMain
    (rootLib := `@@LIB@@)
    (leanOptions := [("autoImplicit", .bool false),
                     ("relaxedAutoImplicit", .bool false)])
    args
'''

CONTRACT_LOCK = """\
# The contract this repository is built against, pinned by exact commit.
#
# Never a branch. `mfc` verifies this pin, and a branch would let the schemas
# change under a repository whose artifacts still claim to satisfy them.
contract_url = "@@CONTRACT_URL@@"
contract_rev = "@@CONTRACT_REV@@"
"""

FORMALIZATION_YAML = """\
# A claim, not decoration. Every field here is read by machines and by people
# deciding whether to trust this repository.
#
# Unfillable fields are `none` or `pending` -- NEVER a plausible-looking guess.
# A wrong value is worse than an absent one, because an absent one is visibly
# absent.
topic: @@TOPIC@@
lean_library: @@LIB@@

# This repository has no declarations yet, so its emission is empty, and an
# empty emission does not validate -- `minItems: 1` on `constants[]` is the
# vacuous-pass guard and it is doing its job. `bootstrap: true` suspends
# exactly that one constraint so a new repository can reach a green build
# before it has any mathematics in it. While it is set, every content rule
# reports `not_run`: nothing here has been checked.
#
# It is WRITE-ONCE. `mfc lint` clears it the first time the emission is
# non-empty and stamps `bootstrap_cleared_at`; after that, setting it again is
# a hard failure. It is a starting state, not a way to silence the guard.
bootstrap: true

source:
  # The paper or book this topic formalizes.
  scheme: pending          # arxiv | textbook | none
  identifier: none
  version: none

anchor: @@ANCHOR_YAML@@

status:
  sorry_count: 0           # keep at 0; absent beats sorry-backed
  builds_clean: pending    # set by CI, never by hand
  axiom_policy: [propext, Classical.choice, Quot.sound]

review:
  human_review: none       # a dated, named act or nothing at all
  machine_review: pending
"""

CLAUDE_MD = '''\
# @@LIB@@ — working agreements

Four invariants. They are not style preferences; each one is enforced
mechanically and each has a failure it was written in response to.

## 1. The pins are load-bearing

`lakefile.toml` carries **one** Mathlib revision, and it must be the revision
the anchor resolves to. Two independent Mathlib pins in one environment is not
a conflict Lake reports — it is a silent re-resolution whose first symptom is a
proof that stops compiling with nothing in the diff to explain it.

`math-formal-contract` is the single **named exception**, argued from its leaf
property: zero transitive dependencies, core Lean only, no Mathlib and no
anchor. If it ever grows a dependency the exception lapses.

Never pin a branch. `lake update` re-resolves it and the pin stops pinning.

## 2. No `sorry`. Absent beats sorry-backed.

A declaration that does not exist is an honest gap. A declaration that exists
and is sorry-backed is a false claim in a file whose purpose is to be believed,
and it is indistinguishable from a real one to everything downstream.

If a proof needs a fact this environment cannot supply, say so and stop.
Do not axiomatize the gap.

## 3. Closed lanes are declared, not assumed

A lane you have decided not to enter is a fact about this repository, and it
belongs in `closed-lanes.json` where `mfc lint` rule `E-09` can enforce it —
not in someone's memory. A declaration reaching into a closed lane is a finding
whether or not anyone remembers the decision.

## 4. `formalization.yaml` is a claim, not decoration

Every field is read by machines and by people deciding whether to trust this
repository. Unfillable fields are `none` or `pending`, never a plausible guess:
an absent value is visibly absent, and a wrong one is not.

## Build

```bash
lake build
lake exe emit --out attest/lean-emission.json
mfc bundle --emission attest/lean-emission.json \\
           --environment attest/environment.json \\
           --out attest/declarations.json
mfc lint --emission attest/lean-emission.json \\
         --environment attest/environment.json
mfc check-ilean-coverage --emission attest/lean-emission.json
```
'''

WORKFLOW = """\
# Generated by `mfc init`. Edit freely -- it is yours now.
#
# The order matters. `check-ilean-coverage` runs LAST of the checks because it
# is the only one that looks for what is ABSENT: everything before it reads the
# emission and asks whether its contents are allowed, and all of them pass
# happily over an emission that swept the wrong modules.
name: contract

on:
  push:
    branches: [main]
  pull_request:

jobs:
  contract:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4

      - uses: leanprover/lean-action@v1
        with:
          use-mathlib-cache: true

      - name: install mfc
        # The `yaml` extra is not optional here. `formalization.yaml` carries
        # the bootstrap flag, and a build that cannot read the flag cannot tell
        # "this repository has not started yet" from "this emission is empty
        # and should not be" -- so it fails rather than guessing.
        run: pip install 'mfc[yaml] @ @@CONTRACT_URL@@/archive/@@CONTRACT_REV@@.tar.gz#subdirectory=contract'

      - name: emit
        run: |
          mkdir -p attest
          lake exe emit --out attest/lean-emission.json

      - name: bundle and validate
        run: |
          mfc validate attest/lean-emission.json
          mfc bundle --emission attest/lean-emission.json \\
                     --environment attest/environment.json \\
                     --out attest/declarations.json
          mfc validate attest/declarations.json

      - name: lint
        # --require-all is deliberately NOT passed: rules whose inputs do not
        # exist yet report not_run, and the run prints which ones rather than
        # hiding them in a count.
        run: |
          mfc lint --emission attest/lean-emission.json \\
                   --environment attest/environment.json

      - name: coverage
        run: mfc check-ilean-coverage --emission attest/lean-emission.json

      # This is the only sorry-gate that survives the seam, and it works only
      # because nothing under attest/ that is committed changes per run: the
      # timestamp and the CI identity live in attest/run.json, which is
      # gitignored (run/1.0). If you add a field that moves every run to a
      # committed artifact, this step reddens on no-op commits -- fix the
      # field, not this line. `mfc lint-schemas` fails on exactly that mistake.
      #
      # `--stat` on failure, because the default output of a diff over
      # generated JSON is unreadable and an unreadable gate gets skipped.
      - name: no hand-edited attestations
        run: git diff --exit-code --stat attest/ || {
          echo "::error::attest/ differs from what this run produced"; exit 1; }
"""


def render(answers: Answers) -> dict[str, str]:
    """The file set, as `relative path -> contents`. Writes nothing."""
    validate(answers)
    anchor_block = ""
    anchor_yaml = "none"
    if answers.has_anchor:
        anchor_block = _sub(ANCHOR_REQUIRE, answers)
        anchor_yaml = (f"\n  name: {answers.anchor_name}"
                       f"\n  url: {answers.anchor_url}"
                       f"\n  rev: {answers.anchor_rev}")

    files = {
        "lean-toolchain": LEAN_TOOLCHAIN,
        ".gitignore": GITIGNORE,
        "lakefile.toml": LAKEFILE.replace("@@ANCHOR_REQUIRE@@", anchor_block),
        f"{answers.lib}.lean": ROOT_MODULE,
        f"{answers.lib}/Basic.lean": BASIC,
        "exe/Emit.lean": EMIT_EXE,
        "contract.lock": CONTRACT_LOCK,
        "formalization.yaml": FORMALIZATION_YAML.replace("@@ANCHOR_YAML@@",
                                                         anchor_yaml),
        "CLAUDE.md": CLAUDE_MD,
        ".github/workflows/contract.yml": WORKFLOW,
        "registry/.gitkeep": "",
        "attest/.gitkeep": "",
    }
    rendered = {path: _sub(text, answers) for path, text in files.items()}

    leftover = {p: _leftover(t) for p, t in rendered.items() if _leftover(t)}
    if leftover:
        # A template token that survived rendering would ship `@@LIB@@` into an
        # adopter's lakefile. Caught here rather than by them.
        raise ScaffoldError(
            f"unsubstituted template tokens: "
            + "; ".join(f"{p}: {sorted(t)}" for p, t in sorted(leftover.items())))
    return rendered


def _sub(text: str, a: Answers) -> str:
    for token, value in (
        ("@@LIB@@", a.lib),
        ("@@TOPIC@@", a.topic),
        ("@@TOOLCHAIN@@", a.toolchain),
        ("@@MATHLIB_REV@@", a.mathlib_rev),
        ("@@CONTRACT_URL@@", a.contract_url),
        ("@@CONTRACT_REV@@", a.contract_rev),
        ("@@ANCHOR_NAME@@", a.anchor_name or ""),
        ("@@ANCHOR_URL@@", a.anchor_url or ""),
        ("@@ANCHOR_REV@@", a.anchor_rev or ""),
    ):
        text = text.replace(token, value)
    return text


def _leftover(text: str) -> set[str]:
    return set(re.findall(r"@@[A-Z_]+@@", text))


def write(files: dict[str, str], dest: Path, *, force: bool = False) -> list[Path]:
    """Write the rendered set under `dest`. Returns the paths written, sorted.

    Refuses a non-empty directory without `force`: the first thing this would
    overwrite is a `lakefile.toml` somebody wrote.
    """
    if dest.exists() and any(dest.iterdir()) and not force:
        raise ScaffoldError(
            f"{dest} is not empty. Refusing to overwrite; pass --force if that "
            f"is what you want.")
    written = []
    for rel, text in sorted(files.items()):
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


#: Printed after a successful render. Everything here is a thing `mfc init`
#: deliberately did NOT do, so an adopter is never left believing the repository
#: is finished when it is not.
NEXT_STEPS = """\
Rendered, but NOT created as a repository. `mfc init` runs no `git init`, adds
no remote and makes no commit -- a hand-initialised repository has no remote, no
CI permissions, no branch protection and no provisioning, and handing you one
that looks finished would be the unhelpful thing to do.

Still to do, in order:

  1. Create the repository through whatever path provisions repositories for
     you, and push this tree to it.
  2. Fill `formalization.yaml`: `source.scheme`, `source.identifier`,
     `source.version`. Leave anything you cannot fill as `none` or `pending`.
  3. Replace `{lib}/Basic.lean`. It ships one real declaration because an
     emission over zero constants is not a representable artifact -- do not
     delete it and leave the library empty.
  4. `lake build && lake exe emit --out attest/lean-emission.json`.
  5. Mint a registry id and start the registry. `@[cites]` keys are
     `stmt:<12 hex>:<label>`, and the 12 hex is minted once for this repository.

The corpus side is separate and is not mechanised by this command. Standing up
an arXMCP notebook has three steps that can be skipped without any error being
reported -- the `bridgeland-stability` notebook ran 13 ingests with
`display_name` empty before anyone noticed.
"""
