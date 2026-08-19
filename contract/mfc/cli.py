"""The `mfc` command line.

Subcommands land as the contract does. Today: `lint-schemas`, `validate`,
`bundle`, `lint`, `conformance`.

`lint` and `conformance` share `_report`, so a rule table means the same thing
in both: `ok` / `FAIL` / `not_run`, with the `not_run` names printed every time
and never folded into the count.

Exit codes are the contract with CI and are fixed:

* ``0`` — clean
* ``1`` — findings (the check ran and something failed)
* ``2`` — usage or environment error (the check did not run)

The distinction between 1 and 2 is load-bearing. A CI job that treats "the
linter crashed" as "the linter passed" is the vacuous pass this whole project
exists to make impossible.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import bootstrap as bootstrap_flag
from .build import BuildError, parse_checker
from .build import build as build_document
from .bundle import BundleError, build_declarations, dumps
from .conformance import check as conformance_check
from .conformance import evidence_table, gather, short_type
from .digest import file_digest
from .freshness import DEFAULT_MAX_AGE_DAYS, FreshnessError
from .freshness import check as freshness_check
from .restate import RestateError
from .restate import carried_forward
from .restate import check as restate_check
from .withdrawals import WithdrawalsError
from .withdrawals import check as withdrawals_check
from .env import EnvError
from .env import build as env_build
from .ilean import DEFAULT_BUILD_DIR, IleanError
from .ilean import allowlist as ilean_allowlist
from .ilean import roots_of as ilean_roots
from .ilean import check as ilean_check
from .ilean import coverage as ilean_coverage
from .ilean import load_modules
from .join import check as join_check
from .join import claim_table, coverage, workqueue
from .registry import RegistryShapeError
from .rules_registry import check as registry_check
from .rules_registry import external_decls as registry_external_decls
from .rules_registry import interface_ratio
from .rules_registry import mint_registry_id
from .scaffold import (
    NEXT_STEPS,
    Answers,
    ScaffoldError,
    lib_from_topic,
    render,
    write,
)
from .seal import SealError, parse_provisional
from .seal import seal as seal_bundle
from .lint import (
    FORBIDDEN_PROPERTY_NAMES,
    VOLATILE_PROPERTY_NAMES,
    lint_schema,
    lint_volatile,
)
from .rules import Status, check, summarize
from .validate import (
    LoadError,
    SCHEMA_DIR,
    load_artifact,
    schema_path_for,
    validate_artifact,
)

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

#: Schemas are found here when `--schema-dir` is not given. They live inside
#: the package so an installed `mfc` can find them, which is what ADR-0009's
#: "mfc is a shared tool again" requires.
DEFAULT_SCHEMA_DIR = SCHEMA_DIR


def _load(path: Path) -> tuple[dict | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    except OSError as exc:
        return None, f"unreadable: {exc}"


def cmd_lint_schemas(args: argparse.Namespace) -> int:
    schema_dir = Path(args.schema_dir) if args.schema_dir else DEFAULT_SCHEMA_DIR
    if not schema_dir.is_dir():
        print(f"error: no such schema directory: {schema_dir}", file=sys.stderr)
        return EXIT_USAGE

    paths = sorted(schema_dir.glob("*.schema.json"))
    if not paths:
        # An empty sweep is not a pass. A mis-pointed --schema-dir must not
        # look identical to a clean run.
        print(f"error: no *.schema.json files in {schema_dir}", file=sys.stderr)
        return EXIT_USAGE

    findings = 0
    volatile = 0
    unreadable = 0
    for path in paths:
        document, problem = _load(path)
        if problem is not None:
            print(f"error: {path.name}: {problem}", file=sys.stderr)
            unreadable += 1
            continue
        for finding in lint_schema(document):
            print(f"{path.name}: {finding}", file=sys.stderr)
            findings += 1
        for finding in lint_volatile(document, path.name):
            print(f"{path.name}: {finding.path}: declares {finding.name!r}, which "
                  f"changes every run", file=sys.stderr)
            volatile += 1

    if unreadable:
        return EXIT_USAGE
    if findings:
        print(
            f"error: {findings} forbidden property name(s) across "
            f"{len(paths)} schema(s). arXMCP CLAUDE.md 4.9 forbids any single "
            f"token that collapses distinct trust questions; the banned set is "
            f"{sorted(FORBIDDEN_PROPERTY_NAMES)}.",
            file=sys.stderr,
        )
    if volatile:
        print(
            f"error: {volatile} volatile property name(s) in an artifact CI both "
            f"regenerates and commits. `git diff --exit-code attest/` would then "
            f"be red on every no-op commit, and a gate that is red on no-op "
            f"commits gets deleted. Move the field to run/1.0, which is not "
            f"committed; the volatile set is {sorted(VOLATILE_PROPERTY_NAMES)}.",
            file=sys.stderr,
        )
    if findings or volatile:
        return EXIT_FINDINGS

    print(f"ok: {len(paths)} schema(s), no forbidden or volatile property names")
    return EXIT_OK


def _bootstrap_state(args: argparse.Namespace) -> tuple[Any, int | None]:
    """Read the bootstrap flag, or explain why the answer is unavailable.

    A record that cannot be read is EXIT_USAGE, never a silent "off". The flag
    decides whether an empty emission validates, so guessing at it would be
    guessing at the vacuous-pass guard.
    """
    record = getattr(args, "record", None)
    try:
        return bootstrap_flag.read(Path(record) if record else None), None
    except bootstrap_flag.RecordError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None, EXIT_USAGE
    except (LoadError, OSError) as exc:
        print(f"error: {record or bootstrap_flag.DEFAULT_RECORD}: {exc}", file=sys.stderr)
        return None, EXIT_USAGE


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.artifact)
    if not path.is_file():
        print(f"error: no such artifact: {path}", file=sys.stderr)
        return EXIT_USAGE

    try:
        document = load_artifact(path)
    except LoadError as exc:
        print(f"error: {path.name}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except OSError as exc:
        print(f"error: {path.name}: unreadable: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.schema:
        schema_path = Path(args.schema)
        declared = None
    else:
        if not isinstance(document, dict) or "schema_version" not in document:
            print(
                f"error: {path.name}: no top-level schema_version, so the schema "
                f"cannot be inferred. Every contract artifact carries one; pass "
                f"--schema only to debug.",
                file=sys.stderr,
            )
            return EXIT_USAGE
        declared = document["schema_version"]
        if not isinstance(declared, str):
            print(f"error: {path.name}: schema_version is not a string", file=sys.stderr)
            return EXIT_USAGE
        try:
            schema_path = schema_path_for(declared)
        except LoadError as exc:
            print(f"error: {path.name}: {exc}", file=sys.stderr)
            return EXIT_USAGE

    if not schema_path.is_file():
        # A version this build does not carry is a HARD failure. There is no
        # tolerant mode: silently skipping would let an artifact from a future
        # contract version pass unchecked.
        which = f"declared schema_version {declared!r}" if declared else str(schema_path)
        print(
            f"error: {path.name}: unsupported contract version -- {which} maps to "
            f"{schema_path.name}, which this build does not carry.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    schema_doc, problem = _load(schema_path)
    if problem is not None:
        print(f"error: {schema_path.name}: {problem}", file=sys.stderr)
        return EXIT_USAGE

    if (declared == "emission/1.0" and isinstance(document, dict)
            and bootstrap_flag.is_empty(document)):
        state, rc = _bootstrap_state(args)
        if rc is not None:
            return rc
        if state.active:
            schema_doc = bootstrap_flag.relax(schema_doc)
            print(f"note: {state.path.name} sets bootstrap: true, so an empty "
                  f"emission is accepted here. Nothing about this repository has "
                  f"been checked -- run `mfc lint` for the rule table, where every "
                  f"rule reports not_run.", file=sys.stderr)

    try:
        problems = validate_artifact(document, schema_doc)
    except LoadError as exc:
        # The check did not run. Exit 2, never 1 -- see validate_artifact.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if problems:
        for p in problems:
            print(f"{path.name}: {p}", file=sys.stderr)
        print(
            f"error: {path.name}: {len(problems)} problem(s) against {schema_path.name}",
            file=sys.stderr,
        )
        return EXIT_FINDINGS

    print(f"ok: {path.name} validates against {schema_path.name}")
    return EXIT_OK


def _validate_against(document: object, version: str, label: str,
                      *, bootstrap: bool = False) -> int | None:
    """Validate `document` against the schema `version` names. None if clean."""
    try:
        schema_path = schema_path_for(version)
    except LoadError as exc:
        print(f"error: {label}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if not schema_path.is_file():
        print(f"error: {label}: unsupported contract version {version!r}", file=sys.stderr)
        return EXIT_USAGE
    schema_doc, problem = _load(schema_path)
    if problem is not None:
        print(f"error: {schema_path.name}: {problem}", file=sys.stderr)
        return EXIT_USAGE
    if bootstrap:
        schema_doc = bootstrap_flag.relax(schema_doc)
    try:
        problems = validate_artifact(document, schema_doc)
    except LoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if problems:
        for pr in problems:
            print(f"{label}: {pr}", file=sys.stderr)
        print(f"error: {label}: {len(problems)} problem(s) against {schema_path.name}",
              file=sys.stderr)
        return EXIT_FINDINGS
    return None


def cmd_bundle(args: argparse.Namespace) -> int:
    emission_path, env_path = Path(args.emission), Path(args.environment)
    for p in (emission_path, env_path):
        if not p.is_file():
            print(f"error: no such file: {p}", file=sys.stderr)
            return EXIT_USAGE

    try:
        emission = load_artifact(emission_path)
        environment = load_artifact(env_path)
    except (LoadError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    state, rc = _bootstrap_state(args)
    if rc is not None:
        return rc
    # Bundling an empty emission is not vacuous the way linting one is: the
    # output honestly reports zero declarations, and `declarations/1.0` already
    # permits that (its counts carry `minimum: 0`). Only the emission's own
    # vacuity constraints are relaxed, and only while the flag is set.
    bootstrap_active = (state.active and isinstance(emission, dict)
                        and bootstrap_flag.is_empty(emission))

    # Validate the INPUTS before deriving anything from them. Recomputing over
    # a malformed emission would produce a well-formed declarations.json built
    # on nonsense, which is worse than failing.
    for doc, want, label in ((emission, "emission/1.0", emission_path.name),
                             (environment, "environment/1.0", env_path.name)):
        if not isinstance(doc, dict) or doc.get("schema_version") != want:
            print(f"error: {label}: expected schema_version {want!r}, got "
                  f"{doc.get('schema_version') if isinstance(doc, dict) else type(doc).__name__!r}",
                  file=sys.stderr)
            return EXIT_USAGE
        rc = _validate_against(doc, want, label,
                               bootstrap=bootstrap_active and want == "emission/1.0")
        if rc is not None:
            return rc

    try:
        declarations = build_declarations(emission, environment, emission_path)
    except BundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    # ...and validate the OUTPUT before writing it. mfc must not be able to
    # emit an artifact that its own schema rejects.
    rc = _validate_against(declarations, "declarations/1.0", "declarations.json")
    if rc is not None:
        return rc

    out = Path(args.out)
    if out.parent and not out.parent.is_dir():
        out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps(declarations), encoding="utf-8")

    c = declarations["counts"]
    sorries = sum(1 for d in declarations["declarations"] if d["contains_sorry_ax"])
    disallowed = sum(1 for d in declarations["declarations"] if d["axioms_disallowed"])
    print(f"ok: wrote {out} -- {c['total']} declarations "
          f"({c['in_scope']} in scope, {c['cited']} cited)")
    if sorries or disallowed:
        # Reported, not fatal. The artifact must exist and be honest even when
        # -- especially when -- the answer is bad; `mfc lint` is the gate.
        print(f"note: {sorries} declaration(s) depend on sorryAx, "
              f"{disallowed} carry a disallowed axiom", file=sys.stderr)
    return EXIT_OK


def cmd_build(args: argparse.Namespace) -> int:
    ndjson_path, env_path = Path(args.ndjson), Path(args.environment)
    emission_path = Path(args.emission)
    for p in (ndjson_path, env_path, emission_path):
        if not p.is_file():
            print(f"error: no such file: {p}", file=sys.stderr)
            return EXIT_USAGE

    try:
        environment = load_artifact(env_path)
        emission = load_artifact(emission_path)
        ndjson = ndjson_path.read_text(encoding="utf-8")
    except (LoadError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    # Validate the INPUT first. A build.json stamped with an env_digest read
    # out of a malformed environment record would be well formed and wrong.
    if not isinstance(environment, dict) or \
            environment.get("schema_version") != "environment/1.0":
        print(f"error: {env_path.name}: expected schema_version "
              f"'environment/1.0'", file=sys.stderr)
        return EXIT_USAGE
    rc = _validate_against(environment, "environment/1.0", env_path.name)
    if rc is not None:
        return rc

    try:
        checkers = [parse_checker(spec) for spec in (args.checker or [])]
        document = build_document(
            ndjson,
            environment=environment,
            emission=emission,
            lake_build_exit=args.lake_exit,
            lake_build_jobs=args.lake_jobs,
            covers=args.covers,
            covers_all=args.covers_all,
            independent_checkers=checkers,
        )
    except BuildError as exc:
        # Exit 2, never 1. "The build log could not be read" must not be
        # reportable as "the build has findings".
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    rc = _validate_against(document, "build/1.0", Path(args.out).name)
    if rc is not None:
        return rc

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps(document), encoding="utf-8")

    print(f"wrote {out}")
    print(f"  env_digest    {document['env_digest']}")
    print(f"  diagnostics   {len(document['diagnostics'])} "
          f"({document['error_count']} error(s), "
          f"{document['warning_count']} warning(s))")
    print(f"  lake exit     {document['lake_build_exit']} "
          f"over {document['lake_build_jobs']} job(s)  [RECORDED, NOT THE GATE]")

    m = document["measured"]
    covered, in_scope = len(m["modules"]), m["in_scope_modules"]
    if covered < in_scope:
        # Said every run, and on stderr. Zero diagnostics over a fraction of the
        # repository is the most flattering artifact this command can write, and
        # the only thing standing between that and a reader is this line.
        print(f"  PARTIAL       diagnostics cover {covered} of {in_scope} "
              f"in-scope module(s); the counts below describe those and no "
              f"others", file=sys.stderr)
    else:
        print(f"  measured      all {in_scope} in-scope module(s)")

    sorries = document["sorry_diagnostic_count"]
    if sorries:
        # Said on stderr because the exit code above may well be 0: that is the
        # whole reason this field exists rather than being inferred.
        print(f"  SORRY         {sorries} diagnostic(s) report a sorry, while "
              f"lake exited {document['lake_build_exit']}", file=sys.stderr)
    if not document["independent_checkers"]:
        print("  no independent checkers recorded; the array is empty rather "
              "than absent, which reads as 'none was run' and not as 'none was "
              "needed'")
    return EXIT_OK


def cmd_lint(args: argparse.Namespace) -> int:
    emission_path, env_path = Path(args.emission), Path(args.environment)
    for p in (emission_path, env_path):
        if not p.is_file():
            print(f"error: no such file: {p}", file=sys.stderr)
            return EXIT_USAGE
    try:
        emission = load_artifact(emission_path)
        environment = load_artifact(env_path)
        registry = load_artifact(Path(args.registry)) if args.registry else None
        lanes_doc = load_artifact(Path(args.closed_lanes)) if args.closed_lanes else None
    except (LoadError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    state, rc = _bootstrap_state(args)
    if rc is not None:
        return rc

    # The flag burns down here, and only here. `mfc lint` is the one command
    # that reads a whole emission and judges it, so it is the one place that
    # can see the transition from "no declarations yet" to "declarations", and
    # it records that transition rather than letting the flag outlive the
    # condition it describes. The rewritten record is left in the working tree
    # uncommitted, which is exactly what CI's `git diff --exit-code` reports:
    # clearing the flag is a commit an operator makes, not a fact CI invents.
    empty = isinstance(emission, dict) and bootstrap_flag.is_empty(emission)
    started = isinstance(emission, dict) and bootstrap_flag.has_declarations(emission)
    if state.active and started:
        try:
            at = bootstrap_flag.clear(state.path)
        except (bootstrap_flag.RecordError, OSError) as exc:
            print(f"error: {state.path}: bootstrap must be cleared now that this "
                  f"repository has declarations, and it could not be: {exc}",
                  file=sys.stderr)
            return EXIT_USAGE
        print(f"note: {state.path.name}: this repository now has declarations, so "
              f"bootstrap was cleared and dated {at}. Commit that change -- the "
              f"flag is write-once and may never be set again.", file=sys.stderr)
        state = state._replace(active=False, cleared_at=at)

    bootstrap_active = state.active and empty
    for doc, want, label in ((emission, "emission/1.0", emission_path.name),
                             (environment, "environment/1.0", env_path.name)):
        rc = _validate_against(doc, want, label,
                               bootstrap=bootstrap_active and want == "emission/1.0")
        if rc is not None:
            return rc

    closed_lanes = lanes_doc.get("closed_lanes") if isinstance(lanes_doc, dict) else None
    results = check(emission, environment, registry=registry,
                    closed_lanes=closed_lanes, bootstrap=bootstrap_active)
    return _report(results, args.require_all)


def _report(results: list, require_all: bool) -> int:
    """Print a rule table and derive the exit code. Shared by lint and conformance.

    The `not_run` note is printed on every invocation and never folded into the
    count, in both commands, because the misreading it guards against is the
    same one: a green run with a rule that never executed.
    """
    for r in results:
        mark = {Status.PASS: "ok", Status.FAIL: "FAIL", Status.NOT_RUN: "not_run"}[r.status]
        print(f"{mark:>7}  {r.rule}  {r.title}")
        for f in r.findings:
            print(f"         {f.where}: {f.detail}", file=sys.stderr)
        if r.reason:
            print(f"         ({r.reason})")

    passed, failed, not_run = summarize(results)
    print(f"\n{passed} passed, {failed} failed, {not_run} not_run")

    if not_run:
        names = ", ".join(r.rule for r in results if r.status is Status.NOT_RUN)
        print(f"note: {names} did NOT run. This is not a pass -- nothing checked "
              f"what they check.", file=sys.stderr)
        if require_all:
            print("error: --require-all and at least one rule did not run",
                  file=sys.stderr)
            return EXIT_FINDINGS
    return EXIT_FINDINGS if failed else EXIT_OK


def cmd_conformance(args: argparse.Namespace) -> int:
    bundle_path = Path(args.bundle)
    if not bundle_path.is_file():
        print(f"error: no such bundle: {bundle_path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        bundle = load_artifact(bundle_path)
    except (LoadError, OSError) as exc:
        print(f"error: {bundle_path.name}: {exc}", file=sys.stderr)
        return EXIT_USAGE

    # The bundle is both the input and the map. A malformed one cannot be
    # walked, so it is checked against its own schema before anything else.
    rc = _validate_against(bundle, "bundle/1.0", bundle_path.name)
    if rc is not None:
        return rc

    # Predicate paths are repo-relative, so the default root is the directory
    # the bundle's own directory sits in -- `attest/bundle.json` naming
    # `attest/build.json` resolves without the caller passing anything.
    root = Path(args.root) if args.root else bundle_path.resolve().parent.parent
    if not root.is_dir():
        print(f"error: no such root directory: {root}", file=sys.stderr)
        return EXIT_USAGE

    evidence = gather(bundle, root)
    if not evidence:
        # A bundle attesting nothing must not pass. Same vacuous-pass guard as
        # an empty --schema-dir.
        print("error: the bundle carries no predicates; there is nothing to "
              "check and this is not a clean run", file=sys.stderr)
        return EXIT_USAGE

    # #646: restate evidence lets C-10 carry a review across an environment
    # bump instead of failing it. Validated like every other input, because a
    # malformed restate document deciding that a review survives would be worse
    # than no restate document at all.
    restate_doc = None
    if getattr(args, "restate", None):
        restate_path = Path(args.restate)
        if not restate_path.is_file():
            print(f"error: no such restate report: {restate_path}", file=sys.stderr)
            return EXIT_USAGE
        try:
            restate_doc = load_artifact(restate_path)
        except (LoadError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        rc = _validate_against(restate_doc, "restate/1.0", restate_path.name)
        if rc is not None:
            return rc

    results = conformance_check(
        bundle, evidence,
        restate=restate_doc,
        emission_path=Path(args.emission) if args.emission else None)
    rc = _report(results, args.require_all)

    rows = evidence_table(bundle, evidence)
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    print("\nevidence")
    for r in rows:
        print("  " + "  ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip())
    independent = sum(1 for r in rows if r.attestation == "independent")
    elsewhere = sum(1 for r in rows if r.environment.startswith("OTHER"))
    # Reported as separate facts, never combined into one number. "6 predicates"
    # must not be readable as "6 measurements of this build by six parties".
    print(f"\n{len(rows)} predicate(s); {independent} not self-attested; "
          f"{elsewhere} produced in another environment")
    if bundle.get("unrecognized_predicates"):
        kinds = ", ".join(sorted({p["predicateType"] for p
                                  in bundle["unrecognized_predicates"]}))
        print(f"note: {len(bundle['unrecognized_predicates'])} predicate(s) were "
              f"ingested but not understood by this build ({kinds}). Their "
              f"contents were NOT checked.", file=sys.stderr)
    return rc


def cmd_join(args: argparse.Namespace) -> int:
    paths = {"declarations": Path(args.declarations)}
    for name in ("review", "resolution", "registry", "environment",
                 "withdrawals"):
        if getattr(args, name):
            paths[name] = Path(getattr(args, name))
    for label, p in paths.items():
        if not p.is_file():
            print(f"error: no such {label}: {p}", file=sys.stderr)
            return EXIT_USAGE

    docs: dict[str, Any] = {}
    try:
        for label, p in paths.items():
            docs[label] = load_artifact(p)
    except (LoadError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    # Validate every input that has a schema. The registry has none yet, which
    # is the same fact J-06 reports -- so it is passed through unvalidated and
    # said so, rather than silently trusted.
    for label, version in (("declarations", "declarations/1.0"),
                           ("review", "review/1.0"),
                           ("resolution", "resolution/1.0"),
                           ("registry", "registry/1.0"),
                           ("environment", "environment/1.0"),
                           ("withdrawals", "withdrawals/1.0")):
        if label in docs:
            rc = _validate_against(docs[label], version, paths[label].name)
            if rc is not None:
                return rc

    results = join_check(docs["declarations"], review=docs.get("review"),
                         resolution=docs.get("resolution"),
                         registry=docs.get("registry"))
    rc = _report(results, args.require_all)

    if args.workqueue_out:
        if "registry" not in docs:
            # Refused, not written empty. A queue with no lanes and a queue
            # nobody computed are different files, and only one of them means
            # there is nothing owed.
            print("error: --workqueue-out needs --registry; a queue cannot be "
                  "computed from citations alone, and writing an empty one "
                  "would read as 'nothing is owed'", file=sys.stderr)
            return EXIT_USAGE
        queue = workqueue(
            docs["declarations"], docs["registry"],
            registry_sha256=file_digest(paths["registry"]),
            declarations_sha256=file_digest(paths["declarations"]))
        out = Path(args.workqueue_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(queue, indent=2) + "\n", encoding="utf-8")
        lanes = ", ".join(f"{k}: {v['count']}"
                          for k, v in sorted(queue["lanes"].items()))
        print(f"\nwrote {out} -- {lanes or 'every entry is cited; no lanes'}")

    rows = claim_table(docs["declarations"], review=docs.get("review"),
                       withdrawals=docs.get("withdrawals"),
                       resolution=docs.get("resolution"),
                       environment=docs.get("environment"))
    if "review" in docs and "environment" not in docs:
        # Not a finding -- the table already says not_run in every review cell.
        # This says WHY, because "nobody reviewed" and "I was not told which
        # environment to judge the reviews against" are different problems and
        # the cell spells them the same.
        print("\nnote: --review supplied without --environment, so no review "
              "verdict can be shown to be about this build; review columns "
              "read not_run", file=sys.stderr)
    if rows:
        header = ("key", "decl", "claimed", "faithful", "confirmed", "resolved",
                  "frontier")
        table = [header, *rows]
        widths = [max(len(str(r[i])) for r in table) for i in range(len(header))]
        print("\nclaims")
        for r in table:
            print("  " + "  ".join(str(c).ljust(w)
                                   for c, w in zip(r, widths)).rstrip())
        c = coverage(rows)
        # Separate facts. Never a ratio and never one number: "reviewed" and
        # "resolved" are different questions about different rows, and
        # not_applicable is a third that must not be folded into either.
        na = (f"; {c.review_not_applicable} reviewed against another environment"
              if c.review_not_applicable else "")
        print(f"\n{c.bindings} binding(s) over {c.keys} key(s); "
              f"{c.reviewed} reviewed; {c.resolved} resolved; "
              f"{c.frontier_open} with an open frontier{na}")
    else:
        # Zero bindings is not a clean join. It is what a declarations.json
        # built from a mis-scoped emission looks like.
        print("\nno citation bindings: nothing to join", file=sys.stderr)
    return rc


def _mfc_version() -> str:
    """This package's version, reported as unknown rather than guessed.

    A hard-coded fallback would put a version string into a trust record that
    no installed artifact carries.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415
    except ImportError:  # pragma: no cover - stdlib since 3.8
        return "unknown"
    try:
        return version("mfc")
    except PackageNotFoundError:
        return "unknown"


def cmd_env(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    if not repo.is_dir():
        print(f"error: no such repository: {repo}", file=sys.stderr)
        return EXIT_USAGE

    allowlist = [a.strip() for a in args.axiom_allowlist.split(",") if a.strip()]
    if not allowlist:
        print("error: --axiom-allowlist is empty; an empty allowlist permits "
              "nothing and is never what a caller means", file=sys.stderr)
        return EXIT_USAGE

    try:
        doc = env_build(
            repo,
            allowlist=allowlist,
            contract_package=args.contract_package,
            lean_githash=args.lean_githash,
            lake_version=args.lake_version,
            mfc_version=_mfc_version(),
            emitter_version=args.emitter_version,
        )
    except EnvError as exc:
        # Exit 2, never 1. "The environment could not be read" must not be
        # reportable as "the environment has findings".
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    rc = _validate_against(doc, "environment/1.0", Path(args.out).name)
    if rc is not None:
        return rc

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"  env_digest    {doc['env_digest']}")
    print(f"  packages      {len(doc['packages'])}")

    # Both of these are drift the record is supposed to make visible rather
    # than carry silently, so they are said out loud on every run.
    if doc["input_rev_is_branch"]:
        print(f"  NOT PINNED    {len(doc['input_rev_is_branch'])} package(s) "
              f"track a branch and would move on `lake update`: "
              f"{', '.join(doc['input_rev_is_branch'])}")
    if doc["root_package"]["worktree_dirty"]:
        print("  DIRTY         the worktree has uncommitted changes, so this "
              "digest describes a tree that is not in git", file=sys.stderr)
    if doc["root_package"]["tag"] is None:
        print("  UNTAGGED      valid artifact, invalid release -- a release pin "
              "requires a tag")
    return EXIT_OK


def cmd_check_ilean_coverage(args: argparse.Namespace) -> int:
    emission_path = Path(args.emission)
    if not emission_path.is_file():
        print(f"error: no such emission: {emission_path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        emission = load_artifact(emission_path)
    except (LoadError, OSError) as exc:
        print(f"error: {emission_path.name}: {exc}", file=sys.stderr)
        return EXIT_USAGE

    rc = _validate_against(emission, "emission/1.0", emission_path.name)
    if rc is not None:
        return rc

    try:
        modules = load_modules(Path(args.build_dir) if args.build_dir
                               else DEFAULT_BUILD_DIR)
    except IleanError as exc:
        # The check did not run. Exit 2, never 1 -- "no .ilean files" must not
        # be reportable as "everything is covered".
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    # I-06 rides here rather than in `mfc lint` because its evidence is the
    # `.ilean` tree, not the emission -- the same reason every other I-rule is
    # in this command. `E-09`'s denylist and this allowlist are deliberately
    # both live: they answer different questions (§4 "did you import geometry"
    # versus §3 "are you building on anything you did not declare"), and a
    # repository that configures only one gets exactly one of them.
    permitted = None
    if args.closed_lanes:
        try:
            lanes_doc = load_artifact(Path(args.closed_lanes))
        except (LoadError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if isinstance(lanes_doc, dict):
            raw = lanes_doc.get("permitted_module_prefixes")
            if raw is not None and (not isinstance(raw, list)
                                    or not all(isinstance(x, str) for x in raw)):
                print("error: permitted_module_prefixes must be an array of "
                      "strings", file=sys.stderr)
                return EXIT_USAGE
            permitted = raw

    results = ilean_check(emission, modules, lib=args.lib)
    results.append(ilean_allowlist(
        modules, permitted, root=ilean_roots(emission, args.lib)))
    rc = _report(results, args.require_all)

    c = ilean_coverage(emission, modules, lib=args.lib)
    print(f"\n{c.in_scope_modules} in-scope module(s) of {len(modules)} built; "
          f"{c.built_declarations} built declaration(s); "
          f"{c.emitted_constants} emitted constant(s); {c.missing} missing")
    if c.built_declarations == 0:
        print("note: the in-scope modules carry NO declarations. Consistent with "
              "an empty emission, and this is what a repository's first build "
              "looks like -- but nothing was checked, because there is nothing "
              "to check.", file=sys.stderr)
    return rc


DEFAULT_CONTRACT_URL = "https://github.com/chris-dare-dev/math-formal-contract-lean"


def cmd_init(args: argparse.Namespace) -> int:
    answers = Answers(
        topic=args.topic,
        lib=args.lib or lib_from_topic(args.topic),
        toolchain=args.toolchain,
        mathlib_rev=args.mathlib_rev,
        contract_rev=args.contract_rev,
        contract_url=args.contract_url or DEFAULT_CONTRACT_URL,
        anchor_name=args.anchor_name,
        anchor_url=args.anchor_url,
        anchor_rev=args.anchor_rev,
    )
    try:
        files = render(answers)
    except ScaffoldError as exc:
        # Nothing has been written at this point, and nothing will be.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    dest = Path(args.dest)
    if args.dry_run:
        print(f"would write {len(files)} file(s) to {dest}:")
        for rel in sorted(files):
            print(f"  {rel}  ({len(files[rel])} bytes)")
        return EXIT_OK

    try:
        written = write(files, dest, force=args.force)
    except ScaffoldError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except OSError as exc:
        print(f"error: could not write to {dest}: {exc}", file=sys.stderr)
        return EXIT_USAGE

    print(f"ok: wrote {len(written)} file(s) to {dest}")
    for p in written:
        print(f"  {p.relative_to(dest)}")
    print()
    print(NEXT_STEPS.format(lib=answers.lib), file=sys.stderr)
    return EXIT_OK


def cmd_registry_init(args: argparse.Namespace) -> int:
    import secrets  # noqa: PLC0415 - only this one subcommand needs randomness

    print(mint_registry_id(secrets.randbits))
    print("\nMinted once, then committed. It is not derived from the notebook "
          "slug: slugs live in a machine-local, unauthenticated database with no "
          "global registry, so two adopters both choosing `number-theory` would "
          "collide silently.\n\nPut it in the registry's `registry_id`, and use "
          "it as the middle segment of every citation key:\n"
          "  stmt:<that value>:<your label>", file=sys.stderr)
    return EXIT_OK


def cmd_registry_validate(args: argparse.Namespace) -> int:
    path = Path(args.registry)
    if not path.is_file():
        print(f"error: no such registry: {path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        document = load_artifact(path)
    except (LoadError, OSError) as exc:
        print(f"error: {path.name}: {exc}", file=sys.stderr)
        return EXIT_USAGE

    rc = _validate_against(document, "registry/1.0", path.name)
    if rc is not None:
        return rc

    try:
        results = registry_check(
            document, frontier_kind_labels=args.frontier_kind_labels)
    except RegistryShapeError as exc:
        # Unreachable once the schema has passed, but a shape error must never
        # become a finding: it means nothing was checked.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    rc = _report(results, args.require_all)
    entries = document.get("entries", {})
    kinds: dict[str, int] = {}
    for entry in entries.values():
        kind = entry.get("kind", "unknown")
        kinds[kind] = kinds.get(kind, 0) + 1
    # Per kind, never totalled -- see mfc join's J-06 for why. A count of
    # entries is not a count of formalizable work.
    print(f"\n{path.name}: " +
          (", ".join(f"{k}: {n}" for k, n in sorted(kinds.items())) or "no entries"))

    # Reported, never thresholded, and never folded into the rule table: there
    # is no fraction of interfaces that is *wrong*, but a registry that is
    # nine-tenths interfaces relates almost nothing to anything outside this
    # repository, and that fact should not require reading the file to notice.
    interfaces, frontier_total = interface_ratio(entries)
    if frontier_total:
        pct = 100.0 * interfaces / frontier_total
        print(f"{path.name}: interface_ratio {interfaces}/{frontier_total} "
              f"({pct:.0f}%) of frontier items model something outside this "
              f"repo only by claim")
    return rc


def cmd_registry_external_decls(args: argparse.Namespace) -> int:
    """Write the emitter's `--externals` input: a plain JSON array of names.

    Deliberately a separate command rather than a flag on the emitter. The
    emitter is Lean, the registry is YAML-or-JSON, and teaching a Lean binary
    to parse a hand-authored registry would put a second reader of the contract
    in a second language -- which is the drift this package exists to avoid.
    `mfc` reads the registry, the emitter reads a list of strings.
    """
    path = Path(args.registry)
    if not path.is_file():
        print(f"error: no such registry: {path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        document = load_artifact(path)
    except (LoadError, OSError) as exc:
        print(f"error: {path.name}: {exc}", file=sys.stderr)
        return EXIT_USAGE

    rc = _validate_against(document, "registry/1.0", path.name)
    if rc is not None:
        return rc

    bindings = registry_external_decls(document.get("entries") or {})
    names = [b["name"] for b in bindings]
    payload = json.dumps(names, indent=2, sort_keys=False) + "\n"
    if args.out:
        out = Path(args.out)
        if out.parent and not out.parent.is_dir():
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
        print(f"ok: wrote {out} -- {len(names)} external binding(s)")
    else:
        print(payload, end="")
    for binding in bindings:
        print(f"  {binding['name']}  <- {', '.join(binding['cited_by'])}",
              file=sys.stderr)
    return EXIT_OK


def cmd_check_resolution(args: argparse.Namespace) -> int:
    path = Path(args.resolution)
    if not path.is_file():
        print(f"error: no such resolution: {path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        document = load_artifact(path)
    except (LoadError, OSError) as exc:
        print(f"error: {path.name}: {exc}", file=sys.stderr)
        return EXIT_USAGE

    rc = _validate_against(document, "resolution/1.0", path.name)
    if rc is not None:
        return rc

    registry_sha = None
    if args.registry:
        registry_path = Path(args.registry)
        if not registry_path.is_file():
            print(f"error: no such registry: {registry_path}", file=sys.stderr)
            return EXIT_USAGE
        # The bytes on disk, not a field anyone typed.
        registry_sha = file_digest(registry_path)

    try:
        results = freshness_check(
            document, registry_sha256=registry_sha,
            manifest_hash=args.manifest_hash, max_age_days=args.max_age_days)
    except FreshnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return _report(results, args.require_all)


def cmd_withdrawals_check(args: argparse.Namespace) -> int:
    path = Path(args.withdrawals)
    if not path.is_file():
        print(f"error: no such withdrawals file: {path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        document = load_artifact(path)
        registry = load_artifact(Path(args.registry)) if args.registry else None
        previous = load_artifact(Path(args.previous)) if args.previous else None
    except (LoadError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    rc = _validate_against(document, "withdrawals/1.0", path.name)
    if rc is not None:
        return rc

    try:
        results = withdrawals_check(document, registry=registry, previous=previous)
    except WithdrawalsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    rc = _report(results, args.require_all)
    n = len(document.get("withdrawals") or [])
    print(f"\n{path.name}: {n} withdrawal(s). This file may be read from a "
          f"NEWER tag than the one a consumer pins -- it is the only artifact "
          f"that may, because it can only remove trust.")
    return rc


def cmd_restate_check(args: argparse.Namespace) -> int:
    path = Path(args.restate)
    if not path.is_file():
        print(f"error: no such restate report: {path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        document = load_artifact(path)
        review = load_artifact(Path(args.review)) if args.review else None
    except (LoadError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    rc = _validate_against(document, "restate/1.0", path.name)
    if rc is not None:
        return rc
    if review is not None:
        rc = _validate_against(review, "review/1.0", Path(args.review).name)
        if rc is not None:
            return rc

    try:
        results = restate_check(document, review=review)
    except RestateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    rc = _report(results, args.require_all)

    c = document.get("counts", {})
    print(f"\n{path.name}: {c.get('restated', 0)} restated, "
          f"{c.get('changed', 0)} changed, "
          f"{c.get('not_checkable', 0)} not_checkable")
    # Never summed, and the reason is printed rather than assumed: a reader who
    # adds `changed` and `not_checkable` together has invented a number that
    # means "reviews I should distrust", which is not what either says.
    print(f"{path.name}: {len(carried_forward(document))} review(s) may be "
          f"carried forward. `changed` invalidates; `not_checkable` means "
          f"nobody knows, which is also not a pass.", file=sys.stderr)
    return rc


def cmd_seal(args: argparse.Namespace) -> int:
    root, env_path = Path(args.root), Path(args.environment)
    registry_path = Path(args.registry)
    if not root.is_dir():
        print(f"error: no such root: {root}", file=sys.stderr)
        return EXIT_USAGE
    for p in (env_path, registry_path):
        if not p.is_file():
            print(f"error: no such file: {p}", file=sys.stderr)
            return EXIT_USAGE

    try:
        environment = load_artifact(env_path)
    except (LoadError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    rc = _validate_against(environment, "environment/1.0", env_path.name)
    if rc is not None:
        return rc

    resolution = None
    if args.resolution:
        try:
            resolution = load_artifact(Path(args.resolution))
        except (LoadError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        rc = _validate_against(resolution, "resolution/1.0",
                               Path(args.resolution).name)
        if rc is not None:
            return rc

    try:
        document = seal_bundle(
            root=root,
            environment_path=env_path,
            environment=environment,
            registry_path=registry_path,
            declarations_path=Path(args.declarations) if args.declarations else None,
            build_path=Path(args.build) if args.build else None,
            review_path=Path(args.review) if args.review else None,
            review_produced_by=args.review_produced_by,
            resolution_path=Path(args.resolution) if args.resolution else None,
            resolution=resolution,
            provisional=[parse_provisional(s) for s in (args.provisional or [])],
            allow_dirty=args.allow_dirty,
        )
    except SealError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    rc = _validate_against(document, "bundle/1.0", Path(args.out).name)
    if rc is not None:
        return rc

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps(document), encoding="utf-8")

    subject = document["subject"][0]
    print(f"wrote {out}")
    print(f"  subject       {subject['name']} @ {subject['digest']['gitCommit'][:10]}")
    print(f"  env_digest    {document['env_digest']}")
    for pred in document["predicates"]:
        env = pred["env_digest"]
        label = "null" if env is None else env[:10]
        print(f"  {short_type(pred['predicateType']):<22} {pred['file']}  "
              f"env={label}  self_attested={str(pred['self_attested']).lower()}")

    if subject["digest"]["gitTag"] is None:
        # Same sentence `mfc env` uses, for the same reason: the bundle is a
        # valid artifact and the release it describes does not exist.
        print("  UNTAGGED      valid artifact, invalid release -- a release pin "
              "requires a tag")
    if args.allow_dirty and environment.get("root_package", {}).get("worktree_dirty"):
        print("  DIRTY         sealed over a worktree with uncommitted changes; "
              "this bundle is explicitly not a release", file=sys.stderr)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mfc",
        description="Tooling for the math-formalization contract.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    lint = sub.add_parser(
        "lint-schemas",
        help="reject schemas declaring a forbidden property name",
    )
    lint.add_argument(
        "--schema-dir",
        help=f"directory of *.schema.json (default: {DEFAULT_SCHEMA_DIR})",
    )
    lint.set_defaults(func=cmd_lint_schemas)

    val = sub.add_parser(
        "validate",
        help="validate an artifact against the schema it declares",
    )
    val.add_argument("artifact", help="path to a .json or .yaml contract artifact")
    val.add_argument(
        "--schema",
        help="override the schema path (debugging only -- the schema is "
             "normally inferred from the artifact's own schema_version)",
    )
    val.add_argument(
        "--record",
        help=f"path to formalization.yaml, read only for the bootstrap flag "
             f"(default: {bootstrap_flag.DEFAULT_RECORD})",
    )
    val.set_defaults(func=cmd_validate)

    bun = sub.add_parser(
        "bundle",
        help="build declarations.json from an emission and an environment",
        description="Recomputes every field rather than carrying it across "
                    "from the emission. Validates both inputs and the output.",
    )
    bun.add_argument("--emission", required=True, help="path to lean-emission.json")
    bun.add_argument("--environment", required=True, help="path to environment.json")
    bun.add_argument("--out", required=True, help="where to write declarations.json")
    bun.add_argument(
        "--record",
        help=f"path to formalization.yaml, read only for the bootstrap flag "
             f"(default: {bootstrap_flag.DEFAULT_RECORD})",
    )
    bun.set_defaults(func=cmd_bundle)

    bld = sub.add_parser(
        "build",
        help="turn `lake env lean --json` NDJSON into build/1.0",
        description="Records what the build reported. `lake_build_exit` is "
                    "RECORDED AND NEVER CONSULTED -- on this toolchain a build "
                    "whose only defect is a sorry exits 0 -- and the gate is "
                    "`sorry_diagnostic_count`, counted from records whose kind "
                    "is hasSorry. A message that reads like a sorry but is not "
                    "tagged as one is a hard failure, not an uncounted warning.",
    )
    bld.add_argument("--ndjson", required=True,
                     help="file of `lake env lean --json` output, one JSON "
                          "message per line")
    bld.add_argument("--environment", required=True, help="path to environment.json")
    bld.add_argument("--emission", required=True,
                     help="path to lean-emission.json; its modules[] is the "
                          "denominator for what this build measured, and it is "
                          "read from there rather than from the caller")
    covered = bld.add_mutually_exclusive_group(required=True)
    covered.add_argument("--covers", action="append", metavar="MODULE",
                         help="an in-scope module the NDJSON covers, repeatable. "
                              "Declared rather than observed: a module that "
                              "elaborated cleanly leaves no trace in the log")
    covered.add_argument("--covers-all", dest="covers_all", action="store_true",
                         help="the NDJSON is over every in-scope module. A "
                              "claim, spelled as its own flag so that making it "
                              "is deliberate")
    bld.add_argument("--lake-exit", dest="lake_exit", type=int, required=True,
                     help="the exit code lake actually returned; recorded, "
                          "never the gate")
    bld.add_argument("--lake-jobs", dest="lake_jobs", type=int, required=True,
                     help="jobs lake reported building")
    bld.add_argument("--checker", action="append", metavar="N:V:VALUE:ALLOW_SORRY",
                     help="an independent checker's verdict, repeatable, e.g. "
                          "leanchecker:bundled-4.32.1:pass:false")
    bld.add_argument("--out", required=True, help="where to write build.json")
    bld.set_defaults(func=cmd_build)

    lnt = sub.add_parser(
        "lint",
        help="run the E-01..E-10 content rules over an emission",
        description="Schema validation says an artifact is well FORMED; these "
                    "rules say whether what it contains is ALLOWED. A rule "
                    "whose input is missing reports not_run, never pass.",
    )
    lnt.add_argument("--emission", required=True)
    lnt.add_argument("--environment", required=True)
    lnt.add_argument("--registry", help="enables E-04 and the registry half of E-05")
    lnt.add_argument("--closed-lanes", dest="closed_lanes",
                     help="JSON with a closed_lanes[] array; enables E-09")
    lnt.add_argument("--require-all", dest="require_all", action="store_true",
                     help="treat any not_run rule as a failure")
    lnt.add_argument(
        "--record",
        help=f"path to formalization.yaml; carries the bootstrap flag, which "
             f"this command clears once the emission is non-empty "
             f"(default: {bootstrap_flag.DEFAULT_RECORD})",
    )
    lnt.set_defaults(func=cmd_lint)

    con = sub.add_parser(
        "conformance",
        help="run the C-01..C-12 rules over the set of artifacts a bundle names",
        description="Asks whether seven individually valid artifacts describe "
                    "the SAME thing. Writes no artifact: the output is a report "
                    "and an exit code, because a conformance.json with a verdict "
                    "in it would be the aggregate-verdict rejection fixture.",
    )
    con.add_argument("--bundle", required=True, help="path to bundle.json")
    con.add_argument("--root", help="directory predicate file[] paths are "
                                    "relative to (default: the bundle's parent's parent)")
    con.add_argument("--emission", help="path to lean-emission.json; enables C-11")
    con.add_argument("--restate", help="path to restate/1.0; lets C-10 carry a "
                                       "review across an environment bump when "
                                       "-- and only when -- the outcome is "
                                       "`restated`")
    con.add_argument("--require-all", dest="require_all", action="store_true",
                     help="treat any not_run rule as a failure")
    con.set_defaults(func=cmd_conformance)

    slp = sub.add_parser(
        "seal",
        help="assemble bundle/1.0, the in-toto Statement conformance reads",
        description="The producer for the file every conformance rule needs "
                    "and nothing wrote. REFUSES to omit a required predicate: "
                    "a bundle with no build/v1 satisfies eleven of twelve rules "
                    "while attesting nothing about the build, and a tool that "
                    "wrote it would be shipping the C-12 fixture as a product. "
                    "A dirty worktree is refused for the same reason C-09 "
                    "exists.",
    )
    slp.add_argument("--root", default=".",
                     help="directory every predicate file is named relative to "
                          "(default: .)")
    slp.add_argument("--environment", required=True, help="path to environment.json")
    slp.add_argument("--registry", required=True,
                     help="registry file; its bytes are the registry_sha256 the "
                          "corpus side is compared against by C-08")
    slp.add_argument("--declarations", help="path to declarations.json")
    slp.add_argument("--build", help="path to build.json")
    slp.add_argument("--review", help="path to review.yaml")
    slp.add_argument("--review-produced-by", dest="review_produced_by",
                     metavar="WHO",
                     help="who performed the review, e.g. 'human:Ada Lovelace'; "
                          "required with --review")
    slp.add_argument("--resolution", help="path to resolution.json (arXMCP's)")
    slp.add_argument("--provisional", action="append",
                     metavar="FILE:PRODUCED_BY:ENV_DIGEST",
                     help="evidence from ANOTHER environment, repeatable; the "
                          "env_digest is required because C-05 is what keeps it "
                          "visibly separate")
    slp.add_argument("--allow-dirty", dest="allow_dirty", action="store_true",
                     help="seal over uncommitted changes; the result is "
                          "explicitly not a release")
    slp.add_argument("--out", required=True, help="where to write bundle.json")
    slp.set_defaults(func=cmd_seal)

    joi = sub.add_parser(
        "join",
        help="join citations to reviews and corpus resolutions (J-01..J-06)",
        description="Builds the claim table: one row per (key, declaration) "
                    "binding, with the author's claim, the reviewer's finding "
                    "and the corpus resolution kept in SEPARATE columns. "
                    "A pure function of local JSON -- it reaches no corpus.",
    )
    joi.add_argument("--declarations", required=True, help="path to declarations.json")
    joi.add_argument("--review", help="path to review.yaml/json; enables J-01..J-03")
    joi.add_argument("--withdrawals",
                     help="withdrawals.yaml; retracted keys are marked in the "
                          "claim table and excluded from the reviewed count")
    joi.add_argument("--environment", help="path to environment.json; without it "
                                           "no review verdict can be shown to be "
                                           "about this build, so the review "
                                           "columns read not_run")
    joi.add_argument("--resolution", help="path to resolution.json; enables J-04, J-05")
    joi.add_argument("--registry", help="path to the registry; enables J-06 "
                                        "and --workqueue-out")
    joi.add_argument("--workqueue-out", dest="workqueue_out",
                     help="write workqueue/1.0 here: every registry entry with "
                          "zero inbound cites, by kind. Needs --registry")
    joi.add_argument("--require-all", dest="require_all", action="store_true",
                     help="treat any not_run rule as a failure")
    joi.set_defaults(func=cmd_join)

    envp = sub.add_parser(
        "env",
        help="read a topic repo's checkout and write environment/1.0",
        description="Produces the record every other check CONSUMES and "
                    "nothing produced. Observed from lean-toolchain, "
                    "lake-manifest.json, lakefile.toml and git -- not authored. "
                    "`axiom_policy` is required rather than defaulted, because "
                    "a policy is not a property of a build and this package "
                    "must not assert one on a topic repo's behalf.",
    )
    envp.add_argument("--repo", required=True, help="topic repository checkout")
    envp.add_argument("--out", required=True, help="where to write environment.json")
    envp.add_argument("--axiom-allowlist", dest="axiom_allowlist", required=True,
                      help="comma-separated, e.g. propext,Quot.sound,Classical.choice")
    envp.add_argument("--emitter-version", dest="emitter_version", required=True,
                      help="version of the Lean emitter that produced the "
                           "emission; this tool cannot observe it")
    envp.add_argument("--contract-package", dest="contract_package",
                      default="MathFormalContract",
                      help="manifest name of the contract package (default: "
                           "MathFormalContract)")
    envp.add_argument("--lean-githash", dest="lean_githash",
                      help="override `lean --githash`, for a caller with no "
                           "toolchain on PATH that measured it elsewhere")
    envp.add_argument("--lake-version", dest="lake_version",
                      help="override `lake --version`")
    envp.set_defaults(func=cmd_env)

    cov = sub.add_parser(
        "check-ilean-coverage",
        help="set-diff what lake built against what the emitter swept (I-01..I-05)",
        description="The vacuous-pass guard. Every other check reads the "
                    "emission and asks whether what it CONTAINS is allowed; "
                    "this asks whether anything is MISSING, using the .ilean "
                    "files lake writes -- the one description of what was built "
                    "that does not come from the emitter.",
    )
    cov.add_argument("--emission", required=True, help="path to lean-emission.json")
    cov.add_argument("--closed-lanes", dest="closed_lanes",
                     help="JSON carrying permitted_module_prefixes[]; enables I-06")
    cov.add_argument("--build-dir", dest="build_dir",
                     help=f"where lake writes .ilean (default: {DEFAULT_BUILD_DIR})")
    cov.add_argument("--lib", action="append", default=None,
                     help="root library module name; overrides the emission's "
                          "declared root_lib. REPEATABLE: the emitter takes "
                          "additionalRoots, so a monorepo legitimately has "
                          "more than one root and scoping to a single one "
                          "fails I-04 and I-05 on the other's modules")
    cov.add_argument("--require-all", dest="require_all", action="store_true",
                     help="treat any not_run rule as a failure")
    cov.set_defaults(func=cmd_check_ilean_coverage)

    ini = sub.add_parser(
        "init",
        help="render a topic repository that reaches a green build on run one",
        description="Renders files and NOTHING else: no git init, no remote, no "
                    "commit. Every pin must be a 40-hex commit; branches are "
                    "refused because `lake update` re-resolves them.",
    )
    ini.add_argument("--topic", required=True,
                     help="topic slug, e.g. analytic-nt (same shape as an "
                          "arXMCP notebook slug)")
    ini.add_argument("--lib", help="Lean library name (default: derived from --topic)")
    ini.add_argument("--toolchain", required=True,
                     help="e.g. leanprover/lean4:v4.29.0")
    ini.add_argument("--mathlib-rev", dest="mathlib_rev", required=True,
                     help="40-hex Mathlib commit; must match what the anchor resolves to")
    ini.add_argument("--contract-rev", dest="contract_rev", required=True,
                     help="40-hex commit of this contract repo to pin against")
    ini.add_argument("--contract-url", dest="contract_url",
                     help=f"default: {DEFAULT_CONTRACT_URL}")
    ini.add_argument("--anchor-name", dest="anchor_name",
                     help="upstream package to build on; all three anchor flags "
                          "are given together or not at all")
    ini.add_argument("--anchor-url", dest="anchor_url")
    ini.add_argument("--anchor-rev", dest="anchor_rev", help="40-hex")
    ini.add_argument("--dest", default=".", help="directory to render into (default: .)")
    ini.add_argument("--force", action="store_true",
                     help="write into a non-empty directory")
    ini.add_argument("--dry-run", dest="dry_run", action="store_true",
                     help="list what would be written and exit")
    ini.set_defaults(func=cmd_init)

    res = sub.add_parser(
        "check-resolution",
        help="the freshness gate over resolution.json",
        description="Distinguishes DRIFT (the corpus moved) from STALENESS "
                    "(nobody has looked). They are different facts and must "
                    "not share a finding.")
    res.add_argument("resolution", help="path to attest/resolution.json")
    res.add_argument("--registry", help="registry file, hashed to check F-01")
    res.add_argument("--manifest-hash", dest="manifest_hash",
                     help="live corpus manifest content hash; enables F-02")
    res.add_argument("--max-age-days", dest="max_age_days", type=int,
                     default=DEFAULT_MAX_AGE_DAYS,
                     help=f"staleness limit (default: {DEFAULT_MAX_AGE_DAYS})")
    res.add_argument("--require-all", dest="require_all", action="store_true",
                     help="treat any not_run rule as a failure")
    res.set_defaults(func=cmd_check_resolution)

    rst = sub.add_parser(
        "restate-check",
        help="read the Lean restate run: does human review survive the bump",
        description="Three outcomes, no aggregate. `restated` carries a review "
                    "forward; `changed` invalidates it; `not_checkable` means "
                    "nobody knows, and is never evidence that it changed.")
    rst.add_argument("restate", help="path to the restate/1.0 report")
    rst.add_argument("--review", help="attest/review.yaml; enables T-01 and T-02")
    rst.add_argument("--require-all", dest="require_all", action="store_true")
    rst.set_defaults(func=cmd_restate_check)

    wd = sub.add_parser(
        "check-withdrawals",
        help="the W-01..W-04 rules over withdrawals.yaml",
        description="A published record that a human determined is not "
                    "faithful must be retractable. This file is append-only "
                    "and may travel forward in time, because it can only "
                    "remove trust and never grant it.")
    wd.add_argument("withdrawals")
    wd.add_argument("--registry", help="enables W-02 and W-03")
    wd.add_argument("--previous", help="an earlier revision of this file; "
                                       "enables the append-only check W-04")
    wd.add_argument("--require-all", dest="require_all", action="store_true")
    wd.set_defaults(func=cmd_withdrawals_check)

    reg = sub.add_parser(
        "registry",
        help="mint a registry id, or validate a hand-authored registry",
        description="The registry is the only hand-authored artifact here, so "
                    "its rules are about the mistakes typing makes: a digest "
                    "nobody recomputed, a placeholder that still validates, a "
                    "key pasted from a corpus.",
    )
    regsub = reg.add_subparsers(dest="registry_command", required=True)

    reg_init = regsub.add_parser(
        "init", help="mint a 12-hex registry id (once per repository)")
    reg_init.set_defaults(func=cmd_registry_init)

    reg_ext = regsub.add_parser(
        "external-decls",
        help="write the emitter's --externals list from the registry",
        description="Constants already in Mathlib or the anchor that entries "
                    "bind to directly. The emitter sweeps them with "
                    "scope: external, so a topic never has to restate an "
                    "upstream theorem and then attest to the wrapper.")
    reg_ext.add_argument("registry")
    reg_ext.add_argument("--out", help="destination (default: stdout)")
    reg_ext.set_defaults(func=cmd_registry_external_decls)

    reg_val = regsub.add_parser(
        "validate", help="schema plus the R-01..R-09 content rules")
    reg_val.add_argument("registry", help="path to registry/*.yaml or .json")
    reg_val.add_argument("--frontier-kind-labels", dest="frontier_kind_labels",
                         nargs="*", help="the topic's allowlist; enables R-08")
    reg_val.add_argument("--require-all", dest="require_all", action="store_true",
                         help="treat any not_run rule as a failure")
    reg_val.set_defaults(func=cmd_registry_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
