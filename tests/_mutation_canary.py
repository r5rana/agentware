#!/usr/bin/env python3
"""Mutation canary for the strangler facade's WRITE seam.

Feature 260813-modular-core-adoption, task 28.

The whole suite reaches the CLI through ``tests/_fixtures.load_cli()``, which
loads ``scripts/agentware`` as a module and lets tests monkeypatch symbols on
it. Task 25 put a facade in front of the monolith, and a facade has TWO seams:

  * the READ seam -- does ``CLI.<name>`` still resolve? That is what the frozen
    CLI-symbol reachability gate in ``_preserved_freeze`` measures, and it is
    easy to satisfy;

  * the WRITE seam -- does REBINDING ``CLI.<name>`` still change what the
    dispatched command executes? ``import agentware_legacy; globals().update(
    vars(...))`` satisfies the read seam and DESTROYS this one, because the
    imported functions' ``__globals__`` is the legacy module dict: a patched
    name is a copy nobody reads. Every patch-based test in the suite would keep
    passing while asserting nothing, and the reachability gate would report
    full coverage the entire time.

``hasattr`` cannot tell those two worlds apart -- it is satisfied by a
photograph. Only a MUTATION can: patch one symbol, dispatch a real command, and
require the observed output to CHANGE. This module is that probe, plus the
synthesizer for the two broken facades it must redden against, so the canary is
proven to bite rather than assumed to.

The probed symbol is deliberately one the facade never mentions:
``resolve_knowledge_dir`` is defined in the legacy body and called by
``cmd_config`` through a plain global lookup, so the patched value can only
reach stdout if the executed body's globals really are the facade's module dict.

Usage:
    python3 tests/_mutation_canary.py [--root <tree>]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

SCHEMA_VERSION = 1

#: The legacy symbol to rebind. Called by `cmd_config` via a global lookup.
CANARY_SYMBOL = "resolve_knowledge_dir"

#: The command whose stdout must change when the symbol is rebound.
CANARY_ARGV = ("config", "--knowledge-dir-only")

#: What the patched symbol returns. Absent from every tree, so its presence in
#: stdout can only come from the patch.
CANARY_SENTINEL = "/aw-mutation-canary-sentinel-2b7f1c"

#: The exec line the facade's write seam depends on. The synthesizer edits THIS
#: text; if it ever stops matching, the mutations below are silently no-ops, so
#: `synthesize_facade` refuses rather than producing a tree that proves nothing.
FACADE_EXEC_ANCHOR = 'exec(compile(_LEGACY_BODY, _LEGACY_PATH, "exec"), globals())'

#: Facade mutations. `exec` is the shipped file verbatim (the positive control).
FACADE_KINDS = ("exec", "import-copy", "absent")

_MUTATIONS = {
    "exec": None,
    # Read seam intact, write seam destroyed: the classic silent killer.
    "import-copy": ("import agentware_legacy as _aw_legacy\n"
                    "globals().update(vars(_aw_legacy))"),
    # No passthrough at all: nothing to dispatch.
    "absent": "pass  # passthrough removed",
}


# --- the probe ---------------------------------------------------------------

def probe_inprocess(root):
    """Load `<root>/scripts/agentware`, dispatch, patch, dispatch again.

    MUST run in a dedicated process: it neutralises AGENTWARE_* env vars and
    loads a 34k-line module under a fixed name.
    """
    import importlib.util
    import io
    import tempfile
    from importlib.machinery import SourceFileLoader

    for key in [k for k in os.environ if k.startswith("AGENTWARE_")]:
        del os.environ[key]
    sandbox = tempfile.mkdtemp(prefix="aw-canary-")
    kdir = os.path.join(sandbox, "kb")
    os.makedirs(kdir, exist_ok=True)
    os.environ["AGENTWARE_KNOWLEDGE_DIR"] = kdir
    os.environ["AGENTWARE_CONFIG_HOME"] = os.path.join(sandbox, "cfg")

    result = {"root": root, "kdir": kdir, "loaded": False, "reachable": False,
              "dispatched": False, "before": None, "after": None, "error": None}

    path = os.path.join(root, "scripts", "agentware")
    try:
        loader = SourceFileLoader("agentware_canary_cli", path)
        spec = importlib.util.spec_from_loader("agentware_canary_cli", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
    except BaseException as exc:  # SystemExit included: an install-integrity exit
        result["error"] = "LOAD: %s: %s" % (type(exc).__name__, exc)
        return result
    result["loaded"] = True
    result["reachable"] = hasattr(mod, CANARY_SYMBOL)
    if not hasattr(mod, "main"):
        result["error"] = "main() is not reachable on the loaded CLI module"
        return result

    def dispatch():
        buf = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(buf):
            mod.main(list(CANARY_ARGV))
        return buf.getvalue().strip()

    try:
        result["before"] = dispatch()
        if result["reachable"]:
            original = getattr(mod, CANARY_SYMBOL)
            setattr(mod, CANARY_SYMBOL, lambda *a, **k: CANARY_SENTINEL)
            try:
                result["after"] = dispatch()
            finally:
                setattr(mod, CANARY_SYMBOL, original)
        result["dispatched"] = True
    except BaseException as exc:
        result["error"] = "DISPATCH: %s: %s" % (type(exc).__name__, exc)
    return result


def probe(root):
    """Run `probe_inprocess` for `root` in a subprocess and return its dict."""
    worker = (
        "import json,sys;"
        "sys.path.insert(0, %r);"
        "from _mutation_canary import probe_inprocess as p;"
        "sys.stdout.write('\\n@@CANARY@@' + json.dumps(p(%r)))"
        % (os.path.dirname(os.path.realpath(__file__)), os.path.realpath(root))
    )
    proc = subprocess.run([sys.executable, "-c", worker],
                          capture_output=True, text=True, timeout=600)
    marker = "@@CANARY@@"
    if marker not in proc.stdout:
        return {"root": root, "loaded": False, "reachable": False,
                "dispatched": False, "before": None, "after": None,
                "error": "probe process rc=%d: %s"
                         % (proc.returncode, (proc.stderr or "")[-800:])}
    return json.loads(proc.stdout.rsplit(marker, 1)[1])


# --- the verdict (PURE) -------------------------------------------------------

def verdict(result, sentinel=CANARY_SENTINEL, symbol=CANARY_SYMBOL):
    """PURE: probe result -> sorted violations. Empty means the seam is live.

    Kept free of I/O so every failure mode can be asserted on a synthetic dict
    instead of only through a multi-second real load.
    """
    out = []
    if not result.get("loaded"):
        out.append("CANARY: the CLI module failed to load (%s)"
                   % (result.get("error") or "no error reported"))
        return out
    if not result.get("reachable"):
        out.append("CANARY: %s is not reachable on the CLI module -- the "
                   "passthrough is gone" % symbol)
    if result.get("error"):
        out.append("CANARY: %s" % result["error"])
    if not result.get("dispatched"):
        return sorted(out)
    before, after = result.get("before"), result.get("after")
    if not before:
        out.append("CANARY: the unpatched command printed nothing -- the probe "
                   "observes no output and could not detect a change")
    if before == after:
        out.append("CANARY: rebinding %s did not change the dispatched output "
                   "-- the WRITE seam is dead (namespace copy?), so every "
                   "monkeypatching test in the suite is vacuous" % symbol)
    elif sentinel not in (after or ""):
        out.append("CANARY: output changed but the patched value never reached "
                   "stdout -- %s is not the symbol the dispatch reads" % symbol)
    return sorted(out)


# --- the synthesizer (proves the canary bites) --------------------------------

def synthesize_facade(dest, kind, repo):
    """Build a scratch tree at `dest` whose facade carries mutation `kind`.

    The lib and the legacy body are SYMLINKED, so the only difference between
    the positive control and a mutation is the facade's passthrough line.
    """
    if kind not in _MUTATIONS:
        raise ValueError("unknown facade mutation: %s" % kind)
    scripts = os.path.join(dest, "scripts")
    os.makedirs(scripts, exist_ok=True)
    for rel in ("scripts/agentware_lib", "scripts/agentware_legacy.py",
                # The lib's repo-root resolver walks UP from its own __file__,
                # which under a symlinked package is the SCRATCH path -- so the
                # scratch tree must carry the repo-root marker or every variant
                # dies at import and the mutations prove nothing.
                "AGENTS.md"):
        link = os.path.join(dest, rel)
        if not os.path.lexists(link):
            os.symlink(os.path.join(repo, rel), link)

    with open(os.path.join(repo, "scripts", "agentware"), encoding="utf-8") as fh:
        text = fh.read()
    if text.count(FACADE_EXEC_ANCHOR) != 1:
        raise SystemExit("mutation canary: the facade's exec anchor no longer "
                         "matches exactly once -- update FACADE_EXEC_ANCHOR "
                         "before trusting this gate")
    replacement = _MUTATIONS[kind]
    if replacement is not None:
        text = text.replace(FACADE_EXEC_ANCHOR, replacement)
    target = os.path.join(scripts, "agentware")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(target, 0o755)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description="run the facade mutation canary")
    parser.add_argument("--root", default=os.path.dirname(
        os.path.dirname(os.path.realpath(__file__))))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = probe(args.root)
    problems = verdict(result)
    if args.json:
        json.dump({"schema_version": SCHEMA_VERSION, "result": result,
                   "violations": problems}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print("canary root:   %s" % result.get("root"))
        print("  loaded:      %s" % result.get("loaded"))
        print("  reachable:   %s (%s)" % (result.get("reachable"), CANARY_SYMBOL))
        print("  before:      %r" % (result.get("before"),))
        print("  after:       %r" % (result.get("after"),))
        for line in problems:
            print("  %s" % line)
        print("CANARY %s" % ("FAILED" if problems else "PASSED"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
