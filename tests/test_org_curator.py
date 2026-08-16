"""The CURATOR gate on shared-root writes, and the fleet payload contract keys.

Parity row: `org:curator-gate-on-shared-writes` (task 50). Neither half has a CLI
surface the generated parity tiers can see.

What is at stake — the curator gate
-----------------------------------
R-ORG-03. `shared/` is the org-wide corpus: an entry written there is recalled by
EVERY team, so it is the highest-blast-radius write in the system. The default
agent loop must not be able to reach it at all. Two conditions, both required:

  1. `--to-shared` was explicitly passed (the loop never passes it), and
  2. the resolved handle is listed as a curator in `shared/OWNERS.md`.

Either one alone is insufficient, and the reason is asymmetric. Drop (1) and
every routine team write starts landing org-wide. Drop (2) — the mutation this
row names — and the flag becomes self-service: any teammate who reads the help
text can write to the org corpus, which is the same as having no gate while
looking like one.

A flat (non-org) knowledge dir is ALWAYS allowed, unconditionally. That is INV-1:
a single-operator install has no `shared/` and no curators, and a gate that
engaged there would refuse ordinary writes on a KB that never opted into org
mode.

What is at stake — the payload contract keys
--------------------------------------------
`FLEET_TERMINAL_REQUIRED` is the key set every terminal loop event must carry
(R-ORG-06), and `steering_sha` is the causal join key the fleet report groups by.
A missing key does not crash anything — it produces an event that silently drops
out of every aggregate, so a run that failed is simply not counted. That is why
the contract is a named tuple that lint O12 validates rather than a convention.

Hermetic: a scratch org tree in a tempdir, stdlib unittest only.
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

from agentware_lib.core.orgkeys import ORG_ROOT_KEY, ORG_SHARED_DIR  # noqa: E402
from agentware_lib.ops.fleet.fleetguard import (  # noqa: E402
    _is_curator,
    guard_shared_write,
)
from agentware_lib.ops.fleet.fleetshard import FLEET_TERMINAL_REQUIRED  # noqa: E402

CURATOR = "carol"
TEAMMATE = "dave"


class _OrgTreeCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-org-curator-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for rel in ("teams/alpha", ORG_SHARED_DIR):
            os.makedirs(os.path.join(self.root, rel), exist_ok=True)
        self.shared = os.path.join(self.root, ORG_SHARED_DIR)
        self.alpha = os.path.join(self.root, "teams", "alpha")
        with open(os.path.join(self.shared, "OWNERS.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("# Curators\n\n| handle | role |\n|---|---|\n"
                     "| %s | curator |\n" % CURATOR)
        self._saved = {k: os.environ.get(k)
                       for k in (ORG_ROOT_KEY, "AGENTWARE_USER_HANDLE", "USER")}
        os.environ[ORG_ROOT_KEY] = self.root
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _as(self, handle):
        os.environ["AGENTWARE_USER_HANDLE"] = handle
        os.environ["USER"] = handle


class CuratorRosterTests(_OrgTreeCase):
    def test_a_listed_handle_is_a_curator(self):
        self.assertTrue(_is_curator(self.root, CURATOR))

    def test_an_unlisted_handle_is_not(self):
        self.assertFalse(_is_curator(self.root, TEAMMATE))

    def test_an_empty_handle_is_never_a_curator(self):
        """A resolver that returns nothing must not read as authorised — the
        no-handle case has to fail CLOSED."""
        self.assertFalse(_is_curator(self.root, None))
        self.assertFalse(_is_curator(self.root, ""))

    def test_a_missing_owners_file_authorises_nobody(self):
        """Tolerant read, closed default: an org with no OWNERS.md has no
        curators, rather than everyone being one."""
        os.remove(os.path.join(self.shared, "OWNERS.md"))
        self.assertFalse(_is_curator(self.root, CURATOR))


class SharedWriteGateTests(_OrgTreeCase):
    def test_a_team_write_is_always_allowed(self):
        self._as(TEAMMATE)
        ok, err = guard_shared_write(self.alpha)
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_a_flat_kb_write_is_always_allowed(self):
        """INV-1: a single-operator install has no shared/ and no curators, so
        the gate must not engage at all."""
        flat = tempfile.mkdtemp(prefix="aw-flat-curator-")
        self.addCleanup(shutil.rmtree, flat, ignore_errors=True)
        self._as(TEAMMATE)
        ok, err = guard_shared_write(flat)
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_the_default_team_loop_cannot_write_to_shared(self):
        """Condition 1: without --to-shared the write is refused even FOR a
        curator. The loop never passes the flag, so this is what keeps routine
        team knowledge out of the org corpus."""
        self._as(CURATOR)
        ok, err = guard_shared_write(self.shared, to_shared=False)
        self.assertFalse(ok)
        self.assertIn("shared/ write refused", err)
        self.assertIn("--to-shared", err)

    def test_a_non_curator_shared_write_is_refused(self):
        """Condition 2 — the property this row's mutation removes. With the flag
        set but a non-curator handle, the write must still be refused, or
        `--to-shared` is self-service and the gate is decorative."""
        self._as(TEAMMATE)
        ok, err = guard_shared_write(self.shared, to_shared=True)
        self.assertFalse(ok, "a non-curator wrote to the org shared corpus")
        self.assertIn("curator", err)
        self.assertIn(TEAMMATE, err)

    def test_a_curator_with_the_flag_is_allowed(self):
        """The positive control. Without it, a gate that refused EVERYTHING would
        pass both refusal tests above and prove nothing."""
        self._as(CURATOR)
        ok, err = guard_shared_write(self.shared, to_shared=True)
        self.assertTrue(ok, "the curator path is broken: %s" % err)
        self.assertIsNone(err)

    def test_both_conditions_are_required_independently(self):
        """The truth table, stated once so neither condition can quietly become
        sufficient on its own."""
        for handle, flag, expect in ((CURATOR, False, False),
                                     (TEAMMATE, True, False),
                                     (TEAMMATE, False, False),
                                     (CURATOR, True, True)):
            self._as(handle)
            ok, _err = guard_shared_write(self.shared, to_shared=flag)
            self.assertEqual(ok, expect,
                             "handle=%s to_shared=%s expected ok=%s"
                             % (handle, flag, expect))


class PayloadContractTests(unittest.TestCase):
    """The terminal-event key contract (R-ORG-06)."""

    def test_the_required_keys_are_the_documented_five(self):
        self.assertEqual(set(FLEET_TERMINAL_REQUIRED),
                         {"event", "ts", "outcome", "steering_sha", "team"})

    def test_steering_sha_is_in_the_contract(self):
        """The causal join key the fleet report groups by. An event without it
        does not crash anything — it silently drops out of every aggregate, so a
        failed run is simply never counted."""
        self.assertIn("steering_sha", FLEET_TERMINAL_REQUIRED)

    def test_team_is_in_the_contract(self):
        """R-ORG-05: without `team` a terminal event cannot be attributed, and a
        fleet report over a multi-team org silently merges them."""
        self.assertIn("team", FLEET_TERMINAL_REQUIRED)

    def test_the_contract_is_an_immutable_tuple(self):
        """A mutable contract is one a caller can extend at runtime, which makes
        the lint that validates against it unfalsifiable."""
        self.assertIsInstance(FLEET_TERMINAL_REQUIRED, tuple)


if __name__ == "__main__":
    unittest.main()
