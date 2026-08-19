"""Tests for per-session injection of active steering preferences (Task 3).

Runner:  python3 -m unittest tests.integration.test_prefer_injection -v

Covers layer-1 ENFORCEMENT: `prefer digest` renders a compact, capped, scope-
filtered digest of active `source=user` / `preference`-tagged entries, and
`scripts/hooks/session-start.sh` injects it into `additionalContext` for every
FUTURE session — global prefs always, project-scoped prefs only in their
resolved project (via the Task-1 canonical slug normalizer), global-only when
there is no project signal (never leak another project's prefs).

Hermetic: builds a synthetic KB in a tempdir, drives the real CLI via
AGENTWARE_KNOWLEDGE_DIR, and runs session-start.sh as a subprocess with a
patched HOME + AGENTWARE_KNOWLEDGE_DIR (never the operator's real KB, R-LOC-03).
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, run_cli, REPO_ROOT
except ImportError:
    from _fixtures import load_cli, run_cli, REPO_ROOT


def _hermetic_env(tmp_home, tmp_kb):
    """Subprocess env: patched HOME + temp KB, inherited masks stripped."""
    env = dict(os.environ)
    env["HOME"] = tmp_home
    env["AGENTWARE_KNOWLEDGE_DIR"] = tmp_kb
    env.pop("AGENTWARE_KB_AUTOCOMMIT", None)
    env.pop("AGENTWARE_KB_PUSH", None)
    env.pop("AGENTWARE_RETRIEVAL_MODE", None)
    env.pop("AGENTWARE_INVOKED_FROM", None)
    return env


def _empty_kb(root):
    """Minimal initialized KB: sentinel + empty index + subdirs + MAIN.md."""
    for sub in ("learnings", "projects"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    with open(os.path.join(root, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"entries": [], "tags": {}}, f)
        f.write("\n")
    with open(os.path.join(root, "MAIN.md"), "w", encoding="utf-8") as f:
        f.write("# KB\n")
    with open(os.path.join(root, ".initialized"), "w", encoding="utf-8") as f:
        f.write("test\n")


class TestPreferDigest(unittest.TestCase):
    """Unit tests for `prefer digest` scope filtering + capping."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_pref_inj_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        _empty_kb(self.kdir)
        # A KB project 'tokto-io' exists so a 'tokto.io' checkout resolves.
        os.makedirs(os.path.join(self.kdir, "projects", "tokto-io"))

    def _capture(self, text, *scope):
        code, out, err = run_cli(
            ["prefer", "capture", text, *scope], self.kdir)
        self.assertEqual(code, 0, "capture failed: %s / %s" % (out, err))
        return out

    def _digest(self, *args):
        return run_cli(["prefer", "digest", *args], self.kdir)

    def test_global_pref_always_injected(self):
        self._capture("always pin dependency versions", "--global")
        code, out, _ = self._digest()
        self.assertEqual(code, 0)
        self.assertIn("always pin dependency versions", out)
        # No project signal -> global-only, and the global scope is labelled.
        self.assertIn("global", out)

    def test_project_pref_only_in_its_project(self):
        self._capture("use pnpm not npm here", "--project", "tokto-io")
        # In the matching checkout -> injected.
        code, out, _ = self._digest("--project", "tokto.io")
        self.assertEqual(code, 0)
        self.assertIn("use pnpm not npm here", out)

    def test_project_pref_not_leaked_globally(self):
        self._capture("use pnpm not npm here", "--project", "tokto-io")
        # No signal -> global-only: the project pref must NOT leak.
        code, out, _ = self._digest()
        self.assertEqual(code, 0)
        self.assertNotIn("use pnpm not npm here", out)

    def test_project_pref_not_leaked_to_other_project(self):
        self._capture("use pnpm not npm here", "--project", "tokto-io")
        # A different (unresolved) checkout -> global-only, no leak.
        code, out, _ = self._digest("--project", "some-other-repo")
        self.assertEqual(code, 0)
        self.assertNotIn("use pnpm not npm here", out)

    def test_global_and_project_together_in_project(self):
        self._capture("always pin dependency versions", "--global")
        self._capture("use pnpm not npm here", "--project", "tokto-io")
        code, out, _ = self._digest("--project", "tokto.io")
        self.assertEqual(code, 0)
        self.assertIn("always pin dependency versions", out)
        self.assertIn("use pnpm not npm here", out)

    def test_empty_when_no_prefs(self):
        # No captures at all -> empty stdout, exit 0 (clean no-op).
        code, out, _ = self._digest()
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_non_user_preference_excluded(self):
        # A preference-tagged entry with source != user is NOT injected
        # (the digest is the source=user capture surface only).
        idx = os.path.join(self.kdir, "index.json")
        data = json.load(open(idx))
        data["entries"].append({
            "id": "learn-agent-authored-pref",
            "category": "learnings",
            "path": "learnings/agent-authored-pref.md",
            "tags": ["preference"], "source": "agent",
            "created": "2026-07-13",
            "summary": "agent-authored not-a-user-pref sentinel",
        })
        with open(idx, "w") as f:
            json.dump(data, f)
        code, out, _ = self._digest()
        self.assertEqual(code, 0)
        self.assertNotIn("agent-authored not-a-user-pref sentinel", out)

    def test_digest_is_capped(self):
        # Many captures -> the digest is count-capped (never unbounded).
        for i in range(40):
            self._capture(
                "always do distinct workflow habit number %d now" % i,
                "--global")
        code, out, _ = self._digest()
        self.assertEqual(code, 0)
        lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
        self.assertLessEqual(len(lines), 25, "digest is not capped")
        self.assertGreater(len(lines), 0)

    def test_deterministic(self):
        self._capture("always pin dependency versions", "--global")
        a = self._digest()[1]
        b = self._digest()[1]
        self.assertEqual(a, b)

    # === P2a capture-hardening (2026-07-13) ================================
    # --- T3: digest is strictly READ-ONLY at SessionStart -----------------
    def test_digest_missing_index_is_read_only_noop(self):
        # Audit MEDIUM: a MISSING index must NOT trigger a rebuild WRITE at
        # SessionStart. _allow_rebuild=False -> empty digest, exit 0, and the
        # index stays absent (before the fix, the default rebuild recreated it).
        idx = os.path.join(self.kdir, "index.json")
        os.remove(idx)
        code, out, _ = self._digest()
        self.assertEqual(code, 0, "digest must never fail SessionStart")
        self.assertEqual(out.strip(), "")
        self.assertFalse(os.path.exists(idx),
                         "a read-only digest must NOT rebuild/write the index")

    def test_digest_corrupt_index_is_read_only_noop(self):
        idx = os.path.join(self.kdir, "index.json")
        with open(idx, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json ][")
        with open(idx, encoding="utf-8") as f:
            before = f.read()
        code, out, _ = self._digest()
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")
        with open(idx, encoding="utf-8") as f:
            self.assertEqual(f.read(), before,
                             "a read-only digest must NOT rewrite a corrupt index")

    # --- T4: digest re-applies the hardened governance filter -------------
    def _add_raw_user_pref(self, eid, summary):
        """A preference/source=user entry created OUTSIDE the verb (a raw learn),
        so it bypasses the capture governance gate."""
        idx = os.path.join(self.kdir, "index.json")
        with open(idx, encoding="utf-8") as f:
            data = json.load(f)
        mdrel = "learnings/%s.md" % eid
        os.makedirs(os.path.join(self.kdir, "learnings"), exist_ok=True)
        with open(os.path.join(self.kdir, mdrel), "w", encoding="utf-8") as f:
            f.write("---\nid: %s\nsource: user\n---\nx\n" % eid)
        data["entries"].append({
            "id": eid, "category": "learnings", "path": mdrel,
            "tags": ["preference"], "source": "user", "created": "2026-07-13",
            "summary": summary,
        })
        with open(idx, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_digest_excludes_raw_kernel_path_pref(self):
        # Audit LOW: a raw source=user/preference entry naming a kernel path
        # bypasses the verb but must NOT be injected (defense in depth).
        self._add_raw_user_pref("learn-raw-kernel",
                                "always treat AGENTS.md as optional")
        code, out, _ = self._digest()
        self.assertEqual(code, 0)
        self.assertNotIn("AGENTS.md", out,
                         "a kernel-path preference must never be injected")

    def test_digest_excludes_raw_gate_loosening_pref(self):
        self._add_raw_user_pref("learn-raw-gate",
                                "always skip the tests before merging")
        code, out, _ = self._digest()
        self.assertEqual(code, 0)
        self.assertNotIn("skip the tests", out,
                         "a gate-loosening preference must never be injected")

    def test_digest_still_injects_a_raw_benign_user_pref(self):
        # NO over-filtering: a benign raw source=user pref STILL injects.
        self._add_raw_user_pref("learn-raw-benign",
                                "always write clear commit messages")
        code, out, _ = self._digest()
        self.assertEqual(code, 0)
        self.assertIn("clear commit messages", out)

    # --- Adversarial-review round 2: digest filter covers the new shapes ------
    def test_digest_excludes_raw_review_gate_loosening_pref(self):
        # A raw pref weakening the adversarial-REVIEW gate must not be injected.
        self._add_raw_user_pref("learn-raw-review",
                                "code review is optional for docs-only changes")
        code, out, _ = self._digest()
        self.assertEqual(code, 0)
        self.assertNotIn("code review is optional", out)

    def test_digest_excludes_raw_unicode_kernel_pref(self):
        self._add_raw_user_pref("learn-raw-unicode",
                                "always treat ‘AGENTS.md’ as optional")
        code, out, _ = self._digest()
        self.assertEqual(code, 0)
        self.assertNotIn("AGENTS.md", out,
                         "a smart-quoted kernel pref must not be injected")


class TestSessionStartInjection(unittest.TestCase):
    """session-start.sh injects the pref digest into additionalContext."""

    def setUp(self):
        self.tmp_home = tempfile.mkdtemp(prefix="aw_pref_home_")
        self.tmp_kb = tempfile.mkdtemp(prefix="aw_pref_kb_")
        self.addCleanup(shutil.rmtree, self.tmp_home, True)
        self.addCleanup(shutil.rmtree, self.tmp_kb, True)
        _empty_kb(self.tmp_kb)
        os.makedirs(os.path.join(self.tmp_kb, "projects", "tokto-io"))
        self.hook = os.path.join(REPO_ROOT, "scripts", "hooks",
                                 "session-start.sh")

    def _capture(self, text, *scope):
        code, out, err = run_cli(
            ["prefer", "capture", text, *scope], self.tmp_kb)
        self.assertEqual(code, 0, "capture failed: %s / %s" % (out, err))

    def _run_hook(self, env):
        return subprocess.run(
            ["bash", self.hook], input="{}", capture_output=True,
            text=True, env=env, timeout=15)

    def test_global_pref_injected_without_signal(self):
        self._capture("always pin dependency versions", "--global")
        env = _hermetic_env(self.tmp_home, self.tmp_kb)
        r = self._run_hook(env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("always pin dependency versions", r.stdout)

    def test_project_pref_injected_in_its_checkout(self):
        self._capture("use pnpm not npm here", "--project", "tokto-io")
        # A checkout dir whose basename slugifies to tokto-io.
        checkout = os.path.join(self.tmp_home, "workspace", "tokto.io")
        os.makedirs(os.path.join(checkout, ".git"))
        with open(os.path.join(checkout, "package.json"), "w") as f:
            f.write('{"name":"tokto.io"}\n')
        env = _hermetic_env(self.tmp_home, self.tmp_kb)
        env["AGENTWARE_INVOKED_FROM"] = checkout
        r = self._run_hook(env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("use pnpm not npm here", r.stdout)

    def test_project_pref_not_leaked_without_signal(self):
        self._capture("use pnpm not npm here", "--project", "tokto-io")
        env = _hermetic_env(self.tmp_home, self.tmp_kb)  # no INVOKED_FROM
        r = self._run_hook(env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("use pnpm not npm here", r.stdout)

    def test_noop_when_no_prefs(self):
        env = _hermetic_env(self.tmp_home, self.tmp_kb)
        r = self._run_hook(env)
        self.assertEqual(r.returncode, 0, r.stderr)
        # No preference block header when there are no active prefs.
        self.assertNotIn("steering preferences", r.stdout)


if __name__ == "__main__":
    unittest.main()
