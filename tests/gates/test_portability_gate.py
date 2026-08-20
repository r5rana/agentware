"""Task-12 suite for the five-surface portability gate and its debt ratchet.

Three things this suite is careful about.

**Every probe is INDEPENDENT.** The plan asks that a file whose CONTENT carries
the token, a file whose NAME carries it, and a commit whose MESSAGE carries it
each be caught on their own, with the gate naming the surface. So each probe
builds a tree where exactly ONE surface can fire: the filename probe's file is
empty, the commit probe's tree is clean and only its message is dirty. A probe
that fires two surfaces would not tell you which one works.

**The banned tokens are ASSEMBLED FROM PARTS**, here and in the gate, so neither
file is itself a hit for the zero-tolerance scan it performs -- no exemption
needed, because there is nothing to exempt.

**The environment class is private**, so every test that needs it either brings
its own needle file or skips LOUDLY, naming what it looked for. The zero-
tolerance tests never skip: that class is built in and always runs.
"""

from tests._paths import TESTS_DIR

import ast
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    from tests import _portability_gate as pg
    from tests import _portability_debt as pd
except ImportError:  # pragma: no cover - direct-script invocation
    import _portability_gate as pg
    import _portability_debt as pd

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
WORKSPACE = os.path.dirname(REPO)

DONOR = os.path.join(WORKSPACE, ".aw-migration-safety", "donor-Agentware")
STAGING = os.path.join(WORKSPACE, "agentware-scratch",
                       "260813-modular-core-adoption", "staging", "donor")

#: The operator-directed class, assembled so this file carries no literal hit.
PRODUCT = "".join(("whats", "app"))
BRIDGE = "".join(("wa-", "bridge"))
BANNED = tuple("".join(p) for p in (("whats", "app"), ("wa", "bridge"),
                                    ("wa-", "bridge"), ("wa_", "bridge")))

ARTIFACTS = ("tests/_portability_gate.py", "tests/_portability_debt.py",
             "tests/gates/test_portability_gate.py",
             "tests/fixtures/portability-debt.json",
             "tests/fixtures/portability-needles.example.json")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def write(root, rel, body=""):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path


def needle_file(root, *rows):
    payload = {"schema": pg.NEEDLE_SCHEMA, "needles": list(rows)}
    return write(root, "needles.json", json.dumps(payload))


def env_needle(token, match=pg.SUBSTRING, exempt=None):
    return {"class": pg.ENVIRONMENT, "token": token, "match": match,
            "hint": "probe", "exempt": exempt or []}


@contextlib.contextmanager
def isolated_needles(tmp):
    """No ambient needle file: env cleared and the workspace fallback rehomed."""
    saved_env = {k: os.environ.pop(k, None)
                 for k in (pg.NEEDLE_ENV, "AGENTWARE_KNOWLEDGE_DIR")}
    saved_ws = pg.WORKSPACE
    pg.WORKSPACE = tmp
    try:
        yield
    finally:
        pg.WORKSPACE = saved_ws
        for key, value in saved_env.items():
            if value is not None:
                os.environ[key] = value


def scan(root, needles=None, **kwargs):
    return pg.build_report([root], needles_path=needles, **kwargs)


def surfaces(report):
    return sorted({f["surface"] for f in report["findings"]})


def tokens_for(report, surface):
    return sorted(f["token"] for f in report["findings"]
                  if f["surface"] == surface and f["token"])


def git(repo, *args, **kwargs):
    hooks = kwargs.pop("hooks", os.path.join(repo, ".git", "no-hooks"))
    os.makedirs(hooks, exist_ok=True)
    cmd = ["git", "-C", repo, "-c", "core.hooksPath=%s" % hooks,
           "-c", "user.name=Probe", "-c", "user.email=probe@example.invalid",
           "-c", "commit.gpgsign=false"] + list(args)
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def init_repo(root, message, body="clean\n", rel="file.md"):
    subprocess.run(["git", "init", "-q", root],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    write(root, rel, body)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return root


def run_cli(argv):
    out = io.StringIO()
    err = io.StringIO()
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        rc = pg.main(argv)
    finally:
        sys.stdout, sys.stderr = saved
    return rc, out.getvalue() + err.getvalue()


class TempCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aw-portgate-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def tree(self, name="tree"):
        path = os.path.join(self.tmp, name)
        os.makedirs(path, exist_ok=True)
        return path


# --------------------------------------------------------------------------
# the token classes
# --------------------------------------------------------------------------

class TokenClassTest(TempCase):

    def test_portgate_banned_set_holds_all_four_operator_tokens(self):
        built = {row["token"] for row in pg.zero_tolerance_needles()}
        self.assertEqual(built, set(BANNED))

    def test_portgate_zero_tolerance_needles_admit_no_exemption(self):
        for row in pg.zero_tolerance_needles():
            self.assertEqual(row["exempt"], [])
            self.assertEqual(row["match"], pg.SUBSTRING)

    def test_portgate_private_file_may_not_claim_the_zero_tolerance_class(self):
        path = needle_file(self.tmp, {"class": pg.ZERO_TOLERANCE,
                                      "token": "x", "match": pg.SUBSTRING})
        with self.assertRaises(ValueError) as ctx:
            pg.load_needles(path)
        self.assertIn(pg.ZERO_TOLERANCE, str(ctx.exception))

    def test_portgate_malformed_needle_raises_instead_of_loading_none(self):
        for row in ({"token": "x"}, {"token": "x", "match": "regex"},
                    {"match": pg.SUBSTRING},
                    {"token": "x", "match": pg.SUBSTRING, "exempt": "webui"}):
            with self.assertRaises(ValueError):
                pg.load_needles(needle_file(self.tmp, row))

    def test_portgate_absent_needle_file_reports_unavailable_not_clean(self):
        with isolated_needles(self.tmp):
            rows, meta = pg.load_needles()
        self.assertEqual(meta[pg.ENVIRONMENT]["status"], "unavailable")
        self.assertEqual(meta[pg.ENVIRONMENT]["count"], 0)
        self.assertTrue(meta[pg.ENVIRONMENT]["searched"])
        self.assertEqual(meta[pg.ZERO_TOLERANCE]["status"], "builtin")
        self.assertEqual(len(rows), 4)

    def test_portgate_require_class_fails_when_the_class_is_unavailable(self):
        root = self.tree()
        write(root, "ok.md", "clean\n")
        with isolated_needles(self.tmp):
            rc, out = run_cli(["--tree", root, "--require-class",
                               pg.ENVIRONMENT])
        self.assertEqual(rc, 2)
        self.assertIn("unavailable", out)


# --------------------------------------------------------------------------
# the three independent probes the plan names
# --------------------------------------------------------------------------

class ProbeTest(TempCase):

    def test_portgate_content_probe_is_caught_on_the_contents_surface(self):
        root = self.tree()
        write(root, "notes.md", "a line naming %s here\n" % PRODUCT)
        report = scan(root)
        self.assertEqual(surfaces(report), [pg.CONTENTS])
        hit = report["findings"][0]
        self.assertEqual(hit["class"], pg.ZERO_TOLERANCE)
        self.assertEqual(hit["token"], PRODUCT)
        self.assertTrue(hit["location"].startswith("notes.md:"))

    def test_portgate_filename_probe_is_caught_on_the_filenames_surface(self):
        root = self.tree()
        write(root, "%s.md" % BRIDGE, "")
        report = scan(root)
        self.assertEqual(surfaces(report), [pg.FILENAMES])
        self.assertEqual(tokens_for(report, pg.FILENAMES), [BRIDGE])

    def test_portgate_commit_message_probe_is_caught_on_the_range_surface(self):
        # No tree target: the working tree is clean and only the history is
        # dirty, so a hit can have come from nowhere else.
        root = init_repo(self.tree(), "chore: mention %s in a message" % PRODUCT)
        report = pg.build_report([], repo=root, rng="HEAD")
        self.assertEqual(surfaces(report), [pg.COMMIT_RANGE])
        hit = report["findings"][0]
        self.assertTrue(hit["location"].endswith(":message"))
        self.assertEqual(hit["token"], PRODUCT)

    def test_portgate_a_committed_message_also_lands_in_the_editor_sidecar(self):
        # The same message, seen by a DIFFERENT surface: `git commit` writes
        # .git/COMMIT_EDITMSG, which travels with any copied .git and is not a
        # tracked file. Deleting the branch would not remove it.
        root = init_repo(self.tree(), "chore: mention %s in a message" % PRODUCT)
        report = pg.build_report([root])
        located = [f["location"] for f in report["findings"]
                   if f["surface"] == pg.GIT_METADATA]
        self.assertIn("git-sidecar:.git/COMMIT_EDITMSG", located)

    def test_portgate_each_probe_fires_exactly_one_surface(self):
        content = self.tree("c")
        write(content, "a.md", PRODUCT)
        name = self.tree("n")
        write(name, "%s.md" % BRIDGE, "")
        message = init_repo(self.tree("m"), "feat: %s" % PRODUCT)
        for root, expected in ((content, pg.CONTENTS), (name, pg.FILENAMES)):
            self.assertEqual(surfaces(scan(root)), [expected], root)
        self.assertEqual(
            surfaces(pg.build_report([], repo=message, rng="HEAD")),
            [pg.COMMIT_RANGE])

    def test_portgate_a_probe_tree_exits_non_zero_and_names_its_surface(self):
        root = self.tree()
        write(root, "%s.md" % BRIDGE, "")
        rc, out = run_cli(["--tree", root])
        self.assertEqual(rc, 1)
        self.assertIn(pg.FILENAMES, out)
        self.assertIn("FAIL", out)


# --------------------------------------------------------------------------
# all five surfaces, and what each one alone can see
# --------------------------------------------------------------------------

class SurfaceTest(TempCase):

    def test_portgate_git_metadata_catches_a_remote_url_and_a_sidecar(self):
        root = init_repo(self.tree(), "chore: clean message")
        git(root, "remote", "add", "origin",
            "ssh://%s.example.invalid/pkg" % PRODUCT)
        write(root, os.path.join(".git", "COMMIT_EDITMSG"),
              "chore: %s in the editor sidecar\n" % BRIDGE)
        report = scan(root)
        self.assertIn(pg.GIT_METADATA, surfaces(report))
        located = {f["location"] for f in report["findings"]
                   if f["surface"] == pg.GIT_METADATA}
        self.assertIn("git-config:remote.origin.url", located)
        self.assertIn("git-sidecar:.git/COMMIT_EDITMSG", located)

    def test_portgate_empty_directory_is_reported_structurally(self):
        root = self.tree()
        write(root, "kept.md", "clean\n")
        os.makedirs(os.path.join(root, "hollow", "inner"))
        report = scan(root)
        empties = [f for f in report["findings"]
                   if f["surface"] == pg.EMPTY_DIRS]
        # Reported at EVERY level of the hollow chain, not only at its leaf.
        self.assertEqual([f["location"] for f in empties],
                         ["hollow", os.path.join("hollow", "inner")])
        self.assertEqual(empties[0]["kind"], "structural")
        self.assertIsNone(empties[0]["class"])

    def test_portgate_an_empty_directory_name_is_token_scanned_too(self):
        root = self.tree()
        write(root, "kept.md", "clean\n")
        os.makedirs(os.path.join(root, "%s-logs" % PRODUCT))
        report = scan(root)
        self.assertEqual(tokens_for(report, pg.EMPTY_DIRS), [PRODUCT])

    def test_portgate_commit_range_sees_author_identity_and_paths(self):
        root = init_repo(self.tree(), "chore: clean", rel="%s.md" % BRIDGE)
        items = dict(pg.commit_range_items(root, "HEAD"))
        self.assertTrue(any(k.endswith(":author") for k in items))
        self.assertTrue(any(k.endswith(":committer") for k in items))
        self.assertIn("commit-path:%s.md" % BRIDGE, items)

    def test_portgate_half_a_commit_range_target_is_refused(self):
        root = self.tree()
        for kwargs in ({"repo": root}, {"rng": "HEAD"}):
            with self.assertRaises(ValueError):
                pg.build_report([root], **kwargs)
        rc, out = run_cli(["--tree", root, "--range", "HEAD"])
        self.assertEqual(rc, 2)
        self.assertIn("commit-range surface needs", out)

    def test_portgate_declared_surfaces_are_all_reachable(self):
        self.assertEqual(len(pg.SURFACES), 5)
        counted = pg._counts([])
        for surface in pg.SURFACES:
            self.assertIn(surface, counted)


# --------------------------------------------------------------------------
# match semantics
# --------------------------------------------------------------------------

class MatchTest(TempCase):

    def test_portgate_word_match_does_not_fire_inside_a_longer_word(self):
        root = self.tree()
        write(root, "a.md", "the gazorp was gazorped\n")
        write(root, "b.md", "a zorp, standalone\n")
        needles = needle_file(self.tmp, env_needle("zorp", pg.WORD))
        report = scan(root, needles=needles)
        located = [f["location"] for f in report["findings"]]
        self.assertEqual(len(located), 1)
        self.assertTrue(located[0].startswith("b.md:"))

    def test_portgate_substring_match_fires_inside_a_word(self):
        root = self.tree()
        write(root, "a.md", "the gazorp was gazorped\n")
        needles = needle_file(self.tmp, env_needle("zorp", pg.SUBSTRING))
        self.assertEqual(len(scan(root, needles=needles)["findings"]), 1)

    def test_portgate_exemption_scopes_to_its_path_prefix(self):
        root = self.tree()
        write(root, os.path.join("build", "out.js"), "zorp through\n")
        write(root, os.path.join("src", "in.js"), "zorp through\n")
        needles = needle_file(self.tmp,
                              env_needle("zorp", exempt=["build"]))
        report = scan(root, needles=needles)
        located = [f["location"] for f in report["findings"]]
        self.assertEqual(len(located), 1)
        self.assertTrue(located[0].startswith(os.path.join("src", "in.js")))

    def test_portgate_matching_is_case_insensitive(self):
        root = self.tree()
        write(root, "a.md", "MiXeD %s CASE\n" % PRODUCT.upper())
        self.assertEqual(len(scan(root)["findings"]), 1)

    def test_portgate_a_finding_reports_every_occurrence_it_saw(self):
        root = self.tree()
        write(root, "a.md", "\n".join(["x"] * 4 + [PRODUCT, PRODUCT]) + "\n")
        hit = scan(root)["findings"][0]
        self.assertIn("2 occurrence(s)", hit["detail"])
        self.assertIn("line 5", hit["detail"])


# --------------------------------------------------------------------------
# the debt ratchet
# --------------------------------------------------------------------------

class DebtTest(TempCase):

    def rows(self, **over):
        row = {"surface": pg.CONTENTS, "class": pg.ENVIRONMENT,
               "location": "a.md", "count": 1, "owner": "task 99",
               "reason": "probe"}
        row.update(over)
        return [row]

    def probe_tree(self, token="zorp", copies=1):
        root = self.tree()
        write(root, "a.md", "\n".join([token] * copies) + "\n")
        needles = needle_file(self.tmp, env_needle(token))
        return scan(root, needles=needles)

    def test_portgate_debt_forgives_exactly_the_declared_count(self):
        view = pd.apply_debt(self.probe_tree(), self.rows())
        self.assertTrue(view["ok"])
        self.assertEqual(len(view["forgiven"]), 1)

    def test_portgate_a_finding_above_the_declared_count_is_not_forgiven(self):
        report = self.probe_tree(copies=1)
        report["findings"] = report["findings"] * 2
        view = pd.apply_debt(report, self.rows())
        self.assertFalse(view["ok"])
        self.assertEqual(len(view["findings"]), 1)

    def test_portgate_a_finding_at_an_undeclared_location_is_not_forgiven(self):
        view = pd.apply_debt(self.probe_tree(), self.rows(location="other.md"))
        self.assertFalse(view["ok"])

    def test_portgate_a_stale_debt_row_reddens_the_gate(self):
        clean = pg.build_report([self.tree()])
        view = pd.apply_debt(clean, self.rows())
        self.assertFalse(view["ok"])
        self.assertEqual(view["findings"][0]["kind"], pd.STALE)
        self.assertIn("task 99", view["findings"][0]["detail"])

    def test_portgate_debt_may_never_forgive_a_zero_tolerance_finding(self):
        with self.assertRaises(ValueError):
            pd.validate_row(self.rows(**{"class": pg.ZERO_TOLERANCE})[0], "x")
        root = self.tree()
        write(root, "a.md", PRODUCT)
        forced = self.rows(**{"class": pg.ZERO_TOLERANCE})
        view = pd.apply_debt(scan(root), forced)
        self.assertFalse(view["ok"])
        self.assertEqual(view["findings"][0]["class"], pg.ZERO_TOLERANCE)

    def test_portgate_debt_rows_must_name_an_owner_a_reason_and_no_token(self):
        for bad in ({"owner": " "}, {"reason": ""}, {"count": 0},
                    {"surface": "elsewhere"}, {"token": "leak"}):
            with self.assertRaises(ValueError):
                pd.validate_row(self.rows(**bad)[0], "x")

    def test_portgate_debt_line_numbers_do_not_pin_a_row(self):
        found = pg.finding(pg.CONTENTS, "token", pg.ENVIRONMENT, "t",
                           "a/b.md:412", "d")
        self.assertEqual(pd.bare_location(found), "a/b.md")
        keep = pg.finding(pg.FILENAMES, "token", pg.ENVIRONMENT, "t",
                          "a/b.md", "d")
        self.assertEqual(pd.bare_location(keep), "a/b.md")

    def test_portgate_committed_debt_fixture_is_valid_and_owned(self):
        """NARROWED IN PLACE at task 83, never renamed.

        Task 12 asserted the fixture was non-empty, which was true of every day
        between then and now and is the assertion a burndown must eventually
        falsify: task 83 cleared the last three rows, so "there is at least one
        row" became a demand that the debt never be paid off. What survives is
        the part that was always the point -- every row PRESENT is a valid,
        owned, non-zero-tolerance row -- plus `summarize` still answering over
        whatever the file holds. The emptiness itself is asserted by
        `test_portgate_committed_debt_fixture_is_empty_at_task_83`; splitting
        them keeps this one meaningful if a future wave ever declares a row
        again.
        """
        rows = pd.load_debt()
        for row in rows:
            self.assertIn(row["surface"], pg.SURFACES)
            self.assertNotEqual(row.get("class"), pg.ZERO_TOLERANCE)
            self.assertRegex(row["owner"], r"task \d+")
        self.assertEqual(sorted(pd.summarize(rows)),
                         sorted({r["owner"] for r in rows}))

    def test_portgate_committed_debt_fixture_is_empty_at_task_83(self):
        """The ratchet reached zero, and the gate is green on its own terms.

        The last three rows named task 58 as their owner -- a task that had
        already closed, which is how a shrink-only ledger rots into an
        allowlist. They were cleared by REDACTING the token inside each frozen
        capture rather than by deleting the row that carried it (a capture
        rewritten to hide a deletion destroys the only record of what the other
        tree had), and the gate now passes with no subtraction at all.
        """
        self.assertEqual(pd.load_debt(), [])

    def test_portgate_committed_debt_fixture_carries_no_token_text(self):
        with open(pd.DEFAULT_DEBT, "r", encoding="utf-8") as handle:
            body = handle.read().lower()
        rows, _meta = pg.load_needles()
        for row in rows:
            self.assertNotIn(row["token"], body)


# --------------------------------------------------------------------------
# the real trees this task must prove things about
# --------------------------------------------------------------------------

def sourced_environment():
    _rows, meta = pg.load_needles()
    return meta[pg.ENVIRONMENT]["status"] == "sourced"


class RealTreeTest(unittest.TestCase):

    def zero_tolerance(self, report):
        return [f for f in report["findings"]
                if f["class"] == pg.ZERO_TOLERANCE]

    def test_portgate_repository_tree_has_zero_operator_product_hits(self):
        report = pg.build_report([REPO])
        self.assertEqual(self.zero_tolerance(report), [])

    def test_portgate_branch_commit_range_has_zero_operator_product_hits(self):
        if not pg.is_git_worktree(REPO):
            self.skipTest("not a git work tree: %s" % REPO)
        if pg._git(REPO, "rev-parse", "--verify", "-q",
                   "aw-pre-merge") is None:
            self.skipTest("aw-pre-merge tag absent")
        report = pg.build_report([], repo=REPO, rng="aw-pre-merge..HEAD")
        self.assertEqual(self.zero_tolerance(report), [])

    def test_portgate_staged_donor_has_zero_operator_product_hits(self):
        if not os.path.isdir(STAGING):
            self.skipTest("staged donor absent: %s" % STAGING)
        self.assertEqual(self.zero_tolerance(pg.build_report([STAGING])), [])

    def test_portgate_unsanitized_donor_hits_at_least_three_surfaces(self):
        if not os.path.isdir(DONOR):
            self.skipTest("unsanitized donor absent: %s" % DONOR)
        if not sourced_environment():
            self.skipTest("private %s needles unavailable; searched: %s"
                          % (pg.ENVIRONMENT,
                             ", ".join(pg.needle_search_path())))
        report = pg.build_report([DONOR])
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(len(surfaces(report)), 3, surfaces(report))
        self.assertEqual(self.zero_tolerance(report), [])

    def test_portgate_repository_tree_is_green_under_the_debt_ratchet(self):
        if not sourced_environment():
            self.skipTest("private %s needles unavailable" % pg.ENVIRONMENT)
        view = pd.apply_debt(pg.build_report([REPO]), pd.load_debt())
        self.assertTrue(view["ok"], view["findings"])


# --------------------------------------------------------------------------
# hygiene and the ceilings
# --------------------------------------------------------------------------

class HygieneTest(unittest.TestCase):

    def bodies(self):
        for rel in ARTIFACTS:
            with open(os.path.join(REPO, rel), encoding="utf-8") as handle:
                yield rel, handle.read()

    def test_portgate_no_artifact_carries_a_banned_token(self):
        for rel, body in self.bodies():
            for token in BANNED:
                self.assertNotIn(token, body.lower(), "%s: %s" % (rel, token))

    def test_portgate_no_artifact_carries_an_absolute_home_path(self):
        home = os.path.expanduser("~")
        for rel, body in self.bodies():
            self.assertNotIn(home, body, rel)

    def test_portgate_no_artifact_exceeds_the_tests_ceiling(self):
        for rel, body in self.bodies():
            self.assertLessEqual(len(body.splitlines()), 600, rel)

    def test_portgate_no_function_exceeds_the_per_function_caps(self):
        branchy = (ast.If, ast.For, ast.While, ast.Try, ast.BoolOp,
                   ast.IfExp, ast.comprehension, ast.ExceptHandler)
        for rel, body in self.bodies():
            if not rel.endswith(".py"):
                continue
            for node in ast.walk(ast.parse(body)):
                if not isinstance(node, (ast.FunctionDef,
                                         ast.AsyncFunctionDef)):
                    continue
                span = node.end_lineno - node.lineno + 1
                self.assertLessEqual(span, 120, "%s:%s" % (rel, node.name))
                branches = sum(1 for c in ast.walk(node)
                               if isinstance(c, branchy))
                self.assertLessEqual(branches, 12, "%s:%s" % (rel, node.name))

    def test_portgate_example_needle_file_is_a_valid_example(self):
        path = os.path.join(REPO, ARTIFACTS[4])
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["schema"], pg.NEEDLE_SCHEMA)
        rows, meta = pg.load_needles(path)
        self.assertEqual(meta[pg.ENVIRONMENT]["status"], "sourced")
        self.assertEqual(len(rows), 4 + len(payload["needles"]))


if __name__ == "__main__":
    unittest.main()
