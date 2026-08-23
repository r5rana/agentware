"""Owning unit file for `agentware_lib.ops.planpremises` (task 99 wave 14).

This module is the TRUST BOUNDARY of the R-GND-01 premise surface: untrusted plan
text in, a closed-vocabulary record out, nothing executable ever constructed. It
had coverage before this file existed and none of it was OWNERSHIP —
`tests/unit/ops/test_premise_gate.py` is named for the FEATURE and drives the
parser and the evaluators together, so it does not redden when this module alone
breaks.

What is asserted here that the feature file does not reach:

  * THE BLOCK LOCATOR's candidate ordering. `_premise_region` collects every
    `## Premises` SECTION then every ```premises FENCE, and returns the first
    span that actually CONTAINS a row — so an EMPTY heading above a populated
    fence does not silently suppress it, while a populated section still wins.
    Three plans are needed to separate those claims and they are all here.
  * FIRST-WINS, per FIELD and INDEPENDENTLY. A second `cite:` is ignored without
    affecting whether a later `check:` is absorbed.
  * NO RECORD LEAVES UN-PARSED. `plan_premises` has two flush points and they
    were copy-pasted; `_premise_flush` is now the only site that parses, and the
    invariant is asserted over every plan shape rather than the two the feature
    file happened to use.
  * THE FAIL-OPEN / FAIL-CLOSED SPLIT of `verify_premises`, which is what
    decides whether the loop pre-hook halts a run.

Stdlib `unittest` only; every KB is a throwaway tempdir (R-LOC-03).

Runner:  python3 -m unittest tests.unit.ops.test_planpremises -v
"""

from tests._paths import TESTS_DIR

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

import agentware_lib.ops.planpremises as PP                    # noqa: E402


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# --- the block locator -------------------------------------------------------

class PremiseRegionTest(unittest.TestCase):

    def test_no_block_at_all_is_none(self):
        for text in ("", None, "# Plan\n\nprose\n"):
            self.assertIsNone(PP._premise_region(text), repr(text))

    def test_a_section_ends_at_the_next_h2(self):
        text = ("## Premises\n"
                "- premise: p\n"
                "  check: cite-live\n"
                "## Next\n"
                "- premise: NOT mine\n")
        start, end = PP._premise_region(text)
        self.assertEqual((start, end), (1, 3))
        self.assertEqual(len(PP.plan_premises(text)), 1)

    def test_a_section_with_no_following_h2_runs_to_end_of_file(self):
        text = "## Premises\n- premise: p\n  check: cite-live\n"
        self.assertEqual(PP._premise_region(text), (1, 3))

    def test_a_populated_section_wins_over_a_populated_fence(self):
        text = ("## Premises\n- premise: from the section\n"
                "  check: cite-live\n\n"
                "## Detail\n```premises\n- premise: from the fence\n"
                "  check: cite-live\n```\n")
        recs = PP.plan_premises(text)
        self.assertEqual([r["premise"] for r in recs], ["from the section"])

    def test_an_empty_section_does_not_suppress_a_populated_fence(self):
        # The reason the locator collects CANDIDATES before choosing, instead of
        # returning the first span it finds. Without this a plan that keeps its
        # premises in a fence under an empty heading verifies NOTHING and
        # reports PASSED.
        text = ("## Premises\n\n(nothing yet)\n\n"
                "## Detail\n```premises\n- premise: from the fence\n"
                "  check: cite-live\n```\n")
        recs = PP.plan_premises(text)
        self.assertEqual([r["premise"] for r in recs], ["from the fence"])

    def test_an_empty_section_with_no_fence_still_returns_a_span(self):
        # R14 must still be able to flag an EMPTY premises block, so the
        # fall-through returns the first candidate rather than None.
        text = "## Premises\n\n(none)\n\n## Next\n"
        self.assertIsNotNone(PP._premise_region(text))
        self.assertEqual(PP.plan_premises(text), [])

    def test_an_empty_first_section_loses_to_a_populated_second_one(self):
        text = ("## Premises\n\n(empty)\n\n## Other\n\n"
                "## Premises\n- premise: the populated one\n"
                "  check: cite-live\n")
        recs = PP.plan_premises(text)
        self.assertEqual([r["premise"] for r in recs], ["the populated one"])

    def test_an_unterminated_fence_runs_to_end_of_file(self):
        text = "```premises\n- premise: p\n  check: cite-live\n"
        self.assertEqual(len(PP.plan_premises(text)), 1)

    def test_the_heading_and_the_rows_are_case_insensitive(self):
        text = "## PREMISES\n- PREMISE: shouty\n  CITE: x\n  CHECK: cite-live\n"
        recs = PP.plan_premises(text)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["cite"], "x")
        self.assertEqual(recs[0]["check"], "cite-live")


# --- the record parser -------------------------------------------------------

class PlanPremisesTest(unittest.TestCase):

    def _one(self, rows):
        return PP.plan_premises("## Premises\n" + rows)

    def test_every_record_carries_a_parsed_key(self):
        # THE invariant `_premise_flush` exists to hold in one place: both flush
        # points (a new `- premise:` row, and end-of-block) parse, and a record
        # that reached `records` un-parsed would make every downstream consumer
        # take a different arm.
        recs = self._one("- premise: a\n  check: cite-live\n"
                         "- premise: b\n  check: path exists x\n"
                         "- premise: c\n")
        self.assertEqual(len(recs), 3)
        for r in recs:
            self.assertIn("parsed", r)
            self.assertIn("kind", r["parsed"])

    def test_the_last_record_is_flushed_at_end_of_block(self):
        # The second flush point. A test with two or more premises passes with
        # it deleted only if the FIRST is also checked, so both counts matter.
        self.assertEqual(len(self._one("- premise: only\n  check: cite-live\n")),
                         1)
        self.assertEqual(len(self._one("- premise: a\n- premise: b\n")), 2)

    def test_a_premise_with_no_check_parses_to_malformed_not_to_none(self):
        r = self._one("- premise: no check row\n")[0]
        self.assertIsNone(r["check"])
        self.assertEqual(r["parsed"]["kind"], "__malformed__")

    def test_cite_first_wins_and_does_not_block_a_later_check(self):
        # The two fields are independent. A shared "already absorbed" flag would
        # pass a test that only asserted the cite.
        r = self._one("- premise: p\n"
                      "  cite: first\n"
                      "  cite: second\n"
                      "  check: cite-live\n")[0]
        self.assertEqual(r["cite"], "first")
        self.assertEqual(r["check"], "cite-live")

    def test_check_first_wins_and_does_not_block_a_later_cite(self):
        r = self._one("- premise: p\n"
                      "  check: cite-live\n"
                      "  check: path exists x\n"
                      "  cite: c\n")[0]
        self.assertEqual(r["check"], "cite-live")
        self.assertEqual(r["cite"], "c")

    def test_the_fields_may_appear_in_either_order(self):
        a = self._one("- premise: p\n  cite: c\n  check: cite-live\n")[0]
        b = self._one("- premise: p\n  check: cite-live\n  cite: c\n")[0]
        self.assertEqual((a["cite"], a["check"]), (b["cite"], b["check"]))

    def test_a_field_row_before_the_first_premise_is_discarded(self):
        # An orphan `cite:` above the block must not attach itself to the first
        # premise below it.
        r = PP.plan_premises("## Premises\n"
                             "  cite: orphan\n"
                             "- premise: p\n"
                             "  check: cite-live\n")[0]
        self.assertIsNone(r["cite"])

    def test_the_line_number_is_one_based_and_points_at_the_premise_row(self):
        text = "# Plan\n\n## Premises\n- premise: p\n  check: cite-live\n"
        self.assertEqual(PP.plan_premises(text)[0]["line"], 4)
        self.assertEqual(text.splitlines()[3], "- premise: p")

    def test_a_blank_line_does_not_close_a_record(self):
        r = self._one("- premise: p\n\n  check: cite-live\n")[0]
        self.assertEqual(r["check"], "cite-live")

    def test_the_absorb_helper_mutates_in_place_and_returns_nothing(self):
        cur = {"cite": None, "check": None}
        self.assertIsNone(PP._premise_absorb_row(cur, "  cite: c"))
        self.assertEqual(cur["cite"], "c")
        PP._premise_absorb_row(cur, "  check: cite-live")
        self.assertEqual(cur["check"], "cite-live")
        PP._premise_absorb_row(cur, "not a field row at all")
        self.assertEqual((cur["cite"], cur["check"]), ("c", "cite-live"))

    def test_the_flush_helper_is_the_only_site_that_parses(self):
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(PP))
        sites = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_parse_premise_check"):
                sites.append(node.lineno)
        # ONE call site inside this module's own code path. Two would mean the
        # copy-paste `_premise_flush` was extracted to remove has come back.
        self.assertEqual(len(sites), 1, sites)


# --- the closed grammar ------------------------------------------------------

class ParsePremiseCheckTest(unittest.TestCase):

    def test_each_kind_of_the_closed_set_parses(self):
        for check, kind in (("cite-live", "cite-live"),
                            ("config-eq K V", "config-eq"),
                            ("path exists a", "path"),
                            ("path absent a", "path"),
                            ("path mtime-before 2026-01-01 a", "path"),
                            ("corpus-metric claude-error-records >= 1",
                             "corpus-metric")):
            self.assertEqual(PP._parse_premise_check(check)["kind"], kind,
                             check)

    def test_a_shell_metacharacter_is_rejected_at_parse(self):
        # SECURITY, defence in depth: nothing a plan declares is ever
        # shell-executed, but such a check is never even STORED as runnable.
        for bad in ("path exists a; ls", "path exists a && ls",
                    "path exists a | ls", "path exists $(ls)",
                    "path exists `ls`", "path exists a\nls"):
            p = PP._parse_premise_check(bad)
            self.assertEqual(p["kind"], "__rejected__", bad)
            self.assertIn("never run", p["error"])

    def test_the_numeric_operators_are_not_metacharacters(self):
        # `<` and `>` are legal corpus-metric operators and are inert in a
        # grammar nothing shell-executes. A metachar set that swallowed them
        # would reject half the comparator table.
        for op in (">=", "<=", ">", "<", "==", "!="):
            p = PP._parse_premise_check(
                "corpus-metric claude-error-records %s 1" % op)
            self.assertEqual(p["kind"], "corpus-metric", op)

    def test_a_wrong_arity_is_refused_even_when_every_domain_is_legal(self):
        # N guards can each reject the same input, so a fixture naming ONE of
        # them must be given an input the other N-1 cannot explain. Both rows
        # below carry a LEGAL metric and a LEGAL operator, so only the arity
        # check can refuse them.
        for bad in ("corpus-metric claude-error-records >= 1 extra",
                    "corpus-metric claude-error-records >="):
            p = PP._parse_premise_check(bad)
            self.assertEqual(p["kind"], "__malformed__", bad)
            self.assertIn("requires", p["error"], bad)

    def test_a_wrong_path_arity_is_refused_with_a_legal_mode(self):
        for bad in ("path exists a b", "path exists",
                    "path mtime-before 2026-01-01",
                    "path mtime-before 2026-01-01 a b"):
            self.assertEqual(PP._parse_premise_check(bad)["kind"],
                             "__malformed__", bad)

    def test_arity_and_domain_errors_are_malformed_not_unknown(self):
        for bad in ("config-eq K", "config-eq K V W", "path",
                    "path bogus a", "path mtime-before a",
                    "corpus-metric bogus >= 1",
                    "corpus-metric claude-error-records ~ 1",
                    "corpus-metric claude-error-records >= x", ""):
            self.assertEqual(PP._parse_premise_check(bad)["kind"],
                             "__malformed__", bad)

    def test_an_unrecognised_leading_token_is_unknown(self):
        p = PP._parse_premise_check("teleport the KB")
        self.assertEqual(p["kind"], "__unknown__")
        self.assertIn("closed grammar", p["error"])

    def test_nothing_outside_the_declared_kind_set_ever_parses_to_a_kind(self):
        for check in ("cite-live", "config-eq K V", "path exists a",
                      "corpus-metric claude-error-records >= 1",
                      "teleport", "", "path exists a; ls"):
            kind = PP._parse_premise_check(check)["kind"]
            self.assertIn(kind, PP._PREMISE_CHECK_KINDS
                          + ("__rejected__", "__unknown__", "__malformed__"),
                          check)


# --- the load-bearing predicates --------------------------------------------

class LoadBearingTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw_planpremises_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.kdir = os.path.join(self.root, "kb")
        _write(os.path.join(self.kdir, "MAIN.md"), "# MAIN\n")
        _write(os.path.join(self.kdir, "index.json"), json.dumps(
            {"entries": [{"id": "learn-a-long-live-id",
                          "path": "learnings/a.md"},
                         {"id": "short", "path": "learnings/b.md"}]}))

    def test_the_type_header_and_the_warning_heading_both_trigger(self):
        self.assertTrue(PP._plan_is_self_extension(
            "> Type: **self-extension (package change)**\n"))
        self.assertTrue(PP._plan_is_self_extension(
            "## ‼️ Self-extension warning (read before executing)\n"))
        self.assertFalse(PP._plan_is_self_extension("> Type: feature\n"))

    def test_a_live_kb_id_makes_a_plain_plan_load_bearing(self):
        self.assertTrue(PP._plan_names_live_kb_id(
            "prose citing learn-a-long-live-id inline", self.kdir))
        self.assertTrue(PP._plan_is_load_bearing(
            "> Type: feature\n\nlearn-a-long-live-id\n", self.kdir))
        self.assertFalse(PP._plan_is_load_bearing("> Type: feature\n",
                                                  self.kdir))

    def test_a_short_id_is_not_a_reliable_signal_and_is_skipped(self):
        # Ids under 8 chars are skipped on purpose: a substring match on a short
        # token would make almost every plan load-bearing.
        self.assertFalse(PP._plan_names_live_kb_id("short", self.kdir))

    def test_no_kdir_and_an_unloadable_index_are_both_false(self):
        self.assertFalse(PP._plan_names_live_kb_id("learn-a-long-live-id",
                                                   None))
        d = os.path.join(self.root, "corrupt")
        _write(os.path.join(d, "index.json"), "{ not json")
        self.assertFalse(PP._plan_names_live_kb_id("learn-a-long-live-id", d))

    def test_the_live_id_scan_never_rebuilds_the_index(self):
        # INV-2: a LINT must not write. `load_index` rebuilds a corrupt index
        # transparently unless _allow_rebuild=False, and a rebuild writes
        # index.json, FEATURES.md, the rosters and the BM25 cache. Asserted as a
        # read-after-call on the bytes, because a False return is identical
        # either way and hides the write completely.
        d = os.path.join(self.root, "norebuild")
        _write(os.path.join(d, "MAIN.md"), "# MAIN\n")
        idx = _write(os.path.join(d, "index.json"), "{ not json")
        with open(idx, encoding="utf-8") as f:
            before = f.read()
        self.assertFalse(PP._plan_names_live_kb_id("learn-a-long-live-id", d))
        with open(idx, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)
        self.assertFalse(os.path.exists(os.path.join(d, "FEATURES.md")))

    def test_self_extension_short_circuits_before_the_index_read(self):
        # `_plan_is_load_bearing` must not need a kdir to answer for a
        # self-extension plan; the loop pre-hook calls it before resolving one.
        self.assertTrue(PP._plan_is_load_bearing("> Type: self-extension\n"))


# --- the verifier's exit policy ---------------------------------------------

class VerifyPremisesTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw_verifypremises_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.kdir = os.path.join(self.root, "kb")
        _write(os.path.join(self.kdir, "MAIN.md"), "# MAIN\n")
        _write(os.path.join(self.kdir, "index.json"), json.dumps(
            {"entries": [{"id": "learn-one", "path": "learnings/one.md"}]}))

    def _plan(self, header, rows):
        return header + "\n## Premises\n" + rows

    def test_a_reproduced_falsification_is_not_ok_regardless_of_strict(self):
        text = self._plan("> Type: feature\n",
                          "- premise: p\n  check: path exists ghost.md\n")
        for strict in (False, True):
            r = PP.verify_premises(text, self.kdir, strict=strict)
            self.assertFalse(r["ok"], strict)
            self.assertTrue(r["falsified"], strict)

    def test_an_uncheckable_premise_warns_and_still_passes(self):
        # D5 fail-OPEN. A drifted check must never DoS the loop.
        text = self._plan("> Type: feature\n",
                          "- premise: p\n  check: teleport the KB\n")
        for strict in (False, True):
            r = PP.verify_premises(text, self.kdir, strict=strict)
            self.assertTrue(r["ok"], strict)
            self.assertEqual(r["premises"][0]["verdict"], "uncheckable")

    def test_an_unresolvable_cite_fails_closed_only_on_a_self_ext_plan_under_strict(self):
        rows = "- premise: p\n  cite: learn-nope\n  check: cite-live\n"
        selfext = self._plan("> Type: **self-extension**\n", rows)
        plain = self._plan("> Type: feature\n", rows)
        # All four corners, because the conjunction has two operands and a test
        # that drove one corner would pass with either deleted.
        self.assertTrue(PP.verify_premises(selfext, self.kdir)["ok"])
        self.assertFalse(
            PP.verify_premises(selfext, self.kdir, strict=True)["ok"])
        self.assertTrue(PP.verify_premises(plain, self.kdir)["ok"])
        self.assertTrue(
            PP.verify_premises(plain, self.kdir, strict=True)["ok"])

    def test_the_fail_closed_flag_is_stamped_on_the_offending_item_only(self):
        text = self._plan(
            "> Type: **self-extension**\n",
            "- premise: resolves\n  cite: learn-one\n  check: cite-live\n"
            "- premise: does not\n  cite: learn-nope\n  check: cite-live\n")
        r = PP.verify_premises(text, self.kdir, strict=True)
        self.assertFalse(r["ok"])
        self.assertTrue(r["fail_closed_unresolved_cite"])
        self.assertNotIn("fail_closed", r["premises"][0])
        self.assertTrue(r["premises"][1]["fail_closed"])

    def test_a_plan_with_no_premises_block_passes_with_an_empty_list(self):
        r = PP.verify_premises("> Type: feature\n\n# Plan\n", self.kdir)
        self.assertTrue(r["ok"])
        self.assertEqual(r["premises"], [])
        self.assertFalse(r["falsified"])

    def test_every_result_item_carries_the_source_row_it_came_from(self):
        text = self._plan(
            "> Type: feature\n",
            "- premise: the claim\n  cite: learn-one\n  check: cite-live\n")
        item = PP.verify_premises(text, self.kdir)["premises"][0]
        self.assertEqual(item["premise"], "the claim")
        self.assertEqual(item["cite"], "learn-one")
        self.assertEqual(item["check"], "cite-live")
        self.assertEqual(item["verdict"], "grounded")

    def test_the_optional_evaluator_keys_are_forwarded_when_present(self):
        text = self._plan(
            "> Type: feature\n",
            "- premise: p\n"
            "  check: corpus-metric claude-error-records >= 1\n")
        _write(os.path.join(self.kdir, "logs", "sessions", "s", "live.jsonl"),
               json.dumps({"status": "ERR"}) + "\n")
        item = PP.verify_premises(text, self.kdir)["premises"][0]
        self.assertEqual(item["actual"], 1)
        self.assertEqual(item["op"], ">=")
        self.assertEqual(item["expected"], 1)

    def test_a_rejected_check_is_reported_as_rejected(self):
        text = self._plan("> Type: feature\n",
                          "- premise: p\n  check: path exists x; rm -rf /\n")
        r = PP.verify_premises(text, self.kdir)
        self.assertTrue(r["ok"])
        self.assertTrue(r["premises"][0]["rejected"])


class CmdPlanVerifyPremisesTest(unittest.TestCase):
    """The handler's own arms — the exit code is what the loop pre-hook reads."""

    class _Args(object):
        def __init__(self, path, strict=False, format="text"):
            self.path = path
            self.strict = strict
            self.format = format

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw_verifycmd_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _capture(self, args):
        import io
        saved = sys.stderr
        sys.stderr = io.StringIO()
        try:
            rc = PP.cmd_plan_verify_premises(args)
            return rc, sys.stderr.getvalue()
        finally:
            sys.stderr = saved

    def test_a_missing_plan_exits_one(self):
        rc, err = self._capture(
            self._Args(os.path.join(self.root, "nope.md")))
        self.assertEqual(rc, 1)
        self.assertIn("plan not found", err)

    def test_a_directory_takes_the_not_found_arm_rather_than_raising(self):
        # The exit CODE cannot separate the two arms: `open()` on a directory
        # raises IsADirectoryError, which is an OSError the read arm already
        # catches, so `isfile` -> `exists` still exits 1. The MESSAGE is what
        # says which arm ran, and "cannot read plan" for a directory is a
        # diagnosis that sends the reader looking for a permission problem.
        rc, err = self._capture(self._Args(self.root))
        self.assertEqual(rc, 1)
        self.assertIn("plan not found", err)
        self.assertNotIn("cannot read plan", err)

    @unittest.skipIf(os.geteuid() == 0, "root bypasses permission bits")
    def test_an_unreadable_plan_exits_one_without_raising(self):
        p = _write(os.path.join(self.root, "locked.md"), "# Plan\n")
        os.chmod(p, 0)
        try:
            rc, err = self._capture(self._Args(p))
        finally:
            os.chmod(p, 0o644)
        self.assertEqual(rc, 1)
        self.assertIn("cannot read plan", err)


class ModuleBoundaryTest(unittest.TestCase):

    def test_the_ramp_threshold_is_above_the_current_schema(self):
        # R14 stays a WARN under the current KB schema so the strict pre-hook
        # cannot retroactively hard-fail an existing self-extension plan.
        self.assertEqual(PP._PLAN_PREMISES_ERROR_SCHEMA, 2)

    def test_the_new_helpers_are_not_added_to_the_facade_export_list(self):
        # Wave 14's helpers have no monolith counterpart, so exporting them
        # would widen the facade surface the parity machinery pins.
        for new in ("_premise_flush", "_premise_absorb_row"):
            self.assertTrue(hasattr(PP, new), new)
            self.assertNotIn(new, PP._EXPORTS)
        for name in PP._EXPORTS:
            self.assertTrue(hasattr(PP, name), name)


if __name__ == "__main__":
    unittest.main()
