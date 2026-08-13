"""The add-a-feature walkthrough, EXECUTED (task 75).

`docs/add-a-feature.md` claims a contributor can go from nothing to a green tree
by following it in order. This module makes that claim falsifiable: it copies the
repository into a scratch checkout, applies every block of the document in
document order — writing the files it shows, applying the hunks it quotes,
running the commands it lists — and reads every exit code.

WHY THE DOCUMENT IS THE INPUT, and not a script beside it
---------------------------------------------------------
A walkthrough verified by a *parallel* script is verified only until someone
edits one of the two. Driving the real markdown means a step that is wrong on the
page is wrong in the run: rename a pin, drop a generator, quote a line that no
longer exists, and this goes red naming the block. The `-`/`+` hunks are the
sharpest arm — a hunk only applies if the line it quotes is really in the tree at
that moment, so the document cannot describe a file it has stopped matching.

Scope, stated honestly:

  * every ```` ```bash ```` block runs and must exit 0; the one
    ```` ```bash expect-fail ```` block must exit NON-zero (it is the mutation
    proof, whose evidence is a red run);
  * two blocks are marked ```` norun: ```` with a declared reason — the
    four-tier suite (a suite cannot run itself) and the R-PKG gate chain (it
    records eval baselines and resolves the operator's real knowledge dir).
    `tests/_walkthrough_check.py` arm E pins that allowlist, so the unexecuted
    set cannot quietly grow;
  * the scratch tree is a COPY, never the live one, and every child runs with a
    patched HOME and a temp knowledge dir, so nothing here can reach the
    operator's config or KB.

The whole pass costs a few seconds: the walkthrough's commands are the scaffold,
five generators and two targeted unittest runs — no tier discovery anywhere.
"""

from tests._paths import REPO_ROOT, TESTS_DIR

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

import _walkthrough_check as wc  # noqa: E402

#: Copied out: git metadata, build output and caches. None of them is claimed by
#: `catalog/modules.json`, so the manifest regenerator sees the same corpus it
#: sees in the real tree — which is the property that makes `--check` meaningful
#: in the scratch at all.
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "node_modules",
                                "dist", ".venv", ".pytest_cache")

#: A command may not run forever: a walkthrough step that hangs is worse than one
#: that fails, because it cannot be told from a slow one without checking CPU.
STEP_TIMEOUT = 300


class Runner(object):
    """Applies one document to one scratch tree, recording every outcome."""

    def __init__(self, root, text):
        self.root = root
        self.text = text
        self.steps = []          # (kind, line, label, rc)

    def env(self, home, kdir):
        environ = dict(os.environ)
        environ.update({
            "HOME": home,
            "AGENTWARE_KNOWLEDGE_DIR": kdir,
            # The suite must never re-spawn the suite through the CLI fixture.
            "AGENTWARE_NESTED_UNITTEST": "1",
            "CI": "true",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        environ.pop("AGENTWARE_ORG_ROOT", None)
        environ.pop("AGENTWARE_TEAM", None)
        return environ

    def write(self, rel, body, line):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(body) + "\n")
        self.steps.append(("write", line, rel, 0))

    def edit(self, rel, body, line):
        """Apply a `-`/`+` hunk. The `-` side must match EXACTLY once."""
        old = "\n".join(l[1:] for l in body if l.startswith("-"))
        new = "\n".join(l[1:] for l in body if l.startswith("+"))
        path = os.path.join(self.root, rel)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        hits = text.count(old)
        if hits != 1:
            self.steps.append(("edit", line, "%s (%d matches)" % (rel, hits), 1))
            return
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))
        self.steps.append(("edit", line, rel, 0))

    def run(self, kind, body, line, env):
        # `set -e` is load-bearing, not hygiene: a bash script exits with the
        # status of its LAST command, so a multi-command block would swallow a
        # failing step in the middle and report the block green. Proven by
        # planting a bad flag on the third of five generator lines.
        script = "set -e\n" + "\n".join(body)
        proc = subprocess.run(["bash", "-c", script], cwd=self.root, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=STEP_TIMEOUT)
        rc = proc.returncode
        ok = (rc != 0) if kind == "run-fail" else (rc == 0)
        label = "%s :: %s" % (body[0] if body else "<empty>",
                              proc.stdout.decode("utf-8", "replace")[-400:])
        self.steps.append((kind, line, label, 0 if ok else (rc or 1)))

    def play(self, env):
        for info, body, line in wc.blocks(self.text):
            kind, arg = wc.classify(info)
            if kind == "write":
                self.write(arg, body, line)
            elif kind == "edit":
                self.edit(arg, body, line)
            elif kind in ("run", "run-fail"):
                self.run(kind, body, line, env)
            elif kind == "norun":
                self.steps.append(("norun", line, arg, 0))
        return self.steps

    def failures(self):
        return [s for s in self.steps if s[3] != 0]


class WalkthroughExecutesTest(unittest.TestCase):
    """One pass over the document; every assertion reads the recorded steps."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="aw-walkthrough-e2e-")
        cls.root = os.path.join(cls.tmp, "checkout")
        home = os.path.join(cls.tmp, "home")
        kdir = os.path.join(cls.tmp, "kb")
        os.makedirs(home)
        os.makedirs(kdir)
        shutil.copytree(REPO_ROOT, cls.root, ignore=IGNORE, symlinks=True)
        cls.text = wc.read(REPO_ROOT, wc.WALKTHROUGH)
        cls.runner = Runner(cls.root, cls.text)
        cls.steps = cls.runner.play(cls.runner.env(home, kdir))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def kinds(self, kind):
        return [s for s in self.steps if s[0] == kind]

    # ------------------------------------------------------------------ arms

    def test_every_command_in_the_walkthrough_succeeds(self):
        """The plan's acceptance criterion, read literally."""
        failed = ["line %d: rc=%d %s" % (line, rc, label)
                  for kind, line, label, rc in self.steps
                  if rc != 0 and kind in ("run", "run-fail")]
        self.assertEqual(failed, [], "\n".join(failed))

    def test_every_quoted_hunk_still_matches_the_tree(self):
        """A `-` side that matches zero or many places is a rotted quotation."""
        failed = ["line %d: %s" % (line, label)
                  for kind, line, label, rc in self.steps
                  if kind == "edit" and rc != 0]
        self.assertEqual(failed, [], "\n".join(failed))

    def test_the_document_actually_did_something(self):
        """A silent no-op run would satisfy every arm above."""
        self.assertGreaterEqual(len(self.kinds("run")), 10)
        self.assertGreaterEqual(len(self.kinds("write")), 2)
        self.assertGreaterEqual(len(self.kinds("edit")), 6)
        self.assertEqual(len(self.kinds("run-fail")), 1)

    def test_the_unexecuted_blocks_are_exactly_the_declared_ones(self):
        reasons = sorted(label for _k, _l, label, _rc in self.kinds("norun"))
        self.assertEqual(reasons, sorted(wc.ALLOWED_NORUN))

    # -------------------------------------------------- the end state is real

    def test_the_scaffolded_verb_is_served_by_the_scratch_checkout(self):
        proc = subprocess.run([sys.executable, "scripts/agentware", "greet",
                               "--name", "world", "--format", "json"],
                              cwd=self.root, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=STEP_TIMEOUT)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("hello, world", proc.stdout.decode("utf-8", "replace"))

    def test_the_end_state_passes_the_wiring_audit(self):
        """`scaffold check` from the outside: the five wiring questions at once."""
        proc = subprocess.run([sys.executable, "scripts/agentware", "scaffold",
                               "check"], cwd=self.root, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=STEP_TIMEOUT)
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_the_regenerators_are_idempotent_at_the_end_state(self):
        """Re-running a generator writes nothing — so the walkthrough's own
        output is the same output the gate would compute (R-MOD-07)."""
        for argv in (["tests/_manifest_gen.py", "--check"],
                     ["tests/_parity_manifest.py", "--check"],
                     ["tests/_doc_sync.py", "--check"],
                     ["tests/_ceiling_gate.py"],
                     ["tests/_modularity_rules.py"],
                     ["tests/_test_ownership.py"]):
            proc = subprocess.run([sys.executable] + argv, cwd=self.root,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=STEP_TIMEOUT)
            self.assertEqual(proc.returncode, 0,
                             "%s: %s" % (argv[0], proc.stdout))

    def test_the_live_tree_was_not_touched(self):
        """Hermeticity: the worked example must not exist in the real repo."""
        for rel in ("scripts/agentware_lib/ops/greet.py",
                    "tests/unit/ops/test_greet.py"):
            self.assertFalse(os.path.exists(os.path.join(REPO_ROOT, rel)), rel)


if __name__ == "__main__":
    unittest.main()
