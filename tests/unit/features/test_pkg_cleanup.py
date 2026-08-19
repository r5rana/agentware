"""Dead-code + docstring cleanup guards (feature 260712-p2b-hardening-followups,
Task 5 — audit F12/F10).

  F12 — `PKG_UPDATE_GATES` was an orphaned constant (zero live consumers) whose
        `eval --record --gate` entry is exactly what the client apply path is
        DESIGNED never to run (client gate = SMOKE ONLY). It is deleted; this test
        asserts it is gone and not referenced anywhere as a live gate.
  F10 — the `cmd_update` docstring called `--check` "READ-ONLY (safe for a
        SessionStart nudge)", but `--check` performs network fetches. The docstring
        must no longer claim SessionStart-safety for `--check`; that belongs only to
        the genuinely no-network `--status-line` mode.

Pure introspection over the loaded CLI module + its source — no fixture, no network.
`test_pkgcleanup_` is a unique -k prefix across the suite.
Runner: python3 -m unittest tests.unit.features.test_pkg_cleanup -v
"""

import os
import re
import unittest

try:
    from tests._fixtures import cli_source, load_cli
except ImportError:
    from _fixtures import cli_source, load_cli


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_cli()
        # The IMPLEMENTATION source, not the entrypoint path: task 25 moved the
        # monolith body to scripts/agentware_legacy.py behind a thin facade.
        self.source = cli_source()

    # --- F12: dead PKG_UPDATE_GATES removed --------------------------------
    def test_pkgcleanup_pkg_update_gates_attr_gone(self):
        self.assertFalse(hasattr(self.mod, "PKG_UPDATE_GATES"),
                         "PKG_UPDATE_GATES is dead code and must be removed")

    def test_pkgcleanup_pkg_update_gates_not_in_source(self):
        # Not defined AND not referenced anywhere (a live gate would re-introduce
        # `eval --record --gate` on the client apply path, which is forbidden).
        self.assertNotIn("PKG_UPDATE_GATES", self.source)

    def test_pkgcleanup_client_apply_is_smoke_only(self):
        # The design the dead constant contradicted: the client apply gate is smoke
        # only — no `eval --record --gate` in the apply sequence.
        self.assertIn("client apply gate = SMOKE ONLY", self.source)

    # --- F10: --check docstring no longer claims SessionStart-safety --------
    def test_pkgcleanup_check_docstring_not_sessionstart_safe(self):
        doc = self.mod.cmd_update.__doc__ or ""
        self.assertNotIn("safe for a SessionStart nudge", doc,
                         "`--check` fetches from the remote — it is NOT SessionStart-safe")
        # It must state --check fetches / is not for SessionStart.
        low = doc.lower()
        self.assertIn("--check", doc)
        self.assertTrue("fetch" in low,
                        "the docstring must state that --check fetches (network)")
        self.assertIn("--status-line", doc,
                      "the no-network SessionStart mode (--status-line) must be named")

    def test_pkgcleanup_status_line_is_the_no_network_mode(self):
        # The genuinely no-network SessionStart handler keeps the safe wording.
        m = re.search(r"NO-NETWORK[^\n]*SessionStart", self.source)
        self.assertIsNotNone(
            m, "the --status-line handler should remain the NO-NETWORK SessionStart mode")


if __name__ == "__main__":
    unittest.main()
