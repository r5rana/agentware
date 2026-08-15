"""agentware_lib.cli — the parser ASSEMBLER (leaf of the layer graph).

Owns: assemble_parser — building the argparse tree from the frozen registration tuple — PLUS the whereami verb (cmd_whereami + register_whereami), a self-contained CLI-lifecycle handler with no domain owner, homed with the assembler in the leaf per the target architecture (cli.py owns main() + cmd_whereami).
Does NOT own: the DOMAIN per-verb registrars or command handlers — those live in each owning agentware_lib module (see agentware_lib._registration for the module->verb map); whereami is the exception because it belongs to no domain layer. Nor the RESOLUTION whereami renders: the marker-driven project/repo walk is agentware_lib.core.projectcontext's, so the steering-capture auto-scope (a features-layer caller that must not import the cli leaf) reaches the same answer.
Layer deps: cli -> core, features, knowledge, ops, retrieval (the leaf; may import anything, nothing imports it).
Fan-in: 1 — agentware_lib.strangler, which imports main() INSIDE dispatch() on the branch that routes an allowlisted verb here (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

``assemble_parser`` builds the argparse tree by iterating the FROZEN
``_PARSER_REGISTRATION`` tuple (agentware_lib._registration) and invoking each
verb's registrar in display order. This is what makes the parser surface
data-driven: every extraction wave repoints a verb's registration entry from
the entrypoint to its owning agentware_lib module WITHOUT changing ``--help``,
because the tuple's ORDER is frozen and each registrar reproduces exactly the
add_parser / add_argument / set_defaults calls it owned in the monolith.

Layer position: cli is the LEAF — it may import anything; nothing imports it.

Registrar contract: every registrar is ``register_<verb>(sub, fmt_parent)``
where ``sub`` is the top-level subparsers action and ``fmt_parent`` is the
shared ``--format`` parent parser. Registrars that do not use ``--format`` still
accept ``fmt_parent`` for a uniform signature.
"""

import argparse
import importlib
import json
import os

from ._registration import _PARSER_REGISTRATION
from .core import projectcontext
from .features import migratecmd


def _resolve_registrar(verb, module, attr, entrypoint_registrars):
    """Resolve a single registration entry to its callable registrar.

    ``module is None`` -> entrypoint-owned; looked up by ``verb`` in
                          ``entrypoint_registrars`` (supplied by the caller that
                          still holds the not-yet-extracted registrar).
    ``module`` a str   -> a fully-extracted agentware_lib module; the registrar
                          is ``getattr(import_module(module), attr)``.
    """
    if module is None:
        if entrypoint_registrars is None or verb not in entrypoint_registrars:
            raise KeyError(
                "assemble_parser: no registrar supplied for entrypoint-owned "
                "verb %r (expected in entrypoint_registrars)" % (verb,))
        return entrypoint_registrars[verb]
    return getattr(importlib.import_module(module), attr)


def assemble_parser(entrypoint_registrars=None):
    """Build the full agentware ArgumentParser from ``_PARSER_REGISTRATION``.

    Byte-for-byte equivalent to the pre-modularization monolithic build_parser:
    the header (prog / description + the shared ``--format`` parent + the
    ``<command>`` subparsers action) is constructed here, then each verb's
    registrar runs in the frozen tuple order. ``entrypoint_registrars`` maps a
    verb name to its registrar for any entry whose ``module`` is still ``None``.
    """
    fmt_parent = argparse.ArgumentParser(add_help=False)
    fmt_parent.add_argument("--format", choices=("text", "json"), default="text",
                            help="output format (default: text)")

    parser = argparse.ArgumentParser(
        prog="agentware",
        parents=[fmt_parent],
        description="Deterministic CLI toolkit for an agentware workspace.")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    for verb, module, attr in _PARSER_REGISTRATION:
        registrar = _resolve_registrar(verb, module, attr, entrypoint_registrars)
        registrar(sub, fmt_parent)
    return parser


def build_parser():
    """Assemble the full agentware CLI parser.

    Thin lifecycle wrapper over ``assemble_parser``. Since wave-6 seam 31 every
    entry in ``_PARSER_REGISTRATION`` is module-owned (no ``module is None``
    entries remain), so no ``entrypoint_registrars`` map is needed and the parser
    is byte-for-byte identical to the pre-modularization monolithic build_parser.
    """
    return assemble_parser()


def main(argv=None):
    """Parse ``argv`` and dispatch to the selected verb's handler.

    The single CLI entrypoint: the extensionless ``scripts/agentware`` shim calls
    this from its ``if __name__ == "__main__"`` guard (and re-exports it so
    ``load_cli().main`` and the dashboard sidecar reach it unchanged).

    Between PARSE and DISPATCH sits the KB schema interlock
    (``features.migratecmd._kb_predispatch_gate``, task 37): on a KB-writing verb
    it refuses the write when this package is OLDER than the KB's ``.schema``,
    and auto-applies pending ADDITIVE migrations when it is newer. It is a no-op
    for readers and for an unconfigured KB, and it lives HERE — on the one path
    every lib-routed verb takes — rather than in each handler, exactly as the
    monolith's own dispatch does it, so a writer cannot be ported past the guard
    by forgetting to call it.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    rc = migratecmd._kb_predispatch_gate(args)
    if rc is not None:
        return rc
    return args.func(args)


def cmd_whereami(args):
    """Resolve the project/repo context for a directory (build-system-neutral).

    Resolution is `core.projectcontext`'s: a marker-driven walk that works in any
    repository layout. This handler only chooses the starting directory and
    renders — no layout knowledge lives here.
    """
    start_dir = getattr(args, "dir", None) or os.environ.get("AGENTWARE_INVOKED_FROM") or os.getcwd()
    result = projectcontext.whereami_payload(start_dir)

    fmt = getattr(args, "format", "json")
    if fmt == "json":
        print(json.dumps(result, indent=2))
    else:
        if result["project_name"]:
            print("invoked_from:  %s" % result["invoked_from"])
            print("repo_root:     %s" % result["repo_root"])
            print("project_name:  %s" % result["project_name"])
            print("project_dir:   %s" % result["project_dir"])
        else:
            print("No project resolved for: %s" % result["invoked_from"])
    return 0


def register_whereami(sub, fmt_parent):
    # whereami ---------------------------------------------------------------
    p_whereami = sub.add_parser(
        "whereami", parents=[fmt_parent],
        help="resolve the project/repo context for a directory")
    p_whereami.add_argument("--dir", default=None,
                            help="directory to resolve (default: $AGENTWARE_INVOKED_FROM or $PWD)")
    p_whereami.set_defaults(func=cmd_whereami)


__all__ = ["assemble_parser"]
