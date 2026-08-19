"""HeadExtractionCompletenessTest, ABInstallSeamTest, ABAssemblerParityTest -- split from `test_eval_pinning` (task 58).

See `tests.unit.retrieval._eval_pinning_support` for the
shared header.
"""

from tests.unit.retrieval._eval_pinning_support import (  # noqa: F401
    AB, CLI, CLI_PATH, LEGACY_PATH, RG, _ABArgs, ast, os, shutil,
    tempfile, unittest)


class HeadExtractionCompletenessTest(unittest.TestCase):
    """`_verify_head_extraction` is pure over (tmpdir, listing) — no git, no
    subprocess — so the completeness contract is testable without a repo."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="agentware-verify-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

    def _touch(self, rel):
        dest = os.path.join(self.tmpdir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write("x")

    def test_complete_extraction_verifies(self):
        listing = ["scripts/agentware", "scripts/agentware_lib/cli.py"]
        for rel in listing:
            self._touch(rel)
        self.assertTrue(RG._verify_head_extraction(self.tmpdir, listing))

    def test_missing_path_raises_naming_it(self):
        listing = ["scripts/agentware", "scripts/agentware_lib/cli.py"]
        self._touch("scripts/agentware")
        with self.assertRaises(RG.HeadExtractionError) as caught:
            RG._verify_head_extraction(self.tmpdir, listing)
        self.assertEqual(caught.exception.missing,
                         ["scripts/agentware_lib/cli.py"])
        self.assertIn("scripts/agentware_lib/cli.py", str(caught.exception))

    def test_empty_listing_is_vacuously_complete(self):
        # An ABSENT tree is not a BROKEN one: _extract_head_cli returns
        # (None, None) for that case and the gate keeps passing on it.
        self.assertTrue(RG._verify_head_extraction(self.tmpdir, []))

class ABInstallSeamTest(unittest.TestCase):
    """The facade's post-exec wiring must be a WRITE seam, not a photograph.

    Task 25's recorded lesson: an indirection that satisfies `hasattr` but does
    not carry a monkeypatch through is exactly how a gate reports full coverage
    while measuring nothing."""

    def test_facade_ab_family_is_the_lib_one(self):
        self.assertIs(CLI._verify_head_extraction, RG._verify_head_extraction)
        self.assertIs(CLI.HeadExtractionError, RG.HeadExtractionError)
        self.assertIs(CLI._cleanup_head_cli, RG._cleanup_head_cli)

    def test_installed_run_ab_gate_honours_a_monkeypatch(self):
        orig_extract, orig_head = CLI._extract_head_cli, CLI._ab_head_recall
        self.addCleanup(setattr, CLI, "_extract_head_cli", orig_extract)
        self.addCleanup(setattr, CLI, "_ab_head_recall", orig_head)
        CLI._extract_head_cli = lambda: ("/nonexistent/head.py", "deadbee")
        CLI._ab_head_recall = lambda *a, **k: 0.42
        result = {"metrics": {"recall_at_k": 0.42}, "strategy": "bm25",
                  "top_k": 5, "as_of": "2026-06-26"}
        ab = CLI.run_ab_gate(_ABArgs(), "/tmp/kdir", result, "/tmp/gold.json")
        self.assertEqual(ab["head_commit"], "deadbee")
        self.assertTrue(ab["available"], ab)
        self.assertAlmostEqual(ab["delta"], 0.0)

    def test_dispatch_tail_lives_in_the_facade_not_the_exec(self):
        """The guard must sit AFTER the wiring, or the wiring is tests-only.

        Under the real CLI `__name__ == "__main__"`, so a guard left at the end
        of the exec'd legacy body would `sys.exit(main())` before any post-exec
        statement ran — while `load_cli()`'s non-`__main__` load would run them
        all. Green tests, unwired production.

        TERMINAL STATE (task 96). This whole hazard is a property of EXEC: it
        exists only because the facade runs a legacy body in its own globals and
        then wires over it. With the passthrough gone there is no exec, no
        post-exec statement to be skipped, and no legacy tail to split off — so
        the ordering has nothing to order. Assert the ABSENCE rather than
        deleting the coverage: no exec seam, no installer call, no legacy file.
        The A/B family still resolving to the lib is `test_facade_ab_family_is_
        the_lib_one`, and the behaviour the installer used to add now lives in
        `run_ab_gate` itself (`ABAssemblerParityTest` below).
        """
        with open(CLI_PATH, encoding="utf-8") as f:
            facade = f.read()
        if not os.path.isfile(LEGACY_PATH):
            # Assert over the AST, not the source text. The facade's prose and
            # its sys.path comment still narrate the passthrough it used to
            # carry, and rewriting that narration is task 96's job — a gate that
            # reddens on a comment is a gate that gets edited to shut it up.
            # What must be terminal is the BEHAVIOUR: no exec seam, no installer
            # call, no code path naming the deleted module.
            tree = ast.parse(facade)
            names, strings, calls = set(), [], set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    strings.append(node.value)
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    names.update(a.name for a in node.names)
                    names.add(getattr(node, "module", None) or "")
            self.assertNotIn("exec", calls, "the facade still exec's a body")
            self.assertNotIn("compile", calls)
            self.assertNotIn("install_ab_extraction", names)
            self.assertFalse([n for n in names if "agentware_legacy" in n],
                             "the facade still binds the deleted monolith")
            # The docstring is strings[0]; every OTHER literal is live code.
            self.assertFalse(
                [s for s in strings[1:] if "agentware_legacy" in s],
                "the facade still names the deleted monolith in code")
            return
        install_at = facade.index("install_ab_extraction(globals())")
        guard_at = facade.rindex('if __name__ == "__main__":')
        self.assertLess(install_at, guard_at)
        with open(LEGACY_PATH, encoding="utf-8") as f:
            legacy = f.read()
        # The facade must still be splitting off a tail that actually exists.
        self.assertIn('\nif __name__ == "__main__":\n    sys.exit(main())\n',
                      legacy)

class ABAssemblerParityTest(unittest.TestCase):
    """The task-26 repair must live on the LIVE path, not only the facade's.

    `retrieval.evalcmd` reaches `run_ab_gate` by attribute on the module — no
    namespace, no installer. For two commits that call site resolved to an
    assembler that did NOT catch `HeadExtractionError`, because the repair had
    been written into `_run_ab_gate_ns` alone; `eval --ab` therefore raised out
    of `cmd_eval` instead of returning the RED verdict, and `_print_ab`'s
    broken-arm branch was unreachable from production. The A/B tests could not
    see it — they read `CLI.run_ab_gate`, which the facade's post-exec install
    had rebound to the namespace copy.

    So these address `AB.run_ab_gate` — the OWNING module's own function, the
    one `evalcmd` calls — and are independent of whether the monolith exists.
    """

    def _result(self):
        return {"metrics": {"recall_at_k": 0.8}, "strategy": "bm25",
                "top_k": 5, "as_of": "2026-06-26"}

    def _with_extract(self, fn):
        real = AB._extract_head_cli
        AB._extract_head_cli = fn
        self.addCleanup(setattr, AB, "_extract_head_cli", real)

    def test_live_path_reddens_on_an_incomplete_extraction(self):
        def _boom():
            raise AB.HeadExtractionError(["scripts/agentware_lib/cli.py"])

        self._with_extract(_boom)
        result = self._result()
        ab = AB.run_ab_gate(_ABArgs(), "/tmp/kdir", result, "/tmp/gold.json")
        self.assertFalse(ab["passed"], ab)
        self.assertFalse(ab["available"], ab)
        self.assertIn("scripts/agentware_lib/cli.py", ab["error"])
        self.assertEqual(ab["missing"], ["scripts/agentware_lib/cli.py"])
        self.assertIs(result["ab"], ab)
        # A BROKEN arm must move the exit code; an ABSENT one must not.
        self.assertEqual(CLI._gate_exit(None, ab), 1)

    def test_live_path_still_PASSES_on_an_absent_arm(self):
        """The discriminator: unavailable-because-absent stays green."""
        self._with_extract(lambda: (None, None))
        ab = AB.run_ab_gate(_ABArgs(), "/tmp/kdir", self._result(),
                            "/tmp/gold.json")
        self.assertFalse(ab["available"], ab)
        self.assertTrue(ab["passed"], ab)
        self.assertNotIn("missing", ab)
        self.assertEqual(CLI._gate_exit(None, ab), 0)

    def test_both_assemblers_agree_verdict_for_verdict(self):
        """One body, reached two ways — so they cannot drift apart again.

        Runs the SAME three states through the bare-name path and the
        namespace-resolved one and requires byte-equal verdicts. This is the
        test that could not fail while there were two bodies: the facade copy
        was the only one the suite ever called.
        """
        states = {
            "broken": lambda: (_ for _ in ()).throw(
                AB.HeadExtractionError(["scripts/agentware_lib/cli.py"])),
            "absent": lambda: (None, None),
            "scored": lambda: ("/nonexistent/head.py", "deadbee"),
        }
        for label, extract in states.items():
            with self.subTest(state=label):
                ns = {"_extract_head_cli": extract,
                      "_ab_head_recall": lambda *a, **k: 0.8,
                      "_ab_verdict": AB._ab_verdict,
                      "_today": lambda: "2026-06-26"}
                self._with_extract(extract)
                real_recall = AB._ab_head_recall
                AB._ab_head_recall = lambda *a, **k: 0.8
                self.addCleanup(setattr, AB, "_ab_head_recall", real_recall)
                bare = AB.run_ab_gate(_ABArgs(), "/tmp/kdir", self._result(),
                                      "/tmp/gold.json")
                via_ns = AB._run_ab_gate_ns(ns, _ABArgs(), "/tmp/kdir",
                                            self._result(), "/tmp/gold.json")
                self.assertEqual(bare, via_ns)
