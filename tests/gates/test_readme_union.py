"""The top-level documents are a UNION of both trees, not a pick-one.

Feature 260813-modular-core-adoption, task 20. The merge takes the donor tree's
modular core, and the donor tree had DELETED `ROADMAP.md` and rewritten the
README: it dropped this tree's hero image, licence badge, both maturity/adoption
rows, the roadmap section and the licence section, and added an architecture
section plus a "this README is not the source of truth" note. A pick-one merge
in either direction silently loses half of that, and nothing else in this
migration can see it -- the surface fixture compares argparse objects, the body
and constant differs compare `def`s and module constants, and the parity
manifest compares capability rows. Prose has no other gate.

So the anchors are frozen here, by CONTENT rather than by line number, and the
byte-identity of `ROADMAP.md` against the pre-merge tag is asserted rather than
eyeballed.
"""

from tests._paths import TESTS_DIR

import os
import subprocess
import sys
import unittest

HERE = TESTS_DIR
REPO_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _markdown_links as ml  # noqa: E402

PRE_MERGE_TAG = "aw-pre-merge"

#: The ONE sanctioned deviation from the preserved `ROADMAP.md` (task 77): three
#: vision quotes attributed to the operator BY NAME, which `R-LOC-03` forbids in
#: a tracked file. 1-indexed line numbers, the separator the attribution follows,
#: and the neutral token that replaced the handle.
ROADMAP_REDACTED_LINES = (16, 21, 25)
ATTRIBUTION_SEP = "\u2014 "
ROADMAP_ATTRIBUTION = "the operator"

#: Six anchors this tree contributes; the donor README carries none of them.
LOCAL_ANCHORS = (
    ("hero image", "docs/assets/hero.png"),
    ("licence badge", "badge/license-Apache--2.0-blue"),
    ("maturity row (harness comparison)",
     "| Maturity / adoption (★, approx) | new | ≈78k |"),
    ("maturity row (memory-layer comparison)",
     "| Maturity / adoption (★, approx) | new | ≈59k |"),
    ("roadmap section", "\n## Roadmap\n"),
    ("licence section", "\n## License\n"),
)

#: Two anchors the donor contributes; this tree's README carried neither.
DONOR_ANCHORS = (
    ("architecture section", "\n## Architecture\n"),
    ("not-the-source-of-truth note",
     "**This README is the public/marketing face — it is _not_ the source of "
     "truth.**"),
)


def readme():
    with open(os.path.join(REPO_ROOT, "README.md"), "r",
              encoding="utf-8") as handle:
        return handle.read()


def _git(*args):
    proc = subprocess.run(["git", "-C", REPO_ROOT] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, proc.stdout


class ReadmeUnionTest(unittest.TestCase):

    def test_every_local_anchor_survives_the_merge(self):
        body = readme()
        for label, anchor in LOCAL_ANCHORS:
            self.assertIn(anchor, body, "lost local anchor: %s" % label)

    def test_every_donor_anchor_is_adopted(self):
        body = readme()
        for label, anchor in DONOR_ANCHORS:
            self.assertIn(anchor, body, "missing donor anchor: %s" % label)

    def test_the_anchor_sets_are_the_sizes_the_plan_names(self):
        self.assertEqual(len(LOCAL_ANCHORS), 6)
        self.assertEqual(len(DONOR_ANCHORS), 2)

    def test_the_architecture_section_names_the_modular_layout(self):
        body = readme()
        for token in ("scripts/agentware_lib/", "core → knowledge → retrieval",
                      "catalog/modules.json"):
            self.assertIn(token, body, token)

    def test_the_readme_links_all_resolve(self):
        problems = ml.check_one(REPO_ROOT, "README.md", {})
        self.assertEqual(problems, [], problems)


class RoadmapPreservedTest(unittest.TestCase):
    """The donor DELETED this file; the plan requires it byte-identical."""

    def test_the_roadmap_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, "ROADMAP.md")))

    def test_the_roadmap_is_byte_identical_to_the_pre_merge_tag(self):
        """NARROWED at task 77: identical to the tag APART FROM the R-LOC-03
        attribution redaction, which is enumerated here rather than forgiven.

        The preserved file quoted the operator by name three times, which is a
        personal-data leak in a repository whose whole claim is that it ships
        none (`scripts/agentware audit` -> `personal_data`, recorded as
        pre-existing drift owned by task 77). Preservation and R-LOC-03 cannot
        both be absolute, so this asserts the STRONGEST remaining statement: no
        line added, none removed, every other line byte-identical, and each of
        the three changed lines differing ONLY in the attribution token after
        its trailing em dash. A fourth changed line, a changed word anywhere
        else, or a re-personalised attribution each reddens it.
        """
        code, blob = _git("show", "%s:ROADMAP.md" % PRE_MERGE_TAG)
        if code != 0:
            self.skipTest("%s tag not present in this checkout" % PRE_MERGE_TAG)
        with open(os.path.join(REPO_ROOT, "ROADMAP.md"), "rb") as handle:
            live = handle.read()
        if live == blob:                      # nothing to forgive
            return
        was = blob.decode("utf-8").splitlines()
        now = live.decode("utf-8").splitlines()
        self.assertEqual(len(was), len(now),
                         "the redaction may not add or remove a line")
        changed = [i for i, (a, b) in enumerate(zip(was, now)) if a != b]
        self.assertEqual(len(changed), len(ROADMAP_REDACTED_LINES),
                         "unexpected change(s) at line(s) %s"
                         % [i + 1 for i in changed])
        for i in changed:
            before, after = was[i], now[i]
            self.assertIn(ATTRIBUTION_SEP, before, before)
            self.assertEqual(before.rsplit(ATTRIBUTION_SEP, 1)[0],
                             after.rsplit(ATTRIBUTION_SEP, 1)[0],
                             "only the attribution token may change")
            self.assertEqual(after.rsplit(ATTRIBUTION_SEP, 1)[1],
                             ROADMAP_ATTRIBUTION,
                             "a redacted attribution must be the neutral one")
            self.assertNotEqual(before.rsplit(ATTRIBUTION_SEP, 1)[1],
                                ROADMAP_ATTRIBUTION,
                                "line %d was already neutral -- the ledger "
                                "claims a redaction that did not happen" % (i + 1))

    def test_every_redacted_roadmap_line_is_the_neutral_attribution(self):
        """The live file's own side of the ledger: the three declared lines say
        the neutral thing, so the redaction cannot be quietly reverted in a
        checkout where the pre-merge tag is absent and the test above skips."""
        with open(os.path.join(REPO_ROOT, "ROADMAP.md"), encoding="utf-8") as h:
            lines = h.read().splitlines()
        for number in ROADMAP_REDACTED_LINES:
            self.assertTrue(lines[number - 1].endswith(
                ATTRIBUTION_SEP + ROADMAP_ATTRIBUTION),
                "ROADMAP.md:%d is not the neutral attribution: %r"
                % (number, lines[number - 1]))

    def test_the_licence_and_notice_the_donor_dropped_are_present(self):
        for name in ("LICENSE", "NOTICE"):
            self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, name)), name)


if __name__ == "__main__":
    unittest.main()
