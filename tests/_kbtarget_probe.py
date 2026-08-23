"""The KB TARGET GUARD as the live library actually answers it.

The static half of this gate (`_kbtarget_gate`) reads the tree's SOURCE: that
`kb_pointable` is called by name at every repoint site, that the retired
auto-repoint module is gone, that the derived index's filename is defined once.
None of that can say what the guard REPLIES when handed a real directory, or
what `init` does when a second one is offered -- and those are the two questions
an operator's config actually depends on. This module asks them by running the
library, in a sandbox, and comparing the answers to a declared table.

It therefore owns the GUARD'S IDENTITY as well as the probes: which module and
name the guard is, which verdict each target shape must produce, and which
clobber case must end at which directory. The static scanner is a SECOND reader
of that identity, which is why the import runs scanner -> probe and never back:
a table and its two readers in three files is the shape where two of them drift.

Every probe here runs through `_probe_sandbox`, so AGENTWARE_CONFIG_HOME and
AGENTWARE_KNOWLEDGE_DIR are redirected to throwaway paths and the operator's
real config and knowledge base are never read or written.

Stdlib only.
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _probe_sandbox  # noqa: E402

GUARD_MODULE = "agentware_lib.core.kblayout"
GUARD_NAME = "kb_pointable"

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
    """Run a probe against `tree`'s lib in a fully redirected subprocess.

    The sandbox is `_probe_sandbox`'s; the WORDING of each failure is this
    gate's own, because a caller reports into its own problem list and the
    truncation widths below are tuned to what a reader of THIS gate needs.
    """
    with tempfile.TemporaryDirectory(prefix="aw-kbtarget-home-") as scratch:
        env = _probe_sandbox.sandbox_env(
            scratch, pythonpath=os.path.join(tree, "scripts"),
            drop=("PYTHONDONTWRITEBYTECODE",), extra=extra_env)
        _probe_sandbox.ensure_config_home(env)
        probe = _probe_sandbox.run_json_probe(source, env, cwd=tree)
    if probe.kind == _probe_sandbox.COULD_NOT_RUN:
        return None, "probe could not run: %s" % (probe.exc,)
    if probe.kind == _probe_sandbox.NONZERO_EXIT:
        return None, "probe exited %d: %s" % (probe.returncode,
                                              (probe.stderr or "").strip()[-500:])
    if probe.kind == _probe_sandbox.NO_JSON:
        return None, "probe emitted no JSON (%s): %s" % (probe.decode_error,
                                                         probe.stdout[-300:])
    return probe.payload, None


# --------------------------------------------------------------------------
# the checks that read a probe
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

