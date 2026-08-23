"""Owning unit file for `agentware_lib.ops.audit` — task 99 wave 17.

The module carried a `test-ownership` row. `audit` is covered end to end by the
loop's own gates and by `tests/e2e`, and those runs assert that a healthy KB
passes — so they redden when a CHECK breaks and stay green for every decision
the command makes ABOUT the checks: which list is built on which load outcome,
where the two optional sections land, and what the two renderers may say. That
is where this file aims.

What is asserted here is the module's PROMISE, not its shape:

  * ONE CHECK LIST, TWO RENDERS. Both modes render the SAME assembled list, so a
    check can never be reported in one format and missing from the other. The
    test compares the two renders of one corpus rather than asserting each
    against a literal.
  * A CORPUS THAT WILL NOT LOAD COLLAPSES TO EXACTLY ONE CHECK. Not zero (which
    would exit 0 on an unreadable KB) and not thirteen (which would run readers
    over `None`). The verbatim loader error is the detail, because that string is
    the operator's only clue.
  * THE OPTIONAL SECTIONS ARE OMITTED, NOT NULLED. A consumer must be able to
    tell "not asked for" from "asked for and empty", so the json payload carries
    the keys only when the flags were on.
  * A PASSING CHECK HAS NOTHING TO SAY. Details are printed for FAILING checks
    only — the property that keeps a green audit one screen long, and the one a
    corpus with no failures cannot test.
  * `--with-tests` IS TWO CHECKS WITH DIFFERENT PRECONDITIONS. The suite check
    runs regardless; the gate check runs only when the index loaded. A broken
    index with `--with-tests` is the one input that separates them.
  * THE ADVISORY METRIC IS BEST-EFFORT AND NEVER BLOCKS. An unwritable logs dir
    leaves the audit's verdict untouched.

Hermetic: every corpus is a fresh mkdtemp, every expensive collaborator (the
suite runner, the release gate, the check functions themselves where a row is
about assembly rather than about the check) is stubbed. No live KB, no git.
"""

from tests._paths import TESTS_DIR

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.core.config as config          # noqa: E402
import agentware_lib.knowledge.index as index       # noqa: E402
import agentware_lib.ops.audit as audit             # noqa: E402


class Args(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _check(name, ok, details=()):
    return {"name": name, "ok": ok, "details": list(details)}


class _AuditCase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-t99w17-audit-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.kdir = os.path.join(self.root, "kb")
        _write(os.path.join(self.kdir, "index.json"),
               json.dumps({"entries": [], "tags": {}}) + "\n")
        self._patch(config, "require_knowledge_dir", lambda: self.kdir)
        self._patch(audit.config, "require_knowledge_dir", lambda: self.kdir)

    def _patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def run_audit(self, **kw):
        kw.setdefault("format", "text")
        kw.setdefault("with_tests", False)
        kw.setdefault("stale", False)
        kw.setdefault("propose", False)
        kw.setdefault("max_age_days", 120)
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = audit.cmd_audit(Args(**kw))
        finally:
            sys.stdout, sys.stderr = old
        return rc, out.getvalue(), err.getvalue()

    def stub_checks(self, checks):
        """Replace the whole check list, so a row about ASSEMBLY does not depend
        on what the real checks happen to say about a scratch corpus."""
        self._patch(audit, "_audit_index_checks", lambda k, d: list(checks))
        self._patch(audit, "_append_graph_health_metric", lambda k, d, ts=None: None)


# --------------------------------------------------------------------------
# _audit_index_checks — the flat literal
# --------------------------------------------------------------------------

class AuditIndexChecksTest(_AuditCase):

    def test_it_returns_the_thirteen_named_checks_in_report_order(self):
        checks = audit._audit_index_checks(self.kdir, {"entries": [], "tags": {}})
        self.assertEqual([c["name"] for c in checks], [
            "index_validate", "filesystem_sync", "tag_consistency",
            "template_conformance", "orphaned_learnings", "steering_lint",
            "personal_data", "bm25_cache_health", "mode_b_health",
            "graph_integrity", "provenance_coverage", "dream_health",
            "pkg_version"])

    def test_every_check_carries_the_ok_and_details_contract(self):
        for c in audit._audit_index_checks(self.kdir, {"entries": [], "tags": {}}):
            self.assertIsInstance(c.get("name"), str, c)
            self.assertIsInstance(c.get("ok"), bool, c)
            self.assertIsInstance(c.get("details"), list, c)

    def test_a_failing_index_reader_makes_its_row_not_ok_and_carries_its_errors(self):
        self._patch(index, "tag_consistency_errors",
                    lambda d: ["tag 'ghost' names unknown id 'nobody'"])
        self._patch(audit.index, "tag_consistency_errors",
                    lambda d: ["tag 'ghost' names unknown id 'nobody'"])
        checks = audit._audit_index_checks(self.kdir, {"entries": [], "tags": {}})
        row = [c for c in checks if c["name"] == "tag_consistency"][0]
        self.assertFalse(row["ok"])
        self.assertEqual(row["details"],
                         ["tag 'ghost' names unknown id 'nobody'"])

    def test_a_failing_index_validate_carries_its_own_errors(self):
        # index_validate is asserted SEPARATELY from tag_consistency: with only
        # the passing corpus, its `details` is [] either way, so a row that
        # dropped its errors would be invisible on exactly the check whose
        # errors matter most.
        self._patch(audit.index, "validate_index",
                    lambda k, d: ["entry 'x' has no path", "entry 'y' has no id"])
        checks = audit._audit_index_checks(self.kdir, {"entries": [], "tags": {}})
        row = [c for c in checks if c["name"] == "index_validate"][0]
        self.assertFalse(row["ok"])
        self.assertEqual(row["details"],
                         ["entry 'x' has no path", "entry 'y' has no id"])

    def test_an_empty_error_list_is_a_passing_row(self):
        checks = audit._audit_index_checks(self.kdir, {"entries": [], "tags": {}})
        row = [c for c in checks if c["name"] == "index_validate"][0]
        self.assertTrue(row["ok"])
        self.assertEqual(row["details"], [])


# --------------------------------------------------------------------------
# _audit_json_payload
# --------------------------------------------------------------------------

class AuditJsonPayloadTest(unittest.TestCase):

    def test_the_optional_sections_are_omitted_when_absent(self):
        payload = audit._audit_json_payload(True, [], None, None)
        self.assertEqual(sorted(payload), ["checks", "ok"])

    def test_a_stale_report_is_carried_when_present(self):
        payload = audit._audit_json_payload(True, [], {"rows": []}, None)
        self.assertEqual(payload["stale_report"], {"rows": []})
        self.assertNotIn("proposal", payload)

    def test_a_proposal_is_carried_independently_of_the_report(self):
        payload = audit._audit_json_payload(True, [], None, {"proposed": False})
        self.assertEqual(payload["proposal"], {"proposed": False})
        self.assertNotIn("stale_report", payload)

    def test_an_EMPTY_stale_report_is_still_carried(self):
        # "asked for and empty" must be distinguishable from "not asked for";
        # an emptiness test here would collapse the two.
        payload = audit._audit_json_payload(True, [], {}, None)
        self.assertIn("stale_report", payload)

    def test_the_verdict_and_the_checks_are_always_present(self):
        payload = audit._audit_json_payload(False, [_check("a", False)], None, None)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["checks"], [_check("a", False)])


# --------------------------------------------------------------------------
# _audit_print_text
# --------------------------------------------------------------------------

class AuditPrintTextTest(unittest.TestCase):

    def _render(self, ok, checks, stale=None, proposal=None):
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            audit._audit_print_text(ok, checks, stale, proposal)
        finally:
            sys.stdout = old
        return out.getvalue()

    def test_the_header_carries_the_verdict_and_the_count(self):
        body = self._render(True, [_check("a", True), _check("b", True)])
        self.assertIn("AUDIT PASSED (2 check(s)):", body)

    def test_a_failing_run_says_failed(self):
        self.assertIn("AUDIT FAILED",
                      self._render(False, [_check("a", False)]))

    def test_a_passing_check_prints_no_details(self):
        # The corpus deliberately gives a PASSING check details anyway: a
        # renderer that printed them unconditionally would be invisible against
        # the more usual fixture where a passing check has an empty list.
        body = self._render(True, [_check("a", True, ["measured 1.0000"])])
        self.assertIn("[ok] a", body)
        self.assertNotIn("measured 1.0000", body)

    def test_a_failing_check_prints_every_detail_not_just_the_first(self):
        body = self._render(False, [_check("a", False, ["one", "two", "three"])])
        for d in ("one", "two", "three"):
            self.assertIn("      - %s" % d, body)

    def test_each_check_is_labelled_by_its_own_verdict(self):
        body = self._render(False, [_check("a", True), _check("b", False)])
        self.assertIn("[ok] a", body)
        self.assertIn("[FAIL] b", body)

    def test_the_stale_section_is_rendered_after_the_checks(self):
        seen = []
        old = audit._print_stale_section
        audit._print_stale_section = lambda r, p: seen.append((r, p))
        try:
            self._render(True, [_check("a", True)], {"rows": []}, {"proposed": False})
        finally:
            audit._print_stale_section = old
        self.assertEqual(seen, [({"rows": []}, {"proposed": False})])


# --------------------------------------------------------------------------
# cmd_audit — assemble, then render
# --------------------------------------------------------------------------

class CmdAuditTest(_AuditCase):

    def test_an_unconfigured_workspace_exits_one_silently(self):
        self._patch(config, "require_knowledge_dir", lambda: None)
        self._patch(audit.config, "require_knowledge_dir", lambda: None)
        rc, out, err = self.run_audit()
        self.assertEqual((rc, out, err), (1, "", ""))

    def test_a_clean_corpus_exits_zero(self):
        self.stub_checks([_check("a", True), _check("b", True)])
        rc, out, _err = self.run_audit()
        self.assertEqual(rc, 0)
        self.assertIn("AUDIT PASSED (2 check(s))", out)

    def test_one_failing_check_fails_the_whole_audit(self):
        self.stub_checks([_check("a", True), _check("b", False, ["boom"])])
        rc, out, _err = self.run_audit()
        self.assertEqual(rc, 1)
        self.assertIn("boom", out)

    def test_both_modes_render_the_same_assembled_list(self):
        checks = [_check("a", True), _check("b", False, ["boom"])]
        self.stub_checks(checks)
        _rc, text, _err = self.run_audit()
        _rc2, js, _err2 = self.run_audit(format="json")
        payload = json.loads(js)
        self.assertEqual([c["name"] for c in payload["checks"]],
                         [c["name"] for c in checks])
        for c in checks:
            self.assertIn(c["name"], text)
        self.assertEqual(payload["ok"], False)

    def test_an_unloadable_index_collapses_to_exactly_one_check(self):
        self._patch(audit.index, "load_index",
                    lambda k: (None, "invalid JSON in index.json: line 1"))
        rc, out, _err = self.run_audit()
        self.assertEqual(rc, 1)
        self.assertIn("AUDIT FAILED (1 check(s))", out)
        self.assertIn("[FAIL] index_validate", out)

    def test_the_loader_error_is_surfaced_verbatim(self):
        # That string is the operator's only clue about WHY the corpus is
        # unreadable; a paraphrase would send them to the wrong file.
        self._patch(audit.index, "load_index",
                    lambda k: (None, "invalid JSON in index.json: line 1 col 2"))
        _rc, out, _err = self.run_audit()
        self.assertIn("invalid JSON in index.json: line 1 col 2", out)

    def test_an_unloadable_index_never_runs_the_index_checks(self):
        self._patch(audit.index, "load_index", lambda k: (None, "broken"))
        self._patch(audit, "_audit_index_checks", _never("_audit_index_checks"))
        self._patch(audit, "_append_graph_health_metric",
                    _never("_append_graph_health_metric"))
        rc, _out, _err = self.run_audit()
        self.assertEqual(rc, 1)

    def test_with_tests_appends_both_extra_checks_after_the_others(self):
        self.stub_checks([_check("a", True)])
        self._patch(audit, "_audit_tests_check",
                    lambda: _check("tests", True, ["3 ran"]))
        self._patch(audit, "_audit_gate_check",
                    lambda k: _check("gate", True, ["gate ok"]))
        _rc, out, _err = self.run_audit(format="json", with_tests=True)
        self.assertEqual([c["name"] for c in json.loads(out)["checks"]],
                         ["a", "tests", "gate"])

    def test_with_tests_on_a_broken_index_runs_the_suite_but_not_the_gate(self):
        # The ONE input that separates the two preconditions: a gate check needs
        # a loadable corpus, a suite run does not.
        self._patch(audit.index, "load_index", lambda k: (None, "broken"))
        self._patch(audit, "_audit_tests_check",
                    lambda: _check("tests", True, ["3 ran"]))
        self._patch(audit, "_audit_gate_check", _never("_audit_gate_check"))
        _rc, out, _err = self.run_audit(format="json", with_tests=True)
        self.assertEqual([c["name"] for c in json.loads(out)["checks"]],
                         ["index_validate", "tests"])

    def test_a_failing_extra_check_fails_the_audit(self):
        self.stub_checks([_check("a", True)])
        self._patch(audit, "_audit_tests_check",
                    lambda: _check("tests", False, ["2 failed"]))
        self._patch(audit, "_audit_gate_check", lambda k: _check("gate", True))
        rc, _out, _err = self.run_audit(with_tests=True)
        self.assertEqual(rc, 1)

    def test_the_stale_sections_are_absent_from_the_payload_by_default(self):
        self.stub_checks([_check("a", True)])
        _rc, out, _err = self.run_audit(format="json")
        self.assertEqual(sorted(json.loads(out)), ["checks", "ok"])

    def test_the_stale_sections_appear_when_the_flags_are_on(self):
        self.stub_checks([_check("a", True)])
        self._patch(audit, "_audit_stale_section",
                    lambda k, d, a, e: ({"rows": []}, {"proposed": False,
                                                       "reason": "clean report"}))
        _rc, out, _err = self.run_audit(format="json", stale=True, propose=True)
        payload = json.loads(out)
        self.assertIn("stale_report", payload)
        self.assertIn("proposal", payload)

    def test_a_stale_finding_never_changes_the_exit_code(self):
        # "there is stale knowledge" is not the same claim as "this knowledge
        # base is inconsistent", and only the second is what `audit` exits on.
        self.stub_checks([_check("a", True)])
        self._patch(audit, "_audit_stale_section",
                    lambda k, d, a, e: ({"rows": [{"id": "x"}]}, None))
        rc, out, _err = self.run_audit(format="json", stale=True)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["stale_report"]["rows"], [{"id": "x"}])

    def test_the_advisory_metric_is_appended_on_the_loaded_path(self):
        seen = []
        self._patch(audit, "_audit_index_checks", lambda k, d: [_check("a", True)])
        self._patch(audit, "_append_graph_health_metric",
                    lambda k, d, ts=None: seen.append(k))
        self.run_audit()
        self.assertEqual(seen, [self.kdir])

    def test_an_unwritable_metrics_dir_never_changes_the_verdict(self):
        # Best-effort by contract: the signal is advisory, and an audit that
        # failed because a log could not be written would be reporting on the
        # wrong thing.
        self._patch(audit, "_audit_index_checks", lambda k, d: [_check("a", True)])
        _write(os.path.join(self.kdir, "logs"), "")   # a FILE where a dir must go
        rc, _out, _err = self.run_audit()
        self.assertEqual(rc, 0)


def _never(what):
    def _raise(*a, **kw):
        raise AssertionError("%s must not be reached" % what)
    return _raise


# --------------------------------------------------------------------------
# the export surface
# --------------------------------------------------------------------------

class ExportSurfaceTest(unittest.TestCase):
    """The wave's three new helpers are deliberately ABSENT from the facade
    surface: they have no monolith counterpart, and exporting them would widen
    the surface the parity machinery pins."""

    def test_the_shipped_exports_are_unchanged_by_this_wave(self):
        self.assertEqual(audit._EXPORTS, [
            "_audit_steering_lint_check", "_audit_tests_check",
            "_audit_gate_check", "_audit_bm25_cache_check",
            "_audit_mode_b_health_check", "provenance_coverage",
            "_audit_provenance_coverage_check", "_audit_personal_data_check",
            "_audit_graph_integrity_check", "_append_graph_health_metric",
            "cmd_audit"])

    def test_the_new_helpers_exist_but_are_not_exported(self):
        for name in ("_audit_index_checks", "_audit_json_payload",
                     "_audit_print_text"):
            self.assertTrue(hasattr(audit, name), name)
            self.assertNotIn(name, audit._EXPORTS)


if __name__ == "__main__":
    unittest.main()
