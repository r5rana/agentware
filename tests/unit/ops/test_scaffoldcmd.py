"""Owning unit file for `agentware_lib.ops.scaffoldcmd` (R-MOD-06).

Feature 260813-modular-core-adoption, task 73. This is the test the
`surface:scaffold` parity row names, so it asserts the SURFACE claims that row
makes: two subcommands, the required arguments, and the two distinct exit codes.

The handlers are driven directly with a namespace rather than through the CLI,
except where the point IS the parser. `new` is only ever exercised with
`--dry-run` or against a scratch tree — a test for a generator that wrote into
the checkout would leave the tree dirty on failure.
"""

from tests._paths import REPO_ROOT

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

if os.path.join(REPO_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import agentware_lib.ops.scaffoldcheck as sc  # noqa: E402
import agentware_lib.ops.scaffoldcmd as scmd  # noqa: E402
import agentware_lib.ops.scaffoldunit as su  # noqa: E402
from agentware_lib import verbs as vb  # noqa: E402


def _args(**kwargs):
    ns = argparse.Namespace(format="text", kind=su.KIND_MODULE, name="widgetthing",
                            layer="ops", verb=None, task=73, dry_run=True,
                            force=False, no_regen=True)
    for key, value in kwargs.items():
        setattr(ns, key, value)
    return ns


def _run(handler, args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = handler(args)
    return rc, out.getvalue(), err.getvalue()


class DeclarationTest(unittest.TestCase):
    """The verb is declared once, here, and the discovery contract accepts it."""

    def test_the_declaration_is_a_row_discovery_validates(self):
        rows = vb.collect([("agentware_lib.ops.scaffoldcmd", scmd.__verbs__)])
        self.assertEqual(rows, (("scaffold", 4500, "agentware_lib.ops.scaffoldcmd",
                                 "register_scaffold"),))

    def test_the_live_package_serves_the_verb_from_this_module(self):
        rows = {verb: (module, attr) for verb, _o, module, attr in vb.discover()}
        self.assertEqual(rows["scaffold"],
                         ("agentware_lib.ops.scaffoldcmd", "register_scaffold"))

    def test_the_order_key_is_unique_and_last(self):
        keys = [order for _v, order, _m, _a in vb.discover()]
        self.assertEqual(keys.count(4500), 1)
        self.assertEqual(max(keys), 4500)


class ParserSurfaceTest(unittest.TestCase):
    """What `register_scaffold` declares, asserted through argparse itself."""

    def setUp(self):
        self.fmt = argparse.ArgumentParser(add_help=False)
        self.fmt.add_argument("--format", choices=("text", "json"),
                              default="text")
        self.root = argparse.ArgumentParser(prog="agentware")
        sub = self.root.add_subparsers(dest="command")
        scmd.register_scaffold(sub, self.fmt)

    def _parse(self, argv):
        with contextlib.redirect_stderr(io.StringIO()):
            return self.root.parse_args(argv)

    def test_both_subcommands_exist(self):
        self.assertEqual(self._parse(["scaffold", "check"]).func,
                         scmd.cmd_scaffold_check)
        parsed = self._parse(["scaffold", "new", "--kind", "module", "--name",
                              "widgetthing", "--layer", "ops", "--task", "73"])
        self.assertEqual(parsed.func, scmd.cmd_scaffold_new)

    def test_a_bare_scaffold_is_an_error_not_a_silent_noop(self):
        with self.assertRaises(SystemExit):
            self._parse(["scaffold"])

    def test_new_refuses_to_run_without_kind_name_or_task(self):
        for argv in (["scaffold", "new", "--name", "widgetthing", "--task", "73"],
                     ["scaffold", "new", "--kind", "module", "--task", "73"],
                     ["scaffold", "new", "--kind", "module", "--name", "w"]):
            with self.assertRaises(SystemExit):
                self._parse(argv)

    def test_the_kind_choices_are_the_four_the_plan_names(self):
        for kind in ("module", "verb", "panel", "loop-lib"):
            parsed = self._parse(["scaffold", "new", "--kind", kind, "--name",
                                  "widgetthing", "--task", "73"])
            self.assertEqual(parsed.kind, kind)
        with self.assertRaises(SystemExit):
            self._parse(["scaffold", "new", "--kind", "gadget", "--name", "w",
                         "--task", "73"])

    def test_check_takes_the_shared_format_parent(self):
        self.assertEqual(self._parse(["scaffold", "check", "--format",
                                      "json"]).format, "json")


class NewHandlerTest(unittest.TestCase):

    def test_a_dry_run_reports_the_plan_and_writes_nothing(self):
        before = os.path.getmtime(os.path.join(REPO_ROOT, su.CURATED_REL))
        rc, out, _err = _run(scmd.cmd_scaffold_new, _args())
        self.assertEqual(rc, 0)
        self.assertIn("DRY RUN", out)
        self.assertIn("scripts/agentware_lib/ops/widgetthing.py", out)
        self.assertIn("tests/unit/ops/test_widgetthing.py", out)
        self.assertEqual(
            before, os.path.getmtime(os.path.join(REPO_ROOT, su.CURATED_REL)))
        self.assertFalse(os.path.exists(
            os.path.join(REPO_ROOT, "scripts/agentware_lib/ops/widgetthing.py")))

    def test_an_invalid_unit_exits_two_not_one(self):
        """CI must be able to tell "your unit is invalid" from "your tree is
        incomplete"; the audit owns exit 1."""
        rc, _out, err = _run(scmd.cmd_scaffold_new, _args(name="utils"))
        self.assertEqual(rc, scmd.RC_BAD_UNIT)
        self.assertEqual(rc, 2)
        self.assertIn("R-MOD-05", err)

    def test_a_missing_layer_is_reported_not_raised(self):
        rc, _out, err = _run(scmd.cmd_scaffold_new, _args(layer=None))
        self.assertEqual(rc, scmd.RC_BAD_UNIT)
        self.assertIn("--layer", err)

    def test_json_reports_the_error_on_stdout_as_data(self):
        rc, out, _err = _run(scmd.cmd_scaffold_new,
                             _args(format="json", name="utils"))
        payload = json.loads(out)
        self.assertEqual(rc, scmd.RC_BAD_UNIT)
        self.assertFalse(payload["ok"])
        self.assertIn("R-MOD-05", payload["error"])

    def test_json_reports_the_plan_as_data(self):
        rc, out, _err = _run(scmd.cmd_scaffold_new, _args(format="json"))
        payload = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["unit"]["dotted"],
                         "agentware_lib.ops.widgetthing")
        self.assertIn("scaffold check", " ".join(payload["next_steps"]))

    def test_a_verb_unit_reports_the_parser_pins_as_the_authors_work(self):
        rc, out, _err = _run(scmd.cmd_scaffold_new,
                             _args(kind=su.KIND_VERB, verb="widget"))
        self.assertEqual(rc, 0)
        self.assertIn("EXPECTED_VERBS", out)
        self.assertIn("_help_golden.py --write", out)


class CheckHandlerTest(unittest.TestCase):

    def _patched(self, result):
        real = sc.audit
        sc.audit = lambda *a, **k: result
        self.addCleanup(setattr, sc, "audit", real)

    def test_a_clean_tree_exits_zero(self):
        self._patched({"ok": True, "modules": 7, "findings": [], "counts": {}})
        rc, out, _err = _run(scmd.cmd_scaffold_check, _args(format="text"))
        self.assertEqual(rc, 0)
        self.assertIn("PASSED", out)

    def test_a_finding_exits_one_and_names_the_gap(self):
        self._patched({"ok": False, "modules": 7, "counts": {},
                       "findings": [{"kind": "owning_test_missing",
                                     "location": "scripts/agentware_lib/ops/x.py",
                                     "detail": "no owning unit file"}]})
        rc, out, _err = _run(scmd.cmd_scaffold_check, _args(format="text"))
        self.assertEqual(rc, 1)
        self.assertIn("FAILED", out)
        self.assertIn("owning_test_missing", out)
        self.assertIn("scripts/agentware_lib/ops/x.py", out)

    def test_json_carries_the_same_verdict_as_the_exit_code(self):
        self._patched({"ok": False, "modules": 7, "counts": {},
                       "findings": [{"kind": "doc_row_missing",
                                     "location": "x", "detail": "d"}]})
        rc, out, _err = _run(scmd.cmd_scaffold_check, _args(format="json"))
        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(out)["ok"])

    def test_the_live_audit_is_reachable_through_the_handler(self):
        """Unpatched: the CI path end to end, on the real tree."""
        rc, out, _err = _run(scmd.cmd_scaffold_check, _args(format="json"))
        payload = json.loads(out)
        self.assertEqual(rc, 0, payload["findings"])
        self.assertTrue(payload["ok"])


class EndToEndScratchTreeTest(unittest.TestCase):
    """`new` for real, in a scratch tree: the unit lands and the test collects."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-scaffold-cmd-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for rel in (su.CATALOG_REL, su.README_REL, su.CURATED_REL):
            src = os.path.join(REPO_ROOT, rel)
            dest = os.path.join(self.tmp, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(src, dest)

    def test_the_emitted_module_and_test_are_both_valid_python(self):
        import agentware_lib.ops.scaffoldwire as sw
        unit = su.resolve(su.KIND_MODULE, "widgetthing", layer="ops", task=73,
                          root=self.tmp)
        sw.emit(unit, root=self.tmp, regen=False)
        for rel in (unit["path"], unit["test_path"]):
            with open(os.path.join(self.tmp, rel), encoding="utf-8") as fh:
                compile(fh.read(), rel, "exec")

    def test_the_emitted_unit_passes_the_audits_own_checks(self):
        """The scaffold's output satisfies the auditor, module for module."""
        import agentware_lib.ops.scaffoldwire as sw
        unit = su.resolve(su.KIND_MODULE, "widgetthing", layer="ops", task=73,
                          root=self.tmp)
        sw.emit(unit, root=self.tmp, regen=False)
        manifest = {"modules": [{"name": unit["dotted"], "path": unit["path"],
                                 "layer": "ops", "test_file": unit["test_path"],
                                 "verbs": []}]}
        self.assertEqual(sc.check_tests(self.tmp, manifest), [])
        self.assertEqual(sc.check_docs(self.tmp, manifest), [])


if __name__ == "__main__":
    unittest.main()
