"""The per-team template contract's own unit file — task 99 wave 12.

`agentware_lib.ops.fleet.fleettemplates` had no owning unit file at its module
path. Its coverage lived in `tests/unit/knowledge/test_templates_lint.py`, named
for the BEHAVIOUR rather than the module — a file that would not redden if this
module alone broke its layer contract, and one a reader looking for "who owns
fleettemplates" never finds. R-MOD-06 fixes the path; this is that file.

Wave 12 split `lint_templates` (22 branches) into a stem classifier it already
had plus three named obligations: find a conflict marker, read which keys the
frontmatter block DECLARES, and hold an entry template to T2/T3. Each is now
callable alone, so each is asserted on concrete values rather than through the
whole linter.

The properties that matter, and why each is a way the split can be wrong:

  DISAGREEMENT — `_conflict_marker_line` is deliberately NOT the org linter's
                 `ORG_CONFLICT_MARKER_RE`. That pattern also matches `=======`,
                 which is a legal markdown setext underline. Folding the two
                 scanners into one "shared" helper is the de-duplication this
                 wave was invited to make and would have been a silent
                 behaviour change; the disagreement is pinned by a test so the
                 next reader finds the reason before making it.
  DECLARED     — a template's keys are declared-but-EMPTY by construction, so a
                 YAML parse that drops empty values answers T2/T3/T4 with
                 "missing" for every key. The line scanner is the point.
  ORDER        — T5 is scanned on the RAW text BEFORE the frontmatter parse, so
                 a template that is both conflicted and frontmatter-less reports
                 both. T1's early return would swallow T5 if reordered.
  COUNTED      — `checked` counts what the CLASSIFIER recognized, so an
                 unreadable template is checked-and-failed, not skipped. A
                 report of "0 templates checked, 0 errors" on a locked-down
                 directory is the failure mode.
  SEVERITY     — T3 is the only rule whose severity moves with --strict, and T2
                 beside it must NOT move.

Hermetic: a scratch templates/ dir per test, stdlib unittest only.
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

from agentware_lib.core.orgkeys import ORG_CONFLICT_MARKER_RE      # noqa: E402
from agentware_lib.ops.fleet.fleettemplates import (               # noqa: E402
    SKILL_TEMPLATE_FM,
    TEMPLATE_ORG_FM,
    TEMPLATE_REQUIRED_FM,
    _conflict_marker_line,
    _declared_frontmatter_keys,
    _lint_entry_contract,
    _lint_template_file,
    _template_kind,
    lint_templates,
)

FULL_ENTRY = ("---\nid: \ntitle: \ncategory: learnings\ntags: []\nsummary: \n"
              "origin: \nteam: \n---\n\n# body\n")


class TemplateKindTest(unittest.TestCase):
    """The stem classifier — what the whole contract dispatches on."""

    def test_skill_wins_over_every_entry_word(self):
        """`skill` is checked FIRST and returns immediately. A stem carrying
        both words (a real one: `skill-reference-template`) must be held to the
        SKILL contract, or a skill template is asked for `category` and `tags`
        it has no business declaring."""
        self.assertEqual(_template_kind("skill-reference-template"), "skill")
        self.assertEqual(_template_kind("MySkill"), "skill")

    def test_the_entry_vocabulary_is_matched_as_a_SUBSTRING(self):
        for stem in ("learning-template", "decision", "team-project-notes",
                     "configuration", "config", "reference", "prompt"):
            self.assertEqual(_template_kind(stem), "entry", stem)

    def test_an_unknown_stem_is_an_entry_only_if_it_says_template(self):
        self.assertEqual(_template_kind("widget-template"), "entry")
        self.assertEqual(_template_kind("widget_template"), "entry")
        self.assertEqual(_template_kind("template"), "entry")
        self.assertIsNone(_template_kind("widget"))
        self.assertIsNone(_template_kind("README"))
        self.assertIsNone(_template_kind(""))

    def test_the_classifier_is_case_insensitive(self):
        self.assertEqual(_template_kind("TEMPLATE"), "entry")
        self.assertEqual(_template_kind("Learning-Template"), "entry")

    def test_a_bare_template_suffix_is_not_the_same_as_a_bare_name(self):
        """`endswith('-template')` and `== 'template'` are separate arms: a stem
        that merely CONTAINS the word is not a template."""
        self.assertIsNone(_template_kind("templates-readme"))


class ConflictMarkerTest(unittest.TestCase):
    """T5's scanner, and the reason it is not the org linter's."""

    def test_it_returns_the_line_of_the_FIRST_marker(self):
        self.assertEqual(_conflict_marker_line("a\nb\n<<<<<<< HEAD\nc\n"), 3)
        self.assertEqual(_conflict_marker_line("<<<<<<< a\n>>>>>>> b\n"), 1)

    def test_a_clean_text_returns_None_not_zero(self):
        """Line numbers are 1-based, so None is the only value the caller can
        distinguish from a marker on line 1 — `if marker:` would work today and
        break the moment a 0-based index is introduced."""
        self.assertIsNone(_conflict_marker_line("clean\n"))
        self.assertIsNone(_conflict_marker_line(""))

    def test_it_deliberately_DISAGREES_with_the_org_linter_on_a_divider(self):
        """THE reason this is not a shared scanner. `=======` is a legal setext
        underline in markdown; O6's pattern flags it and T5's must not, so
        de-duplicating the two would silently start failing template lint on
        every heading underlined with seven or more equals signs."""
        divider = "Heading\n=======\n"
        self.assertIsNone(_conflict_marker_line(divider))
        self.assertTrue(any(ORG_CONFLICT_MARKER_RE.match(l)
                            for l in divider.splitlines()))

    def test_a_marker_must_START_the_line(self):
        self.assertIsNone(_conflict_marker_line("  <<<<<<< indented\n"))
        self.assertIsNone(_conflict_marker_line("see <<<<<<< inline\n"))


class DeclaredKeysTest(unittest.TestCase):
    """Which keys a frontmatter block DECLARES — the T2/T3/T4 input."""

    def test_a_key_with_an_EMPTY_value_still_counts_as_declared(self):
        """The whole reason this is not a YAML parse: every key in a template is
        empty by construction, so a parser that drops empty values reports the
        entire contract as missing on a perfectly good template."""
        self.assertEqual(_declared_frontmatter_keys(FULL_ENTRY),
                         {"id", "title", "category", "tags", "summary",
                          "origin", "team"})

    def test_a_commented_out_key_is_NOT_declared(self):
        keys = _declared_frontmatter_keys(
            "---\nid: \n# summary: later\n---\n\nbody\n")
        self.assertEqual(keys, {"id"})

    def test_an_indented_key_IS_declared(self):
        keys = _declared_frontmatter_keys("---\nid: \n  tags: []\n---\n\nb\n")
        self.assertEqual(keys, {"id", "tags"})

    def test_a_line_with_no_colon_is_ignored(self):
        keys = _declared_frontmatter_keys("---\nid: \njust prose\n---\n\nb\n")
        self.assertEqual(keys, {"id"})

    def test_a_body_line_after_the_block_is_never_read(self):
        keys = _declared_frontmatter_keys("---\nid: \n---\n\nsummary: no\n")
        self.assertEqual(keys, {"id"})

    def test_a_text_with_no_block_at_all_declares_nothing(self):
        self.assertEqual(_declared_frontmatter_keys("> legacy\n"), set())

    def test_the_block_must_open_on_the_FIRST_line(self):
        self.assertEqual(
            _declared_frontmatter_keys("\n---\nid: \n---\n\nbody\n"), set())


class EntryContractTest(unittest.TestCase):
    """T2 + T3 over a declared key set."""

    def setUp(self):
        self.errors, self.warnings = [], []

    def err(self, rule, file, line, message):
        self.errors.append((rule, message))

    def warn(self, rule, file, line, message):
        self.warnings.append((rule, message))

    def _run(self, declared, strict=False):
        _lint_entry_contract("templates/x.md", set(declared), strict, self.err,
                             self.warn)

    def test_t2_and_t3_are_independent_and_reported_in_that_order(self):
        self._run([])
        self.assertEqual([r for r, _ in self.errors], ["T2"])
        self.assertEqual([r for r, _ in self.warnings], ["T3"])

    def test_the_missing_keys_are_listed_in_the_VOCABULARY_order(self):
        """A set has no order, so the message is built from the required tuple.
        Iterating the set instead makes the message non-deterministic across
        runs and every golden capture of it flaky."""
        self._run(["title"])
        self.assertIn("id, category, tags, summary", self.errors[0][1])
        self.assertEqual(TEMPLATE_REQUIRED_FM[0], "id")

    def test_strict_moves_T3_and_leaves_T2_alone(self):
        self._run([], strict=True)
        self.assertEqual([r for r, _ in self.errors], ["T2", "T3"])
        self.assertEqual(self.warnings, [])

    def test_a_complete_entry_template_is_silent_in_both_modes(self):
        full = list(TEMPLATE_REQUIRED_FM) + list(TEMPLATE_ORG_FM)
        self._run(full)
        self._run(full, strict=True)
        self.assertEqual((self.errors, self.warnings), ([], []))


class LintTemplatesTest(unittest.TestCase):
    """The linter as a whole: ordering, counting and degradation."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-w12-tpl-")
        self.addCleanup(self._cleanup)
        self.tdir = os.path.join(self.root, "templates")
        os.makedirs(self.tdir, exist_ok=True)

    def _cleanup(self):
        for name in os.listdir(self.tdir) if os.path.isdir(self.tdir) else []:
            try:
                os.chmod(os.path.join(self.tdir, name), 0o644)
            except OSError:
                pass
        shutil.rmtree(self.root, ignore_errors=True)

    def _tpl(self, name, text):
        path = os.path.join(self.tdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_an_absent_templates_dir_is_a_clean_noop(self):
        res = lint_templates(os.path.join(self.root, "nope"))
        self.assertEqual(res, {"ok": True, "errors": [], "warnings": [],
                               "checked": 0})

    def test_a_None_kdir_lints_the_process_CWD_not_nothing(self):
        """`kdir or ""` stops `os.path.join(None, …)` raising — and what it
        produces instead is the RELATIVE path `templates`, so an unresolved
        knowledge dir does not lint nothing, it lints whatever `templates/`
        happens to sit beside the process. Asserted rather than assumed,
        because the benign reading ("it degrades to a no-op") is what a reader
        expects and is wrong: run from a checkout that ships templates/, the
        report is about THAT directory and says nothing about which one.
        """
        cwd = os.getcwd()
        try:
            os.chdir(self.root)                     # holds an empty templates/
            self._tpl("learning-template.md", "> legacy\n")
            for kdir in (None, ""):
                res = lint_templates(kdir)
                self.assertEqual(res["checked"], 1, kdir)
                self.assertEqual([e["file"] for e in res["errors"]],
                                 ["templates/learning-template.md"], kdir)
            empty = tempfile.mkdtemp(prefix="aw-w12-cwd-")
            self.addCleanup(shutil.rmtree, empty, True)
            os.chdir(empty)                          # no templates/ at all
            self.assertEqual(lint_templates(None)["checked"], 0)
        finally:
            os.chdir(cwd)

    def test_an_EMPTY_templates_dir_reaches_the_second_ok_return(self):
        """Not the same line as the absent-dir arm above, and only this input
        reaches it: `ok` is computed from the error list rather than hardcoded."""
        res = lint_templates(self.root)
        self.assertEqual(res, {"ok": True, "errors": [], "warnings": [],
                               "checked": 0})

    def test_unrecognized_files_are_skipped_and_never_counted(self):
        self._tpl("README.md", "no frontmatter here\n")
        self._tpl("notes.txt", "> legacy\n")
        res = lint_templates(self.root)
        self.assertEqual((res["checked"], res["errors"]), (0, []))

    def test_files_are_walked_in_SORTED_order(self):
        """The emitted rows are a byte contract the org linter re-surfaces
        verbatim, so `os.listdir` order — which is filesystem-dependent — would
        make every golden capture of an org report flaky."""
        self._tpl("z-prompt-template.md", "> legacy\n")
        self._tpl("a-decision-template.md", "> legacy\n")
        res = lint_templates(self.root)
        self.assertEqual([e["file"] for e in res["errors"]],
                         ["templates/a-decision-template.md",
                          "templates/z-prompt-template.md"])

    def test_T5_is_reported_ALONGSIDE_T1_not_swallowed_by_it(self):
        """The ordering claim. T1's early return comes after the T5 scan, so a
        conflicted legacy template reports both — reversing the two loses the
        conflict marker, which is the more urgent of the pair."""
        self._tpl("learning-template.md",
                  "> legacy\n<<<<<<< HEAD\nmine\n>>>>>>> theirs\n")
        res = lint_templates(self.root)
        self.assertEqual([e["rule"] for e in res["errors"]], ["T5", "T1"])
        self.assertEqual(res["errors"][0]["line"], 2)
        self.assertEqual(res["errors"][1]["line"], 1)

    def test_a_skill_template_is_never_held_to_the_entry_contract(self):
        self._tpl("skill-template.md", "---\nname: \n---\n\nbody\n")
        res = lint_templates(self.root)
        self.assertEqual([e["rule"] for e in res["errors"]], ["T4"])
        self.assertIn("description", res["errors"][0]["message"])
        self.assertEqual(SKILL_TEMPLATE_FM, ("name", "description"))
        self.assertEqual(res["warnings"], [])

    def test_a_complete_skill_template_is_silent(self):
        self._tpl("skill-template.md",
                  "---\nname: \ndescription: \n---\n\nbody\n")
        res = lint_templates(self.root)
        self.assertEqual((res["errors"], res["warnings"], res["checked"]),
                         ([], [], 1))

    def test_an_unreadable_template_is_COUNTED_and_failed(self):
        """`checked` is incremented before the open, so a directory the process
        cannot read reports "1 checked, 1 error" rather than the far more
        misleading "0 checked, 0 errors"."""
        locked = self._tpl("learning-template.md", FULL_ENTRY)
        os.chmod(locked, 0)
        try:
            res = lint_templates(self.root)
        finally:
            os.chmod(locked, 0o644)
        self.assertEqual(res["checked"], 1)
        self.assertEqual([e["rule"] for e in res["errors"]], ["T1"])
        self.assertEqual(res["errors"][0]["line"], 0)
        self.assertIn("cannot read template", res["errors"][0]["message"])

    def test_ok_is_false_for_errors_and_true_for_warnings_alone(self):
        self._tpl("learning-template.md",
                  "---\nid: \ntitle: \ncategory: c\ntags: []\nsummary: \n"
                  "---\n\nbody\n")
        res = lint_templates(self.root)
        self.assertTrue(res["ok"])
        self.assertEqual([w["rule"] for w in res["warnings"]], ["T3"])
        strict = lint_templates(self.root, strict=True)
        self.assertFalse(strict["ok"])

    def test_the_file_helper_reports_through_the_callers_accumulators(self):
        """Every helper appends through the caller's `err`/`warn`. One that
        RETURNED its findings would be one refactor away from fail-fast, and
        report-every-problem is the contract."""
        self._tpl("decision-template.md", "---\nid: \n---\n\nbody\n")
        errors, warnings = [], []
        _lint_template_file(
            self.tdir, "decision-template.md", "entry", False,
            lambda r, f, l, m: errors.append(r),
            lambda r, f, l, m: warnings.append(r))
        self.assertEqual(errors, ["T2"])
        self.assertEqual(warnings, ["T3"])


if __name__ == "__main__":
    unittest.main()
