"""Task-16: dream never mutates the package tree, proven by breaking the gate.

`tests/_dreampkg_gate.py` runs five checks over whichever tree carries the modular
library. This module asserts they PASS there — and then, for each one, constructs
the cheapest mutation an author could make by accident and asserts it REDDENS. A
gate only ever observed passing is not evidence.

Every mutation is a synthetic tree built in a temporary directory, so a failure
here can never leave the working tree or the staged donor modified.

Stdlib `unittest` only. Never touches the real knowledge base or config.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import _dreampkg_gate as gate  # noqa: E402
import _parity_manifest as pm  # noqa: E402
from _parity_schema import run_probe  # noqa: E402

#: The exclusion row this task owes the ledger.
ROW_ID = "dream:package-pull-step"


def write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def _registry_source(rows, bodies=""):
    """A dreamsteps-shaped module defining a registry over `rows`."""
    lines = ["\"\"\"synthetic dream step module.\"\"\"", "", bodies, ""]
    for _sid, _label, fn in rows:
        if ("def %s(" % fn) not in bodies:
            lines.append("def %s(kdir, dry_run):\n    return {}\n" % fn)
    lines.append("DREAM_STEP_FUNCS = (")
    for sid, label, fn in rows:
        lines.append("    (%r, %r, %s)," % (sid, label, fn))
    lines.append(")")
    return "\n".join(lines) + "\n"


#: This repo's retired set, as a synthetic registry: every step present except
#: the four plan task 46 restores.
_GOOD_ROWS = [(sid, label, "_dream_step_%d" % i)
              for i, (sid, label) in enumerate(gate.LOCAL_RETIRED_STEPS)
              if sid not in gate.PENDING_RESTORE_STEPS]


class SyntheticTree(object):
    """A minimal tree carrying only what a given check reads."""

    def __init__(self, case):
        self.root = tempfile.mkdtemp(prefix="aw-dreamsynth-%s-" % case)

    def steps(self, source):
        write(os.path.join(self.root, gate.LIB_REL, "__init__.py"), "")
        write(os.path.join(self.root, gate.LIB_REL, "features", "__init__.py"), "")
        write(os.path.join(self.root, gate.LIB_REL, "features", "dreamsteps.py"),
              source)
        return self

    def file(self, rel, body):
        write(os.path.join(self.root, rel), body)
        return self


class ResolvedTreeTest(unittest.TestCase):
    """The gate must find a tree, and say so — never pass by finding nothing."""

    def test_dreampkg_a_tree_carrying_the_lib_resolves(self):
        tree, problems = gate.resolve_tree()
        self.assertEqual(problems, [])
        self.assertTrue(os.path.isdir(os.path.join(tree, gate.LIB_REL)), tree)

    def test_dreampkg_no_tree_is_a_FAILURE_not_a_silent_pass(self):
        empty = tempfile.mkdtemp(prefix="aw-dreamnotree-")
        self.addCleanup(shutil.rmtree, empty, True)
        saved_repo, saved_dest = gate.REPO, gate._stage_donor.resolve_dest
        saved_env = os.environ.pop("AGENTWARE_LIB_TREE", None)
        gate.REPO = empty
        gate._stage_donor.resolve_dest = lambda *a, **k: empty
        try:
            tree, problems = gate.resolve_tree()
        finally:
            gate.REPO, gate._stage_donor.resolve_dest = saved_repo, saved_dest
            if saved_env is not None:
                os.environ["AGENTWARE_LIB_TREE"] = saved_env
        self.assertIsNone(tree)
        self.assertTrue(problems)
        self.assertIn("no tree carries", problems[0])


class GatePassesTest(unittest.TestCase):
    """The real assertion: every check is clean over the resolved tree."""

    @classmethod
    def setUpClass(cls):
        cls.tree, problems = gate.resolve_tree()
        if problems:
            raise unittest.SkipTest(problems[0])

    def test_dreampkg_whole_gate_is_clean(self):
        self.assertEqual(gate.check(self.tree), [])

    def test_dreampkg_gate_runs_as_a_command_and_exits_zero(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = gate.main(["--tree", self.tree, "--json"])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertTrue(json.loads(buf.getvalue())["ok"])

    def test_dreampkg_the_registry_resolves_exactly_once(self):
        rows, rel, problems = gate.find_registry(self.tree)
        self.assertEqual(problems, [])
        self.assertTrue(rel.endswith(".py"), rel)
        ids = [sid for sid, _ in rows]
        self.assertNotIn(gate.RETIRED_STEP_ID, ids)
        # A subset of this repo's retired set, missing exactly the task-46 four.
        local = [sid for sid, _ in gate.LOCAL_RETIRED_STEPS]
        self.assertEqual([sid for sid in local if sid in set(ids)], ids)
        self.assertEqual(sorted(set(local) - set(ids)),
                         sorted(gate.PENDING_RESTORE_STEPS))

    def test_dreampkg_the_shared_post_check_survived_the_retirement(self):
        # The retirement must not take the KB-pull step's post-check with it.
        sources = gate.function_sources(self.tree)
        self.assertIn("_dream_pull_post_check", sources)
        self.assertIn("_dream_step_kb_pull", sources)


class MutationTest(unittest.TestCase):
    """Each check, broken the cheapest way, must name what broke."""

    def synth(self, case):
        tree = SyntheticTree(case)
        self.addCleanup(shutil.rmtree, tree.root, True)
        return tree

    def test_dreampkg_reintroducing_the_step_reddens_the_registry_check(self):
        rows = [(gate.RETIRED_STEP_ID, gate.RETIRED_STEP_LABEL,
                 "_dream_step_pkg")] + _GOOD_ROWS
        tree = self.synth("readd").steps(_registry_source(rows))
        problems = gate.check_registry(tree.root)
        self.assertTrue(problems, "a reintroduced step passed the registry check")
        joined = "\n".join(problems)
        self.assertIn(repr(gate.RETIRED_STEP_ID), joined)
        self.assertIn(repr(gate.RETIRED_STEP_LABEL), joined)

    def test_dreampkg_losing_an_unrelated_step_reddens_the_registry_check(self):
        # The check is symmetric: it catches a LOSS as loudly as a return. Only
        # the declared task-46 four may be missing.
        rows = [r for r in _GOOD_ROWS if r[0] != "c"]
        tree = self.synth("lost").steps(_registry_source(rows))
        problems = gate.check_registry(tree.root)
        self.assertTrue(problems, "a dropped step passed the registry check")
        self.assertIn("'c'", "\n".join(problems))

    def test_dreampkg_relabelling_a_step_reddens_the_registry_check(self):
        rows = [(sid, "audit-v2" if sid == "c" else label, fn)
                for sid, label, fn in _GOOD_ROWS]
        tree = self.synth("relabel").steps(_registry_source(rows))
        problems = gate.check_registry(tree.root)
        self.assertTrue(problems)
        self.assertIn("audit-v2", "\n".join(problems))

    def test_dreampkg_two_registries_are_a_failure_not_a_coin_toss(self):
        tree = self.synth("two").steps(_registry_source(_GOOD_ROWS))
        write(os.path.join(tree.root, gate.LIB_REL, "features", "dreamalt.py"),
              _registry_source(_GOOD_ROWS))
        rows, _rel, problems = gate.find_registry(tree.root)
        self.assertIsNone(rows)
        self.assertIn("assigned in 2 places", "\n".join(problems))

    def test_dreampkg_redefining_a_retired_callable_reddens(self):
        body = "def %s():\n    return None\n" % gate.RETIRED_CALLABLES[0]
        tree = self.synth("recall").steps(_registry_source(_GOOD_ROWS, body))
        problems = gate.check_retired_callables(tree.root)
        self.assertTrue(problems, "a redefined retired callable passed")
        self.assertIn(gate.RETIRED_CALLABLES[0], "\n".join(problems))

    def test_dreampkg_naming_the_retired_step_in_a_doc_reddens(self):
        tree = self.synth("doc").steps(_registry_source(_GOOD_ROWS))
        tree.file(os.path.join("docs", "GUIDE.md"),
                  "| **0** %s | pulls the package |\n" % gate.RETIRED_STEP_LABEL)
        problems = gate.check_no_hits(tree.root)
        self.assertTrue(problems, "a doc naming the retired step passed")
        self.assertIn("docs/GUIDE.md", "\n".join(problems))

    def test_dreampkg_the_scan_exemptions_are_named_and_narrow(self):
        # A proof of absence must be allowed to say what is absent — but the
        # exemption must be a named list, never a pattern that could hide a
        # reintroduction in shipped code.
        self.assertEqual(gate.SCAN_EXEMPT_DIRS, ("tests",))
        self.assertEqual(sorted(gate.SCAN_EXEMPT_FILES),
                         [os.path.join("scripts", "agentware_legacy.py")])
        # ...and every entry is keyed by a NON-EMPTY identity anchor, so no
        # exemption can be granted on the strength of a path alone.
        for rel, anchor in gate.SCAN_EXEMPT_FILES.items():
            self.assertTrue(anchor, "%s is exempted by path, not identity" % rel)
        tree = self.synth("exempt").steps(_registry_source(_GOOD_ROWS))
        tree.file(os.path.join("tests", "test_x.py"),
                  "RETIRED = %r\n" % gate.RETIRED_STEP_LABEL)
        self.assertEqual(gate.check_no_hits(tree.root), [])
        tree.file(os.path.join("scripts", "hooks", "x.sh"),
                  "# %s\n" % gate.RETIRED_STEP_LABEL)
        self.assertTrue(gate.check_no_hits(tree.root))

    def test_dreampkg_the_monolith_exemption_retires_with_the_monolith(self):
        # `SCAN_EXEMPT_DIRS` is a standing rule; the one FILE entry is coupled to
        # a file the shim retirement deletes. Asserted as a biconditional so this
        # keeps its meaning on both sides of the deletion: while the monolith
        # exists the entry must be EARNED (it carries the retirement note
        # verbatim, which is the only reason it is exempt), and once the monolith
        # is gone the entry must read as retired rather than as an exemption
        # still in force over something.
        legacy = os.path.join("scripts", "agentware_legacy.py")
        self.assertIn(legacy, gate.SCAN_EXEMPT_FILES)
        if os.path.isfile(os.path.join(REPO, legacy)):
            self.assertEqual(gate.stale_exemptions(REPO), [])
            self.assertEqual(gate.impostor_exemptions(REPO), [],
                             "the file at the exempt path is not the monolith")
            self.assertEqual(
                gate.unused_exemptions(REPO), [],
                "the monolith no longer carries a retired token, so its "
                "exemption is unearned dead weight over live code")
        else:
            self.assertEqual(gate.stale_exemptions(REPO), [legacy])
            self.assertEqual(gate.impostor_exemptions(REPO), [])
            self.assertEqual(gate.unused_exemptions(REPO), [])

    def test_dreampkg_a_retired_exemption_masks_NOTHING_in_this_tree(self):
        # Inertness executed rather than argued: for an exemption whose file is
        # gone, running check 3 WITHOUT the entry must give the identical answer.
        # That is the difference between "we assume it is safe to delete" and
        # "the gate has already proved the deletion is a no-op" — and while the
        # monolith is still here the same comparison proves the opposite, that
        # the entry is genuinely load-bearing and may not simply be dropped.
        stale = gate.stale_exemptions(REPO)
        original = gate.SCAN_EXEMPT_FILES
        with_entries = gate.check_no_hits(REPO)
        gate.SCAN_EXEMPT_FILES = dict(
            (r, a) for r, a in original.items() if r not in stale)
        try:
            without_stale = gate.check_no_hits(REPO)
        finally:
            gate.SCAN_EXEMPT_FILES = original
        self.assertEqual(without_stale, with_entries,
                         "a retired exemption still changes the scan result")
        if not stale:
            # The live half: dropping a LOAD-BEARING entry must change the answer,
            # or the exemption was never doing anything and the comparison above
            # would pass for a reason unrelated to inertness.
            gate.SCAN_EXEMPT_FILES = {}
            try:
                self.assertTrue(
                    gate.check_no_hits(REPO),
                    "no exemption is load-bearing — this comparison is vacuous")
            finally:
                gate.SCAN_EXEMPT_FILES = original

    def test_dreampkg_an_UNEARNED_exemption_is_reported_as_a_blind_spot(self):
        # The teeth: an entry kept after the text that justified it is edited out
        # is an un-argued hole over live code. Nothing else reports it — check 3
        # by construction cannot see inside an exempt file.
        tree = self.synth("unearned").steps(_registry_source(_GOOD_ROWS))
        rel = os.path.join("scripts", "innocent.py")
        anchor = "PROBE-IDENTITY-ANCHOR"
        tree.file(rel, "# nothing retired here\n# %s\n" % anchor)
        original = gate.SCAN_EXEMPT_FILES
        probe = dict(original)
        probe[rel] = anchor
        gate.SCAN_EXEMPT_FILES = probe
        try:
            self.assertEqual(gate.stale_exemptions(tree.root), sorted(original))
            self.assertEqual(gate.impostor_exemptions(tree.root), [])
            self.assertEqual(gate.unused_exemptions(tree.root), [rel])
            problems = gate.check_no_hits(tree.root)
            self.assertTrue(problems, "an unearned exemption passed")
            self.assertIn("blind spot", "\n".join(problems))
        finally:
            gate.SCAN_EXEMPT_FILES = original

    def test_dreampkg_an_IMPOSTOR_at_the_exempt_path_is_SCANNED_not_inherited(self):
        # The hole a path-keyed exemption leaves, closed and measured. Once the
        # monolith is deleted its entry is stale forever (the biconditional test
        # above pins the entry in place), so a file arriving at that exact path
        # later would inherit an exemption written for something else — and it
        # would inherit it SILENTLY, since check 3 cannot see inside an exempt
        # file and `unused_exemptions` only looks at files that are exempt.
        # Binding the entry to the retirement note makes the exemption
        # non-transferable: no anchor, no exemption, so the resurrection is
        # scanned and check 3 reports it like any other published file.
        tree = self.synth("impostor").steps(_registry_source(_GOOD_ROWS))
        rel = sorted(gate.SCAN_EXEMPT_FILES)[0]
        self.assertEqual(gate.stale_exemptions(tree.root),
                         sorted(gate.SCAN_EXEMPT_FILES))
        tree.file(rel, "STEP = %r  # a resurrection, not the monolith\n"
                       % gate.RETIRED_STEP_LABEL)
        self.assertEqual(gate.stale_exemptions(tree.root), [])
        self.assertEqual(gate.impostor_exemptions(tree.root), [rel])
        problems = gate.check_no_hits(tree.root)
        self.assertTrue(problems, "an impostor inherited the exemption")
        self.assertIn(rel, "\n".join(problems))
        # ...and the genuine article at the same path IS exempt, or the check
        # above would pass merely because nothing is ever exempt any more.
        tree.file(rel, "# %s: dream step-0 %s\n"
                       % (gate.SCAN_EXEMPT_FILES[rel], gate.RETIRED_TOKENS[0]))
        self.assertEqual(gate.impostor_exemptions(tree.root), [])
        self.assertEqual(gate.unused_exemptions(tree.root), [])
        self.assertEqual(gate.check_no_hits(tree.root), [])

    def test_dreampkg_a_reintroduction_still_reddens_with_the_monolith_GONE(self):
        # The exemption list must never be the reason the scan is quiet. Proved
        # against a tree that carries NO monolith at all, so the entry is stale
        # exactly as it will be after the retirement: a reintroduction in shipped
        # code must still be found.
        tree = self.synth("regone").steps(_registry_source(_GOOD_ROWS))
        self.assertEqual(gate.stale_exemptions(tree.root),
                         sorted(gate.SCAN_EXEMPT_FILES))
        self.assertEqual(gate.check_no_hits(tree.root), [])
        tree.file(os.path.join("scripts", "agentware_lib", "resurrect.py"),
                  "STEP = %r\n" % gate.RETIRED_STEP_LABEL)
        problems = gate.check_no_hits(tree.root)
        self.assertTrue(problems, "a reintroduction passed with the shim gone")
        self.assertIn("resurrect.py", "\n".join(problems))

    def test_dreampkg_a_step_reaching_the_package_root_reddens(self):
        body = ("def _dream_step_repo(kdir, dry_run):\n"
                "    return {'target': paths.REPO_ROOT}\n")
        rows = [("1", "kb-pull", "_dream_step_repo")] + _GOOD_ROWS[1:]
        tree = self.synth("root").steps(_registry_source(rows, body))
        problems = gate.check_no_package_mutation(tree.root)
        self.assertTrue(problems, "a step reaching the package root passed")
        self.assertIn("never mutates the repo", "\n".join(problems))

    def test_dreampkg_a_hard_reset_anywhere_in_the_dream_corpus_reddens(self):
        body = ("def _dream_revert(target, sha):\n"
                "    return _git(target, 'reset', '--hard', sha)\n")
        tree = self.synth("reset").steps(_registry_source(_GOOD_ROWS, body))
        problems = gate.check_no_package_mutation(tree.root)
        self.assertTrue(problems, "a hard reset passed the mutation check")
        self.assertIn("hard-resets", "\n".join(problems))

    def test_dreampkg_a_git_pull_on_the_package_root_reddens(self):
        body = ("def _dream_step_pull(kdir, dry_run):\n"
                "    return _git_timed(REPO_ROOT, 'pull', '--ff-only')\n")
        rows = [("1", "kb-pull", "_dream_step_pull")] + _GOOD_ROWS[1:]
        tree = self.synth("pull").steps(_registry_source(rows, body))
        problems = gate.check_no_package_mutation(tree.root)
        self.assertTrue(problems)
        self.assertIn("git pull", "\n".join(problems))

    def test_dreampkg_a_cli_that_accepts_the_retired_step_reddens(self):
        tree = self.synth("cli").steps(_registry_source(_GOOD_ROWS))
        write(os.path.join(tree.root, gate.ENTRY_REL), _FAKE_ENTRY)
        problems = gate.check_behaviour(tree.root)
        self.assertTrue(problems, "a CLI still serving the step passed")
        joined = "\n".join(problems)
        self.assertIn("ACCEPTED", joined)
        self.assertIn("still contains step", joined)
        self.assertIn("plans a package pull", joined)

    def test_dreampkg_a_missing_entrypoint_is_a_failure_not_a_pass(self):
        tree = self.synth("noentry").steps(_registry_source(_GOOD_ROWS))
        problems = gate.check_behaviour(tree.root)
        self.assertTrue(problems)
        self.assertIn("entrypoint missing", "\n".join(problems))


#: A CLI that still serves the retired step — the shape check 5 must catch.
_FAKE_ENTRY = '''#!/usr/bin/env python3
import json
import sys

PLAN = {"steps": [{"step": "0", "label": "pkg-pull", "status": "planned",
                   "would": "git pull agentware repo (/tmp/x)"},
                  {"step": "1", "label": "kb-pull", "status": "planned"}]}

if "--format" in sys.argv:
    print(json.dumps(PLAN))
else:
    print("planned 0")
sys.exit(0)
'''


class ParityLedgerTest(unittest.TestCase):
    """The exclusion row this task owes, and its live absence probe."""

    def setUp(self):
        self.manifest = pm.load_json(pm.MANIFEST)
        self.rows = {row["id"]: row for row in self.manifest["rows"]}

    def test_dreampkg_the_retired_step_is_an_excluded_row_with_a_passing_probe(self):
        row = self.rows[ROW_ID]
        self.assertEqual(row["status"], "excluded")
        self.assertEqual(row["verification"], "absence_probe")
        self.assertEqual(row["dimension"], "dream_maintenance")
        for field in ("reason", "approver", "follow_up", "absence_probe"):
            self.assertTrue(row.get(field), field)
        ok, detail = run_probe(REPO, row["absence_probe"])
        self.assertTrue(ok, detail)

    def test_dreampkg_the_probe_is_behavioural_not_textual(self):
        # A grep would be satisfied by deleting the WORD; only the CLI refusing
        # the step proves the capability is gone. (This repo's own monolith
        # carries the retirement note, so a grep could not even run here.)
        probe = self.rows[ROW_ID]["absence_probe"]
        self.assertEqual(probe["kind"], "cli-absent")
        self.assertEqual(probe["argv"][:2], ["dream", "--steps"])
        self.assertIn("unknown dream step", probe["expect"])

    def test_dreampkg_the_probe_refutes_when_the_step_is_served(self):
        # The refutation direction: a tree whose CLI ACCEPTS the step must make
        # the probe report the exclusion false.
        root = tempfile.mkdtemp(prefix="aw-dreamprobe-")
        self.addCleanup(shutil.rmtree, root, True)
        entry = write(os.path.join(root, "scripts", "agentware"), _FAKE_ENTRY)
        os.chmod(entry, 0o755)
        ok, detail = run_probe(root, self.rows[ROW_ID]["absence_probe"])
        self.assertFalse(ok, "the probe passed against a CLI still serving it")
        self.assertIn("ACCEPTED", detail)

    def test_dreampkg_the_committed_manifest_equals_a_fresh_derivation(self):
        fresh = pm.derive(REPO)
        self.assertEqual(json.dumps(fresh["rows"], sort_keys=True),
                         json.dumps(self.manifest["rows"], sort_keys=True))


if __name__ == "__main__":
    unittest.main()
