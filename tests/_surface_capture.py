#!/usr/bin/env python3
"""Capture the CLI SURFACE of one tree as a parser OBJECT dump.

Feature 260813-modular-core-adoption, task 6.

Why an object dump and not rendered help
----------------------------------------
Every downstream gate in this migration (the union fixture, the symmetric
per-verb promotion gate, the donor-only presence gate) has to answer questions
that rendered ``--help`` text physically cannot: *is this flag still required?*
*did its ``choices`` set narrow?* *did its ``default`` move off the decided
value?* *is it still ``nargs="+"``?*  ``format_help()`` collapses all of that
into prose that also churns with the interpreter's own help renderer -- which
is exactly why the donor's eighty golden help fixtures all fail here (task 60).

So this tool walks the live ``argparse`` object graph and records, per action,
only the machine-comparable fields:

    kind, dest, option_strings, required, nargs, default, choices, type

and NOTHING that is prose. ``help=``, ``description=``, ``epilog=`` and
``usage=`` are deliberately never read. ``--verify`` re-asserts that, so a
later edit cannot quietly reintroduce rendered text into a fixture that
downstream gates diff byte-wise.

Counting convention (pinned; the acceptance numbers depend on it)
----------------------------------------------------------------
* a NODE is one ``ArgumentParser``: the root, plus every subparser reachable
  through every ``_SubParsersAction``, at every depth.
* an ACTION is one ``argparse.Action`` on a node, EXCLUDING the two structural
  kinds that argparse synthesises rather than the author declaring:
  ``_HelpAction`` (the automatic ``-h/--help``) and ``_SubParsersAction``
  (the child-dispatch slot, which is recorded as node metadata instead).
  Counting those would measure argparse's boilerplate, not the surface.

Under that convention the local tree is 106 nodes / 442 actions and the donor
tree is 80 nodes / 301 actions.

Hermeticity
-----------
The capture neutralises every ``AGENTWARE_*`` variable and points the knowledge
dir and config home at a throwaway temp dir before importing, so a fixture can
never depend on -- or reach -- the operator's live knowledge base or machine
config. The entrypoint is loaded with ``SourceFileLoader`` (it has no ``.py``
suffix) after its own ``scripts/`` dir is put on ``sys.path``, which is what
makes the donor's thin facade resolve ``agentware_lib``. Run ONE tree per
invocation: the two trees define colliding module names.

Usage:
    python3 tests/_surface_capture.py --root <tree> --label local \
        --out tests/fixtures/surface-local.json
    python3 tests/_surface_capture.py --verify tests/fixtures/surface-local.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from importlib.machinery import SourceFileLoader

SCHEMA_VERSION = 1

# Structural actions argparse synthesises; see the counting convention above.
STRUCTURAL_ACTIONS = (argparse._HelpAction, argparse._SubParsersAction)

# Per-action fields recorded. Prose fields are absent BY CONSTRUCTION.
ACTION_FIELDS = ("kind", "dest", "option_strings", "required", "nargs",
                 "default", "choices", "type")

# A fixture must never carry rendered help. These are the shapes that would
# appear if it did -- argparse section headers, the automatic -h help string,
# and a rendered usage line.
FORBIDDEN_SUBSTRINGS = (
    "show this help message and exit",
    "usage: ",
    "positional arguments:",
    "optional arguments:",
)
FORBIDDEN_KEYS = ("help", "description", "epilog", "usage", "format_help")


# --------------------------------------------------------------------------
# value coercion
# --------------------------------------------------------------------------

def jsonable(value):
    """Coerce an argparse field to a stable, JSON-safe representation.

    Scalars pass through so ``default: 1500`` stays an int and compares
    numerically. Anything else (a callable ``type=``, a sentinel, a tuple)
    becomes its name or repr, which is still deterministic and diffable.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if value is argparse.SUPPRESS:
        return "==SUPPRESS=="
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    name = getattr(value, "__name__", None)
    if name:
        return name
    return repr(value)


def action_record(action):
    """One action as its machine-comparable fields. Never reads prose."""
    return {
        "kind": type(action).__name__,
        "dest": action.dest,
        "option_strings": list(action.option_strings),
        "required": bool(action.required),
        "nargs": jsonable(action.nargs),
        "default": jsonable(action.default),
        "choices": None if action.choices is None else [
            jsonable(c) for c in action.choices],
        "type": jsonable(action.type),
    }


# --------------------------------------------------------------------------
# parser walk
# --------------------------------------------------------------------------

def walk(parser, path, nodes):
    """Depth-first over the parser graph, appending one record per node."""
    actions, children, sub_dest = [], [], None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            sub_dest = action.dest if action.dest != argparse.SUPPRESS else None
            children.append(action)
            continue
        if isinstance(action, argparse._HelpAction):
            continue
        actions.append(action_record(action))

    nodes.append({
        "path": list(path),
        "depth": len(path),
        "subcommand_dest": sub_dest,
        "subcommands": [name for a in children for name in a.choices],
        "actions": actions,
    })

    for action in children:
        for name, subparser in action.choices.items():
            walk(subparser, path + [name], nodes)


def load_entrypoint(root):
    """Import ``<root>/scripts/agentware`` as a module.

    The entrypoint has no ``.py`` suffix, so it is loaded through
    ``SourceFileLoader`` -- the same mechanism ``tests/_fixtures.load_cli``
    uses. Its ``scripts/`` dir goes on ``sys.path`` FIRST so a thin facade
    resolving ``agentware_lib`` finds its own tree's package.
    """
    scripts_dir = os.path.join(root, "scripts")
    entry = os.path.join(scripts_dir, "agentware")
    if not os.path.isfile(entry):
        raise SystemExit(f"no entrypoint at {entry}")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    loader = SourceFileLoader("agentware_cli", entry)
    spec = importlib.util.spec_from_loader("agentware_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def neutralise_env():
    """Drop every AGENTWARE_* variable; redirect KB + config to a temp dir.

    A surface fixture must be a property of the TREE, not of the machine that
    captured it, and must never be able to reach the live knowledge base.
    """
    for key in [k for k in os.environ if k.startswith("AGENTWARE_")]:
        del os.environ[key]
    sandbox = tempfile.mkdtemp(prefix="aw-surface-")
    os.environ["AGENTWARE_KNOWLEDGE_DIR"] = os.path.join(sandbox, "kb")
    os.environ["AGENTWARE_CONFIG_HOME"] = os.path.join(sandbox, "config")
    os.makedirs(os.environ["AGENTWARE_KNOWLEDGE_DIR"], exist_ok=True)
    os.makedirs(os.environ["AGENTWARE_CONFIG_HOME"], exist_ok=True)
    return sandbox


def redact(path):
    """Home-relativise a path so no fixture carries an operator home dir."""
    home = os.path.expanduser("~")
    real = os.path.abspath(path)
    if real == home:
        return "~"
    if real.startswith(home + os.sep):
        return "~" + real[len(home):]
    return real


def git_head(root):
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or None
    except Exception:
        return None


# --------------------------------------------------------------------------
# capture / verify
# --------------------------------------------------------------------------

def capture(root, label):
    neutralise_env()
    module = load_entrypoint(root)
    parser = module.build_parser()

    nodes = []
    walk(parser, [], nodes)

    top = sorted(nodes[0]["subcommands"])
    return {
        "schema_version": SCHEMA_VERSION,
        "label": label,
        "root": redact(root),
        "git_head": git_head(root),
        "python": "%d.%d" % sys.version_info[:2],
        "counting_convention": {
            "node": "one ArgumentParser: the root plus every reachable subparser",
            "action_excludes": [c.__name__ for c in STRUCTURAL_ACTIONS],
            "action_fields": list(ACTION_FIELDS),
            "prose_recorded": False,
        },
        "counts": {
            "nodes": len(nodes),
            "actions": sum(len(n["actions"]) for n in nodes),
            "top_level_verbs": len(top),
        },
        "top_level_verbs": top,
        "nodes": nodes,
    }


def scan_for_prose(fixture):
    """Return every place rendered help text leaked into a fixture."""
    hits = []

    def visit(node, where):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in FORBIDDEN_KEYS and where != "$.counting_convention":
                    hits.append(f"{where}.{key}: forbidden key")
                visit(value, f"{where}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                visit(value, f"{where}[{i}]")
        elif isinstance(node, str):
            low = node.lower()
            for bad in FORBIDDEN_SUBSTRINGS:
                if bad in low:
                    hits.append(f"{where}: contains {bad!r}")

    visit(fixture, "$")
    return hits


def verify(path, expect_nodes=None, expect_actions=None):
    with open(path, encoding="utf-8") as fh:
        fixture = json.load(fh)          # valid JSON or this raises

    problems = []
    counts = fixture.get("counts", {})
    real_nodes = len(fixture.get("nodes", []))
    real_actions = sum(len(n.get("actions", [])) for n in fixture.get("nodes", []))
    if counts.get("nodes") != real_nodes:
        problems.append(f"counts.nodes {counts.get('nodes')} != {real_nodes} recorded")
    if counts.get("actions") != real_actions:
        problems.append(f"counts.actions {counts.get('actions')} != {real_actions} recorded")
    if expect_nodes is not None and real_nodes != expect_nodes:
        problems.append(f"nodes {real_nodes} != expected {expect_nodes}")
    if expect_actions is not None and real_actions != expect_actions:
        problems.append(f"actions {real_actions} != expected {expect_actions}")

    for node in fixture.get("nodes", []):
        for act in node.get("actions", []):
            if set(act) != set(ACTION_FIELDS):
                problems.append(
                    "/".join(node["path"]) + f": action field set is {sorted(act)}")
                break
            if act["kind"] in [c.__name__ for c in STRUCTURAL_ACTIONS]:
                problems.append("/".join(node["path"]) + f": structural action {act['kind']}")

    problems.extend(scan_for_prose(fixture))

    for line in problems:
        print(f"[FAIL] {line}")
    if problems:
        return 1
    print(f"[OK] {path}: {real_nodes} nodes, {real_actions} actions, "
          f"{counts.get('top_level_verbs')} top-level verbs, no rendered help text")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="_surface_capture",
        description="capture or verify a CLI parser-object surface fixture")
    ap.add_argument("--verify", metavar="FIXTURE",
                    help="verify an existing fixture instead of capturing")
    ap.add_argument("--expect-nodes", type=int, default=None)
    ap.add_argument("--expect-actions", type=int, default=None)
    ap.add_argument("--root", help="tree to capture (holds scripts/agentware)")
    ap.add_argument("--label", help="fixture label, e.g. local or donor")
    ap.add_argument("--out", help="fixture path to write")
    args = ap.parse_args(argv)

    if args.verify:
        return verify(args.verify, args.expect_nodes, args.expect_actions)

    if not (args.root and args.label and args.out):
        ap.error("--root, --label and --out are required when capturing")

    fixture = capture(os.path.abspath(args.root), args.label)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(fixture, fh, indent=2, sort_keys=False)
        fh.write("\n")
    print(f"wrote {args.out}: {fixture['counts']['nodes']} nodes, "
          f"{fixture['counts']['actions']} actions, "
          f"{fixture['counts']['top_level_verbs']} top-level verbs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
