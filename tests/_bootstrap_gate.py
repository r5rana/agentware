#!/usr/bin/env python3
"""The FRESH-CLONE BOOTSTRAP gate -- clone-and-go, proven from an empty directory
(feature 260813-modular-core-adoption, task 78).

    python3 tests/_bootstrap_gate.py --health      # the health sequence, here
    python3 tests/_bootstrap_gate.py --from-empty  # clone into an empty dir first

agentware's headline claim is that a brand-new operator clones the repository and
it WORKS -- no `pip install`, no lockfile, no virtualenv, no network, because the
package has zero hard dependencies (INV-6). `tests/e2e/test_fresh_clone.py`
already proves the empty-KB world is healthy, but it runs against the checkout
the suite lives in, so it cannot see the failure modes that only a real clone
has: a file that works because it is untracked and therefore never shipped, a
generated artifact that exists only because someone ran the generator once, an
import that resolves off the developer's `sys.path` rather than the repository's
own bootstrap. `--from-empty` closes that: it makes an EMPTY directory, clones
into it, and runs the documented command there.

THE ONE DOCUMENTED COMMAND
--------------------------
`BOOTSTRAP_COMMAND` below is the single string a fresh clone runs. It is declared
HERE, once, and `tests/gates/test_ci_workflow.py` arm E asserts the same bytes
appear in `CONTRIBUTING.md` and in `.github/workflows/ci.yml` -- so the command a
contributor is told to type cannot drift from the command that is proven to work.
It is deliberately ONE command: no `&&`, no `;`, no pipe, nothing to install
first, because "one documented command" is the claim under test and a compound
line quietly makes it three.

WHY THE CLONE IS PATCHED WITH THE WORKING-TREE DIFF
---------------------------------------------------
`git clone` copies COMMITTED state. In CI that is exactly right -- the tree under
test is the commit. Locally it is a trap: a contributor who has not committed yet
would be testing the previous commit and reading its result as a verdict on their
change, which is worse than not running the gate at all. So the clone is brought
up to the working tree with `git diff HEAD --binary` (staged and unstaged changes
to TRACKED files). Untracked files are NOT carried, deliberately and visibly: a
file git does not track is a file a real clone would not receive, so reporting it
as working would be the exact lie this gate exists to catch. The report states
which mode ran and whether a patch was applied, so a green is never ambiguous.

WHY A SUBPROCESS WITH A PATCHED HOME IS MANDATORY
-------------------------------------------------
`agentware init` writes `~/.agentware/config.env`, and the path is bound at module
IMPORT time -- so an in-process init CANNOT be redirected and WOULD clobber the
operator's real knowledge-dir pointer (`tests/_fixtures.py` warns tests must
never call it; `tests/e2e/test_fresh_clone.py` explains the seam in full). Every
step here therefore runs in a subprocess under a temp `HOME`, and the final check
asserts the operator's real `config.env` is byte-for-byte unchanged.

Read-only with respect to the repository. No network. Standard library only.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
REPO_ROOT = os.path.dirname(HERE)

#: THE ONE DOCUMENTED COMMAND a fresh clone runs to reach a working state.
#: Declared once; `test_ci_workflow.py` arm E pins CONTRIBUTING.md and the
#: workflow to these exact bytes. Keep it a SINGLE command with no shell
#: operators -- the claim being tested is "one command", not "one line".
BOOTSTRAP_COMMAND = "python3 tests/_ci_gate.py --profile bootstrap"

#: Shell operators that would turn the one command into several. Arm E rejects
#: any of these appearing in `BOOTSTRAP_COMMAND` itself.
COMPOUND_OPERATORS = ("&&", "||", ";", "|", "\n")

#: The knowledge-dir subdirectories a working `init` must scaffold. Re-declared
#: here rather than imported from `core.kblayout` on purpose: this file is a
#: CONTRACT check, and a contract that imports its expectation from the code
#: under test asserts only that the code equals itself.
EXPECTED_SUBDIRS = ("learnings", "projects", "configurations", "prompts",
                    "references", "skills")

#: The audit checks a fresh, empty knowledge base must pass. A subset -- the ones
#: that are meaningful with no content yet -- so the gate stays true rather than
#: aspirational.
EXPECTED_AUDIT_CHECKS = ("index_validate", "steering_lint", "personal_data")


def _run(argv, cwd, env, timeout=900):
    """Run a command, returning (rc, combined output). Never raises on failure."""
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True)
    except subprocess.TimeoutExpired:
        # A timeout is a FAILURE, never a slow pass: a hung step at 0.0% CPU is
        # indistinguishable from a working one to any check that waits for a
        # success marker.
        return 124, "TIMEOUT after %ss: %s" % (timeout, " ".join(argv))
    return proc.returncode, proc.stdout or ""


def _hermetic_env(home, kdir, extra=None):
    """A subprocess env with a patched HOME and a temp knowledge dir.

    Inherited `AGENTWARE_*` shell settings are stripped so the operator's own
    environment cannot mask what a fresh clone would actually do -- several
    resolvers read the env var BEFORE `config.env`, so leaving them in would make
    this gate report the developer's machine rather than the clone.
    """
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("AGENTWARE_"):
            del env[key]
    env["HOME"] = home
    env["AGENTWARE_KNOWLEDGE_DIR"] = kdir
    env["AGENTWARE_NESTED_UNITTEST"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra or {})
    return env


def _real_config_digest():
    """Digest of the operator's REAL `~/.agentware/config.env`, or None."""
    path = os.path.join(os.path.expanduser("~"), ".agentware", "config.env")
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


# --- the health sequence ----------------------------------------------------

def _check_help(root, env, failures):
    rc, out = _run([sys.executable, "scripts/agentware", "--help"], root, env)
    if rc != 0:
        failures.append("--help exited %d: %s" % (rc, out.strip()[:200]))
    elif "<command>" not in out:
        failures.append("--help rendered no command list")
    return out


def _check_init(root, env, kdir, failures):
    rc, out = _run([sys.executable, "scripts/agentware", "init",
                    "--knowledge-dir", kdir], root, env)
    if rc != 0:
        failures.append("init exited %d: %s" % (rc, out.strip()[:400]))
        return
    missing = [d for d in EXPECTED_SUBDIRS
               if not os.path.isdir(os.path.join(kdir, d))]
    if missing:
        failures.append("init scaffolded no %s" % ", ".join(missing))
    if not os.path.isfile(os.path.join(kdir, "index.json")):
        failures.append("init seeded no index.json")


def _check_index_validate(root, env, failures):
    rc, out = _run([sys.executable, "scripts/agentware", "index", "validate"],
                   root, env)
    if rc != 0:
        failures.append("index validate exited %d: %s" % (rc, out.strip()[:400]))


def _check_recall(root, env, failures):
    rc, out = _run([sys.executable, "scripts/agentware", "recall",
                    "bootstrap smoke", "--format", "json"], root, env)
    if rc != 0:
        failures.append("recall exited %d: %s" % (rc, out.strip()[:300]))
        return
    try:
        json.loads(out)
    except ValueError:
        failures.append("recall --format json emitted no parsable JSON")


def _check_audit(root, env, failures):
    rc, out = _run([sys.executable, "scripts/agentware", "audit"], root, env)
    if rc != 0:
        failures.append("audit exited %d: %s" % (rc, out.strip()[:600]))
        return
    for name in EXPECTED_AUDIT_CHECKS:
        if ("[ok] " + name) not in out:
            failures.append("audit did not report [ok] %s" % name)


def health(root=None, echo=True):
    """Run the health sequence against `root` in a hermetic temp HOME + KB.

    This is what "a working state" MEANS, stated as commands rather than prose:
    the CLI renders, `init` scaffolds a knowledge base, the index validates,
    retrieval answers, and the consistency audit is clean -- with the operator's
    real config proven untouched afterwards.
    """
    root = os.path.abspath(root or REPO_ROOT)
    before = _real_config_digest()
    tmp = tempfile.mkdtemp(prefix="agentware-bootstrap-")
    failures = []
    try:
        home = os.path.join(tmp, "home")
        kdir = os.path.join(tmp, "kb")
        os.makedirs(home)
        env = _hermetic_env(home, kdir)
        _check_help(root, env, failures)
        _check_init(root, env, kdir, failures)
        _check_index_validate(root, env, failures)
        _check_recall(root, env, failures)
        _check_audit(root, env, failures)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if before != _real_config_digest():
        failures.append("HERMETICITY: the operator's real ~/.agentware/"
                        "config.env CHANGED during the bootstrap")
    if echo:
        _report("clone health", root, failures)
    return failures


# --- the fresh clone --------------------------------------------------------

def _git(argv, cwd, capture=True):
    kwargs = {"cwd": cwd, "text": True}
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.STDOUT
    proc = subprocess.run(["git"] + list(argv), **kwargs)
    return proc.returncode, (proc.stdout or "") if capture else ""


def clone_into_empty(dest_parent, source=None, name="agentware"):
    """Clone `source` into a directory that must be EMPTY. Returns (path, notes).

    The emptiness assertion is the point: "from an empty directory" is the claim,
    so the gate refuses to run against a directory that already holds state it
    could accidentally be relying on.
    """
    source = os.path.abspath(source or REPO_ROOT)
    if os.path.exists(dest_parent) and os.listdir(dest_parent):
        raise ValueError("destination is not empty: %s" % dest_parent)
    os.makedirs(dest_parent, exist_ok=True)
    dest = os.path.join(dest_parent, name)
    notes = []
    rc, out = _git(["clone", "--quiet", "--no-tags", source, dest], dest_parent)
    if rc != 0:
        raise RuntimeError("git clone failed (%d): %s" % (rc, out.strip()))
    rc, head = _git(["rev-parse", "--short", "HEAD"], dest)
    notes.append("cloned %s at %s" % (os.path.basename(source), head.strip()))
    # Bring the clone up to the working tree for TRACKED files only. An untracked
    # file is one a real clone would not receive, and carrying it here would
    # manufacture the green this gate exists to withhold.
    rc, patch = _git(["diff", "HEAD", "--binary"], source)
    if rc == 0 and patch.strip():
        patch_path = os.path.join(dest_parent, "worktree.patch")
        with open(patch_path, "w", encoding="utf-8") as f:
            f.write(patch)
        rc, out = _git(["apply", patch_path], dest)
        notes.append("applied the working-tree diff of tracked files" if rc == 0
                     else "WARNING: working-tree patch did not apply: %s"
                          % out.strip()[:200])
    else:
        notes.append("working tree is clean; the clone is HEAD verbatim")
    rc, untracked = _git(["ls-files", "--others", "--exclude-standard"], source)
    stragglers = [ln for ln in untracked.splitlines() if ln.strip()]
    if stragglers:
        notes.append("NOT carried into the clone (untracked, %d): %s"
                     % (len(stragglers), ", ".join(sorted(stragglers)[:8])))
    return dest, notes


def from_empty(source=None, echo=True):
    """Clone into an empty directory and run the ONE documented command there."""
    before = _real_config_digest()
    tmp = tempfile.mkdtemp(prefix="agentware-freshclone-")
    empty = os.path.join(tmp, "empty")
    failures, notes = [], []
    try:
        clone, notes = clone_into_empty(empty, source)
        for note in notes:
            if note.startswith("WARNING"):
                failures.append(note)
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        env = _hermetic_env(home, os.path.join(tmp, "kb"))
        argv = BOOTSTRAP_COMMAND.split()
        if argv and argv[0] == "python3":
            argv[0] = sys.executable
        rc, out = _run(argv, clone, env)
        if rc != 0:
            failures.append("the documented command exited %d in the fresh "
                            "clone:\n%s" % (rc, _indent(out)))
        elif echo:
            notes.append("`%s` -> rc 0 in the clone" % BOOTSTRAP_COMMAND)
    except (ValueError, RuntimeError) as exc:
        failures.append(str(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if before != _real_config_digest():
        failures.append("HERMETICITY: the operator's real ~/.agentware/"
                        "config.env CHANGED during the fresh-clone bootstrap")
    if echo:
        for note in notes:
            sys.stdout.write("  note: %s\n" % note)
        # Flush before the report: notes go to stdout and failures to stderr, and
        # an unflushed stdout puts the reason for a red BELOW the red in a CI log.
        sys.stdout.flush()
        _report("fresh-clone bootstrap", source or REPO_ROOT, failures)
    return failures


def _indent(text, lines=20):
    kept = [ln for ln in text.splitlines() if ln.strip()][-lines:]
    return "".join("     | %s\n" % ln for ln in kept)


def _report(what, root, failures):
    for failure in failures:
        sys.stderr.write("FAIL %s\n" % failure)
    stream = sys.stderr if failures else sys.stdout
    stream.write("%s %s (%s)\n"
                 % (what, "FAILED" if failures else "OK",
                    os.path.relpath(root, os.path.dirname(REPO_ROOT))))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="tests/_bootstrap_gate.py",
        description="Prove a fresh clone reaches a working state with one "
                    "documented command.")
    ap.add_argument("--health", action="store_true",
                    help="run the health sequence against this tree")
    ap.add_argument("--from-empty", dest="from_empty", action="store_true",
                    help="clone into an empty directory, then run the "
                         "documented command inside the clone")
    ap.add_argument("--source", default=None,
                    help="repository to clone (default: this checkout)")
    ap.add_argument("--command", action="store_true",
                    help="print the one documented command and exit")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.command:
        sys.stdout.write(BOOTSTRAP_COMMAND + "\n")
        return 0
    if not args.health and not args.from_empty:
        args.health = True
    failures = []
    if args.from_empty:
        failures += from_empty(args.source)
    if args.health:
        failures += health(args.source)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
