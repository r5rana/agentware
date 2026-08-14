"""agentware_lib.ops.scaffoldcmd — the `scaffold` verb's CLI surface (layer: ops).

Owns: the `scaffold` verb — its `__verbs__` declaration, the `register_scaffold`
argparse registrar, and the two handlers `cmd_scaffold_new` (emit a compliant
unit and all of its wiring) and `cmd_scaffold_check` (the CI audit), plus their
text and `--format json` rendering.
Does NOT own: the unit model and the rendered artifacts -> ops.scaffoldunit; the
writes and the regenerator invocations -> ops.scaffoldwire; the audit's five
checks -> ops.scaffoldcheck. This module is the SURFACE and nothing else, so the
scaffold contract is testable without argparse and the surface is testable
without touching disk.
Layer deps: ops -> same-layer ops.scaffoldunit, ops.scaffoldwire, ops.scaffoldcheck plus stdlib. All DOWNWARD.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

WHY `new` IS A SUBCOMMAND AND NOT THE VERB
------------------------------------------
`scaffold` has two halves that are used by different people at different times:
an author emits a unit ONCE, and CI asks the audit question on EVERY run. Folding
the audit into a flag on a generating verb would make the CI step read
`agentware scaffold --check`, i.e. a command whose default behaviour WRITES
FILES, one dropped flag away from a generator running in a pipeline. Two
subcommands make the read-only half the one you can run anywhere.

EXIT CODES
----------
`new` exits 0 on a written (or planned) unit and 2 on a rejected description —
never 1, so a CI script can tell "your unit is invalid" from "your tree is
incomplete". `check` exits 0 when every unit is whole and 1 when any finding
stands, which is the shape every other gate in this tree uses.

Stdlib only. Writes only inside the package checkout; never the knowledge base.
"""

import json
import sys

import agentware_lib.ops.scaffoldcheck as scaffoldcheck
import agentware_lib.ops.scaffoldunit as scaffoldunit
import agentware_lib.ops.scaffoldwire as scaffoldwire
from agentware_lib.ops.scaffoldunit import ScaffoldError


#: Exit code for an invalid unit DESCRIPTION, kept distinct from the audit's 1.
RC_BAD_UNIT = 2


def _emit_json(payload):
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_scaffold_new(args):
    """`agentware scaffold new` — emit a compliant unit and all of its wiring."""
    as_json = getattr(args, "format", "text") == "json"
    try:
        unit = scaffoldunit.resolve(args.kind, args.name,
                                    layer=getattr(args, "layer", None),
                                    verb=getattr(args, "verb", None),
                                    task=getattr(args, "task", 0))
        result = scaffoldwire.emit(unit, dry_run=bool(args.dry_run),
                                   force=bool(args.force),
                                   regen=not bool(args.no_regen))
    except ScaffoldError as exc:
        if as_json:
            _emit_json({"ok": False, "error": str(exc)})
        else:
            sys.stderr.write("scaffold: %s\n" % (exc,))
        return RC_BAD_UNIT
    if as_json:
        _emit_json(dict(result, ok=True))
        return 0
    _print_new(result)
    return 0


def _print_new(result):
    verb = " (verb `%s`)" % result["unit"]["verb"] if result["unit"]["verb"] else ""
    print("scaffold %s `%s`%s%s:"
          % (result["unit"]["kind"], result["unit"]["name"], verb,
             " — DRY RUN, nothing written" if result["dry_run"] else ""))
    for rel in result["created"]:
        print("  create  %s" % rel)
    for rel in result["edited"]:
        print("  edit    %s" % rel)
    for item in result["regenerated"]:
        print("  regen   %s (rc=%d) %s"
              % (item["generator"], item["rc"], item["output"]))
    print("next:")
    for i, step in enumerate(result["next_steps"], 1):
        print("  %d. %s" % (i, step))


def cmd_scaffold_check(args):
    """`agentware scaffold check` — the CI audit. Exit 1 on any finding."""
    result = scaffoldcheck.audit()
    if getattr(args, "format", "text") == "json":
        _emit_json(result)
        return 0 if result["ok"] else 1
    if result["ok"]:
        print("scaffold check: PASSED — %d modules, every unit wired"
              % result["modules"])
        return 0
    print("scaffold check: FAILED — %d finding(s) across %d modules:"
          % (len(result["findings"]), result["modules"]))
    for item in result["findings"]:
        print("  [%s] %s — %s" % (item["kind"], item["location"], item["detail"]))
    return 1


#: Verb declaration — the ONE place this verb is registered (R-MOD-01). Discovered
#: by `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("scaffold", 4500, "register_scaffold"),
)


def register_scaffold(sub, fmt_parent):
    """Declare the `scaffold` verb: `new` (emit a unit) and `check` (audit)."""
    p_scaffold = sub.add_parser(
        "scaffold",
        help="emit a compliant new unit with all its wiring, and audit the tree "
             "for units missing theirs")
    scaffold_sub = p_scaffold.add_subparsers(dest="scaffold_command",
                                             metavar="<subcommand>")
    scaffold_sub.required = True
    _register_new(scaffold_sub, fmt_parent)
    p_check = scaffold_sub.add_parser(
        "check", parents=[fmt_parent],
        help="CI audit: every module has its manifest entry, owning test, doc "
             "row and parity row (exit 1 on any finding)")
    p_check.set_defaults(func=cmd_scaffold_check)


def _register_new(scaffold_sub, fmt_parent):
    p_new = scaffold_sub.add_parser(
        "new", parents=[fmt_parent],
        help="emit a unit (module, verb, panel or loop lib) plus its verb "
             "declaration, manifest entry, mirrored test, doc row and parity row")
    p_new.add_argument("--kind", required=True, choices=list(scaffoldunit.KIND_NAMES),
                       help="what kind of unit to emit")
    p_new.add_argument("--name", required=True,
                       help="the unit stem: lowercase letters and digits "
                            "(e.g. `planlint`)")
    p_new.add_argument("--layer", choices=list(scaffoldunit.LIB_LAYERS),
                       help="the lib layer (required for --kind module/verb)")
    p_new.add_argument("--verb", help="the top-level CLI verb (required for "
                                      "--kind verb)")
    p_new.add_argument("--task", type=int, required=True,
                       help="the plan task number this unit lands under; it is "
                            "recorded on the curated parity row, which the parity "
                            "schema refuses without one")
    p_new.add_argument("--dry-run", action="store_true",
                       help="report every path the scaffold would claim, and "
                            "write nothing")
    p_new.add_argument("--force", action="store_true",
                       help="overwrite an existing unit or test file")
    p_new.add_argument("--no-regen", action="store_true",
                       help="skip the manifest and parity regenerators")
    p_new.set_defaults(func=cmd_scaffold_new)


_EXPORTS = [
    "RC_BAD_UNIT",
    "cmd_scaffold_new",
    "cmd_scaffold_check",
]
