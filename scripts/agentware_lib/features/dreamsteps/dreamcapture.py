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


def _dream_capture(fn, *a, **k):
    """Call `fn` with stdout/stderr captured; return (rc, stdout_text)."""
    import io          # stdlib, lazy
    import contextlib  # stdlib, lazy
    buf, errbuf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(errbuf):
        rc = fn(*a, **k)
    return rc, buf.getvalue()


_EXPORTS = [
    "DREAM_REPORT_REL",
    "_dream_perf",
    "_dream_file_fp",
    "_dream_capture",
]
