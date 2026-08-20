"""Gate for tests/_commit_series.py -- the task-82 publishable-series check.

"Zero findings" is also what a broken comparison prints, so the LIVE assertion
(the real branch is clean) is worth nothing on its own. Every arm here is
proven to BITE against a synthetic repository built in a temp directory with
one defect planted in it, and every planted-defect test first asserts that the
SAME repository is clean without the defect -- otherwise an inert probe would
report a false green and a test that never ran would look like a test that
passed.

The synthetic repositories are hermetic: `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM`
are pointed at `os.devnull` and `HOME` at the sandbox, so the operator's real
git identity can never leak in and make a planted foreign author accidentally
correct.
"""

import datetime
import os
import subprocess
import sys
import tempfile
import unittest

from tests._paths import REPO_ROOT, TESTS_DIR

if TESTS_DIR not in sys.path:  # gate helpers are imported by bare name
    sys.path.insert(0, TESTS_DIR)

import _commit_series as cs  # noqa: E402

NAME = "Test Operator"
EMAIL = "operator@example.invalid"
BASE_TAG = "synth-base"
GOOD = "feat(core): land the change the plan asked for"
BASE_DAY = "2026-08-10"
DAY = "2026-08-11"


def _iso(day, hour=12):
    return "%sT%02d:00:00+00:00" % (day, hour)


class _Sandbox(object):
    """A throwaway git repository with a tagged base and a controlled series."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="aw-commit-series-")
        self.env = dict(os.environ)
        self.env.update({"GIT_CONFIG_GLOBAL": os.devnull,
                         "GIT_CONFIG_SYSTEM": os.devnull,
                         "GIT_CONFIG_NOSYSTEM": "1",
                         "HOME": self.root})
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", NAME)
        self.git("config", "user.email", EMAIL)
        self.commit("chore(base): the pre-merge base", day=BASE_DAY)
        self.git("tag", BASE_TAG)

    def git(self, *args, **kw):
        env = dict(self.env)
        env.update(kw.pop("env", {}) or {})
        proc = subprocess.run(["git", "-C", self.root] + list(args),
                              capture_output=True, text=True, env=env)
        if proc.returncode != 0 and not kw.get("allow_fail"):
            raise AssertionError("git %s failed: %s" % (args, proc.stderr))
        return proc.stdout

    def commit(self, subject, body="", name=NAME, email=EMAIL, day=DAY,
               committer_day=None, committer_hour=12, author_hour=12):
        path = os.path.join(self.root, "f%d.txt" % len(subject))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(subject + "\n")
        self.git("add", "-A")
        message = subject if not body else subject + "\n\n" + body
        env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
               "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
               "GIT_AUTHOR_DATE": _iso(day, author_hour),
               "GIT_COMMITTER_DATE": _iso(committer_day or day, committer_hour)}
        self.git("commit", "-q", "-m", message, env=env)

    def merge_commit(self):
        self.git("checkout", "-q", "-b", "side")
        self.commit("feat(side): a change made on a side branch")
        self.git("checkout", "-q", "main")
        self.commit("feat(main): a change made on the trunk")
        env = {"GIT_AUTHOR_NAME": NAME, "GIT_AUTHOR_EMAIL": EMAIL,
               "GIT_COMMITTER_NAME": NAME, "GIT_COMMITTER_EMAIL": EMAIL,
               "GIT_AUTHOR_DATE": _iso(DAY), "GIT_COMMITTER_DATE": _iso(DAY)}
        self.git("merge", "-q", "--no-ff", "-m",
                 "chore(merge): join the side branch", "side", env=env)


def _fx(**over):
    fx = {"schema": cs.SCHEMA,
          "range": {"base_ref": BASE_TAG, "head_ref": "HEAD"},
          "identity": {"anchor": cs.BASE_AUTHOR_ANCHOR},
          "window": {"first": DAY},
          "count": {"min": 1},
          "subject_exemptions": []}
    fx.update(over)
    return fx


def _kinds(report):
    return sorted(f["kind"] for f in report["findings"])


class LiveSeriesTest(unittest.TestCase):
    """The real branch, through the real fixture."""

    def test_the_shipped_series_has_no_findings(self):
        report = cs.run_checks(REPO_ROOT)
        self.assertEqual([], report["schema_errors"])
        self.assertEqual([], report["findings"], cs._render(report))

    def test_the_shipped_fixture_satisfies_its_own_schema(self):
        self.assertEqual([], cs.schema_errors(cs.load_fixture()))

    def test_the_shipped_fixture_writes_down_no_identity_at_all(self):
        # R-LOC-03: an address here would be operator data in a public repo,
        # and it would be the one field a second address could be added to.
        ident = cs.load_fixture()["identity"]
        self.assertEqual(cs.BASE_AUTHOR_ANCHOR, ident["anchor"])
        for key in ("name", "email", "emails"):
            self.assertNotIn(key, ident)

    def test_the_anchor_resolves_to_the_pre_merge_author(self):
        name, email = cs.anchor_identity(REPO_ROOT, "aw-pre-merge")
        self.assertTrue(name and email, (name, email))

    def test_the_shipped_range_is_not_empty(self):
        # A gate that reads zero commits reports zero findings.
        self.assertGreater(cs.run_checks(REPO_ROOT)["commits"], 100)


class _Planted(unittest.TestCase):
    """Base class: every subclass proves clean-then-red on ONE sandbox."""

    def setUp(self):
        self.box = _Sandbox()

    def clean(self, fx=None):
        report = cs.run_checks(self.box.root, fx or _fx())
        self.assertEqual([], report["findings"],
                         "the probe is inert: %s" % cs._render(report))
        return report

    def red(self, kind, fx=None):
        report = cs.run_checks(self.box.root, fx or _fx())
        self.assertIn(kind, _kinds(report), cs._render(report))
        return [f for f in report["findings"] if f["kind"] == kind]


class IdentityArmTest(_Planted):

    def test_a_foreign_author_address_is_named(self):
        self.box.commit(GOOD)
        self.clean()
        self.box.commit("feat(core): a change by somebody else",
                        email="someone@bigcorp.example")
        hits = self.red("foreign_author_email")
        self.assertIn("someone@bigcorp.example", hits[0]["detail"])

    def test_a_foreign_committer_address_is_named(self):
        # The committer is a DIFFERENT field, set from a different key, and is
        # the one a rebase rewrites -- an author-only check would miss this.
        self.box.commit(GOOD)
        self.clean()
        self.box.git("commit", "-q", "--allow-empty", "-m",
                     "feat(core): a change committed by somebody else",
                     env={"GIT_AUTHOR_NAME": NAME, "GIT_AUTHOR_EMAIL": EMAIL,
                          "GIT_COMMITTER_NAME": NAME,
                          "GIT_COMMITTER_EMAIL": "ci-bot@vendor.example",
                          "GIT_AUTHOR_DATE": _iso(DAY),
                          "GIT_COMMITTER_DATE": _iso(DAY)})
        hits = self.red("foreign_committer_email")
        self.assertIn("ci-bot@vendor.example", hits[0]["detail"])

    def test_a_foreign_author_name_is_named(self):
        self.box.commit(GOOD)
        self.clean()
        self.box.commit("feat(core): a change under another name",
                        name="Somebody Else")
        self.assertIn("Somebody Else", self.red("foreign_author_name")[0]["detail"])

    def test_an_address_in_the_message_body_is_named(self):
        self.box.commit(GOOD)
        self.clean()
        self.box.commit("feat(core): a change that quotes a mailbox",
                        body="Reported over mail by colleague@bigcorp.example.")
        self.assertIn("colleague@bigcorp.example",
                      self.red("foreign_address_in_message")[0]["detail"])

    def test_a_co_authored_by_trailer_is_named(self):
        self.box.commit(GOOD)
        self.clean()
        self.box.commit("feat(core): a change with a second author",
                        body="Co-Authored-By: A Tool <tool@vendor.example>")
        self.assertTrue(self.red("attribution_trailer"))

    def test_a_tool_attribution_line_without_an_address_is_named(self):
        # `Generated with ...` carries no address at all, so the email sweep
        # alone cannot see it. This is why the trailer check is separate.
        self.box.commit(GOOD)
        self.clean()
        self.box.commit("feat(core): a change a tool wrote",
                        body="Generated with SomeAssistant")
        report = cs.run_checks(self.box.root, _fx())
        self.assertEqual(["attribution_trailer"], _kinds(report))


class WindowArmTest(_Planted):

    def test_a_commit_dated_before_the_branch_point_is_named(self):
        self.box.commit(GOOD)
        self.clean()
        self.box.commit("feat(core): a change dated before the base",
                        day="2026-08-01")
        self.assertTrue(self.red("predates_branch_point"))

    def test_a_future_dated_commit_is_named(self):
        self.box.commit(GOOD)
        self.clean()
        future = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        self.box.commit("feat(core): a change from the future", day=future)
        self.assertTrue(self.red("dated_in_the_future"))

    def test_a_committer_date_preceding_the_author_date_is_named(self):
        # A real commit cannot be committed before it was authored; a crude
        # backdating forgery produces exactly this inversion.
        self.box.commit(GOOD)
        self.clean()
        self.box.commit("feat(core): a change committed before it was authored",
                        author_hour=18, committer_hour=6)
        self.assertTrue(self.red("committer_precedes_author"))

    def test_a_window_widened_to_admit_a_commit_reddens_on_the_other_side(self):
        # The two-directional property: `first` is pinned by VALUE and asserted
        # equal to reality, so you cannot buy silence by loosening the bound.
        self.box.commit(GOOD, day="2026-08-11")
        self.clean()
        self.box.commit("feat(core): a change dated earlier than declared",
                        day="2026-08-10")
        self.assertTrue(self.red("before_declared_window"))
        widened = _fx(window={"first": "2026-08-01"})
        self.assertTrue(self.red("declared_first_is_stale", fx=widened))


class MessageArmTest(_Planted):

    def test_a_non_conventional_subject_is_named(self):
        self.box.commit(GOOD)
        self.clean()
        self.box.commit("made the retrieval ladder faster than before")
        self.assertTrue(self.red("non_conventional_subject"))

    def test_a_junk_subject_is_named(self):
        self.box.commit(GOOD)
        self.clean()
        self.box.commit("feat(core): wip on the retrieval ladder")
        self.assertTrue(self.red("junk_subject"))

    def test_a_short_subject_is_named(self):
        self.box.commit(GOOD)
        self.clean()
        self.box.commit("fix(a): b")
        self.assertTrue(self.red("subject_too_short"))

    def test_a_trailing_period_is_named(self):
        self.box.commit(GOOD)
        self.clean()
        self.box.commit("feat(core): land the retrieval ladder change.")
        self.assertTrue(self.red("subject_trailing_period"))

    def test_an_exemption_licenses_exactly_its_own_subject(self):
        bad = "made the retrieval ladder faster than before"
        other = "also changed something else entirely here"
        self.box.commit(bad)
        fx = _fx(subject_exemptions=[
            {"subject": bad, "reason": "a pre-convention subject kept verbatim"}])
        self.assertEqual([], cs.run_checks(self.box.root, fx)["findings"])
        self.box.commit(other)
        self.assertIn("non_conventional_subject",
                      _kinds(cs.run_checks(self.box.root, fx)))

    def test_an_exemption_whose_subject_now_conforms_is_named(self):
        self.box.commit(GOOD)
        fx = _fx(subject_exemptions=[
            {"subject": GOOD, "reason": "a row that has outlived its violation"}])
        self.assertIn("stale_exemption", _kinds(cs.run_checks(self.box.root, fx)))

    def test_an_exemption_matching_no_commit_is_named(self):
        self.box.commit(GOOD)
        fx = _fx(subject_exemptions=[
            {"subject": "a subject no commit in the range carries",
             "reason": "a row left behind by a rewritten commit message"}])
        self.assertIn("orphan_exemption", _kinds(cs.run_checks(self.box.root, fx)))


class ShapeArmTest(_Planted):

    def test_a_merge_commit_is_named(self):
        self.box.commit(GOOD)
        self.clean()
        self.box.merge_commit()
        self.assertTrue(self.red("not_linear"))

    def test_a_series_shorter_than_its_floor_is_named(self):
        self.box.commit(GOOD)
        self.box.commit("feat(core): a second change on the branch")
        self.clean(_fx(count={"min": 2}))
        self.box.git("reset", "-q", "--hard", "HEAD~1")
        self.assertTrue(self.red("series_shrank", fx=_fx(count={"min": 2})))

    def test_a_range_not_descending_from_the_base_is_named(self):
        self.box.commit(GOOD)
        self.clean()
        self.box.git("checkout", "-q", "--orphan", "detached")
        self.box.commit("feat(core): a change with no shared ancestry")
        self.assertTrue(self.red("wrong_base"))


class SchemaTest(unittest.TestCase):
    """The declaration is hostile to being widened."""

    def test_a_declared_address_is_a_schema_error(self):
        # The escape hatch a maintainer would reach for -- writing both
        # addresses down -- is refused by the schema, not merely discouraged.
        fx = _fx(identity={"anchor": cs.BASE_AUTHOR_ANCHOR,
                           "emails": [EMAIL, "other@example.invalid"]})
        errors = cs.schema_errors(fx)
        self.assertTrue(any("ANCHORED, never declared" in e for e in errors), errors)

    def test_a_declared_name_is_a_schema_error(self):
        fx = _fx(identity={"anchor": cs.BASE_AUTHOR_ANCHOR, "name": NAME})
        self.assertTrue(cs.schema_errors(fx))

    def test_an_unknown_anchor_is_a_schema_error(self):
        self.assertTrue(cs.schema_errors(_fx(identity={"anchor": "whoever"})))

    def test_a_declared_ceiling_is_a_schema_error(self):
        errors = cs.schema_errors(_fx(count={"min": 1, "max": 200}))
        self.assertTrue(any("count.max" in e for e in errors), errors)

    def test_a_shrug_reason_is_a_schema_error(self):
        fx = _fx(subject_exemptions=[{"subject": "x", "reason": "legacy"}])
        errors = cs.schema_errors(fx)
        self.assertTrue(any("shrug" in e for e in errors), errors)

    def test_duplicate_exemption_subjects_are_a_schema_error(self):
        row = {"subject": "same", "reason": "a reason long enough to be real here"}
        errors = cs.schema_errors(_fx(subject_exemptions=[row, dict(row)]))
        self.assertTrue(any("duplicate" in e for e in errors), errors)

    def test_a_bad_window_date_is_a_schema_error(self):
        self.assertTrue(cs.schema_errors(_fx(window={"first": "August 2026"})))


class UnreadableRangeTest(unittest.TestCase):

    def test_an_unreadable_range_raises_rather_than_reporting_clean(self):
        # "0 findings" must never be what a missing ref prints.
        box = _Sandbox()
        with self.assertRaises(ValueError):
            cs.run_checks(box.root, _fx(range={"base_ref": "no-such-ref",
                                               "head_ref": "HEAD"}))

    def test_an_unresolvable_anchor_raises_rather_than_defaulting(self):
        # "I could not tell whose commits these are" must never read the same
        # as "they are all the operator's".
        box = _Sandbox()
        with self.assertRaises(ValueError):
            cs.anchor_identity(box.root, "no-such-ref")


class EntrypointTest(unittest.TestCase):
    """The documented command must be able to fail (the task-78 lesson)."""

    def test_the_module_runs_as_a_command_and_exits_zero_on_the_live_tree(self):
        proc = subprocess.run([sys.executable,
                               os.path.join(TESTS_DIR, "_commit_series.py")],
                              capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("0 finding(s)", proc.stdout)

    def test_the_command_exits_one_against_a_repo_with_a_planted_defect(self):
        box = _Sandbox()
        box.commit("feat(core): a change by somebody else",
                   email="someone@bigcorp.example")
        proc = subprocess.run([sys.executable,
                               os.path.join(TESTS_DIR, "_commit_series.py"),
                               "--repo", box.root, "--arm", cs.IDENTITY],
                              capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
