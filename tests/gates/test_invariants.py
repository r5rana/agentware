"""Moat-protection guard tests (Phase 0.2).

These tests pin the NON-NEGOTIABLE design invariants that make agentware's
retrieval/measurement spine a competitive moat. If any of these fail, a change
has eroded something agentware is supposed to be great at — treat it as a bug in
the change, not in the test.

  INV-1  Deterministic ranking — `recall`/`eval` are hand-rolled BM25, stdlib
         only, NO network / NO LLM / NO embeddings / NO wall-clock in scoring;
         identical inputs -> byte-identical output across runs.
  INV-2  Read-only retrieval — `recall`/`eval`/`metrics` NEVER mutate index.json
         or any KB file.
  INV-3  System of record unchanged — the git-versioned markdown + index.json
         stay authoritative (asserted via the byte-snapshot in INV-2).
  INV-5  Anti-context-rot — `recall` is token-budgeted and REPLACES the
         whole-corpus dump with a focused, strictly-smaller set.
  INV-6  Zero hard dependencies — every python file in the tree obeys the rule
         its PATH CLASS declares (`tests/_security_policy.py`), and every file
         on disk is claimed by exactly one class.

Stdlib `unittest` only (no pytest, no new deps). Never touches the real KB.
"""

import json
import os
import subprocess
import sys
import unittest

try:
    from tests._fixtures import (SyntheticKBTestCase, load_cli, CLI_PATH,
                                 run_cli, build_synthetic_kb, REPO_ROOT)
    from tests import _security_policy as policy
except ImportError:  # allow `python3 -m unittest tests.gates.test_invariants`
    from _fixtures import (SyntheticKBTestCase, load_cli, CLI_PATH,
                           run_cli, build_synthetic_kb, REPO_ROOT)
    import _security_policy as policy


CLI = load_cli()

# Modules whose presence in a guarded file would mean the retrieval/measurement
# path could reach the network or shell out to a model service (INV-1/INV-6).
# ONE definition, in the policy table — a second copy here is how two scans
# drift into disagreeing about what "network" means.
_NETWORK_MODULES = policy.NETWORK_MODULES

#: Discovered once per process: the walk parses ~350 files and every test in
#: this class asks the same question of the same tree.
_CORPUS = policy.discover_python_files()


class StdlibOnlyTest(unittest.TestCase):
    """INV-6 + INV-1 over the WHOLE tree, one rule per path class.

    These two tests used to walk a single file — `scripts/agentware`. Task 25
    made that file a facade, and the walk went on passing while reading a
    docstring: 0.3% coverage of the code the invariant claims. They now scan
    every path the policy table marks guarded, which is the entrypoint, the
    preserved monolith, all ~115 `agentware_lib` modules, the agent hooks and
    the suite itself.
    """

    @unittest.skipIf(sys.version_info < (3, 10),
                     "sys.stdlib_module_names requires Python 3.10+")
    def test_cli_imports_are_stdlib_only(self):
        # sys.stdlib_module_names is the authoritative stdlib set (py3.10+).
        # First-party in-repo modules are not DEPENDENCIES (see
        # _security_policy.first_party_names): a name resolving to a file the
        # clone already ships installs nothing and cannot go missing.
        findings = policy.dependency_violations(_CORPUS, root=REPO_ROOT)
        self.assertEqual(
            findings, [],
            "non-stdlib imports in stdlib-only path classes (INV-6 violated):\n"
            + policy.format_findings(findings))

    def test_sibling_allowance_is_scoped_to_the_importing_directory(self):
        """The INV-6 sibling allowance must model `sys.path[0]`, not a blanket.

        Task 35 split `scripts/hooks/codex-stream.py`, whose renderer now does
        `from codex_items import ...`. That name resolves because Python puts a
        script's own directory on `sys.path[0]`, so it is first-party — but the
        allowance is only honest while it is SCOPED. Folded into the global
        `first_party_names` set instead, it would excuse `import codex_items`
        from `agentware_lib`, where the interpreter would never find it: a real
        broken import, reported as clean. This pins the scoping directly.
        """
        here = policy.sibling_modules("scripts/hooks/codex-stream.py", REPO_ROOT)
        self.assertIn("codex_items", here)
        elsewhere = policy.sibling_modules(
            "scripts/agentware_lib/__init__.py", REPO_ROOT)
        self.assertNotIn("codex_items", elsewhere)
        # A hyphenated script is spawned, never imported — it is not an
        # identifier, so it must never be offered as an importable sibling.
        self.assertNotIn(
            "codex-stream",
            policy.sibling_modules("scripts/hooks/codex_items.py", REPO_ROOT))
        # And the allowance must not blunt the check it lives inside: a genuine
        # third-party import in the same directory is still a violation.
        self.assertNotIn("requests", here)

    def test_cli_imports_no_network_modules(self):
        findings = policy.network_violations(_CORPUS, root=REPO_ROOT)
        self.assertEqual(
            findings, [],
            "network-capable imports in no-network path classes (INV-1: "
            "ranking must never touch the network):\n"
            + policy.format_findings(findings))

    def test_sidecars_excluded_from_guarded_corpus(self):
        # The scripts/ SIDECARS are loaded by path/dotted name and legitimately
        # import a non-stdlib backend or http.server, so none of them may sit in
        # the FULLY guarded corpus the CLI's own contract covers — but they must
        # still be CLAIMED, with the exemption written down and attributed per
        # rule. If one rode into the guarded corpus INV-6 would false-positive;
        # if the CLI body fell out, INV-6 would go vacuous. Assert both
        # directions. The dashboard is a PACKAGE since task 69, so its
        # representative here is a MODULE of it — the one that imports
        # `http.server`, which is the whole reason the exemption exists.
        sidecars = ["scripts/agentware_dashboard/httpserver.py",
                    "scripts/agentware_dashboard/__main__.py",
                    "scripts/agentware_embedder_fastembed.py",
                    "scripts/agentware_embedder_ollama.py"]
        guarded = set(policy.fully_guarded_corpus(_CORPUS))
        for rel in sidecars:
            self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, rel)),
                            "missing sidecar: %s" % rel)
            self.assertIsNotNone(
                policy.classify(rel),
                "sidecar %s is unclaimed — its exemption must be a POLICY, not "
                "an omission" % rel)
        leaked = sorted(p for p in sidecars if p in guarded)
        self.assertEqual(
            leaked, [],
            "scripts/ sidecars leaked into the stdlib-only guarded corpus: %s "
            "(they load by path and may import non-stdlib backends)" % leaked)
        self.assertIn("scripts/agentware", guarded,
                      "the entrypoint fell out of the guarded corpus")

    def test_the_whole_cli_body_is_guarded_not_just_the_facade(self):
        # The anti-vacuity pin, stated as the thing that actually went wrong:
        # the facade is 110 lines and defines no command. Guarding it alone is
        # not guarding the CLI. Require the legacy body AND a substantial slice
        # of the package, so a future narrowing cannot pass as a rename.
        guarded = set(policy.guarded_corpus(_CORPUS))
        # The monolith is guarded only while it exists; the shim retirement
        # deletes it. Asserted as a BICONDITIONAL rather than pinning the side of
        # the retirement we happen to be on — `assertIn` would redden on a tree
        # where the deletion is exactly right, and dropping the line would stop
        # noticing a monolith that came back ungoverned.
        legacy = policy.LEGACY_REL
        if os.path.isfile(os.path.join(REPO_ROOT, legacy)):
            self.assertIn(legacy, guarded)
        else:
            self.assertNotIn(legacy, guarded)
        # The load-bearing half, and the one that holds in BOTH states: the CLI
        # body is the package, and guarding the 110-line facade is not guarding
        # it. Independent of the monolith entirely.
        self.assertIn("scripts/agentware", guarded)
        lib = sorted(p for p in guarded
                     if p.startswith("scripts/agentware_lib/"))
        self.assertGreater(
            len(lib), 100,
            "only %d agentware_lib modules are guarded — the corpus narrowed"
            % len(lib))


class SecurityPolicyCoverageTest(unittest.TestCase):
    """The coverage assertion: no python file in the tree is UNGOVERNED.

    A path-keyed scan cannot report the file it never looked at, so its failure
    mode is silence. Coverage inverts that: every file on disk must be claimed
    by some class, and an unclaimed one is a failure that names it.
    """

    def test_every_python_file_is_claimed_by_a_policy(self):
        missing = policy.unclaimed(_CORPUS)
        self.assertEqual(
            missing, [],
            "python files claimed by NO security policy — add a path class in "
            "tests/_security_policy.py (or widen an existing one) rather than "
            "leaving them ungoverned:\n  " + "\n  ".join(missing))

    def test_walk_sees_every_git_tracked_python_file(self):
        # SKIP_DIRS is the one way the walk can narrow. Cross-check it against
        # an independent enumeration so a growing skip list cannot quietly
        # shrink the corpus the way a non-recursive glob does.
        tracked = policy.tracked_python_files()
        if tracked is None:
            self.skipTest("git unavailable — cross-check not possible here")
        missed = sorted(set(tracked) - set(_CORPUS))
        self.assertEqual(missed, [],
                         "git-tracked python files the walk never saw: %s"
                         % missed)

    def test_no_policy_class_is_dead(self):
        # A rule guarding an empty set reads exactly like a rule that passes.
        dead = policy.dead_policies(_CORPUS)
        self.assertEqual(
            dead, [],
            "policy classes claiming zero files (renamed or deleted paths?): %s"
            % dead)

    def test_the_only_retired_class_is_the_monoliths_and_only_once_it_is_gone(self):
        # The retirement, stated as a biconditional so it keeps its meaning on
        # both sides of the deletion. While the monolith exists NOTHING is
        # retired — a terminal declaration must not buy an early exemption —
        # and once it is gone `cli-legacy` is the only class the removal may
        # empty. A second name showing up here is a rule that stopped guarding
        # for a reason nobody declared.
        retired = policy.retired_policies(_CORPUS)
        if os.path.isfile(os.path.join(REPO_ROOT, policy.LEGACY_REL)):
            self.assertEqual(retired, [],
                             "a class retired while its subject still exists")
        else:
            self.assertEqual(retired, ["cli-legacy"])

    def test_a_retired_class_is_INERT_not_merely_unused(self):
        # "Harmless because its file is gone" is a claim about what would change
        # if the row were deleted — so delete it and compare, rather than argue
        # it. All three corpora must be byte-identical without the row, which is
        # what makes the deletion at the end of the retirement a no-op the gate
        # has already proved rather than one it will discover.
        live = (policy.unclaimed(_CORPUS), policy.guarded_corpus(_CORPUS),
                policy.network_free_corpus(_CORPUS))
        for name in policy.retired_policies(_CORPUS):
            self.assertEqual(policy.corpora_without(name, _CORPUS), live,
                             "retired class %s still moves a corpus — it is "
                             "not inert and cannot simply be deleted" % name)

    def test_an_empty_class_that_would_EXEMPT_is_reported_however_it_emptied(self):
        # The safety property that lets a retired row be tolerated at all: an
        # empty row is harmless only when every rule it carries is the strictest
        # available, so a file reappearing at its path is scanned no more weakly
        # than it is today. An empty row that LOOSENS is the opposite — a hole
        # waiting for its path to come back — and no other check here reports it,
        # because `dead_policies` deliberately excuses a declared retirement.
        self.assertEqual(policy.masking_policies(_CORPUS), [])
        # Measured, not asserted: a permissive row that claims nothing IS caught.
        hole = policy.Policy(
            "probe-hole", ("scripts/_never_exists_probe.py",), False, False,
            "synthetic", terminal_with="scripts/_never_exists_probe.py")
        original = policy.POLICIES
        policy.POLICIES = original + (hole,)
        try:
            self.assertIn("probe-hole", policy.masking_policies(_CORPUS))
            # ...and it is NOT laundered by the terminal declaration.
            self.assertIn("probe-hole", policy.retired_policies(_CORPUS))
            self.assertNotIn("probe-hole", policy.dead_policies(_CORPUS))
        finally:
            policy.POLICIES = original

    def test_a_terminal_class_shadowed_while_its_subject_LIVES_is_still_dead(self):
        # The declaration buys an exception for exactly one state. A terminal row
        # whose subject is still on disk but which claims nothing anyway (an
        # earlier row shadows it, its pattern rotted) must fall straight through
        # to the dead-class report, or `terminal_with` becomes a way to silence
        # a genuinely broken rule years before its subject ever goes away.
        subject = "tests/__init__.py"                 # claimed by `test-support`
        self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, subject)))
        shadowed = policy.Policy("probe-shadowed", (subject,), True, True,
                                 "synthetic", terminal_with=subject)
        original = policy.POLICIES
        policy.POLICIES = original + (shadowed,)      # LAST — always shadowed
        try:
            self.assertEqual(policy.classify(subject).name, "test-support")
            self.assertIn("probe-shadowed", policy.dead_policies(_CORPUS))
            self.assertNotIn("probe-shadowed", policy.retired_policies(_CORPUS))
        finally:
            policy.POLICIES = original

    def test_every_file_is_claimed_by_exactly_one_EFFECTIVE_class(self):
        # `classify` is first-match-wins, so an overlap resolves silently and the
        # row ORDER is the entire answer. Every overlap in the table is a
        # deliberate specific-before-general pair; pinning the set means a NEW
        # ambiguity has to be argued for instead of being absorbed by whichever
        # row happens to sort first — which is how a fully-exempt class would
        # come to claim a test file without one pattern changing.
        declared = {
            ("test-fixture-corpus", "tests"),
            ("test-loopback-client", "tests"),
            ("test-optional-backend", "tests"),
        }
        seen = {}
        for path, names in policy.overlaps(_CORPUS):
            seen.setdefault(tuple(names), []).append(path)
        self.assertEqual(set(seen), declared,
                         "undeclared policy-class ambiguity: %s" % sorted(
                             set(seen) - declared))
        for names, paths in seen.items():
            # The specialisation must WIN, or the general row silently governs.
            for path in paths:
                self.assertEqual(policy.classify(path).name, names[0], path)

    def test_a_new_unclaimed_file_under_scripts_reddens_coverage(self):
        # The gate's discriminating power, measured rather than asserted: drop a
        # real python file at a scripts/ path no class claims and confirm the
        # real walk reports it.
        rel = "scripts/_policy_coverage_probe.py"
        probe = os.path.join(REPO_ROOT, rel)
        self.assertFalse(os.path.exists(probe), "stale probe file: %s" % probe)
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("import os\n")
        self.addCleanup(lambda: os.path.exists(probe) and os.remove(probe))
        found = policy.discover_python_files()
        self.assertIn(rel, found, "the walk did not see the new file")
        self.assertIn(rel, policy.unclaimed(found))
        os.remove(probe)
        self.assertEqual(policy.unclaimed(policy.discover_python_files()), [])

    def test_a_dependency_in_a_guarded_class_reddens_the_scan(self):
        # Hermetic negative test: a synthetic tree rooted elsewhere, so the
        # violation is real code at a guarded PATH without touching this repo.
        import tempfile
        root = tempfile.mkdtemp(prefix="agentware-policy-probe-")
        import shutil
        self.addCleanup(shutil.rmtree, root, True)
        rel = "scripts/agentware_lib/core/probe.py"
        target = os.path.join(root, rel)
        os.makedirs(os.path.dirname(target))
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("import os\nimport requests\nimport urllib.request\n")
        self.assertEqual(policy.classify(rel).name, "cli-lib")
        deps = policy.dependency_violations([rel], root=root,
                                            stdlib={"os", "urllib"},
                                            first_party=set())
        self.assertEqual(deps, [(rel, "cli-lib", ["requests"])])
        net = policy.network_violations([rel], root=root)
        self.assertEqual(net, [(rel, "cli-lib", ["requests", "urllib"])])

    def test_an_exempt_class_does_not_redden_on_its_declared_exemption(self):
        # The other direction: the exemptions are narrow and attributed, so the
        # sidecar classes must NOT be dragged into the guarded corpora at all.
        guarded = set(policy.guarded_corpus(_CORPUS))
        netfree = set(policy.network_free_corpus(_CORPUS))
        self.assertNotIn("scripts/agentware_embedder_fastembed.py", guarded)
        self.assertNotIn("scripts/agentware_dashboard/httpserver.py", netfree)
        self.assertIn("scripts/agentware_dashboard/httpserver.py", guarded,
                      "the sidecar is exempt from NO-NETWORK only; it still "
                      "may not add a third-party dependency")


class DeterministicRankingTest(SyntheticKBTestCase):
    """INV-1: identical inputs -> byte-identical recall/eval output across runs."""

    def _gold_path(self):
        """Write a small synthetic gold set and return its path."""
        bench = os.path.join(self.kdir, "benchmarks")
        os.makedirs(bench, exist_ok=True)
        gold = [
            {"query": "geofence arrive reminders",
             "expected_ids": ["learn-geofence-reminders"]},
            {"query": "gtimeout coreutils command",
             "expected_ids": ["learn-macos-no-timeout"]},
            {"query": "saturation inverse document frequency",
             "expected_ids": ["ref-bm25-ranking"]},
            {"query": "python stdlib runtime dependency",
             "expected_ids": ["config-python-runtime"]},
        ]
        path = os.path.join(bench, "recall-gold.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(gold, f)
        return path

    def test_recall_json_is_byte_identical_across_runs(self):
        a = self.run_cli(["recall", "geofence arrive reminders", "--format", "json"])
        b = self.run_cli(["recall", "geofence arrive reminders", "--format", "json"])
        self.assertEqual(a[0], 0, a[2])
        self.assertEqual(a[1], b[1])  # byte-identical stdout

    def test_eval_ranking_metrics_are_deterministic_across_runs(self):
        # INV-1 forbids wall-clock in SCORING, not in REPORTED latency: eval's
        # latency_ms_* fields are timed and legitimately vary run-to-run. Every
        # ranking-derived metric (recall/precision/ndcg/mrr/ctx-tokens) must be
        # byte-identical, so strip only the two latency fields before comparing.
        gp = self._gold_path()
        a = self.run_cli(["eval", "--gold", gp, "--strategy", "bm25",
                          "--format", "json"])
        b = self.run_cli(["eval", "--gold", gp, "--strategy", "bm25",
                          "--format", "json"])
        self.assertEqual(a[0], 0, a[2])

        def _strip_latency(obj):
            # Drop every timed field (latency is the ONLY non-deterministic part
            # of an eval result) anywhere in the payload, including per_query rows.
            if isinstance(obj, dict):
                return {k: _strip_latency(v) for k, v in obj.items()
                        if "latency" not in k}
            if isinstance(obj, list):
                return [_strip_latency(v) for v in obj]
            return obj

        self.assertEqual(_strip_latency(json.loads(a[1])),
                         _strip_latency(json.loads(b[1])))

    def test_recall_makes_no_subprocess_call(self):
        # No model service / shell-out is reachable from the ranking path: patch
        # subprocess.run to detonate and confirm recall still succeeds.
        #
        # Patched on the STDLIB module rather than through `CLI.subprocess`. That
        # read used to resolve because the monolith was exec'd into the CLI
        # module's globals, so `subprocess` was a name in its dict — and setting
        # `.run` on it mutated the one shared module object anyway. `load_cli()`
        # deliberately never forwards module objects (a submodule named like a
        # symbol is not the symbol), so with the monolith gone the read has
        # nowhere to land. Same object, same process-wide detonation, named
        # directly: the patch now reaches every caller in every module, which is
        # what "the ranking path shells out NOWHERE" actually requires.
        orig = subprocess.run

        def _boom(*a, **k):
            raise AssertionError("recall must not shell out (INV-1)")

        subprocess.run = _boom
        try:
            code, out, err = self.run_cli(
                ["recall", "geofence arrive reminders", "--format", "json"])
        finally:
            subprocess.run = orig
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["results"])


class ReadOnlyRetrievalTest(SyntheticKBTestCase):
    """INV-2/INV-3: recall / eval / metrics never mutate index.json or KB files."""

    def _index_bytes(self):
        with open(os.path.join(self.kdir, "index.json"), "rb") as f:
            return f.read()

    def _snapshot_tree(self):
        """Map of every KB file path -> bytes, for an exact before/after diff."""
        snap = {}
        for root, _dirs, files in os.walk(self.kdir):
            for name in files:
                p = os.path.join(root, name)
                with open(p, "rb") as f:
                    snap[p] = f.read()
        return snap

    def _gold_path(self):
        bench = os.path.join(self.kdir, "benchmarks")
        os.makedirs(bench, exist_ok=True)
        gold = [{"query": "geofence arrive reminders",
                 "expected_ids": ["learn-geofence-reminders"]},
                {"query": "python stdlib runtime",
                 "expected_ids": ["config-python-runtime"]}]
        path = os.path.join(bench, "recall-gold.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(gold, f)
        return path

    def test_recall_leaves_index_unchanged(self):
        before = self._index_bytes()
        code, _out, err = self.run_cli(
            ["recall", "python ranking stdlib", "--format", "json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(before, self._index_bytes())

    def test_recall_leaves_entire_kb_tree_unchanged(self):
        before = self._snapshot_tree()
        self.run_cli(["recall", "geofence reminders", "--format", "json"])
        self.run_cli(["recall", "macos timeout", "--format", "text"])
        self.assertEqual(before, self._snapshot_tree())

    def test_eval_leaves_index_unchanged(self):
        gp = self._gold_path()
        before = self._index_bytes()
        # Plain eval (no --record/--gate) must be read-only.
        code, _out, err = self.run_cli(
            ["eval", "--gold", gp, "--strategy", "bm25", "--format", "json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(before, self._index_bytes())

    def test_eval_ablate_leaves_index_unchanged(self):
        gp = self._gold_path()
        before = self._index_bytes()
        code, _out, err = self.run_cli(
            ["eval", "--gold", gp, "--ablate", "--format", "json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(before, self._index_bytes())

    @unittest.skipUnless(hasattr(CLI, "cmd_metrics"),
                         "metrics command not implemented yet (Phase 4.1)")
    def test_metrics_leaves_index_unchanged(self):
        before = self._index_bytes()
        # metrics parses logs/sessions only; it must never touch index.json.
        code, _out, _err = self.run_cli(["metrics", "--format", "json"])
        # Exit code may be non-zero if no logs exist; the invariant is no mutation.
        self.assertEqual(before, self._index_bytes())
        self.assertIn(code, (0, 1))


class TokenBudgetInvariantTest(SyntheticKBTestCase):
    """INV-5: recall is token-budgeted and reduces injected context vs a full dump."""

    def _full_corpus_footprint(self):
        """Estimated tokens to inject the WHOLE corpus (the dump recall replaces)."""
        corpus = CLI.build_corpus(self.kdir, self.index_data)
        return sum(CLI.estimate_tokens(text) for _e, text in corpus)

    def test_recall_never_exceeds_token_budget(self):
        for budget in (0, 30, 80, 250, 1500):
            payload = json.loads(self.run_cli(
                ["recall", "python ranking stdlib retrieval geofence",
                 "--token-budget", str(budget), "--top-k", "10",
                 "--format", "json"])[1])
            self.assertLessEqual(
                payload["context_tokens"], budget,
                "budget %d exceeded: %d tokens" % (budget, payload["context_tokens"]))
            self.assertEqual(
                payload["context_tokens"],
                sum(r["estimated_tokens"] for r in payload["results"]))

    def test_recall_footprint_smaller_than_full_corpus_dump(self):
        # The whole-corpus dump is the baseline recall is meant to REPLACE
        # (analog of injecting all of MAIN.md every session). A focused recall
        # must inject strictly fewer tokens than dumping everything.
        full = self._full_corpus_footprint()
        payload = json.loads(self.run_cli(
            ["recall", "geofence reminders", "--top-k", "1",
             "--token-budget", "1500", "--format", "json"])[1])
        self.assertTrue(payload["results"])
        self.assertLess(payload["context_tokens"], full)

    def test_budget_zero_injects_no_context(self):
        payload = json.loads(self.run_cli(
            ["recall", "geofence reminders", "--token-budget", "0",
             "--format", "json"])[1])
        self.assertEqual(payload["context_tokens"], 0)
        self.assertEqual(payload["results"], [])


if __name__ == "__main__":
    unittest.main()
