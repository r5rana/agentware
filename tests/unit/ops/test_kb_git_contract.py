"""KbNothingLostGateTest, KbCommitSubjectContractTest -- split from `test_kb_git` (task 58).

See `tests.unit.ops._kb_git_support` for the
shared header.
"""

from tests.unit.ops._kb_git_support import (  # noqa: F401
    HAVE_GIT, _commit_count, _head_subject, _init_repo, _run, _seed_entry,
    _write_entry, contextlib, io, load_cli, os, shutil, tempfile, types,
    unittest)


@unittest.skipUnless(HAVE_GIT, "git not available")
class KbNothingLostGateTest(unittest.TestCase):
    """Task 6.1 — mechanical nothing-lost ID-superset gate (C-2) + bounded retry."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-test-kbgate-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cli = load_cli()

    def _mk(self, name):
        return os.path.join(self.tmp, name)

    def _build_frontmatter_kb(self, repo, eid="alpha"):
        for sub in ("learnings", "projects", "configurations", "prompts",
                    "references", "skills"):
            os.makedirs(os.path.join(repo, sub), exist_ok=True)
        _write_entry(self.cli, repo, _seed_entry(eid))
        _data, _r, errors = self.cli.rebuild_kb(repo)
        self.assertEqual(errors, [], "seed rebuild must be clean")
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "seed kb")

    def _add_learning_and_commit(self, repo, eid):
        _write_entry(self.cli, repo, _seed_entry(eid))
        _data, _r, errors = self.cli.rebuild_kb(repo)
        self.assertEqual(errors, [])
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "add %s" % eid)

    def _rev(self, repo, ref="HEAD"):
        return _run(repo, "rev-parse", ref).stdout.strip()

    # --- gate core ---------------------------------------------------------

    def test_gate_rejects_dropped_id(self):
        """A merge whose result is missing a parent's entry id is REJECTED."""
        repo = _init_repo(self._mk("drop"), with_commit=False)
        self._build_frontmatter_kb(repo, "alpha")        # HEAD: alpha
        self._add_learning_and_commit(repo, "beta")      # HEAD: alpha + beta
        both = self._rev(repo)                            # parent with BOTH ids
        # Now drop beta from the work tree (simulating a lossy merge result).
        os.remove(os.path.join(repo, "learnings", "beta.md"))
        self.cli.rebuild_kb(repo)
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "lossy: drop beta")

        ok, note = self.cli.kb_nothing_lost_gate(repo, [both, "HEAD"])
        self.assertFalse(ok)
        self.assertIn("DROP", note)
        self.assertIn("learn-beta", note)

    def test_gate_passes_clean_union(self):
        """A result that is a superset of both parents PASSES the gate."""
        repo = _init_repo(self._mk("union"), with_commit=False)
        self._build_frontmatter_kb(repo, "alpha")        # P1: alpha
        p1 = self._rev(repo)
        self._add_learning_and_commit(repo, "beta")      # HEAD: alpha + beta
        ok, note = self.cli.kb_nothing_lost_gate(repo, [p1, "HEAD"])
        self.assertTrue(ok, note)
        self.assertIn("ok", note)

    def test_gate_skips_unresolvable_parent(self):
        """A parent ref that does not resolve is skipped, not counted as empty."""
        repo = _init_repo(self._mk("skip"), with_commit=False)
        self._build_frontmatter_kb(repo, "alpha")
        ok, note = self.cli.kb_nothing_lost_gate(
            repo, ["HEAD", "refs/does/not/exist"])
        self.assertTrue(ok, note)

    def test_entry_ids_at_ref(self):
        """git_entry_ids_at_ref reads entry ids from a ref tree (no checkout)."""
        repo = _init_repo(self._mk("ids"), with_commit=False)
        self._build_frontmatter_kb(repo, "alpha")
        self._add_learning_and_commit(repo, "beta")
        ids = self.cli.git_entry_ids_at_ref(repo, "HEAD")
        self.assertEqual(ids, {"learn-alpha", "learn-beta"})
        self.assertIsNone(
            self.cli.git_entry_ids_at_ref(repo, "refs/nope"))

    # --- bounded retry -----------------------------------------------------

    def test_bounded_retry_fails_loud_after_cap(self):
        """A persistent re-push race retries exactly max_retries times, then rc 1."""
        calls = {"n": 0}

        def fake_push_once(target, on_prose_conflict="abort"):
            calls["n"] += 1
            return self.cli.RC_REPUSH_RACE, "race"

        saved = self.cli.kb_git_push_once
        self.cli.kb_git_push_once = fake_push_once
        try:
            rc, note = self.cli.kb_git_push_synced(
                self._mk("unused"), max_retries=3)
        finally:
            self.cli.kb_git_push_once = saved
        self.assertEqual(calls["n"], 3)          # bounded — no unbounded loop
        self.assertEqual(rc, 1)                  # fail loud (race mapped to 1)
        self.assertIn("re-push race", note)

    def test_bounded_retry_succeeds_within_cap(self):
        """A race that clears before the cap returns the eventual success."""
        seq = [self.cli.RC_REPUSH_RACE, self.cli.RC_REPUSH_RACE, 0]
        calls = {"n": 0}

        def fake_push_once(target, on_prose_conflict="abort"):
            rc = seq[calls["n"]]
            calls["n"] += 1
            return rc, ("pushed" if rc == 0 else "race")

        saved = self.cli.kb_git_push_once
        self.cli.kb_git_push_once = fake_push_once
        try:
            rc, note = self.cli.kb_git_push_synced(
                self._mk("unused"), max_retries=3)
        finally:
            self.cli.kb_git_push_once = saved
        self.assertEqual(calls["n"], 3)
        self.assertEqual(rc, 0)
        self.assertIn("pushed", note)

    def test_non_race_failure_returns_immediately(self):
        """A fail-loud rc 1 (not a race) is returned without retrying."""
        calls = {"n": 0}

        def fake_push_once(target, on_prose_conflict="abort"):
            calls["n"] += 1
            return 1, "non-derived conflict"

        saved = self.cli.kb_git_push_once
        self.cli.kb_git_push_once = fake_push_once
        try:
            rc, note = self.cli.kb_git_push_synced(
                self._mk("unused"), max_retries=3)
        finally:
            self.cli.kb_git_push_once = saved
        self.assertEqual(calls["n"], 1)          # no retry on a non-race failure
        self.assertEqual(rc, 1)

@unittest.skipUnless(HAVE_GIT, "git not available")
class KbCommitSubjectContractTest(unittest.TestCase):
    """The subject derivation extracted out of the handler (task 57).

    `cmd_kb_git_commit` grew past both the line and the branch cap, so its
    densest branch cluster -- subject derivation, a pure function of (args,
    changed paths) -- moved to `_kb_commit_subject`. That makes it a
    `decomposed` row in the body-adjudication fixture, and a `decomposed`
    rationale is a BEHAVIOURAL CLAIM rather than a bookkeeping note: the body
    gate can only see that the characters still exist SOMEWHERE in the host plus
    its absorbers, never that the capability survived the move.

    The four DERIVATION branches were already covered end-to-end by the sibling
    tests above, and those are left untouched -- re-asserting them here would
    prove nothing the pre-existing tests do not. What the extraction ADDED, and
    what nothing covered, is a new inter-function contract: the refusal is now
    signalled by RETURNING None, and the handler must translate that None into
    "exit 1 and commit nothing".

    That branch is unreachable from the CLI -- `--type` is
    `choices=("feat", "chore")`, `_sanitize_commit_tag` cannot emit an invalid
    tag (it falls back to `kb`), and the message is never empty (C-2) -- so it
    is a defensive guard, which is precisely the kind of thing a refactor drops
    with no test noticing and no gate able to tell.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-test-kbsubj-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cli = load_cli()

    def _mk(self, name):
        return os.path.join(self.tmp, name)

    @staticmethod
    def _ns(**kw):
        base = dict(path=None, tag="msgfix", type="chore", message=None)
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_subject_refusal_returns_none_rather_than_a_malformed_subject(self):
        """A subject failing the conventional-commit shape yields None, loudly."""
        from agentware_lib.ops import staging
        # `wip` is not in (feat|chore), so the assembled subject cannot match
        # COMMIT_MSG_RE. Reached directly, because the parser makes the CLI
        # route to it impossible by design.
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            subject = staging._kb_commit_subject(
                self._ns(type="wip", message="a real summary"),
                ["learnings/a.md"])
        self.assertIsNone(subject, "a malformed subject was returned, not refused")
        self.assertIn("refusing", err.getvalue())
        # The refusal NAMES the offending subject, so the operator sees why.
        self.assertIn("wip(msgfix): a real summary", err.getvalue())

    def test_subject_refusal_makes_the_handler_exit_1_and_commit_nothing(self):
        """The handler HONOURS the None -- the half of the contract it owns.

        Locks the WIRING, not the derivation: with the helper forced to refuse,
        a handler that ignored the None would commit a malformed subject or
        crash on it. Reddens if the `if subject is None: return 1` arm is
        dropped, or if the helper is ever changed to fall back to a default
        subject instead of refusing.
        """
        from unittest import mock
        from agentware_lib.ops import staging
        repo = _init_repo(self._mk("refuse"))
        os.makedirs(os.path.join(repo, "learnings"))
        with open(os.path.join(repo, "learnings", "a.md"), "w",
                  encoding="utf-8") as f:
            f.write("# a\n")
        before = _commit_count(repo)
        with mock.patch.object(staging, "_kb_commit_subject", return_value=None):
            rc = self.cli.cmd_kb_git_commit(self._ns(path=repo))
        self.assertEqual(rc, 1, "the handler ignored the refusal")
        self.assertEqual(_commit_count(repo), before, "it committed anyway")
        # Nothing staged either: derivation refuses BEFORE the staging half.
        staged = _run(repo, "diff", "--cached", "--name-only").stdout.strip()
        self.assertEqual(staged, "", "a refused commit left files staged")

    def test_subject_helper_is_the_derivation_the_handler_actually_commits(self):
        """Round-trip per branch: the helper's output IS the committed subject.

        The extraction's real risk is a PARTIAL copy -- a helper reproducing the
        common branch while the handler quietly keeps its own path for the rest.
        Each case derives the subject from the SAME (args, changed) the handler
        will see, then runs the real commit and asserts the two agree, so a
        divergence between the two halves cannot pass unnoticed.
        """
        from agentware_lib.ops import staging
        cases = [
            ("topics", ["learnings/alpha-topic.md", "learnings/beta-topic.md"],
             {}),                                              # default <- topics
            ("suffix", ["learnings/gamma.md"],
             {"message": "my feature title", "type": "feat"}),  # message + topics
            ("dirlist", ["references/x.md"], {}),               # fallback <- dirs
            ("verbatim", ["notes.md"],
             {"message": "add recall learning", "type": "feat", "tag": "Recall"}),
        ]
        for name, rels, kw in cases:
            with self.subTest(branch=name):
                repo = _init_repo(self._mk(name))
                for rel in rels:
                    abs_p = os.path.join(repo, rel)
                    os.makedirs(os.path.dirname(abs_p), exist_ok=True)
                    with open(abs_p, "w", encoding="utf-8") as f:
                        f.write("x\n")
                _run(repo, "add", "-A")
                ns = self._ns(path=repo, **kw)
                # Same inputs the handler derives from, read the same way.
                changed = self.cli.git_changed_paths(repo)
                self.assertTrue(changed, "fixture staged nothing for %s" % name)
                derived = staging._kb_commit_subject(ns, changed)
                self.assertIsNotNone(derived, "branch %s refused" % name)
                rc = self.cli.cmd_kb_git_commit(ns)
                self.assertEqual(rc, 0, "commit failed for branch %s" % name)
                self.assertEqual(
                    _head_subject(repo), derived,
                    "the handler committed a subject the helper did not derive")
