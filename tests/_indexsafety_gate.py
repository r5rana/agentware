"""The INDEX WRITE-SAFETY gate — atomicity, mutual exclusion, and self-healing.

Plan task 22 lands four guarantees in the modular index before any verb is ported
onto it. Every one of them fails SILENTLY, and none is visible to the surface,
parity, manifest or ceiling gates — which is precisely why they need a gate of
their own:

  * ATOMIC + DURABLE write — `save_index` stages into a sibling temp file, fsyncs,
    and `os.replace`s. Without it a concurrent reader (recall, the dream, the
    dashboard, autocommit) can observe a truncated index, and a crash destroys it
    rather than leaving it merely stale.
  * MUTUAL EXCLUSION — atomicity is NOT exclusion. Two agents that each `load ->
    mutate -> save` leave only the second's entry, and the first's learning is
    simply gone from recall. The lock must therefore wrap the whole read-modify-
    write, in the CALLER, because the lost-update window opens at the READ.
  * AUTO-REBUILD-ONCE — index.json is derived and gitignored, so a fresh clone has
    none and a corruption is recoverable from entry frontmatter. Both must
    self-heal, exactly once, without recursion.
  * FAULT-ISOLATING rebuild — one malformed or duplicate-id entry file must not
    abort the whole rebuild (which wrote NOTHING and staled the index for every
    reader). The offender is skipped and REPORTED.

## The load-bearing check is the WIRING one

Asserting that `_index_lock` EXISTS is nearly worthless — the donor tree has zero
`fcntl` across ~70 modules, and a lock nobody takes is indistinguishable from no
lock at all. So the substantive check classifies every `save_index` call site in
the tree and demands the right thing of each:

  * a READ-MODIFY-WRITE site (its function also calls `load_index`) MUST sit inside
    a `with ..._index_lock(...)` block;
  * a CREATE site (saves without loading) must be saving a fresh DICT LITERAL —
    that is what makes it safe unlocked, and it stops "no load_index call" from
    becoming a loophole for an unlocked mutation.

Coverage is computed by REACHABILITY, not by pinning a function name: a site whose
own function is unlocked is still covered when EVERY caller wraps it in the lock.
That is deliberate — task 21 recorded that a gate pinning a filename or a single
function silently disarms itself the moment the code is decomposed, and it goes
GREEN rather than red. A helper split out of a locked handler must keep passing;
an orphan helper with no caller at all must fail.

Stdlib only, read-only, deterministic, py3.7-safe. Never imports the code it
measures — everything here is `ast` over source text.
"""

import ast
import os

HERE = os.path.dirname(os.path.realpath(__file__))

# The tree under measurement. Defaults to the checkout this file lives in; the
# override lets the gate run FROM the package repo AGAINST the staged merge tree
# during the migration, which is the only window in which the two differ. Same
# idiom as `_ceiling_gate`'s AGENTWARE_CEILING_ROOT.
REPO_ROOT = os.environ.get("AGENTWARE_INDEXSAFETY_ROOT") or os.path.dirname(HERE)
LIB_DIR = os.path.join(REPO_ROOT, "scripts", "agentware_lib")

INDEX_PKG = os.path.join(LIB_DIR, "knowledge", "index")
LOCK_MODULE = os.path.join(INDEX_PKG, "indexlock.py")
WRITE_MODULE = os.path.join(INDEX_PKG, "indexwrite.py")
READ_MODULE = os.path.join(INDEX_PKG, "indexread.py")
REBUILD_MODULE = os.path.join(INDEX_PKG, "indexrebuild.py")
KBLAYOUT_MODULE = os.path.join(LIB_DIR, "core", "kblayout.py")

LOCK_NAME = "_index_lock"
SAVE_NAME = "save_index"
LOAD_NAME = "load_index"


def lib_present():
    """True once the modular core exists in the tree under measurement."""
    return os.path.isdir(LIB_DIR)


def _rel(path):
    return os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def lib_files():
    """Every .py under the lib, sorted, repo-relative-stable."""
    out = []
    for dirpath, dirnames, files in os.walk(LIB_DIR):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for fn in sorted(files):
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return out


# --- primitive detection ------------------------------------------------------
def _called_names(node):
    """Every simple name invoked under `node`, whether bare or attribute-qualified."""
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def _attr_chain(node):
    """`os.replace` -> 'os.replace'; a bare name -> itself; else ''."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _funcs(tree):
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def atomic_write_errors():
    """`save_index` stages, fsyncs, replaces — and never writes THROUGH the index."""
    errs = []
    if not os.path.exists(WRITE_MODULE):
        return ["missing module: %s" % _rel(WRITE_MODULE)]
    tree = _parse(WRITE_MODULE)
    fns = _funcs(tree)
    save = fns.get(SAVE_NAME)
    if save is None:
        return ["%s does not define %s" % (_rel(WRITE_MODULE), SAVE_NAME)]
    # The three primitives may live in save_index itself or in a helper it calls
    # (a decomposition must not disarm this) — so resolve across the module.
    reachable = set()
    pending, seen = [save], set()
    while pending:
        fn = pending.pop()
        if fn.name in seen:
            continue
        seen.add(fn.name)
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Call):
                chain = _attr_chain(sub.func)
                if chain:
                    reachable.add(chain)
                tgt = fns.get(chain.split(".")[-1])
                if tgt is not None and tgt is not fn:
                    pending.append(tgt)
    for needed in ("tempfile.mkstemp", "os.fsync", "os.replace"):
        if needed not in reachable:
            errs.append("%s: %s is not reached from %s (atomic durable write)"
                        % (_rel(WRITE_MODULE), needed, SAVE_NAME))
    # The destination must never be opened for writing: that is the truncate-then-
    # stream shape the atomic write replaces, and it is what tears a reader.
    for sub in ast.walk(save):
        if isinstance(sub, ast.Call) and _attr_chain(sub.func) in ("open",):
            for arg in list(sub.args[1:]) + [k.value for k in sub.keywords
                                             if k.arg == "mode"]:
                if isinstance(arg, ast.Constant) and "w" in str(arg.value):
                    errs.append("%s: %s opens a path for writing directly — the "
                                "index must only ever be replaced, never truncated"
                                % (_rel(WRITE_MODULE), SAVE_NAME))
    return errs


def lock_primitive_errors():
    """The lock module exists and carries all three of its claimed properties."""
    errs = []
    if not os.path.exists(LOCK_MODULE):
        return ["missing module: %s (the same-host index mutation lock)"
                % _rel(LOCK_MODULE)]
    with open(LOCK_MODULE, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src, filename=LOCK_MODULE)
    fns = _funcs(tree)
    for name in (LOCK_NAME, "_index_lock_path"):
        if name not in fns:
            errs.append("%s does not define %s" % (_rel(LOCK_MODULE), name))
    # cross-PROCESS half
    for needed in ("flock", "LOCK_EX", "LOCK_UN"):
        if needed not in src:
            errs.append("%s: no %s — the lock does not serialize two PROCESSES"
                        % (_rel(LOCK_MODULE), needed))
    # cross-THREAD half: flock is per-open-file-description and cannot do this.
    if "threading.RLock()" not in src:
        errs.append("%s: no threading.RLock — flock alone does NOT serialize two "
                    "threads in one process holding different fds"
                    % _rel(LOCK_MODULE))
    # reentrancy: the depth counter that keeps rebuild_kb-under-load_index from
    # self-deadlocking on a second LOCK_EX.
    if '"depth"' not in src:
        errs.append("%s: no depth counter — a reentrant acquisition would take a "
                    "second LOCK_EX on a new fd and block on itself"
                    % _rel(LOCK_MODULE))
    # best-effort degrade: a lock problem may never be why a knowledge write fails.
    if "ImportError" not in src:
        errs.append("%s: the fcntl import is not guarded — the package must still "
                    "import where flock is unavailable" % _rel(LOCK_MODULE))
    lock_fn = fns.get(LOCK_NAME)
    if lock_fn is not None:
        decos = {_attr_chain(d) for d in lock_fn.decorator_list}
        if "contextlib.contextmanager" not in decos:
            errs.append("%s: %s is not a context manager — a lock released by "
                        "hand leaks on every exception path"
                        % (_rel(LOCK_MODULE), LOCK_NAME))
        has_finally = any(isinstance(s, ast.Try) and s.finalbody
                          for s in ast.walk(lock_fn))
        if not has_finally:
            errs.append("%s: %s has no finally — a raising body would leave the "
                        "lock held and hang the next writer"
                        % (_rel(LOCK_MODULE), LOCK_NAME))
    return errs


def recovery_errors():
    """`load_index` self-heals a missing/corrupt derived index, exactly once."""
    errs = []
    if not os.path.exists(READ_MODULE):
        return ["missing module: %s" % _rel(READ_MODULE)]
    fns = _funcs(_parse(READ_MODULE))
    load = fns.get(LOAD_NAME)
    if load is None:
        return ["%s does not define %s" % (_rel(READ_MODULE), LOAD_NAME)]
    args = [a.arg for a in load.args.args]
    if "_allow_rebuild" not in args:
        errs.append("%s: %s has no _allow_rebuild guard — recovery could recurse"
                    % (_rel(READ_MODULE), LOAD_NAME))
    if "_try_rebuild_missing_index" not in _called_names(load):
        errs.append("%s: %s never attempts a rebuild — a fresh clone (no derived "
                    "index) is bricked on its FIRST command"
                    % (_rel(READ_MODULE), LOAD_NAME))
    recover = fns.get("_try_rebuild_missing_index")
    if recover is None:
        errs.append("%s does not define _try_rebuild_missing_index"
                    % _rel(READ_MODULE))
    else:
        handlers = [h for n in ast.walk(recover) if isinstance(n, ast.Try)
                    for h in n.handlers]
        if not handlers:
            errs.append("%s: _try_rebuild_missing_index does not swallow a failing "
                        "rebuild — recovery must never crash the caller"
                        % _rel(READ_MODULE))
    return errs


# Load sites whose verb REPORTS ON or JUDGES index health. Auto-rebuild-once is a
# recovery for ordinary read paths (a fresh clone must not be bricked), but at these
# sites it is a defect: the verb would silently repair the corruption it exists to
# surface, write into a directory it is only inspecting, and report a pass over a
# finding. Discovered the hard way — adding recovery without these opt-outs turned
# `attach`'s corrupt-index REFUSAL into an acceptance and `features` into a renderer
# of an index the operator never had.
READONLY_LOAD_SITES = (
    ("knowledge/attach.py", "attach_conformance_report"),
    ("knowledge/curation/curationfeatures.py", "cmd_features"),
    # `fleet` reports on committed terminal EVENTS and declares itself read-only
    # (INV-2). Its index read is a tag histogram, so the recovery would write
    # index.json as a side effect of a report — and make its own documented
    # "degrades when there is no index yet" contract false.
    ("ops/fleet/fleetreport.py", "_fleet_top_learning_tags"),
)


def readonly_load_errors():
    """Every read-only/judging load site opts OUT of load_index's auto-rebuild."""
    errs = []
    for rel, fname in READONLY_LOAD_SITES:
        path = os.path.join(LIB_DIR, *rel.split("/"))
        if not os.path.exists(path):
            errs.append("missing module: %s" % _rel(path))
            continue
        fn = _funcs(_parse(path)).get(fname)
        if fn is None:
            errs.append("%s: %s is gone — the read-only load-site rule can no "
                        "longer be checked here (re-point READONLY_LOAD_SITES)"
                        % (_rel(path), fname))
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call) or \
                    _attr_chain(node.func).split(".")[-1] != LOAD_NAME:
                continue
            kw = {k.arg for k in node.keywords}
            if "_allow_rebuild" not in kw:
                errs.append(
                    "%s:%d %s() loads the index WITHOUT _allow_rebuild=False — a "
                    "read-only verb must never trigger the auto-rebuild, or it "
                    "repairs the corruption it exists to report and writes into a "
                    "directory it is only inspecting"
                    % (_rel(path), node.lineno, fname))
    return errs


def quarantine_errors():
    """The rebuild is fault-isolating: skip + REPORT, never abort wholesale."""
    errs = []
    if not os.path.exists(REBUILD_MODULE):
        return ["missing module: %s" % _rel(REBUILD_MODULE)]
    with open(REBUILD_MODULE, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src, filename=REBUILD_MODULE)
    fns = _funcs(tree)
    build = fns.get("build_index_from_frontmatter")
    if build is None:
        errs.append("%s does not define build_index_from_frontmatter"
                    % _rel(REBUILD_MODULE))
    elif "duplicate id" not in ast.get_source_segment(src, build):
        errs.append("%s: build_index_from_frontmatter does not de-duplicate ids — "
                    "a colliding id makes `query --id` ambiguous and double-counts "
                    "the tag map" % _rel(REBUILD_MODULE))
    if "QUARANTINED" not in src:
        errs.append("%s: no QUARANTINED report — a skipped entry file must be "
                    "surfaced, never silently dropped" % _rel(REBUILD_MODULE))
    # The abort shape this replaces: `if <problems>: return None, [], <problems>`
    # straight after the build. If it is back, one bad file stales the whole index.
    for name in ("rebuild_kb", "_rebuild_kb_locked"):
        fn = fns.get(name)
        if fn is None:
            continue
        seg = ast.get_source_segment(src, fn) or ""
        if "if errors:" in seg and "return None, [], errors" in seg:
            errs.append("%s: %s aborts the whole rebuild on any frontmatter "
                        "problem — that writes NOTHING and stales the index for "
                        "every reader" % (_rel(REBUILD_MODULE), name))
    return errs


def gitignore_errors():
    """The lock sidecar is local run state and must never be committed."""
    if not os.path.exists(KBLAYOUT_MODULE):
        return ["missing module: %s" % _rel(KBLAYOUT_MODULE)]
    with open(KBLAYOUT_MODULE, "r", encoding="utf-8") as f:
        src = f.read()
    errs = []
    if "KB_INDEX_LOCK_REL" not in src:
        errs.append("%s: KB_INDEX_LOCK_REL is undefined — the lock filename must "
                    "have ONE definition shared with the .gitignore scaffolder"
                    % _rel(KBLAYOUT_MODULE))
        return errs
    if "KB_INDEX_LOCK_REL" not in src.split("KB_GITIGNORE_REQUIRED", 1)[-1][:400]:
        errs.append("%s: the lock sidecar is not in KB_GITIGNORE_REQUIRED — it "
                    "would be committed and pushed as if it were knowledge"
                    % _rel(KBLAYOUT_MODULE))
    return errs


# --- the load-bearing check: every mutation site is covered -------------------
def _lock_covered_ranges(fn):
    """Line ranges inside `fn` that a `with ..._index_lock(...)` block covers."""
    spans = []
    for node in ast.walk(fn):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            call = item.context_expr
            if isinstance(call, ast.Call) and \
                    _attr_chain(call.func).split(".")[-1] == LOCK_NAME:
                spans.append((node.body[0].lineno, node.end_lineno))
    return spans


def _covered(lineno, spans):
    return any(lo <= lineno <= hi for lo, hi in spans)


def _call_sites(trees, name):
    """[(file, lineno)] for every ast.Call invoking `name`, bare or qualified."""
    out = []
    for path, tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and \
                    _attr_chain(node.func).split(".")[-1] == name:
                out.append((path, node.lineno))
    return out


def _enclosing_func(tree, lineno):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.lineno <= lineno <= node.end_lineno:
            if best is None or node.lineno > best.lineno:
                best = node
    return best


def wiring_errors():
    """Every `save_index` site is either lock-covered or a fresh-literal CREATE."""
    errs = []
    trees = []
    for path in lib_files():
        try:
            trees.append((path, _parse(path)))
        except SyntaxError as exc:
            errs.append("%s: unparseable (%s)" % (_rel(path), exc))
    if errs:
        return errs

    # A function is a CALLER-LOCKED helper when every ast.Call to it, anywhere in
    # the lib, sits inside a lock block. Computed up front so a helper split out of
    # a locked handler keeps passing — the failure mode task 21 recorded is a gate
    # that pins one function and disarms itself on the next decomposition.
    def caller_locked(fname):
        sites = _call_sites(trees, fname)
        if not sites:
            return False  # an orphan helper is unreachable, not "safe"
        for path, lineno in sites:
            tree = dict(trees)[path]
            owner = _enclosing_func(tree, lineno)
            if owner is None or not _covered(lineno, _lock_covered_ranges(owner)):
                return False
        return True

    for path, tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            saves = [n for n in ast.walk(node) if isinstance(n, ast.Call)
                     and _attr_chain(n.func).split(".")[-1] == SAVE_NAME]
            if not saves:
                continue
            # The lock module's own definition site is not a mutation site.
            if os.path.abspath(path) == os.path.abspath(LOCK_MODULE):
                continue
            loads = LOAD_NAME in _called_names(node)
            spans = _lock_covered_ranges(node)
            for call in saves:
                if _covered(call.lineno, spans) or caller_locked(node.name):
                    continue
                if not loads and len(call.args) >= 2 and \
                        isinstance(call.args[1], ast.Dict):
                    continue  # CREATE: a fresh literal index, safe unlocked
                errs.append(
                    "%s:%d %s() mutates the index outside %s() — a concurrent "
                    "writer's entry is silently LOST (atomicity is not exclusion; "
                    "the window opens at the read)"
                    % (_rel(path), call.lineno, node.name, LOCK_NAME))
    return errs


def all_errors():
    """Every check, in a stable order. Empty list == the gate is green."""
    out = []
    out += lock_primitive_errors()
    out += atomic_write_errors()
    out += recovery_errors()
    out += readonly_load_errors()
    out += quarantine_errors()
    out += gitignore_errors()
    out += wiring_errors()
    return out


if __name__ == "__main__":  # pragma: no cover - manual invocation
    import sys
    if not lib_present():
        print("indexsafety: %s absent — gate inert" % _rel(LIB_DIR))
        raise SystemExit(0)
    problems = all_errors()
    for p in problems:
        print("FAIL %s" % p)
    print("indexsafety: %d problem(s) over %d lib file(s)"
          % (len(problems), len(lib_files())))
    sys.exit(1 if problems else 0)
