"""The CONSTANT DIFFER suite (plan task 31) — proof that the gate bites.

Four claims, deliberately separated:

  1. GREEN — the gate passes over the live tree and the baseline regenerates
     byte-identically.
  2. GENERATOR RATCHET — `--write` cannot disarm the gate. The first SUCCESSFUL
     port is where it tries: a monolith-only constant that lands correctly stops
     being monolith-only, so a naive regeneration would drop its row and free
     the constant it was protecting, with a green run every step of the way.
  3. DOORS — the guarded value is broken through every distinct path an author
     could reach it by. The lesson in
     `learn-a-derived-constant-is-invisible-to-a-static-allowlist-gate` is that
     the mutation which finds the hole is the NATURAL edit, not the obvious one.
  4. EXHAUSTIVE REFUTATION — all 265 rows are then broken IN TURN and each must
     redden individually. Task 30 shipped a per-row gate in which 2 of 242 rows
     were inert, and only the exhaustive pass found them; a gate is proven only
     for the rows you actually broke.

What the fixture's normalisation is, and that the artifact is well formed, is
pinned separately in `test_constant_baseline.py`.

Stdlib only, deterministic. The static half is hermetic (`ast` over the tree,
no imports of the code under test); the runtime probe deliberately is not — it
runs a subprocess, because a derived constant cannot be discharged any other way.
"""

from tests.gates._constant_differ_support import (  # noqa: F401
    REPO, TERMINAL, _StubAdjudications, _kinds, _named, _tree, adj, cd, cp, json, os, re,
    shutil, subprocess, sys, tempfile, unittest)


class GateGreenTest(unittest.TestCase):
    """The gate passes over the tree it ships with, and regenerates cleanly."""

    def test_the_live_tree_is_green(self):
        report = cd.check(cd.load_fixture(), cd.legacy_tree(), cd.lib_tree(),
                          TERMINAL)
        self.assertEqual(cd.violations(report), [])

    def test_the_baseline_is_byte_identical_on_a_second_write(self):
        fixture = cd.build_fixture(REPO, adj)
        with open(cd.FIXTURE, "r", encoding="utf-8") as fh:
            committed = json.load(fh)
        self.assertEqual(fixture, committed)

    def test_the_ratchet_records_todays_debt_rather_than_zero(self):
        # The invariant is that the ratchet equals TODAY'S debt -- recording a
        # number the tree does not have is a lie in either direction. It used to
        # also assert the count was positive, which held while constants were
        # still owed to the port wave; task 56 ported the last four, so the honest
        # number is now 0 and demanding a positive one would force the fixture to
        # overstate. The shrink-only direction is enforced by the ratchet itself.
        fx = cd.load_fixture()
        report = cd.check(fx, cd.legacy_tree(), cd.lib_tree(), TERMINAL)
        self.assertEqual(len(report["unresolved"]), fx["ratchet"]["unresolved_max"])
        self.assertGreaterEqual(fx["ratchet"]["unresolved_max"], 0)

class GeneratorRatchetTest(unittest.TestCase):
    """`--write` may not disarm the gate, and the first port is where it tries.

    A monolith-only constant that lands CORRECTLY stops being monolith-only. A
    naive regeneration therefore drops its row, and the constant it was
    protecting becomes free to change in the very next commit -- the gate
    quietly retiring itself one successful port at a time, with a green run
    every step of the way. The row set is a ratchet for exactly this reason.
    """

    def setUp(self):
        self.legacy = _tree(A_CAP="7", D_LIB="2")
        self.lib = _tree(D_LIB="3")
        self.prev = {
            "legacy_only": [{"name": "A_CAP", "literal": "7",
                             "legacy_sites": ["scripts/agentware_legacy.py"],
                             "status": "pending", "follow_up": "task 44"}],
            "divergent": [{"name": "D_LIB", "legacy_literal": "2",
                           "lib_literal": "3", "decision": "lib",
                           "decided_literal": "3", "rationale": "x",
                           "legacy_sites": [], "lib_sites": []}],
        }

    def _build(self, legacy, lib):
        names = {"legacy_only": sorted(set(legacy) - set(lib)),
                 "divergent": [n for n in sorted(set(legacy) & set(lib))
                               if cd.representative(legacy[n]) != cd.representative(lib[n])]}
        return {key: cd._tracked(names[key], self.prev, key) for key in names}

    def test_a_ported_constant_keeps_its_row_instead_of_dropping_out(self):
        self.lib["A_CAP"] = [("l.py", "7")]          # the port lands correctly
        self.assertIn("A_CAP", self._build(self.legacy, self.lib)["legacy_only"])

    def test_a_row_survives_the_monolith_being_deleted(self):
        # Task 56 deletes agentware_legacy.py. Every row must still be present
        # and still gating, or the whole baseline evaporates in one commit.
        tracked = self._build({}, self.lib)
        self.assertIn("A_CAP", tracked["legacy_only"])
        self.assertIn("D_LIB", tracked["divergent"])

    def test_the_monolith_literal_is_pinned_and_never_re_derived(self):
        prev = self.prev["legacy_only"][0]
        drifted = _tree(A_CAP="9")
        row = cd._legacy_only_row("A_CAP", drifted, {}, _StubAdjudications, prev)
        self.assertEqual(row["literal"], "7", "an edit was absorbed by --write")

    def test_a_first_capture_derives_the_literal_from_the_tree(self):
        row = cd._legacy_only_row("A_CAP", self.legacy, {}, _StubAdjudications, None)
        self.assertEqual(row["literal"], "7")
        self.assertEqual(row["status"], "pending")

    def test_a_landed_row_is_marked_resolved(self):
        self.lib["A_CAP"] = [("scripts/agentware_lib/x.py", "7")]
        row = cd._legacy_only_row("A_CAP", self.legacy, self.lib, _StubAdjudications,
                                  self.prev["legacy_only"][0])
        self.assertEqual(row["status"], "resolved")
        self.assertEqual(row["lib_sites"], ["scripts/agentware_lib/x.py"])

    def test_a_lib_decision_is_pinned_so_a_later_edit_cannot_become_the_decision(self):
        self.lib["D_LIB"] = [("l.py", "4")]
        row = cd._divergent_row("D_LIB", self.legacy, self.lib, _StubAdjudications,
                                self.prev["divergent"][0])
        self.assertEqual(row["decided_literal"], "3")
        self.assertEqual(row["lib_literal"], "4")   # recorded, but not blessed

class DoorTest(unittest.TestCase):
    """Break the guarded value through EVERY path an author could reach it by.

    Not one representative mutation: the hole that motivated this discipline was
    found by editing the table one level to the LEFT of the checked expression,
    which no "obvious" mutation would have touched.
    """

    def setUp(self):
        self.fx = {
            "ratchet": {"unresolved_max": 1, "undecided_max": 1},
            "multi_literal": {},
            "legacy_only": [
                {"name": "A_CAP", "literal": "7", "legacy_sites": ["m.py"],
                 "status": "pending", "follow_up": "task 44"},
                {"name": "A_GONE", "literal": "1", "legacy_sites": ["m.py"],
                 "status": "excluded", "reason": "deleted by a decision",
                 "follow_up": "task 15"},
            ],
            "divergent": [
                {"name": "D_LIB", "legacy_literal": "2", "lib_literal": "3",
                 "decision": "lib", "decided_literal": "3", "rationale": "x",
                 "legacy_sites": ["m.py"], "lib_sites": ["l.py"]},
                {"name": "D_LEG", "legacy_literal": "0.85", "lib_literal": "None",
                 "decision": "legacy", "decided_literal": "0.85", "landed": False,
                 "pending_literal": "None", "rationale": "x", "follow_up": "task 49",
                 "legacy_sites": ["m.py"], "lib_sites": ["l.py"]},
                {"name": "D_EQ", "legacy_literal": "f()", "lib_literal": "g()",
                 "decision": "equivalent", "rationale": "x",
                 "legacy_sites": ["m.py"], "lib_sites": ["l.py"]},
            ],
        }
        self.legacy = _tree(A_CAP="7", D_LIB="2", D_LEG="0.85", D_EQ="f()")
        self.lib = _tree(D_LIB="3", D_LEG="None", D_EQ="g()")

    def _check(self):
        return cd.check(self.fx, self.legacy, self.lib)

    def test_baseline_is_green(self):
        self.assertEqual(cd.violations(self._check()), [])

    # --- door 1: the pending constant is simply deleted from the monolith ----
    def test_a_pending_constant_deleted_from_the_monolith_is_lost(self):
        del self.legacy["A_CAP"]
        report = self._check()
        self.assertIn("lost", _kinds(report))
        self.assertIn("A_CAP", _named(report, "lost"))

    # --- door 2: its value is edited while it waits to be ported -------------
    def test_a_pending_constant_edited_in_place_reddens(self):
        self.legacy["A_CAP"] = [("m.py", "9")]
        self.assertIn("A_CAP", _named(self._check(), "legacy_drift"))

    # --- door 3: it lands in the lib carrying a DIFFERENT value --------------
    def test_a_constant_ported_with_the_wrong_literal_reddens(self):
        self.lib["A_CAP"] = [("l.py", "9")]
        self.assertIn("A_CAP", _named(self._check(), "literal_mismatch"))

    # --- door 4: it lands correctly -- green, and the debt shrinks -----------
    def test_a_constant_ported_correctly_is_green_and_reduces_the_debt(self):
        self.lib["A_CAP"] = [("l.py", "7")]
        report = self._check()
        self.assertEqual(cd.violations(report), [])
        self.assertEqual(report["unresolved"], [])
        self.assertIn("A_CAP", report["landed"])

    # --- door 5: an excluded constant quietly comes back ---------------------
    def test_an_excluded_constant_reappearing_fails_its_absence_probe(self):
        self.lib["A_GONE"] = [("l.py", "1")]
        self.assertIn("A_GONE", _named(self._check(), "excluded_present"))

    # --- door 6: a lib-decided value is edited -------------------------------
    def test_editing_a_lib_decided_value_reddens(self):
        self.lib["D_LIB"] = [("l.py", "4")]
        self.assertIn("D_LIB", _named(self._check(), "decision_mismatch"))

    # --- door 7: a legacy-decided value drifts in the monolith ---------------
    def test_editing_the_decided_value_in_the_monolith_reddens(self):
        self.legacy["D_LEG"] = [("m.py", "0.5")]
        self.assertIn("D_LEG", _named(self._check(), "legacy_drift"))

    # --- door 8: the lib takes a THIRD value, neither decision nor pending ---
    def test_a_third_value_appearing_in_the_lib_reddens(self):
        self.lib["D_LEG"] = [("l.py", "0.5")]
        self.assertIn("D_LEG", _named(self._check(), "decision_mismatch"))

    # --- door 9: the port lands the decision -- green, undecided shrinks -----
    def test_landing_the_decided_value_is_green_and_clears_the_undecided_row(self):
        self.lib["D_LEG"] = [("l.py", "0.85")]
        report = self._check()
        self.assertEqual(cd.violations(report), [])
        self.assertEqual(report["undecided"], [])

    # --- door 10: an equivalence is re-spelt on either side ------------------
    def test_re_spelling_an_equivalence_reddens_even_without_the_probe(self):
        self.lib["D_EQ"] = [("l.py", "h()")]
        self.assertIn("D_EQ", _named(self._check(), "decision_mismatch"))
        self.setUp()
        self.legacy["D_EQ"] = [("m.py", "h()")]
        self.assertIn("D_EQ", _named(self._check(), "legacy_drift"))

    # --- door 11: a divergent constant disappears from the lib --------------
    def test_a_divergent_constant_vanishing_from_the_lib_is_lost(self):
        del self.lib["D_LIB"]
        self.assertIn("D_LIB", _named(self._check(), "lost"))

    # --- door 12: a constant gains a SECOND definition ----------------------
    def test_a_constant_gaining_a_second_definition_reddens_the_multi_pin(self):
        # The natural way to defeat a longest-literal representative: leave the
        # checked assignment alone and add a second, shorter one elsewhere.
        self.lib["D_LIB"] = [("l.py", "3"), ("other.py", "4")]
        self.assertIn("D_LIB", _named(self._check(), "multi_literal"))

    # --- door 13: the debt grows -------------------------------------------
    def test_the_ratchet_reddens_when_the_debt_grows(self):
        self.fx["ratchet"]["unresolved_max"] = 0
        self.assertIn("ratchet", _kinds(self._check()))

    def test_the_ratchet_reddens_when_undecided_rows_grow(self):
        self.fx["ratchet"]["undecided_max"] = 0
        self.assertIn("ratchet", _kinds(self._check()))

class RuntimeProbeTest(unittest.TestCase):
    """The half a static read cannot do: compare the live objects."""

    @classmethod
    def setUpClass(cls):
        cls.fx = cd.load_fixture()
        cls.targets = cp.probe_targets(cls.fx)

    def test_a_lib_file_maps_to_the_module_the_probe_will_import(self):
        self.assertEqual(cp.module_path("scripts/agentware_lib/core/paths.py"),
                         "agentware_lib.core.paths")
        self.assertEqual(cp.module_path("scripts/agentware_lib/ops/fleet/__init__.py"),
                         "agentware_lib.ops.fleet")

    def test_every_equivalence_is_covered_by_the_probe(self):
        equivalences = {r["name"] for r in self.fx["divergent"]
                        if r["decision"] == "equivalent"}
        self.assertTrue(equivalences)
        self.assertTrue(equivalences <= {n for n, _ in self.targets})

    def test_the_live_objects_agree(self):
        results = cp.run_probe(self.targets)
        self.assertEqual(sorted(results), sorted(n for n, _ in self.targets))
        expected = "resolve" if TERMINAL else "compare"
        self.assertEqual({r.get("mode") for r in results.values()}, {expected})
        # In COMPARE mode the two live objects must be equal; in RESOLVE mode
        # there is no second object, and the surviving obligation is that the
        # lib side still imports and still carries the name. A row that answered
        # neither question is a violation in both — never a pass.
        key = "equal" if expected == "compare" else "resolved"
        unanswered = sorted(n for n, r in results.items() if not r.get(key))
        self.assertEqual(unanswered, [], "probe rows that did not answer")

    def test_the_terminal_probe_still_reddens_on_an_unresolvable_constant(self):
        """The degradation must keep its bite: resolve-only is not a free pass."""
        report = {k: [] for k in cd.REPORT_KINDS}
        cp.apply_probe(report, {"X": {"mode": "resolve", "resolved": False}}, cd._fail)
        self.assertIn("X", _named(report, "runtime"))

    def test_a_resolve_only_row_is_NAMED_rather_than_silently_passing(self):
        """A degraded run must not read like a full comparison."""
        report = {k: [] for k in cd.REPORT_KINDS}
        cp.apply_probe(report, {"X": {"mode": "resolve", "resolved": True,
                                      "digests": ["aa"]}}, cd._fail)
        self.assertEqual(_named(report, "runtime"), set())
        self.assertEqual(report["runtime_resolved"], ["X"])
        self.assertIn("resolve-only", cd._render(dict(
            report, checked=1, unresolved=[], landed=[], undecided=[],
            equivalent=[], terminal=True)))

    def test_the_probe_compares_digests_and_never_the_value_itself(self):
        # REPO_ROOT resolves to an absolute path under the operator's home. The
        # probe must never hand that back for a report or a fixture to carry.
        results = cp.run_probe([("REPO_ROOT", "agentware_lib.core.paths")])
        blob = json.dumps(results)
        self.assertNotIn(os.path.expanduser("~"), blob)
        self.assertIn("digests", results["REPO_ROOT"])

    def test_a_mismatch_is_reported_as_a_runtime_violation(self):
        report = {k: [] for k in cd.REPORT_KINDS}
        cp.apply_probe(report, {"X": {"equal": False, "digests": ["aa", "bb"]}}, cd._fail)
        self.assertIn("X", _named(report, "runtime"))

    def test_an_unimportable_module_is_reported_rather_than_swallowed(self):
        results = cp.run_probe([("REPO_ROOT", "agentware_lib.does.not.exist")])
        self.assertIn("error", results["REPO_ROOT"])
        report = {k: [] for k in cd.REPORT_KINDS}
        cp.apply_probe(report, results, cd._fail)
        self.assertIn("REPO_ROOT", _named(report, "runtime"))

    def test_a_constant_missing_from_its_module_is_reported(self):
        results = cp.run_probe([("NO_SUCH_CONSTANT", "agentware_lib.core.paths")])
        self.assertIn("error", results["NO_SUCH_CONSTANT"])

class CliTest(unittest.TestCase):
    """End to end, against a real mutated tree -- not just the pure function."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="aw-const-cli-")
        shutil.copytree(os.path.join(REPO, "scripts"),
                        os.path.join(cls.tmp, "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run(self, root, *extra):
        return subprocess.run(
            [sys.executable, os.path.join(REPO, "tests", "_constant_differ.py"),
             "--check", "--root", root, "--no-probe"] + list(extra),
            capture_output=True, text=True, timeout=300)

    def test_the_real_tree_exits_zero(self):
        proc = self._run(REPO)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_a_mutated_tree_exits_one_naming_the_constant(self):
        path = os.path.join(self.tmp, "scripts", "agentware_lib", "retrieval", "bm25.py")
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        mutated = re.sub(r"^BM25_CACHE_VERSION = 3$", "BM25_CACHE_VERSION = 4",
                         src, count=1, flags=re.M)
        self.assertNotEqual(src, mutated, "the mutation did not apply")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(mutated)
        try:
            proc = self._run(self.tmp)
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("BM25_CACHE_VERSION", proc.stdout)
            self.assertIn("decision_mismatch", proc.stdout)
        finally:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(src)

    def test_json_format_is_machine_readable(self):
        proc = self._run(REPO, "--format", "json")
        payload = json.loads(proc.stdout)
        self.assertIn("unresolved", payload)
        self.assertEqual(payload["checked"], 265)
