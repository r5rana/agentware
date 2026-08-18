"""agentware_lib.retrieval.regressiongate.abgate — the clean-HEAD vs working-tree
A/B gate (layer: retrieval).

Owns: the R-PKG-05 question as a deterministic check — "is THIS diff responsible for
the recall drop?" — by scoring the working-tree CLI and the git-HEAD CLI over the
SAME live corpus and PASSING iff working >= head - tolerance, which isolates a diff's
recall effect from live-KB corpus drift BY CONSTRUCTION. The pure verdict
(_ab_verdict); the read-only (INV-2) HEAD extraction of the WHOLE scripts/ tree via
`git archive HEAD scripts` (per-blob `git show` fallback) with a synthesized AGENTS.md
marker so the split package's marker-walk resolves (_extract_head_cli /
_extract_head_scripts / _synthesize_head_marker) and its guarded temp-dir cleanup
(_cleanup_head_cli); the extraction's COMPLETENESS contract — the HEAD listing that is
its authority (_head_scripts_listing), the pure check against it
(_verify_head_extraction) and the named failure it raises (HeadExtractionError); the
HEAD-CLI subprocess recall scorer (_ab_head_recall); the SINGLE assembler (run_ab_gate
-- ONE body serving both the live bare-name path and the transitional namespace-resolved
one through its `ns` parameter, because two bodies is exactly how the task-26 repair came
to be missing from the live path) and its printer (_print_ab). AND, transitionally while
the strangler facade stands, the namespace ADAPTER + installer that put this layout-aware
extraction in front of the preserved monolith's flat-blob one (_run_ab_gate_ns /
install_ab_extraction, feature 260813 task 26 — both deleted with the passthrough at
task 56).

Why the completeness contract is a separate thing from "did the extraction work":
`git archive`/`git show` can drop a blob and still leave an entrypoint on disk, and
the fallback loop `continue`s past a blob it cannot read. Under the modular layout a
dropped agentware_lib module leaves an entrypoint that cannot import its own package,
so the HEAD subprocess dies, _ab_head_recall returns None, and the arm reports
`available: False, passed: True` — byte-identical to "there is no git here". The
R-PKG-05 question would then be answered PASS, for every diff, having compared
nothing. Checking the materialised tree against the HEAD LISTING is what makes a
BROKEN arm distinguishable from an ABSENT one.

Does NOT own: the absolute INV-7 baseline gate and the shared exit-code
(agentware_lib.retrieval.regressiongate.gatebaseline — _gate_exit lives there and
takes this module's `ab` verdict as its second argument); the repo-root anchor
(REPO_ROOT -> agentware_lib.core.paths, imported as a BARE name so the A/B
integration test can monkeypatch it — after the split the live patch target is
`regressiongate.abgate.REPO_ROOT`, and every function here also accepts an explicit
`repo_root=` so a caller can pin it without patching at all); the git EXEC primitive
(_git / _git_capture -> agentware_lib.core.gitio via gitio); the today-stamp
(dates._today -> agentware_lib.core.dates); the eval CLI handlers
(agentware_lib.retrieval.evalcmd), which reach run_ab_gate / _print_ab through the
_regressiongate module alias by attribute.
Layer deps: retrieval -> core (agentware_lib.core.{paths,dates,gitio}). Imports
nothing in ops/features/knowledge; nothing in core/ or knowledge/ imports from here.

INV-2: read-only w.r.t. the work tree / index — the HEAD extraction only reads git
objects (`git archive` / `git show` / `git ls-tree`) and writes ONLY under a throwaway
`agentware-head-*` temp dir, cleaned up by a guarded rmtree that refuses any path not
directly under the system temp root.
"""

import json
import os
import shutil
import subprocess
import sys

from agentware_lib.core import dates
from agentware_lib.core import gitio
from agentware_lib.core.paths import REPO_ROOT


def _ab_verdict(working, head, tolerance):
    """Pure A/B decision: (passed, delta). PASS iff working >= head - tolerance."""
    delta = working - head
    return (working >= head - tolerance), delta

class HeadExtractionError(Exception):
    """The HEAD `scripts/` tree materialised INCOMPLETELY — named missing paths.

    Raised by _verify_head_extraction when a path git LISTS at HEAD did not land
    in the throwaway tree. It exists because the failure it describes is
    otherwise INVISIBLE: under the modular layout a dropped `agentware_lib`
    module leaves an entrypoint that cannot import its own package, the HEAD
    subprocess exits non-zero, `_ab_head_recall` returns None, and the arm
    reports `available: False, passed: True` — a GREEN gate that measured
    nothing. `.missing` carries the offending repo-relative paths so the verdict
    NAMES the module rather than shrugging (feature 260813 task 26).
    """

    def __init__(self, missing):
        self.missing = list(missing)
        Exception.__init__(
            self, "HEAD extraction incomplete: %d file(s) git lists at HEAD did "
                  "not materialise: %s" % (len(self.missing),
                                           ", ".join(self.missing[:5])))

def _head_scripts_listing(repo_root=None):
    """Repo-relative paths git lists under scripts/ at HEAD; [] when absent.

    The AUTHORITY for what a complete extraction contains: the extractor is
    checked against this listing, never against its own idea of success.
    """
    cp = gitio._git(repo_root or REPO_ROOT,
                    "ls-tree", "-r", "--name-only", "HEAD", "scripts")
    if cp.returncode != 0:
        return []
    return [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]

def _verify_head_extraction(tmpdir, listing):
    """True iff every path in `listing` materialised under tmpdir.

    Raises HeadExtractionError naming the missing paths otherwise. Pure over
    (tmpdir, listing) — no git, no subprocess — so it is directly testable.
    """
    missing = sorted(rel for rel in listing
                     if not os.path.exists(os.path.join(tmpdir, rel)))
    if missing:
        raise HeadExtractionError(missing)
    return True

def _synthesize_head_marker(tmpdir, repo_root=None):
    """Put a repo-root AGENTS.md marker in the throwaway tree. Temp dir only.

    The extracted tree carries no AGENTS.md of its own, and without it the split
    package's marker-walk paths.REPO_ROOT cannot resolve.
    """
    try:
        src_marker = os.path.join(repo_root or REPO_ROOT, "AGENTS.md")
        dst_marker = os.path.join(tmpdir, "AGENTS.md")
        if os.path.isfile(src_marker):
            shutil.copy2(src_marker, dst_marker)
        else:
            with open(dst_marker, "w", encoding="utf-8") as f:
                f.write("")
    except OSError:
        return False
    return True

def _extract_head_cli(repo_root=None):
    """Extract HEAD's scripts/ tree to a throwaway temp dir; return
    (entrypoint_path, head_commit). Read-only (INV-2).

    Extracts the WHOLE scripts/ tree (not just the entrypoint blob) so the split
    agentware_lib package sits next to the entrypoint and `import agentware_lib...`
    resolves under the extracted CLI's own sys.path bootstrap; a self-contained
    monolith HEAD extracts fine too. An AGENTS.md marker is synthesized in the
    temp dir so agentware_lib.core.paths' marker-walk REPO_ROOT resolution
    succeeds (the extracted tree carries no AGENTS.md of its own).

    Returns (None, None) when git/HEAD/the tree is unavailable — that state
    cannot prove a regression, so the gate keeps PASSING on it. It RAISES
    HeadExtractionError when git DID list a tree and the extraction dropped part
    of it: a broken arm is a failure, not an absent one.

    Writes ONLY under a throwaway temp dir — never the work tree or the index.
    The caller removes the temp dir via _cleanup_head_cli(entrypoint_path).
    """
    root = repo_root or REPO_ROOT
    cp = gitio._git(root, "rev-parse", "--short", "HEAD")
    head_commit = cp.stdout.strip() if cp.returncode == 0 else ""
    if not head_commit:
        return None, None
    listing = _head_scripts_listing(root)
    if not listing:
        return None, None
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="agentware-head-")
    entry = os.path.join(tmpdir, "scripts", "agentware")
    try:
        ok = _extract_head_scripts(tmpdir, root)
        if ok:
            ok = _synthesize_head_marker(tmpdir, root)
        if not ok or not os.path.isfile(entry):
            shutil.rmtree(tmpdir, ignore_errors=True)
            return None, None
        _verify_head_extraction(tmpdir, listing)
    except HeadExtractionError:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise
    return entry, head_commit

def _extract_head_scripts(tmpdir, repo_root=None):
    """Extract HEAD:scripts/ into <tmpdir>/scripts/. Return True on success.

    Prefers `git archive HEAD scripts | tar -x -C tmpdir` (single 'scripts'
    pathspec — extra pathspecs make git archive exit 128 on a scripts-only repo);
    falls back to a per-blob loop over `git ls-tree -r --name-only HEAD scripts`.
    Read-only: reads git objects, writes only under tmpdir.

    True here means "the entrypoint landed", NOT "the package is complete" — the
    fallback loop deliberately `continue`s past a blob it cannot read, and `tar`
    can report success on a short archive. Completeness is the separate,
    listing-checked job of _verify_head_extraction, which is what turns a dropped
    agentware_lib module into a NAMED failure instead of a silent one.
    """
    root = repo_root or REPO_ROOT
    try:
        archive = subprocess.run(
            ["git", "archive", "HEAD", "scripts"], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if archive.returncode == 0 and archive.stdout:
            tar = subprocess.run(
                ["tar", "-x", "-C", tmpdir], input=archive.stdout,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if tar.returncode == 0 and os.path.isfile(
                    os.path.join(tmpdir, "scripts", "agentware")):
                return True
    except OSError:
        pass
    listing = _head_scripts_listing(root)
    if not listing:
        return False
    got_entry = False
    for rel in listing:
        try:
            proc = subprocess.run(
                ["git", "show", "HEAD:%s" % rel], cwd=root,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError:
            return False
        if proc.returncode != 0:
            continue
        dest = os.path.join(tmpdir, rel)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(proc.stdout)
        except OSError:
            return False
        if rel == "scripts/agentware":
            got_entry = True
    return got_entry

def _cleanup_head_cli(head_path):
    """Remove the throwaway HEAD extraction created by _extract_head_cli.

    head_path is <tmpdir>/scripts/agentware. Removes the tmpdir ONLY when it is a
    genuine `agentware-head-*` dir directly under the system temp root — never a
    derived or foreign path (guards against mocked test paths and any accidental
    broad delete). Any other path falls back to a best-effort file unlink.
    """
    if not head_path:
        return
    import tempfile
    tmpdir = os.path.dirname(os.path.dirname(os.path.abspath(head_path)))
    base = os.path.basename(tmpdir)
    sys_tmp = os.path.realpath(tempfile.gettempdir())
    if (base.startswith("agentware-head-")
            and os.path.realpath(os.path.dirname(tmpdir)) == sys_tmp
            and os.path.isdir(tmpdir)):
        shutil.rmtree(tmpdir, ignore_errors=True)
        return
    try:
        os.unlink(head_path)
    except OSError:
        pass

def _ab_head_recall(head_path, kdir, strategy, top_k, as_of, gold_path):
    """Score the HEAD CLI's plain eval over the SAME corpus; return recall_at_k.

    Runs the extracted HEAD script as a subprocess (a plain read-only eval — NO
    --record/--gate, so the ledger is untouched), pinning AGENTWARE_KNOWLEDGE_DIR
    so it scores the IDENTICAL live corpus regardless of the temp file's location.
    Returns the float recall_at_k, or None if the HEAD run produced none.
    """
    env = dict(os.environ)
    env["AGENTWARE_KNOWLEDGE_DIR"] = kdir
    argv = [sys.executable, head_path, "eval",
            "--strategy", strategy, "--top-k", str(top_k),
            "--as-of", as_of, "--gold", gold_path, "--format", "json"]
    try:
        proc = subprocess.run(
            argv, cwd=REPO_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, env=env)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return None
    recall = (payload.get("metrics") or {}).get("recall_at_k")
    return recall if isinstance(recall, (int, float)) else None

def run_ab_gate(args, kdir, result, gold_path, ns=None):
    """Clean-HEAD vs working-tree A/B over the SAME corpus (read-only).

    Attaches `result["ab"]` and returns it. PASS iff working >= head - tolerance.
    An unavailable HEAD/git (or a HEAD run that yields no recall) PASSES with
    available=False — it cannot prove this diff caused a regression. A HEAD that
    git DID list but whose extraction came back INCOMPLETE is the opposite case:
    a BROKEN arm, which goes RED naming the missing modules (`error` / `missing`)
    rather than reporting the absent arm's silent `available: False, passed: True`.

    ONE body, two collaborator resolutions (`ns`):

      * `ns is None` — the LIVE path (`retrieval.evalcmd` -> here by attribute).
        `_extract_head_cli` / `_ab_head_recall` / `_ab_verdict` resolve out of
        THIS module's globals at call time, so patching `abgate.<name>` (the
        owning module, per this module's docstring) still reaches them.
      * `ns` given — the transitional facade path (`_run_ab_gate_ns` below).

    Why the resolution is a parameter instead of a second function: it WAS a
    second function, and the two bodies diverged. The HeadExtractionError arm —
    the entire task-26 repair — was written into the namespace-resolved copy only,
    so the copy the `eval` verb actually calls raised the exception out of
    `cmd_eval` instead of returning the RED verdict, and `_print_ab`'s broken-arm
    rendering was unreachable from the live path. The A/B tests could not see it:
    they read `CLI.run_ab_gate`, which the facade's post-exec install had rebound
    to the namespace copy. Two bodies for one behaviour is what made that
    survivable, so there is one body now.
    """
    if ns is None:
        _resolve, today = globals().__getitem__, dates._today
    else:
        _resolve, today = ns.__getitem__, ns["_today"]
    working = (result.get("metrics") or {}).get("recall_at_k")
    tolerance = args.tolerance
    strategy = result.get("strategy") or getattr(args, "strategy", "bm25")
    top_k = result.get("top_k") or args.top_k
    as_of = result.get("as_of") or today()
    base = {"working": working, "head": None, "delta": None,
            "tolerance": tolerance, "head_commit": None, "strategy": strategy}
    try:
        head_path, head_commit = _resolve("_extract_head_cli")()
    except HeadExtractionError as exc:
        ab = dict(base, available=False, passed=False, reason=str(exc),
                  error=str(exc), missing=list(exc.missing))
        result["ab"] = ab
        return ab
    if head_path is None:
        ab = dict(base, available=False, passed=True,
                  reason="git HEAD scripts/agentware unavailable")
        result["ab"] = ab
        return ab
    try:
        head = _resolve("_ab_head_recall")(head_path, kdir, strategy, top_k,
                                           as_of, gold_path)
    finally:
        _cleanup_head_cli(head_path)
    base["head_commit"] = head_commit
    if head is None or working is None:
        ab = dict(base, available=False, passed=True, head=head,
                  reason="HEAD eval produced no recall score")
        result["ab"] = ab
        return ab
    passed, delta = _resolve("_ab_verdict")(working, head, tolerance)
    ab = dict(base, available=True, passed=passed, head=head, delta=delta)
    result["ab"] = ab
    return ab

def _run_ab_gate_ns(ns, args, kdir, result, gold_path):
    """`run_ab_gate` with every collaborator resolved from a CONSUMER namespace.

    TRANSITIONAL (feature 260813 task 26); retired with the legacy passthrough at
    task 56, when `eval` is served from this module directly and run_ab_gate is
    reached by name again.

    A thin adapter, deliberately: it used to be a SECOND COPY of the assembler,
    and the copy is what let the task-26 repair miss the live path (see
    `run_ab_gate`). The reason the namespace resolution exists at all is
    unchanged — a function installed into the entrypoint facade still carries the
    LIB module dict as its `__globals__`, so a bare-name form would ignore a
    `CLI.<name> = ...` monkeypatch and the existing A/B unit tests would go on
    passing while exercising nothing (the write-seam failure recorded at task 25).
    """
    return run_ab_gate(args, kdir, result, gold_path, ns=ns)

def install_ab_extraction(ns):
    """Install the LAYOUT-AWARE A/B HEAD extraction over a legacy namespace.

    TRANSITIONAL (feature 260813 task 26; deleted with the legacy passthrough at
    task 56). The preserved monolith extracts a SINGLE flat `HEAD:scripts/agentware`
    blob and runs it. Under the modular layout that blob is a ~74-line facade with
    no `agentware_lib` and no `agentware_legacy` beside it, so the HEAD arm cannot
    even start: `_ab_head_recall` returns None and the A/B gate reports
    `available: False, passed: True`. The R-PKG-05 "is THIS diff responsible for
    the recall drop?" check would then pass, for every diff, having compared
    nothing — the facade breaking its own evaluation arm.

    Rebinding these names in the namespace the legacy body was exec'd INTO (see
    the entrypoint docstring: exec, not import, so the legacy callers' globals
    ARE this namespace) makes the arm materialise the whole `scripts/` tree.

    Returns `ns` so the caller can assert on it.
    """
    def _extract():
        # repo_root is read from ns at CALL time: the A/B integration test
        # monkeypatches CLI.REPO_ROOT to a throwaway git repo.
        return _extract_head_cli(repo_root=ns.get("REPO_ROOT"))

    def _run(args, kdir, result, gold_path):
        return _run_ab_gate_ns(ns, args, kdir, result, gold_path)

    ns["HeadExtractionError"] = HeadExtractionError
    ns["_head_scripts_listing"] = _head_scripts_listing
    ns["_verify_head_extraction"] = _verify_head_extraction
    ns["_synthesize_head_marker"] = _synthesize_head_marker
    ns["_extract_head_cli"] = _extract
    ns["_extract_head_scripts"] = _extract_head_scripts
    ns["_cleanup_head_cli"] = _cleanup_head_cli
    ns["run_ab_gate"] = _run
    ns["_print_ab"] = _print_ab
    return ns

def _print_ab(ab):
    """Human-readable A/B verdict for text format.

    An unavailable arm that is ALSO failing is a BROKEN arm (an incomplete HEAD
    extraction), and must never render with the same "PASS" word as an absent
    one — that equivalence is the whole defect task 26 closes.
    """
    if ab is None:
        return
    if not ab.get("available", True):
        if not ab.get("passed", True):
            print("  ab: FAIL (clean-HEAD A/B BROKEN: %s)"
                  % ab.get("reason", "n/a"))
            return
        print("  ab: PASS (clean-HEAD A/B unavailable: %s)"
              % ab.get("reason", "n/a"))
        return
    print("  ab: %s  working=%.4f  head=%.4f  delta=%+.4f  tol=%.4f  head=%s" % (
        "PASS" if ab["passed"] else "FAIL",
        ab["working"], ab["head"], ab["delta"], ab["tolerance"],
        ab.get("head_commit") or "?"))

# Re-exported BY NAME on the entrypoint facade (the M3/_EXPORTS convention) so the
# tests still reach them as CLI.<name>, and aggregated by the package __init__ so
# every pre-split `_regressiongate.<name>` call site resolves unchanged.
_EXPORTS = [
    '_ab_verdict',
    'HeadExtractionError',
    '_head_scripts_listing',
    '_verify_head_extraction',
    '_synthesize_head_marker',
    '_extract_head_cli',
    '_extract_head_scripts',
    '_cleanup_head_cli',
    '_ab_head_recall',
    'run_ab_gate',
    '_run_ab_gate_ns',
    'install_ab_extraction',
    '_print_ab',
]
