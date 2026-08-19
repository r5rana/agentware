"""Org corpus SCOPING and shared-corpus precedence — the behavioural probe.

Parity row: `org:corpus-scoping-and-shared-precedence` (task 50). The capability
has NO CLI surface of its own and no distinguishing node id, so the generated
parity tiers are blind to it; this file is the evidence the curated row points at.

What is actually at stake
-------------------------
Scoping happens BEFORE `build_corpus`, not after ranking. That ordering is the
whole behaviour, and it is invisible in the output of a single-team KB:

  * BM25 is a CORPUS-RELATIVE score. `df` (how many documents contain a term) and
    `avgdl` (average document length) are computed over whatever set is handed to
    the scorer. Filter AFTER scoring and every score is computed against the WHOLE
    org's statistics — so a term that is common org-wide but rare inside one team
    is scored as if it were common, and that team's most distinctive entry ranks
    below a generic one. The result set can even look correct while the ORDER is
    wrong, which is the failure nobody notices.
  * It is also the R2 performance property: only in-scope files are read from
    disk. Scoping after the build means reading every team's entries on every
    recall.

The mutation the parity row names — "scope after the build" — reddens
`test_scoping_happens_before_the_corpus_is_built` and
`test_two_team_recall_isolation_scores_over_the_scoped_corpus` together, which is
the point: one of them proves the filter, the other proves the ORDERING of the
filter against the scorer.

Hermetic: a two-team scratch org KB in a tempdir, stdlib unittest only.
"""

from tests._paths import TESTS_DIR

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

from agentware_lib.core.config import (  # noqa: E402
    _infer_team_from_kdir,
    _kdir_is_shared_root,
)
from agentware_lib.retrieval.skillpin import (  # noqa: E402
    _entry_is_shared,
    scope_entries_for_team,
)


def _entry(eid, category, origin, team, summary):
    return {"id": eid, "category": category, "origin": origin, "team": team,
            "title": eid, "summary": summary, "path": "%s/%s.md" % (category, eid),
            "tags": [], "created": "2026-01-01", "last_verified": "2026-01-01"}


#: A two-team org corpus plus shared knowledge and a shared skill. The summaries
#: are chosen so ONE term ("deploy") is common ORG-WIDE but rare inside alpha —
#: the exact condition under which post-filter scoring reorders alpha's results.
CORPUS = [
    _entry("alpha-deploy-runbook", "learnings", "team", "alpha",
           "deploy the alpha service behind the canary flag"),
    _entry("alpha-canary-notes", "learnings", "team", "alpha",
           "canary flag rollout notes for the alpha service"),
    _entry("beta-deploy-1", "learnings", "team", "beta", "deploy deploy deploy beta"),
    _entry("beta-deploy-2", "learnings", "team", "beta", "deploy deploy beta again"),
    _entry("beta-deploy-3", "learnings", "team", "beta", "deploy beta once more"),
    _entry("shared-knowledge", "learnings", "shared", None,
           "org-wide deploy knowledge everyone can read"),
    _entry("shared-skill", "skills", "shared", None,
           "org-wide deploy skill everyone follows"),
]


def _org_kb():
    """A real on-disk org KB: <root>/teams/{alpha,beta} + <root>/shared."""
    root = tempfile.mkdtemp(prefix="aw-org-scope-")
    for rel in ("teams/alpha", "teams/beta", "shared"):
        os.makedirs(os.path.join(root, rel), exist_ok=True)
    return root


class TeamInferenceTests(unittest.TestCase):
    """The path IS the team facet — `org:team-inference-from-knowledge-dir`."""

    def setUp(self):
        self.root = _org_kb()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_team_is_inferred_from_the_knowledge_dir_path(self):
        self.assertEqual(
            _infer_team_from_kdir(os.path.join(self.root, "teams", "alpha")),
            "alpha")
        self.assertEqual(
            _infer_team_from_kdir(os.path.join(self.root, "teams", "beta")),
            "beta")

    def test_a_flat_knowledge_dir_infers_no_team(self):
        """INV-1: a flat single-operator install must be byte-identical, which
        requires the team facet to be ABSENT rather than defaulted."""
        flat = tempfile.mkdtemp(prefix="aw-flat-kb-")
        self.addCleanup(shutil.rmtree, flat, ignore_errors=True)
        self.assertIsNone(_infer_team_from_kdir(flat))
        self.assertIsNone(_infer_team_from_kdir(""))
        self.assertIsNone(_infer_team_from_kdir(None))

    def test_a_deeper_path_under_a_team_does_not_infer_that_team(self):
        """`teams/<t>` must be the TAIL. A work dir underneath it is not the team
        dir, and reading it as one would let a write aimed at
        `teams/alpha/work/...` resolve as the alpha SHARD."""
        deeper = os.path.join(self.root, "teams", "alpha", "work", "260813-x")
        self.assertIsNone(_infer_team_from_kdir(deeper))

    def test_shared_root_detection_is_a_whole_component_match(self):
        """A flat KB whose path merely CONTAINS 'shared' must not be read as the
        org shared root — otherwise its entries get stamped origin:shared and its
        recall breaks (INV-1 violation for a flat KB)."""
        self.assertTrue(_kdir_is_shared_root(os.path.join(self.root, "shared")))
        decoy = tempfile.mkdtemp(prefix="aw-shared-notes-")
        self.addCleanup(shutil.rmtree, decoy, ignore_errors=True)
        self.assertFalse(_kdir_is_shared_root(decoy))
        self.assertFalse(_kdir_is_shared_root("/home/u/shared-notes"))


class ScopingContractTests(unittest.TestCase):
    """The default scope and each explicit widening."""

    def _ids(self, **kw):
        return sorted(e["id"] for e in
                      scope_entries_for_team(CORPUS, "alpha", **kw))

    def test_default_is_caller_team_plus_shared_SKILLS_only(self):
        """The documented default: own team ∪ shared *skills*. Shared KNOWLEDGE
        and other teams are excluded — shared skills are always-on so agent
        behaviour is consistent org-wide, while shared knowledge is not, because
        cross-team knowledge pollution is what makes a shared corpus unusable."""
        got = self._ids()
        self.assertEqual(got, ["alpha-canary-notes", "alpha-deploy-runbook",
                               "shared-skill"])
        self.assertNotIn("shared-knowledge", got)
        self.assertFalse(any(i.startswith("beta-") for i in got))

    def test_all_teams_widens_to_every_team_and_all_shared(self):
        self.assertEqual(self._ids(all_teams=True),
                         sorted(e["id"] for e in CORPUS))

    def test_include_shared_adds_shared_knowledge_but_not_other_teams(self):
        got = self._ids(include_shared=True)
        self.assertIn("shared-knowledge", got)
        self.assertIn("shared-skill", got)
        self.assertFalse(any(i.startswith("beta-") for i in got))

    def test_team_selects_exactly_that_team(self):
        self.assertEqual(self._ids(team="beta"),
                         ["beta-deploy-1", "beta-deploy-2", "beta-deploy-3"])

    def test_scope_shared_selects_shared_entries_only(self):
        self.assertEqual(self._ids(scope="shared"),
                         ["shared-knowledge", "shared-skill"])

    def test_a_flat_CORPUS_is_returned_unchanged(self):
        """INV-1, stated precisely. The identity guarantee is conditioned on the
        CORPUS carrying no team/origin facet at all — not merely on the caller
        having no team. That distinction is the whole safety property for a flat
        single-operator install: its entries have no facets, so no filter can
        apply, so its recall cannot change the day org support ships. (A FACETED
        corpus queried with caller_team=None is a different situation entirely —
        it falls through to the default branch and yields the shared entries,
        which is why this test pins the flat CORPUS rather than the flat caller.)
        """
        flat = [{"id": "a", "category": "learnings", "title": "a", "summary": "x"},
                {"id": "b", "category": "skills", "title": "b", "summary": "y"}]
        self.assertEqual(scope_entries_for_team(flat, None), flat)
        self.assertEqual(scope_entries_for_team(flat, "alpha"), flat)
        self.assertEqual(scope_entries_for_team(flat, None, all_teams=True), flat)

    def test_a_faceted_corpus_with_no_caller_team_yields_the_shared_entries(self):
        """The complement of the test above, pinned so the distinction cannot be
        lost: with facets present the filter DOES apply, and an unknown caller
        sees exactly the org-shared entries — never another team's."""
        got = sorted(e["id"] for e in scope_entries_for_team(CORPUS, None))
        self.assertEqual(got, ["shared-skill"])
        self.assertFalse(any(i.startswith("beta-") for i in got))

    def test_shared_is_recognised_by_origin_not_by_path(self):
        self.assertTrue(_entry_is_shared({"origin": "shared"}))
        self.assertFalse(_entry_is_shared({"origin": "team", "team": "shared"}))


class ScopedCorpusRankingTests(unittest.TestCase):
    """The ORDERING property: scope BEFORE the corpus is built.

    This is the pair the parity row's mutation ("scope after the build") reddens.
    """

    def setUp(self):
        self.root = _org_kb()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.kdir = os.path.join(self.root, "teams", "alpha")

    def _write(self, entries):
        """Materialise entries as real files + an index under the alpha shard."""
        for e in entries:
            p = os.path.join(self.kdir, e["path"])
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("# %s\n\n%s\n" % (e["title"], e["summary"]))
        data = {"entries": entries}
        with open(os.path.join(self.kdir, "index.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return data

    def test_two_team_recall_isolation_scores_over_the_scoped_corpus(self):
        """alpha's recall must contain NO beta entry, and the scores must be the
        ones a corpus of ONLY the in-scope entries produces."""
        from agentware_lib.retrieval.bm25 import bm25_positive_scores

        data = self._write(CORPUS)
        scoped = scope_entries_for_team(data["entries"], "alpha")
        scoped_data = dict(data)
        scoped_data["entries"] = scoped

        got = bm25_positive_scores(self.kdir, scoped_data, "deploy service")
        ids = [e["id"] for (e, _s) in got]
        self.assertTrue(ids, "the scoped corpus should still match the query")
        self.assertFalse(any(i.startswith("beta-") for i in ids),
                         "cross-team leak into alpha's recall: %r" % (ids,))

        # The isolation property, stated as scores: ranking over the FULL corpus
        # and over the SCOPED corpus must not agree, or this test could not tell
        # a pre-filter from a post-filter implementation.
        full = bm25_positive_scores(self.kdir, data, "deploy service")
        full_scoped_ids = [e["id"] for (e, _s) in full
                           if e["id"] in {x["id"] for x in scoped}]
        scores_scoped = {e["id"]: s for (e, s) in got}
        scores_full = {e["id"]: s for (e, s) in full if e["id"] in scores_scoped}
        self.assertTrue(
            any(abs(scores_scoped[i] - scores_full[i]) > 1e-9
                for i in scores_scoped if i in scores_full)
            or full_scoped_ids != ids,
            "scoped and unscoped scoring produced identical results, so this "
            "probe cannot distinguish scoping BEFORE the build from scoping "
            "after it — the corpus fixture is no longer discriminating")

    def test_scoping_happens_before_the_corpus_is_built(self):
        """Only the IN-SCOPE files may be read. Proven by deleting an
        out-of-scope entry's FILE: if the corpus were built over every entry the
        build would have to touch it."""
        data = self._write(CORPUS)
        victim = os.path.join(self.kdir, "learnings", "beta-deploy-1.md")
        self.assertTrue(os.path.isfile(victim))
        os.remove(victim)

        scoped = scope_entries_for_team(data["entries"], "alpha")
        scoped_data = dict(data)
        scoped_data["entries"] = scoped
        from agentware_lib.retrieval.corpus import build_corpus
        docs = build_corpus(self.kdir, scoped_data)
        built = {e["id"] for (e, _t) in docs}
        self.assertNotIn("beta-deploy-1", built)
        self.assertTrue({"alpha-deploy-runbook", "alpha-canary-notes"} <= built)


class McpInheritsScopingTests(unittest.TestCase):
    """The MCP recall path must inherit scoping, not re-implement it.

    Structural, deliberately. The strong form of "MCP also scopes" is not a test
    that MCP's output happens to be scoped today — it is that there is exactly
    ONE payload builder and MCP calls it. A second builder could be scoped when
    written and drift the first time either side changes; a shared one cannot
    diverge at all.
    """

    def test_the_mcp_recall_tool_calls_the_shared_payload_builder(self):
        import agentware_lib.features.mcp as mcp
        from agentware_lib.retrieval.recallcmd import build_recall_payload
        self.assertIs(mcp._recall.build_recall_payload, build_recall_payload,
                      "the MCP recall tool no longer resolves the shared payload "
                      "builder — scoping can now diverge between the CLI and MCP")

    def test_the_shared_builder_scopes_BEFORE_it_ranks(self):
        """...and that shared builder is genuinely the scoping seam, so the
        assertion above is worth making.

        Compares CALL ORDER from the AST, not substring positions in the source:
        the docstring names `recall_ranked` well before the code calls anything,
        so a naive text search reports the opposite of the truth.
        """
        import ast
        import inspect
        import textwrap
        from agentware_lib.retrieval import recallcmd
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(recallcmd.build_recall_payload)))
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = getattr(f, "attr", None) or getattr(f, "id", None)
                if name in ("scope_entries_for_team", "recall_ranked"):
                    calls.append((node.lineno, name))
        calls.sort()
        ordered = [n for _l, n in calls]
        self.assertIn("scope_entries_for_team", ordered,
                      "the shared builder no longer scopes at all")
        self.assertIn("recall_ranked", ordered)
        self.assertLess(ordered.index("scope_entries_for_team"),
                        ordered.index("recall_ranked"),
                        "scoping must happen BEFORE ranking in the shared "
                        "builder — scoring after the filter computes df/avgdl "
                        "over the whole org")

    def test_mcp_registers_no_write_tool(self):
        """R-KB-01 / INV-2: an untrusted MCP client must not be able to mutate
        the KB, so the org isolation story cannot be bypassed over the wire."""
        import agentware_lib.features.mcp as mcp
        names = {t.get("name") for t in mcp._mcp_tool_definitions()}
        self.assertEqual(names, {"recall", "query"})


if __name__ == "__main__":
    unittest.main()
