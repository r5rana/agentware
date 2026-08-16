"""The shipped TEMPLATE CORPUS and the T1..T5 contract that guards it.

Parity row: `docs:template-frontmatter-corpus` (task 51), whose mutation is
"remove a required field from any template: the linter must redden naming the
field" — reproduced verbatim below in
`TemplateLinterBitesTests.test_dropping_each_required_field_reddens_and_names_it`.

Why this corpus has to be reconciled BEFORE the templates verb and org lint land
-------------------------------------------------------------------------------
This tree's templates opened with a markdown heading and carried no frontmatter
at all; the donor's linter requires a declared frontmatter block. Adopting either
one alone breaks every knowledge base at once — adopt the linter and every
shipped template fails it; adopt the templates without the linter and nothing
notices when one drifts back.

The templates are also not decoration. `agentware init` COPIES them into the
operator's knowledge dir, so they are the first thing a new KB contains and the
shape every hand-authored entry is modelled on. A template missing `summary`
produces entries missing `summary`, and `summary` is what `recall` ranks over and
what a future session reads instead of opening the file — so the defect surfaces
much later as "recall is not finding things", with no trace back to the template.

Stdlib unittest only; the mutation arms run against a COPY so the shipped corpus
is never modified.
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

from agentware_lib.knowledge.index import REQUIRED_FIELDS  # noqa: E402
from agentware_lib.ops.fleet.fleettemplates import (  # noqa: E402
    SKILL_TEMPLATE_FM,
    TEMPLATE_ORG_FM,
    TEMPLATE_REQUIRED_FM,
    lint_templates,
)

TEMPLATES = os.path.join(REPO, "templates")

#: The four the plan requires. `decision-template.md` is the one this tree lacked.
EXPECTED = {"learning-template.md", "decision-template.md",
            "project-template.md", "skill-template.md"}


def _declared(path):
    """The frontmatter keys a template DECLARES (values may be placeholders)."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not text.startswith("---"):
        return set()
    block = text.split("---", 2)[1]
    return {ln.split(":", 1)[0].strip() for ln in block.splitlines()
            if ":" in ln and not ln.strip().startswith("#")}


class ShippedCorpusTests(unittest.TestCase):
    """What the repo actually ships."""

    def test_the_corpus_holds_the_four_templates(self):
        got = {f for f in os.listdir(TEMPLATES) if f.endswith(".md")}
        self.assertEqual(got, EXPECTED)

    def test_a_decision_template_exists(self):
        """Called out separately because its ABSENCE was the gap: `decide` is a
        first-class entry category with no template to model an entry on."""
        self.assertIn("decision-template.md", os.listdir(TEMPLATES))

    def test_every_template_opens_with_a_frontmatter_block(self):
        for fn in sorted(EXPECTED):
            with open(os.path.join(TEMPLATES, fn), encoding="utf-8") as fh:
                first = fh.readline().rstrip("\n")
            self.assertEqual(first, "---",
                             "%s does not OPEN with a frontmatter block" % fn)

    def test_entry_templates_declare_the_full_required_field_set(self):
        for fn in ("learning-template.md", "decision-template.md",
                   "project-template.md"):
            declared = _declared(os.path.join(TEMPLATES, fn))
            missing = [k for k in TEMPLATE_REQUIRED_FM if k not in declared]
            self.assertEqual(missing, [], "%s is missing %r" % (fn, missing))

    def test_entry_templates_declare_the_org_facets(self):
        """R-ORG-05. Inferred from the path on a flat install, so a
        single-operator KB may delete them — but the SHIPPED template declares
        them, because a team install that has to discover the facets by reading
        the linter's error message has already had a bad first day."""
        for fn in ("learning-template.md", "decision-template.md",
                   "project-template.md"):
            declared = _declared(os.path.join(TEMPLATES, fn))
            missing = [k for k in TEMPLATE_ORG_FM if k not in declared]
            self.assertEqual(missing, [], "%s is missing %r" % (fn, missing))

    def test_the_skill_template_declares_the_agentskills_contract(self):
        declared = _declared(os.path.join(TEMPLATES, "skill-template.md"))
        for k in SKILL_TEMPLATE_FM:
            self.assertIn(k, declared)

    def test_the_declared_set_covers_what_index_validate_requires(self):
        """The templates and the INDEX must agree. `path` is the one required
        index field a template cannot carry — it is assigned when the entry is
        registered, not authored — so it is excluded here deliberately rather
        than by omission."""
        for fn in ("learning-template.md", "decision-template.md",
                   "project-template.md"):
            declared = _declared(os.path.join(TEMPLATES, fn))
            for k in REQUIRED_FIELDS:
                if k == "path":
                    continue
                self.assertIn(k, declared, "%s does not declare %r, which "
                              "`index validate` requires" % (fn, k))

    def test_the_linter_passes_over_the_shipped_corpus_in_strict_mode(self):
        rep = lint_templates(REPO, strict=True)
        self.assertEqual(rep["checked"], 4)
        self.assertTrue(rep["ok"], "shipped templates fail T1..T5: %r"
                        % (rep["errors"],))
        self.assertEqual(rep["errors"], [])


class TemplateLinterBitesTests(unittest.TestCase):
    """The row's MUTATION, run. A linter nobody has watched fail is decoration."""

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="aw-tmpl-lint-")
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        shutil.copytree(TEMPLATES, os.path.join(self.scratch, "templates"))

    def _lint(self):
        return lint_templates(self.scratch, strict=True)

    def _rewrite(self, fn, text):
        with open(os.path.join(self.scratch, "templates", fn), "w",
                  encoding="utf-8") as fh:
            fh.write(text)

    def _read(self, fn):
        with open(os.path.join(self.scratch, "templates", fn),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_the_copied_corpus_is_clean_before_any_mutation(self):
        """The negative control. Without it every assertion below could be
        satisfied by a linter that fails on everything."""
        rep = self._lint()
        self.assertTrue(rep["ok"], rep["errors"])
        self.assertEqual(rep["checked"], 4)

    def test_dropping_each_required_field_reddens_and_names_it(self):
        """The parity row's mutation, per field. Naming the field is the part
        that matters: a linter that says only 'invalid template' sends the
        operator to read the rules instead of fixing the line."""
        fn = "learning-template.md"
        original = self._read(fn)
        for field in TEMPLATE_REQUIRED_FM:
            self._rewrite(fn, "\n".join(
                l for l in original.split("\n")
                if not l.startswith(field + ":")))
            rep = self._lint()
            self.assertFalse(rep["ok"], "dropping %r left the linter green" % field)
            t2 = [e for e in rep["errors"] if e["rule"] == "T2"]
            self.assertTrue(t2, "dropping %r did not raise T2" % field)
            self.assertIn(field, t2[0]["message"],
                          "T2 fired for %r without naming the field: %s"
                          % (field, t2[0]["message"]))
            self._rewrite(fn, original)
        self.assertTrue(self._lint()["ok"], "restore failed")

    def test_removing_the_frontmatter_block_entirely_raises_T1(self):
        """The legacy shape this task removed — a heading-only template — must
        be caught as a distinct rule, because the fix is different: T2 says 'add
        a line', T1 says 'this file predates the contract'."""
        self._rewrite("decision-template.md", "# Decision Title\n\nbody\n")
        rep = self._lint()
        self.assertFalse(rep["ok"])
        self.assertTrue([e for e in rep["errors"] if e["rule"] == "T1"])

    def test_dropping_a_skill_key_raises_T4_not_T2(self):
        """A skill template is held to the agentskills.io contract, not the entry
        contract. Conflating them would demand `category`/`summary` of a portable
        skill and reject a spec-compliant one."""
        fn = "skill-template.md"
        original = self._read(fn)
        self._rewrite(fn, "\n".join(l for l in original.split("\n")
                                    if not l.startswith("description:")))
        rep = self._lint()
        self.assertFalse(rep["ok"])
        self.assertTrue([e for e in rep["errors"] if e["rule"] == "T4"])
        self.assertFalse([e for e in rep["errors"] if e["rule"] == "T2"])

    def test_a_conflict_marker_raises_T5(self):
        """A template is a file people merge. A leftover marker would be copied
        verbatim into every entry authored from it."""
        original = self._read("project-template.md")
        self._rewrite("project-template.md",
                      original + "\n<<<<<<< HEAD\nx\n>>>>>>> other\n")
        rep = self._lint()
        self.assertFalse(rep["ok"])
        self.assertTrue([e for e in rep["errors"] if e["rule"] == "T5"])

    def test_a_directory_with_no_templates_is_a_clean_noop(self):
        """A team that ships no templates of its own is not a violation — the
        contract binds templates that EXIST, so an empty install stays green."""
        empty = tempfile.mkdtemp(prefix="aw-tmpl-empty-")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        rep = lint_templates(empty, strict=True)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["checked"], 0)


if __name__ == "__main__":
    unittest.main()
