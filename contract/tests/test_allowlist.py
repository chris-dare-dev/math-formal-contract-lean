"""`I-06`, the import allowlist — the §4 half of #165.

`closed_lanes.forbidden_module_prefixes` was presented as mechanising both
`CLAUDE.md` §3 and §4. It mechanises §4, with a **denylist over Mathlib**,
which is unbounded: a module its author never heard of is permitted by default,
and every Mathlib release enlarges the set that silently passes.

These tests pin the inversion, and one property matters more than the rest:
`test_a_module_nobody_anticipated_is_refused`. That is the case a denylist
cannot have, and it is the whole reason for the change.
"""

from __future__ import annotations

from pathlib import Path

from contract.mfc.ilean import Module, allowlist
from contract.mfc.rules import Status

ROOT = "Topic"


def _mod(name: str, imports: frozenset[str] | None) -> Module:
    return Module(name=name, path=Path(f"{name}.ilean"),
                  decls=frozenset({f"{name}.thing"}), direct_imports=imports)


PERMITTED = ["Mathlib.Algebra.", "Mathlib.CategoryTheory."]


def test_a_permitted_import_passes() -> None:
    mods = [_mod("Topic.Basic", frozenset({"Mathlib.Algebra.Group.Defs"}))]
    assert allowlist(mods, PERMITTED, root=ROOT).status is Status.PASS


def test_a_module_nobody_anticipated_is_refused() -> None:
    """The case a denylist structurally cannot have.

    `Mathlib.AlgebraicGeometry.Sheaf` is on no denylist written before it
    existed, and is refused here without anyone having to have heard of it.
    """
    mods = [_mod("Topic.Basic", frozenset({"Mathlib.NotYetInvented.Sheaf"}))]
    result = allowlist(mods, PERMITTED, root=ROOT)
    assert result.status is Status.FAIL
    assert "Mathlib.NotYetInvented.Sheaf" in result.findings[0].detail


def test_the_topics_own_modules_are_permitted_implicitly() -> None:
    """Requiring every topic to allowlist itself would make the common case
    noisy and the interesting case harder to see."""
    mods = [_mod("Topic.Basic", frozenset({"Topic.Prelude"}))]
    assert allowlist(mods, PERMITTED, root=ROOT).status is Status.PASS


def test_prefixes_match_component_wise_not_as_strings() -> None:
    """`TopicExtra` is not under `Topic`, though the string says otherwise."""
    mods = [_mod("Topic.Basic", frozenset({"TopicExtra.Sneaky"}))]
    assert allowlist(mods, PERMITTED, root=ROOT).status is Status.FAIL

    mods = [_mod("Topic.Basic", frozenset({"Mathlib.AlgebraOfSomething.X"}))]
    assert allowlist(mods, PERMITTED, root=ROOT).status is Status.FAIL, \
        "'Mathlib.Algebra.' must not match 'Mathlib.AlgebraOfSomething.'"


def test_out_of_scope_modules_are_not_judged() -> None:
    """A repo builds its dependencies too; only the topic's own tree is ours."""
    mods = [_mod("Other.Thing", frozenset({"Whatever.At.All"}))]
    assert allowlist(mods, PERMITTED, root=ROOT).status is Status.PASS


# --------------------------------------------------------------------------
# The two ways it must report not_run rather than pass.
# --------------------------------------------------------------------------

def test_no_configuration_is_not_run() -> None:
    mods = [_mod("Topic.Basic", frozenset({"Anything.At.All"}))]
    result = allowlist(mods, None, root=ROOT)
    assert result.status is Status.NOT_RUN and result.reason


def test_an_ilean_without_directimports_is_not_run() -> None:
    """`None` means the key was absent. It is not `frozenset()`.

    Collapsing the two would report a clean sweep over files whose imports
    could not be read — the vacuous pass in its purest form.
    """
    mods = [_mod("Topic.Basic", None)]
    result = allowlist(mods, PERMITTED, root=ROOT)
    assert result.status is Status.NOT_RUN
    assert "directImports" in result.reason


def test_a_module_that_genuinely_imports_nothing_is_checked_and_passes() -> None:
    """The other side of the same distinction: empty is a real answer."""
    mods = [_mod("Topic.Basic", frozenset())]
    assert allowlist(mods, PERMITTED, root=ROOT).status is Status.PASS


def test_one_blind_module_suppresses_the_whole_rule() -> None:
    """Partial evidence is not a partial pass.

    A run that judged the readable modules and stayed silent about the rest
    would print `ok I-06` while having seen an unknown fraction of the tree.
    """
    mods = [_mod("Topic.A", frozenset({"Mathlib.Algebra.X"})),
            _mod("Topic.B", None)]
    assert allowlist(mods, PERMITTED, root=ROOT).status is Status.NOT_RUN


def test_an_empty_allowlist_refuses_everything_external() -> None:
    """`[]` is a configuration, not a missing one: it says 'nothing outside
    this repo'. Only `None` means unconfigured."""
    mods = [_mod("Topic.Basic", frozenset({"Mathlib.Algebra.X"}))]
    assert allowlist(mods, [], root=ROOT).status is Status.FAIL
    mods = [_mod("Topic.Basic", frozenset({"Topic.Other"}))]
    assert allowlist(mods, [], root=ROOT).status is Status.PASS


def test_findings_name_the_module_and_the_import() -> None:
    """A finding that does not say where is a finding nobody can act on."""
    mods = [_mod("Topic.Basic", frozenset({"Bad.One", "Bad.Two"}))]
    result = allowlist(mods, PERMITTED, root=ROOT)
    assert len(result.findings) == 2
    assert all(f.where == "Topic.Basic" for f in result.findings)
    assert [f.detail for f in result.findings] == sorted(f.detail for f in result.findings), \
        "sorted, so the report is comparable run to run"
