"""Tests for robust `> LEARNED:` -> promoted-learning recognition
(feature 260625-worklog-promotion-matching-fix).

The promote-before-promise gate (R-SI-03) depends on `worklog scan` reliably
recognizing when a `> LEARNED:` marker has been promoted into a durable learning.
The historical heuristic was a brittle fuzzy SUBSTRING match between the slugified
marker prose and learning filename/id stems. That bit twice:

  * FALSE POSITIVE — a short marker slug (`> LEARNED: ci`) is a substring of an
    unrelated stem (`learn-ci-cache-warming`), so an un-promoted marker was
    silently passed.
  * FALSE NEGATIVE — a genuine promotion whose marker prose does not slug-contain
    the topic is missed, so the loop refuses to finish / re-promotes.

The fix introduces an explicit, deterministic promotion reference
`(Promoted: <id>)` resolved EXACTLY against the live index/files, and tightens the
no-reference fallback from substring containment to exact stem equality.

Invariants re-stated as executable assertions:
  * INV-1 deterministic, stdlib-only — same worklog + same index => byte-identical
    scan output (no LLM/RNG/network/wall-clock).
  * INV-2 read-only — `worklog scan` never mutates the KB; the index is untouched.

Written RED-first against the unmodified `_is_promoted`:
  python3 -m unittest tests.test_worklog_promotion -v
"""

import json
import os
import unittest

try:
    from tests._fixtures import load_cli, run_cli, build_synthetic_kb
except ImportError:  # when run via `discover -s tests`, tests/ is on sys.path
    from _fixtures import load_cli, run_cli, build_synthetic_kb

import shutil
import tempfile


# Distinctive synthetic learnings. `learn-ci-cache-warming` is the false-positive
# bait: its stem `ci-cache-warming` has the short token `ci` as a substring.
_LEARNINGS = [
    {
        "id": "learn-ci-cache-warming",
        "title": "CI Cache Warming",
        "category": "learnings",
        "path": "learnings/ci-cache-warming.md",
        "tags": ["ci", "cache"],
        "created": "2026-01-02",
        "summary": "Warm the CI cache before the build to cut cold-start time.",
        "body": "# CI Cache Warming\n\nWarm the cache before the build.\n",
    },
    {
        "id": "learn-geofence-reminders",
        "title": "Geofence Reminders Not Firing",
        "category": "learnings",
        "path": "learnings/geofence-reminders.md",
        "tags": ["geofence", "ios"],
        "created": "2026-01-03",
        "summary": "Why arrival geofence reminders never fired and the fixes.",
        "body": "# Geofence Reminders\n\nRegister geofences at launch.\n",
    },
]


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class _PromotionKB(unittest.TestCase):
    """Base: a synthetic KB seeded with the distinctive learnings above."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-test-promo-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.index_data = build_synthetic_kb(self.kdir, entries=_LEARNINGS)

    def _worklog(self, text):
        p = os.path.join(self.kdir, "wl.md")
        _write(p, text)
        return p

    def _scan(self, text):
        wl = self._worklog(text)
        code, out, err = run_cli(
            ["worklog", "scan", "--path", wl, "--format", "json"], self.kdir)
        return code, json.loads(out)

    def _unpromoted_texts(self, payload):
        return [item["text"] for item in payload["unpromoted"]]


class FalsePositiveTestCase(_PromotionKB):
    """An un-promoted short marker must NOT be matched by substring against an
    unrelated stem."""

    def test_short_slug_substring_is_not_promotion(self):
        # `ci` is a substring of stem `ci-cache-warming` — the legacy bug called
        # this promoted. It is NOT: there is no promotion reference.
        code, payload = self._scan("# wl\n\n> LEARNED: ci\n")
        self.assertIn(
            "ci", self._unpromoted_texts(payload),
            "un-promoted `> LEARNED: ci` must be flagged, not substring-matched "
            "against learn-ci-cache-warming")
        self.assertEqual(code, 1)


class FalseNegativeTestCase(_PromotionKB):
    """A genuine promotion carrying an explicit `(Promoted: <id>)` reference for a
    real index entry must be recognized even when the prose does not slug-contain
    the stem."""

    def test_explicit_reference_resolves_to_real_entry(self):
        marker = ("> LEARNED: arrival alerts stayed silent on device "
                  "(Promoted: learn-geofence-reminders)\n")
        code, payload = self._scan("# wl\n\n" + marker)
        self.assertEqual(
            payload["unpromoted"], [],
            "explicit (Promoted: learn-geofence-reminders) for a real entry "
            "must be recognized as promoted")
        self.assertEqual(code, 0)


class ReferenceIntegrityTestCase(_PromotionKB):
    """A reference to an id absent from BOTH the index and the files must be
    reported unpromoted — no fuzzy rescue."""

    def test_dangling_reference_is_unpromoted(self):
        marker = ("> LEARNED: some finding with a typo reference "
                  "(Promoted: learn-does-not-exist)\n")
        code, payload = self._scan("# wl\n\n" + marker)
        self.assertIn(
            "learn-does-not-exist",
            " ".join(self._unpromoted_texts(payload)),
            "a dangling (Promoted: <missing>) reference must fail the scan")
        self.assertEqual(code, 1)


class BackwardCompatTestCase(_PromotionKB):
    """Pre-existing promoted markers keep passing."""

    def test_no_reference_slug_equal_stem_is_promoted(self):
        # Legacy promoted marker whose prose slug EQUALS the learning stem.
        code, payload = self._scan("# wl\n\n> LEARNED: ci-cache-warming\n")
        self.assertEqual(payload["unpromoted"], [], payload)
        self.assertEqual(code, 0)

    def test_legacy_bracket_reference_is_promoted(self):
        # The decisions-style `[promoted -> <stem>]` reference still resolves.
        code, payload = self._scan(
            "# wl\n\n> LEARNED: [promoted -> geofence-reminders] arrival fix\n")
        self.assertEqual(payload["unpromoted"], [], payload)
        self.assertEqual(code, 0)


class PromotedRegexTestCase(unittest.TestCase):
    """Task 2: PROMOTED_RE / promoted_ids extract the referenced id exactly."""

    def setUp(self):
        self.mod = load_cli()

    def test_promoted_re_extracts_learn_id(self):
        rx = getattr(self.mod, "PROMOTED_RE", None)
        self.assertIsNotNone(rx, "scripts/agentware must define PROMOTED_RE")
        m = rx.search("x (Promoted: learn-foo-bar)")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).lower(), "learn-foo-bar")

    def test_promoted_ids_helper(self):
        fn = getattr(self.mod, "promoted_ids", None)
        self.assertIsNotNone(fn, "scripts/agentware must define promoted_ids")
        self.assertEqual(
            fn("noise (Promoted: learn-foo-bar) tail"), ["learn-foo-bar"])
        self.assertEqual(fn("no reference here"), [])


class DeterminismReadOnlyTestCase(_PromotionKB):
    """INV-1 determinism + INV-2 read-only."""

    def _index_bytes(self):
        with open(os.path.join(self.kdir, "index.json"), "rb") as f:
            return f.read()

    def test_scan_is_byte_identical_and_read_only(self):
        wl = self._worklog(
            "# wl\n\n> LEARNED: ci\n"
            "> LEARNED: arrival fix (Promoted: learn-geofence-reminders)\n")
        before = self._index_bytes()
        c1, o1, _ = run_cli(
            ["worklog", "scan", "--path", wl, "--format", "json"], self.kdir)
        c2, o2, _ = run_cli(
            ["worklog", "scan", "--path", wl, "--format", "json"], self.kdir)
        self.assertEqual(o1, o2, "INV-1: scan output must be byte-identical")
        self.assertEqual(c1, c2)
        self.assertEqual(
            before, self._index_bytes(), "INV-2: scan must not mutate the index")


class ScanShapeTestCase(_PromotionKB):
    """Task 7: the `{unpromoted,total}` JSON contract is preserved."""

    def test_json_shape_and_total_count(self):
        # Three markers: one promoted (reference), two unpromoted.
        code, payload = self._scan(
            "# wl\n\n"
            "> LEARNED: ci\n"
            "> LEARNED: arrival fix (Promoted: learn-geofence-reminders)\n"
            "> LEARNED: another dangling (Promoted: learn-missing)\n")
        self.assertIn("unpromoted", payload)
        self.assertIn("total", payload)
        self.assertEqual(payload["total"], 3)
        self.assertEqual(len(payload["unpromoted"]), 2)
        self.assertEqual(code, 1)


class AlsoAssessmentTestCase(_PromotionKB):
    """260710-learning-repair-p1 task 6: --also-assessment flags Extracted-
    Knowledge 'Suggested ID' lines in the sibling assessment.md whose ids do not
    resolve in the live index. Advisory: never changes the exit code (that is
    still governed by worklog markers)."""

    def _write_assessment(self, text):
        p = os.path.join(self.kdir, "assessment.md")
        _write(p, text)
        return p

    def _scan_ax(self, extra_argv):
        # Clean worklog (marker slug == a real stem => promoted) so any exit-code
        # effect must come from the assessment sidecar, not the worklog.
        wl = self._worklog("# wl\n\n> LEARNED: ci-cache-warming\n")
        code, out, _ = run_cli(
            ["worklog", "scan", "--path", wl, "--format", "json"] + extra_argv,
            self.kdir)
        return code, json.loads(out)

    def test_flag_absent_field_omitted(self):
        self._write_assessment(
            "## Extracted Knowledge\n- **Suggested ID:** `learn-does-not-exist`\n")
        code, payload = self._scan_ax([])
        self.assertNotIn("assessment_unpromoted", payload)
        self.assertEqual(set(payload), {"unpromoted", "total"})
        self.assertEqual(code, 0)

    def test_unresolved_ids_flagged_both_markdown_styles(self):
        self._write_assessment(
            "## Extracted Knowledge\n\n"
            "### EK-1\n- **Suggested ID:** `learn-does-not-exist`\n"
            "### EK-2\n- **Suggested ID**: `learn-geofence-reminders` "
            "*(already promoted)*\n"
            "### EK-3\n- **Suggested ID**: `learn-another-missing`\n")
        code, payload = self._scan_ax(["--also-assessment"])
        self.assertIn("assessment_unpromoted", payload)
        ids = [i["id"] for i in payload["assessment_unpromoted"]]
        self.assertEqual(ids, ["learn-does-not-exist", "learn-another-missing"])
        self.assertNotIn("learn-geofence-reminders", ids)
        self.assertEqual(code, 0)

    def test_missing_assessment_yields_empty_list(self):
        code, payload = self._scan_ax(["--also-assessment"])
        self.assertEqual(payload.get("assessment_unpromoted"), [])
        self.assertEqual(code, 0)

    def test_line_number_reported(self):
        self._write_assessment(
            "intro line\n- **Suggested ID:** `learn-does-not-exist`\n")
        _code, payload = self._scan_ax(["--also-assessment"])
        item = payload["assessment_unpromoted"][0]
        self.assertEqual(item["line"], 2)
        self.assertEqual(item["id"], "learn-does-not-exist")

    def test_read_only_and_deterministic(self):
        self._write_assessment(
            "## Extracted Knowledge\n- **Suggested ID:** `learn-does-not-exist`\n")
        idx = os.path.join(self.kdir, "index.json")
        with open(idx, "rb") as f:
            before = f.read()
        c1, p1 = self._scan_ax(["--also-assessment"])
        c2, p2 = self._scan_ax(["--also-assessment"])
        self.assertEqual(p1, p2)
        self.assertEqual(c1, c2)
        with open(idx, "rb") as f:
            self.assertEqual(before, f.read())


class Task2MarkerFormsTestCase(_PromotionKB):
    """260710-learning-repair-p1 task 2: the scanner must recognize bullet-prefixed
    markers, the unicode-arrow / `[kb:]` promotion forms, cross-category (skill)
    references, and `> WAIVED:` closures."""

    def test_bullet_prefixed_marker_is_counted(self):
        # The exact form that scanned as 0 in the wild (`- > LEARNED:`).
        code, payload = self._scan("# wl\n\n- > LEARNED: ci\n")
        self.assertEqual(payload["total"], 1,
                         "bullet-prefixed marker must be counted")
        self.assertIn("ci", self._unpromoted_texts(payload))
        self.assertEqual(code, 1)

    def test_bullet_prefixed_marker_promoted_by_slug(self):
        code, payload = self._scan("# wl\n\n- > LEARNED: ci-cache-warming\n")
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["unpromoted"], [], payload)
        self.assertEqual(code, 0)

    def test_unicode_arrow_reference_is_promoted(self):
        code, payload = self._scan(
            "# wl\n\n> LEARNED: [promoted → geofence-reminders] arrival fix\n")
        self.assertEqual(payload["unpromoted"], [], payload)
        self.assertEqual(code, 0)

    def test_kb_bracket_reference_is_promoted(self):
        code, payload = self._scan(
            "# wl\n\n> LEARNED: arrival fix [kb: learn-geofence-reminders]\n")
        self.assertEqual(payload["unpromoted"], [], payload)
        self.assertEqual(code, 0)

    def test_waived_line_closes_preceding_marker(self):
        code, payload = self._scan(
            "# wl\n\n"
            "- > LEARNED: something trivial not worth keeping\n"
            "> WAIVED: too trivial to promote\n")
        self.assertEqual(payload["total"], 1, "marker still counted in total")
        self.assertEqual(payload["unpromoted"], [],
                         "a waived marker is not counted unpromoted")
        self.assertEqual(code, 0)

    def test_cross_category_skill_reference_is_promoted(self):
        # A `> LEARNED:` marker promoted INTO a skill resolves against the skill
        # id even though skills are a different category.
        _write(os.path.join(self.kdir, "skills", "cache-warming.md"),
               "# Cache Warming Skill\n")
        code, payload = self._scan(
            "# wl\n\n> LEARNED: generalized the tip (Promoted: skill-cache-warming)\n")
        self.assertEqual(payload["unpromoted"], [], payload)
        self.assertEqual(code, 0)

    def test_unicode_arrow_regex_extracts_id(self):
        mod = load_cli()
        m = mod.PROMOTED_RE.search("x [promoted → learn-foo-bar]")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).lower(), "learn-foo-bar")
        self.assertEqual(mod.promoted_ids("y [kb: decide-baz]"), ["decide-baz"])


class MarkerBulletFormTestCase(_PromotionKB):
    """A LIST-BULLETED marker must still BLOCK the completion gate.

    Regression guard for task 45. The lib's regexes had dropped the monolith's
    optional `[-*+]` bullet prefix, so a marker written as a list item — the
    natural form inside a worklog's bulleted section — did not match. The scanner
    then reported zero unpromoted markers and the promise went through: the gate
    FAILED OPEN and an unpromoted learning was silently lost.

    Asserted through the real `worklog scan` verb rather than against the regex,
    so it guards the BEHAVIOUR (does an unpromoted marker block?) and not an
    implementation detail that could be satisfied while the gate still passes.
    """

    def test_bulleted_unpromoted_marker_blocks_the_gate(self):
        for line in ("- > LEARNED: a dash-bulleted discovery",
                     "* > DECISION: a star-bulleted choice",
                     "+ LEARNED: a plus bullet with no quote",
                     "  - > DECISION: an indented bullet"):
            code, payload = self._scan("# wl\n\n%s\n" % line)
            self.assertNotEqual(
                code, 0,
                "marker form did not block the gate (FAILS OPEN): %r" % line)
            self.assertTrue(
                self._unpromoted_texts(payload),
                "marker form was not even counted: %r" % line)

    def test_bulleted_marker_clears_once_promoted(self):
        code, payload = self._scan(
            "# wl\n\n- > LEARNED: warm the cache "
            "(Promoted: learn-ci-cache-warming)\n")
        self.assertEqual(code, 0, payload)
        self.assertFalse(self._unpromoted_texts(payload))

    def test_prose_mentioning_the_word_is_not_a_marker(self):
        code, payload = self._scan(
            "# wl\n\nI LEARNED: something in passing\n"
            "the DECISION: was taken elsewhere\n")
        self.assertEqual(
            code, 0,
            "prose matched as a marker -> the gate would FAIL CLOSED: %s" % payload)


if __name__ == "__main__":
    unittest.main()
