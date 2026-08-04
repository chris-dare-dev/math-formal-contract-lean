"""The `mfc` command line.

Subcommands land as the contract does. Today: `lint-schemas`.

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

from .bundle import BundleError, build_declarations, dumps
from .lint import FORBIDDEN_PROPERTY_NAMES, lint_schema
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
        return EXIT_FINDINGS

    print(f"ok: {len(paths)} schema(s), no forbidden property names")
    return EXIT_OK


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


def _validate_against(document: object, version: str, label: str) -> int | None:
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
        rc = _validate_against(doc, want, label)
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

    for doc, want, label in ((emission, "emission/1.0", emission_path.name),
                             (environment, "environment/1.0", env_path.name)):
        rc = _validate_against(doc, want, label)
        if rc is not None:
            return rc

    closed_lanes = lanes_doc.get("closed_lanes") if isinstance(lanes_doc, dict) else None
    results = check(emission, environment, registry=registry, closed_lanes=closed_lanes)

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
        # Printed every time, never folded into a count. A green lint with a
        # not_run rule must not be readable as if that rule had passed.
        names = ", ".join(r.rule for r in results if r.status is Status.NOT_RUN)
        print(f"note: {names} did NOT run. This is not a pass -- nothing checked "
              f"what they check.", file=sys.stderr)
        if args.require_all:
            print("error: --require-all and at least one rule did not run",
                  file=sys.stderr)
            return EXIT_FINDINGS
    return EXIT_FINDINGS if failed else EXIT_OK


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
    bun.set_defaults(func=cmd_bundle)

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
    lnt.set_defaults(func=cmd_lint)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
