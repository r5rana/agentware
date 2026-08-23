"""Owning unit file for `agentware_lib.retrieval.evalcmd` — task 99 wave 10.

The module had no owning file before this one. It carried three ceiling rows —
`cmd_eval` (21 branches / 120 lines), `_run_longmemeval_suite` (18) and
`_run_gold_fixture_suite` (13) — and its behaviour was asserted only through CLI
rows, which cannot construct a scorer that raises.

What is asserted here is the module's PROMISE, not its shape:

  * THE EXCEPTION TAXONOMY IS NOT ONE SET, AND THE ASYMMETRY IS DELIBERATE.
    `_run_longmemeval_suite` catches NotImplementedError -> 2 and
    (ValueError, OSError, JSONDecodeError) -> 1, because it reads an
    OPERATOR-SUPPLIED dataset file. `_run_gold_fixture_suite` catches
    NotImplementedError -> 2 and ValueError -> 1 and lets an OSError PROPAGATE,
    because its corpus ships frozen with the package and an unreadable one is a
    packaging defect rather than an input to report. Both arms are driven with
    all four exception kinds; a refactor that lifted "the guards" into one
    shared helper collapses them and fails exactly one row.
  * A SUITE IS DECIDED BEFORE THE OWN GOLD SET IS EVEN LOADED, because a suite
    scores its OWN corpus — so a KB with no `recall-gold.json` must still be
    able to run one.
  * EACH SUITE STAMPS ITS OWN NAME ON THE GATE, so the ledger never compares a
    longmemeval run to a gold-fixture one.

This file is the SUITE half. The own-gold half — the ablation instrument,
`cmd_eval`'s arm dispatch, the two own-gold arms and the ACR win gate's renderer
— lives in `test_evalcmd_owngold.py`, split off when this file hit the corpus's
600-line cap; the shared header is `_evalcmd_support.py`. The module is still
owned by THIS path, per R-MOD-06.

Stdlib-only, hermetic: a temp knowledge dir, scorers injected at the module's own
sibling-alias seams (so the PARENT under test is always the real one), the
package's `AGENTWARE_NESTED_UNITTEST` recursion guard set so `--record` never
shells the suite. No network.
"""

from tests._paths import TESTS_DIR  # noqa: F401  (repo anchor, see _evalcmd_support)

import ast
import json
import os
import unittest

try:
    from tests.unit.retrieval._evalcmd_support import (
        _Args, _Boom, _EvalCmdCase, _captured, _lme_scored, _metrics, _scored,
        evalcmd)
except ImportError:  # allow `python3 -m unittest tests.unit...` from tests/
    from _evalcmd_support import (  # noqa: F401
        _Args, _Boom, _EvalCmdCase, _captured, _lme_scored, _metrics, _scored,
        evalcmd)


class SuiteExceptionTaxonomyTest(_EvalCmdCase):
    """The asymmetry between the two suite runners' except-chains."""

    _KINDS = [
        ("notimplemented", NotImplementedError("no embedder available")),
        ("valueerror", ValueError("malformed dataset")),
        ("oserror", OSError("disk went away")),
        ("jsondecodeerror", json.JSONDecodeError("bad", "{}", 0)),
    ]

    def _lme(self, exc):
        self._patch(evalcmd._longmemeval, "evaluate_longmemeval", _Boom(exc))
        return self._call(evalcmd._run_longmemeval_suite,
                          _Args(dataset=self.dataset, top_k=2,
                                as_of="2026-01-15", limit=None, strategy="tag",
                                format="text", tolerance=0.02),
                          self.kdir, self.data)

    def _gold(self, exc):
        self._patch(evalcmd._goldfixture, "evaluate_gold_fixture", _Boom(exc))
        return self._call(evalcmd._run_gold_fixture_suite,
                          _Args(top_k=3, as_of="2026-01-15", strategy="tag",
                                format="text", tolerance=0.02),
                          self.kdir, self.data)

    def test_evalcmd_an_unavailable_backend_exits_two_on_both_suites(self):
        """Exit 2 is "cannot run", distinct from 1 which is "ran and failed".
        A caller that treated them alike would report a missing embedder as a
        recall regression."""
        for run in (self._lme, self._gold):
            rc, _out, err = run(NotImplementedError("no embedder available"))
            self.assertEqual(rc, 2)
            self.assertIn("no embedder available", err)

    def test_evalcmd_lme_reports_a_bad_dataset_rather_than_crashing(self):
        """The dataset is OPERATOR-SUPPLIED, so all three of ValueError,
        OSError and JSONDecodeError are input problems to report at rc 1."""
        for _name, exc in self._KINDS[1:]:
            rc, _out, err = self._lme(exc)
            self.assertEqual(rc, 1, exc)
            self.assertTrue(err.startswith("agentware:"), err)

    def test_evalcmd_gold_fixture_reports_a_valueerror(self):
        rc, _out, err = self._gold(ValueError("malformed fixture"))
        self.assertEqual(rc, 1)
        self.assertIn("malformed fixture", err)

    def test_evalcmd_gold_fixture_reports_a_jsondecodeerror_too(self):
        """Not a separate arm: JSONDecodeError SUBCLASSES ValueError, so the
        narrow chain catches it for free. Asserted so the inheritance is a
        stated property rather than a coincidence the next reader re-derives."""
        rc, _out, _err = self._gold(json.JSONDecodeError("bad", "{}", 0))
        self.assertEqual(rc, 1)

    def test_evalcmd_gold_fixture_lets_an_oserror_propagate(self):
        """THE ROW THAT SEPARATES THE TWO CHAINS. The frozen corpus ships with
        the package, so an unreadable one is a packaging defect and must
        surface as the crash it is — not as exit 1 beside a printed message
        that suggests the operator mistyped something. A shared guard for both
        suites fails here and nowhere else."""
        with self.assertRaises(OSError):
            self._gold(OSError("disk went away"))


class SuiteRunnerTest(_EvalCmdCase):
    """The two suite runners on their success paths."""

    def _run_lme(self, **kw):
        self._patch(evalcmd._longmemeval, "evaluate_longmemeval",
                    lambda *a, **k: _lme_scored())
        args = _Args(dataset=self.dataset, top_k=2, as_of="2026-01-15",
                     limit=None, strategy="tag", format="text",
                     tolerance=0.02)
        for k, v in kw.items():
            setattr(args, k, v)
        return self._call(evalcmd._run_longmemeval_suite, args, self.kdir,
                          self.data)

    def _run_gold(self, **kw):
        self._patch(evalcmd._goldfixture, "evaluate_gold_fixture",
                    lambda *a, **k: _scored(suite="gold-fixture"))
        args = _Args(top_k=3, as_of="2026-01-15", strategy="tag",
                     format="text", tolerance=0.02)
        for k, v in kw.items():
            setattr(args, k, v)
        return self._call(evalcmd._run_gold_fixture_suite, args, self.kdir,
                          self.data)

    def test_evalcmd_lme_prints_the_report_and_exits_zero(self):
        rc, out, _err = self._run_lme()
        self.assertEqual(rc, 0)
        self.assertIn("eval --suite longmemeval", out)

    def test_evalcmd_lme_stamps_the_dataset_as_the_gold_path(self):
        """The ledger keys comparability on what was scored, so the dataset
        path travels with the result rather than being re-derived."""
        _rc, out, _err = self._run_lme(format="json")
        self.assertEqual(json.loads(out)["gold_path"], self.dataset)

    def test_evalcmd_lme_refuses_a_missing_dataset_before_scoring(self):
        """The guard runs first, so the scorer is never reached — asserted
        with a scorer that would EXPLODE if it were."""
        self._patch(evalcmd._longmemeval, "evaluate_longmemeval",
                    _Boom(AssertionError("scorer must not run")))
        rc, _out, err = self._call(
            evalcmd._run_longmemeval_suite,
            _Args(dataset=os.path.join(self.kdir, "absent.json"), top_k=2,
                  as_of="2026-01-15", limit=None, strategy="tag",
                  format="text"), self.kdir, self.data)
        self.assertEqual(rc, 1)
        self.assertIn("longmemeval dataset not found", err)

    def test_evalcmd_gold_fixture_prints_the_report_and_exits_zero(self):
        rc, out, _err = self._run_gold()
        self.assertEqual(rc, 0)
        self.assertIn("eval --suite gold-fixture", out)

    def test_evalcmd_gold_fixture_uses_the_shipped_path_by_default(self):
        """No `--gold`, so the handler resolves the frozen fixture itself."""
        import agentware_lib.retrieval.goldfixture as goldfixture
        _rc, out, _err = self._run_gold(format="json")
        self.assertEqual(json.loads(out)["gold_path"],
                         goldfixture.gold_fixture_path())

    def test_evalcmd_gold_fixture_honours_an_explicit_gold_path(self):
        _rc, out, _err = self._run_gold(format="json", gold="/custom/fix.json")
        self.assertEqual(json.loads(out)["gold_path"], "/custom/fix.json")

    def test_evalcmd_both_suites_refuse_a_bad_top_k(self):
        self._patch(evalcmd._longmemeval, "evaluate_longmemeval",
                    _Boom(AssertionError("scorer must not run")))
        self._patch(evalcmd._goldfixture, "evaluate_gold_fixture",
                    _Boom(AssertionError("scorer must not run")))
        rc_l, _o, err_l = self._call(
            evalcmd._run_longmemeval_suite,
            _Args(dataset=self.dataset, top_k=0, as_of="2026-01-15",
                  limit=None, strategy="tag", format="text"),
            self.kdir, self.data)
        rc_g, _o2, err_g = self._call(
            evalcmd._run_gold_fixture_suite,
            _Args(top_k=0, as_of="2026-01-15", strategy="tag", format="text"),
            self.kdir, self.data)
        self.assertEqual((rc_l, rc_g), (1, 1))
        self.assertEqual(err_l, err_g)

    def test_evalcmd_suite_json_arm_prints_no_human_report(self):
        """The two formats are exclusive: a JSON consumer that also received
        the human table would fail to parse the stream."""
        _rc, out, _err = self._run_gold(format="json")
        self.assertNotIn("eval --suite", out)
        json.loads(out)

    def test_evalcmd_suites_stamp_their_own_suite_on_the_gate(self):
        """Each suite records under its OWN name, so the ledger never compares
        a longmemeval run to a gold-fixture one."""
        _rc, out_l, _e = self._run_lme(format="json", gate=True)
        _rc2, out_g, _e2 = self._run_gold(format="json", gate=True)
        self.assertEqual(json.loads(out_l)["gate"]["suite"], "longmemeval")
        self.assertEqual(json.loads(out_g)["gate"]["suite"], "gold-fixture")

class EvalCmdSurfaceTest(unittest.TestCase):

    def test_evalcmd_exports_name_every_handler(self):
        for name in ("_ABLATE_METRIC_KEYS", "evaluate_ablation", "_ok",
                     "_print_acr_gate", "_run_acr_gate",
                     "_run_longmemeval_suite", "_run_gold_fixture_suite",
                     "_run_ablate_suite", "_run_default_suite", "cmd_eval"):
            self.assertIn(name, evalcmd._EXPORTS)
            self.assertTrue(hasattr(evalcmd, name), name)

    def test_evalcmd_declares_the_eval_verb_once(self):
        """`__verbs__` is the ONE place the verb is registered; the registrar
        travels with the handler."""
        verbs = [v for (v, _order, _reg) in evalcmd.__verbs__]
        self.assertEqual(verbs, ["eval"])
        self.assertTrue(hasattr(evalcmd, evalcmd.__verbs__[0][2]))

    def test_evalcmd_keeps_the_index_import_function_local(self):
        """retrieval -> knowledge must stay lazy (layer discipline)."""
        with open(evalcmd.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [getattr(node, "module", "") or ""] + [
                    a.name for a in node.names]
                self.assertFalse(any("knowledge" in n for n in names), names)

    def test_evalcmd_owns_the_ablation_so_the_sibling_never_imports_back(self):
        """The one-way direction is what keeps `evalsuites` cycle-free:
        `evaluate_ablation` is defined HERE, so no renderer over there ever
        needs to reach back for it."""
        import agentware_lib.retrieval.evalsuites as evalsuites
        self.assertTrue(hasattr(evalcmd, "evaluate_ablation"))
        self.assertFalse(hasattr(evalsuites, "evaluate_ablation"))


if __name__ == "__main__":
    unittest.main()
