"""Owning unit file for `agentware_lib.retrieval.bench.benchledger` — task 99 w10.

The module had no owning file before this one, and it carried the widest body
left in the lib after wave 9: `cmd_bench_lint` at 25 branches. It also owns the
ONE sanctioned mutation of an append-only ledger, which is the kind of code that
should be hard to change by accident and was, until now, asserted only through a
CLI row.

What is asserted here is the module's PROMISE, not its shape:

  * THE REDACTOR TOUCHES ONE FIELD AND NOTHING ELSE. A row whose `gold_path`
    does not change keeps its ORIGINAL BYTES — not an equal re-serialization, the
    same bytes — so re-writing the file cannot quietly reformat an immutable
    ledger. Blank and un-parseable lines pass through verbatim.
  * THE COVERAGE VERDICT IS TWO-PART. A run fails on a low ratio OR on a
    dangling expected_id, and prints a separate line for each. The row that
    separates `ratio >= threshold and not dangling` from a ratio-only verdict is
    a HEALTHY ratio WITH a dangling id, and it is driven here.
  * `covered` COUNTS REFERENCED IDS, NOT REACHED ONES. A dangling id is in both
    `covered` and `dangling`. Narrowing it would leave `uncovered` identical and
    silently change one printed number on exactly the runs that also print a
    DANGLING line — the shape a same-looking refactor changes and no metric moves.
  * THE CLUSTER BLOCK IS BOUNDED AT 30. Both sides are driven, because a
    renderer that lost the bound prints a wall no smaller fixture can see.
  * A POISONED TAG DOES NOT CRASH THE REPORT. `setdefault(<dict>, ...)` is an
    unhashable-type TypeError, and an index is attacker-influenced input.

Stdlib-only, hermetic: temp dirs under `mkdtemp`, no network, no real KB.
"""

from tests._paths import TESTS_DIR

import ast
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.retrieval.bench.benchledger as benchledger  # noqa: E402


class _Args(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


@contextlib.contextmanager
def _captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def _entries(n, poisoned=False):
    out = []
    for i in range(n):
        tags = ["alpha", "beta", "gamma"] if i % 2 else ["beta"]
        if poisoned and i == 0:
            tags = [{"not": "a string"}, "alpha"]
        out.append({"id": "e%02d" % i, "title": "Entry %d" % i, "tags": tags})
    return out


def _gold(covered=0, dangling=0):
    rows = [{"query": "q%d" % i, "expected_ids": ["e%02d" % i]}
            for i in range(covered)]
    rows += [{"query": "d%d" % j, "expected_ids": ["ghost-%d" % j]}
             for j in range(dangling)]
    return rows


class GoldCoverageTest(unittest.TestCase):
    """`_gold_coverage` — the single pass that used to be three loops."""

    def test_benchledger_coverage_splits_reached_from_missing(self):
        cov = benchledger._gold_coverage(_gold(covered=2), _entries(5))
        self.assertEqual(cov["covered"], {"e00", "e01"})
        self.assertEqual(cov["uncovered"], {"e02", "e03", "e04"})
        self.assertEqual(cov["dangling"], set())

    def test_benchledger_a_dangling_id_is_reported_as_dangling(self):
        cov = benchledger._gold_coverage(_gold(covered=1, dangling=2),
                                         _entries(3))
        self.assertEqual(cov["dangling"], {"ghost-0", "ghost-1"})

    def test_benchledger_a_dangling_id_is_also_counted_as_referenced(self):
        """`covered` is every id the gold set REFERENCES. A dangling id is in
        BOTH sets, and that is deliberate: `covered` exists to compute
        `uncovered = entry_ids - covered`, which a dangling id cannot affect,
        and the report prints its size. Narrowing it to `covered & entry_ids`
        leaves `uncovered` identical and changes that one printed number."""
        cov = benchledger._gold_coverage(_gold(covered=1, dangling=1),
                                         _entries(3))
        self.assertIn("ghost-0", cov["covered"])
        self.assertEqual(cov["uncovered"], {"e01", "e02"})

    def test_benchledger_clusters_group_uncovered_entries_by_tag(self):
        cov = benchledger._gold_coverage(_gold(covered=0), _entries(4))
        self.assertEqual(sorted(cov["clusters"]), ["alpha", "beta"])
        self.assertEqual(sorted(cov["clusters"]["beta"]),
                         ["e00", "e01", "e02", "e03"])

    def test_benchledger_clusters_keep_only_the_first_two_tags(self):
        """The cluster map is a work order, not an index: a five-tag entry
        would otherwise appear in five clusters and inflate every one."""
        cov = benchledger._gold_coverage([], _entries(2))
        self.assertNotIn("gamma", cov["clusters"])

    def test_benchledger_clusters_exclude_covered_entries(self):
        cov = benchledger._gold_coverage(_gold(covered=4), _entries(4))
        self.assertEqual(cov["clusters"], {})

    def test_benchledger_a_non_string_tag_does_not_crash_the_fold(self):
        """`clusters.setdefault(<dict>, ...)` is an unhashable-type TypeError,
        and an index entry is attacker-influenced input. The string filter is
        the guard, and this is the input that proves it is doing work."""
        cov = benchledger._gold_coverage([], _entries(2, poisoned=True))
        self.assertIn("alpha", cov["clusters"])
        self.assertIn("e00", cov["clusters"]["alpha"])

    def test_benchledger_a_gold_row_with_no_expected_ids_is_skipped(self):
        """A malformed gold row must not crash the lint that exists to find
        malformed gold rows."""
        cov = benchledger._gold_coverage([{"query": "q"}], _entries(2))
        self.assertEqual(cov["covered"], set())
        self.assertEqual(cov["uncovered"], {"e00", "e01"})


class BenchLintReportTest(unittest.TestCase):
    """`_print_bench_lint_text` — the human report."""

    def _render(self, cov, n_queries, n_entries, ratio, threshold, ok):
        with _captured() as (out, _err):
            benchledger._print_bench_lint_text(cov, n_queries, n_entries,
                                               ratio, threshold, ok)
        return out.getvalue()

    def _cov(self, n=8, covered=4, dangling=0):
        return benchledger._gold_coverage(_gold(covered, dangling), _entries(n))

    def test_benchledger_report_passes_when_told_it_passed(self):
        text = self._render(self._cov(), 4, 8, 0.5, 0.25, True)
        self.assertIn("BENCH LINT PASSED", text)
        self.assertNotIn("FAILED", text)

    def test_benchledger_report_names_the_low_ratio(self):
        text = self._render(self._cov(40, 4), 4, 40, 0.10, 0.25, False)
        self.assertIn("BENCH LINT FAILED: coverage 0.10 < threshold 0.25", text)

    def test_benchledger_report_names_the_dangling_ids_separately(self):
        cov = self._cov(8, 4, 2)
        text = self._render(cov, 6, 8, 0.75, 0.25, False)
        self.assertIn("DANGLING expected_ids", text)
        self.assertIn("dangling expected_ids reference", text)
        self.assertNotIn("coverage 0.75 < threshold", text)

    def test_benchledger_report_prints_both_failure_lines_when_both_fail(self):
        """The two halves are independent, so a run that is wrong in both ways
        says both things rather than stopping at the first."""
        cov = self._cov(40, 4, 2)
        text = self._render(cov, 6, 40, 0.15, 0.25, False)
        self.assertIn("coverage 0.15 < threshold", text)
        self.assertIn("dangling expected_ids reference", text)

    def test_benchledger_report_lists_clusters_at_exactly_thirty_uncovered(self):
        cov = self._cov(32, 2)
        self.assertEqual(len(cov["uncovered"]), 30)
        text = self._render(cov, 2, 32, 0.06, 0.05, True)
        self.assertIn("top uncovered clusters", text)

    def test_benchledger_report_suppresses_clusters_above_thirty(self):
        """The bound is `<= 30`. One entry more and the block is a wall rather
        than a work order — a renderer that lost the guard fails only here."""
        cov = self._cov(33, 2)
        self.assertEqual(len(cov["uncovered"]), 31)
        text = self._render(cov, 2, 33, 0.06, 0.05, True)
        self.assertNotIn("top uncovered clusters", text)

    def test_benchledger_report_omits_the_cluster_block_when_all_covered(self):
        cov = self._cov(6, 6)
        self.assertEqual(cov["uncovered"], set())
        text = self._render(cov, 6, 6, 1.0, 0.25, True)
        self.assertNotIn("top uncovered clusters", text)

    def test_benchledger_report_caps_the_cluster_list_at_five(self):
        entries = [{"id": "e%d" % i, "tags": ["t%d" % i]} for i in range(8)]
        cov = benchledger._gold_coverage([], entries)
        text = self._render(cov, 0, 8, 0.0, 0.0, True)
        listed = [ln for ln in text.splitlines() if ln.startswith("    t")]
        self.assertEqual(len(listed), 5)

    def test_benchledger_report_elides_a_cluster_over_three_entries(self):
        entries = [{"id": "e%d" % i, "tags": ["shared"]} for i in range(4)]
        cov = benchledger._gold_coverage([], entries)
        text = self._render(cov, 0, 4, 0.0, 0.0, True)
        self.assertIn("...", text)

    def test_benchledger_report_prints_the_covered_and_uncovered_counts(self):
        cov = self._cov(8, 4)
        text = self._render(cov, 4, 8, 0.5, 0.25, True)
        self.assertIn("covered: 4 entries | uncovered: 4 entries", text)


class BenchLintHandlerTest(unittest.TestCase):
    """`cmd_bench_lint` — the handler, its guards and its two formats."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="benchledger-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(self._restore)

    def _restore(self):
        if self.prev is None:
            os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        else:
            os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.prev

    def _seed(self, n=8, covered=4, dangling=0, poisoned=False):
        with open(os.path.join(self.kdir, "index.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"entries": _entries(n, poisoned)}, f)
        os.makedirs(os.path.join(self.kdir, "benchmarks"), exist_ok=True)
        with open(os.path.join(self.kdir, "benchmarks", "recall-gold.json"),
                  "w", encoding="utf-8") as f:
            json.dump(_gold(covered, dangling), f)

    def _run(self, **kw):
        with _captured() as (out, err):
            rc = benchledger.cmd_bench_lint(_Args(**kw))
        return rc, out.getvalue(), err.getvalue()

    def test_benchledger_lint_skips_and_passes_with_no_gold_set(self):
        """A KB that has never had a gold set is not a failing KB. It must exit
        0 and SAY it skipped, rather than reporting a coverage of zero."""
        with open(os.path.join(self.kdir, "index.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"entries": _entries(3)}, f)
        rc, out, _err = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("skipping", out)

    def test_benchledger_lint_passes_on_a_healthy_gold_set(self):
        self._seed()
        rc, out, _err = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("BENCH LINT PASSED", out)

    def test_benchledger_lint_fails_a_healthy_ratio_with_a_dangling_id(self):
        """THE ROW that separates the two-part verdict from a ratio-only one.
        Coverage is comfortably above threshold and the run must still FAIL."""
        self._seed(n=8, covered=4, dangling=1)
        rc, out, _err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("dangling expected_ids", out)

    def test_benchledger_lint_fails_a_low_ratio(self):
        self._seed(n=40, covered=4)
        rc, _out, _err = self._run()
        self.assertEqual(rc, 1)

    def test_benchledger_lint_default_threshold_is_a_quarter(self):
        """No `min_coverage` attribute at all — the handler must default it
        rather than raise. 4 queries over 40 entries is 0.10, below 0.25."""
        self._seed(n=40, covered=4)
        rc, out, _err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("threshold: 0.25", out)

    def test_benchledger_lint_honours_an_explicit_threshold(self):
        self._seed(n=40, covered=4)
        rc, _out, _err = self._run(min_coverage=0.05)
        self.assertEqual(rc, 0)

    def test_benchledger_lint_json_and_text_agree_on_the_verdict(self):
        """The two arms share ONE `ok`, so they can never disagree about
        whether the KB passed while printing different things."""
        self._seed(n=8, covered=4, dangling=1)
        rc_text, _t, _e = self._run()
        rc_json, out_json, _e2 = self._run(format="json")
        self.assertEqual(rc_text, rc_json)
        self.assertFalse(json.loads(out_json)["pass"])

    def test_benchledger_lint_json_reports_every_count(self):
        self._seed(n=8, covered=4, dangling=1)
        _rc, out, _err = self._run(format="json")
        payload = json.loads(out)
        self.assertEqual(payload["entries"], 8)
        self.assertEqual(payload["gold_queries"], 5)
        self.assertEqual(payload["dangling_expected_ids"], ["ghost-0"])
        self.assertEqual(payload["covered_entries"], 5)
        self.assertEqual(payload["uncovered_entries"], 4)

    def test_benchledger_lint_survives_a_poisoned_tag(self):
        self._seed(n=8, covered=0, poisoned=True)
        rc, out, _err = self._run(min_coverage=0.0)
        self.assertEqual(rc, 0)
        self.assertIn("top uncovered clusters", out)

    def test_benchledger_lint_an_empty_index_scores_zero_not_a_crash(self):
        """`n_queries / n_entries` with no entries is a ZeroDivisionError. The
        guard makes it 0.0, which then fails the default threshold."""
        with open(os.path.join(self.kdir, "index.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"entries": []}, f)
        os.makedirs(os.path.join(self.kdir, "benchmarks"), exist_ok=True)
        with open(os.path.join(self.kdir, "benchmarks", "recall-gold.json"),
                  "w", encoding="utf-8") as f:
            json.dump(_gold(covered=0), f)
        rc, out, _err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("0.00 coverage", out)


class LedgerRedactorTest(unittest.TestCase):
    """`redact_ledger_gold_paths` — the ONE sanctioned append-only mutation."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="benchledger-redact-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.path = os.path.join(self.root, "history.jsonl")

    def _write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def _read(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_benchledger_redact_of_a_missing_file_is_a_noop(self):
        self.assertEqual(
            benchledger.redact_ledger_gold_paths(
                os.path.join(self.root, "absent.jsonl")), (0, 0))

    def test_benchledger_redact_preserves_an_unchanged_rows_bytes(self):
        """Not "an equal row" — the SAME BYTES. A ledger is append-only, so a
        redactor that re-serialized every row would rewrite key order and
        spacing across a file nobody is allowed to rewrite."""
        original = '{"z":1,   "gold_path": "benchmarks/recall-gold.json","a":2}\n'
        self._write(original)
        total, changed = benchledger.redact_ledger_gold_paths(self.path)
        self.assertEqual((total, changed), (1, 0))
        self.assertEqual(self._read(), original)

    def test_benchledger_redact_passes_blank_lines_through(self):
        self._write("\n\n")
        total, changed = benchledger.redact_ledger_gold_paths(self.path)
        self.assertEqual((total, changed), (0, 0))
        self.assertEqual(self._read(), "\n\n")

    def test_benchledger_redact_passes_unparseable_lines_through_verbatim(self):
        """A corrupt line is not a licence to drop it: the row may be the only
        record of a run, and this is the one writer allowed to touch the file."""
        self._write("not json at all\n")
        total, changed = benchledger.redact_ledger_gold_paths(self.path)
        self.assertEqual((total, changed), (0, 0))
        self.assertEqual(self._read(), "not json at all\n")

    def test_benchledger_redact_counts_a_row_without_a_gold_path(self):
        """It is a ROW (so it counts toward the total) and it is untouched."""
        self._write('{"mode": "eval"}\n')
        self.assertEqual(benchledger.redact_ledger_gold_paths(self.path),
                         (1, 0))

    def test_benchledger_redact_rewrites_only_the_gold_path_field(self):
        home = os.path.expanduser("~")
        row = {"mode": "eval", "recall_at_k": 0.5,
               "gold_path": os.path.join(home, "kb", "benchmarks",
                                         "recall-gold.json")}
        self._write(json.dumps(row) + "\n")
        total, changed = benchledger.redact_ledger_gold_paths(self.path)
        self.assertEqual(total, 1)
        after = json.loads(self._read().strip())
        self.assertEqual(after["recall_at_k"], 0.5)
        self.assertEqual(after["mode"], "eval")
        if changed:
            self.assertNotIn(home, after["gold_path"])

    def test_benchledger_redact_preserves_row_order(self):
        rows = [{"mode": "eval", "n": i,
                 "gold_path": os.path.join(os.path.expanduser("~"), "g.json")}
                for i in range(3)]
        self._write("".join(json.dumps(r) + "\n" for r in rows))
        benchledger.redact_ledger_gold_paths(self.path)
        after = [json.loads(ln) for ln in self._read().splitlines() if ln.strip()]
        self.assertEqual([r["n"] for r in after], [0, 1, 2])


class BenchLedgerSurfaceTest(unittest.TestCase):

    def test_benchledger_exports_name_every_helper(self):
        for name in ("redact_ledger_gold_paths", "cmd_bench_redact",
                     "_gold_coverage", "_print_bench_lint_text",
                     "cmd_bench_lint"):
            self.assertIn(name, benchledger._EXPORTS)
            self.assertTrue(hasattr(benchledger, name), name)

    def test_benchledger_keeps_the_index_import_function_local(self):
        """retrieval -> knowledge must stay lazy (layer discipline), so the
        index import lives inside the handler, not at module level."""
        with open(benchledger.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        top = [n for n in tree.body
               if isinstance(n, (ast.Import, ast.ImportFrom))]
        for node in top:
            names = [getattr(node, "module", "") or ""] + [
                a.name for a in node.names]
            self.assertFalse(
                any("knowledge" in n for n in names),
                "knowledge is imported at MODULE level: %r" % (names,))
        nested = [n for n in ast.walk(tree)
                  if isinstance(n, ast.ImportFrom)
                  and (n.module or "").startswith("agentware_lib.knowledge")]
        self.assertTrue(nested, "the lazy index import is gone entirely")


if __name__ == "__main__":
    unittest.main()
