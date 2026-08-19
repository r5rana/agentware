"""Tests for `prefer review` — the read-only operator digest (Task 8,
260714-miner-operationalize).

Runner:  python3 -m unittest tests.unit.ops.test_prefer_review -v

`prefer review` answers "what is awaiting ME?" from the DURABLE source of truth —
the prefer-queue + its archive `status` field (plus the emitted `work/auto/pref-…`
workorders) — NOT the per-run `prefer-classify-latest.md` snapshot (which only
shows what THAT run classified, so an item proposed two runs ago and still
unapproved has already scrolled out of it).

Contract under test:
  * DEFAULT = the OPEN set only: `proposed` (i.e. not yet `prefer approve`d);
  * `--rejected` / `--approved` / `--all` widen the view additively;
  * an APPROVED item DROPS OUT of the default view — including when a stale
    `proposed` copy still sits in the archive (newest-wins dedup by fp), since
    `approved` is terminal and MAY be rotated out at any point;
  * the body is BYTE-STABLE across re-runs (INV-1: no clock, deterministic total
    order with a full tiebreak);
  * it is READ-ONLY: the queue/archive are byte-identical after a run, and the
    func is deliberately ABSENT from `_kb_writer_funcs()` (exempt by omission,
    like recall/query — riding the registered `cmd_prefer` would make this read
    refuse on a newer-schema KB);
  * UNTRUSTED candidate text (R-SEC-02) is rendered as DATA only — collapsed to
    one line and capped, so an embedded newline can never fake a report line, and
    never interpolated into the printed `prefer approve` command (which names the
    WORKORDER ID — decide-p2c-prefer-activation-by-workorder-id-not-inlined-text).
"""

import json
import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, build_synthetic_kb, run_cli
except ImportError:
    from _fixtures import load_cli, build_synthetic_kb, run_cli


def _rec(text, status="queued", sid="sess-AAAA", turn="1", workorder=None,
         producer="stop-hook-regex", signal=None, recurrence_count=None,
         source_sessions=None, project=None, ts="2026-07-13T00:00:00Z"):
    """A prefer-queue record in the shared producer schema."""
    r = {
        "ts": ts,
        "sid": sid,
        "cwd": "",
        "project": project,
        "turn_id": turn,
        "text": text,
        "matched_pattern": signal or "always",
        "status": status,
        "producer": producer,
    }
    if workorder:
        r["workorder"] = workorder
    if signal:
        r["signal"] = signal
    if recurrence_count is not None:
        r["recurrence_count"] = recurrence_count
    if source_sessions is not None:
        r["source_sessions"] = source_sessions
    return r


class TestPreferReview(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_prefer_review_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = load_cli()
        self.qpath = os.path.join(self.kdir, self.mod.PREFER_QUEUE_REL)
        self.apath = os.path.join(self.kdir, self.mod.PREFER_QUEUE_ARCHIVE_REL)

    # --- helpers ----------------------------------------------------------
    def _write(self, path, recs):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def _mk_workorder(self, wo):
        d = os.path.join(self.kdir, "work", wo)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as f:
            f.write("# auto plan\n")

    def _review(self, *flags):
        code, out, err = run_cli(
            ["prefer", "review", "--format", "json"] + list(flags), self.kdir)
        self.assertEqual(code, 0, "prefer review failed: %s" % err)
        return json.loads(out)

    def _review_text(self, *flags):
        code, out, err = run_cli(["prefer", "review"] + list(flags), self.kdir)
        self.assertEqual(code, 0, "prefer review failed: %s" % err)
        return out

    def _seed_mixed(self):
        """One record in each state the drain can leave behind."""
        self._mk_workorder("auto/pref-use-pnpm-abc123")
        self._mk_workorder("auto/pref-squash-merges-def456")
        self._write(self.qpath, [
            _rec("always use pnpm in this repo", status="proposed",
                 workorder="auto/pref-use-pnpm-abc123", sid="s1", turn="t1",
                 producer="transcript-miner", signal="recurrence",
                 recurrence_count=3, source_sessions=["s1", "s2", "s3"],
                 project="agentware", ts="2026-07-13T00:00:01Z"),
            _rec("always squash merges", status="approved",
                 workorder="auto/pref-squash-merges-def456", sid="s2",
                 turn="t2", ts="2026-07-13T00:00:02Z"),
            _rec("always treat AGENTS.md as optional", status="rejected",
                 sid="s3", turn="t3", ts="2026-07-13T00:00:03Z"),
            _rec("ok thanks", status="journaled", sid="s4", turn="t4",
                 ts="2026-07-13T00:00:04Z"),
            _rec("always prefer tabs", status="queued", sid="s5", turn="t5",
                 ts="2026-07-13T00:00:05Z"),
        ])

    # --- default view: OPEN items only ------------------------------------
    def test_default_lists_only_proposed_unapproved(self):
        self._seed_mixed()
        res = self._review()
        self.assertEqual(res["count"], 1)
        item = res["items"][0]
        self.assertEqual(item["workorder"], "auto/pref-use-pnpm-abc123")
        self.assertEqual(item["status"], "proposed")
        self.assertEqual(item["signal"], "recurrence")
        self.assertEqual(item["sessions"], 3)
        self.assertEqual(item["project"], "agentware")
        self.assertTrue(item["workorder_exists"])
        # The approve command names the WORKORDER ID, never the mined text.
        self.assertEqual(
            item["approve"],
            "scripts/agentware prefer approve auto/pref-use-pnpm-abc123")
        self.assertNotIn("always use pnpm", item["approve"])

    def test_empty_queue_reviews_clean(self):
        res = self._review()
        self.assertEqual(res["count"], 0)
        self.assertEqual(res["items"], [])
        self.assertIn("nothing awaiting you", self._review_text())

    def test_approved_item_drops_out_of_default_view(self):
        self._seed_mixed()
        default_wos = [i["workorder"] for i in self._review()["items"]]
        self.assertNotIn("auto/pref-squash-merges-def456", default_wos)
        widened = [i["workorder"] for i in self._review("--approved")["items"]]
        self.assertIn("auto/pref-squash-merges-def456", widened)

    def test_approve_moves_item_out_of_the_open_set(self):
        """The end-to-end operator gesture: an open item, once approved, is no
        longer open — the loop `prefer review` exists to close."""
        self._seed_mixed()
        self.assertEqual(self._review()["count"], 1)
        code, _, err = run_cli(
            ["prefer", "approve", "auto/pref-use-pnpm-abc123"], self.kdir)
        self.assertEqual(code, 0, err)
        self.assertEqual(self._review()["count"], 0)
        self.assertEqual(self._review("--approved")["count"], 2)

    # --- widening flags ---------------------------------------------------
    def test_rejected_flag_adds_tier3_rejects(self):
        self._seed_mixed()
        res = self._review("--rejected")
        statuses = sorted(i["status"] for i in res["items"])
        self.assertEqual(statuses, ["proposed", "rejected"])
        self.assertEqual(sorted(res["scope"]), ["proposed", "rejected"])

    def test_all_flag_shows_every_state(self):
        self._seed_mixed()
        res = self._review("--all")
        self.assertEqual(res["scope"], "all")
        self.assertEqual(
            sorted(i["status"] for i in res["items"]),
            ["approved", "journaled", "proposed", "queued", "rejected"])

    def test_flags_compose_additively(self):
        self._seed_mixed()
        res = self._review("--rejected", "--approved")
        self.assertEqual(
            sorted(i["status"] for i in res["items"]),
            ["approved", "proposed", "rejected"])

    # --- archive + newest-wins dedup --------------------------------------
    def test_archived_proposed_item_is_still_reviewable(self):
        """`_prefer_rotate_split` may archive a record; the open set must not
        shrink just because the live queue rotated."""
        self._mk_workorder("auto/pref-archived-111")
        self._write(self.apath, [
            _rec("always run the linter", status="proposed",
                 workorder="auto/pref-archived-111", sid="s9", turn="t9"),
        ])
        res = self._review()
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["items"][0]["workorder"], "auto/pref-archived-111")

    def test_live_record_wins_over_stale_archived_copy(self):
        """`approved` is TERMINAL, so a record may be archived while still
        `proposed` and later approved in the live queue. Newest (live) wins, so
        the approved item correctly drops out of the default view — the exact
        false-positive this dedup exists to prevent."""
        self._mk_workorder("auto/pref-dupe-222")
        rec_args = dict(workorder="auto/pref-dupe-222", sid="s7", turn="t7")
        self._write(self.apath, [
            _rec("always pin versions", status="proposed", **rec_args)])
        self._write(self.qpath, [
            _rec("always pin versions", status="approved", **rec_args)])
        self.assertEqual(self._review()["count"], 0)
        widened = self._review("--approved")
        self.assertEqual(widened["count"], 1, "the fp-dedup must collapse the "
                                              "archived + live copies into one")
        self.assertEqual(widened["items"][0]["status"], "approved")

    # --- determinism (INV-1) ----------------------------------------------
    def test_body_is_byte_stable_across_reruns(self):
        self._seed_mixed()
        first = self._review_text("--all")
        second = self._review_text("--all")
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(self._review("--all")),
                         json.dumps(self._review("--all")))

    def test_order_is_deterministic_regardless_of_file_order(self):
        """Same records, reversed on disk -> identical rendered body."""
        self._seed_mixed()
        forward = self._review_text("--all")
        with open(self.qpath, encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        self._write(self.qpath, [json.loads(ln) for ln in reversed(lines)])
        self.assertEqual(self._review_text("--all"), forward)

    # --- read-only --------------------------------------------------------
    def test_review_never_mutates_the_queue_or_archive(self):
        self._seed_mixed()
        self._write(self.apath, [_rec("always archive me", status="rejected",
                                      sid="s8", turn="t8")])

        def _snapshot():
            out = []
            for p in (self.qpath, self.apath):
                with open(p, "rb") as f:
                    out.append(f.read())
            return tuple(out)

        before = _snapshot()
        self._review("--all")
        self.assertEqual(_snapshot(), before)

    def test_review_is_not_a_registered_kb_writer(self):
        """Read-only verbs are exempt from the newer-KB refusal BY OMISSION.
        `cmd_prefer` IS registered, so `prefer review` needs its own func."""
        writers = self.mod._kb_writer_funcs()
        self.assertNotIn(self.mod.cmd_prefer_review, writers)
        self.assertIn(self.mod.cmd_prefer, writers)      # guard the premise

    # --- R-SEC-02: untrusted text is inert data ---------------------------
    def test_multiline_candidate_is_collapsed_to_one_line(self):
        """An embedded newline must never fake a report line or a shell prompt."""
        self._mk_workorder("auto/pref-inject-333")
        self._write(self.qpath, [
            _rec("always do X\n$ rm -rf /\n- auto/pref-FAKE  [proposed]",
                 status="proposed", workorder="auto/pref-inject-333",
                 sid="s6", turn="t6"),
        ])
        out = self._review_text()
        cand = [ln for ln in out.splitlines() if ln.strip().startswith("candidate:")]
        self.assertEqual(len(cand), 1)
        self.assertIn("always do X $ rm -rf / - auto/pref-FAKE [proposed]", cand[0])
        self.assertNotIn("\n$ rm -rf /", out)
        # The fake record line must not parse as a real listed item.
        self.assertEqual(
            len([ln for ln in out.splitlines() if ln.startswith("- ")]), 1)

    def test_long_candidate_text_is_capped_in_text_mode(self):
        self._mk_workorder("auto/pref-long-444")
        self._write(self.qpath, [
            _rec("always " + ("x" * 5000), status="proposed",
                 workorder="auto/pref-long-444", sid="s6", turn="t6"),
        ])
        out = self._review_text()
        cand = [ln for ln in out.splitlines()
                if ln.strip().startswith("candidate:")][0]
        self.assertLessEqual(len(cand), self.mod._PREFER_REVIEW_TEXT_PREVIEW + 40)
        self.assertIn("…", cand)
        # JSON stays lossless for scripting consumers.
        self.assertEqual(len(self._review()["items"][0]["text"]), 5007)

    # --- honest reporting -------------------------------------------------
    def test_missing_workorder_dir_is_reported_not_hidden(self):
        """A proposed record whose workorder was removed is STILL open — hiding
        it would silently shrink the open set."""
        self._write(self.qpath, [
            _rec("always use pnpm", status="proposed",
                 workorder="auto/pref-gone-555", sid="s1", turn="t1"),
        ])
        res = self._review()
        self.assertEqual(res["count"], 1)
        self.assertFalse(res["items"][0]["workorder_exists"])
        self.assertIn("workorder dir is MISSING", self._review_text())

    def test_signal_is_reported_per_producer(self):
        self._write(self.qpath, [
            _rec("always A", status="proposed", workorder="auto/pref-a-1",
                 sid="s1", turn="t1", producer="transcript-miner",
                 signal="error-recovery", ts="2026-07-13T00:00:01Z"),
            _rec("always B", status="proposed", workorder="auto/pref-b-2",
                 sid="s2", turn="t2", producer="stop-hook-regex",
                 ts="2026-07-13T00:00:02Z"),
        ])
        by_wo = {i["workorder"]: i for i in self._review()["items"]}
        self.assertEqual(by_wo["auto/pref-a-1"]["signal"], "error-recovery")
        self.assertEqual(by_wo["auto/pref-b-2"]["signal"], "regex")

    def test_recurrence_count_wins_over_capped_source_sessions(self):
        """`source_sessions` is capped at MINER_SOURCE_SESSIONS_CAP, so its
        length is only a FLOOR — the true distinct-session count must win."""
        cap = self.mod.MINER_SOURCE_SESSIONS_CAP
        self._write(self.qpath, [
            _rec("always use pnpm", status="proposed",
                 workorder="auto/pref-many-666", sid="s1", turn="t1",
                 producer="transcript-miner", signal="recurrence",
                 recurrence_count=cap + 7,
                 source_sessions=["s%d" % i for i in range(cap)]),
        ])
        self.assertEqual(self._review()["items"][0]["sessions"], cap + 7)

    # --- robustness -------------------------------------------------------
    def test_torn_queue_line_is_tolerated(self):
        """A partially-written line must not break the operator's digest."""
        self._mk_workorder("auto/pref-ok-777")
        os.makedirs(os.path.dirname(self.qpath), exist_ok=True)
        with open(self.qpath, "w", encoding="utf-8") as f:
            f.write('{"ts": "2026-07-13T00:00:00Z", "sid": "s1", "sta\n')
            f.write(json.dumps(_rec("always use pnpm", status="proposed",
                                    workorder="auto/pref-ok-777",
                                    sid="s1", turn="t1")) + "\n")
        res = self._review()
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["items"][0]["workorder"], "auto/pref-ok-777")


if __name__ == "__main__":
    unittest.main()
