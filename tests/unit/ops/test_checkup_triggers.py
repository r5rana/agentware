"""KB-structure trigger-signal measurement (Task 5).

Runner: python3 -m unittest tests.unit.ops.test_checkup_triggers -v

Proven here (Q8 per-signal measurability): corpus-size fires when index entries
reach the threshold and is inert below it (the one clean live threshold); git-
worktree count measures read-only; multi-tenancy fires under team mode; recall-p50
latency is REPORT-ONLY (fires_workorder False) so it NEVER becomes a candidate even
when over threshold; rebuild-time is DROPPED (no signal with that name — inert by
construction). At solo scale every fireable signal is far under threshold, so the
service ships inert. Read-only throughout.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from tests._fixtures import load_cli
    from tests._operator_config import isolate_operator_config
except ImportError:
    from _fixtures import load_cli
    from _operator_config import isolate_operator_config


class TriggerTests(unittest.TestCase):
    def setUp(self):
        self.m = load_cli()
        self.kb = tempfile.mkdtemp(prefix="aw_ck_trig_")
        self.addCleanup(shutil.rmtree, self.kb, True)
        os.makedirs(os.path.join(self.kb, "learnings"))
        # Isolate config so resolve_kb_mode()/resolve_user_handle() never read the
        # operator's real ~/.agentware/config.env. Shared with the aggregate
        # suite, which was red for exactly the leak this closes.
        isolate_operator_config(self, self.m, self.kb)

    def _index(self, n):
        entries = [{"id": "e%d" % i, "category": "learnings",
                    "path": "learnings/e%d.md" % i} for i in range(n)]
        with open(os.path.join(self.kb, "index.json"), "w", encoding="utf-8") as f:
            json.dump({"entries": entries, "tags": {}}, f)

    def _by_signal(self, out):
        return {t["signal"]: t for t in out}

    def test_corpus_size_inert_under_threshold(self):
        self._index(5)
        t = self._by_signal(self.m._checkup_structure_triggers(self.kb))["corpus-size"]
        self.assertFalse(t["fired"])
        self.assertEqual(t["value"], 5)
        self.assertTrue(t["fires_workorder"])

    def test_corpus_size_fires_over_threshold(self):
        self._index(self.m.CHECKUP_CORPUS_ENTRIES_MAX + 1)
        t = self._by_signal(self.m._checkup_structure_triggers(self.kb))["corpus-size"]
        self.assertTrue(t["fired"])

    def test_multi_tenancy_fires_in_team_mode(self):
        self._index(1)
        os.environ["AGENTWARE_KB_MODE"] = "team"
        self.addCleanup(os.environ.pop, "AGENTWARE_KB_MODE", None)
        t = self._by_signal(
            self.m._checkup_structure_triggers(self.kb))["multi-tenancy"]
        self.assertTrue(t["fired"])

    def test_multi_tenancy_inert_solo(self):
        self._index(1)
        t = self._by_signal(
            self.m._checkup_structure_triggers(self.kb))["multi-tenancy"]
        self.assertFalse(t["fired"])

    def test_multi_tenancy_does_not_count_the_author_placeholder_as_a_person(self):
        # REGRESSION (plan task 89). One operator with a configured handle and a
        # knowledge base that names nobody is ONE person. Before the fix the
        # unresolved `author` default 'operator' joined the set as a second
        # identity, so a bare single-operator KB proposed a structure migration.
        self._index(1)
        os.environ["AGENTWARE_USER_HANDLE"] = "solo"
        self.assertFalse(os.path.exists(os.path.join(self.kb, "MAIN.md")))
        t = self._by_signal(
            self.m._checkup_structure_triggers(self.kb))["multi-tenancy"]
        self.assertEqual(t["value"], 1,
                         "one operator, one handle — the placeholder is not a person")
        self.assertFalse(t["fired"])

    def test_multi_tenancy_still_fires_on_two_genuinely_distinct_handles(self):
        # the other direction: the signal must keep its teeth. A per-user handle
        # that differs from the handle the knowledge base itself names is two
        # real people, and that is exactly what this signal is for.
        self._index(1)
        os.environ["AGENTWARE_USER_HANDLE"] = "alice"
        with open(os.path.join(self.kb, "MAIN.md"), "w", encoding="utf-8") as f:
            f.write("# KB\n\n- **Handle**: bob\n")
        t = self._by_signal(
            self.m._checkup_structure_triggers(self.kb))["multi-tenancy"]
        self.assertEqual(t["value"], 2)
        self.assertTrue(t["fired"])

    def test_multi_tenancy_is_one_person_when_both_sources_name_them(self):
        # the same person configured twice is still one person — the set dedupes,
        # and the fix must not have turned that into a count of SOURCES.
        self._index(1)
        os.environ["AGENTWARE_USER_HANDLE"] = "alice"
        with open(os.path.join(self.kb, "MAIN.md"), "w", encoding="utf-8") as f:
            f.write("# KB\n\n- **Handle**: alice\n")
        t = self._by_signal(
            self.m._checkup_structure_triggers(self.kb))["multi-tenancy"]
        self.assertEqual(t["value"], 1)
        self.assertFalse(t["fired"])

    def test_recall_p50_is_report_only_never_fires_a_workorder(self):
        self._index(1)
        # even if the proxy is over threshold, it can NEVER become a candidate.
        with mock.patch.object(self.m, "_checkup_recall_p50_ms",
                               return_value=99999):
            out = self.m._checkup_structure_triggers(self.kb)
        t = self._by_signal(out)["recall-p50-ms"]
        self.assertTrue(t["fired"])
        self.assertFalse(t["fires_workorder"],
                         "recall-p50 must never drive a workorder on its own")
        # confirm it produces no candidate
        agg = {"triggers": out, "plan_health": {"stalled": []}, "watcher": {}}
        for c in self.m._checkup_candidates(agg):
            self.assertNotEqual(c["kind"], "trigger")

    def test_worktree_count_measured_read_only(self):
        self._index(1)
        t = self._by_signal(
            self.m._checkup_structure_triggers(self.kb))["git-worktrees"]
        # a value (>=1) or None on error; never fires at solo scale
        self.assertFalse(t["fired"])

    def test_rebuild_time_is_dropped_by_construction(self):
        self._index(1)
        signals = {t["signal"]
                   for t in self.m._checkup_structure_triggers(self.kb)}
        self.assertNotIn("rebuild-time", signals,
                         "rebuild-time is inert by construction (would write index.json)")

    def test_missing_index_not_rebuilt(self):
        # REGRESSION (adversarial-review finding 2): structure-trigger measurement
        # must READ the index only — never trigger load_index's rebuild WRITE.
        idx = os.path.join(self.kb, "index.json")
        self.assertFalse(os.path.exists(idx))     # setUp writes none
        with open(os.path.join(self.kb, "learnings", "x.md"), "w",
                  encoding="utf-8") as f:
            f.write("---\nid: learn-x\ncategory: learnings\ntitle: X\n"
                    "created: 2026-01-01\n---\nbody\n")
        out = self.m._checkup_structure_triggers(self.kb)
        self.assertFalse(os.path.exists(idx),
                         "must not rebuild index.json (Q7 read-only)")
        t = self._by_signal(out)["corpus-size"]
        self.assertFalse(t["fired"], "uncheckable corpus size never fires")

    def test_fired_corpus_trigger_becomes_a_candidate(self):
        self._index(self.m.CHECKUP_CORPUS_ENTRIES_MAX + 1)
        out = self.m._checkup_structure_triggers(self.kb)
        agg = {"triggers": out, "plan_health": {"stalled": []}, "watcher": {}}
        cands = self.m._checkup_candidates(agg)
        self.assertTrue(any(c["kind"] == "trigger" for c in cands))


if __name__ == "__main__":
    unittest.main()
