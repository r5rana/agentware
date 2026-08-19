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


@unittest.skipUnless(HAVE_GIT, "git not available")
class KbBenchmarkLedgerIsASinkTest(unittest.TestCase):
    """The canonical benchmark ledger survives the blob cap (task 59).

    `kb_git_stage_allowlist` drops any blob over `KB_BLOB_MAX_BYTES` unless it is
    a committed SINK — and its own comment says "benchmarks history-*.jsonl ...
    are text/committed sink data and are ALLOWED explicitly". The pattern was
    `base.startswith("history-")`, which misses the canonical ledger
    `benchmarks/history.jsonl` (`retrieval.ledger.HISTORY_REL`) by one character.

    That gap is not hypothetical and not stable: the ledger is APPEND-ONLY, so it
    grows with every `eval --record` and is guaranteed to cross a fixed byte cap
    eventually. On the day it does, the KB commit silently stops carrying it
    while `releasegate._gate2_lite_gold_fixture` and `_gate3_reliability_delta`
    keep reading the LAST COMMITTED row as if it were current -- a stale baseline
    that reports PASS. Reproduced on this KB at 263,311 bytes vs the 262,144 cap.

    Both halves are asserted: the over-cap ledger is ALLOWED, and an ordinary
    over-cap file is still REJECTED, so the fix cannot be satisfied by disabling
    the cap.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-test-kbsink-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cli = load_cli()

    def _seed(self, repo, *rels):
        """Create + COMMIT each path small, so git reports it individually.

        `git status --porcelain` collapses an untracked directory to a single
        `dir/` entry, so an untracked file is never even seen by the per-path
        guard. The live KB the bug was found on has a TRACKED ledger, and that is
        the state the cap actually applies to -- so the fixture must reproduce it.
        """
        for rel in rels:
            abs_p = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(abs_p), exist_ok=True)
            with open(abs_p, "w", encoding="utf-8") as f:
                f.write("seed\n")
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "seed sink fixtures")

    def _over_cap(self, repo, rel):
        from agentware_lib.ops import staging
        abs_p = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(abs_p), exist_ok=True)
        with open(abs_p, "w", encoding="utf-8") as f:
            f.write("x" * (staging.KB_BLOB_MAX_BYTES + 1024))
        return abs_p

    def test_over_cap_benchmark_ledger_is_staged_and_a_plain_blob_is_not(self):
        from agentware_lib.ops import staging
        repo = _init_repo(os.path.join(self.tmp, "kb"))
        self._seed(repo, "benchmarks/history.jsonl",
                   "benchmarks/history-gold-fixture.jsonl", "references/huge.md")
        self._over_cap(repo, "benchmarks/history.jsonl")
        self._over_cap(repo, "benchmarks/history-gold-fixture.jsonl")
        self._over_cap(repo, "references/huge.md")
        allow, rejected = staging.kb_git_stage_allowlist(repo, "")
        self.assertIn("benchmarks/history.jsonl", allow,
                      "the canonical ledger must survive the blob cap")
        self.assertIn("benchmarks/history-gold-fixture.jsonl", allow,
                      "the per-suite ledger sibling must survive too")
        rejected_paths = [p for p, _why in rejected]
        self.assertIn("references/huge.md", rejected_paths,
                      "the cap must still reject an ordinary over-cap blob")
        self.assertNotIn("benchmarks/history.jsonl", rejected_paths)

    def test_the_ledger_actually_reaches_the_commit(self):
        """End to end: `kb-git commit` carries an over-cap ledger into HEAD."""
        repo = _init_repo(os.path.join(self.tmp, "kb2"))
        self._seed(repo, "benchmarks/history.jsonl")
        self._over_cap(repo, "benchmarks/history.jsonl")
        ns = types.SimpleNamespace(path=repo, tag="bench", type="chore",
                                   message="ledger", format="text")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.cli.cmd_kb_git_commit(ns)
        self.assertEqual(rc, 0, buf.getvalue())
        # NOT `ls-files`: the seed commit already tracks the path, so presence
        # proves nothing and passes with the fix reverted. Assert the committed
        # CONTENT is the grown ledger -- that is what "carried into HEAD" means.
        from agentware_lib.ops import staging
        blob = _run(repo, "cat-file", "-s", "HEAD:benchmarks/history.jsonl")
        self.assertGreater(int(blob.stdout.strip()), staging.KB_BLOB_MAX_BYTES,
                           "HEAD still holds the pre-growth ledger — the commit "
                           "skipped it: %s" % buf.getvalue())


class KbCommittedSinkPredicateTest(unittest.TestCase):
    """`_is_committed_sink` -- the sink rule as a predicate (task 59).

    The rule was a 4-clause inline boolean inside `kb_git_stage_allowlist`, which
    was AT its declared branch debt; adding the canonical ledger pushed it over
    and the ceiling ratchet refused it. Extracting the decision is the fix (the
    debt row is retired, not raised) -- and an extraction is a BEHAVIOURAL claim,
    so every shape it must answer is asserted here directly rather than only
    through the fold that calls it. All four allow shapes, plus the negatives
    that keep the exemption from swallowing ordinary files.
    """

    def _sink(self, inner):
        from agentware_lib.ops import staging
        return staging._is_committed_sink(inner, inner.rsplit("/", 1)[-1])

    def test_every_sink_shape_is_recognised(self):
        for inner in ("teams/core/fleet/rahul.jsonl",
                      "fleet/rahul.jsonl",
                      "benchmarks/history.jsonl",
                      "benchmarks/history-gold-fixture.jsonl",
                      "configurations/assets/diagram.png",
                      "configurations/assets/DIAGRAM.SVG"):
            with self.subTest(inner=inner):
                self.assertTrue(self._sink(inner), inner)

    def test_ordinary_files_are_not_sinks(self):
        for inner in ("learnings/a.md",
                      "benchmarks/README.md",          # not .jsonl
                      "benchmarks/historyx.jsonl",     # neither canonical nor history-
                      "notes/history.jsonl",           # canonical NAME, wrong dir
                      "fleet/notes.md",                # in fleet, not .jsonl
                      "configurations/assets/dump.har"):
            with self.subTest(inner=inner):
                self.assertFalse(self._sink(inner), inner)

    def test_the_predicate_matches_the_fold_that_uses_it(self):
        """The extraction preserved behaviour: same inputs, same allow decision."""
        from agentware_lib.ops import staging
        for inner in ("benchmarks/history.jsonl", "learnings/a.md",
                      "teams/core/fleet/x.jsonl", "configurations/assets/a.png"):
            with self.subTest(inner=inner):
                base = inner.rsplit("/", 1)[-1]
                self.assertEqual(staging._is_committed_sink(inner, base),
                                 self._sink(inner))
