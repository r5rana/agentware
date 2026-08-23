"""The DSF steering linter's own unit file — task 99 wave 12.

`agentware_lib.ops.steering` had no owning unit file. Its coverage was
`tests/gates/test_steering_content.py`, which runs the linter over the LIVE repo
corpus and asserts the repo is clean — the right gate for "has the shipped
steering drifted" and the wrong test for "what does the linter do", because a
healthy corpus reaches exactly one path through every branch: the silent one.
Every violation arm, the char budget, the DSF-read degradation and two of the
three CP rules had no assertion anywhere.

That matters more here than for most modules: `lint_steering` is what R-PKG-04
runs after every confirmed package edit and what Gate 1 of the release gate
consumes, so a linter that silently stopped reporting would turn two governance
gates into theatre while both stayed green.

Wave 12 split it into `_dsf_scan` (the corpus walk), `_dsf_scan_rule_line` (the
three per-bullet rules) and `_content_preservation_errors` (CP-1/2/3). The split
is what makes a synthetic corpus testable at all: REPO_ROOT and STEERING_GLOB are
module globals read at CALL time, so a scratch corpus is a two-attribute
substitution rather than a subprocess.

The properties that matter, and why each is a way the split can be wrong:

  ONE READ   — `_dsf_scan` hands its file texts to the CP half rather than
               letting it re-read AGENTS.md. Two reads of a corpus that changed
               in between is a report about no single state of the tree.
  PER-RULE   — one bullet can be DSF-2 and DSF-6 and several DSF-5s at once.
               They are fixed by different edits, so they are reported
               separately.
  IDS        — `all_rule_ids` accumulates across FILES, which is what makes CP-1
               a claim about the corpus and not about one file.
  TOLERANT   — an unreadable file is a DSF-read violation and never aborts the
               walk; the corpus is always-loaded steering, so a half-linted
               report that claims to be clean is the dangerous outcome.
  SEVERITY   — content errors decide `ok` only under --strict, and the payload
               carries them either way.

Hermetic: scratch corpora in a tempdir with REPO_ROOT/STEERING_GLOB substituted
and restored; stdlib unittest only.
"""

from tests._paths import TESTS_DIR

import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.ops.steering as steering                      # noqa: E402
from agentware_lib.ops.steering import (                           # noqa: E402
    DSF_BANNED_WORDS,
    RULE_COUNT_REGRESSION_MARGIN,
    STEERING_CHAR_BUDGET,
    _content_preservation_errors,
    _dsf_scan,
    _dsf_scan_rule_line,
    _first_token_verb,
    _iter_rule_lines,
    _read_rule_count_baseline,
    _steering_files,
    lint_steering,
)

#: An AGENTS.md carrying every REQUIRED_SECTION heading, so CP-2 is silent and a
#: test about CP-1 or CP-3 is not reading seventeen unrelated CP-2 rows.
ALL_SECTIONS = "# AGENTS\n\n" + "\n\n".join(
    "## %s\n\nprose" % s for s in steering.REQUIRED_SECTIONS) + "\n"

#: Every protected rule id, one per bullet, so CP-1 is silent too.
ALL_REQUIRED_IDS = "\n".join(
    "- MUST hold the line. [%s]" % rid for rid in steering.REQUIRED_RULE_IDS)

CLEAN_AGENTS = ALL_SECTIONS + "\n" + ALL_REQUIRED_IDS + "\n"


class ScratchCorpusCase(unittest.TestCase):
    """Substitutes REPO_ROOT + STEERING_GLOB onto a scratch tree."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-w12-steer-")
        self.addCleanup(self._cleanup)
        os.makedirs(os.path.join(self.root, "steering"), exist_ok=True)
        self._saved = (steering.REPO_ROOT, steering.STEERING_GLOB)
        steering.REPO_ROOT = self.root
        steering.STEERING_GLOB = os.path.join(self.root, "steering", "*.md")

    def _cleanup(self):
        steering.REPO_ROOT, steering.STEERING_GLOB = self._saved
        for dirpath, dirnames, filenames in os.walk(self.root):
            for name in dirnames + filenames:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o644)
                except OSError:
                    pass
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def clean_corpus(self):
        self.write("AGENTS.md", CLEAN_AGENTS)
        self.write("CLAUDE.md", "# CLAUDE\n\n@AGENTS.md\n")


class RuleLineTest(unittest.TestCase):
    """Which markdown lines are RULES — the scanner's input filter."""

    def _bodies(self, text):
        return [body for _line, body in _iter_rule_lines(text)]

    def test_frontmatter_is_skipped_only_when_it_opens_line_one(self):
        self.assertEqual(
            self._bodies("---\n- MUST hide [R-A-01]\n---\n- MUST show [R-A-02]\n"),
            ["MUST show [R-A-02]"])
        self.assertEqual(
            self._bodies("\n---\n- MUST show [R-A-01]\n---\n"),
            ["MUST show [R-A-01]"])

    def test_fenced_code_is_never_linted(self):
        self.assertEqual(
            self._bodies("```\n- MUST hide [R-A-01]\n```\n- MUST show [R-A-02]\n"),
            ["MUST show [R-A-02]"])

    def test_an_UNTERMINATED_fence_swallows_the_rest_of_the_file(self):
        """Asserted because it is surprising and load-bearing: a stray fence in
        always-loaded steering silently disarms the linter for every line after
        it, and nothing else in the report says so."""
        self.assertEqual(self._bodies("```\n- MUST hide [R-A-01]\n"), [])

    def test_only_bullets_are_rules(self):
        text = ("1. numbered [R-A-01]\n> quoted [R-A-02]\n| table | [R-A-03] |\n"
                "## heading [R-A-04]\nprose [R-A-05]\n* starred [R-A-06]\n"
                "  - indented [R-A-07]\n")
        self.assertEqual(self._bodies(text),
                         ["starred [R-A-06]", "indented [R-A-07]"])

    def test_a_bullet_with_an_EMPTY_body_is_not_a_rule(self):
        self.assertEqual(self._bodies("-\n-   \n- MUST x [R-A-01]\n"),
                         ["MUST x [R-A-01]"])

    def test_the_line_number_is_1_based_and_counts_skipped_lines(self):
        pairs = list(_iter_rule_lines("prose\n\n- MUST x [R-A-01]\n"))
        self.assertEqual(pairs, [(3, "MUST x [R-A-01]")])


class FirstTokenVerbTest(unittest.TestCase):
    """DSF-2's verb extraction — markup must not change the verdict."""

    def test_markup_and_trailing_punctuation_are_stripped(self):
        for body, verb in (("MUST do it", "MUST"),
                           ("**NEVER** do it", "NEVER"),
                           ("`ALWAYS` do it", "ALWAYS"),
                           ("_RUN_ the thing", "RUN"),
                           ("IF: a THEN b", "IF"),
                           ("STOP. now", "STOP"),
                           ("READ, then act", "READ")):
            self.assertEqual(_first_token_verb(body), verb, body)

    def test_a_lowercase_verb_is_NOT_normalised(self):
        """DSF-2 is a claim about the SHOUTED imperative; folding case here
        would silently accept `must do it`, and the format's whole point is that
        the directive is unmissable in an always-loaded file."""
        self.assertEqual(_first_token_verb("must do it"), "must")


class DsfScanTest(ScratchCorpusCase):
    """The per-bullet rules and the corpus walk."""

    def test_one_bullet_can_violate_all_three_rules_at_once(self):
        violations, banned, ids = [], [], set()
        _dsf_scan_rule_line("f.md", 7, "should try to consider it", violations,
                            banned, ids)
        self.assertEqual([v["rule"] for v in violations], ["DSF-2", "DSF-6"])
        self.assertEqual(sorted(b["word"] for b in banned),
                         ["consider", "should", "try"])
        self.assertEqual(ids, set())
        self.assertEqual({v["line"] for v in violations}, {7})

    def test_a_banned_word_must_be_a_WHOLE_word(self):
        """`\\b` is what stops `preference`, `trying` and `shouldering` from
        being reported. Without it the linter fails on ordinary prose and gets
        turned off, which is the real failure mode of a noisy gate."""
        violations, banned, ids = [], [], set()
        for body in ("MUST honour a preference [R-A-01]",
                     "MUST keep retrying [R-A-02]",
                     "MUST consider" .replace("consider", "considerate")
                     + " tone [R-A-03]"):
            _dsf_scan_rule_line("f.md", 1, body, violations, banned, ids)
        self.assertEqual(banned, [])
        self.assertIn("prefer", DSF_BANNED_WORDS)

    def test_a_multi_word_banned_phrase_is_matched(self):
        violations, banned, ids = [], [], set()
        _dsf_scan_rule_line("f.md", 1, "MUST do it when possible [R-A-01]",
                            violations, banned, ids)
        self.assertEqual([b["word"] for b in banned], ["when possible"])

    def test_a_SHOUTED_hedge_word_is_still_a_hedge_word(self):
        """The vocabulary is lower-case, so the body is folded before matching.
        Steering is written in shouted imperatives, which is exactly the text a
        case-sensitive scan would miss."""
        violations, banned, ids = [], [], set()
        _dsf_scan_rule_line("f.md", 1, "MUST NOT PREFER shouting [R-A-01]",
                            violations, banned, ids)
        self.assertEqual([b["word"] for b in banned], ["prefer"])

    def test_every_rule_id_on_a_line_is_collected(self):
        violations, banned, ids = [], [], set()
        _dsf_scan_rule_line("f.md", 1, "MUST fold [R-A-01] and [R-B-02]",
                            violations, banned, ids)
        self.assertEqual(ids, {"R-A-01", "R-B-02"})
        self.assertEqual(violations, [])

    def test_rule_ids_accumulate_ACROSS_files(self):
        """CP-1 is a claim about the CORPUS. A per-file id set makes it a claim
        about each file, and then every protected id is 'missing' from most of
        them."""
        self.clean_corpus()
        self.write("steering/a.md", "- MUST a [R-ZZ-01]\n")
        self.write("steering/b.md", "- MUST b [R-ZZ-02]\n")
        _v, _b, _c, ids, texts = _dsf_scan()
        self.assertIn("R-ZZ-01", ids)
        self.assertIn("R-ZZ-02", ids)
        self.assertEqual(len(texts), 4)

    def test_an_unreadable_file_is_a_violation_and_the_walk_continues(self):
        self.clean_corpus()
        locked = self.write("steering/locked.md", "- MUST x [R-ZZ-01]\n")
        self.write("steering/zz-after.md", "- should hedge [R-ZZ-02]\n")
        os.chmod(locked, 0)
        try:
            violations, banned, chars, ids, texts = _dsf_scan()
        finally:
            os.chmod(locked, 0o644)
        self.assertIn("DSF-read", [v["rule"] for v in violations])
        self.assertEqual([b["word"] for b in banned], ["should"])
        self.assertNotIn(locked, texts)
        self.assertGreater(chars, 0)

    def test_the_file_ORDER_is_explicit_then_sorted_glob(self):
        """The report is read by humans and diffed by gates, so the order is
        part of it: CLAUDE.md, AGENTS.md, then steering/ alphabetically.

        The sort is asserted against an UNSORTED glob rather than against the
        filesystem's own order, because the filesystem's order is not a fact a
        test may rely on: on this machine `glob.glob` already returns these
        three names sorted, so dropping `sorted()` changed nothing and the
        first version of this test stayed green under exactly the mutation it
        was written to catch. Substituting the glob is what makes the sort
        load-bearing on every filesystem.
        """
        self.clean_corpus()
        names = ["zebra.md", "alpha.md", "middle.md"]
        for name in names:
            self.write("steering/" + name, "- MUST x [R-ZZ-01]\n")

        class ReversedGlob(object):
            @staticmethod
            def glob(pattern):
                import glob as _g
                return list(reversed(sorted(_g.glob(pattern))))

        saved = steering.glob
        steering.glob = ReversedGlob
        try:
            found = [os.path.relpath(p, self.root) for p in _steering_files()]
        finally:
            steering.glob = saved
        self.assertEqual(found,
                         ["CLAUDE.md", "AGENTS.md",
                          os.path.join("steering", "alpha.md"),
                          os.path.join("steering", "middle.md"),
                          os.path.join("steering", "zebra.md")])

    def test_an_absent_explicit_file_is_skipped_rather_than_read(self):
        self.write("AGENTS.md", CLEAN_AGENTS)          # no CLAUDE.md
        self.assertEqual([os.path.basename(p) for p in _steering_files()],
                         ["AGENTS.md"])


class ContentPreservationTest(ScratchCorpusCase):
    """CP-1 / CP-2 / CP-3 over the scan's outputs."""

    def test_cp1_names_every_missing_protected_id(self):
        errors, _count, _base = _content_preservation_errors(set(), {})
        cp1 = [e for e in errors if e["rule"] == "CP-1"]
        self.assertEqual(len(cp1), len(steering.REQUIRED_RULE_IDS))
        self.assertIn(steering.REQUIRED_RULE_IDS[0], cp1[0]["text"])

    def test_cp2_reads_AGENTS_md_out_of_the_scans_texts(self):
        """The one-read claim. If CP-2 re-opened the file, substituting the text
        here would change nothing and this test could not exist."""
        agents = os.path.join(self.root, "AGENTS.md")
        errors, _c, _b = _content_preservation_errors(
            set(steering.REQUIRED_RULE_IDS), {agents: ALL_SECTIONS})
        self.assertEqual(errors, [])

    def test_cp2_matches_a_heading_EXACTLY_and_at_level_two(self):
        agents = os.path.join(self.root, "AGENTS.md")
        wrong_level = "# %s\n" % steering.REQUIRED_SECTIONS[0]
        errors, _c, _b = _content_preservation_errors(
            set(steering.REQUIRED_RULE_IDS), {agents: wrong_level})
        self.assertEqual(len(errors), len(steering.REQUIRED_SECTIONS))
        self.assertIn(steering.REQUIRED_SECTIONS[0], errors[0]["text"])

    def test_cp2_tolerates_trailing_whitespace_on_a_heading(self):
        agents = os.path.join(self.root, "AGENTS.md")
        text = "\n".join("## %s   " % s for s in steering.REQUIRED_SECTIONS)
        errors, _c, _b = _content_preservation_errors(
            set(steering.REQUIRED_RULE_IDS), {agents: text})
        self.assertEqual(errors, [])

    def test_an_absent_AGENTS_md_reports_every_section_rather_than_raising(self):
        errors, _c, _b = _content_preservation_errors(
            set(steering.REQUIRED_RULE_IDS), {})
        self.assertEqual([e["rule"] for e in errors],
                         ["CP-2"] * len(steering.REQUIRED_SECTIONS))

    def test_cp3_fires_only_BEYOND_the_margin(self):
        """The margin exists so ordinary consolidation does not fail the gate.
        Both sides of the boundary are asserted, because an off-by-one here
        either blocks every merge or never fires at all."""
        self.write(".rule-count-baseline", "100\n")
        ids = {"R-A-%02d" % i for i in range(100 - RULE_COUNT_REGRESSION_MARGIN)}
        errors, count, base = _content_preservation_errors(ids, {})
        self.assertEqual((count, base), (97, 100))
        self.assertEqual([e for e in errors if e["rule"] == "CP-3"], [])
        ids = {"R-A-%02d" % i for i in range(96)}
        errors, _c, _b = _content_preservation_errors(ids, {})
        cp3 = [e for e in errors if e["rule"] == "CP-3"]
        self.assertEqual(len(cp3), 1)
        self.assertIn("96 < baseline 100", cp3[0]["text"])

    def test_an_absent_or_unparsable_baseline_disables_CP3(self):
        for content in (None, "not-a-number\n", ""):
            if content is None:
                self.assertIsNone(_read_rule_count_baseline())
            else:
                self.write(".rule-count-baseline", content)
                self.assertIsNone(_read_rule_count_baseline())
            errors, _c, base = _content_preservation_errors(set(), {})
            self.assertIsNone(base)
            self.assertEqual([e for e in errors if e["rule"] == "CP-3"], [])

    def test_a_GROWN_corpus_is_never_a_regression(self):
        self.write(".rule-count-baseline", "10\n")
        errors, count, _b = _content_preservation_errors(
            {"R-A-%02d" % i for i in range(50)}, {})
        self.assertEqual(count, 50)
        self.assertEqual([e for e in errors if e["rule"] == "CP-3"], [])


class LintSteeringTest(ScratchCorpusCase):
    """The parent: how the two halves combine into `ok`."""

    def test_a_clean_corpus_is_green_in_both_modes(self):
        self.clean_corpus()
        for strict in (False, True):
            res = lint_steering(strict=strict)
            self.assertTrue(res["ok"], res)
            self.assertEqual(res["violations"], [])
            self.assertEqual(res["content_errors"], [])
            self.assertTrue(res["content_ok"])

    def test_content_errors_decide_ok_ONLY_under_strict(self):
        """R-PKG-04 runs it unstrict and the release gate runs it strict, so the
        two modes are not a convenience — collapsing them either blocks every
        package edit or lets Gate 1 pass with a protected rule deleted."""
        self.write("AGENTS.md", "## Planning\n\n- MUST plan. [R-PLAN-01]\n")
        loose = lint_steering()
        self.assertTrue(loose["ok"])
        self.assertFalse(loose["content_ok"])
        self.assertTrue(loose["content_errors"])
        strict = lint_steering(strict=True)
        self.assertFalse(strict["ok"])
        self.assertEqual(strict["content_errors"], loose["content_errors"])

    def test_each_DSF_half_can_fail_ok_on_its_own(self):
        """`(not violations) and (not banned) and budget_ok` — three
        independent disjuncts, each asserted alone, because dropping any one of
        them leaves the other two green and the linter still 'passing'."""
        self.clean_corpus()
        self.write("steering/a.md", "- lowercase leads [R-ZZ-01]\n")
        self.assertFalse(lint_steering()["ok"])

        os.remove(os.path.join(self.root, "steering", "a.md"))
        self.write("steering/b.md", "- MUST prefer the fast tier [R-ZZ-02]\n")
        res = lint_steering()
        self.assertFalse(res["ok"])
        self.assertEqual(res["violations"], [])
        self.assertEqual([b["word"] for b in res["banned_words"]], ["prefer"])

        os.remove(os.path.join(self.root, "steering", "b.md"))
        self.write("steering/c.md", "x" * (STEERING_CHAR_BUDGET + 1))
        res = lint_steering()
        self.assertFalse(res["ok"])
        self.assertFalse(res["budget_ok"])
        self.assertEqual((res["violations"], res["banned_words"]), ([], []))

    def test_the_budget_is_measured_over_the_WHOLE_corpus(self):
        """Per-file budgets would let ten files of 24k each through, and the
        budget is a claim about the context every session pays for."""
        half = STEERING_CHAR_BUDGET // 2 + 10
        self.write("steering/a.md", "x" * half)
        self.write("steering/b.md", "x" * half)
        res = lint_steering()
        self.assertFalse(res["budget_ok"])
        self.assertEqual(res["total_chars"], 2 * half)
        self.assertEqual(res["char_budget"], STEERING_CHAR_BUDGET)

    def test_an_exactly_full_budget_is_allowed(self):
        self.write("steering/a.md", "x" * STEERING_CHAR_BUDGET)
        self.assertTrue(lint_steering()["budget_ok"])

    def test_the_payload_carries_both_halves_regardless_of_mode(self):
        self.write("AGENTS.md", "- should hedge\n")
        res = lint_steering()
        self.assertEqual(sorted(res), ["banned_words", "budget_ok",
                                       "char_budget", "content_errors",
                                       "content_ok", "ok", "rule_count",
                                       "rule_count_baseline", "total_chars",
                                       "violations"])
        self.assertEqual(res["rule_count"], 0)
        self.assertIsNone(res["rule_count_baseline"])


class LiveCorpusTest(unittest.TestCase):
    """One row against the SHIPPED corpus — the gate tier's claim, kept here as
    a wiring check that the module's globals point at the real repo."""

    def test_the_repo_corpus_is_reachable_and_lints_green(self):
        res = lint_steering()
        self.assertTrue(res["ok"], res["violations"][:3])
        self.assertGreater(res["rule_count"], 0)
        self.assertLessEqual(res["total_chars"], STEERING_CHAR_BUDGET)


if __name__ == "__main__":
    unittest.main()
