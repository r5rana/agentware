"""Owning unit file for `agentware_lib.features.skillpropose` — task 99 w11.

The module had no owning file before this one, and it carried two of the wave's
six ceiling rows: `_skill_promote_run` (16 branches) and
`_existing_skill_coverage` (13). The behaviour-named files that touch it
(`test_skill_promote.py`, `test_skill_promote_cluster.py`,
`test_dream_skill_propose.py`) drive it through the dream step and the verb;
neither reaches the report's cap arms deterministically, because both depend on
what `cluster_learnings` finds in a synthesised corpus.

What is asserted here is the module's PROMISE, not its shape:

  * THE TWO CAPS ARE DIFFERENT CAPS. The per-run cap increments `skipped_cap`
    and KEEPS GOING; the global draft cap returns `deferred:` and BREAKS, so no
    later cluster is even considered. Three qualifying clusters is the smallest
    input on which "kept going" and "broke" produce different reports, and both
    are driven at that size.
  * `None` MEANS UNSET, NOT ZERO. `cap=0` is a run that ranks and emits nothing;
    omitting `cap` resolves the configured default. `_resolve_promote_params`
    defaults each parameter independently, so a caller may override one
    threshold without restating the other two.
  * CLEAN IS NOT "NOTHING HAPPENED". `existing` and `skipped_cap` are quiet
    successes and deliberately NOT in the verdict; only a new emission, a defer
    or a block is something a human must act on.
  * IDEMPOTENCY DOES NOT CONSUME THE BUDGET. A cluster whose workorder already
    exists is an `existing` no-op, and the per-run budget is still available to
    the next cluster.
  * THE COVERAGE INDEX IS ORDER-INDEPENDENT AND ITS MATCH IS FIRST-BY-NAME. The
    three sources merge into a dict keyed by name, and the matcher walks the
    sorted list — so two skills covering one tag-spine resolve to the
    lexicographically first, every run.
  * A MISSING PACKAGE SKILLS DIR IS NOT AN ERROR. Coverage is an anti-bloat
    hint; losing a source proposes a duplicate, never a wrong thing.

`cluster_learnings` is injected at THIS module's own name, never at
`ops.skillcluster` — rebinding the real module would redirect every other caller
in the process.

Stdlib-only, hermetic: temp KB under `mkdtemp`, no network, no real corpus.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.features.skillpropose as skillpropose  # noqa: E402


@contextlib.contextmanager
def _captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def _cluster(tag, spine=None, members=None):
    return {"members": members or ["learn-%s-%d" % (tag, i) for i in range(3)],
            "cohesion": 0.9, "density": 0.8, "separation": 0.7,
            "tag_spine": spine, "size": 3}


class CoverageIngestionTest(unittest.TestCase):
    """The three sources, each driven alone, and the closure they share."""

    def _sink(self):
        seen = {}

        def add(name, tokens):
            if not name:
                return
            n = str(name).strip()
            if not n:
                return
            seen.setdefault(n, set()).add(n.lower())
            for t in tokens or ():
                if t:
                    seen[n].add(str(t).strip().lower())
        return seen, add

    def test_the_package_dir_source_contributes_directory_names(self):
        seen, add = self._sink()
        skillpropose._coverage_from_skills_dir(add)
        # The shipped package really does have `.claude/skills/`; this is the
        # one row that would catch a source reading the wrong path.
        self.assertTrue(seen)
        self.assertTrue(all(isinstance(k, str) for k in seen))

    def test_a_missing_package_dir_is_swallowed_rather_than_raised(self):
        seen, add = self._sink()
        real = skillpropose.REPO_ROOT
        try:
            skillpropose.REPO_ROOT = tempfile.mkdtemp(prefix="aw-norepo-")
            skillpropose._coverage_from_skills_dir(add)
        finally:
            shutil.rmtree(skillpropose.REPO_ROOT, ignore_errors=True)
            skillpropose.REPO_ROOT = real
        self.assertEqual(seen, {})

    def test_the_catalog_source_contributes_names_and_tags(self):
        seen, add = self._sink()
        real = skillpropose.load_skill_catalog
        try:
            skillpropose.load_skill_catalog = lambda: (
                {"skills": [{"name": "alpha", "tags": ["T-One", ""]},
                            "not-a-dict",
                            {"tags": ["orphan"]}]}, None)
            skillpropose._coverage_from_catalog(add)
        finally:
            skillpropose.load_skill_catalog = real
        self.assertEqual(seen, {"alpha": {"alpha", "t-one"}})

    def test_a_catalog_that_fails_to_load_contributes_nothing(self):
        seen, add = self._sink()
        real = skillpropose.load_skill_catalog
        try:
            skillpropose.load_skill_catalog = lambda: (None, "boom")
            skillpropose._coverage_from_catalog(add)
        finally:
            skillpropose.load_skill_catalog = real
        self.assertEqual(seen, {})

    def test_the_index_source_takes_ONLY_skills_category_entries(self):
        seen, add = self._sink()
        skillpropose._coverage_from_index(add, {"entries": [
            {"id": "skill-a", "category": "skills", "tags": ["x"]},
            {"id": "learn-b", "category": "learnings", "tags": ["y"]},
            {"id": "skill-c"},                       # no category at all
            "not-a-dict",
        ]})
        self.assertEqual(sorted(seen), ["skill-a"])

    def test_the_index_source_falls_back_from_id_to_title(self):
        seen, add = self._sink()
        skillpropose._coverage_from_index(add, {"entries": [
            {"title": "Titled Only", "category": "skills", "tags": []}]})
        self.assertEqual(sorted(seen), ["Titled Only"])

    def test_a_missing_or_null_entries_key_is_not_an_error(self):
        seen, add = self._sink()
        skillpropose._coverage_from_index(add, {})
        skillpropose._coverage_from_index(add, {"entries": None})
        self.assertEqual(seen, {})


class ExistingSkillCoverageTest(unittest.TestCase):
    def test_the_result_is_sorted_by_name(self):
        cov = skillpropose._existing_skill_coverage(None, {"entries": [
            {"id": "skill-zeta", "category": "skills", "tags": []},
            {"id": "skill-alpha", "category": "skills", "tags": []}]})
        names = [n for n, _t in cov]
        self.assertEqual(names, sorted(names))

    def test_blank_and_whitespace_ids_never_become_coverage_names(self):
        cov = dict(skillpropose._existing_skill_coverage(None, {"entries": [
            {"id": "", "category": "skills", "tags": ["a"]},
            {"id": "   ", "category": "skills", "tags": ["b"]}]}))
        self.assertNotIn("", cov)
        self.assertNotIn("   ", cov)

    def test_a_name_that_appears_twice_MERGES_its_tokens(self):
        cov = dict(skillpropose._existing_skill_coverage(None, {"entries": [
            {"id": "skill-a", "category": "skills", "tags": ["one"]},
            {"id": "skill-a", "category": "skills", "tags": ["two"]}]}))
        self.assertTrue({"one", "two"} <= cov["skill-a"])

    def test_tokens_are_lowercased_so_the_match_is_case_insensitive(self):
        cov = dict(skillpropose._existing_skill_coverage(None, {"entries": [
            {"id": "skill-MiXeD", "category": "skills", "tags": ["MiXeD-Tag"]}]}))
        self.assertIn("mixed-tag", cov["skill-MiXeD"])
        self.assertIn("skill-mixed", cov["skill-MiXeD"])


class MatchExistingSkillTest(unittest.TestCase):
    COV = [("aaa-skill", {"aaa-skill", "shared-spine"}),
           ("zzz-skill", {"zzz-skill", "shared-spine"})]

    def test_the_first_name_in_SORTED_order_wins(self):
        # Two skills covering one spine is the only input on which "first match
        # over a sorted list" is distinguishable from "any match".
        self.assertEqual(
            skillpropose._match_existing_skill(
                {"tag_spine": "shared-spine"}, self.COV), "aaa-skill")

    def test_the_spine_is_matched_case_insensitively(self):
        self.assertEqual(
            skillpropose._match_existing_skill(
                {"tag_spine": "SHARED-SPINE"}, self.COV), "aaa-skill")

    def test_an_absent_blank_or_missing_spine_matches_nothing(self):
        for cluster in ({"tag_spine": "nope"}, {"tag_spine": "  "},
                        {"tag_spine": None}, {}):
            self.assertIsNone(
                skillpropose._match_existing_skill(cluster, self.COV), cluster)

    def test_an_UNSAFE_skill_name_is_never_surfaced(self):
        # A KB skills-category name is external data; a path-shaped or
        # kernel-shaped name riding a workorder is the refusal this guards.
        cov = [("../../etc/passwd", {"../../etc/passwd", "spine"})]
        self.assertIsNone(
            skillpropose._match_existing_skill({"tag_spine": "spine"}, cov))


class ResolvePromoteParamsTest(unittest.TestCase):
    def test_None_resolves_each_parameter_from_config(self):
        cap, edge, dens, sep = skillpropose._resolve_promote_params(
            None, None, None, None)
        th = skillpropose.resolve_skill_promote_thresholds()
        self.assertEqual((edge, dens, sep),
                         (th["edge_min"], th["density_min"],
                          th["separation_min"]))
        self.assertEqual(cap, skillpropose.resolve_skill_promote_cap())

    def test_ZERO_is_an_explicit_value_and_is_NOT_defaulted(self):
        # `if cap is None` rather than `if not cap`: a caller asking for cap=0
        # wants a run that emits nothing, which is a different request from
        # omitting it. `not cap` would silently turn it into the default.
        self.assertEqual(
            skillpropose._resolve_promote_params(0, 0, 0.0, 0.0),
            (0, 0, 0.0, 0.0))

    def test_each_parameter_defaults_INDEPENDENTLY(self):
        th = skillpropose.resolve_skill_promote_thresholds()
        cap, edge, dens, sep = skillpropose._resolve_promote_params(
            7, None, 0.5, None)
        self.assertEqual((cap, edge, dens, sep),
                         (7, th["edge_min"], 0.5, th["separation_min"]))


class PromoteReportCleanTest(unittest.TestCase):
    BASE = {"proposed": [], "extended": [], "deferred": [], "blocked": [],
            "existing": [], "skipped_cap": 0}

    def test_an_empty_run_is_clean(self):
        self.assertTrue(skillpropose._promote_report_clean(dict(self.BASE)))

    def test_each_of_the_four_actionable_fields_makes_it_dirty(self):
        for key in ("proposed", "extended", "deferred", "blocked"):
            report = dict(self.BASE)
            report[key] = ["x"]
            self.assertFalse(skillpropose._promote_report_clean(report), key)

    def test_existing_and_skipped_cap_are_QUIET_successes(self):
        # A re-run over an unchanged corpus, and a run that hit its budget, are
        # not things a human must act on. Folding either into the verdict would
        # make every steady-state nightly run report as dirty.
        report = dict(self.BASE, existing=["auto/x"], skipped_cap=3)
        self.assertTrue(skillpropose._promote_report_clean(report))


class _Clusters(object):
    def __init__(self, clusters):
        self.clusters = clusters
        self.calls = []

    def __call__(self, kdir, data, **kw):
        self.calls.append(dict(kw))
        return [dict(c) for c in self.clusters]


class SkillPromoteRunTest(unittest.TestCase):
    """The report's arms, with the detector injected at THIS module's name."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-promote-w11-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.kdir = os.path.join(self.root, "kb")
        os.makedirs(os.path.join(self.kdir, "work", "auto"))
        self._real = skillpropose.cluster_learnings
        self._prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        # The emitter resolves the KB from the ENVIRONMENT, not from `kdir`.
        # Leaving it unset does not un-configure it — it falls through to the
        # operator's machine config — so it is pointed here explicitly.
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(self._restore)

    def _restore(self):
        skillpropose.cluster_learnings = self._real
        if self._prev is None:
            os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        else:
            os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self._prev

    def run_promote(self, clusters, data=None, **kw):
        fake = _Clusters(clusters)
        skillpropose.cluster_learnings = fake
        with _captured():
            report = skillpropose._skill_promote_run(
                self.kdir, data or {"entries": []}, **kw)
        return report, fake

    def seed_drafts(self, *slugs):
        for slug in slugs:
            d = os.path.join(self.kdir, "work", "auto", slug)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as f:
                f.write("# seeded\n\n> Status: draft\n")

    def test_no_clusters_is_a_clean_empty_report(self):
        report, _f = self.run_promote([])
        self.assertEqual(report["candidates"], 0)
        self.assertTrue(report["clean"])

    def test_dry_run_RANKS_without_emitting_anything(self):
        report, _f = self.run_promote([_cluster("a"), _cluster("b")],
                                      dry_run=True)
        self.assertEqual(len(report["ranked"]), 2)
        self.assertEqual(report["proposed"], [])
        self.assertTrue(report["clean"])
        self.assertEqual(os.listdir(os.path.join(self.kdir, "work", "auto")), [])

    def test_the_PER_RUN_cap_skips_and_KEEPS_GOING(self):
        report, _f = self.run_promote(
            [_cluster("a"), _cluster("b"), _cluster("c")], cap=1)
        self.assertEqual(len(report["proposed"]), 1)
        self.assertEqual(report["skipped_cap"], 2)   # the loop did not break
        self.assertEqual(report["deferred"], [])

    def test_cap_zero_ranks_three_and_emits_none(self):
        report, _f = self.run_promote(
            [_cluster("a"), _cluster("b"), _cluster("c")], cap=0)
        self.assertEqual(report["skipped_cap"], 3)
        self.assertEqual(report["emitted"], 0)
        self.assertTrue(report["clean"])

    def test_the_GLOBAL_draft_cap_defers_and_BREAKS(self):
        # The opposite arm, at the same input size: ONE deferred reason and
        # nothing else, because the loop stopped rather than skipping.
        self.seed_drafts(*["seeded-%d" % i for i in
                           range(skillpropose.WORKORDER_DRAFT_CAP)])
        report, _f = self.run_promote(
            [_cluster("a"), _cluster("b"), _cluster("c")], cap=5)
        self.assertEqual(len(report["deferred"]), 1)
        self.assertEqual(report["skipped_cap"], 0)
        self.assertEqual(report["blocked"], [])
        self.assertFalse(report["clean"])

    def test_an_EXISTING_workorder_is_a_no_op_that_keeps_the_budget(self):
        first = _cluster("a")
        self.seed_drafts("skill-candidate-%s"
                         % skillpropose._skill_candidate_fingerprint(first))
        report, _f = self.run_promote([first, _cluster("b")], cap=1)
        self.assertEqual(len(report["existing"]), 1)
        self.assertEqual(len(report["proposed"]), 1)   # budget was not spent
        self.assertEqual(report["skipped_cap"], 0)

    def test_a_covered_cluster_is_EXTENDED_rather_than_proposed(self):
        report, _f = self.run_promote(
            [_cluster("d", spine="covered-spine")], cap=5,
            data={"entries": [{"id": "skill-shared", "category": "skills",
                               "tags": ["covered-spine"]}]})
        self.assertEqual(report["proposed"], [])
        self.assertEqual([e["skill"] for e in report["extended"]],
                         ["skill-shared"])
        self.assertEqual(report["emitted"], 1)

    def test_a_kernel_path_member_is_BLOCKED_permanently_not_deferred(self):
        # `blocked:` is PERMANENT (draining work/auto/ will not clear it) and
        # `deferred:` is drainable. Reporting one as the other sends the
        # operator to do work that cannot help.
        report, _f = self.run_promote(
            [_cluster("e", members=["scripts/agentware", "AGENTS.md"])], cap=5)
        self.assertEqual(report["deferred"], [])
        self.assertEqual(len(report["blocked"]), 1)
        self.assertTrue(report["blocked"][0].startswith("blocked:"))

    def test_emitted_counts_proposed_PLUS_extended(self):
        report, _f = self.run_promote([_cluster("a"), _cluster("b")], cap=5)
        self.assertEqual(report["emitted"],
                         len(report["proposed"]) + len(report["extended"]))

    def test_explicit_thresholds_reach_the_detector_verbatim(self):
        _report, fake = self.run_promote([], cap=2, edge_min=3,
                                         density_min=0.5, separation_min=0.25)
        self.assertEqual(fake.calls, [{"edge_min": 3, "density_min": 0.5,
                                       "separation_min": 0.25}])

    def test_the_report_echoes_the_RESOLVED_cap_not_the_argument(self):
        report, _f = self.run_promote([], cap=None)
        self.assertEqual(report["cap"], skillpropose.resolve_skill_promote_cap())

    def test_the_report_echoes_an_EXPLICIT_cap_not_the_configured_default(self):
        # `cap` states the budget that was IN FORCE for this run, which is how a
        # reader tells "nothing qualified" from "the budget ran out". Driven on a
        # value that DIFFERS from the configured default, so a report that
        # re-derived the default cannot satisfy it.
        default = skillpropose.resolve_skill_promote_cap()
        explicit = default + 3
        report, _f = self.run_promote([_cluster("a")], cap=explicit)
        self.assertEqual(report["cap"], explicit)
        self.assertNotEqual(report["cap"], default)


if __name__ == "__main__":
    unittest.main()
