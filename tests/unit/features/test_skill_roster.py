"""Task 5 — skills two-tier: session-start roster status-line function.
Fixed-format, sanitized (names only, no author), empty -> byte-identical no-op.
Hermetic, stdlib-only.
"""

import contextlib
import io
import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import load_cli
except ImportError:  # pragma: no cover
    from _fixtures import load_cli


class SkillRosterTest(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        self.tmp = tempfile.mkdtemp(prefix="aw-roster-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _line(self, lock):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.cli._skill_status_line(lock)
        return rc, buf.getvalue()

    def test_empty_lock_prints_nothing(self):
        rc, out = self._line({"schema": 1, "skills": {}})
        self.assertEqual(rc, 0)
        self.assertEqual(out, "", "empty lockfile roster must be a byte-identical no-op")

    def test_absent_lock_prints_nothing(self):
        rc, out = self._line(self.cli.load_skill_lock(os.path.join(self.tmp, "nope.json")))
        self.assertEqual(out, "")

    def test_only_blocked_prints_nothing(self):
        # A lockfile with ONLY blocked entries surfaces neither installed nor
        # pending -> still a no-op.
        rc, out = self._line({"schema": 1, "skills": {
            "bad": {"status": "blocked", "verdict": "BLOCK"}}})
        self.assertEqual(out, "")

    def test_installed_and_pending_named(self):
        lock = {"schema": 1, "skills": {
            "alpha": {"status": "installed", "verdict": "PASS"},
            "beta": {"status": "quarantined", "verdict": "PASS"},
            "bad": {"status": "blocked", "verdict": "BLOCK"},
        }}
        rc, out = self._line(lock)
        self.assertEqual(rc, 0)
        self.assertIn("1 installed", out)
        self.assertIn("1 pending-approval", out)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)
        self.assertIn("skill sync --approve", out)
        self.assertNotIn("bad", out)  # blocked never surfaced in the status-line

    def test_no_author_or_raw_metadata_leaks(self):
        # Even a stored (already-sanitized) author must not appear in the roster.
        lock = {"schema": 1, "skills": {
            "alpha": {"status": "installed", "verdict": "PASS",
                      "authored_by": "attacker <e@x>",
                      "reason": "some reason text"}}}
        rc, out = self._line(lock)
        self.assertNotIn("attacker", out)
        self.assertNotIn("reason", out)
        self.assertNotIn("authored", out)

    def test_non_conforming_name_dropped(self):
        # Defense-in-depth: a name that fails the allowlist is dropped from the roster.
        lock = {"schema": 1, "skills": {
            "ok-name": {"status": "installed", "verdict": "PASS"},
            "BAD Name": {"status": "installed", "verdict": "PASS"},
        }}
        rc, out = self._line(lock)
        self.assertIn("ok-name", out)
        self.assertNotIn("BAD Name", out)
        self.assertIn("1 installed", out)  # only the conforming one counted


if __name__ == "__main__":
    unittest.main()
