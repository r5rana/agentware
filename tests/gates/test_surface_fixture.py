from tests._paths import TESTS_DIR
#!/usr/bin/env python3
"""The CLI surface fixtures are frozen baselines of the two ORIGINAL trees.

Feature 260813-modular-core-adoption, task 6.

These two fixtures are captured ONCE, from the local tree at ``aw-pre-merge``
and from the donor tree, and are never regenerated as the migration proceeds:
they are the evidence the merged tree is diffed against. The union derived
from them (task 7) is what every downstream gate compares to.

So this module deliberately does NOT re-capture the live tree and demand it
still match -- that would force a regeneration on every ported verb and quietly
destroy the very baseline the migration is measured against. It asserts the
recorded shape, the pinned counts, and the invariant the fixtures exist to
carry: that they hold comparable parser OBJECT fields and no rendered help
text. It also self-tests the prose scanner, so the "no rendered help" claim is
proven to be detectable rather than merely asserted.
"""

import json
import os
import unittest
from importlib.machinery import SourceFileLoader
import importlib.util

HERE = TESTS_DIR
FIXTURE_DIR = os.path.join(HERE, "fixtures")
LOCAL_FIXTURE = os.path.join(FIXTURE_DIR, "surface-local.json")
DONOR_FIXTURE = os.path.join(FIXTURE_DIR, "surface-donor.json")

# Pinned by the plan. A change here is a change to what the migration is
# measured against and must be justified in the worklog, not edited to match.
EXPECTED = {
    LOCAL_FIXTURE: {"nodes": 106, "actions": 442, "top_level_verbs": 38},
    DONOR_FIXTURE: {"nodes": 80, "actions": 301, "top_level_verbs": 33},
}


def load_capture():
    """Import tests/_surface_capture.py (module, not a subprocess)."""
    path = os.path.join(HERE, "_surface_capture.py")
    loader = SourceFileLoader("aw_surface_capture", path)
    spec = importlib.util.spec_from_loader("aw_surface_capture", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class SurfaceFixtureShapeTest(unittest.TestCase):
    """Both fixtures exist, parse, and record the pinned counts."""

    def test_fixtures_exist_and_are_valid_json(self):
        for path in EXPECTED:
            self.assertTrue(os.path.isfile(path), f"missing fixture {path}")
            self.assertIsInstance(load(path), dict)

    def test_counts_match_the_pinned_baseline(self):
        for path, want in EXPECTED.items():
            fixture = load(path)
            self.assertEqual(fixture["counts"], want, os.path.basename(path))

    def test_recorded_counts_agree_with_the_recorded_nodes(self):
        """A hand-edited header cannot pass by contradicting the body."""
        for path, want in EXPECTED.items():
            fixture = load(path)
            nodes = fixture["nodes"]
            self.assertEqual(len(nodes), want["nodes"], path)
            self.assertEqual(
                sum(len(n["actions"]) for n in nodes), want["actions"], path)
            self.assertEqual(
                len(fixture["top_level_verbs"]), want["top_level_verbs"], path)

    def test_every_node_is_reachable_from_its_parent(self):
        """Node paths form a tree: each child's parent declares it."""
        for path in EXPECTED:
            fixture = load(path)
            by_path = {tuple(n["path"]): n for n in fixture["nodes"]}
            self.assertIn((), by_path, f"{path}: no root node")
            for key, node in by_path.items():
                if not key:
                    continue
                parent = by_path.get(key[:-1])
                self.assertIsNotNone(parent, f"{path}: orphan node {key}")
                self.assertIn(key[-1], parent["subcommands"],
                              f"{path}: {key} not declared by its parent")


class SurfaceFixtureFieldTest(unittest.TestCase):
    """The fixtures carry comparable OBJECT fields, not rendered help."""

    def test_actions_carry_exactly_the_recorded_field_set(self):
        capture = load_capture()
        want = set(capture.ACTION_FIELDS)
        for path in EXPECTED:
            for node in load(path)["nodes"]:
                for action in node["actions"]:
                    self.assertEqual(set(action), want,
                                     f"{path}: {node['path']}")

    def test_structural_actions_are_excluded(self):
        """-h and the subparsers slot are argparse boilerplate, not surface."""
        capture = load_capture()
        structural = {c.__name__ for c in capture.STRUCTURAL_ACTIONS}
        for path in EXPECTED:
            for node in load(path)["nodes"]:
                for action in node["actions"]:
                    self.assertNotIn(action["kind"], structural)
                    self.assertNotIn("-h", action["option_strings"])

    def test_no_rendered_help_text_in_either_fixture(self):
        capture = load_capture()
        for path in EXPECTED:
            self.assertEqual(capture.scan_for_prose(load(path)), [], path)

    def test_fixtures_carry_no_operator_home_path(self):
        """R-LOC-03: a committed fixture leaks no absolute operator path."""
        home = os.path.expanduser("~")
        for path in EXPECTED:
            with open(path, encoding="utf-8") as fh:
                self.assertNotIn(home, fh.read(), path)

    def test_comparable_defaults_survive_as_scalars(self):
        """The point of an object dump: a default compares as a value.

        ``recall --token-budget`` is the decided 1500 here and 10000 in the
        donor -- a divergence rendered help could never expose.
        """
        local = load(LOCAL_FIXTURE)
        node = next(n for n in local["nodes"] if n["path"] == ["recall"])
        budget = next(a for a in node["actions"] if a["dest"] == "token_budget")
        self.assertEqual(budget["default"], 1500)
        self.assertEqual(budget["type"], "int")


class ProseScannerTest(unittest.TestCase):
    """The "no rendered help" claim must be DETECTABLE, not just asserted."""

    def test_scanner_catches_a_leaked_help_string(self):
        capture = load_capture()
        poisoned = {"nodes": [{"path": [], "actions": [
            {"kind": "_StoreAction", "help": "show this help message and exit"}]}]}
        hits = capture.scan_for_prose(poisoned)
        self.assertTrue(any("forbidden key" in h for h in hits), hits)
        self.assertTrue(any("show this help" in h for h in hits), hits)

    def test_scanner_catches_a_rendered_usage_block(self):
        capture = load_capture()
        poisoned = {"nodes": [{"path": [], "rendered": "usage: agentware [-h]"}]}
        self.assertTrue(capture.scan_for_prose(poisoned))

    def test_scanner_is_quiet_on_a_clean_fixture(self):
        capture = load_capture()
        clean = {"nodes": [{"path": ["recall"], "actions": [
            {"kind": "_StoreAction", "dest": "top_k", "option_strings": ["--top-k"],
             "required": False, "nargs": None, "default": 5, "choices": None,
             "type": "int"}]}]}
        self.assertEqual(capture.scan_for_prose(clean), [])


if __name__ == "__main__":
    unittest.main()
