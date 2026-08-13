"""The SECURITY-INVARIANT POLICY TABLE — one rule per path class, whole tree.

Feature 260813 task 27. Before this module, INV-1 (no network on the ranking
path) and INV-6 (zero hard dependencies) were enforced by a **single-file import
walk**: `_imported_top_modules(CLI_PATH)`. That worked while `scripts/agentware`
WAS the toolkit. Task 25 turned it into a 110-line facade over
`scripts/agentware_legacy.py` plus ~115 modules under `scripts/agentware_lib/`,
and the walk kept passing — it was reading a docstring. The invariant that
"agentware installs nothing and phones home from no ranking path" was being
asserted over 0.3% of the code it claims to cover.

A wider walk alone is not the fix either, because the tree is NOT uniform. Three
`scripts/` sidecars exist precisely BECAUSE they may not obey the CLI's rules:
the dashboard is a separate process (a PACKAGE since task 69) so `http.server`
never lands in the entrypoint (learn-agentware-dashboard-is-a-subprocess-http-
sidecar), and the embedder backends are optional extension points importing
`fastembed` or a local model service. A single global rule would either
false-positive on them or be relaxed until it stops catching a real dependency.

So the rule is per PATH CLASS, and the load-bearing addition is COVERAGE: every
Python file on disk must be CLAIMED by some class. A file that matches no policy
is a failure, not a pass. That inverts the failure mode — a new module lands
UNGOVERNED and the gate says so, where a path-keyed scan would simply not look
at it and stay green. (`learn-a-check-that-cannot-fail-is-not-passing`.)

Pure and stdlib-only: every function here is a total function of paths and file
contents, so the gate's own semantics can be tested on synthetic inputs rather
than only through a multi-second real walk.
"""

import ast
import fnmatch
import os
import sys

try:
    from tests._fixtures import REPO_ROOT, first_party_modules
except ImportError:  # allow `python3 -m unittest tests.gates.test_invariants`
    from _fixtures import REPO_ROOT, first_party_modules


#: Modules whose presence means a file could reach the network or shell out to a
#: model service. `asyncio` is here because an event loop is the usual carrier.
NETWORK_MODULES = frozenset({
    "socket", "ssl", "urllib", "http", "ftplib", "telnetlib", "smtplib",
    "asyncio", "requests", "httpx", "aiohttp", "websocket", "websockets",
})

#: Directories never walked: version control, byte-code caches, vendored or
#: built trees. Kept SMALL and explicit — every name here is a hole in the
#: coverage assertion, so `tracked_python_files()` cross-checks the walk against
#: git so a skip can never silently narrow the corpus.
SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".eggs",
})


#: The preserved monolith. Named ONCE here because two rules key on it: the
#: `cli-legacy` class claims it, and that class declares it as the subject whose
#: retirement retires the class.
LEGACY_REL = "scripts/agentware_legacy.py"


class Policy(object):
    """One path class and the invariants that hold over it.

    `patterns` are matched with `fnmatch` against the repo-relative POSIX path.
    NOTE the semantics deliberately: fnmatch's `*` DOES cross `/`, so
    `scripts/agentware_lib/*.py` claims the package recursively. That is the
    intent everywhere below; anything narrower would leave a subpackage
    unclaimed, which the coverage assertion would then report.
    """

    __slots__ = ("name", "patterns", "stdlib_only", "no_network", "allow",
                 "why", "terminal_with")

    def __init__(self, name, patterns, stdlib_only, no_network, why,
                 allow=frozenset(), terminal_with=None):
        self.name = name
        self.patterns = tuple(patterns)
        self.stdlib_only = stdlib_only
        self.no_network = no_network
        self.allow = frozenset(allow)
        self.why = why
        #: A path whose RETIREMENT retires this whole class. A row declaring one
        #: is allowed to claim zero files once that path is gone -- and ONLY
        #: then. Without it, "this rule guards nothing because its subject was
        #: deliberately deleted" and "this rule guards nothing because someone
        #: renamed a directory" are the same silent state, which is the failure
        #: `dead_policies` exists to prevent.
        self.terminal_with = terminal_with

    def grants_exemption(self):
        """True when this class LOOSENS something relative to the CLI contract.

        The safety property for a retired row: a class that only ever tightened
        cannot mask a finding if its path reappears, because every rule it
        carries is the strictest one available. A class that exempts (a sidecar,
        a fixture corpus, an `allow` entry) is the opposite -- were it to go
        dead and its path return, the return would be silently un-scanned.
        """
        return not (self.stdlib_only and self.no_network) or bool(self.allow)

    def matches(self, rel_path):
        return any(fnmatch.fnmatch(rel_path, p) for p in self.patterns)

    def __repr__(self):  # pragma: no cover - diagnostics only
        return "<Policy %s stdlib_only=%s no_network=%s>" % (
            self.name, self.stdlib_only, self.no_network)


#: ORDERED — first match wins, so a specific class must precede the general one
#: it sits inside (the dashboard tests before `tests/test_*.py`, the swe fixture
#: corpus before every `tests/` rule).
POLICIES = (
    Policy(
        "cli-entrypoint", ("scripts/agentware",), True, True,
        "the installed entrypoint: INV-6 says a clone runs with a stock "
        "python3 and INV-1 says ranking never reaches the network."),
    Policy(
        "cli-legacy", (LEGACY_REL,), True, True,
        "the preserved monolith the facade execs — while it exists it IS the "
        "CLI body, so it carries the entrypoint's rules verbatim. TERMINAL: the "
        "shim retirement deletes it, and this row then claims nothing. That is "
        "declared rather than discovered (`terminal_with`) and it is INERT "
        "rather than merely unused — the row carries the two strictest rules "
        "and grants no `allow`, so deleting it changes no corpus and a file "
        "reappearing at this path would be scanned by exactly these rules "
        "anyway. `test_invariants` executes both halves of that claim.",
        terminal_with=LEGACY_REL),
    Policy(
        "cli-lib", ("scripts/agentware_lib/*.py",), True, True,
        "the decomposed CLI body: a dependency or a socket added to any module "
        "is a dependency or a socket added to the CLI."),
    Policy(
        "agent-hooks", ("scripts/hooks/*.py",), True, True,
        "hooks run on EVERY agent spawn on the operator's machine; a hook that "
        "needed installing would break the clone-and-go contract."),
    Policy(
        "dashboard-sidecar", ("scripts/agentware_dashboard/*.py",), True, False,
        "the read-only HTTP sidecar (a PACKAGE since task 69): a SEPARATE "
        "process, so http/socket never enter the CLI corpus. It binds loopback "
        "only and still imports stdlib exclusively.",
    ),
    Policy(
        "embedder-sidecar", ("scripts/agentware_embedder_*.py",), False, False,
        "optional embedding backends, loaded by dotted name and absent by "
        "default: they may import a third-party backend or reach a LOCAL model "
        "service. Nothing on the default path imports them.",
        allow=frozenset({"fastembed"})),
    Policy(
        "test-fixture-corpus", ("tests/fixtures/swe_tasks/*.py",), False, False,
        "synthetic task repositories fed to the SWE bench harness — data, not "
        "agentware code, and never imported by the package. Scoped to "
        "`swe_tasks/` DELIBERATELY and not to `tests/fixtures/*.py`: this is "
        "the only class besides the embedder sidecars exempt from BOTH rules, "
        "and fnmatch's `*` crosses `/`, so the wider pattern would silently "
        "hand full exemption to any future file dropped anywhere under "
        "fixtures/. Narrow, a stray fixture helper lands UNCLAIMED and the "
        "coverage assertion names it — failing closed, which is the whole "
        "point of the table."),
    Policy(
        "test-support", ("tests/_*.py", "tests/*/_*.py", "tests/__init__.py",
                         "tests/*/__init__.py"), True, True,
        "shared fixtures and gate helpers: they must run on a stock python3 in "
        "CI and on a fresh clone, exactly like the code they guard. Task 58 "
        "layered the corpus, so a helper now sits at `tests/<tier>/_x.py` as "
        "well as at the root — `tests/*/_*.py` claims both, because fnmatch's "
        "`*` crosses `/` (the same property the fixture-corpus rule above is "
        "narrowed against). The `/_` in the pattern is what keeps it off "
        "`test_*.py`, so widening this class cannot quietly swallow a test."),
    Policy(
        "test-loopback-client", ("tests/integration/test_dashboard.py",
                                 "tests/integration/test_dashboard_api.py"), True, False,
        "the only tests that drive the sidecar: they urllib-request the "
        "loopback port they just bound, which is the behaviour under test."),
    Policy(
        "test-optional-backend", ("tests/unit/retrieval/test_embedder_fastembed.py",), False,
        True,
        "exercises the optional backend behind a guarded import and skips when "
        "it is absent; it may name the third-party module it tests.",
        allow=frozenset({"fastembed"})),
    Policy(
        "tests", ("tests/test_*.py", "tests/*/test_*.py"), True, True,
        "the suite itself: a test that needed a pip install would make the "
        "zero-dependency claim untestable on a fresh clone."),
)


def _is_python_file(path, name):
    """True for a `.py` file OR an extension-less python script (by shebang).

    The entrypoint is `scripts/agentware` with no suffix. Sniffing the shebang
    rather than hardcoding that one name means the NEXT extension-less python
    script is discovered — and therefore has to be claimed — automatically.
    """
    if name.endswith(".py"):
        return True
    if "." in name:
        return False
    try:
        with open(path, "rb") as fh:
            first = fh.readline(128)
    except OSError:
        return False
    return first.startswith(b"#!") and b"python" in first


def discover_python_files(root=REPO_ROOT):
    """Every python source file in the tree, as sorted repo-relative paths."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if _is_python_file(full, name):
                found.append(os.path.relpath(full, root).replace(os.sep, "/"))
    return sorted(found)


def tracked_python_files(root=REPO_ROOT):
    """Git-tracked python files, or None when git is unavailable.

    The independent second enumeration. `SKIP_DIRS` is a list of places the walk
    does not look, and a skip that grows can silently shrink the guarded corpus
    — the same failure shape as a non-recursive glob. Comparing against git
    turns that into a red test instead of a quieter scan.
    """
    import subprocess
    try:
        proc = subprocess.run(["git", "-C", root, "ls-files", "-z"],
                              capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = []
    for rel in proc.stdout.decode("utf-8", "replace").split("\0"):
        if not rel:
            continue
        full = os.path.join(root, rel)
        if os.path.isfile(full) and _is_python_file(full,
                                                    os.path.basename(rel)):
            out.append(rel)
    return sorted(out)


def classify(rel_path):
    """The FIRST policy claiming `rel_path`, or None when nothing claims it."""
    for policy in POLICIES:
        if policy.matches(rel_path):
            return policy
    return None


def unclaimed(rel_paths):
    """The coverage assertion, as a pure function: what no policy claims."""
    return sorted(p for p in rel_paths if classify(p) is None)


def claimed_policy_names(rel_paths):
    """Names of the classes that actually claim at least one file."""
    claimed = set()
    for path in rel_paths:
        policy = classify(path)
        if policy is not None:
            claimed.add(policy.name)
    return claimed


def retired_policies(rel_paths):
    """Terminal classes whose declared subject is GONE from the corpus.

    Distinguished from `dead_policies` on purpose. Both describe a rule guarding
    an empty set, but they are different events: a retired class is empty
    because the file it was written for was deliberately deleted, which is the
    plan landing; a dead class is empty because a path moved underneath it,
    which is the gate quietly ceasing to check. Only the second is a failure,
    and separating them is the only way to say so.

    A terminal class whose subject still EXISTS is not retired, so if it claims
    nothing anyway (an earlier row shadowed it, the pattern rotted) it falls
    through to `dead_policies` and is reported — the declaration buys an
    exception for exactly one state, not a blanket one.
    """
    present = set(rel_paths)
    claimed = claimed_policy_names(rel_paths)
    return sorted(p.name for p in POLICIES
                  if p.terminal_with is not None
                  and p.terminal_with not in present
                  and p.name not in claimed)


def dead_policies(rel_paths):
    """Policy names claiming nothing — a rename or a deletion left a rule that
    guards an empty set, which reads as a pass at every surface.

    Excludes classes RETIRED by their declared terminal subject's removal (see
    `retired_policies`); those are checked separately, and for inertness rather
    than for emptiness.
    """
    claimed = claimed_policy_names(rel_paths)
    retired = set(retired_policies(rel_paths))
    return sorted(p.name for p in POLICIES
                  if p.name not in claimed and p.name not in retired)


def masking_policies(rel_paths):
    """Empty classes that would LOOSEN a rule — the one unsafe kind.

    The teeth behind tolerating a retired row at all. An empty row is only
    harmless if a file reappearing at its path would be governed no more weakly
    than it is now; a row that exempts something and claims nothing is a hole
    waiting for its path to come back, and nothing else in this module would
    report it. Must be empty in EVERY state, retired or not.
    """
    claimed = claimed_policy_names(rel_paths)
    return sorted(p.name for p in POLICIES
                  if p.name not in claimed and p.grants_exemption())


def overlaps(rel_paths):
    """`[(path, [class names in order])]` for files more than one class matches.

    `classify` is first-match-wins, so an overlap is resolved silently and the
    ORDER is the whole answer: `tests/fixtures/swe_tasks/x.py` is claimed by the
    fixture corpus only because that row precedes `tests/*/test_*.py`, whose `*`
    crosses `/`. Reordering those two rows would hand a fully-exempt class to
    the suite without changing a single pattern. Enumerating the overlaps lets a
    test pin the declared set, so a NEW ambiguity has to be argued for rather
    than absorbed by whichever row happens to sort first.
    """
    out = []
    for path in rel_paths:
        names = [p.name for p in POLICIES if p.matches(path)]
        if len(names) > 1:
            out.append((path, names))
    return out


def corpora_without(name, rel_paths):
    """The three corpora recomputed with class `name` removed — the INERTNESS
    probe, executed rather than argued.

    "This retired row is harmless" is a claim about what would change if it were
    deleted, so the honest way to check it is to delete it and compare. Returns
    `(unclaimed, guarded, network_free)` for the table minus that row; a caller
    asserts the triple is identical to the live one.
    """
    global POLICIES
    original = POLICIES
    try:
        POLICIES = tuple(p for p in original if p.name != name)
        return (unclaimed(rel_paths), guarded_corpus(rel_paths),
                network_free_corpus(rel_paths))
    finally:
        POLICIES = original


def imported_top_modules_from_source(source, filename="<source>"):
    """Top-level module names imported by `source` (pure AST walk, no import).

    Collects the first dotted component of every `import x.y` /
    `from x.y import z`. Relative imports are intra-package edges, not external
    dependencies, so they are ignored. Nested and function-local imports ARE
    collected — a lazily imported dependency is still a dependency
    (learn-agentware-import-guard-ast-walks-nested-imports).
    """
    tree = ast.parse(source, filename=filename)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                mods.add(node.module.split(".")[0])
    return mods


_IMPORT_CACHE = {}


def imported_top_modules(path):
    """`imported_top_modules_from_source` over a file on disk.

    Memoised on (path, mtime, size) for the process. Five test modules ask this
    question of the same ~350-file corpus; parsing the 34k-line legacy monolith
    once per caller is the difference between a 1.2s scan and a 6s one. The key
    carries mtime and size so a file rewritten mid-run (the coverage probe does
    exactly that) is re-read rather than served stale.
    """
    try:
        stat = os.stat(path)
        key = (path, stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = None
    if key is not None and key in _IMPORT_CACHE:
        return _IMPORT_CACHE[key]
    with open(path, "r", encoding="utf-8") as fh:
        mods = imported_top_modules_from_source(fh.read(), filename=path)
    if key is not None:
        _IMPORT_CACHE[key] = mods
    return mods


def first_party_names(root=REPO_ROOT):
    """Module names that SHIP IN THIS REPO — DERIVED from disk, never hardcoded.

    INV-6 is "zero hard DEPENDENCIES", not "zero imports": a name resolving to a
    file the clone already carries installs nothing and cannot go missing.
    Covers `scripts/` (via the shared `_fixtures` derivation) plus the test
    package's own top-level helpers, which the suite imports bare under the
    `except ImportError` sibling-import fallback.
    """
    names = set(first_party_modules())
    names.add("tests")
    tests_dir = os.path.join(root, "tests")
    try:
        entries = os.listdir(tests_dir)
    except OSError:
        entries = []
    for entry in entries:
        if entry.endswith(".py"):
            names.add(entry[:-3])
        elif os.path.isdir(os.path.join(tests_dir, entry)) and os.path.isfile(
                os.path.join(tests_dir, entry, "__init__.py")):
            names.add(entry)
    return names


def _stdlib_names():
    """The authoritative stdlib set, empty on interpreters that cannot say."""
    if sys.version_info < (3, 10):
        return set()
    return set(sys.stdlib_module_names) | set(sys.builtin_module_names)


def guarded_corpus(rel_paths):
    """Paths whose class carries the stdlib-only rule (the INV-6 corpus)."""
    out = []
    for path in rel_paths:
        policy = classify(path)
        if policy is not None and policy.stdlib_only:
            out.append(path)
    return sorted(out)


def fully_guarded_corpus(rel_paths):
    """Paths carrying BOTH rules — the corpus the CLI's own contract covers.

    The distinction matters for the sidecars: the dashboard is exempt from
    NO-NETWORK (it is an HTTP server) but still stdlib-only, while the embedder
    backends are exempt from both. "Exempt" is therefore per RULE, not per file,
    and a sidecar is never fully guarded.
    """
    out = []
    for path in rel_paths:
        policy = classify(path)
        if policy is not None and policy.stdlib_only and policy.no_network:
            out.append(path)
    return sorted(out)


def network_free_corpus(rel_paths):
    """Paths whose class carries the no-network rule (the INV-1 corpus)."""
    out = []
    for path in rel_paths:
        policy = classify(path)
        if policy is not None and policy.no_network:
            out.append(path)
    return sorted(out)


def guarded_import_surface(rel_paths=None, root=REPO_ROOT):
    """Union of top-level imports across the FULLY guarded corpus.

    "What does the always-on toolkit import?" asked once. Four sibling test
    modules used to ask it of `scripts/agentware` alone — which since task 25 is
    a facade that imports six stdlib names and nothing else, so each of those
    guards had gone vacuous in the same way and for the same reason. They ask it
    here now, so the corpus can never again be one stale path.
    """
    rel_paths = discover_python_files(root) if rel_paths is None else rel_paths
    mods = set()
    for path in fully_guarded_corpus(rel_paths):
        mods |= imported_top_modules(os.path.join(root, path))
    return mods


def guarded_import_offenders(rel_paths=None, root=REPO_ROOT, stdlib=None):
    """Non-stdlib, non-first-party imports across the fully guarded corpus.

    The ANSWER to "does the always-on toolkit depend on anything it does not
    ship?", computed once. Four sibling test modules each derived it themselves
    from `guarded_import_surface()` — every one carrying a comment saying the
    allowance was "defined ONCE, in tests/_security_policy.py", which was true of
    the corpus and the first-party set but not of the subtraction they all
    open-coded. That gap only became visible when the answer stopped being a
    property of names alone: `scripts/hooks/codex-stream.py` imports its sibling
    `codex_items`, which resolves via `sys.path[0]` and therefore depends on
    WHICH FILE the import appears in. A flattened union of module names cannot
    express that, so the subtraction has to happen per file — here, where the
    path is still in hand, rather than four times over a set that has already
    thrown the paths away.
    """
    rel_paths = discover_python_files(root) if rel_paths is None else rel_paths
    stdlib = _stdlib_names() if stdlib is None else stdlib
    if not stdlib:
        raise RuntimeError(
            "cannot enumerate the stdlib on python %d.%d — the import surface "
            "is UNCHECKABLE here, not clean"
            % (sys.version_info[0], sys.version_info[1]))
    allowed = set(stdlib) | first_party_names(root)
    offenders = set()
    for path in fully_guarded_corpus(rel_paths):
        siblings = sibling_modules(path, root)
        for mod in imported_top_modules(os.path.join(root, path)):
            if mod not in allowed and mod not in siblings:
                offenders.add(mod)
    return sorted(offenders)


def sibling_modules(rel_path, root=REPO_ROOT):
    """Module names a SCRIPT at `rel_path` can import because they sit next to it.

    Python puts a script's OWN directory on `sys.path[0]`, so `scripts/hooks/
    codex-stream.py` doing `from codex_items import ...` resolves to
    `scripts/hooks/codex_items.py` — a file the clone already carries. That is
    first-party by INV-6's own definition ("zero hard DEPENDENCIES, not zero
    imports"): it installs nothing, downloads nothing, and cannot go missing.

    Resolved PER FILE rather than folded into `first_party_names`, which is a
    single global set. Widening that set would excuse `import codex_items` from
    anywhere in the tree — including `agentware_lib`, where it would NOT resolve
    at runtime — turning a real broken-import defect into a silent pass. Scoped
    here, the allowance holds exactly where the interpreter would actually find
    the module, and nowhere else.

    `scripts/hooks/` cannot become a package instead: two of its modules carry
    hyphens (`codex-stream.py`, `render-transcript.py`) because they are spawned
    as executables, and a hyphen is not a legal identifier.
    """
    directory = os.path.dirname(os.path.join(root, rel_path))
    names = set()
    try:
        entries = os.listdir(directory)
    except OSError:
        return names
    for entry in entries:
        if entry.endswith(".py") and entry != os.path.basename(rel_path):
            stem = entry[:-3]
            if stem.isidentifier():      # a hyphenated script is not importable
                names.add(stem)
    return names


def dependency_violations(rel_paths, root=REPO_ROOT, stdlib=None,
                          first_party=None):
    """INV-6 findings: `[(path, policy, sorted non-stdlib module names)]`.

    Raises rather than returning `[]` when the interpreter cannot enumerate the
    stdlib (`sys.stdlib_module_names`, py3.10+). An empty finding list is what
    this function returns when everything is FINE, so returning it when the scan
    could not run would make "cannot check" indistinguishable from "checked and
    clean" — the exact degradation `learn-a-check-that-cannot-fail-is-not-
    passing` records. Callers gate on the interpreter version and skip loudly.
    """
    stdlib = _stdlib_names() if stdlib is None else stdlib
    if not stdlib:
        raise RuntimeError(
            "cannot enumerate the stdlib on python %d.%d — INV-6 is "
            "UNCHECKABLE here, not satisfied"
            % (sys.version_info[0], sys.version_info[1]))
    allowed = first_party_names(root) if first_party is None else first_party
    out = []
    for path in guarded_corpus(rel_paths):
        policy = classify(path)
        mods = imported_top_modules(os.path.join(root, path))
        siblings = sibling_modules(path, root)
        offenders = sorted(m for m in mods
                           if m not in stdlib and m not in allowed
                           and m not in policy.allow and m not in siblings)
        if offenders:
            out.append((path, policy.name, offenders))
    return out


def network_violations(rel_paths, root=REPO_ROOT):
    """INV-1 findings: `[(path, policy, sorted network module names)]`."""
    out = []
    for path in network_free_corpus(rel_paths):
        policy = classify(path)
        mods = imported_top_modules(os.path.join(root, path))
        used = sorted(mods & NETWORK_MODULES)
        if used:
            out.append((path, policy.name, used))
    return out


def format_findings(findings):
    """Render findings so the failure names the file, the class AND the rule."""
    lines = []
    for path, policy_name, mods in findings:
        lines.append("  %s [%s]: %s" % (path, policy_name, ", ".join(mods)))
    return "\n".join(lines)
