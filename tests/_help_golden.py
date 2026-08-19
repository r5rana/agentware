#!/usr/bin/env python3
"""Capture + compare the RENDERED ``--help`` corpus of the merged tree.

Feature 260813-modular-core-adoption, task 60.

Why this exists alongside ``_surface_capture.py``
-------------------------------------------------
``_surface_capture.py`` records the parser OBJECT graph and deliberately reads
no prose at all (``help=``, ``description=``, ``epilog=``, ``usage=`` are never
touched), because every downstream migration gate needs machine-comparable
fields -- ``required``, ``nargs``, ``choices``, ``default`` -- that rendered
text physically cannot answer.

That leaves a real hole with the opposite shape: **the prose itself is
unpinned.** A verb whose ``help=`` string silently empties, a description that
still documents a flag that was removed, or a subcommand summary that drifts
away from what the handler does are all invisible to an object dump, to the
parity manifest (which compares capability ROWS) and to the mutation canary
(which proves ONE patched symbol is observable). This module closes that hole
the cheapest honest way: a byte-level golden master over every rendered help
surface, walked recursively so a nested sub-verb cannot escape it.

The two are complements, not rivals, and neither subsumes the other.

Why the donor's eighty goldens all fail here
--------------------------------------------
``--help`` bytes are NOT interpreter-independent. Between the version the
donor captured on and the one this tree runs, argparse changed its section
header (``optional arguments:`` -> ``options:``) and how it renders an option
that takes a value (``-f FMT, --format FMT`` -> ``-f, --format FMT``). Neither
is a surface change; both rewrite every fixture. Width matters too: argparse
derives its wrap column from ``$COLUMNS`` (falling back to 80), so an operator
with a wide terminal re-wraps every paragraph.

So the corpus is captured ONCE, on this operator's interpreter, with
``COLUMNS`` pinned -- and the capture RECORDS the interpreter that produced it
in ``_meta.json``. The gate reads that record rather than assuming: on the
recorded ``(major, minor)`` it compares bytes, and on any other it says so
explicitly and names the regeneration command instead of failing an operator
for running a different Python. The STRUCTURAL pins (the surface set, the
count, the display order, the dispatch contract) are version-independent and
run everywhere -- so a foreign interpreter still catches a lost or added verb.

Regenerate (only when a verb or its prose legitimately changes):

    python3 tests/_help_golden.py --write

Verify without writing (what the gate does):

    python3 tests/_help_golden.py --verify
"""

import argparse
import json
import os
import sys

if __package__ in (None, ""):  # direct `python3 tests/_help_golden.py` run
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    from _paths import REPO_ROOT, TESTS_DIR
else:
    from tests._paths import REPO_ROOT, TESTS_DIR

#: The committed corpus. One ``<surface-key>.txt`` per parser surface.
GOLDEN_DIR = os.path.join(TESTS_DIR, "fixtures", "help_golden")

#: Capture provenance. Named with a leading underscore so it can never collide
#: with a surface key (argparse verbs are never underscore-initial) and is
#: skipped by every ``.txt`` enumeration.
META_PATH = os.path.join(GOLDEN_DIR, "_meta.json")

#: Pinned render width. Without this the corpus records the capturing
#: operator's terminal width and fails for everyone else.
COLUMNS = "80"

#: The one surface key that is not a verb chain.
ROOT_KEY = "top"


def _scripts_dir():
    return os.path.join(REPO_ROOT, "scripts")


def build_parser():
    """The live top-level parser, imported from the lib (never the facade).

    The facade is a ``sys.path`` bootstrap plus a dispatch tail; the parser is
    assembled in ``agentware_lib.cli``. Importing the owner directly keeps the
    golden corpus a statement about the LIB, which is what the modular tree
    ships, rather than about the shim in front of it.
    """
    scripts = _scripts_dir()
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import agentware_lib.cli as _cli

    return _cli.build_parser()


def walk_help(parser, parts=None):
    """Map every parser surface -> its rendered ``format_help()``.

    Recurses the root and every ``_SubParsersAction.choices`` entry to
    arbitrary depth. Surface key: ``"top"`` for the root, else the verb chain
    joined with ``_`` (``bench_lint``, ``fanout_merge-policy``).
    """
    parts = list(parts or [])
    key = "_".join(parts) if parts else ROOT_KEY
    surfaces = {key: parser.format_help()}
    for act in parser._actions:
        if isinstance(act, argparse._SubParsersAction):
            for name, sub in act.choices.items():
                surfaces.update(walk_help(sub, parts + [name]))
    return surfaces


def leaf_parsers(parser, parts=None):
    """Yield ``(verb_chain, parser)`` for every LEAF parser (no subparsers)."""
    parts = tuple(parts or ())
    subacts = [a for a in parser._actions
               if isinstance(a, argparse._SubParsersAction)]
    if not subacts:
        yield parts, parser
        return
    for act in subacts:
        for name, sub in act.choices.items():
            for res in leaf_parsers(sub, parts + (name,)):
                yield res


def top_level_verbs(parser):
    """Top-level verbs in ``--help`` DISPLAY order (insertion order)."""
    for act in parser._actions:
        if isinstance(act, argparse._SubParsersAction):
            return list(act.choices.keys())
    return []


def capture(parser=None):
    """Render every surface with ``COLUMNS`` pinned, restoring the caller's.

    Restores rather than leaks: a test process that captured once would
    otherwise re-wrap every later ``format_help()`` in the same run, which is
    the shape of bug that makes a suite pass alone and fail in discovery order.
    """
    prev = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = COLUMNS
    try:
        return walk_help(parser if parser is not None else build_parser())
    finally:
        if prev is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = prev


def interpreter_tag(version_info=None):
    """``"3.13"`` -- the granularity at which argparse rendering changes."""
    vi = version_info or sys.version_info
    return "%d.%d" % (vi[0], vi[1])


def capture_meta(surfaces):
    return {
        "schema_version": 1,
        "kind": "help-golden-meta",
        "written_by": "tests/_help_golden.py --write",
        "python": platform_version(),
        "python_tag": interpreter_tag(),
        "columns": COLUMNS,
        "surfaces": len(surfaces),
        "why": ("--help bytes are interpreter- and width-dependent; the gate "
                "compares bytes only on python_tag and pins COLUMNS so the "
                "corpus is reproducible."),
    }


def platform_version():
    return "%d.%d.%d" % sys.version_info[:3]


def read_meta(path=META_PATH):
    """Recorded capture provenance, or ``None`` when the corpus is absent."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def golden_keys(dest=None):
    """Surface keys present in the committed corpus (``_meta.json`` excluded)."""
    dest = dest or GOLDEN_DIR
    if not os.path.isdir(dest):
        return set()
    return set(f[:-4] for f in os.listdir(dest) if f.endswith(".txt"))


def read_golden(key, dest=None):
    path = os.path.join(dest or GOLDEN_DIR, key + ".txt")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(dest=None, surfaces=None):
    """(Re)write the corpus into ``dest``; prune surfaces that no longer exist.

    Returns ``(written, pruned)`` as sorted filename lists so a caller can
    assert on the second-regeneration-is-a-no-op property directly.
    """
    dest = dest or GOLDEN_DIR
    surfaces = surfaces if surfaces is not None else capture()
    os.makedirs(dest, exist_ok=True)
    existing = set(f for f in os.listdir(dest) if f.endswith(".txt"))
    written = set()
    for key, text in sorted(surfaces.items()):
        fname = key + ".txt"
        written.add(fname)
        with open(os.path.join(dest, fname), "w", encoding="utf-8") as fh:
            fh.write(text)
    pruned = existing - written
    for stale in sorted(pruned):
        os.remove(os.path.join(dest, stale))
    with open(os.path.join(dest, os.path.basename(META_PATH)), "w",
              encoding="utf-8") as fh:
        json.dump(capture_meta(surfaces), fh, indent=2, sort_keys=True)
        fh.write("\n")
    return sorted(written), sorted(pruned)


def compare(dest=None, surfaces=None):
    """Byte-diff the live surfaces against the committed corpus.

    Returns a list of human-readable difference strings; empty means clean.
    Reports a missing fixture and an unpinned live surface distinctly, because
    the two have opposite causes (a stale corpus vs a new verb).
    """
    dest = dest or GOLDEN_DIR
    surfaces = surfaces if surfaces is not None else capture()
    diffs = []
    for key in sorted(golden_keys(dest) - set(surfaces)):
        diffs.append("%s: fixture has no live surface (verb removed?)" % key)
    for key, text in sorted(surfaces.items()):
        path = os.path.join(dest, key + ".txt")
        if not os.path.isfile(path):
            diffs.append("%s: NO GOLDEN FIXTURE (new surface is unpinned)" % key)
            continue
        expected = read_golden(key, dest)
        if text != expected:
            diffs.append("%s: help bytes changed (live %dB vs golden %dB)"
                         % (key, len(text), len(expected)))
    return diffs


def main(argv):
    ap = argparse.ArgumentParser(
        prog="tests/_help_golden.py",
        description="capture or verify the --help golden corpus")
    ap.add_argument("--write", action="store_true",
                    help="(re)write the committed corpus from the live parser")
    ap.add_argument("--verify", action="store_true",
                    help="byte-diff the live parser against the corpus")
    args = ap.parse_args(argv)
    if not (args.write or args.verify):
        ap.error("pass --write or --verify")
    if args.write:
        written, pruned = write()
        print("wrote %d surfaces into %s (python %s, COLUMNS=%s)"
              % (len(written), os.path.relpath(GOLDEN_DIR, REPO_ROOT),
                 platform_version(), COLUMNS))
        for stale in pruned:
            print("  pruned stale fixture: %s" % stale)
    if args.verify:
        diffs = compare()
        meta = read_meta() or {}
        if meta.get("python_tag") and meta["python_tag"] != interpreter_tag():
            print("NOTE: corpus captured on python %s, running %s -- rendered "
                  "help is interpreter-dependent"
                  % (meta.get("python"), platform_version()))
        if diffs:
            print("HELP GOLDEN DRIFT (%d):" % len(diffs))
            for d in diffs:
                print("  " + d)
            return 1
        print("OK: %d help surfaces byte-identical to the corpus"
              % len(golden_keys()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
