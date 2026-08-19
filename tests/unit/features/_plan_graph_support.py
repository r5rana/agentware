"""Shared header for the `test_plan_graph` suite, split out by task 58.

Holds every import, helper, constant and base class the split
parts share, so no part duplicates a definition and none can
drift from another.
"""

import os
import tempfile
import unittest
try:
    from tests._fixtures import load_cli, REPO_ROOT, SyntheticKBTestCase
except ImportError:  # when run via `discover -s tests`, tests/ is on sys.path
    from _fixtures import load_cli, REPO_ROOT, SyntheticKBTestCase
CLI = load_cli()
def _plan(body, *, self_ext=False, target="~/workspace/agentware"):
    """A minimal but lint-valid-enough plan.md carrying the graph blocks in
    `body`. `self_ext` stamps the `> Type:` self-extension header the deterministic
    detector keys on."""
    typ = "> Type: **self-extension (package change)**\n" if self_ext else ""
    return (
        "# Plan — fixture\n\n"
        "> Status: draft\n"
        "%s"
        "\n## Target packages\n\n- %s\n\n"
        "%s\n"
        "## Tasks\n\n- ⬜ **1** do a thing\n  *Verify:* true\n\n"
        "## Acceptance criteria\n\n- [ ] it works\n"
        % (typ, target, body)
    )
def _manifest(nodes=None, edges=None, *, name="260722-g", schema=None,
              concurrency=3):
    """Build a graph manifest dict. `nodes` is a list of (feature, checkpoint,
    self_ext) tuples or full dicts; `edges` a list of (from, to[, kind]) tuples."""
    man = {
        "schema": CLI.GRAPH_MANIFEST_SCHEMA if schema is None else schema,
        "name": name,
        "concurrency": concurrency,
        "nodes": [],
        "edges": [],
    }
    for n in (nodes or []):
        if isinstance(n, dict):
            man["nodes"].append(n)
        else:
            feat, cp, se, ws = (list(n) + ["none", False, []])[:4]
            man["nodes"].append({"feature": feat, "checkpoint": cp,
                                 "self_extension": se, "write_set": list(ws)})
    for e in (edges or []):
        frm, to = e[0], e[1]
        kind = e[2] if len(e) > 2 else "logical"
        man["edges"].append({"from": frm, "to": to, "kind": kind})
    return man
def _combined(manifest, derived):
    """A manifest with derived resource edges unioned in, then built into the
    (nodes, forward, reverse) triple the scheduler consumes."""
    m = dict(manifest)
    m["edges"] = list(manifest.get("edges") or []) + list(derived)
    return CLI._graph_build(m)
def _status_plan(status="running", *, open_markers=0, done_markers=1,
                 self_ext=False, claim=None, dup=False):
    """Build a plan.md whose header carries `status`, optional `Claimed-by`, and a
    controllable mix of open/done task markers. `claim` is a `who@host <ISO>` raw
    string; `dup=True` injects a SECOND `> Status:` line (degenerate header)."""
    typ = "> Type: **self-extension (package change)**\n" if self_ext else ""
    claim_line = "> Claimed-by: %s\n" % claim if claim else ""
    dup_line = "> Status: draft\n" if dup else ""
    tasks = []
    for i in range(1, done_markers + 1):
        tasks.append("- ✅ **%d** done task\n  *Verify:* true\n" % i)
    for i in range(done_markers + 1, done_markers + open_markers + 1):
        tasks.append("- ⬜ **%d** open task\n  *Verify:* true\n" % i)
    if not tasks:
        tasks.append("- ✅ **1** done task\n  *Verify:* true\n")
    return (
        "# Plan — node fixture\n\n"
        "> Status: %s\n%s%s%s"
        "\n## Target packages\n\n- ~/workspace/agentware\n\n"
        "## Tasks\n\n%s\n"
        "## Acceptance criteria\n\n- [ ] it works\n"
        % (status, claim_line, dup_line, typ, "".join(tasks))
    )
def _have_git_t5():
    import subprocess
    try:
        subprocess.run(["git", "--version"], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, check=True)
        return True
    except Exception:  # pragma: no cover
        return False
HAVE_GIT_T5 = _have_git_t5()
class _FakeHandle:
    """A stand-in for a subprocess.Popen handle: `.wait()` returns instantly so the
    wave barrier never touches a real process."""

    def __init__(self):
        self.waited = False

    def wait(self):
        self.waited = True
        return 0
if __name__ == "__main__":
    unittest.main()
