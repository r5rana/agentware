"""The RETIRED package-pull dream step: what it is called, and that it is gone.

The static half of this gate (`_dreampkg_gate`) reads the tree's SOURCE: that the
step registry no longer declares the retired id or label, that the retired
callables are undefined, that the tokens have zero hits, that no dream step
mutates the package checkout. All of that is a scan, and a scan can only prove
the NAMES are absent. It cannot prove that asking the shipped CLI for the step is
REFUSED, nor that the plan the CLI actually emits contains no package pull -- and
those are the two facts an operator's machine depends on, since a step that is
gone from the registry but reachable by another route is still a step that runs.

This module asks the CLI, in a sandbox, and owns the retired step's IDENTITY
while it is at it: which id and label it went by, which callables existed only to
serve it, which tokens the tree-wide scan hunts, and which entrypoint to ask.
The static scanner is a SECOND reader of that identity, which is why the import
runs scanner -> retired and never back.

Every name below is assembled from parts on purpose: this file names them in
order to prove them ABSENT, and a literal would make the gate's own source a hit
for the tree-wide scan it performs.

The CLI runs through `_probe_sandbox`, so AGENTWARE_CONFIG_HOME and
AGENTWARE_KNOWLEDGE_DIR are redirected to a throwaway, minimally-initialized
knowledge base and the operator's real config and knowledge base are never read
or written.

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

ENTRY_REL = os.path.join("scripts", "agentware")

#: The retired step's id and label. Assembled from parts on purpose: this file
#: names them in order to prove them ABSENT, and a literal here would make the
#: gate's own source a hit for the scan it performs.
RETIRED_STEP_ID = "0"
RETIRED_STEP_LABEL = "pkg" + "-pull"

#: The step function and the two helpers that existed only to serve it, plus the
#: migrate-after-pull hook this tree retired in the same change.
RETIRED_CALLABLES = (
    "_dream_step_pkg" + "_pull",
    "_dream_pkg" + "_syntax_check",
    "_dream_pkg" + "_pull_revert",
    "_dream_apply_migrations" + "_after_pull",
)

#: The literal tokens check 3 scans for, case-insensitively.
RETIRED_TOKENS = (RETIRED_STEP_LABEL, "pkg" + "_pull",
                  "_dream_pkg" + "_syntax_check")


def run_cli(tree, argv):
    """(rc, stdout, stderr) — the tree's own entrypoint, fully redirected.

    The sandbox is `_probe_sandbox`'s; the WORDING of each failure is this
    gate's own. A missing entrypoint is reported BEFORE the sandbox is built,
    because "this tree has no CLI" is a different fact from "the CLI would not
    start", and collapsing the two would let a mis-resolved tree read as a
    broken one.
    """
    entry = os.path.join(tree, ENTRY_REL)
    if not os.path.exists(entry):
        return None, "", "entrypoint missing: %s" % ENTRY_REL
    with tempfile.TemporaryDirectory(prefix="aw-dreampkg-") as scratch:
        env = _probe_sandbox.sandbox_env(
            scratch, drop=("PYTHONDONTWRITEBYTECODE",))
        _probe_sandbox.seed_knowledge_dir(env)
        _probe_sandbox.ensure_config_home(env)
        probe = _probe_sandbox.run_entry([sys.executable, entry] + list(argv),
                                         env, cwd=tree)
    if probe.kind == _probe_sandbox.COULD_NOT_RUN:
        return None, "", "could not run: %s" % (probe.exc,)
    return probe.returncode, probe.stdout, probe.stderr


def check_behaviour(tree):
    """Check 5: the CLI refuses the retired step and plans no package pull."""
    problems = []
    rc, out, err = run_cli(tree, ["dream", "--steps", RETIRED_STEP_ID,
                                  "--dry-run"])
    blob = out + err
    if rc is None:
        problems.append("refusal probe: %s" % err)
    elif rc == 0:
        problems.append("--steps %s was ACCEPTED (rc=0): the retired step is "
                        "still runnable" % RETIRED_STEP_ID)
    elif "unknown dream step" not in blob:
        problems.append("--steps %s failed rc=%s but not as an unknown step: %r"
                        % (RETIRED_STEP_ID, rc, blob.strip()[:200]))

    rc, out, err = run_cli(tree, ["dream", "--dry-run", "--format", "json"])
    if rc is None:
        problems.append("dry-run probe: %s" % err)
        return problems
    if rc != 0:
        problems.append("dream --dry-run exited %s: %s" % (rc, err.strip()[:200]))
        return problems
    try:
        payload = json.loads(out)
    except ValueError as exc:
        problems.append("dream --dry-run emitted no JSON (%s)" % (exc,))
        return problems
    for step in payload.get("steps") or []:
        if step.get("step") == RETIRED_STEP_ID:
            problems.append("the dry-run plan still contains step %r"
                            % RETIRED_STEP_ID)
        would = json.dumps(step).lower()
        if "pull agentware" in would or "agentware repo" in would:
            problems.append("step %r plans a package pull: %s"
                            % (step.get("step"), would[:160]))
    return problems

