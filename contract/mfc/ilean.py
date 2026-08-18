"""`mfc check-ilean-coverage` — the I-01..I-05 rules. The vacuous-pass guard.

Every other check in this package reads the emission and asks whether what it
*contains* is allowed. This one asks the question none of them can: **is
anything missing?** A mis-scoped emitter produces a perfectly valid, perfectly
lint-clean emission over zero declarations, and every downstream artifact is
then immaculate and empty. `E-08` catches the fully-empty case from `counts`
alone; this catches the far more likely partial one, where the emitter swept
some modules and silently skipped others.

## Why `.ilean`, and why it is not circular

`lake build` writes one `.ilean` per module, next to the `.olean`, containing a
`decls` map of exactly the source-written declaration names. It is plain JSON
and it is produced by Lake, not by us — so it is the one description of "what
was built" that does not come from the emitter.

The circularity trap is real and worth stating. The emission carries a
`modules[]` array, computed by the emitter's own `inScopeModules`. Comparing
`.ilean` files against *that* would check the emitter against itself: a scope
bug that dropped a module would drop it from both sides and pass. So the
in-scope set is re-derived here, from the filesystem, using only the emission's
**declared** roots — which the caller passes in, and which `--lib` overrides.
`--lib` is repeatable, because the emitter's `additionalRoots` means a topic
legitimately has more than one; see `roots_of`. `I-04` then compares the two derivations, which is the check that
finds a scope bug rather than inheriting it.

## Module prefixes are matched component-wise, never as strings

`MathFormalContractTest` is not a submodule of `MathFormalContract`, but
`"MathFormalContractTest".startswith("MathFormalContract")` is `True`. A string
prefix would pull the test library into scope, and then `I-03` would fail on
seven declarations the emitter is *correct* to exclude. Matching on the dotted
components is the whole of the fix, and `test_a_sibling_name_is_not_a_submodule`
is what keeps it.

## The bootstrap, and where it is actually solved

A brand-new topic repo's first build has zero declarations, and a guard that
failed there would mean no adopter could ever reach a first green build.

That is solved **upstream of every rule in this package**, and not here:
`emission-1.0.schema.json` sets `constants: minItems 1` and
`counts.total/in_scope: minimum 1`, so an empty emission is not a representable
artifact at all. `mfc validate` rejects it, and every subcommand validates
before it reads.

**Amended (#159).** That used to end "an adopter's first green build therefore
requires exactly one declaration", and it made the first build *unreachable*
instead — the generated workflow emits before anything has been written. The
write-once `bootstrap` flag (`mfc.bootstrap`) now relaxes exactly those three
constraints, and only while it is set, only for a genuinely empty repository,
and never for the mis-scoped case this file exists to catch.

The three states this file distinguishes are still distinguished, for a caller
that reaches `check()` directly rather than through the CLI:

* `.ilean` files carry declarations, `constants[]` is empty → the mis-scoped
  emitter. **Fail.**
* `.ilean` files carry nothing, `constants[]` is empty → a genuinely empty
  repository. **Pass**, and say loudly that nothing was checked.
* No `.ilean` files at all → the build directory is wrong or nothing was built.
  **The check did not run**: exit 2, never a pass.

Note what is *not* here: an `--allow-empty` flag. It would have collapsed the
first two states into one, which is the failure this file exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from .rules import Finding, RuleResult, Status

#: Where `lake build` writes `.ilean` files, relative to the package root.
DEFAULT_BUILD_DIR = Path(".lake/build/lib/lean")

#: Most findings printed by `I-03`. A mis-scoped emitter misses hundreds at
#: once; the cap keeps the output readable and is always reported alongside the
#: true total, never applied silently.
MAX_LISTED = 50


class IleanError(Exception):
    """The check could not run. Exit 2, never a finding."""


class Module(NamedTuple):
    name: str
    path: Path
    decls: frozenset[str]
    #: What this module imports DIRECTLY, from the `.ilean`'s own
    #: `directImports`. Direct rather than transitive is the right granularity
    #: for an allowlist: a topic module that imports `Mathlib.Order.Basic`
    #: transitively pulls in half of Mathlib, and judging it on that would make
    #: every allowlist either empty or meaningless. What a module *writes at
    #: the top of the file* is the thing its author chose.
    #:
    #: `None` means the `.ilean` did not record the key AT ALL, and is not the
    #: same fact as "this module imports nothing" — `frozenset()` says that. A
    #: reader that collapsed the two would let an allowlist report a clean
    #: sweep over files it could not see the imports of, which is the vacuous
    #: pass in its purest form.
    direct_imports: frozenset[str] | None = None


def module_components(name: str) -> tuple[str, ...]:
    return tuple(name.split("."))


def is_under(module: str, root: str) -> bool:
    """Component-wise module containment. `root` counts as under itself.

    NOT `str.startswith`: `MathFormalContractTest` starts with
    `MathFormalContract` as a string while being an unrelated library.
    """
    m, r = module_components(module), module_components(root)
    return m[:len(r)] == r


def load_modules(build_dir: Path) -> list[Module]:
    """Every `.ilean` under `build_dir`, sorted by module name.

    Raises `IleanError` when there are none — a mis-pointed `--build-dir` and a
    clean sweep must not look the same, and this is the only place that
    distinction can still be made.
    """
    if not build_dir.is_dir():
        raise IleanError(
            f"no such build directory: {build_dir}. Run `lake build` first, or "
            f"pass --build-dir.")
    out: list[Module] = []
    for path in sorted(build_dir.rglob("*.ilean")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IleanError(f"{path}: unreadable .ilean: {exc}") from exc
        name = doc.get("module")
        decls = doc.get("decls")
        # `directImports` is read leniently, and only this key is: an .ilean
        # written before Lake recorded it is still perfectly usable for the
        # coverage rules, which are what this reader has always been for.
        # `I-06` reports `not_run` rather than passing when it is absent --
        # see `allowlist()` -- so leniency here does not become a silent pass
        # one layer up.
        raw_imports = doc.get("directImports")
        imports = frozenset(i for i in raw_imports if isinstance(i, str)) \
            if isinstance(raw_imports, list) else None
        if not isinstance(name, str) or not isinstance(decls, dict):
            # An .ilean whose shape we do not recognise means Lake changed the
            # format. Guessing would silently reduce coverage to nothing.
            raise IleanError(
                f"{path}: unrecognised .ilean layout (version "
                f"{doc.get('version')!r}); expected string `module` and object "
                f"`decls`. This build cannot check coverage against it.")
        out.append(Module(name=name, path=path, decls=frozenset(decls),
                          direct_imports=imports))
    if not out:
        raise IleanError(
            f"no *.ilean files under {build_dir}. Nothing was built, or the "
            f"build directory is wrong -- either way this check did NOT run, "
            f"and an empty sweep is not a clean one.")
    return out


def roots_of(emission: dict, lib: str | list[str] | None = None) -> list[str]:
    """The module roots this emission is scoped to.

    **A topic may have more than one, and this repo's own consumer does.** The
    emitter takes `additionalRoots` precisely so a monorepo can keep a thin
    combined umbrella while sweeping the declarations owned by each constituent
    library — `derived-alg-geo-lean` emits with `rootLib := DerivedAlgGeoSweep`
    and `additionalRoots := [DerivedAlgGeo]`.

    Scoping to `root_lib` alone got that badly wrong in both directions, and
    the failure is loud rather than subtle: with the sweep module as the only
    root, `I-05` fails on every constant the library declares, because they all
    come from modules outside it. With `DerivedAlgGeo` as the only root, `I-04`
    and `I-05` fail on the sweep module instead. Neither is a defect in the
    repository being checked; both are this function's predecessor answering a
    single-root question about a two-root emission.

    `lib` overrides, and accepts a list for exactly that reason.
    """
    if isinstance(lib, str):
        return [lib]
    if lib:
        return list(lib)
    declared = emission.get("root_lib")
    return [declared] if declared else []


def check(emission: dict, modules: list[Module], *,
          lib: str | list[str] | None = None) -> list[RuleResult]:
    """Run I-01..I-05 against an emission and the modules Lake actually built."""
    roots = roots_of(emission, lib)
    root = roots[0] if roots else None
    results: list[RuleResult] = []
    # `scope: external` rows are excluded from every rule here, and the reason
    # is the same one that makes them safe: they are constants someone else
    # proved, in modules Lake never built for this repository. Counting them as
    # coverage would let a topic raise its own numbers by citing Mathlib, and
    # feeding their modules to `I-04` would report `Init.Data.List.Basic` as a
    # module the emitter swept out of scope -- a finding about the emitter
    # doing exactly what it was told.
    topic = [c for c in emission["constants"] if c.get("scope", "topic") != "external"]
    names = {c["name"] for c in topic}
    emission_modules = {c["module"] for c in topic}

    def add(rule: str, title: str, findings: list[Finding], reason: str = "") -> None:
        results.append(RuleResult(
            rule, title, Status.FAIL if findings else Status.PASS,
            tuple(findings), reason))

    # I-01 -- there is a root library to scope to at all.
    if not root:
        results.append(RuleResult(
            "I-01", "the emission declares a root library", Status.FAIL,
            (Finding("I-01", "root_lib",
                     "the emission declares no root_lib and --lib was not given; "
                     "without it there is no non-circular way to decide which "
                     "built modules should have been swept"),)))
        return results
    add("I-01", "the emission declares a root library", [],
        reason=f"roots = {roots!r}")

    in_scope = [m for m in modules
                if any(is_under(m.name, r) for r in roots)]
    built = {d for m in in_scope for d in m.decls}

    # I-02 -- the vacuous pass. Empty emission over a non-empty build.
    if not names and built:
        add("I-02", "the emission is not empty over a non-empty build",
            [Finding("I-02", "constants[]",
                     f"empty, while {len(in_scope)} in-scope module(s) carry "
                     f"{len(built)} declaration(s). This is a mis-scoped "
                     f"emitter, not a clean build")])
    elif not names and not built:
        # The bootstrap. Consistent, so it passes -- loudly.
        add("I-02", "the emission is not empty over a non-empty build", [],
            reason=f"BOTH are empty: {len(in_scope)} in-scope module(s) carry no "
                   f"declarations and neither does the emission. Consistent, and "
                   f"this is what a repository's first build looks like -- but "
                   f"nothing has been checked because there is nothing to check")
    else:
        add("I-02", "the emission is not empty over a non-empty build", [],
            reason=f"{len(names)} constant(s) over {len(built)} built declaration(s)")

    # I-03 -- the actual set-diff. Anything Lake built and the emitter did not
    # sweep is a bug, not a policy choice.
    missing = sorted(built - names)
    if missing:
        # A mis-scoped emitter misses hundreds at once, so the finding list is
        # capped -- but the cap is REPORTED. A truncated list that did not say
        # so would read as "12 missing" when 1,200 are.
        shown = missing[:MAX_LISTED]
        add("I-03", "every built in-scope declaration is in the emission",
            [Finding("I-03", d, "built by lake, absent from the emission")
             for d in shown],
            reason="" if len(shown) == len(missing) else
                   f"showing {len(shown)} of {len(missing)} missing declarations")
    else:
        add("I-03", "every built in-scope declaration is in the emission", [],
            reason=f"{len(built)} declaration(s) across {len(in_scope)} module(s)")

    # I-04 -- the emitter's own module scope agrees with what Lake built. This
    # is the rule that catches a scope bug rather than inheriting it.
    declared = set(emission.get("modules") or emission_modules)
    derived = {m.name for m in in_scope}
    only_built = sorted(derived - declared)
    add("I-04", "the emitter's module scope matches what lake built",
        [Finding("I-04", m, "built under the root library, but the emitter did "
                            "not consider it in scope")
         for m in only_built])

    # I-05 -- and nothing in the emission came from a module Lake did not build.
    # A stale emission checked in against a newer tree looks exactly like this.
    all_built_modules = {m.name for m in modules}
    add("I-05", "every emitted constant comes from a module that was built",
        [Finding("I-05", m, "constants were emitted from it, but no .ilean "
                            "exists -- the emission is stale, or was produced "
                            "against a different tree")
         for m in sorted(emission_modules - all_built_modules)])

    return results


def allowlist(modules: list[Module], permitted_prefixes: list[str] | None, *,
              root: str | list[str]) -> RuleResult:
    """`I-06` — every direct import of an in-scope module is on the allowlist.

    ## Why an allowlist, when `E-09` already has a denylist

    `closed_lanes.forbidden_module_prefixes` was presented as mechanising both
    `CLAUDE.md` §3 and §4. It mechanises §4 — *importing* geometry — and it
    does that with a **denylist over Mathlib**, which is unbounded: a Mathlib
    module the denylist author never heard of is permitted by default. Every
    new Mathlib release enlarges the set of things that silently pass.

    Inverting it bounds the problem. A topic repo knows what it is allowed to
    build on; everything else is refused by default, including modules that did
    not exist when the list was written. That is the whole argument, and it is
    the same one behind `additionalProperties: false` on every schema here.

    ## What is permitted implicitly

    A module's imports of its OWN library are always allowed. Requiring every
    topic to list its own root would make the common case noisy and the
    interesting case — a reach into someone else's tree — harder to see.

    ## Direct, not transitive

    Judged on `directImports`: what the file writes at the top. Transitively,
    almost anything reaches almost all of Mathlib, so a transitive allowlist is
    either empty or meaningless. What a module chose to import is the thing its
    author is answerable for.

    ## It reports `not_run` in two distinct situations, and never `pass`

    No configuration, or an `.ilean` that does not record `directImports` at
    all. In both, nothing was checked — and `Module.direct_imports is None`
    exists precisely so the second is distinguishable from a module that
    genuinely imports nothing.
    """
    title = "every direct import is on the allowlist"
    if permitted_prefixes is None:
        return RuleResult("I-06", title, Status.NOT_RUN,
                          reason="no permitted_module_prefixes configuration "
                                 "supplied (--closed-lanes)")
    # A topic may have several roots (see `roots_of`); a module's imports of
    # ANY of them are its own library's, not a reach outside it.
    roots = [root] if isinstance(root, str) else list(root)
    roots = [r for r in roots if r]
    in_scope = [m for m in modules if any(is_under(m.name, r) for r in roots)]
    blind = [m for m in in_scope if m.direct_imports is None]
    if blind:
        return RuleResult(
            "I-06", title, Status.NOT_RUN,
            reason=f"{len(blind)} of {len(in_scope)} in-scope .ilean file(s) do "
                   f"not record directImports (e.g. {blind[0].name}); the "
                   f"imports could not be read, which is not the same as their "
                   f"being permitted")

    # `"Mathlib.Algebra."` is the spelling `forbidden_module_prefixes` already
    # uses, and one config should not have two conventions. The trailing dot is
    # stripped here rather than required or forbidden: component-wise matching
    # splits on ".", so a trailing dot would produce an empty final component
    # that matches nothing, and every prefix in the existing style would
    # silently refuse everything. Stripping keeps the spelling AND the
    # component-wise semantics -- `Mathlib.Algebra` still does not match
    # `Mathlib.AlgebraOfSomething`, which is the whole point of not using
    # `str.startswith`.
    prefixes = [p.rstrip(".") for p in permitted_prefixes]

    findings: list[Finding] = []
    for module in sorted(in_scope):
        for imported in sorted(module.direct_imports or ()):
            if any(is_under(imported, r) for r in roots):
                continue
            if any(is_under(imported, prefix) for prefix in prefixes):
                continue
            findings.append(Finding(
                "I-06", module.name,
                f"imports {imported!r}, which is on no permitted prefix"))
    return RuleResult("I-06", title,
                      Status.FAIL if findings else Status.PASS, tuple(findings),
                      reason="" if findings else
                             f"{len(in_scope)} in-scope module(s) against "
                             f"{len(permitted_prefixes)} permitted prefix(es)")


class Coverage(NamedTuple):
    in_scope_modules: int
    built_declarations: int
    emitted_constants: int
    missing: int


def coverage(emission: dict, modules: list[Module], *,
             lib: str | list[str] | None = None) -> Coverage:
    roots = roots_of(emission, lib)
    in_scope = [m for m in modules
                if any(is_under(m.name, r) for r in roots)]
    built = {d for m in in_scope for d in m.decls}
    names = {c["name"] for c in emission["constants"]
             if c.get("scope", "topic") != "external"}
    return Coverage(
        in_scope_modules=len(in_scope),
        built_declarations=len(built),
        emitted_constants=len(names),
        missing=len(built - names),
    )
