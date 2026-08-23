"""Owning unit file for `agentware_lib.ops.premisecheck` (task 99 wave 14).

This module is the READ-ONLY I/O half of the R-GND-01 premise gate: a validated
record in, a verdict out, no parsing anywhere. It had coverage before this file
existed and none of it was OWNERSHIP — `tests/unit/ops/test_premise_gate.py` is
named for the FEATURE and drives both halves through the CLI, so it does not
redden when this module alone breaks, and it reached none of the degradation
arms below.

This half owns the CITE RESOLVERS and the PATH EVALUATOR. The two corpus folds,
the metric dispatcher, the remaining evaluators and `evaluate_premise` live in
the sibling `test_premisecheck_corpus.py`; the file was split at the 600-line
`tests/**` cap along that class boundary rather than having its cap raised
(R-MOD-04), and the shared fixture lives in `_premisecheck_support.py`.

Three properties are asserted across the pair that no CLI run can express, and
each is the difference between a gate that halts a loop correctly and one that
halts it wrongly:

  1. FAIL-OPEN IS A RETURN VALUE. An unavailable source returns `uncheckable` /
     `None`; it is NEVER a falsification. A plan grounded at authoring time must
     not be able to DoS a later run because its evidence rotated away (D5).
  2. THE None/0 SPLIT. A corpus that is PRESENT but empty is a genuine 0. Only
     an ABSENT source is None. Collapsing the two makes every
     `corpus-metric >= n` premise falsify on a fresh machine.
  3. CONTAINMENT BEFORE EXISTENCE. A cite or a path escaping the knowledge dir
     is unresolvable REGARDLESS of whether the target exists, so the gate can
     never be used to probe the filesystem outside the KB.

Stdlib `unittest` only; every KB is a throwaway tempdir (R-LOC-03).

Runner:  python3 -m unittest tests.unit.ops.test_premisecheck -v
"""

import json
import os
import unittest

from tests.unit.ops._premisecheck_support import (
    PC, PP, _KBCase, _prompt, _write, _libconfig)


# --- the two cite resolvers --------------------------------------------------

class CiteResolversTest(_KBCase):

    def test_the_index_resolver_matches_an_id_and_a_path(self):
        self.assertTrue(PC._cite_in_index("learn-one", self.kdir))
        self.assertTrue(PC._cite_in_index("learnings/one.md", self.kdir))
        self.assertFalse(PC._cite_in_index("learn-nope", self.kdir))

    def test_the_index_resolver_never_answers_for_an_unindexed_file(self):
        # The row that SEPARATES the two resolvers: a work artifact is not
        # indexed, so resolver 1 must say no and resolver 2 must say yes. A test
        # that only drove `_premise_cite_resolves` would pass with either
        # resolver deleted.
        self.assertFalse(PC._cite_in_index("work/f/worklog.md", self.kdir))
        self.assertTrue(PC._cite_under_kdir("work/f/worklog.md", self.kdir))

    def test_the_fs_resolver_never_answers_for_an_indexed_id(self):
        self.assertFalse(PC._cite_under_kdir("learn-one", self.kdir))
        self.assertTrue(PC._cite_in_index("learn-one", self.kdir))

    def test_an_indexed_entry_resolves_even_when_its_file_is_gone(self):
        # The two resolvers are INDEPENDENT, not a fallback ladder.
        os.remove(os.path.join(self.kdir, "learnings", "one.md"))
        self.assertTrue(PC._cite_in_index("learn-one", self.kdir))
        self.assertTrue(PC._premise_cite_resolves("learn-one", self.kdir))

    def test_containment_is_checked_before_existence(self):
        # /etc/passwd EXISTS on this machine; the point is that it is outside
        # the KB, so the answer must be False for a reason that has nothing to
        # do with existence. Asserted against a target proven to exist, or the
        # test passes for the wrong reason.
        outside = _write(os.path.join(self.root, "outside.md"), "x\n")
        self.assertTrue(os.path.exists(outside))
        rel = os.path.relpath(outside, self.kdir)
        self.assertTrue(rel.startswith(".."), rel)
        self.assertFalse(PC._cite_under_kdir(rel, self.kdir))
        self.assertFalse(PC._premise_cite_resolves(rel, self.kdir))

    def test_the_knowledge_dir_itself_resolves(self):
        self.assertTrue(PC._cite_under_kdir(".", self.kdir))

    def test_a_path_that_cannot_be_resolved_is_false_not_a_raise(self):
        # NO on-disk input reaches this except arm, and that is worth stating
        # rather than working around: an embedded NUL does NOT raise here on
        # CPython/macOS (abspath keeps the byte and os.path.exists swallows the
        # ValueError to False), and an escaping path is refused by the
        # containment check one line above. So the guard is reached only by
        # substituting the resolver — which is exactly what it guards, since
        # the gate runs inside the loop pre-hook and a raise would abort the
        # run rather than refuse the citation.
        self.assertFalse(PC._cite_under_kdir("a\x00b", self.kdir))  # no raise
        saved = PC.os.path.abspath

        def _raises(path):
            raise ValueError("embedded null byte")

        PC.os.path.abspath = _raises
        try:
            self.assertFalse(PC._cite_under_kdir("x", self.kdir))
            self.assertFalse(PC._premise_cite_resolves("MAIN.md", self.kdir))
        finally:
            PC.os.path.abspath = saved

    def test_a_corrupt_index_degrades_to_the_filesystem_and_writes_nothing(self):
        d = os.path.join(self.root, "corrupt")
        _write(os.path.join(d, "MAIN.md"), "# MAIN\n")
        idx = _write(os.path.join(d, "index.json"), "{ not json at all")
        before = os.stat(idx).st_size
        self.assertFalse(PC._cite_in_index("learn-one", d))
        self.assertTrue(PC._premise_cite_resolves("MAIN.md", d))
        # INV-2: _allow_rebuild=False — the read-only gate must never trigger
        # the loader's transparent rebuild, which writes index.json.
        self.assertEqual(os.stat(idx).st_size, before)

    def test_an_index_row_that_is_not_a_dict_does_not_raise(self):
        _write(os.path.join(self.kdir, "index.json"), json.dumps(
            {"entries": ["not-a-dict", {"id": "learn-one"}]}))
        self.assertTrue(PC._cite_in_index("learn-one", self.kdir))

    def test_a_loader_that_raises_is_swallowed(self):
        saved = PC.load_index

        def _boom(*a, **kw):
            raise RuntimeError("index loader exploded")

        PC.load_index = _boom
        try:
            self.assertFalse(PC._cite_in_index("learn-one", self.kdir))
            # …and the disjunction still reaches resolver 2.
            self.assertTrue(PC._premise_cite_resolves("MAIN.md", self.kdir))
        finally:
            PC.load_index = saved

    def test_the_empty_guard_covers_both_operands(self):
        for cite, kdir in (("", self.kdir), (None, self.kdir),
                           ("learn-one", None), ("learn-one", "")):
            self.assertFalse(PC._premise_cite_resolves(cite, kdir),
                             (cite, kdir))

    def test_the_disjunction_is_an_or_not_an_and(self):
        # Each resolver alone must be sufficient. Driven with inputs only ONE of
        # them can explain, or the test passes with the `or` turned into `and`.
        self.assertTrue(PC._premise_cite_resolves("learn-one", self.kdir))
        self.assertTrue(PC._premise_cite_resolves("work/f/worklog.md",
                                                  self.kdir))


# --- the path evaluator ------------------------------------------------------

class PathVerdictTest(unittest.TestCase):

    def test_it_returns_the_detail_that_matches_the_side_it_took(self):
        self.assertEqual(PC._path_verdict(True, "yes", "no"),
                         {"verdict": "grounded", "detail": "yes"})
        self.assertEqual(PC._path_verdict(False, "yes", "no"),
                         {"verdict": "falsified", "detail": "no"})

    def test_it_never_returns_uncheckable(self):
        for ok in (True, False):
            self.assertIn(PC._path_verdict(ok, "a", "b")["verdict"],
                          ("grounded", "falsified"))


class EvalCheckPathTest(_KBCase):

    _UNSET = object()

    def _v(self, check, kdir=_UNSET):
        # A DISTINCT sentinel, not None: the arm under test IS `kdir=None`, so
        # a None default would make the no-knowledge-dir case unreachable from
        # this helper and the test would silently assert the opposite of what
        # it ran. (It did, until the first run caught it.)
        return PC._eval_check_path(
            PP._parse_premise_check(check),
            self.kdir if kdir is self._UNSET else kdir)

    def test_exists_and_absent_are_opposites_on_the_same_target(self):
        for rel, present in (("MAIN.md", True), ("ghost.md", False)):
            e = self._v("path exists %s" % rel)
            a = self._v("path absent %s" % rel)
            self.assertEqual(e["verdict"],
                             "grounded" if present else "falsified")
            self.assertEqual(a["verdict"],
                             "falsified" if present else "grounded")

    def test_the_falsifying_detail_is_shouted(self):
        # The operator reads this string when the loop halts, so the asymmetric
        # wording is load-bearing rather than cosmetic.
        self.assertIn("is ABSENT", self._v("path exists ghost.md")["detail"])
        self.assertIn("EXISTS", self._v("path absent MAIN.md")["detail"])

    def test_a_directory_counts_as_existing(self):
        self.assertEqual(self._v("path exists learnings")["verdict"],
                         "grounded")

    def test_a_path_escaping_the_kb_is_uncheckable_not_falsified(self):
        r = self._v("path exists ../../../etc/passwd")
        self.assertEqual(r["verdict"], "uncheckable")
        self.assertIn("escapes", r["detail"])

    def test_no_knowledge_dir_is_uncheckable(self):
        self.assertEqual(self._v("path exists MAIN.md", kdir=None)["verdict"],
                         "uncheckable")

    @unittest.skipIf(os.geteuid() == 0, "root bypasses permission bits")
    def test_an_unreadable_ancestor_is_uncheckable_in_every_mode(self):
        # THE row this whole tri-state exists for. os.path.exists maps
        # PermissionError to False, which would report `path exists` as
        # FALSIFIED and HALT a loop run over a chmod.
        hidden = os.path.join(self.kdir, "hidden")
        _write(os.path.join(hidden, "t.md"), "x\n")
        os.chmod(hidden, 0)
        try:
            for check in ("path exists hidden/t.md",
                          "path absent hidden/t.md",
                          "path mtime-before 2099-01-01 hidden/t.md"):
                r = self._v(check)
                self.assertEqual(r["verdict"], "uncheckable", check)
        finally:
            os.chmod(hidden, 0o755)

    def test_mtime_before_compares_against_the_author_supplied_bound(self):
        far = self._v("path mtime-before 2099-01-01 MAIN.md")
        near = self._v("path mtime-before 1971-01-01 MAIN.md")
        self.assertEqual(far["verdict"], "grounded")
        self.assertEqual(near["verdict"], "falsified")
        self.assertIn("<", far["detail"])
        self.assertIn(">=", near["detail"])

    def test_the_three_mtime_uncheckable_arms_are_distinct(self):
        absent = self._v("path mtime-before 2099-01-01 ghost.md")
        badiso = self._v("path mtime-before not-a-date MAIN.md")
        self.assertEqual(absent["verdict"], "uncheckable")
        self.assertEqual(badiso["verdict"], "uncheckable")
        self.assertNotEqual(absent["detail"], badiso["detail"])
        self.assertIn("cannot compare mtime", absent["detail"])
        self.assertIn("unparseable ISO", badiso["detail"])

    def test_a_stat_that_fails_on_the_second_call_is_uncheckable(self):
        # The tri-state probe succeeds, then the mtime read fails. No corpus can
        # produce this (nothing changes between the two calls), so the only way
        # in is to make the second call raise — which is exactly why the guard
        # is there.
        saved = PC.os.stat
        calls = {"n": 0}

        def _second_raises(path, *a, **kw):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise OSError(5, "input/output error")
            return saved(path, *a, **kw)

        PC.os.stat = _second_raises
        try:
            r = self._v("path mtime-before 2099-01-01 MAIN.md")
        finally:
            PC.os.stat = saved
        self.assertEqual(r["verdict"], "uncheckable")
        self.assertIn("stat failed", r["detail"])

    def test_the_mtime_argument_order_differs_from_the_other_modes(self):
        # `mtime-before <ISO> <rel>`, but `exists <rel>`. Swapping them silently
        # stats the ISO string, which is absent, and reports uncheckable — a
        # verdict that looks reasonable and is wrong.
        p = PP._parse_premise_check("path mtime-before 2099-01-01 MAIN.md")
        self.assertEqual(p["args"], ["mtime-before", "2099-01-01", "MAIN.md"])
        self.assertEqual(PC._eval_check_path(p, self.kdir)["verdict"],
                         "grounded")


class PremisePathStateTest(_KBCase):

    def test_the_three_states(self):
        self.assertEqual(
            PC._premise_path_state(os.path.join(self.kdir, "MAIN.md")),
            "exists")
        self.assertEqual(
            PC._premise_path_state(os.path.join(self.kdir, "ghost.md")),
            "absent")
        # A path THROUGH a regular file is NotADirectoryError -> absent, not an
        # error: the target genuinely is not there.
        self.assertEqual(
            PC._premise_path_state(os.path.join(self.kdir, "MAIN.md", "x")),
            "absent")

    @unittest.skipIf(os.geteuid() == 0, "root bypasses permission bits")
    def test_a_permission_error_is_error_and_not_absent(self):
        hidden = os.path.join(self.kdir, "hidden")
        _write(os.path.join(hidden, "t.md"), "x\n")
        os.chmod(hidden, 0)
        try:
            self.assertEqual(
                PC._premise_path_state(os.path.join(hidden, "t.md")), "error")
        finally:
            os.chmod(hidden, 0o755)


class ParseIsoToEpochTest(unittest.TestCase):

    def test_the_three_accepted_formats_and_the_z_suffix(self):
        self.assertEqual(PC._parse_iso_to_epoch("1970-01-01"), 0)
        self.assertEqual(PC._parse_iso_to_epoch("1970-01-01T00:00:00"), 0)
        self.assertEqual(PC._parse_iso_to_epoch("1970-01-01T00:00"), 0)
        self.assertEqual(PC._parse_iso_to_epoch("1970-01-01T00:00:00Z"), 0)
        self.assertEqual(PC._parse_iso_to_epoch("  1970-01-01  "), 0)

    def test_it_is_utc_and_not_local(self):
        # timegm, never mktime: a gate whose verdict moved with the machine's
        # timezone would not be deterministic (INV-1).
        self.assertEqual(PC._parse_iso_to_epoch("2026-01-02T03:04:05"),
                         1767323045)

    def test_unparseable_input_is_none(self):
        for bad in ("not-a-date", "", None, "2026-13-45",
                    "2026-01-01T00:00:00+00:00"):
            self.assertIsNone(PC._parse_iso_to_epoch(bad), bad)


