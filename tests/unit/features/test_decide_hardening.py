"""decide-time capture quality hardening
(feature 260813-modular-core-adoption, plan task 91).

`cmd_decide` took `--topic` VERBATIM as both the `decisions/` filename stem and
the entry id, so a sentence passed there produced a file whose name carried
spaces and an id with spaces in it — recoverable only by `index remove` + re-add,
which is exactly the manual repair `learn`'s normalization exists to spare the
operator. Both sides of the merge behaved identically, so this was a pre-existing
asymmetry, not a port regression.

Task 91 puts BOTH curation verbs on one `--topic` grammar (`_curation_topic`) and
one near-duplicate policy (`_near_duplicate_refusal` over a category-scoped
`_near_duplicate_entry`). This file is the DECIDE half; `test_learn_hardening`
is the learn half and must keep passing UNCHANGED, because "the same grammar"
is a claim about both verbs, not one.

Stdlib unittest; synthetic KB (seeds learnings, no decisions — each near-dup case
plants its own).
"""

import os

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli
except ImportError:  # discover -s tests puts tests/ on sys.path
    from _fixtures import SyntheticKBTestCase, load_cli


# A distinctive, self-contained rationale used for the near-duplicate cases: the
# same shape `test_learn_hardening.DUP_BODY` has, for the same reason — the ratio
# must be driven by CONTENT, not by whatever the synthetic seeds happen to say.
DUP_RATIONALE = (
    "Frobnicating the quux widget requires calibrating the zorble threshold "
    "before the wibble subsystem initializes, otherwise the flumph handler "
    "deadlocks on the grommet lock and the borogove queue never drains.")


class DecideHardeningTests(SyntheticKBTestCase):
    def _decide(self, topic, **over):
        argv = [
            "decide", "--topic", topic,
            "--summary", over.get("summary", "a one-line decision summary"),
            "--options", over.get("options", "A | B"),
            "--choice", over.get("choice", "A"),
            "--rationale", over.get("rationale", "A is reversible and in-scope"),
            "--reversible", over.get("reversible", "yes")]
        if over.get("tags"):
            argv += ["--tags", over["tags"]]
        if over.get("force"):
            argv.append("--force")
        return self.run_cli(argv)

    def _fm(self, rel):
        mod = load_cli()
        with open(os.path.join(self.kdir, rel), encoding="utf-8") as f:
            fields, _ = mod.split_frontmatter(f.read())
        return fields

    def _ids(self):
        return {e["id"] for e in self.read_index()["entries"]}

    # --- topic slug-normalization -------------------------------------------
    def test_free_text_topic_autoslugified(self):
        """The task's headline case: prose in, slug-safe file + id out."""
        code, _out, err = self._decide("some free text")
        self.assertEqual(code, 0, err)
        self.assertIn("normalized --topic", err)
        self.assertTrue(os.path.isfile(
            os.path.join(self.kdir, "decisions", "some-free-text.md")))
        self.assertIn("decide-some-free-text", self._ids())

    def test_no_spaces_survive_into_the_id_or_the_path(self):
        """The failure this closes was UNPROMOTABLE ids, not ugly ones.

        `worklog scan` slugifies a `> DECISION:` marker before matching it
        against entry stems, so an id or a path carrying a space can never be
        recognised as that marker's promotion.
        """
        self._decide("Adopt The Tier Runner, Not pytest")
        entry = next(e for e in self.read_index()["entries"]
                     if e["id"].startswith("decide-adopt"))
        self.assertNotIn(" ", entry["id"])
        self.assertNotIn(" ", entry["path"])
        self.assertEqual(entry["path"],
                         "decisions/adopt-the-tier-runner-not-pytest.md")

    def test_slug_topic_emits_no_notice(self):
        code, _out, err = self._decide("already-a-slug")
        self.assertEqual(code, 0, err)
        self.assertNotIn("normalized", err)
        self.assertIn("decide-already-a-slug", self._ids())

    def test_title_derives_from_the_original_topic(self):
        """Readability survives auto-slugify, as it does for `learn`."""
        mod = load_cli()
        code, _out, err = self._decide("iOS Signing Decision")
        self.assertEqual(code, 0, err)
        fm = self._fm("decisions/ios-signing-decision.md")
        self.assertEqual(fm["title"], mod._topic_to_title("iOS Signing Decision"))

    def test_topic_with_no_slug_chars_rejected(self):
        before = len(self.read_index()["entries"])
        code, _out, err = self._decide("!!!")
        self.assertNotEqual(code, 0)
        self.assertIn("no slug-safe characters", err)
        self.assertEqual(len(self.read_index()["entries"]), before)

    def test_whitespace_only_topic_rejected(self):
        before = len(self.read_index()["entries"])
        code, _out, err = self._decide("   ")
        self.assertNotEqual(code, 0)
        self.assertIn("--topic must not be empty", err)
        self.assertEqual(len(self.read_index()["entries"]), before)

    def test_index_validate_accepts_a_normalized_entry(self):
        import json
        self._decide("Free Text Topic Here")
        code, out, _err = self.run_cli(["index", "validate", "--format", "json"])
        payload = json.loads(out)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["errors"], [])
        self.assertEqual(code, 0)

    # --- near-duplicate probe ------------------------------------------------
    def test_near_duplicate_decision_blocked_without_force(self):
        c1, _o, e1 = self._decide("dup-alpha", summary="zorble calibration",
                                  options="calibrate | skip",
                                  choice="calibrate", rationale=DUP_RATIONALE)
        self.assertEqual(c1, 0, e1)
        c2, _o2, e2 = self._decide("dup-beta", summary="zorble calibration",
                                   options="calibrate | skip",
                                   choice="calibrate", rationale=DUP_RATIONALE)
        self.assertNotEqual(c2, 0, "a near-verbatim duplicate must be blocked")
        self.assertIn("near-duplicate", e2)
        self.assertIn("decide-dup-alpha", e2)
        self.assertIn("decision", e2)  # the noun names what was matched
        self.assertFalse(os.path.isfile(
            os.path.join(self.kdir, "decisions", "dup-beta.md")))
        self.assertNotIn("decide-dup-beta", self._ids())

    def test_near_duplicate_decision_allowed_with_force(self):
        self._decide("dup-alpha", summary="zorble calibration",
                     options="calibrate | skip", choice="calibrate",
                     rationale=DUP_RATIONALE)
        code, _o, err = self._decide("dup-beta", summary="zorble calibration",
                                     options="calibrate | skip",
                                     choice="calibrate",
                                     rationale=DUP_RATIONALE, force=True)
        self.assertEqual(code, 0, err)
        self.assertIn("decide-dup-beta", self._ids())
        self.assertTrue(os.path.isfile(
            os.path.join(self.kdir, "decisions", "dup-beta.md")))

    def test_distinct_decision_sharing_the_scaffold_not_blocked(self):
        """The guard must not blanket-block on the rendered template.

        Every decision carries the same Options/Choice/Rationale scaffold, and
        the probe is doc-shaped, so it SEES that scaffold. BM25's IDF is what
        keeps it from mattering — this is the test that says so behaviourally.
        """
        c1, _o, e1 = self._decide("dup-alpha", summary="zorble calibration",
                                  options="calibrate | skip",
                                  choice="calibrate", rationale=DUP_RATIONALE)
        self.assertEqual(c1, 0, e1)
        code, _o2, err = self._decide(
            "tier-runner", summary="run the suite as four tier discoveries",
            options="pytest | per-tier unittest discover",
            choice="per-tier unittest discover",
            rationale=("pytest is absent on a stock clone, so the documented "
                       "whole-suite command collects nothing before it fails"))
        self.assertEqual(code, 0, err)
        self.assertIn("decide-tier-runner", self._ids())

    def test_probe_is_scoped_to_decisions_not_learnings(self):
        """A learning is never the near-duplicate of a DECISION, or vice versa.

        The shared probe takes the category as an argument for this reason: the
        two categories answer different questions, and one restating the other is
        normal (a decision is often recorded alongside the learning it produced).
        """
        c1, _o, e1 = self._decide("cross-category", summary="zorble calibration",
                                  options="calibrate | skip",
                                  choice="calibrate", rationale=DUP_RATIONALE)
        self.assertEqual(c1, 0, e1)
        code, _o2, err = self.run_cli(
            ["learn", "--topic", "cross-category-learning",
             "--summary", "zorble calibration", "--tags", "x",
             "--content", DUP_RATIONALE])
        self.assertEqual(code, 0, err)
        self.assertIn("learn-cross-category-learning", self._ids())


if __name__ == "__main__":
    import unittest
    unittest.main()
