#!/usr/bin/env python3
"""SOLO parity: the merged tree vs ``aw-pre-merge``, with team mode UNSET.

Feature 260813-modular-core-adoption, task 81.

What this answers that nothing else does
----------------------------------------
Every other migration gate in this corpus compares a STRUCTURE.
``_surface_capture`` dumps the parser object graph and deliberately reads no
prose. ``_parity_manifest`` compares capability ROWS. ``_body_differ`` compares
normalised function BODIES. ``_help_golden`` pins the merged tree's rendered
help against ITSELF, so it can only catch drift AFTER the merge, never drift
INTRODUCED BY it. None of them ever runs the two CLIs side by side and reads
what an operator would see.

That gap is not theoretical -- landing this module found two live defects that
every structural gate had passed:

  * ``config``'s human-readable report silently lost its ``skill_promote``
    line when ``cmd_config`` was decomposed into ``configcmd/``. The JSON arm
    kept the field, the ``--skill-promote-only`` arm kept the value, the body
    differ recorded the definition as legitimately "decomposed" -- and the
    text report an operator actually reads lost a line. A decomposition claim
    is a BEHAVIOURAL claim, and only a behavioural comparison can check it.
  * eleven ``help=`` strings were replaced by the donor's thinner text, losing
    operator-relevant information (``--token-budget``'s "never pipe recall
    through ``head``" warning, ``--install-git-guards``' non-clobber promise,
    the ``[0.0, 1.0]`` ranges on three thresholds). Prose is invisible to an
    object dump BY CONSTRUCTION, and a golden captured post-merge freezes the
    loss instead of reporting it.

Two arms, one contract
----------------------
**Arm A -- surface + prose.** Every parser node ``aw-pre-merge`` served must
exist in the merged tree; every action on it must still be there under the same
option strings; and every ``help=``/``description=``/``epilog=`` string must be
byte-identical unless a row in the ledger says otherwise. Additions are free
(the merged tree is a superset by design); losses and silent rewrites are not.

**Arm B -- a scripted read-only session.** Both trees run the same roster of
read-only invocations against BYTE-IDENTICAL copies of one seeded scratch
knowledge base, with ``$HOME`` sandboxed and ``AGENTWARE_ORG_ROOT`` /
``AGENTWARE_TEAM`` explicitly removed from the environment -- that removal IS
the "team mode unset" clause of the task. Exit code, stdout and stderr must
match after path normalisation. The knowledge base is fingerprinted before and
after, so "read-only" is asserted rather than assumed.

The ledger is a RATCHET, not an allowlist
-----------------------------------------
``tests/fixtures/solo-parity.json`` declares every difference that is allowed
to survive, each with a reason and, where one exists, the recorded decision id
that authorised it. It bites in both directions: a difference with no row fails
the gate, and a row whose difference has GONE fails it too, naming the row to
delete. Without the second half a row outlives its cause and the ledger becomes
the thing it was written to prevent.

Both sides are fresh clones
---------------------------
The pre side is a real ``git clone`` checked out at the tag, which is what
makes this a FRESH-CLONE comparison rather than a working-tree one. The post
side is the same clone at ``HEAD`` with the working tree's dirty files copied
over it, so a gate can land in the same commit as the fixes it demands;
``--post-ref`` drops the overlay and compares committed state instead. Both
sides are real repositories on purpose -- the first run of this harness gave
one side a ``.git`` and the other a bare directory, and ``whereami`` duly
reported the harness's own asymmetry as a parity failure.

Run:

    python3 tests/_solo_parity.py --verify          # what the gate does
    python3 tests/_solo_parity.py --report          # human-readable dump
    python3 tests/_solo_parity.py --capture <root>  # child-process arm A
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

if __package__ in (None, ""):  # direct `python3 tests/_solo_parity.py` run
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    from _paths import REPO_ROOT, TESTS_DIR
    from _solo_session import (SESSION, fingerprint_kb, normalize, run_session,
                               seed_kb)
else:
    from tests._paths import REPO_ROOT, TESTS_DIR
    from tests._solo_session import (SESSION, fingerprint_kb, normalize,
                                     run_session, seed_kb)

#: The tag the merged tree must not have regressed against.
PRE_REF = "aw-pre-merge"

#: Declared-difference ledger. See the module docstring: ratchet, not allowlist.
LEDGER_PATH = os.path.join(TESTS_DIR, "fixtures", "solo-parity.json")

#: Both trees are materialised under this basename so every path-derived value
#: (`whereami`'s project_name, notably) is identical on both sides. Without it
#: the harness reports its own scratch directory names as a parity failure.
TREE_BASENAME = "agentware"

# --------------------------------------------------------------------------
# tree materialisation
# --------------------------------------------------------------------------

def clone_at_ref(ref, dest, repo=REPO_ROOT):
    """Fresh ``git clone`` of ``repo`` checked out at ``ref``.

    ``--local`` hardlinks the object store, so this costs milliseconds rather
    than the 66 MB a network-shaped clone would copy, and still produces a real
    repository with its own ``.git`` -- which matters, because verbs that
    resolve project context read it.
    """
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    subprocess.run(["git", "clone", "--quiet", "--local", "--no-checkout",
                    repo, dest], check=True, capture_output=True)
    subprocess.run(["git", "-C", dest, "checkout", "--quiet", ref],
                   check=True, capture_output=True)
    return dest


def clone_with_worktree_overlay(dest, repo=REPO_ROOT):
    """Fresh clone at ``HEAD``, then the working tree's dirty files copied in.

    A clone rather than a bare ``copytree`` because BOTH sides must be real
    repositories: verbs that resolve project context read ``.git``, and giving
    one side a repository and the other a directory reports the harness's own
    asymmetry as a parity failure (it did, on the first run: ``whereami``
    resolved a project on the pre side and refused on the post side).

    The overlay is what lets this gate land in the same commit as the fixes it
    demands -- a gate that could only see committed state could never be the
    thing that finds an uncommitted regression. ``--post-ref`` opts out.
    """
    clone_at_ref("HEAD", dest, repo=repo)
    for status, path in _dirty_paths(repo):
        target = os.path.join(dest, path)
        if status == "D":
            if os.path.exists(target):
                os.remove(target)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(os.path.join(repo, path), target)
    return dest


def _dirty_paths(repo):
    """``(status, path)`` for every tracked-modified or untracked-added file.

    ``--untracked-files=all`` still honours ``.gitignore``, so caches and
    generated artifacts stay out without a second exclusion list to maintain.
    A rename arrives as ``XY <new>\\0<old>\\0``; both halves are emitted, so the
    clone loses the old path rather than keeping a stale duplicate of it.
    """
    proc = subprocess.run(["git", "-C", repo, "status", "--porcelain", "-z",
                           "--untracked-files=all"],
                          check=True, capture_output=True, text=True)
    fields = [f for f in proc.stdout.split("\0") if f]
    out, index = [], 0
    while index < len(fields):
        code, path = fields[index][:2], fields[index][3:]
        index += 1
        if code[0] in "RC":
            out.append(("M", path))
            if index < len(fields):
                out.append(("D", fields[index]))
                index += 1
            continue
        out.append(("D" if "D" in code else "M", path))
    return out


def materialize(workdir, side, ref=None):
    """Materialise one side under ``<workdir>/<side>/agentware`` and return it."""
    dest = os.path.join(workdir, side, TREE_BASENAME)
    if ref:
        return clone_at_ref(ref, dest)
    return clone_with_worktree_overlay(dest)


def ref_exists(ref, repo=REPO_ROOT):
    proc = subprocess.run(["git", "-C", repo, "rev-parse", "--verify",
                           "%s^{commit}" % ref], capture_output=True)
    return proc.returncode == 0


# --------------------------------------------------------------------------
# arm A -- surface + prose capture (runs in a child process, one tree per run)
# --------------------------------------------------------------------------

def child_capture(root):
    """Walk ``root``'s parser and record PROSE keyed by structure.

    Runs in a child process: the two trees define colliding module names
    (``agentware_lib`` on one side, a 34k-line monolith module on the other),
    so a single interpreter cannot hold both. Deliberately the mirror image of
    ``_surface_capture``, which records the machine fields and never the prose.
    """
    scripts = os.path.join(root, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from importlib.machinery import SourceFileLoader
    import importlib.util
    entry = os.path.join(scripts, "agentware")
    loader = SourceFileLoader("aw_solo_entry", entry)
    spec = importlib.util.spec_from_loader("aw_solo_entry", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aw_solo_entry"] = mod
    loader.exec_module(mod)
    if hasattr(mod, "build_parser"):
        parser = mod.build_parser()
    else:  # the modular facade is a bootstrap + dispatch tail; the lib owns it
        import agentware_lib.cli as _cli
        parser = _cli.build_parser()
    return walk_prose(parser)


def walk_prose(parser, parts=()):
    """``{node_key: {actions: {name: text}, description, epilog}}``.

    Action key: the joined option strings (``--repo``, ``-f/--format``), or
    ``<pos>DEST`` for a positional, or ``<sub>NAME`` for one subcommand's
    one-line summary. A subcommand summary is recorded on its PARENT because
    that is where argparse stores it and where a reader meets it.

    ``metavar`` is recorded under a parallel ``<metavar>`` key rather than
    skipped: it is rendered text an operator reads (``0..1`` became ``0.0-1.0``
    on three threshold flags during the port) and it is absent from
    ``_surface_capture``'s machine fields, so nothing else in the corpus would
    ever notice it move. Always recorded, even when unset, so an appearing or
    disappearing metavar reads as a change rather than as a lost action.
    """
    key = "_".join(parts) if parts else "top"
    actions, children = node_prose(parser)
    out = {key: {"actions": actions,
                 "description": parser.description or "",
                 "epilog": parser.epilog or ""}}
    for name, sub in children:
        out.update(walk_prose(sub, tuple(parts) + (name,)))
    return out


def node_prose(parser):
    """One parser's ``(actions, [(child_name, child_parser)])``.

    Split out of ``walk_prose`` so each function owns one obligation: this one
    reads a node, that one walks the tree.
    """
    actions, children = {}, []
    for act in parser._actions:
        if isinstance(act, argparse._SubParsersAction):
            children.extend(act.choices.items())
            actions.update(_subcommand_summaries(act))
        elif not isinstance(act, argparse._HelpAction):
            actions.update(_action_prose(act))
    return actions, children


def _subcommand_summaries(subparsers_action):
    """``{"<sub>NAME": one-line summary}`` for one ``add_subparsers`` slot."""
    return {"<sub>" + choice.dest: (choice.help or "")
            for choice in subparsers_action._choices_actions}


def _action_prose(action):
    """One action's rendered strings: its ``help=`` and its ``metavar=``."""
    name = "/".join(action.option_strings) or ("<pos>" + action.dest)
    return {name: action.help or "",
            "<metavar>" + name: action.metavar or ""}


def capture_surface(root, python=None):
    """Spawn the child capture for ``root`` and return its parsed JSON."""
    out = os.path.join(tempfile.mkdtemp(prefix="solo-surface-"), "s.json")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AGENTWARE_")}
    env["AGENTWARE_KNOWLEDGE_DIR"] = tempfile.mkdtemp(prefix="solo-kb-")
    env["COLUMNS"] = "80"
    proc = subprocess.run(
        [python or sys.executable, os.path.realpath(__file__),
         "--capture", root, "--out", out],
        capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError("surface capture failed for %s: %s"
                           % (root, proc.stderr.strip()[-800:]))
    with open(out, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------

def read_ledger(path=LEDGER_PATH):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def prose_index(ledger):
    """``{(node, action): row}`` for the declared prose divergences."""
    return {(r["node"], r["action"]): r for r in ledger.get("prose", [])}


def session_index(ledger):
    return {r["id"]: r for r in ledger.get("session", [])}


# --------------------------------------------------------------------------
# arm A -- comparison
# --------------------------------------------------------------------------

def compare_surface(pre, post, ledger):
    """Differences arm A found, and ledger rows that have gone stale."""
    diffs, seen = [], set()
    declared = prose_index(ledger)
    for node in sorted(pre):
        if node not in post:
            diffs.append({"kind": "node-lost", "node": node,
                          "detail": "parser node absent from the merged tree"})
            continue
        diffs.extend(_compare_node(node, pre[node], post[node], declared, seen))
    stale = [{"kind": "stale-prose-row", "node": n, "action": a,
              "detail": "declared divergence no longer present"}
             for (n, a) in sorted(declared) if (n, a) not in seen]
    return diffs, stale


def _compare_node(node, pre_node, post_node, declared, seen):
    """One node's actions and prose. Additions are free; losses are not."""
    diffs = []
    for field in ("description", "epilog"):
        if pre_node[field] != post_node[field]:
            key = (node, "<%s>" % field)
            seen.add(key)
            if key not in declared:
                diffs.append({"kind": "prose-changed", "node": node,
                              "action": "<%s>" % field,
                              "pre": pre_node[field], "post": post_node[field]})
    for action, text in sorted(pre_node["actions"].items()):
        if action not in post_node["actions"]:
            diffs.append({"kind": "action-lost", "node": node,
                          "action": action,
                          "detail": "action absent from the merged tree"})
            continue
        if post_node["actions"][action] == text:
            continue
        seen.add((node, action))
        diffs.extend(_adjudicate(node, action, text,
                                 post_node["actions"][action], declared))
    return diffs


def _adjudicate(node, action, pre_text, post_text, declared):
    """A prose difference against its ledger row, if it has one.

    A row carries BOTH texts, so it asserts *this* difference rather than
    merely that the action differs. Without that a row written for one
    rewording silently licenses the next one.
    """
    row = declared.get((node, action))
    if row is None:
        return [{"kind": "prose-changed", "node": node, "action": action,
                 "pre": pre_text, "post": post_text}]
    if row.get("pre") != pre_text or row.get("post") != post_text:
        return [{"kind": "prose-row-mismatch", "node": node, "action": action,
                 "pre": pre_text, "post": post_text,
                 "detail": "the declared row no longer describes the live "
                           "difference -- re-adjudicate it"}]
    return []


# --------------------------------------------------------------------------
# arm B -- comparison
# --------------------------------------------------------------------------

def compare_session(pre_rows, post_rows, ledger):
    """Row-by-row session comparison plus the stale half of the ratchet."""
    diffs, seen = [], set()
    declared = session_index(ledger)
    for rid in sorted(pre_rows):
        pre, post = pre_rows[rid], post_rows[rid]
        same_rc = pre["rc"] == post["rc"]
        same_out = pre["out"] == post["out"] and pre["err"] == post["err"]
        if same_rc and same_out:
            continue
        seen.add(rid)
        row = declared.get(rid)
        if row is None:
            diffs.append({"kind": "session-differs", "id": rid,
                          "argv": pre["argv"], "pre_rc": pre["rc"],
                          "post_rc": post["rc"], "pre": pre["out"] + pre["err"],
                          "post": post["out"] + post["err"]})
        elif row.get("rc_must_match", True) and not same_rc:
            diffs.append({"kind": "session-rc-differs", "id": rid,
                          "argv": pre["argv"], "pre_rc": pre["rc"],
                          "post_rc": post["rc"],
                          "detail": "declared output divergence may not change "
                                    "the exit code"})
    stale = [{"kind": "stale-session-row", "id": rid,
              "detail": "declared divergence no longer present"}
             for rid in sorted(declared) if rid not in seen]
    return diffs, stale


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def run_parity(workdir, post_ref=None, ledger=None, pre_ref=PRE_REF):
    """Materialise both sides, run both arms, return the report dict."""
    ledger = ledger if ledger is not None else read_ledger()
    pre_root = materialize(workdir, "pre", ref=pre_ref)
    post_root = materialize(workdir, "post", ref=post_ref)

    pre_surface = capture_surface(pre_root)
    post_surface = capture_surface(post_root)
    surface_diffs, surface_stale = compare_surface(pre_surface, post_surface,
                                                   ledger)

    home = os.path.join(workdir, "home")
    os.makedirs(home, exist_ok=True)
    template = seed_kb(pre_root, workdir, home)
    kbs = {}
    for side in ("pre", "post"):
        kbs[side] = os.path.join(workdir, "kb-" + side)
        shutil.copytree(template, kbs[side])
    before = {s: fingerprint_kb(kbs[s]) for s in kbs}
    pre_rows = run_session(pre_root, kbs["pre"], home)
    post_rows = run_session(post_root, kbs["post"], home)
    after = {s: fingerprint_kb(kbs[s]) for s in kbs}
    session_diffs, session_stale = compare_session(pre_rows, post_rows, ledger)
    mutated = [s for s in kbs if before[s] != after[s]]

    return {
        "pre_ref": pre_ref,
        "post_ref": post_ref or "<working tree>",
        "nodes": {"pre": len(pre_surface), "post": len(post_surface)},
        "session_rows": len(pre_rows),
        "surface_diffs": surface_diffs,
        "surface_stale": surface_stale,
        "session_diffs": session_diffs,
        "session_stale": session_stale,
        "mutated_sides": mutated,
        "declared": {"prose": len(ledger.get("prose", [])),
                     "session": len(ledger.get("session", []))},
    }


def verdict(report):
    """``(ok, [failure strings])`` -- the whole contract in one place."""
    fails = []
    for d in report["surface_diffs"]:
        if d["kind"] in ("node-lost", "action-lost"):
            fails.append("%s: %s %s -- %s" % (d["kind"], d["node"],
                                              d.get("action", ""),
                                              d.get("detail", "")))
        else:
            fails.append("%s %s :: %s\n    pre : %s\n    post: %s"
                         % ("UNDECLARED PROSE CHANGE"
                            if d["kind"] == "prose-changed"
                            else "STALE PROSE ROW", d["node"], d["action"],
                            d["pre"], d["post"]))
    for d in report["session_diffs"]:
        fails.append("UNDECLARED SESSION DIVERGENCE %s (%s) rc %s/%s"
                     % (d["id"], " ".join(d["argv"]), d["pre_rc"],
                        d["post_rc"]))
    for d in report["surface_stale"] + report["session_stale"]:
        fails.append("STALE LEDGER ROW %s %s -- delete it"
                     % (d.get("node", d.get("id")), d.get("action", "")))
    for side in report["mutated_sides"]:
        fails.append("SESSION MUTATED THE %s KNOWLEDGE BASE -- the roster is "
                     "declared read-only" % side.upper())
    return (not fails), fails


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def main(argv):
    ap = argparse.ArgumentParser(
        prog="tests/_solo_parity.py",
        description="solo (team-mode-unset) parity against aw-pre-merge")
    ap.add_argument("--capture", metavar="ROOT",
                    help="child arm: dump ROOT's parser prose as JSON")
    ap.add_argument("--out", help="destination for --capture")
    ap.add_argument("--verify", action="store_true",
                    help="run both arms and exit nonzero on any undeclared "
                         "difference")
    ap.add_argument("--report", action="store_true",
                    help="run both arms and print a human-readable report")
    ap.add_argument("--json", action="store_true", help="print the raw report")
    ap.add_argument("--post-ref",
                    help="compare a committed ref instead of the working tree")
    args = ap.parse_args(argv)

    if args.capture:
        surfaces = child_capture(args.capture)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(surfaces, fh, indent=1, sort_keys=True)
        return 0
    if not (args.verify or args.report or args.json):
        ap.error("pass --capture, --verify, --report or --json")
    if not ref_exists(PRE_REF):
        print("SKIP: %s is unavailable (shallow clone?)" % PRE_REF)
        return 0

    workdir = tempfile.mkdtemp(prefix="solo-parity-")
    try:
        report = run_parity(workdir, post_ref=args.post_ref)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    ok, fails = verdict(report)
    if args.report or args.verify:
        print("solo parity: %s -> %s" % (report["pre_ref"], report["post_ref"]))
        print("  nodes:   %d pre / %d post (superset)"
              % (report["nodes"]["pre"], report["nodes"]["post"]))
        print("  session: %d read-only invocations per side"
              % report["session_rows"])
        print("  declared: %d prose row(s), %d session row(s)"
              % (report["declared"]["prose"], report["declared"]["session"]))
        for line in fails:
            print("  FAIL " + line)
        print("  " + ("OK: zero undeclared behavioural differences" if ok
                      else "FAILED: %d undeclared difference(s)" % len(fails)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
