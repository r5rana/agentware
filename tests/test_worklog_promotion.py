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


if __name__ == "__main__":
    unittest.main()
