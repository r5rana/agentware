"""Task-15 gate: the KB TARGET GUARD, its wiring, and the retired repoint module.

The donor tree carried a one-shot, environment-specific KB auto-repoint migration
module. It is retired here (plan task 15) — but it was also the ONLY home in
EITHER tree of the generic question "is this directory safe to point the knowledge
dir at?". Deleting the module without relocating that guard would silently drop
the only check standing between an operator and a config pointing at a directory
that is not a knowledge base.

So this gate asserts BOTH halves at once, over whichever tree currently carries
the modular library:

  1. GUARD RESOLVES      `kb_pointable` lives in the core knowledge-dir layout
                         module and returns (ok, reason).
  2. GUARD VERDICTS      the six target shapes, including the deliberate
                         asymmetry over the DERIVED index: absent is fine
                         (a fresh checkout has none), present-but-corrupt is a
                         refusal. Refusals name what is missing.
  3. GUARD IS READ-ONLY  judging a directory never mutates it.
  4. WIRING              every repoint path calls it — `init` and
                         `config --set-knowledge-dir`, by name, at named sites.
  5. CLOBBER TABLE       the four `init` clobber cases resolve to the UNION
                         guard: the wider trigger, this tree's exit code 3 and
                         its structured JSON error key.
  6. RETIRED             the retired module is gone and its name has zero hits.
  7. ONE DEFINITION      the derived index's filename is defined exactly once.

Tree resolution, in order: --tree, AGENTWARE_LIB_TREE, this repo (once the lib
lands here at plan task 25), then the task-11 staging destination. If NO tree
carries the lib the gate FAILS — "I could not find anything to check" must never
read the same as "everything checks out".

Stdlib only. Every probe runs in a subprocess with AGENTWARE_CONFIG_HOME and
AGENTWARE_KNOWLEDGE_DIR redirected to throwaway paths, so the operator's real
config and knowledge base are never read or written.
"""

import argparse
import ast
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _stage_donor  # noqa: E402

LIB_REL = os.path.join("scripts", "agentware_lib")

#: The retired module's stem, assembled from parts on purpose: this file names it
#: in order to prove it is ABSENT, and a literal here would make the gate's own
#: source a hit for the tree-wide scan it performs (the self-referential trap the
#: task-13 portability work already paid for once).
RETIRED_STEM = "dream" + "repoint"
RETIRED_REL = os.path.join(LIB_REL, "features", RETIRED_STEM + ".py")

GUARD_MODULE = "agentware_lib.core.kblayout"
GUARD_NAME = "kb_pointable"

#: Every path that can move the knowledge-dir pointer, with the handler that owns
#: it. Both must call the guard; a new repoint path added without one is the
#: regression this row-set exists to catch.
REPOINT_SITES = (
    (os.path.join(LIB_REL, "features", "setupcmd.py"), "cmd_init",
     "agentware init --knowledge-dir"),
    (os.path.join(LIB_REL, "features", "configcmd.py"), "cmd_config",
     "agentware config --set-knowledge-dir"),
)

#: target shape -> (accepted?, a substring the reason must carry)
EXPECTED_VERDICTS = {
    "absent": (False, "not a directory"),
    "no_sentinel": (False, ".initialized"),
    "no_entry_document": (False, "MAIN.md"),
    "no_derived_index": (True, "ok"),
    "corrupt_index": (False, "index.json"),
    "valid_index": (True, "ok"),
}

#: The four `init` clobber cases (the conflicting case runs twice — once against
#: a live KB, once against a bare scaffold, which is the shape the narrower fork
#: let through). Each row: name, configured dir conformant?, seed a config?,
#: which dir the second init targets, extra argv, rc, JSON error key, and which
#: dir the config must name afterwards.
CLOBBER_CASES = (
    ("first_run", False, False, "second", [], 0, None, "second"),
    ("idempotent_same_dir", False, True, "first", [], 0, None, "first"),
    ("conflicting_live_kb", True, True, "second", [], 3,
     "kb-already-configured", "first"),
    ("conflicting_bare_scaffold", False, True, "second", [], 3,
     "kb-already-configured", "first"),
    ("conflicting_with_force", True, True, "second", ["--force"], 0, None,
     "second"),
)

SCAN_SKIP_DIRS = (".git", "__pycache__", ".venv", "node_modules", ".mypy_cache",
                  ".cache")


# --------------------------------------------------------------------------
# tree resolution
# --------------------------------------------------------------------------

def candidate_trees(explicit=None):
    """Every tree that might carry the lib, most authoritative first."""
    out = []
    if explicit:
        out.append(explicit)
    env = os.environ.get("AGENTWARE_LIB_TREE", "").strip()
    if env:
        out.append(env)
    out.append(REPO)
    dest = _stage_donor.resolve_dest()
    out.append(dest)
    out.append(os.path.join(dest, "donor"))
    return out


def resolve_tree(explicit=None):
    """(tree, problems) — the first candidate that actually carries the lib."""
    tried = []
    for cand in candidate_trees(explicit):
        tried.append(cand)
        if os.path.isdir(os.path.join(cand, LIB_REL)):
            return cand, []
    return None, ["no tree carries %s; tried: %s" % (LIB_REL, ", ".join(tried))]


# --------------------------------------------------------------------------
# subprocess probes
# --------------------------------------------------------------------------

_TARGET_PROBE = r'''
import json, os, tempfile
import agentware_lib.core.kblayout as kb

base = tempfile.mkdtemp(prefix="aw-kbtarget-")

def mk(name, sentinel=True, main=True, index=None):
    d = os.path.join(base, name)
    os.makedirs(d)
    if sentinel:
        open(os.path.join(d, kb.SENTINEL_REL), "w").close()
    if main:
        open(os.path.join(d, kb.MAIN_REL), "w").close()
    if index is not None:
        with open(os.path.join(d, kb.INDEX_REL), "w") as f:
            f.write(index)
    return d

cases = {
    "absent": os.path.join(base, "never-created"),
    "no_sentinel": mk("no_sentinel", sentinel=False),
    "no_entry_document": mk("no_entry_document", main=False),
    "no_derived_index": mk("no_derived_index"),
    "corrupt_index": mk("corrupt_index", index="{not json at all"),
    "valid_index": mk("valid_index", index='{"entries": [], "tags": {}}'),
}
res = {"verdicts": {}}
corrupt = cases["corrupt_index"]
before = sorted((n, os.stat(os.path.join(corrupt, n)).st_mtime_ns)
                for n in os.listdir(corrupt))
for name, path in sorted(cases.items()):
    ok, reason = kb.kb_pointable(path)
    res["verdicts"][name] = {"ok": bool(ok), "reason": str(reason)}
after = sorted((n, os.stat(os.path.join(corrupt, n)).st_mtime_ns)
               for n in os.listdir(corrupt))
res["read_only"] = before == after
res["callable"] = callable(getattr(kb, "kb_pointable", None))
res["names"] = {"sentinel": kb.SENTINEL_REL, "main": kb.MAIN_REL,
                "index": kb.INDEX_REL}
print(json.dumps(res))
'''

_CLOBBER_PROBE = r'''
import argparse, contextlib, io, json, os, tempfile
import agentware_lib.core.config as config
import agentware_lib.features.setupcmd as setupcmd

CASES = json.loads(os.environ["AW_CLOBBER_CASES"])
base = tempfile.mkdtemp(prefix="aw-clobber-")

def make_kb(path):
    os.makedirs(path, exist_ok=True)
    open(os.path.join(path, ".initialized"), "w").close()
    with open(os.path.join(path, "MAIN.md"), "w") as f:
        f.write("# knowledge base\n")

def init(kdir, as_json, force):
    args = argparse.Namespace(knowledge_dir=kdir, force=force,
                              format="json" if as_json else "text")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = setupcmd.cmd_init(args)
    return rc, out.getvalue(), err.getvalue()

def configured():
    return config._persisted_knowledge_dir()

results = {}
for i, row in enumerate(CASES):
    name, conformant, seed, target, tail = row[0], row[1], row[2], row[3], row[4]
    kb1 = os.path.join(base, "%02d-first" % i)
    kb2 = os.path.join(base, "%02d-second" % i)
    os.makedirs(kb1); os.makedirs(kb2)
    if os.path.isfile(config.HOME_CONFIG):
        os.remove(config.HOME_CONFIG)
    if conformant:
        make_kb(kb1)
    if seed:
        init(kb1, False, False)
    rc, out, err = init(kb1 if target == "first" else kb2, True,
                        "--force" in tail)
    body = None
    if out.strip():
        try:
            body = json.loads(out)
        except ValueError:
            body = {"error": "<unparseable stdout>"}
    results[name] = {
        "rc": rc,
        "error": (body or {}).get("error"),
        "ok": (body or {}).get("ok"),
        "pointable": (body or {}).get("configured_dir_pointable"),
        "reason": (body or {}).get("configured_dir_reason"),
        "names_both": kb1 in err and kb2 in err,
        "offers_force": "--force" in err,
        "configured_is": ("first" if configured() == kb1
                          else "second" if configured() == kb2
                          else repr(configured())),
    }
print(json.dumps(results))
'''


def run_probe(tree, source, extra_env=None):
    """Run a probe against `tree`'s lib in a fully redirected subprocess."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(tree, "scripts")
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    with tempfile.TemporaryDirectory(prefix="aw-kbtarget-home-") as scratch:
        env["AGENTWARE_CONFIG_HOME"] = os.path.join(scratch, "config")
        env["AGENTWARE_KNOWLEDGE_DIR"] = os.path.join(scratch, "kb")
        os.makedirs(env["AGENTWARE_CONFIG_HOME"], exist_ok=True)
        env.update(extra_env or {})
        try:
            proc = subprocess.run([sys.executable, "-c", source], env=env,
                                  capture_output=True, text=True, timeout=300,
                                  cwd=tree)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, "probe could not run: %s" % (exc,)
    if proc.returncode != 0:
        return None, "probe exited %d: %s" % (proc.returncode,
                                              (proc.stderr or "").strip()[-500:])
    try:
        return json.loads(proc.stdout), None
    except ValueError as exc:
        return None, "probe emitted no JSON (%s): %s" % (exc, proc.stdout[-300:])


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def check_guard(tree):
    """Checks 1-3: the guard resolves, verdicts match, and it never mutates."""
    data, err = run_probe(tree, _TARGET_PROBE)
    if err:
        return ["guard probe failed: %s" % err]
    problems = []
    if not data.get("callable"):
        problems.append("%s.%s is not callable" % (GUARD_MODULE, GUARD_NAME))
    for case, (want_ok, needle) in sorted(EXPECTED_VERDICTS.items()):
        got = data["verdicts"].get(case)
        if got is None:
            problems.append("guard probe returned no verdict for %s" % case)
            continue
        if got["ok"] is not want_ok:
            problems.append("target %s: expected ok=%s, got ok=%s (%s)"
                            % (case, want_ok, got["ok"], got["reason"]))
        elif needle not in got["reason"]:
            problems.append("target %s: refusal must name %r, got %r"
                            % (case, needle, got["reason"]))
    if not data.get("read_only"):
        problems.append("the guard MUTATED the directory it judged")
    return problems


def _calls_guard(path, handler):
    """(found, detail) — does `handler` in `path` call the guard by name?"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError) as exc:
        return False, "unreadable: %s" % (exc,)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != handler:
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if isinstance(func, ast.Attribute) and func.attr == GUARD_NAME:
                return True, "%s:%d" % (handler, getattr(call, "lineno", 0))
            if isinstance(func, ast.Name) and func.id == GUARD_NAME:
                return True, "%s:%d" % (handler, getattr(call, "lineno", 0))
        return False, "%s does not call %s" % (handler, GUARD_NAME)
    return False, "no handler named %s" % handler


def check_wiring(tree):
    """Check 4: every repoint path calls the guard, at a named site."""
    problems = []
    for rel, handler, label in REPOINT_SITES:
        path = os.path.join(tree, rel)
        if not os.path.isfile(path):
            problems.append("repoint path missing: %s (%s)" % (rel, label))
            continue
        found, detail = _calls_guard(path, handler)
        if not found:
            problems.append("`%s` does not reach the target guard: %s (%s)"
                            % (label, detail, rel))
    return problems


def check_clobber_table(tree):
    """Check 5: the four init clobber cases resolve to the UNION guard."""
    payload = json.dumps([list(row[:5]) for row in CLOBBER_CASES])
    data, err = run_probe(tree, _CLOBBER_PROBE,
                          {"AW_CLOBBER_CASES": payload})
    if err:
        return ["clobber probe failed: %s" % err]
    problems = []
    for row in CLOBBER_CASES:
        got = data.get(row[0])
        if got is None:
            problems.append("clobber probe returned no row for %s" % row[0])
            continue
        problems.extend(_clobber_row_problems(row, got))
    return problems


def _clobber_row_problems(row, got):
    """Compare ONE clobber row against what the handler actually did."""
    name, conformant, _seed, _target, _tail, rc, key, ends_at = row
    problems = []
    if got["rc"] != rc:
        problems.append("clobber %s: expected rc=%d, got rc=%s"
                        % (name, rc, got["rc"]))
    if got["configured_is"] != ends_at:
        problems.append("clobber %s: config must name the %s dir, names %s"
                        % (name, ends_at, got["configured_is"]))
    if key is None:
        if got["error"]:
            problems.append("clobber %s: accepted case emitted error %r"
                            % (name, got["error"]))
        return problems
    if got["error"] != key:
        problems.append("clobber %s: expected JSON error key %r, got %r"
                        % (name, key, got["error"]))
    if got["ok"] is not False:
        problems.append("clobber %s: refusal payload is not ok=false" % name)
    if got["pointable"] is not conformant:
        problems.append("clobber %s: kb_pointable verdict %r, expected %r"
                        % (name, got["pointable"], conformant))
    if not got["names_both"]:
        problems.append("clobber %s: stderr must name both dirs" % name)
    if not got["offers_force"]:
        problems.append("clobber %s: stderr must offer --force" % name)
    return problems


def scan_hits(tree, needle):
    """Every tree file whose path or body carries `needle` (case-insensitive)."""
    needle = needle.lower()
    hits = []
    for base, dirs, names in os.walk(tree):
        dirs[:] = sorted(d for d in dirs if d not in SCAN_SKIP_DIRS)
        for name in sorted(names):
            path = os.path.join(base, name)
            rel = os.path.relpath(path, tree)
            if os.path.islink(path):
                continue
            if needle in rel.lower():
                hits.append(rel + " (filename)")
                continue
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    body = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            if needle in body.lower():
                hits.append(rel)
    return hits


def check_retired(tree):
    """Check 6: the retired module is gone, name and all."""
    problems = []
    if os.path.exists(os.path.join(tree, RETIRED_REL)):
        problems.append("retired module still present: %s" % RETIRED_REL)
    hits = scan_hits(tree, RETIRED_STEM)
    if hits:
        problems.append("retired module name still referenced in %d file(s): %s"
                        % (len(hits), ", ".join(hits[:8])))
    return problems


def check_single_index_rel(tree):
    """Check 7: the derived index's filename has exactly one definition."""
    lib = os.path.join(tree, LIB_REL)
    sites = []
    for base, dirs, names in os.walk(lib):
        dirs[:] = sorted(d for d in dirs if d not in SCAN_SKIP_DIRS)
        for name in sorted(n for n in names if n.endswith(".py")):
            path = os.path.join(base, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    lines = fh.read().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(lines, 1):
                if line.startswith("INDEX_REL = "):
                    sites.append("%s:%d" % (os.path.relpath(path, tree), i))
    if len(sites) != 1:
        return ["the derived index filename must be defined exactly once, "
                "found %d: %s" % (len(sites), ", ".join(sites) or "none")]
    return []


CHECKS = (
    ("guard", check_guard),
    ("wiring", check_wiring),
    ("clobber", check_clobber_table),
    ("retired", check_retired),
    ("one_definition", check_single_index_rel),
)


def check(tree):
    problems = []
    for name, fn in CHECKS:
        problems.extend("[%s] %s" % (name, p) for p in fn(tree))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", help="tree carrying scripts/agentware_lib")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    tree, problems = resolve_tree(args.tree)
    if not problems:
        problems = check(tree)
    if args.json:
        print(json.dumps({"tree": tree, "ok": not problems,
                          "problems": problems}, indent=2))
    else:
        print("tree: %s" % (tree or "(none)"))
        for p in problems:
            print("PROBLEM %s" % p)
        print("%s %d problem(s)" % ("FAIL" if problems else "OK", len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
