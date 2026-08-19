"""learn-time capture quality hardening
(feature 260710-learning-repair-p1 task 5).

cmd_learn now: (1) auto-slugifies/normalizes a non-slug --topic (the bug that
left 4 unpromotable spaced/em-dash entries), (2) supports a --title override,
(3) probes for near-duplicate existing learnings and refuses without --force.
Stdlib unittest; synthetic KB (seeds learnings geofence-reminders + macos).
"""

import os

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # discover -s tests puts tests/ on sys.path
    from _fixtures import SyntheticKBTestCase, load_cli


# Distinctive, self-contained body used for the near-duplicate cases.
DUP_BODY = ("Frobnicating the quux widget requires calibrating the zorble "
            "threshold before the wibble subsystem initializes otherwise the "
            "flumph handler deadlocks on the grommet lock.")


class LearnHardeningTests(SyntheticKBTestCase):
    def _fm(self, rel):
        mod = load_cli()
        fields, _ = mod.split_frontmatter(
            open(os.path.join(self.kdir, rel), encoding="utf-8").read())
        return fields

    def _ids(self):
        return {e["id"] for e in self.read_index()["entries"]}

    # --- topic slug-normalization -------------------------------------------
    def test_nonslug_topic_autoslugified(self):
        code, _out, err = self.run_cli(
            ["learn", "--topic", "iOS Signing: Derive Team", "--summary", "x",
             "--tags", "a,b",
             "--content", "Distinct zorble wibble grommet flumph body one."])
        self.assertEqual(code, 0, err)
        self.assertIn("normalized --topic", err)
        self.assertTrue(os.path.isfile(
            os.path.join(self.kdir, "learnings", "ios-signing-derive-team.md")))
        self.assertIn("learn-ios-signing-derive-team", self._ids())

    def test_slug_topic_emits_no_notice(self):
        code, _out, err = self.run_cli(
            ["learn", "--topic", "already-a-slug", "--summary", "x", "--tags",
             "a", "--content", "Distinct quux calibrating zorble body two."])
        self.assertEqual(code, 0, err)
        self.assertNotIn("normalized", err)

    def test_topic_with_no_slug_chars_rejected(self):
        before = len(self.read_index()["entries"])
        code, _out, err = self.run_cli(
            ["learn", "--topic", "!!!", "--summary", "x", "--tags", "a",
             "--content", "Body."])
        self.assertNotEqual(code, 0)
        self.assertIn("no slug-safe characters", err)
        self.assertEqual(len(self.read_index()["entries"]), before)

    # --- title override ------------------------------------------------------
    def test_title_override_written(self):
        code, _out, err = self.run_cli(
            ["learn", "--topic", "widget-quirk", "--summary", "A widget quirk.",
             "--tags", "widgets",
             "--content", "Distinct flumph handler deadlock body three.",
             "--title", "My Custom Title"])
        self.assertEqual(code, 0, err)
        fm = self._fm("learnings/widget-quirk.md")
        self.assertEqual(fm["title"], "My Custom Title")

    def test_default_title_from_original_topic(self):
        mod = load_cli()
        code, _out, err = self.run_cli(
            ["learn", "--topic", "iOS Signing Note", "--summary", "s", "--tags",
             "a", "--content", "Distinct wibble subsystem body four."])
        self.assertEqual(code, 0, err)
        self.assertIn("learn-ios-signing-note", self._ids())
        fm = self._fm("learnings/ios-signing-note.md")
        self.assertEqual(fm["title"], mod._topic_to_title("iOS Signing Note"))

    # --- near-duplicate probe ------------------------------------------------
    def test_near_duplicate_blocked_without_force(self):
        c1, _o, e1 = self.run_cli(
            ["learn", "--topic", "alpha-doc", "--summary", "Distinct note.",
             "--tags", "x,y", "--content", DUP_BODY])
        self.assertEqual(c1, 0, e1)
        c2, _o2, e2 = self.run_cli(
            ["learn", "--topic", "beta-doc", "--summary", "Distinct note.",
             "--tags", "x,y", "--content", DUP_BODY])
        self.assertNotEqual(c2, 0, "a near-verbatim duplicate must be blocked")
        self.assertIn("near-duplicate", e2)
        self.assertIn("learn-alpha-doc", e2)
        self.assertFalse(os.path.isfile(
            os.path.join(self.kdir, "learnings", "beta-doc.md")))
        self.assertNotIn("learn-beta-doc", self._ids())

    def test_near_duplicate_allowed_with_force(self):
        self.run_cli(["learn", "--topic", "alpha-doc", "--summary", "n",
                      "--tags", "x", "--content", DUP_BODY])
        code, _o, err = self.run_cli(
            ["learn", "--topic", "beta-doc", "--summary", "n", "--tags", "x",
             "--content", DUP_BODY, "--force"])
        self.assertEqual(code, 0, err)
        self.assertIn("learn-beta-doc", self._ids())
        self.assertTrue(os.path.isfile(
            os.path.join(self.kdir, "learnings", "beta-doc.md")))

    def test_distinct_content_not_blocked(self):
        code, _o, err = self.run_cli(
            ["learn", "--topic", "unrelated-note", "--summary", "unrelated",
             "--tags", "z",
             "--content", "Snorble grfaltz mimsy borogove distinct content "
                          "unrelated to geofence or macos entirely."])
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    import unittest
    unittest.main()
