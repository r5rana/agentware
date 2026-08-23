"""Owning unit file, second half: `ops.premisecheck`'s CORPUS side (wave 14).

The sibling of `test_premisecheck.py`, which reached the 600-line `tests/**` cap.
This half owns the two corpus folds (`_corpus_metric_recurrence_keys`,
`_corpus_metric_error_records`), the metric dispatcher, the numeric comparator,
the cite-live and config-eq evaluators, and `evaluate_premise` — including the
fallback parse this wave restored, which raised NameError from the strangler
split until now.

The split is at a CLASS boundary and the fixture is shared through
`_premisecheck_support.py`, so neither half imports the other: a failure in the
importer would otherwise look like a failure in the imported.

Runner:  python3 -m unittest tests.unit.ops.test_premisecheck_corpus -v
"""

import json
import os
import shutil
import tempfile
import unittest

from tests.unit.ops._premisecheck_support import (
    PC, PP, _KBCase, _prompt, _write, _libconfig)


# --- the two corpus folds ----------------------------------------------------

class CorpusMetricFoldsTest(_KBCase):

    def _logs(self, kdir):
        return os.path.join(kdir, "logs")

    def _seed_prompts(self, kdir, text):
        return _write(os.path.join(kdir, "logs", "prompts.log"), text)

    def _seed_live(self, kdir, sid, records):
        return _write(
            os.path.join(kdir, "logs", "sessions", sid, "live.jsonl"),
            "".join(json.dumps(r) + "\n" for r in records))

    # --- fold 1: recurrence -------------------------------------------------

    def test_a_key_is_counted_only_at_n_distinct_sessions(self):
        # MINER_RECURRENCE_N is 2. One key in ONE session must not count; the
        # same key in two must. Both directions, or the test passes with the
        # comparison inverted.
        body = "always keep the derived org facet on the index row"
        self._seed_prompts(self.kdir, _prompt("s-a", 1, body))
        self.assertEqual(
            PC._corpus_metric_recurrence_keys(self._logs(self.kdir)), 0)
        self._seed_prompts(self.kdir,
                           _prompt("s-a", 1, body) + _prompt("s-b", 1, body))
        self.assertEqual(
            PC._corpus_metric_recurrence_keys(self._logs(self.kdir)), 1)

    def test_the_same_session_twice_is_not_two_sessions(self):
        body = "always keep the derived org facet on the index row"
        self._seed_prompts(self.kdir,
                           _prompt("s-a", 1, body) + _prompt("s-a", 2, body))
        self.assertEqual(
            PC._corpus_metric_recurrence_keys(self._logs(self.kdir)), 0)

    def test_a_non_human_record_does_not_add_a_session(self):
        body = "always keep the derived org facet on the index row"
        self._seed_prompts(self.kdir,
                           _prompt("s-a", 1, body)
                           + _prompt("s-b", 1, body, origin="agent"))
        self.assertEqual(
            PC._corpus_metric_recurrence_keys(self._logs(self.kdir)), 0)

    def test_a_key_below_min_len_is_not_relevant(self):
        self._seed_prompts(self.kdir,
                           _prompt("s-a", 1, "short") + _prompt("s-b", 1,
                                                                "short"))
        self.assertEqual(
            PC._corpus_metric_recurrence_keys(self._logs(self.kdir)), 0)

    def test_a_blank_body_never_becomes_a_key(self):
        self._seed_prompts(self.kdir,
                           _prompt("s-a", 1, "   ") + _prompt("s-b", 1, "   "))
        self.assertEqual(
            PC._corpus_metric_recurrence_keys(self._logs(self.kdir)), 0)

    def test_an_absent_source_is_none_and_an_empty_one_is_zero(self):
        # THE None/0 split. Both sides asserted, because a fold that returned
        # None for both would pass a test that only checked the absent side.
        os.makedirs(self._logs(self.kdir), exist_ok=True)
        self.assertIsNone(
            PC._corpus_metric_recurrence_keys(self._logs(self.kdir)))
        self._seed_prompts(self.kdir, "")
        self.assertEqual(
            PC._corpus_metric_recurrence_keys(self._logs(self.kdir)), 0)

    @unittest.skipIf(os.geteuid() == 0, "root bypasses permission bits")
    def test_an_unreadable_source_is_none_and_not_zero(self):
        logp = self._seed_prompts(self.kdir, _prompt("s-a", 1, "x"))
        os.chmod(logp, 0)
        try:
            self.assertIsNone(
                PC._corpus_metric_recurrence_keys(self._logs(self.kdir)))
        finally:
            os.chmod(logp, 0o644)

    def test_a_fold_that_raises_part_way_returns_none_not_a_partial_count(self):
        # A partial tally reported as a total is how a grounded premise gets
        # falsified by a corpus fault. Asserted against a fold that yields a
        # REAL record first, so the partial count is genuinely non-zero.
        body = "always keep the derived org facet on the index row"
        self._seed_prompts(self.kdir,
                           _prompt("s-a", 1, body) + _prompt("s-b", 1, body))
        self.assertEqual(
            PC._corpus_metric_recurrence_keys(self._logs(self.kdir)), 1)
        saved = PC._parse_prompts_records

        def _raises(raw):
            yield ({"origin": "human", "sid": "s-a"}, body)
            yield ({"origin": "human", "sid": "s-b"}, body)
            raise ValueError("fold exploded mid-iteration")

        PC._parse_prompts_records = _raises
        try:
            self.assertIsNone(
                PC._corpus_metric_recurrence_keys(self._logs(self.kdir)))
        finally:
            PC._parse_prompts_records = saved

    # --- fold 2: error records ----------------------------------------------

    def test_err_records_are_counted_across_sessions(self):
        self._seed_live(self.kdir, "s-a",
                        [{"status": "ERR"}, {"status": "ok"}])
        self._seed_live(self.kdir, "s-b", [{"status": "ERR"}])
        self.assertEqual(
            PC._corpus_metric_error_records(self.kdir, self._logs(self.kdir)),
            2)

    def test_no_live_jsonl_anywhere_is_none_and_an_empty_one_is_zero(self):
        # The `any_live` flag IS this distinction. Without it every
        # `claude-error-records >= n` premise falsifies on a fresh machine.
        os.makedirs(os.path.join(self.kdir, "logs", "sessions", "s-a"),
                    exist_ok=True)
        self.assertIsNone(
            PC._corpus_metric_error_records(self.kdir, self._logs(self.kdir)))
        self._seed_live(self.kdir, "s-a", [{"status": "ok"}])
        self.assertEqual(
            PC._corpus_metric_error_records(self.kdir, self._logs(self.kdir)),
            0)

    def test_a_non_dict_record_does_not_raise(self):
        _write(os.path.join(self.kdir, "logs", "sessions", "s-a",
                            "live.jsonl"),
               json.dumps(["not", "a", "dict"]) + "\n"
               + json.dumps({"status": "ERR"}) + "\n")
        self.assertEqual(
            PC._corpus_metric_error_records(self.kdir, self._logs(self.kdir)),
            1)


class CorpusMetricDispatchTest(_KBCase):

    def test_the_dispatcher_routes_each_metric_to_its_own_fold(self):
        seen = []
        saved = (PC._corpus_metric_recurrence_keys,
                 PC._corpus_metric_error_records)
        PC._corpus_metric_recurrence_keys = lambda logs: seen.append("rec") or 1
        PC._corpus_metric_error_records = lambda k, logs: seen.append("err") or 2
        os.makedirs(os.path.join(self.kdir, "logs"), exist_ok=True)
        try:
            self.assertEqual(PC._premise_corpus_metric_value(
                "recurrence-keys-at-n", self.kdir), 1)
            self.assertEqual(PC._premise_corpus_metric_value(
                "claude-error-records", self.kdir), 2)
            self.assertIsNone(PC._premise_corpus_metric_value(
                "no-such-metric", self.kdir))
        finally:
            (PC._corpus_metric_recurrence_keys,
             PC._corpus_metric_error_records) = saved
        self.assertEqual(seen, ["rec", "err"])

    def test_no_kdir_and_no_logs_dir_short_circuit_before_either_fold(self):
        called = []
        saved = PC._corpus_metric_recurrence_keys
        PC._corpus_metric_recurrence_keys = lambda logs: called.append(1) or 9
        try:
            self.assertIsNone(PC._premise_corpus_metric_value(
                "recurrence-keys-at-n", None))
            self.assertIsNone(PC._premise_corpus_metric_value(
                "recurrence-keys-at-n", self.bare()))
        finally:
            PC._corpus_metric_recurrence_keys = saved
        self.assertEqual(called, [])

    def test_a_logs_path_that_is_a_file_is_not_a_logs_dir(self):
        d = os.path.join(self.root, "filelogs")
        _write(os.path.join(d, "logs"), "not a directory\n")
        self.assertIsNone(PC._premise_corpus_metric_value(
            "recurrence-keys-at-n", d))


# --- the numeric comparator + its evaluator ----------------------------------

class NumCmpTest(unittest.TestCase):

    def test_every_operator_of_the_closed_set(self):
        self.assertEqual(
            [PC._premise_num_cmp(2, op, 1) for op in
             (">=", "<=", ">", "<", "==", "!=")],
            [True, False, True, False, False, True])
        self.assertEqual(
            [PC._premise_num_cmp(1, op, 2) for op in
             (">=", "<=", ">", "<", "==", "!=")],
            [False, True, False, True, False, True])
        self.assertEqual(
            [PC._premise_num_cmp(1, op, 1) for op in
             (">=", "<=", ">", "<", "==", "!=")],
            [True, True, False, False, True, False])

    def test_an_operator_outside_the_closed_set_is_false_and_never_raises(self):
        for op in ("~", "", None, "=>", "is"):
            self.assertFalse(PC._premise_num_cmp(1, op, 1), op)


class EvalCorpusMetricTest(_KBCase):

    _UNSET = object()

    def _v(self, check, kdir=_UNSET):
        return PC._eval_check_corpus_metric(
            PP._parse_premise_check(check),
            self.kdir if kdir is self._UNSET else kdir)

    def test_an_unavailable_corpus_is_uncheckable_not_falsified(self):
        r = self._v("corpus-metric claude-error-records >= 1", kdir=self.bare())
        self.assertEqual(r["verdict"], "uncheckable")
        self.assertNotIn("actual", r)

    def test_a_present_corpus_grounds_and_falsifies_on_the_same_data(self):
        _write(os.path.join(self.kdir, "logs", "sessions", "s-a",
                            "live.jsonl"),
               json.dumps({"status": "ERR"}) + "\n")
        hit = self._v("corpus-metric claude-error-records >= 1")
        miss = self._v("corpus-metric claude-error-records >= 9999")
        self.assertEqual(hit["verdict"], "grounded")
        self.assertEqual(miss["verdict"], "falsified")
        self.assertEqual(hit["actual"], 1)
        self.assertIn("holds", hit["detail"])
        self.assertIn("FAILS", miss["detail"])

    def test_a_present_but_empty_corpus_is_a_genuine_zero(self):
        # The end-to-end consequence of the None/0 split: 0 must be able to
        # GROUND a `< n` premise and FALSIFY a `>= n` one, where None does
        # neither.
        _write(os.path.join(self.kdir, "logs", "sessions", "s-a",
                            "live.jsonl"),
               json.dumps({"status": "ok"}) + "\n")
        self.assertEqual(
            self._v("corpus-metric claude-error-records < 1")["verdict"],
            "grounded")
        self.assertEqual(
            self._v("corpus-metric claude-error-records >= 1")["verdict"],
            "falsified")


# --- the two remaining evaluators + the dispatcher ---------------------------

class EvalCiteLiveTest(_KBCase):

    def test_a_missing_cite_is_uncheckable_without_the_unresolved_flag(self):
        # The flag is what --strict fail-CLOSES on, so a premise with NO cite
        # must not carry it: an absent citation is an authoring gap, not a
        # citation that stopped resolving.
        for rec in ({}, {"cite": ""}, {"cite": None}):
            r = PC._eval_check_cite_live(rec, self.kdir)
            self.assertEqual(r["verdict"], "uncheckable")
            self.assertNotIn("unresolved_cite", r)

    def test_an_unresolvable_cite_carries_the_flag(self):
        r = PC._eval_check_cite_live({"cite": "learn-nope"}, self.kdir)
        self.assertEqual(r["verdict"], "uncheckable")
        self.assertTrue(r["unresolved_cite"])

    def test_a_resolvable_cite_is_grounded(self):
        self.assertEqual(
            PC._eval_check_cite_live({"cite": "learn-one"},
                                     self.kdir)["verdict"], "grounded")


class EvalConfigEqTest(unittest.TestCase):
    """`config-eq` reads the CONFIG.ENV FILES, never the process environment.

    That distinction is the reason this fixture redirects `CONFIG_PATHS` rather
    than setting an env var: the first version of this test set
    `os.environ[KEY]` and every assertion landed on the `uncheckable` arm, so
    both the grounded and the falsified halves were untested while the suite was
    green. It is also a live trap for a plan AUTHOR — a `config-eq` premise
    about a key that only ever lives in the environment is permanently
    uncheckable — and it is pinned below rather than left as folklore.

    CONFIG_PATHS is late-bound through the core.config AGGREGATOR, so the
    rebind must target the aggregator module the evaluator actually reads.
    """

    KEY = "AGENTWARE_TEST_W14_PROBE"

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw_configeq_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.cfg = os.path.join(self.root, "config.env")
        self._saved_paths = _libconfig.CONFIG_PATHS
        _libconfig.CONFIG_PATHS = (self.cfg,)
        self.addCleanup(self._restore)
        self._saved_env = os.environ.get(self.KEY)
        os.environ.pop(self.KEY, None)
        self.addCleanup(self._restore_env)

    def _restore(self):
        _libconfig.CONFIG_PATHS = self._saved_paths

    def _restore_env(self):
        if self._saved_env is None:
            os.environ.pop(self.KEY, None)
        else:
            os.environ[self.KEY] = self._saved_env

    def _seed(self, value):
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write("# a scratch config\n%s=%s\n" % (self.KEY, value))

    def _v(self, want):
        return PC._eval_check_config_eq(
            PP._parse_premise_check("config-eq %s %s" % (self.KEY, want)))

    def test_an_absent_key_is_uncheckable_and_not_falsified(self):
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write("# a scratch config that defines some OTHER key\n"
                    "AGENTWARE_TEST_W14_SOMETHING_ELSE=yes\n")
        r = self._v("yes")
        self.assertEqual(r["verdict"], "uncheckable")
        self.assertNotIn("actual", r)
        self.assertEqual(r["expected"], "yes")

    def test_a_configured_key_grounds_and_falsifies(self):
        self._seed("yes")
        self.assertEqual(self._v("yes")["verdict"], "grounded")
        f = self._v("no")
        self.assertEqual(f["verdict"], "falsified")
        self.assertEqual(f["actual"], "yes")
        self.assertEqual(f["expected"], "no")

    def test_a_quoted_value_is_unquoted_before_comparison(self):
        self._seed('"yes"')
        self.assertEqual(self._v("yes")["verdict"], "grounded")

    def test_the_process_environment_is_not_consulted(self):
        # The trap, asserted: an env var alone leaves the premise UNCHECKABLE.
        os.environ[self.KEY] = "yes"
        self.assertEqual(self._v("yes")["verdict"], "uncheckable")


class EvaluatePremiseTest(_KBCase):

    def _rec(self, check):
        return {"check": check, "parsed": PP._parse_premise_check(check)}

    def test_every_kind_of_the_closed_grammar_routes_to_its_evaluator(self):
        self.assertEqual(
            PC.evaluate_premise(self._rec("path exists MAIN.md"),
                                self.kdir)["verdict"], "grounded")
        self.assertEqual(
            PC.evaluate_premise({"cite": "learn-one",
                                 "parsed": PP._parse_premise_check("cite-live"),
                                 "check": "cite-live"},
                                self.kdir)["verdict"], "grounded")

    def test_a_rejected_check_is_flagged_rejected_and_never_run(self):
        r = PC.evaluate_premise(self._rec("path exists x; rm -rf /"), self.kdir)
        self.assertEqual(r["verdict"], "uncheckable")
        self.assertTrue(r["rejected"])

    def test_an_unknown_or_malformed_kind_is_uncheckable_without_the_flag(self):
        for check in ("teleport the KB", "config-eq ONLYKEY", ""):
            r = PC.evaluate_premise(self._rec(check), self.kdir)
            self.assertEqual(r["verdict"], "uncheckable", check)
            self.assertNotIn("rejected", r, check)

    def test_a_kind_outside_the_dispatch_table_is_uncheckable(self):
        r = PC.evaluate_premise(
            {"check": "x", "parsed": {"kind": "no-such-kind", "args": []}},
            self.kdir)
        self.assertEqual(r["verdict"], "uncheckable")
        self.assertIn("unhandled check kind", r["detail"])

    # --- the fallback parse, restored in wave 14 ----------------------------

    def test_a_record_with_no_parsed_key_is_parsed_here(self):
        # Between the strangler split and task 99 wave 14 this raised NameError:
        # the monolith had the parser as a sibling GLOBAL, and the split left
        # the fallback naming a symbol this module does not bind. Nothing
        # in-tree reaches it (`plan_premises` stamps `parsed` on every record),
        # which is exactly why nothing caught it.
        self.assertEqual(
            PC.evaluate_premise({"check": "path exists MAIN.md"},
                                self.kdir)["verdict"], "grounded")
        self.assertEqual(
            PC.evaluate_premise({"check": "path exists ghost.md"},
                                self.kdir)["verdict"], "falsified")

    def test_a_falsy_parsed_re_parses_rather_than_dispatching_on_none(self):
        # The `or` is load-bearing: an empty dict and None are both falsy, and
        # dispatching on their `kind` of None would report "unhandled check
        # kind None" for a check the grammar can read perfectly well.
        for parsed in ({}, None):
            r = PC.evaluate_premise(
                {"check": "path exists MAIN.md", "parsed": parsed}, self.kdir)
            self.assertEqual(r["verdict"], "grounded", parsed)

    def test_a_record_with_neither_key_is_uncheckable_and_does_not_raise(self):
        r = PC.evaluate_premise({}, self.kdir)
        self.assertEqual(r["verdict"], "uncheckable")
        self.assertIn("empty check", r["detail"])

    def test_the_fallback_still_refuses_a_metacharacter_check(self):
        # The SECURITY property survives the fallback: an unparsed record is
        # parsed by the TRUST BOUNDARY, not by a second grammar living here.
        r = PC.evaluate_premise({"check": "path exists x; rm -rf /"}, self.kdir)
        self.assertEqual(r["verdict"], "uncheckable")
        self.assertTrue(r["rejected"])


class ModuleBoundaryTest(unittest.TestCase):

    def test_it_holds_no_module_level_import_of_the_parser(self):
        # The parser edge is CALL-TIME on purpose: `ops.planpremises` imports
        # this module at module level, so a module-level import here would close
        # an import cycle and fix the package's load order. A function-local
        # import cannot (R-MOD-02's documented escape hatch).
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(PC))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", "") or ""
                self.assertNotIn("planpremises", mod)
                for alias in node.names:
                    self.assertNotIn("planpremises", alias.name)

    def test_the_facade_export_list_names_only_donor_symbols(self):
        # Wave 14's helpers are new and have no monolith counterpart, so
        # exporting them would widen the facade surface the parity machinery
        # pins. The pre-existing ten stay exported.
        for name in PC._EXPORTS:
            self.assertTrue(hasattr(PC, name), name)
        for new in ("_cite_in_index", "_cite_under_kdir", "_path_verdict",
                    "_eval_path_mtime_before",
                    "_corpus_metric_recurrence_keys",
                    "_corpus_metric_error_records"):
            self.assertTrue(hasattr(PC, new), new)
            self.assertNotIn(new, PC._EXPORTS)


if __name__ == "__main__":
    unittest.main()
