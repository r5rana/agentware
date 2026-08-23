"""The redirected sandbox every behavioural gate probe runs its child in.

Three gate harnesses make the SAME promise, one sentence apiece, in their own
module docstrings:

    "Every probe runs in a subprocess with AGENTWARE_CONFIG_HOME and
     AGENTWARE_KNOWLEDGE_DIR redirected to throwaway paths, so the operator's
     real config and knowledge base are never read or written."

That promise is this module. It was written three times before -- once in
`_dreampkg_retired`, once in `_kbtarget_probe`, once in `_orgidentity_probe` --
which is three chances for one of them to quietly stop redirecting a key the
other two still do, on harnesses whose entire job is to run live code against
the operator's own machine. The three copies had already drifted: two popped
`PYTHONDONTWRITEBYTECODE` and one did not, two created the config home and one
did not, and none of the three ever checked what the child actually RECEIVED --
the promise was asserted in prose and tested nowhere.

WHAT THIS MODULE OWNS

  * `sandbox_env` -- the environment a probe child is handed, as a pure value:
    which keys are SET (and to what, under a caller-owned scratch root), which
    are REMOVED, and what the caller may overlay on top.
  * `ensure_config_home` / `seed_knowledge_dir` -- the two on-disk shapes a
    child may need to exist before it runs.
  * `run_entry` / `run_json_probe` -- the two shapes of child (a tree's own
    entrypoint, and `python -c <source>`), run inside that environment.
  * `FAILURES` -- the CLOSED set of ways a child fails to yield a payload.

WHAT IT DELIBERATELY DOES NOT OWN

The WORDING of a failure. Each gate reports into its own problem list in its own
voice, with its own truncation widths, and a shared module carrying three
callers' message templates would be a message table wearing a boundary's name.
The closed `kind` is the shared vocabulary; the sentence is the caller's.

IMPORT DIRECTION

Nothing here imports a gate. Every gate imports this. A probe module sits BELOW
the static scanner that re-exports it, so the direction is always
scanner -> probe -> sandbox and there is no cycle to break with a lazy import.

Stdlib only.
"""

import json
import os
import subprocess
import sys

#: The two keys that MUST point somewhere disposable before any child runs.
#: Named here rather than spelled at each call site: a redirect that is missing
#: is indistinguishable from a redirect that is misspelled, and both end with a
#: gate writing to the operator's real knowledge base.
CONFIG_HOME_KEY = "AGENTWARE_CONFIG_HOME"
KNOWLEDGE_DIR_KEY = "AGENTWARE_KNOWLEDGE_DIR"

#: The scratch-relative leaves the two keys point at. A caller hands in the
#: root; the LAYOUT under it is this module's, so three harnesses cannot invent
#: three answers to "where does the throwaway config live".
CONFIG_HOME_LEAF = "config"
KNOWLEDGE_DIR_LEAF = "kb"

#: The default ceiling on a child. Generous on purpose: these probes import the
#: library and run real handlers, and a timeout that fires on a slow machine
#: reads exactly like the failure it is supposed to be watching for.
DEFAULT_TIMEOUT = 300

#: The CLOSED set of ways a child fails to yield a payload. A caller renders its
#: own sentence per member; what it may NOT do is invent a fourth kind, because
#: a failure outside this set is one no caller has a message for.
COULD_NOT_RUN = "could-not-run"
NONZERO_EXIT = "nonzero-exit"
NO_JSON = "no-json"
FAILURES = (COULD_NOT_RUN, NONZERO_EXIT, NO_JSON)


class Probe(object):
    """What a sandboxed child produced: a payload, or ONE named failure.

    `kind` is None exactly when `payload` is meaningful. The raw fields are kept
    populated whether or not the run succeeded, because every caller's message
    quotes one of them and each quotes a different slice of it.
    """

    __slots__ = ("kind", "payload", "exc", "returncode", "stdout", "stderr",
                 "decode_error")

    def __init__(self, kind=None, payload=None, exc=None, returncode=None,
                 stdout="", stderr="", decode_error=None):
        self.kind = kind
        self.payload = payload
        self.exc = exc
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.decode_error = decode_error

    @property
    def ok(self):
        """True exactly when a payload was produced."""
        return self.kind is None

    def __repr__(self):
        return "Probe(kind=%r, returncode=%r)" % (self.kind, self.returncode)


def sandbox_env(scratch, pythonpath=None, drop=(), extra=None):
    """The environment a probe child is handed, as a PURE value.

    `scratch` is the caller's disposable root: both redirected keys are placed
    under it, so a caller that removes the root removes everything the child
    could have written through them. `drop` removes keys the child must NOT
    inherit; `extra` overlays the caller's own, applied LAST so a caller can
    deliberately set a key this function also sets.
    """
    env = dict(os.environ)
    env[CONFIG_HOME_KEY] = os.path.join(scratch, CONFIG_HOME_LEAF)
    env[KNOWLEDGE_DIR_KEY] = os.path.join(scratch, KNOWLEDGE_DIR_LEAF)
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    for key in drop:
        env.pop(key, None)
    env.update(extra or {})
    return env


def ensure_config_home(env):
    """Create the redirected config home, for a child that WRITES one."""
    path = env[CONFIG_HOME_KEY]
    os.makedirs(path, exist_ok=True)
    return path


def seed_knowledge_dir(env, sentinel_body="gate\n",
                       main_body="# knowledge base\n"):
    """Create the redirected knowledge dir as a MINIMALLY initialized KB.

    The sentinel and the entry document are what makes a directory read as
    onboarded; a child that resolves a knowledge dir and finds neither takes the
    not-initialized path, which is a different code path from the one a
    behavioural gate means to exercise.
    """
    kdir = env[KNOWLEDGE_DIR_KEY]
    os.makedirs(kdir, exist_ok=True)
    with open(os.path.join(kdir, ".initialized"), "w") as fh:
        fh.write(sentinel_body)
    with open(os.path.join(kdir, "MAIN.md"), "w") as fh:
        fh.write(main_body)
    return kdir


def run_entry(argv, env, cwd=None, timeout=DEFAULT_TIMEOUT):
    """Run `argv` in the sandbox; report the process, or why it never ran.

    A child that ran and FAILED is not a failure of this function -- the
    returncode is data the caller judges. Only a child that could not be started
    at all carries a `kind` here.
    """
    try:
        proc = subprocess.run(argv, env=env, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return Probe(kind=COULD_NOT_RUN, exc=exc)
    return Probe(returncode=proc.returncode, stdout=proc.stdout or "",
                 stderr=proc.stderr or "")


def run_json_probe(source, env, cwd=None, timeout=DEFAULT_TIMEOUT):
    """Run `python -c source` in the sandbox and decode the JSON it prints.

    The three failures are ordered by what they can EXPLAIN: a child that never
    ran has no exit code, and a child that exited non-zero has no reason to have
    printed a payload, so neither is reported as a decode problem.
    """
    probe = run_entry([sys.executable, "-c", source], env, cwd, timeout)
    if probe.kind is not None:
        return probe
    if probe.returncode != 0:
        probe.kind = NONZERO_EXIT
        return probe
    try:
        probe.payload = json.loads(probe.stdout)
    except ValueError as exc:
        probe.kind = NO_JSON
        probe.decode_error = exc
    return probe
