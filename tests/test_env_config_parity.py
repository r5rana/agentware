#!/usr/bin/env python3
"""Freeze the ENV-VAR / CONFIG-KEY parity fixture and prove its gates bite.

Feature 260813-modular-core-adoption, task 8.

Every claim here carries a NEGATIVE proof: a check that cannot redden is not a
gate, it is decoration. The fixture is the evidence the later ports (37-56) and
the parity manifest (10, 79) are measured against, so a scanner that silently
drops a key -- or a gate that passes a tree missing one -- would let a kill
switch, a config-home override or the whole org kernel vanish without a single
red test.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _env_config_capture as cap          # noqa: E402
import _env_config_scan as scan            # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_PATH = os.path.join(REPO, "tests", "fixtures", "env-config-parity.json")
UNION_PATH = os.path.join(REPO, "tests", "fixtures", "surface-union.json")
DONOR = cap.DEFAULT_DONOR_ROOT

# Frozen: the six kill switches across both trees. A seventh appearing without
# a decision, or one of these disappearing, must fail here.
KILL_SWITCHES = frozenset({
    "AGENTWARE_DISABLE_PREMISE_GATE",
    "AGENTWARE_DISABLE_RECALL_GATE",
    "AGENTWARE_DISABLE_RELEASE_GATE",
    "AGENTWARE_DISABLE_RETRIEVAL_LINT",
    "AGENTWARE_DISABLE_TARGET_GATE",
    "AGENTWARE_SKIP_ADVERSARIAL_REVIEW",
})

# Frozen: the donor-only backlog this migration must close. Every one of these
# is a capability the destination tree does NOT read today.
DONOR_ONLY_KEYS = frozenset({
    "AGENTWARE_CONFIG_HOME",
    "AGENTWARE_DISABLE_RECALL_GATE",
    "AGENTWARE_ORG_PACKAGE",
    "AGENTWARE_ORG_ROOT",
    "AGENTWARE_REPOINT_AUTOPROVISION",
    "AGENTWARE_SKILL_PROPOSE_THRESHOLD",
    "AGENTWARE_TEAM",
    "CLAUDE_TOOL_INPUT",
    "CLAUDE_TOOL_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_AUTHOR_NAME",
    "USER",
})

# The nine keys `_phase_key()` builds and that appear as a literal NOWHERE.
PHASE_KEYS = frozenset(
    "AGENTWARE_%s_%s" % (phase, axis)
    for phase in ("PRE", "MAIN", "POST")
    for axis in ("CLI", "MODEL", "LOCAL")
)


def load():
    with open(FIXTURE_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def rows_by_key(fixture):
    return {row["key"]: row for row in fixture["keys"]}


def write_tree(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


class FixtureShapeTest(unittest.TestCase):
    """The fixture is valid, complete and self-consistent."""

    def setUp(self):
        self.fixture = load()
        self.rows = rows_by_key(self.fixture)

    def test_valid_json_and_schema_version(self):
        self.assertEqual(self.fixture["schema_version"], cap.SCHEMA_VERSION)
        self.assertTrue(self.fixture["binding"])
        self.assertEqual(self.fixture["task"], 8)

    def test_verify_passes(self):
        self.assertEqual(cap.verify(self.fixture), [])

    def test_at_least_36_side_attributed_keys(self):
        attributed = [r for r in self.fixture["keys"] if r["sides"]]
        self.assertGreaterEqual(len(attributed), 36)
        self.assertEqual(len(attributed), len(self.fixture["keys"]))

    def test_every_row_carries_default_flag_and_read_site_fields(self):
        for row in self.fixture["keys"]:
            for field in ("default", "flag", "read_sites", "write_sites",
                          "side", "sides", "config_key", "kill_switch"):
                self.assertIn(field, row, row["key"])
            sites = (row["read_sites"]["local"] + row["read_sites"]["donor"]
                     + row["write_sites"]["local"] + row["write_sites"]["donor"])
            self.assertTrue(sites, "%s has no site at all" % row["key"])
            for site in sites:
                for field in ("file", "line", "lang", "form", "kind"):
                    self.assertIn(field, site, row["key"])

    def test_counts_match_the_rows(self):
        self.assertEqual(self.fixture["counts"], cap._counts(self.fixture["keys"]))

    def test_sides_agree_with_the_recorded_sites(self):
        for row in self.fixture["keys"]:
            for side in ("local", "donor"):
                has = bool(row["read_sites"][side] or row["write_sites"][side])
                self.assertEqual(has, side in row["sides"], row["key"])

    def test_no_operator_home_path_leaks(self):
        with open(FIXTURE_PATH, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn(os.path.expanduser("~"), text)
        self.assertIn("~/", self.fixture["derived_from"]["local"]["root"])

    def test_a_count_contradicting_the_body_reddens(self):
        broken = copy.deepcopy(self.fixture)
        broken["counts"]["keys"] += 1
        problems = cap.verify(broken)
        self.assertTrue(any("counts.keys" in p for p in problems), problems)

    def test_a_row_with_no_site_reddens(self):
        broken = copy.deepcopy(self.fixture)
        broken["keys"][0]["read_sites"] = {"local": [], "donor": []}
        broken["keys"][0]["write_sites"] = {"local": [], "donor": []}
        problems = cap.verify(broken)
        self.assertTrue(any("carries no site" in p for p in problems), problems)

    def test_too_few_keys_reddens(self):
        broken = copy.deepcopy(self.fixture)
        broken["keys"] = broken["keys"][:5]
        broken["counts"] = cap._counts(broken["keys"])
        problems = cap.verify(broken)
        self.assertTrue(any("side-attributed keys" in p for p in problems), problems)


class KillSwitchTest(unittest.TestCase):
    """Kill switches are present, explicit and fail-safe."""

    def setUp(self):
        self.fixture = load()
        self.rows = rows_by_key(self.fixture)

    def test_the_six_kill_switches_are_frozen(self):
        found = {r["key"] for r in self.fixture["keys"] if r["kill_switch"]}
        self.assertEqual(found, set(KILL_SWITCHES))

    def test_each_kill_switch_has_an_explicit_fail_safe_default(self):
        for key in KILL_SWITCHES:
            row = self.rows[key]
            self.assertTrue(row["has_explicit_default"], key)
            self.assertIn(row["default"], cap.FAIL_SAFE_DEFAULTS, key)
            self.assertEqual(row["unset_means"], "gate enabled (fail-safe)")

    def test_the_reason_companion_is_not_itself_a_switch(self):
        row = self.rows["AGENTWARE_SKIP_ADVERSARIAL_REVIEW_REASON"]
        self.assertFalse(row["kill_switch"])

    def test_the_registry_declared_switches_carry_a_registry_site(self):
        # The four merge switches are read by iterating GRAPH_KILL_SWITCH_VARS,
        # so no literal appears at a .get() site. Without the registry rule they
        # would be invisible to the scanner entirely.
        row = self.rows["AGENTWARE_SKIP_ADVERSARIAL_REVIEW"]
        forms = {s["form"] for s in row["read_sites"]["local"]}
        self.assertTrue(any(f.startswith("registry:") for f in forms), forms)

    def test_a_kill_switch_without_a_default_reddens(self):
        broken = copy.deepcopy(self.fixture)
        rows_by_key(broken)["AGENTWARE_DISABLE_RELEASE_GATE"].update(
            {"has_explicit_default": False, "default": None,
             "defaults_observed": []})
        problems = cap.check_kill_switches(broken)
        self.assertTrue(any("no explicit default" in p and
                            "DISABLE_RELEASE_GATE" in p for p in problems),
                        problems)

    def test_a_kill_switch_defaulting_truthy_reddens(self):
        broken = copy.deepcopy(self.fixture)
        rows_by_key(broken)["AGENTWARE_DISABLE_TARGET_GATE"]["default"] = "1"
        problems = cap.check_kill_switches(broken)
        self.assertTrue(any("not fail-safe" in p for p in problems), problems)

    def test_a_fixture_with_no_kill_switch_at_all_reddens(self):
        broken = copy.deepcopy(self.fixture)
        for row in broken["keys"]:
            row["kill_switch"] = False
        problems = cap.check_kill_switches(broken)
        self.assertIn("no kill-switch key recorded at all", problems)


class DestinationGateTest(unittest.TestCase):
    """Every key resolves in the destination tree, or is a pending donor-only."""

    def setUp(self):
        self.fixture = load()

    def test_gate_passes_on_the_live_destination_tree(self):
        problems, _ = cap.check_destination(self.fixture, REPO)
        self.assertEqual(problems, [])

    def test_the_pending_backlog_is_exactly_the_donor_only_keys(self):
        """The backlog is exactly the frozen donor keys that are STILL donor-only.

        DONOR_ONLY_KEYS is the record of the backlog as captured at task 8, and
        the migration exists to close it: task 25 landed
        `scripts/agentware_lib/**`, which gave six of the twelve a real read site
        in this tree. So the invariant is "pending == the open subset", measured
        against each row's live side rather than restated as a literal — a set
        pinned by hand would have to be edited on every closure, which is the
        edit that hides a key going missing.
        """
        rows = rows_by_key(self.fixture)
        still_open = {k for k in DONOR_ONLY_KEYS
                      if rows[k]["side"] == "donor-only"}
        self.assertEqual(set(self.fixture["pending_in_destination"]), still_open)

    def test_the_backlog_only_shrinks_and_only_by_landing_the_capability(self):
        """Both directions. A key may leave the backlog ONLY by becoming
        readable here — so a row cannot be deleted to buy green — and no key may
        ever ENTER it that task 8 did not record."""
        pending = set(self.fixture["pending_in_destination"])
        rows = rows_by_key(self.fixture)
        self.assertTrue(pending <= set(DONOR_ONLY_KEYS),
                        "a key entered the backlog that task 8 never recorded: "
                        "%s" % sorted(pending - set(DONOR_ONLY_KEYS)))
        closed = set(DONOR_ONLY_KEYS) - pending
        self.assertTrue(closed, "task 25 closed six of these; none did")
        for key in closed:
            self.assertEqual(rows[key]["side"], "shared", key)
            # Donor provenance must survive the closure.
            self.assertIn("donor", rows[key]["sides"], key)
            self.assertTrue(rows[key]["read_sites"]["local"], key)

    def test_a_lost_key_reddens_the_gate_by_name(self):
        broken = copy.deepcopy(self.fixture)
        broken["keys"].append(dict(broken["keys"][0], key="AGENTWARE_NEVER_READ"))
        problems, _ = cap.check_destination(broken, REPO)
        self.assertTrue(any("LOST" in p and "AGENTWARE_NEVER_READ" in p
                            for p in problems), problems)

    def test_a_resolved_key_left_in_the_ratchet_reddens(self):
        broken = copy.deepcopy(self.fixture)
        broken["pending_in_destination"].append("AGENTWARE_KNOWLEDGE_DIR")
        problems, _ = cap.check_destination(broken, REPO)
        self.assertTrue(any("RATCHET" in p and "KNOWLEDGE_DIR" in p
                            for p in problems), problems)

    def test_removing_a_read_site_from_the_tree_reddens(self):
        # The gate must be able to see a key vanish from real source, not just
        # from a hand-edited fixture.
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, "scripts/thing.py",
                       'import os\nx = os.environ.get("AGENTWARE_KB_MODE", "")\n')
            fixture = {"keys": [{"key": "AGENTWARE_KB_MODE"},
                                {"key": "AGENTWARE_KB_PUSH"}],
                       "pending_in_destination": []}
            problems, unresolved = cap.check_destination(fixture, tmp)
            self.assertEqual(unresolved, ["AGENTWARE_KB_PUSH"])
            self.assertTrue(any("LOST" in p and "KB_PUSH" in p for p in problems),
                            problems)

    @unittest.skipUnless(os.path.isdir(DONOR), "donor tree absent")
    def test_the_gate_reddens_against_the_donor_tree_too(self):
        # Symmetry: a gate built from one side is blind to the other's losses.
        problems, _ = cap.check_destination(self.fixture, DONOR)
        self.assertTrue(problems)
        self.assertTrue(any("AGENTWARE_UPDATE" in p or "AGENTWARE_CHECKUP" in p
                            for p in problems), problems[:5])


class ScannerSemanticsTest(unittest.TestCase):
    """A MENTION is not a READ, and a local is not an environment variable."""

    def setUp(self):
        self.rows = rows_by_key(load())

    def test_printed_prefixes_are_not_keys(self):
        # "AGENTWARE_STATUS: ..." is a printed line prefix; "AGENTWARE_SKILLS:"
        # is a report header. A name-grep finds both; neither is a read.
        self.assertNotIn("AGENTWARE_STATUS", self.rows)
        self.assertNotIn("AGENTWARE_SKILLS", self.rows)

    def test_shell_locals_and_specials_are_not_keys(self):
        for name in ("line", "feature", "i", "BASH_SOURCE", "DOCS_DIR", "KDIR",
                     "LOOP_OUTCOME", "outcome"):
            self.assertNotIn(name, self.rows, name)

    def test_a_non_env_dict_get_is_not_a_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, "scripts/a.py",
                       'payload = {}\n'
                       'a = payload.get("outcome")\n'
                       'import os\n'
                       'b = os.environ.get("HOSTNAME")\n'
                       'def f(env):\n'
                       '    return env.get("AGENTWARE_REVIEW_EFFORT")\n')
            keys = scan.capture_tree(tmp)
            self.assertNotIn("outcome", keys)
            self.assertIn("HOSTNAME", keys)
            self.assertIn("AGENTWARE_REVIEW_EFFORT", keys)

    def test_an_assigned_shell_name_is_a_global_not_an_env_var(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, "agentware.sh",
                       'DOCS_DIR="/tmp/x"\n'
                       'echo "$DOCS_DIR ${AGENTWARE_KB_MODE:-power} $EDITOR"\n')
            keys = scan.capture_tree(tmp)
            self.assertNotIn("DOCS_DIR", keys)
            self.assertIn("AGENTWARE_KB_MODE", keys)
            self.assertIn("EDITOR", keys)

    def test_shell_default_is_extracted_from_the_expansion(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, "agentware.sh",
                       'echo "${AGENTWARE_NOPROGRESS_LIMIT:-3}"\n')
            keys = scan.capture_tree(tmp)
            self.assertEqual(keys["AGENTWARE_NOPROGRESS_LIMIT"][0]["default"], "3")

    def test_a_commented_out_read_is_not_a_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, "agentware.sh",
                       '# echo "${AGENTWARE_ONLY_IN_A_COMMENT:-}"\n'
                       'echo hi  # ${AGENTWARE_TRAILING_COMMENT:-}\n')
            keys = scan.capture_tree(tmp)
            self.assertNotIn("AGENTWARE_ONLY_IN_A_COMMENT", keys)
            self.assertNotIn("AGENTWARE_TRAILING_COMMENT", keys)

    def test_a_registry_tuple_declares_its_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, "scripts/a.py",
                       'SWITCHES = ("AGENTWARE_DISABLE_X", "AGENTWARE_DISABLE_Y")\n'
                       'def f(env):\n'
                       '    return [v for v in SWITCHES if env.get(v)]\n')
            keys = scan.capture_tree(tmp)
            self.assertIn("AGENTWARE_DISABLE_X", keys)
            self.assertEqual(keys["AGENTWARE_DISABLE_Y"][0]["form"],
                             "registry:SWITCHES")


class DynamicFamilyTest(unittest.TestCase):
    """Keys built by string formatting exist and are gated like any other."""

    def setUp(self):
        self.fixture = load()
        self.rows = rows_by_key(self.fixture)

    def test_all_nine_phase_keys_are_present_and_shared(self):
        for key in PHASE_KEYS:
            row = self.rows[key]
            self.assertEqual(row["side"], "shared", key)
            self.assertTrue(row["dynamic"], key)
            self.assertTrue(row["config_key"], key)
            self.assertTrue(row["flag"], key)

    def test_no_phase_key_appears_as_a_literal_in_either_tree(self):
        # The point of the expansion: a literal scanner is blind to these.
        with open(os.path.join(REPO, "scripts", "agentware"),
                  encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        self.assertNotIn('"AGENTWARE_MAIN_MODEL"', text)
        self.assertNotIn("'AGENTWARE_MAIN_MODEL'", text)

    def test_the_expansion_records_its_evidence(self):
        spec = self.fixture["dynamic_expansions"]["AGENTWARE_%s_%s"]
        self.assertIn("PHASES", spec["evidence"])
        self.assertEqual(spec["domains"],
                         [["PRE", "MAIN", "POST"], ["CLI", "MODEL", "LOCAL"]])

    def test_an_unadjudicated_template_halts_the_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, "scripts/a.py",
                       'def k(x):\n    return "AGENTWARE_%s_UNKNOWN" % x\n')
            with self.assertRaises(SystemExit) as ctx:
                scan.capture_tree(tmp)
            self.assertIn("AGENTWARE_%s_UNKNOWN", str(ctx.exception))

    def test_prose_containing_a_key_name_is_not_a_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tree(tmp, "scripts/a.py",
                       'def m(v, p):\n'
                       '    return "AGENTWARE_REVIEW_EFFORT=%r is BELOW %s." % (v, p)\n')
            self.assertEqual(scan.capture_tree(tmp), {})


class FlagMappingTest(unittest.TestCase):
    """Whether a key has a CLI flag, resolved against the task-7 union fixture."""

    def setUp(self):
        self.rows = rows_by_key(load())

    def test_a_derived_flag_resolves(self):
        row = self.rows["AGENTWARE_DREAM"]
        self.assertEqual(row["flag"], "set_dream")
        self.assertEqual(row["flag_source"], "derived")
        self.assertIn("dream_only", row["flag_dests"])

    def test_a_curated_alias_resolves(self):
        for key, flag in (("AGENTWARE_KB_AUTOCOMMIT", "set_autocommit"),
                          ("AGENTWARE_KB_PUSH", "set_push"),
                          ("AGENTWARE_UPDATE", "set_updater")):
            row = self.rows[key]
            self.assertEqual(row["flag"], flag, key)
            self.assertEqual(row["flag_source"], "curated", key)

    def test_a_key_with_no_flag_records_none(self):
        for key in ("AGENTWARE_DISABLE_RELEASE_GATE", "AGENTWARE_INVOKED_FROM",
                    "AGENTWARE_STAGE"):
            self.assertIsNone(self.rows[key]["flag"], key)

    def test_the_org_keys_carry_their_donor_flags(self):
        for key, flag in (("AGENTWARE_ORG_ROOT", "set_org_root"),
                          ("AGENTWARE_TEAM", "set_team")):
            self.assertEqual(self.rows[key]["flag"], flag, key)


class DeterminismTest(unittest.TestCase):

    @unittest.skipUnless(os.path.isdir(DONOR), "donor tree absent")
    def test_rederiving_reproduces_the_committed_fixture(self):
        fresh = cap.derive(REPO, DONOR, UNION_PATH)
        committed = load()
        self.assertEqual(fresh["keys"], committed["keys"])
        self.assertEqual(fresh["counts"], committed["counts"])
        self.assertEqual(fresh["pending_in_destination"],
                         committed["pending_in_destination"])


if __name__ == "__main__":
    unittest.main()
