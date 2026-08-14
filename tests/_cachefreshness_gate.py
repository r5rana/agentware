"""The CACHE-FRESHNESS gate — content is the authority, and cheap tiers stay cheap.

Plan task 24 settles one of the seven unions the plan declared logically
impossible: two trees keyed the derived BM25 postings cache differently, and only
one of them can survive. This tree stamped a monotonic `generation` counter into
index.json and the cache and compared the integers; the donor stored a
whole-corpus content fingerprint. The adjudication is recorded — the FINGERPRINT
is the authority — because a counter only orders the writes of ONE writer: the
moment an index authored elsewhere arrives (a pull, a restored backup, a
teammate's shard), its counter belongs to an unrelated sequence and two genuinely
different corpora that happen to share a number read as FRESH.

Everything in this area fails silently, which is why it needs a gate rather than
a test. A cache wrongly believed fresh raises nothing, warns nothing and changes
no exit code: `recall` just answers from yesterday's corpus, and the only way to
notice is to already know what the knowledge base now contains.

What is gated, and why each one is load-bearing
----------------------------------------------
  * FOREIGN-SCHEMA REJECTION — both pre-merge trees wrote to the SAME
    `.cache/bm25-postings.json`, this tree at version 2 and the donor at version
    1. Reusing either number lets a foreign schema load as valid. The merged
    version must therefore be one NEITHER tree used.
  * THE COUPLING — the O(1) fast path trusts a fingerprint stored IN index.json,
    which is valid ONLY immediately after a rebuild. So every non-rebuild write
    must DROP it, and exactly one writer (rebuild) may keep or set it. The fast
    path and the drop are one mechanism; either alone is a defect, and the half
    that is missing is invisible from the other's tests.
  * TIER ORDER — the three freshness tiers are cheap-to-expensive (stored
    fingerprint, then a stat-only signature, then the whole-corpus hash), and
    each may only ever ADVANCE to a more expensive check. Reordering them is a
    silent performance regression: the confirmed one it replaced took recall from
    0.01s to 0.20s by re-reading and sha256ing every body on EVERY query.
  * ZERO BODY READS — the stat proxy's whole point is that it opens nothing. One
    `open()` inside it and the short-circuit costs what it was meant to save,
    with no test anywhere turning red.
  * ATOMIC WRITE — a truncating write to the real cache file lets a lock-free
    concurrent recall read a half-written cache, and destroys it outright on a
    crash instead of leaving it merely stale.

The gate reads the tree it measures with `ast` and never imports it, so it runs
identically against this checkout and (via AGENTWARE_CACHEFRESH_ROOT) against the
staged merge tree before the core lands. Stdlib only, read-only, deterministic.
"""

import ast
import os

HERE = os.path.dirname(os.path.realpath(__file__))

# The tree under measurement. Same idiom as _indexsafety_gate/_indexreader_gate:
# the override is what lets the gate run FROM the package repo AGAINST the staged
# merge tree during the migration, which is the only window in which they differ.
REPO_ROOT = os.environ.get("AGENTWARE_CACHEFRESH_ROOT") or os.path.dirname(HERE)
LIB_DIR = os.path.join(REPO_ROOT, "scripts", "agentware_lib")

BM25_MODULE = os.path.join(LIB_DIR, "retrieval", "bm25.py")
WRITE_MODULE = os.path.join(LIB_DIR, "knowledge", "index", "indexwrite.py")
REBUILD_MODULE = os.path.join(LIB_DIR, "knowledge", "index", "indexrebuild.py")

#: The two numbers already burned at this cache path — this tree's v2
#: (generation-keyed) and the donor's v1 (fingerprint-only, no stat signature).
RETIRED_CACHE_VERSIONS = (1, 2)

VERSION_CONST = "BM25_CACHE_VERSION"
FINGERPRINT_KEY = "corpus_fingerprint"   # the index.json key (the authority)
CACHE_FP_KEY = "fingerprint"             # the cache's own copy of it
STAT_KEY = "stat_signature"              # the tier-2 zero-body-read proxy
RETIRED_COUNTER = "generation"           # the authority this replaces

FRESH_FN = "bm25_cache_is_fresh"
STAT_FN = "_corpus_stat_signature"
BUILD_FN = "build_bm25_cache"
LOAD_FN = "load_bm25_cache"
WRITE_FN = "write_bm25_cache"
SAVE_FN = "save_index"
PRESERVE_KW = "preserve_fingerprint"

#: Names that mean "this reads entry bodies". The stat proxy may call none.
BODY_READERS = ("open", "build_corpus", "eval_corpus_fingerprint",
                "_read_entry_body", "read_text")


def lib_present():
    """True once the modular core exists in the tree under measurement."""
    return os.path.isdir(LIB_DIR)


def _rel(path):
    return os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def lib_files():
    """Every .py under the lib, sorted and stable."""
    out = []
    for dirpath, dirnames, files in os.walk(LIB_DIR):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for fn in sorted(files):
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return out


# --- small ast helpers --------------------------------------------------------
def _functions(tree):
    """Top-level (and nested) function defs by name -> node."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, node)
    return out


def _called_names(node):
    """Every simple name invoked under `node`, bare or attribute-qualified."""
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def _str_constants(node):
    """Every string constant appearing anywhere under `node`."""
    return {s.value for s in ast.walk(node)
            if isinstance(s, ast.Constant) and isinstance(s.value, str)}


def _first_line(node, predicate):
    """Line number of the first sub-node satisfying `predicate`, else None."""
    hits = [getattr(sub, "lineno", None) for sub in ast.walk(node)
            if predicate(sub)]
    hits = [h for h in hits if h is not None]
    return min(hits) if hits else None


def _calls_named(name):
    def _p(sub):
        if not isinstance(sub, ast.Call):
            return False
        f = sub.func
        return ((isinstance(f, ast.Name) and f.id == name)
                or (isinstance(f, ast.Attribute) and f.attr == name))
    return _p


def _mentions(text):
    def _p(sub):
        return isinstance(sub, ast.Constant) and sub.value == text
    return _p


def _key_uses(tree):
    """Every string used AS A KEY: `x[k]`, `x.get(k, ...)`, or a dict literal key.

    Detection boundary, written down so it is not re-litigated: this matches a
    CONSTANT key only. A key held in a variable is invisible here, deliberately —
    resolving it needs dataflow, and a gate that guesses produces false positives
    nobody trusts and then disables. The residue is covered from the other side by
    the behavioural probe (a coincident counter must not read as fresh).
    """
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                keys.add(sl.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and node.args:
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                keys.add(a0.value)
        elif isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


# --- the checks ---------------------------------------------------------------
def check_version(problems):
    """The merged cache version must be one NEITHER pre-merge tree used."""
    tree = _parse(BM25_MODULE)
    value = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == VERSION_CONST
                for t in node.targets):
            if isinstance(node.value, ast.Constant):
                value = node.value.value
    if value is None:
        problems.append("%s: %s is not assigned a literal"
                        % (_rel(BM25_MODULE), VERSION_CONST))
        return
    if not isinstance(value, int):
        problems.append("%s: %s must be an int, got %r"
                        % (_rel(BM25_MODULE), VERSION_CONST, value))
        return
    if value in RETIRED_CACHE_VERSIONS:
        problems.append(
            "%s: %s == %d is a RETIRED version — a cache written by the other "
            "pre-merge tree at the same path would load as valid"
            % (_rel(BM25_MODULE), VERSION_CONST, value))
    loader = _functions(_parse(BM25_MODULE)).get(LOAD_FN)
    if loader is None:
        problems.append("%s: %s is missing" % (_rel(BM25_MODULE), LOAD_FN))
    elif VERSION_CONST not in {n.id for n in ast.walk(loader)
                               if isinstance(n, ast.Name)}:
        problems.append("%s: %s does not compare against %s — a foreign-schema "
                        "cache is accepted"
                        % (_rel(BM25_MODULE), LOAD_FN, VERSION_CONST))


def check_authority(problems):
    """Content decides freshness, and the retired counter is gone tree-wide."""
    fresh = _functions(_parse(BM25_MODULE)).get(FRESH_FN)
    if fresh is None:
        problems.append("%s: %s is missing" % (_rel(BM25_MODULE), FRESH_FN))
        return
    consts = _str_constants(fresh)
    for key in (FINGERPRINT_KEY, CACHE_FP_KEY):
        if key not in consts:
            problems.append("%s: %s never reads %r — content is not the "
                            "freshness authority"
                            % (_rel(BM25_MODULE), FRESH_FN, key))
    for path in lib_files():
        if RETIRED_COUNTER in _key_uses(_parse(path)):
            problems.append(
                "%s: uses the RETIRED %r index key — the monotonic counter is "
                "stale-blind to an index authored elsewhere and was adjudicated "
                "out; content is the authority" % (_rel(path), RETIRED_COUNTER))


def check_coupling(problems):
    """The fast path is only safe because every non-rebuild write drops the key."""
    save = _functions(_parse(WRITE_MODULE)).get(SAVE_FN)
    if save is None:
        problems.append("%s: %s is missing" % (_rel(WRITE_MODULE), SAVE_FN))
        return
    defaults = dict(zip([a.arg for a in save.args.args][-len(save.args.defaults):]
                        if save.args.defaults else [],
                        save.args.defaults))
    if PRESERVE_KW not in defaults:
        problems.append("%s: %s has no `%s` parameter with a default — the drop "
                        "is not opt-out-only"
                        % (_rel(WRITE_MODULE), SAVE_FN, PRESERVE_KW))
    else:
        d = defaults[PRESERVE_KW]
        if not (isinstance(d, ast.Constant) and d.value is False):
            problems.append("%s: %s's `%s` must default to False — dropping is "
                            "the SAFE default"
                            % (_rel(WRITE_MODULE), SAVE_FN, PRESERVE_KW))
    guard_line = None
    for sub in ast.walk(save):
        if isinstance(sub, ast.If) \
                and PRESERVE_KW in {n.id for n in ast.walk(sub.test)
                                    if isinstance(n, ast.Name)} \
                and FINGERPRINT_KEY in _str_constants(sub):
            guard_line = sub.lineno if guard_line is None else min(guard_line,
                                                                   sub.lineno)
    if guard_line is None:
        problems.append(
            "%s: %s never drops %r on a non-rebuild write — the O(1) fast path "
            "would trust a cache that predates the write, and recall would miss "
            "the just-written entry"
            % (_rel(WRITE_MODULE), SAVE_FN, FINGERPRINT_KEY))
    else:
        write_line = _first_line(save, _calls_named("mkstemp"))
        if write_line is not None and guard_line > write_line:
            problems.append("%s: %s drops %r AFTER staging the write"
                            % (_rel(WRITE_MODULE), SAVE_FN, FINGERPRINT_KEY))
    _check_sole_writer(problems)


def _check_sole_writer(problems):
    """Exactly one module may preserve, and exactly one may SET, the fingerprint."""
    preservers, setters = [], []
    for path in lib_files():
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords or []:
                    if kw.arg == PRESERVE_KW and isinstance(kw.value, ast.Constant) \
                            and kw.value.value is True:
                        preservers.append(_rel(path))
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Subscript) \
                            and isinstance(t.slice, ast.Constant) \
                            and t.slice.value == FINGERPRINT_KEY:
                        setters.append(_rel(path))
    rebuild = _rel(REBUILD_MODULE)
    for label, found in (("preserve %s=True" % PRESERVE_KW, sorted(set(preservers))),
                         ("assign %r" % FINGERPRINT_KEY, sorted(set(setters)))):
        if found != [rebuild]:
            problems.append(
                "sole-writer violated: %s expected only in %s, found in %s — a "
                "second writer can stamp a fingerprint the postings were never "
                "built from" % (label, rebuild, found or ["nowhere"]))


def check_tiers(problems):
    """Cheap before expensive: stored fingerprint -> stat signature -> hash."""
    fresh = _functions(_parse(BM25_MODULE)).get(FRESH_FN)
    if fresh is None:
        return  # already reported by check_authority
    tier1 = _first_line(fresh, _mentions(FINGERPRINT_KEY))
    tier2 = _first_line(fresh, _calls_named(STAT_FN))
    tier3 = _first_line(fresh, _calls_named("eval_corpus_fingerprint"))
    if tier2 is None:
        problems.append(
            "%s: %s never calls %s — with no stored fingerprint (a legacy index, "
            "or the window between a write and the next rebuild) EVERY recall "
            "re-reads and hashes the whole corpus"
            % (_rel(BM25_MODULE), FRESH_FN, STAT_FN))
    if tier3 is None:
        problems.append("%s: %s never falls back to the whole-corpus hash — a "
                        "stat proxy alone can be fooled"
                        % (_rel(BM25_MODULE), FRESH_FN))
    ordered = [("stored-fingerprint", tier1), ("stat-signature", tier2),
               ("corpus-hash", tier3)]
    seen = [(n, ln) for n, ln in ordered if ln is not None]
    for (an, al), (bn, bl) in zip(seen, seen[1:]):
        if al > bl:
            problems.append("%s: %s runs the %s tier before the cheaper %s tier"
                            % (_rel(BM25_MODULE), FRESH_FN, bn, an))


def check_stat_helper(problems):
    """The tier-2 proxy must open NOTHING — that is the entire point of it."""
    fns = _functions(_parse(BM25_MODULE))
    helper = fns.get(STAT_FN)
    if helper is None:
        problems.append("%s: %s is missing" % (_rel(BM25_MODULE), STAT_FN))
        return
    called = _called_names(helper)
    if "stat" not in called:
        problems.append("%s: %s never calls os.stat — it cannot be a stat proxy"
                        % (_rel(BM25_MODULE), STAT_FN))
    for bad in sorted(called & set(BODY_READERS)):
        problems.append(
            "%s: %s calls %s() — the short-circuit reads entry bodies and costs "
            "exactly what it exists to save"
            % (_rel(BM25_MODULE), STAT_FN, bad))


def check_cache_schema(problems):
    """The signature is stamped by the builder and REQUIRED by the loader."""
    fns = _functions(_parse(BM25_MODULE))
    build = fns.get(BUILD_FN)
    if build is None:
        problems.append("%s: %s is missing" % (_rel(BM25_MODULE), BUILD_FN))
    else:
        for key in (CACHE_FP_KEY, STAT_KEY):
            if key not in _str_constants(build):
                problems.append("%s: %s does not stamp %r into the cache"
                                % (_rel(BM25_MODULE), BUILD_FN, key))
        if STAT_FN not in _called_names(build):
            problems.append("%s: %s does not compute the signature in the SAME "
                            "call as the fingerprint — the two could describe "
                            "different states of the tree"
                            % (_rel(BM25_MODULE), BUILD_FN))
    loader = fns.get(LOAD_FN)
    if loader is not None:
        for key in (CACHE_FP_KEY, STAT_KEY):
            if key not in _str_constants(loader):
                problems.append("%s: %s does not require %r — a cache missing it "
                                "loads as valid"
                                % (_rel(BM25_MODULE), LOAD_FN, key))


def check_atomic_write(problems):
    """A torn cache must never be readable as a fresh one."""
    writer = _functions(_parse(BM25_MODULE)).get(WRITE_FN)
    if writer is None:
        problems.append("%s: %s is missing" % (_rel(BM25_MODULE), WRITE_FN))
        return
    called = _called_names(writer)
    for name in ("mkstemp", "fsync", "replace"):
        if name not in called:
            problems.append(
                "%s: %s does not call %s() — a truncating write lets a lock-free "
                "concurrent recall read a half-written cache, and destroys it on "
                "a crash rather than leaving it stale"
                % (_rel(BM25_MODULE), WRITE_FN, name))
    if "open" in called:
        problems.append("%s: %s calls open() directly — write via the staged "
                        "temp file (os.fdopen) and os.replace"
                        % (_rel(BM25_MODULE), WRITE_FN))


CHECKS = (check_version, check_authority, check_coupling, check_tiers,
          check_stat_helper, check_cache_schema, check_atomic_write)


def problems():
    """Every cache-freshness violation in the tree under measurement."""
    found = []
    missing = [p for p in (BM25_MODULE, WRITE_MODULE, REBUILD_MODULE)
               if not os.path.exists(p)]
    if missing:
        # A gate that passes by looking at nothing is worse than no gate: a
        # renamed or deleted module must FAIL rather than vacuously succeed.
        return ["missing module: %s" % _rel(p) for p in missing]
    for check in CHECKS:
        check(found)
    return found
