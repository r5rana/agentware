"""agentware_lib.features.dreamsteps.dreamcapture -- the dream cycle's measure/capture primitives + step e's report artifact path (layer: features).

Owns: the cycle's monotonic perf clock (_dream_perf) the driver times every step
with, the stdout/stderr capture harness every step that re-enters another cmd_*
verb runs through (_dream_capture), the file fingerprint the cache-delta steps
compare before/after (_dream_file_fp), and the detect-report artifact path
(DREAM_REPORT_REL) step e overwrites each cycle.
Does NOT own: the hard-timeout git runner + the post-pull conflict check ->
dreamsteps.dreamgit; any STEP handler -> dreamsteps.step<step> (one file per
step); the ordered registry + the --steps subset parser -> dreamsteps.registry;
the cycle DRIVER cmd_dream -> features.dream. See catalog/modules.json.
Layer deps: NONE -- stdlib only (os/time at module scope; hashlib/io/contextlib
are lazily imported inside the functions that use them, INV-6). The deepest leaf
of the dreamsteps package: it imports no sibling, so nothing in the package can
cycle through it.
Fan-in: 6 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: _dream_perf is the ONLY clock the cycle reads, and it never enters a
journaled payload except as a rounded duration; _dream_file_fp is a pure content
hash. The detect-report artifact (DREAM_REPORT_REL) is report-only (INV-2), fixed
name, byte-stable for unchanged inputs (INV-1), gitignored via logs/.
"""

# Moved DOWN to core: three unrelated subsystems need this 8-line harness, and
# reaching it through this package's submodule executed the whole dreamsteps
# __init__, which closed a latent import cycle. Re-exported so this module's
# surface and every existing monkeypatch target are unchanged.
from agentware_lib.core.capture import _dream_capture   # noqa: F401  (re-export)

import os
import time


# Step e's actionable, report-only (INV-2) artifact. Fixed name ("latest"),
# overwritten each cycle, byte-stable for unchanged inputs (INV-1). Gitignored
# via logs/. It makes step e's COUNTS self-serve (which stale entry / which
# duplicate pair / which unpromoted marker) without re-deriving or re-running.
DREAM_REPORT_REL = os.path.join("logs", "dream-report-latest.md")


def _dream_perf():
    return time.perf_counter()


def _dream_file_fp(path):
    """sha256 of a file's bytes, or None if absent. For cache-delta detection."""
    import hashlib  # stdlib, lazy
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


# --- Task 8: the CHANGE GATE (affordability guard #2, the ~98% cost) -------
# Ported at task 56. Neither function existed in the modular core, so the two
# steps it guards -- the ~150s audit and the ~76s eval-record -- ran on EVERY
# cycle regardless of whether anything had changed, and `_dream_emit_metric`
# never stamped the fingerprint the gate compares against, so the gate could
# not have engaged even if it had been called. ~226s per cycle, every cycle.
def _dream_change_fp(kdir):
    """The committed-state change fingerprint = '<pkg HEAD sha>:<sha256(index.json)>'.
    Uses the index CONTENT hash (via _dream_file_fp), NOT mtime — step a rebuilds
    the index every cycle so mtime always moves, but the deterministic rebuild
    leaves CONTENT byte-stable when the KB is unchanged (F2). Returns None when
    either component cannot be computed, so the caller FAILS-OPEN (runs)."""
    from agentware_lib.core.gitio import _git_head
    from agentware_lib.core.paths import REPO_ROOT
    head = _git_head(REPO_ROOT)
    if not head:
        return None
    idxfp = _dream_file_fp(os.path.join(kdir, "index.json"))
    if not idxfp:
        return None
    return "%s:%s" % (head, idxfp)


def _dream_change_gate_skip(kdir):
    """Task 8 (affordability guard #2 — the ~98% cost). Shared decision for the
    audit (step c) + eval-record (step d) change-gate: return a SKIP reason when a
    CLEAN tree has NOT changed since the last cycle, else None (RUN).

    A DIRTY working tree ALWAYS runs (checked FIRST, before any fp compare): a 0/1
    dirty bit folded into the fp would collapse two DISTINCT dirty states and skip
    the second, masking a mid-development regression (F2) — so dirtiness gates the
    DECISION, never the fp VALUE. FAIL-OPEN: a dirty tree, no prior cycle, a prior
    cycle with no stored fp, or any error computing the fp all RUN."""
    try:
        # Function-local: `features.dream` and `features.pkgapply` both import
        # this package, so a module-scope edge back to either would close a cycle.
        from agentware_lib.features.dream import _dream_last_cycle
        from agentware_lib.features.pkgapply import _pkg_working_tree_dirty
        if _pkg_working_tree_dirty():
            return None                       # dirty -> always run
        last = _dream_last_cycle(kdir)
        if not last:
            return None                       # no prior cycle -> run
        prev = last.get("change_fp")
        if not prev:
            return None                       # prior cycle predates the fp -> run
        cur = _dream_change_fp(kdir)
        if cur is None:
            return None                       # cannot compute -> run (fail-open)
        if cur == prev:
            return "clean tree, no pkg/kb change since last cycle"
        return None
    except Exception:
        return None                           # any error -> run (fail-open)


_EXPORTS = [
    "_dream_change_fp", "_dream_change_gate_skip",
    "DREAM_REPORT_REL",
    "_dream_perf",
    "_dream_file_fp",
    "_dream_capture",
]
