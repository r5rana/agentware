"""Task-83 suite for the OBJECT-STORE surface.

The surface exists because reachability is not publishability, so almost every
probe here builds a synthetic repository in which those two answers DIFFER and
demands that the gate follow the second one. A probe that only ever put a commit
on the branch under review would be re-testing `_commit_series`.

Three things this suite is careful about.

**Every synthetic repo is authored with an explicit identity**, set per-repo
through `-c user.*` rather than inherited, so the probes behave identically on a
contributor's machine and on a CI runner with no global git config -- and so
"foreign" means what the test says it means rather than what the box happens to
be configured for.

**The four reach classes are each proven by CONSTRUCTION**, not by asserting the
classifier's own output back at it: a `pushable` commit is put on a second local
branch, a `mirror_only` one under `refs/remotes/`, and an `unreachable` one is
committed and then abandoned by moving the branch off it. Then the SAME foreign
identity is placed in each, so the difference in verdict can only come from the
reach class.

**The ratchet is proven in both directions in one repo**: declare the row and it
goes green, then delete the ref that made it publishable and the same declaration
must go red as stale. A ratchet only tested forwards is an allowlist.
"""

from tests._paths import TESTS_DIR

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    from tests import _portability_store as ps
except ImportError:  # pragma: no cover - direct-script invocation
    import _portability_store as ps

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)

OWNER = "Local Author"
OWNER_EMAIL = "local@example.invalid"
FOREIGN = "Someone Else"
FOREIGN_EMAIL = "someone@example.invalid"


def git(repo, *args, **kw):
    env = dict(os.environ)
    env.update(kw.pop("env", {}))
    proc = subprocess.run(["git", "-C", repo] + list(args), env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        raise AssertionError("git %s failed in %s:\n%s"
                             % (" ".join(args), repo,
                                proc.stdout.decode("utf-8", "replace")))
    return proc.stdout.decode("utf-8", "replace").strip()


def commit(repo, message, name=OWNER, email=OWNER_EMAIL, body="x"):
    """One commit with a FULLY specified identity on both author and committer."""
    with open(os.path.join(repo, "f.txt"), "a", encoding="utf-8") as handle:
        handle.write(body + "\n")
    git(repo, "add", "-A")
    env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
           "GIT_AUTHOR_DATE": "2026-08-01T12:00:00+00:00",
           "GIT_COMMITTER_DATE": "2026-08-01T12:00:00+00:00"}
    git(repo, "commit", "-q", "-m", message, env=env)
    return git(repo, "rev-parse", "HEAD")


def new_repo(directory):
    """A repo whose base tag is authored by OWNER -- the anchored identity."""
    git(directory, "init", "-q", "-b", "work")
    git(directory, "config", "user.name", OWNER)
    git(directory, "config", "user.email", OWNER_EMAIL)
    git(directory, "config", "commit.gpgsign", "false")
    commit(directory, "base")
    git(directory, "tag", "base")
    return directory


def ledger_file(directory, rows, schema=ps.LEDGER_SCHEMA):
    path = os.path.join(directory, "residue.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"schema": schema, "rows": rows}, handle)
    return path


def row(sha, reach=ps.PUSHABLE, owner="task 84", reason=None):
    return {"sha": sha, "reach": reach, "owner": owner,
            "reason": reason or ("a synthetic residue row whose stated remedy "
                                 "is to delete the ref that publishes it")}


class RepoCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-store-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = new_repo(self.tmp)
        # DEFAULT TO AN EMPTY LEDGER, never to the committed one: `ps.check`
        # falls back to the real fixture, whose three rows describe refs that do
        # not exist in a synthetic repo, so every probe would inherit three
        # stale findings it never asked for.
        self.empty = ledger_file(self.tmp, [])

    def check(self, ledger=None, base="base", head=None):
        return ps.check(self.repo, base, head, ledger or self.empty)


class ReachClassificationTest(RepoCase):

    """The same foreign commit, four places, four different verdicts."""

    def test_a_commit_on_the_branch_under_review_is_branch_reach(self):
        sha = commit(self.repo, "on the branch")
        report = self.check()
        found = next(r for r in report["rows"] if r["sha"] == sha)
        self.assertEqual(found["reach"], ps.BRANCH)

    def test_a_commit_on_another_local_branch_is_pushable(self):
        git(self.repo, "checkout", "-q", "-b", "side")
        sha = commit(self.repo, "on a side branch")
        git(self.repo, "checkout", "-q", "work")
        found = next(r for r in self.check()["rows"] if r["sha"] == sha)
        self.assertEqual(found["reach"], ps.PUSHABLE)

    def test_a_commit_reachable_only_from_a_tag_is_pushable(self):
        """`git push --tags` publishes a tag whose branch was deleted."""
        git(self.repo, "checkout", "-q", "-b", "tagged")
        sha = commit(self.repo, "tagged only")
        git(self.repo, "tag", "keep")
        git(self.repo, "checkout", "-q", "work")
        git(self.repo, "branch", "-q", "-D", "tagged")
        found = next(r for r in self.check()["rows"] if r["sha"] == sha)
        self.assertEqual(found["reach"], ps.PUSHABLE)

    def test_a_commit_reachable_only_from_a_remote_tracking_ref_is_mirror_only(self):
        git(self.repo, "checkout", "-q", "-b", "upstream")
        sha = commit(self.repo, "upstream state")
        git(self.repo, "checkout", "-q", "work")
        git(self.repo, "update-ref", "refs/remotes/origin/main", sha)
        git(self.repo, "branch", "-q", "-D", "upstream")
        found = next(r for r in self.check()["rows"] if r["sha"] == sha)
        self.assertEqual(found["reach"], ps.MIRROR_ONLY)

    def test_a_commit_in_a_private_namespace_is_mirror_only(self):
        """A filter-branch original ref: only `--mirror` would carry it."""
        git(self.repo, "checkout", "-q", "-b", "orig")
        sha = commit(self.repo, "pre-rewrite tip")
        git(self.repo, "checkout", "-q", "work")
        git(self.repo, "update-ref", "refs/original/refs/heads/work", sha)
        git(self.repo, "branch", "-q", "-D", "orig")
        found = next(r for r in self.check()["rows"] if r["sha"] == sha)
        self.assertEqual(found["reach"], ps.MIRROR_ONLY)

    def test_an_abandoned_commit_is_unreachable(self):
        git(self.repo, "checkout", "-q", "-b", "doomed")
        sha = commit(self.repo, "abandoned")
        git(self.repo, "checkout", "-q", "work")
        git(self.repo, "branch", "-q", "-D", "doomed")
        found = next(r for r in self.check()["rows"] if r["sha"] == sha)
        self.assertEqual(found["reach"], ps.UNREACHABLE)

    def test_the_base_refs_own_history_is_never_reported_as_gained(self):
        base_sha = git(self.repo, "rev-parse", "base")
        self.assertNotIn(base_sha, [r["sha"] for r in self.check()["rows"]])

    def test_the_store_sees_a_commit_the_range_walk_cannot(self):
        """THE REASON THIS SURFACE EXISTS, asserted as a difference.

        The abandoned commit is absent from `git log base..HEAD` and present in
        the object store. If a future author reimplements the census on
        `rev-list` this reddens, which is the only way to stop that rewrite --
        it would pass every other test in this file.
        """
        git(self.repo, "checkout", "-q", "-b", "doomed")
        sha = commit(self.repo, "abandoned")
        git(self.repo, "checkout", "-q", "work")
        git(self.repo, "branch", "-q", "-D", "doomed")
        walked = git(self.repo, "rev-list", "base..HEAD").split()
        self.assertNotIn(sha, walked)
        self.assertIn(sha, ps.store_commits(self.repo))


class IdentityVerdictTest(RepoCase):

    """Who is gated, who is merely reported, and why the difference is real."""

    def _foreign_on(self, branch=None, namespace=None, abandon=False):
        git(self.repo, "checkout", "-q", "-b", branch or "tmp")
        sha = commit(self.repo, "foreign work", FOREIGN, FOREIGN_EMAIL)
        git(self.repo, "checkout", "-q", "work")
        if namespace:
            git(self.repo, "update-ref", namespace, sha)
        if abandon or namespace:
            git(self.repo, "branch", "-q", "-D", branch or "tmp")
        return sha

    def test_a_foreign_commit_on_the_branch_fails_on_both_roles(self):
        sha = commit(self.repo, "foreign on branch", FOREIGN, FOREIGN_EMAIL)
        report = self.check()
        kinds = {f["kind"] for f in report["findings"] if f["sha"] == sha[:12]}
        self.assertEqual(kinds, {ps.FOREIGN_AUTHOR, ps.FOREIGN_COMMITTER})
        self.assertFalse(report["ok"])

    def test_a_foreign_committer_alone_is_caught(self):
        """The field a rebase rewrites, and the one an email sweep misses."""
        with open(os.path.join(self.repo, "f.txt"), "a") as handle:
            handle.write("y\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "split identity",
            env={"GIT_AUTHOR_NAME": OWNER, "GIT_AUTHOR_EMAIL": OWNER_EMAIL,
                 "GIT_COMMITTER_NAME": FOREIGN,
                 "GIT_COMMITTER_EMAIL": FOREIGN_EMAIL})
        kinds = {f["kind"] for f in self.check()["findings"]}
        self.assertEqual(kinds, {ps.FOREIGN_COMMITTER})

    def test_a_foreign_name_under_the_anchored_address_is_caught(self):
        """Name and address are two fields; matching one is not matching both."""
        commit(self.repo, "renamed", "Someone Else", OWNER_EMAIL)
        kinds = {f["kind"] for f in self.check()["findings"]}
        self.assertEqual(kinds, {ps.FOREIGN_AUTHOR, ps.FOREIGN_COMMITTER})

    def test_a_foreign_commit_on_a_side_branch_is_gated(self):
        self._foreign_on("side")
        self.assertFalse(self.check()["ok"])

    def test_a_foreign_commit_reachable_only_by_mirror_is_reported_not_gated(self):
        """Upstream state is not this branch's authorship claim to make."""
        sha = self._foreign_on("up", "refs/remotes/origin/main")
        report = self.check()
        self.assertTrue(report["ok"])
        found = next(r for r in report["rows"] if r["sha"] == sha)
        self.assertEqual(found["reach"], ps.MIRROR_ONLY)

    def test_a_foreign_unreachable_commit_is_reported_not_gated(self):
        """No push can send it and `git gc` removes it; red would be noise."""
        sha = self._foreign_on("doomed", abandon=True)
        report = self.check()
        self.assertTrue(report["ok"])
        self.assertIn(sha, [r["sha"] for r in report["rows"]])

    def test_a_locally_authored_gained_commit_produces_no_finding(self):
        commit(self.repo, "ours")
        self.assertTrue(self.check()["ok"])

    def test_the_finding_names_the_ref_that_keeps_it_alive(self):
        """Attribution IS the remedy; a sha with no ref name is unactionable."""
        self._foreign_on("side")
        detail = self.check()["findings"][0]["detail"]
        self.assertIn("refs/heads/side", detail)

    def test_the_identity_is_anchored_to_the_base_ref_not_to_the_config(self):
        """Re-anchoring to a later base changes who counts as foreign.

        Proven by moving the ANCHOR rather than the commits: the same repository
        yields a finding against one base and none against another, which no
        implementation reading `git config user.email` could reproduce.
        """
        sha = commit(self.repo, "foreign work", FOREIGN, FOREIGN_EMAIL)
        git(self.repo, "tag", "later", sha)
        self.assertFalse(self.check(base="base")["ok"])
        self.assertTrue(self.check(base="later")["ok"])


class RatchetTest(RepoCase):

    """Declared once, in both directions, inside one repository."""

    def setUp(self):
        super().setUp()
        git(self.repo, "checkout", "-q", "-b", "side")
        self.sha = commit(self.repo, "foreign", FOREIGN, FOREIGN_EMAIL)
        git(self.repo, "checkout", "-q", "work")

    def test_undeclared_is_red(self):
        self.assertFalse(self.check(ledger_file(self.tmp, []))["ok"])

    def test_declared_is_green(self):
        self.assertTrue(self.check(ledger_file(self.tmp, [row(self.sha)]))["ok"])

    def test_the_declaration_goes_stale_when_the_ref_goes(self):
        path = ledger_file(self.tmp, [row(self.sha)])
        self.assertTrue(self.check(path)["ok"])
        git(self.repo, "branch", "-q", "-D", "side")
        report = self.check(path)
        self.assertFalse(report["ok"])
        self.assertEqual([f["kind"] for f in report["findings"]],
                         [ps.STALE_ROW])
        self.assertIn("task 84", report["findings"][0]["detail"])

    def test_a_row_for_a_commit_this_store_never_had_is_inapplicable_not_stale(self):
        """A residue row describes ONE working copy's refs, not the world.

        A fresh clone, a CI checkout and the parity sweep's `--shared` sandbox
        never receive a local backup branch, and the commit it names is simply
        absent from their store. Treating that as stale would make this gate
        red everywhere except the machine the row was written on -- which is
        how a committed ratchet becomes a machine-local one.
        """
        report = self.check(ledger_file(self.tmp,
                                        [row(self.sha), row("b" * 40)]))
        self.assertTrue(report["ok"], report["findings"])
        self.assertEqual(report["meta"]["inapplicable"], ["b" * 40])

    def test_absent_and_stale_are_told_apart_in_ONE_repository(self):
        """The distinction proven as a difference, not asserted twice.

        Same ledger, same repo: one row's commit is held-but-unpublishable and
        the other's was never here. Exactly one of them may redden.
        """
        git(self.repo, "branch", "-q", "-D", "side")
        report = self.check(ledger_file(self.tmp,
                                        [row(self.sha), row("c" * 40)]))
        self.assertEqual([f["sha"] for f in report["findings"]],
                         [self.sha[:12]])
        self.assertEqual(report["meta"]["inapplicable"], ["c" * 40])

    def test_a_declaration_forgives_only_its_own_sha(self):
        git(self.repo, "checkout", "-q", "-b", "other")
        second = commit(self.repo, "another foreign", FOREIGN, FOREIGN_EMAIL)
        git(self.repo, "checkout", "-q", "work")
        report = self.check(ledger_file(self.tmp, [row(self.sha)]))
        self.assertEqual({f["sha"] for f in report["findings"]},
                         {second[:12]})

    def test_a_missing_ledger_file_means_no_declarations_not_no_gate(self):
        report = ps.check(self.repo, "base", None,
                          os.path.join(self.tmp, "absent.json"))
        self.assertFalse(report["ok"])


class LedgerSchemaTest(RepoCase):

    """A fixture that could forgive the wrong thing is refused before it runs."""

    def _errors(self, rows, schema=ps.LEDGER_SCHEMA):
        report = self.check(ledger_file(self.tmp, rows, schema))
        return "\n".join(report["schema_errors"])

    def test_a_branch_reach_row_is_a_schema_error(self):
        """The one class no ledger may ever declare."""
        errors = self._errors([row("a" * 40, reach=ps.BRANCH)])
        self.assertIn("may never be declared", errors)

    def test_a_schema_error_makes_the_report_not_ok_without_scanning(self):
        report = self.check(ledger_file(self.tmp,
                                        [row("a" * 40, reach=ps.BRANCH)]))
        self.assertFalse(report["ok"])
        self.assertEqual(report["rows"], [])

    def test_a_row_carrying_an_address_is_refused(self):
        """R-LOC-03: the identity is anchored at runtime, never committed."""
        for field in ("email", "emails", "author_email", "committer_email"):
            bad = row("a" * 40)
            bad[field] = "someone@example.invalid"
            self.assertIn("must not carry an address", self._errors([bad]))

    def test_a_row_without_an_owner_or_reason_is_refused(self):
        for field in ("owner", "reason"):
            bad = row("a" * 40)
            bad[field] = ""
            self.assertIn("missing %r" % field, self._errors([bad]))

    def test_a_shrug_reason_is_refused(self):
        self.assertIn("not a shrug", self._errors([row("a" * 40, reason="tbd")]))

    def test_an_unknown_reach_class_is_refused(self):
        self.assertIn("unknown reach class",
                      self._errors([row("a" * 40, reach="whenever")]))

    def test_a_wrong_schema_id_is_refused(self):
        self.assertIn("expected schema", self._errors([], schema="other@9"))

    def test_rows_must_be_a_list(self):
        path = os.path.join(self.tmp, "r.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"schema": ps.LEDGER_SCHEMA, "rows": {}}, handle)
        self.assertIn("must be a list",
                      "\n".join(self.check(path)["schema_errors"]))


class FailLoudTest(RepoCase):

    """A run that could not look must never print what a clean run prints."""

    def test_an_unresolvable_base_ref_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.check(base="no-such-ref")
        self.assertIn("reaches no commit", str(ctx.exception))

    def test_an_unresolvable_base_ref_exits_2_from_the_cli(self):
        rc, _, err = run_cli(["--repo", self.repo, "--base", "no-such-ref"])
        self.assertEqual(rc, 2)
        self.assertIn("no-such-ref", err)

    def test_a_detached_head_leaves_the_branch_class_empty(self):
        """Never silently treated as the branch under review.

        A detached HEAD has no ref, so nothing is `branch`-reach and nothing can
        be misclassified into the zero-tolerance class -- the commits move to
        `pushable`, where a declaration is at least possible.
        """
        sha = commit(self.repo, "detached work")
        git(self.repo, "checkout", "-q", "--detach")
        report = self.check()
        self.assertIsNone(report["meta"]["head_ref"])
        found = next(r for r in report["rows"] if r["sha"] == sha)
        self.assertEqual(found["reach"], ps.PUSHABLE)

    def test_store_commits_raises_outside_a_repository(self):
        with self.assertRaises(ValueError):
            ps.store_commits(tempfile.mkdtemp(prefix="aw-nogit-"))

    def test_the_census_counts_every_reach_class_even_at_zero(self):
        counts = ps.by_reach(self.check()["rows"])
        self.assertEqual(sorted(counts), sorted(ps.REACH_CLASSES))


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    keep = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        rc = ps.main(argv)
    finally:
        sys.stdout, sys.stderr = keep
    return rc, out.getvalue(), err.getvalue()


class CliTest(RepoCase):

    def test_a_clean_repo_exits_0(self):
        commit(self.repo, "ours")
        self.assertEqual(run_cli(["--repo", self.repo, "--base", "base",
                                  "--ledger", ledger_file(self.tmp, [])])[0], 0)

    def test_a_foreign_branch_commit_exits_1(self):
        commit(self.repo, "theirs", FOREIGN, FOREIGN_EMAIL)
        rc, out, _ = run_cli(["--repo", self.repo, "--base", "base",
                              "--ledger", ledger_file(self.tmp, [])])
        self.assertEqual(rc, 1)
        self.assertIn(ps.FOREIGN_AUTHOR, out)

    def test_census_mode_prints_every_gained_commit(self):
        sha = commit(self.repo, "a listed subject")
        _, out, _ = run_cli(["--repo", self.repo, "--base", "base", "--census",
                             "--ledger", ledger_file(self.tmp, [])])
        self.assertIn(sha[:12], out)
        self.assertIn("a listed subject", out)

    def test_json_mode_is_machine_readable(self):
        commit(self.repo, "ours")
        _, out, _ = run_cli(["--repo", self.repo, "--base", "base", "--json",
                             "--ledger", ledger_file(self.tmp, [])])
        payload = json.loads(out)
        self.assertEqual(payload["schema"], ps.SCHEMA)
        self.assertEqual(payload["meta"]["base_ref"], "base")


class LiveRepositoryTest(unittest.TestCase):

    """Task 83's actual claim, against this repository."""

    @classmethod
    def setUpClass(cls):
        cls.report = ps.check(REPO)  # base + anchor from the commit-series fixture

    def test_the_publishable_branch_set_carries_no_foreign_identity(self):
        """A plain `git push origin merge/modularize` leaks nothing.

        Asserted over the ROWS rather than over the findings, because a finding
        can be silenced by a ledger row and this claim may not be: no row is
        permitted to declare a `branch`-reach commit, and here it is proven
        rather than relied on.

        The non-vacuity guard is on the CENSUS, not on the branch class: a
        detached checkout or a `--shared` sandbox legitimately has no
        branch-reach commit at all, and demanding one would make this suite
        pass only in a working copy.
        """
        name, email = _anchor()
        self.assertGreater(len(self.report["rows"]), 0)
        branch_rows = [r for r in self.report["rows"]
                       if r["reach"] == ps.BRANCH]
        self.assertEqual(ps.foreign_rows(branch_rows, name, email), [])

    def test_the_gate_is_green_through_its_committed_ledger(self):
        self.assertTrue(self.report["ok"], self.report["findings"])

    def test_the_committed_ledger_is_empty(self):
        """Task 83 asserts the invariant is TRUE, not forgiven.

        The ledger machinery stays -- schema-validated, both-ways, and proven
        hermetically above -- but it declares nothing, because the one residue
        this branch produced was fixed rather than declared. Its first version
        held three rows and running the gate showed why they could not stand: a
        row keyed on one working copy's ref topology is stale in every clone,
        where the same backup ref arrives as `refs/remotes/*` and is already
        safe. A ratchet that is green only on the machine that wrote it is not a
        ratchet.
        """
        self.assertEqual(ps.load_ledger().get("rows"), [])

    def test_nothing_outside_the_branch_is_even_gated_here(self):
        """The stronger reading of task 83's claim, asserted directly.

        Not merely "no foreign identity survives the ledger" but "the ledger has
        nothing to do": every gated commit in this store is on the ref under
        review. If a future backup lands on `refs/heads/` this reddens with a
        count, before anyone has to read a finding.
        """
        gated = [r for r in self.report["rows"]
                 if r["reach"] in ps.GATED and r["reach"] != ps.BRANCH]
        self.assertEqual(gated, [])

    def test_the_committed_ledger_has_no_schema_errors(self):
        self.assertEqual(
            ps.schema_errors(ps.load_ledger(), ps.DEFAULT_LEDGER), [])

    def test_every_declared_row_is_publishable_here_or_absent_here(self):
        """The stale half of the ratchet, run against the real ledger.

        A declared sha must be reachable from a ref a push would send, OR not be
        in this store at all. The third state -- held but unpublishable -- is a
        row to delete, and this says so before the gate's own stale finding
        does. In a working copy the rows are publishable; in a clone or on CI
        they are absent; neither is a failure and the middle state always is.
        """
        declared = {r["sha"] for r in ps.load_ledger().get("rows", [])}
        gated = {r["sha"] for r in self.report["rows"]
                 if r["reach"] in ps.GATED}
        absent = set(self.report["meta"]["inapplicable"])
        self.assertEqual(declared - gated - absent, set())

    def test_the_census_reaches_beyond_what_a_range_walk_would_see(self):
        """Where the store holds commits `git log <base>..HEAD` never lists, the
        census sees them. A clone-shaped store (CI) may hold none, so this is
        skipped there; RepoCase proves the reach hermetically either way."""
        walked = set(ps.reachable_from(REPO, self.report["meta"]["base_ref"] + "..HEAD"))
        if not set(_store_commits()) - walked - ps.reachable_from(REPO, self.report["meta"]["base_ref"]):
            self.skipTest("store holds nothing beyond the walked range (clone-shaped)")
        self.assertTrue({r["sha"] for r in self.report["rows"]} - walked)

    def test_both_history_gates_share_one_base_and_one_anchor(self):
        cs = _cs()
        fx = cs.load_fixture()
        self.assertEqual(self.report["meta"]["base_ref"], fx["range"]["base_ref"])
        self.assertEqual(ps.resolve_anchor(REPO)[1:], cs.series_identity(REPO, fx))


def _cs():
    try:
        import tests._commit_series as cs
    except ImportError:  # pragma: no cover
        import _commit_series as cs
    return cs


def _anchor():
    return ps.resolve_anchor(REPO)[1:]


def _store_commits():
    return ps.store_commits(REPO)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
