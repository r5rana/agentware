"""Owning unit file for `agentware_lib.features.updatecmd` (task 99 wave 15).

This module is the `update` VERB — five read-only reports plus one mutating
default, sharing a handler because every one of them answers "what is the state
of this package right now?". It had coverage before this file existed and none
of it was OWNERSHIP: `test_pkg_updater.py`, `test_update_state.py` and
`test_updater_activation.py` are named for the MECHANISM (the channel, the
sidecar, the tick), and they exercise the collaborators this module dispatches
to rather than the dispatch itself.

The dispatch is where the contract lives, and this file asserts it directly:

  * FLAG PRECEDENCE. `--status-line` wins over every other flag and over
    `--format json`, because a SessionStart hook must never be handed a JSON
    blob or a network fetch by an operator's stray flag.
  * THE EXIT-CODE TABLE, which is the whole product for a caller that scripts
    `update`. `--check` and `--status-line` ALWAYS exit 0 (they are reports, and
    a nudge that failed because no key is pinned would break every caller that
    only wanted to know); an apply exits 1 on a non-graceful status; and a
    `reconciled-` prefix is NORMALIZED so a reconcile SUCCESS exits 0 and a
    reconcile ERROR exits 1 — the audit finding that a successful self-heal
    used to exit 1 and a migrate-error used to exit 0.
  * SELF-HEAL fails NON-ZERO on a migrate error even though that state does not
    set `needs_manual_recovery` — a self-heal that silently exits 0 there is how
    a broken KB schema stays invisible to the loop that calls it.
  * THE INFORMATIONAL BLOCK IS NEVER THE APPLY DECISION: no behind/ahead count
    reaches the channel resolution, and a missing upstream degrades to a quiet
    `{}` instead of failing the verb.

Every collaborator is stubbed: no test in this file touches git, the network,
the operator's `~/.agentware`, or the real REPO_ROOT (R-LOC-03). The stub table
and the `UpdateCase` base live in `_updatecmd_support.py` — this file hit the
600-line `tests/**` cap in the commit it landed in, and R-MOD-04 says split
where the obligation changes rather than raise a cap or declare a debt row in
the very commit paying four down.

Runner:  python3 -m unittest tests.unit.features.test_updatecmd -v
"""

from tests._paths import TESTS_DIR

import json
import os
import sys
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.features.updatecmd as U                    # noqa: E402

try:
    from tests.unit.features._updatecmd_support import (
        UpdateCase, _Args, _Proc, _captured, _git_counts, _sparse)
except ImportError:  # allow a bare-name run from inside tests/unit/features/
    from _updatecmd_support import (                            # noqa: F401
        UpdateCase, _Args, _Proc, _captured, _git_counts, _sparse)


# --- --status-line -----------------------------------------------------------

class StatusLineTests(UpdateCase):
    def test_silent_when_there_is_nothing_to_say(self):
        rc, out, err = self.run_update(_Args(status_line=True))
        self.assertEqual((rc, out, err), (0, "", ""))

    def test_a_stranded_apply_is_surfaced(self):
        rc, out, _ = self.run_update(
            _Args(status_line=True),
            _pkg_state_read=lambda: {"needs_manual_recovery": True})
        self.assertEqual(rc, 0)
        self.assertIn("STRANDED", out)
        self.assertIn("--self-heal", out)

    def test_an_unfinalized_apply_is_surfaced(self):
        rc, out, _ = self.run_update(
            _Args(status_line=True),
            _pkg_state_read=lambda: {"pending_apply": {"tag": "v2"}})
        self.assertEqual(rc, 0)
        self.assertIn("unfinalized", out)

    def test_stranded_wins_over_pending(self):
        # Both flags set is the state a crashed apply leaves behind; the operator
        # must be told the ACTIONABLE one, not the one that resolves itself.
        _, out, _ = self.run_update(
            _Args(status_line=True),
            _pkg_state_read=lambda: {"needs_manual_recovery": True,
                                     "pending_apply": {"tag": "v2"}})
        self.assertIn("STRANDED", out)
        self.assertNotIn("unfinalized", out)

    def test_a_tampered_anchor_reports_and_still_exits_zero(self):
        def _boom():
            raise U._PkgStateError("group-writable")

        rc, out, _ = self.run_update(_Args(status_line=True),
                                     _pkg_state_read=_boom)
        self.assertEqual(rc, 0)
        self.assertIn("tampered", out)
        self.assertIn("trust verify", out)

    def test_status_line_never_touches_the_network(self):
        def _no(*a, **kw):
            raise AssertionError("--status-line must not touch git/network")

        rc, _, _ = self.run_update(_Args(status_line=True), git_fetch=_no,
                                   git_has_upstream=_no, git_is_work_tree=_no,
                                   _pkg_resolve_verified_tag=_no)
        self.assertEqual(rc, 0)

    def test_status_line_beats_every_other_flag_and_ignores_format(self):
        rc, out, _ = self.run_update(
            _Args(status_line=True, check=True, self_heal=True,
                  staleness_check=True, format="json"),
            _pkg_state_read=lambda: {"pending_apply": {"tag": "v2"}})
        self.assertEqual(rc, 0)
        self.assertIn("unfinalized", out)
        self.assertRaises(ValueError, json.loads, out)


# --- --staleness-check -------------------------------------------------------

class StalenessCheckTests(UpdateCase):
    def test_it_delegates_and_returns_the_prehook_code(self):
        for code in (0, 1):
            rc, out, _ = self.run_update(
                _Args(staleness_check=True),
                _pkg_staleness_prehook=lambda kdir, target, _c=code: _c)
            self.assertEqual((rc, out), (code, ""))

    def test_it_passes_the_resolved_knowledge_dir_and_the_target(self):
        seen = []
        self.run_update(_Args(staleness_check=True),
                        _pkg_staleness_prehook=lambda kdir, target: (
                            seen.append((kdir, target)), 0)[1])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][1], U.REPO_ROOT)

    def test_it_beats_self_heal_and_check(self):
        called = []
        self.run_update(_Args(staleness_check=True, self_heal=True, check=True),
                        _pkg_self_heal=lambda k, t: called.append("heal") or {},
                        _pkg_resolve_verified_tag=lambda t: called.append(
                            "check") or {})
        self.assertEqual(called, [])


# --- --self-heal -------------------------------------------------------------

class SelfHealTests(UpdateCase):
    def test_no_knowledge_dir_is_a_graceful_no_op(self):
        rc, out, _ = self.run_update(_Args(self_heal=True),
                                     require_knowledge_dir=lambda: None)
        self.assertEqual(rc, 0)
        self.assertIn("no knowledge dir", out)

    def test_no_knowledge_dir_json_reports_the_status(self):
        rc, out, _ = self.run_update(_Args(self_heal=True, format="json"),
                                     require_knowledge_dir=lambda: None)
        self.assertEqual((rc, json.loads(out)), (0, {"ok": True,
                                                     "status": "no-kb"}))

    def test_a_clean_heal_exits_zero_and_names_the_status(self):
        rc, out, _ = self.run_update(_Args(self_heal=True))
        self.assertEqual(rc, 0)
        self.assertIn("self-heal -> clean (nothing to heal)", out)

    def test_a_missing_detail_renders_as_empty_not_a_key_error(self):
        rc, out, _ = self.run_update(
            _Args(self_heal=True), _pkg_self_heal=lambda k, t: {"status": "clean"})
        self.assertEqual(rc, 0)
        self.assertIn("self-heal -> clean ()", out)

    def test_needs_manual_recovery_exits_one(self):
        rc, out, _ = self.run_update(
            _Args(self_heal=True),
            _pkg_self_heal=lambda k, t: {"status": "stranded", "detail": "smoke",
                                         "needs_manual_recovery": True})
        self.assertEqual(rc, 1)
        self.assertIn("stranded", out)

    def test_a_migrate_error_exits_one_without_needs_manual_recovery(self):
        # The audit case: a self-heal that silently exits 0 on a migrate error is
        # how a broken KB schema stays invisible to the loop that called it.
        rc, out, _ = self.run_update(
            _Args(self_heal=True),
            _pkg_self_heal=lambda k, t: {"status": "reconciled-migrate-error",
                                         "detail": "schema bump failed"})
        self.assertEqual(rc, 1)
        self.assertIn("migrate-error", out)

    def test_the_json_payload_carries_the_whole_heal_record(self):
        rc, out, _ = self.run_update(
            _Args(self_heal=True, format="json"),
            _pkg_self_heal=lambda k, t: {"status": "reconciled-migrate-error",
                                         "detail": "d"})
        got = json.loads(out)
        self.assertEqual((rc, got["ok"]), (1, False))
        self.assertEqual(got["self_heal"]["status"], "reconciled-migrate-error")

    def test_a_non_migrate_reconciled_status_still_exits_zero(self):
        rc, _, _ = self.run_update(
            _Args(self_heal=True),
            _pkg_self_heal=lambda k, t: {"status": "reconciled-finalized",
                                         "detail": "d"})
        self.assertEqual(rc, 0)

    def test_self_heal_beats_the_apply_path(self):
        self.run_update(_Args(self_heal=True),
                        _pkg_run_update=lambda t: (_ for _ in ()).throw(
                            AssertionError("must not apply during --self-heal")))


# --- the not-a-git-repo guard ------------------------------------------------

class NotAWorkTreeTests(UpdateCase):
    def test_it_is_a_graceful_zero_in_both_formats(self):
        for fmt in ("text", "json"):
            rc, out, _ = self.run_update(_Args(format=fmt),
                                         git_is_work_tree=lambda t: False)
            self.assertEqual(rc, 0)
            if fmt == "json":
                got = json.loads(out)
                self.assertEqual((got["ok"], got["status"], got["applied"]),
                                 (True, "not-a-git-repo", False))
            else:
                self.assertIn("not a git work tree", out)

    def test_it_short_circuits_the_channel_and_the_apply(self):
        def _no(*a, **kw):
            raise AssertionError("must not resolve or apply outside a work tree")

        self.run_update(_Args(check=True), git_is_work_tree=lambda t: False,
                        _pkg_resolve_verified_tag=_no, _pkg_run_update=_no)


# --- the informational @{upstream} view --------------------------------------

class UpstreamInfoTests(UpdateCase):
    def test_no_upstream_degrades_to_an_empty_dict(self):
        lines = []
        self.assertEqual(U._update_upstream_info("/t", False, lines), {})
        self.assertEqual(lines, [])

    def test_a_failed_fetch_degrades_to_an_empty_dict(self):
        self.stub(git_has_upstream=lambda t: True,
                  git_fetch=lambda t: _Proc("", 1))
        lines = []
        self.assertEqual(U._update_upstream_info("/t", False, lines), {})
        self.assertEqual(lines, [])

    def test_no_upstream_never_fetches(self):
        # Short-circuit: `git fetch` is a NETWORK op, and a checkout with no
        # upstream must not pay for one.
        self.stub(git_has_upstream=lambda t: False,
                  git_fetch=lambda t: (_ for _ in ()).throw(
                      AssertionError("must not fetch without an upstream")))
        self.assertEqual(U._update_upstream_info("/t", False, []), {})

    def test_the_counts_are_parsed_and_rendered(self):
        self.stub(git_has_upstream=lambda t: True, _git=_git_counts)
        lines = []
        info = U._update_upstream_info("/t", False, lines)
        self.assertEqual(info, {"upstream_ref": "origin/main", "behind": 3,
                                "ahead": 1})
        self.assertIn("behind 3 / ahead 1", lines[0])
        self.assertIn("NOT the apply decision", lines[0])

    def test_a_non_numeric_count_becomes_zero_rather_than_raising(self):
        self.stub(git_has_upstream=lambda t: True,
                  _git=lambda t, *a: _Proc("origin/main\n" if "--abbrev-ref" in a
                                           else "fatal: bad revision\n", 0))
        info = U._update_upstream_info("/t", False, [])
        self.assertEqual((info["behind"], info["ahead"]), (0, 0))

    def test_the_catalog_diff_is_absent_without_the_flag(self):
        self.stub(git_has_upstream=lambda t: True, _git=_git_counts,
                  _catalog_skill_names_at_ref=lambda ref: (_ for _ in ()).throw(
                      AssertionError("must not read the catalog without --catalog")))
        self.assertNotIn("catalog_changes",
                         U._update_upstream_info("/t", False, []))

    def test_an_unreadable_upstream_catalog_yields_no_diff(self):
        self.assertIsNone(U._upstream_catalog_diff(True))

    def test_the_diff_is_both_directions_and_sorted(self):
        self.stub(_catalog_skill_names_at_ref=lambda ref: (
            {"b", "a"} if ref == "@{upstream}" else {"b", "z"}))
        self.assertEqual(U._upstream_catalog_diff(True),
                         {"added": ["a"], "removed": ["z"]})

    def test_a_missing_head_catalog_is_treated_as_empty(self):
        self.stub(_catalog_skill_names_at_ref=lambda ref: (
            {"a"} if ref == "@{upstream}" else None))
        self.assertEqual(U._upstream_catalog_diff(True),
                         {"added": ["a"], "removed": []})

    def test_an_empty_side_prints_nothing(self):
        self.assertEqual(U._catalog_diff_lines({"added": [], "removed": []}), [])
        self.assertEqual(U._catalog_diff_lines(None), [])

    def test_each_populated_side_prints_its_own_line(self):
        lines = U._catalog_diff_lines({"added": ["a", "b"], "removed": ["z"]})
        self.assertEqual(len(lines), 2)
        self.assertIn("added upstream: a, b", lines[0])
        self.assertIn("removed upstream: z", lines[1])

    def test_the_info_block_lands_on_the_payload_of_a_check_run(self):
        rc, out, _ = self.run_update(_Args(check=True, format="json"),
                                     git_has_upstream=lambda t: True,
                                     _git=_git_counts)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["informational_upstream"]["behind"], 3)

    def test_the_counts_never_reach_the_apply_decision(self):
        # 3 behind, and the signed channel says up-to-date: the verb must report
        # up-to-date. The behind count is informational, and always has been.
        rc, out, _ = self.run_update(_Args(check=True),
                                     git_has_upstream=lambda t: True,
                                     _git=_git_counts)
        self.assertEqual(rc, 0)
        self.assertIn("up to date on the signed channel", out)


# --- --check -----------------------------------------------------------------

class CheckReportTests(UpdateCase):
    def _check(self, r, fmt="text"):
        return self.run_update(_Args(check=True, format=fmt),
                               _pkg_resolve_verified_tag=lambda t: r)

    def test_a_resolved_tag_is_announced_with_a_short_sha(self):
        rc, out, _ = self._check({"status": "resolved", "tag": "v2.0.0",
                                  "version": "2.0.0",
                                  "sha": "abcdef0123456789", "warnings": []})
        self.assertEqual(rc, 0)
        # assertIn on the SHORT sha also passes against the full one, since the
        # short form is its prefix; the line has to be pinned whole.
        self.assertIn("update: signed update AVAILABLE — v2.0.0 (2.0.0) "
                      "@ abcdef012\n", out)
        self.assertNotIn("abcdef0123456789", out)

    def test_up_to_date_no_trust_skipped_and_other_each_have_a_line(self):
        for (status, needle) in (("up-to-date", "up to date on the signed channel"),
                                 ("no-trust", "fail-closed"),
                                 ("skipped", "channel check skipped"),
                                 ("none", "no signed update available")):
            rc, out, _ = self._check({"status": status, "detail": "d",
                                      "warnings": []})
            self.assertEqual(rc, 0, status)
            self.assertIn(needle, out)

    def test_every_status_including_no_trust_exits_zero(self):
        for status in ("resolved", "up-to-date", "no-trust", "skipped", "none"):
            rc, _, _ = self._check({"status": status, "detail": "d", "tag": "v1",
                                    "version": "1", "sha": "0" * 12,
                                    "warnings": []})
            self.assertEqual(rc, 0, status)

    def test_warnings_are_printed_before_the_verdict(self):
        _, out, _ = self._check({"status": "up-to-date", "detail": "d",
                                 "warnings": ["clock skew", "unsigned tag"]})
        lines = [ln for ln in out.strip().split("\n")]
        self.assertIn("⚠ clock skew", lines[1])
        self.assertIn("⚠ unsigned tag", lines[2])
        self.assertIn("up to date", lines[3])

    def test_the_json_payload_carries_the_whole_channel_record(self):
        rc, out, _ = self._check({"status": "no-trust", "detail": "no key",
                                  "warnings": []}, fmt="json")
        got = json.loads(out)
        self.assertEqual((rc, got["mode"], got["applied"]), (0, "check", False))
        self.assertEqual(got["channel"]["status"], "no-trust")

    def test_check_applies_nothing(self):
        self.run_update(_Args(check=True),
                        _pkg_run_update=lambda t: (_ for _ in ()).throw(
                            AssertionError("--check must never apply")))


# --- the apply path ----------------------------------------------------------

class ApplyResultTests(UpdateCase):
    def _apply(self, r, fmt="text"):
        return self.run_update(_Args(format=fmt), _pkg_run_update=lambda t: r)

    def test_a_live_loop_session_defers_without_applying(self):
        rc, out, _ = self.run_update(
            _Args(), is_session_active=lambda k: "260813-feature",
            _pkg_run_update=lambda t: (_ for _ in ()).throw(
                AssertionError("must not apply mid-loop")))
        self.assertEqual(rc, 0)
        self.assertIn("DEFERRED", out)
        self.assertIn("260813-feature", out)

    def test_the_defer_payload_names_the_status_and_stays_unapplied(self):
        rc, out, _ = self.run_update(_Args(format="json"),
                                     is_session_active=lambda k: "feat")
        got = json.loads(out)
        self.assertEqual((rc, got["applied"], got["mode"]), (0, False, "apply"))
        self.assertEqual(got["result"]["status"], "deferred")

    def test_no_knowledge_dir_skips_the_session_probe_entirely(self):
        rc, _, _ = self.run_update(
            _Args(), require_knowledge_dir=lambda: None,
            is_session_active=lambda k: (_ for _ in ()).throw(
                AssertionError("must not probe a session without a knowledge dir")))
        self.assertEqual(rc, 0)

    def test_every_graceful_status_exits_zero(self):
        for status in ("applied", "up-to-date", "none", "skipped", "no-trust",
                       "dirty", "locked", "migrate-workorder", "finalized",
                       "cleared"):
            rc, _, _ = self._apply({"status": status, "detail": "d",
                                    "warnings": []})
            self.assertEqual(rc, 0, status)

    def test_a_non_graceful_status_exits_one(self):
        for status in ("error", "migrate-error", "rolledback", "quarantined"):
            rc, _, _ = self._apply({"status": status, "detail": "d",
                                    "warnings": []})
            self.assertEqual(rc, 1, status)

    def test_the_reconciled_prefix_is_normalized_in_both_directions(self):
        # The audit finding this normalization exists for: without it a
        # successful self-heal exits 1 and a migrate-error exits 0.
        rc_ok, _, _ = self._apply({"status": "reconciled-finalized",
                                   "detail": "d", "warnings": []})
        rc_bad, _, _ = self._apply({"status": "reconciled-migrate-error",
                                    "detail": "d", "warnings": []})
        self.assertEqual((rc_ok, rc_bad), (0, 1))

    def test_a_reconciled_rollback_is_not_graceful(self):
        rc, _, _ = self._apply({"status": "reconciled-rolledback", "detail": "d",
                                "warnings": []})
        self.assertEqual(rc, 1)

    def test_applied_is_true_only_for_the_applied_status(self):
        _, out, _ = self._apply({"status": "applied", "detail": "d", "tag": "v2",
                                 "sha": "abc", "version": "2.0.0",
                                 "warnings": []}, fmt="json")
        got = json.loads(out)
        self.assertEqual((got["applied"], got["tag"], got["version"]),
                         (True, "v2", "2.0.0"))
        _, out2, _ = self._apply({"status": "reconciled-finalized", "detail": "d",
                                  "warnings": []}, fmt="json")
        self.assertEqual(json.loads(out2)["applied"], False)

    def test_needs_manual_recovery_forces_one_even_on_a_graceful_status(self):
        rc, out, _ = self._apply({"status": "applied", "detail": "d",
                                  "needs_manual_recovery": True, "warnings": []})
        self.assertEqual(rc, 1)
        self.assertIn("needs_manual_recovery", out)

    def test_needs_manual_recovery_flips_ok_false_in_the_payload(self):
        _, out, _ = self._apply({"status": "applied", "detail": "d",
                                 "needs_manual_recovery": True,
                                 "warnings": []}, fmt="json")
        self.assertEqual(json.loads(out)["ok"], False)

    def test_an_error_status_flips_ok_false_without_the_recovery_banner(self):
        # BOTH members of the error set: a migrate-error is a distinct status
        # that must flip `ok` too, and a test naming only "error" passes against
        # a tuple that lost the second member.
        for status in ("error", "migrate-error", "reconciled-migrate-error"):
            _, out, _ = self._apply({"status": status, "detail": "d",
                                     "warnings": []}, fmt="json")
            self.assertEqual(json.loads(out)["ok"], False, status)
        _, text, _ = self._apply({"status": "error", "detail": "d",
                                  "warnings": []})
        self.assertNotIn("needs_manual_recovery", text)

    def test_a_graceful_status_keeps_ok_true(self):
        _, out, _ = self._apply({"status": "dirty", "detail": "d",
                                 "warnings": []}, fmt="json")
        self.assertEqual(json.loads(out)["ok"], True)

    def test_warnings_precede_the_detail_line(self):
        _, out, _ = self._apply({"status": "applied", "detail": "applied v2",
                                 "warnings": ["skipped a quarantined tag"]})
        lines = out.strip().split("\n")
        self.assertIn("⚠ skipped a quarantined tag", lines[1])
        self.assertIn("applied v2", lines[2])


# --- the dispatch itself -----------------------------------------------------

class DispatchTests(UpdateCase):
    def test_the_current_line_reports_the_pinned_ref_and_the_version(self):
        _, out, _ = self.run_update(_Args(check=True))
        self.assertTrue(out.startswith("update: current v1.0.0 (package 1.0.0)"))

    def test_an_unversioned_package_says_so(self):
        _, out, _ = self.run_update(_Args(check=True),
                                    _pkg_version=lambda t: None)
        self.assertIn("(package unversioned)", out)

    def test_a_namespace_missing_every_optional_flag_takes_the_apply_path(self):
        with _captured() as (out, _err):
            rc = U.cmd_update(_sparse(format="text"))
        self.assertEqual(rc, 0)
        self.assertIn("already current", out.getvalue())

    def test_a_namespace_without_format_renders_as_text(self):
        with _captured() as (out, _err):
            rc = U.cmd_update(_sparse())
        self.assertEqual(rc, 0)
        self.assertIn("update: current", out.getvalue())

    def test_json_mode_emits_exactly_one_document(self):
        _, out, _ = self.run_update(_Args(check=True, format="json"))
        self.assertEqual(out.count("\n{"), 0)
        json.loads(out)


class ExportSurfaceTests(unittest.TestCase):
    def test_the_new_helpers_are_not_exported(self):
        for name in ("_update_status_line", "_update_upstream_info",
                     "_update_check_report", "_update_apply_result",
                     "_upstream_catalog_diff", "_catalog_diff_lines"):
            self.assertNotIn(name, U._EXPORTS, name)

    def test_the_verb_is_declared_exactly_once(self):
        self.assertEqual(U.__verbs__, (("update", 700, "register_update"),))


if __name__ == "__main__":
    unittest.main()
