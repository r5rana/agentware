"""agentware_lib.core.capture -- call a command function and CAPTURE its output (layer: core).

Owns: `_dream_capture` — invoke `fn(*a, **k)` with stdout/stderr redirected and
return `(rc, stdout_text)`. Eight lines, pure stdlib, no dependencies.
Does NOT own: any command; any dream-cycle machinery.

WHY IT LIVES IN core AND NOT WITH THE DREAM STEPS
-------------------------------------------------
It is named for the dream cycle because that is what needed it first, but nothing
about it is dream-specific: it is the generic "run a `cmd_*` handler in-process
and read what it printed" harness, and three unrelated subsystems need it —
the dream steps, the staleness review emitter, and the skill-candidate emitter,
each of which drives `cmd_plan_new` or `cmd_audit` and parses the JSON.

Homed in `features.dreamsteps.dreamcapture` it could only be reached by importing
that SUBMODULE, which executes the whole `dreamsteps` PACKAGE `__init__` — and
that aggregator imports every step, including the ones that import the very
modules trying to reach the harness. The result was a latent import cycle
(`skillpropose` -> `dreamcapture` -> `dreamsteps/__init__` -> `stepskillpropose`
-> `skillpropose`) that stayed invisible for as long as something happened to
import `dreamsteps` first, and raised `ImportError: partially initialized module`
the moment a new caller changed the order. An 8-line utility should not be able
to decide whether an import succeeds.

`features.dreamsteps.dreamcapture` re-exports it, so its documented surface and
every existing monkeypatch target are unchanged.

Layer deps: none — stdlib only, which is precisely why it belongs here.
Determinism: pure w.r.t. its argument; the capture itself adds no ordering.
"""

import contextlib
import io


def _dream_capture(fn, *a, **k):
    """Call `fn` with stdout/stderr captured; return (rc, stdout_text)."""
    buf, errbuf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(errbuf):
        rc = fn(*a, **k)
    return rc, buf.getvalue()


_EXPORTS = [
    "_dream_capture",
]

__all__ = list(_EXPORTS)
