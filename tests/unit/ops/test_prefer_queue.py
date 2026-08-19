"""Tests for the Stop-hook cheap regex candidate queue (Task 5, 260712-steering-capture).

Runner:  python3 -m unittest tests.unit.ops.test_prefer_queue -v

Layer-2 PRODUCER: a cheap regex scan over origin=human turns in logs/prompts.log
that appends INERT preference-shaped candidates to a producer-agnostic queue
(logs/steering/prefer-queue.jsonl). Constraints proven here:
  * only origin=human turns matching a preference cue are queued (loop/system and
    noise turns are skipped);
  * each queued record carries the shared schema (ts, sid, cwd, project via the
    Task-1 canonical normalizer, turn_id, text, matched_pattern, status=queued,
    producer) and is DATA-ONLY (text stored verbatim, never interpolated);
  * a per-session byte-offset WATERMARK makes each human turn queue AT MOST ONCE —
    two consecutive Stops over the same prompts.log append the candidate exactly
    once;
  * the scan is scoped to the current session id (another session's turns are not
    queued by this session's Stop).
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


def _mk_project(kdir, slug):
    d = os.path.join(kdir, "projects", slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "index.md"), "w", encoding="utf-8") as f:
        f.write("# %s\n\n_project._\n" % slug)


def _mk_checkout(root, name):
    """A real on-disk checkout dir (with a .git marker so `_resolve_context`
    resolves its project_name to the basename). Returns the abs path."""
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, ".git"), exist_ok=True)
    return d


def _rec(ts, sid, cwd, origin, turn, prompt):
    """One prompts.log record in the exact log-prompt.sh format."""
    return ("[%s] [session %s] [cwd %s] [origin=%s] [turn=%s]\n%s\n\n"
            % (ts, sid, cwd, origin, turn, prompt))


class TestPreferQueue(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_prefer_queue_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        _mk_project(self.kdir, "tokto-io")
        self.checkouts = tempfile.mkdtemp(prefix="aw_prefer_queue_co_")
        self.addCleanup(shutil.rmtree, self.checkouts, True)
        self.mod = load_cli()
        self.sid = "sess-AAAA"
        os.makedirs(os.path.join(self.kdir, "logs"), exist_ok=True)
        self.logp = os.path.join(self.kdir, "logs", "prompts.log")
        self.qpath = os.path.join(self.kdir, self.mod.PREFER_QUEUE_REL)

    # --- helpers ----------------------------------------------------------
    def _write_log(self, records):
        with open(self.logp, "w", encoding="utf-8") as f:
            f.write("".join(records))

    def _scan(self, cwd="", sid=None):
        return self.mod._prefer_queue_scan(self.kdir, sid or self.sid, cwd,
                                           prompts_path=self.logp)

    def _queue(self):
        if not os.path.isfile(self.qpath):
            return []
        out = []
        with open(self.qpath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    # --- tests ------------------------------------------------------------
    def test_human_preference_turn_is_queued_once(self):
        self._write_log([_rec("2026-07-13T00:00:00Z", self.sid, "/tmp/x",
                              "human", "t1",
                              "Always pin dependency versions, never use ranges.")])
        n = self._scan()
        self.assertEqual(n, 1)
        q = self._queue()
        self.assertEqual(len(q), 1)
        r = q[0]
        self.assertEqual(r["sid"], self.sid)
        self.assertEqual(r["turn_id"], "t1")
        self.assertEqual(r["status"], "queued")
        self.assertEqual(r["cwd"], "/tmp/x")
        self.assertEqual(r["matched_pattern"], "always")
        self.assertEqual(r["producer"], self.mod.PREFER_QUEUE_PRODUCER)
        self.assertIn("Always pin dependency versions", r["text"])
        self.assertIn("ts", r)
        self.assertIn("project", r)

    def test_watermark_second_scan_no_double_append(self):
        """Two consecutive Stops over the SAME prompts.log queue exactly once."""
        self._write_log([_rec("2026-07-13T00:00:00Z", self.sid, "/tmp/x",
                              "human", "t1", "From now on prefer tabs.")])
        self.assertEqual(self._scan(), 1)
        self.assertEqual(self._scan(), 0)          # watermark -> nothing new
        self.assertEqual(len(self._queue()), 1)

    def test_new_turn_after_first_scan_is_queued(self):
        """A watermark advances but still catches genuinely NEW turns."""
        self._write_log([_rec("2026-07-13T00:00:00Z", self.sid, "/tmp/x",
                              "human", "t1", "Always run the tests.")])
        self.assertEqual(self._scan(), 1)
        # Append a second human preference turn, then Stop again.
        with open(self.logp, "a", encoding="utf-8") as f:
            f.write(_rec("2026-07-13T00:01:00Z", self.sid, "/tmp/x",
                         "human", "t2", "Never force-push to main."))
        self.assertEqual(self._scan(), 1)
        q = self._queue()
        self.assertEqual([r["turn_id"] for r in q], ["t1", "t2"])

    def test_loop_and_system_origin_turns_skipped(self):
        self._write_log([
            _rec("2026-07-13T00:00:00Z", self.sid, "/tmp/x", "loop", "t1",
                 "Always pin versions."),
            _rec("2026-07-13T00:00:01Z", self.sid, "/tmp/x", "system", "t2",
                 "Never do X. <system-reminder>"),
        ])
        self.assertEqual(self._scan(), 0)
        self.assertEqual(self._queue(), [])

    def test_noise_human_turn_not_queued(self):
        """A human turn with NO preference cue is not queued."""
        self._write_log([_rec("2026-07-13T00:00:00Z", self.sid, "/tmp/x",
                              "human", "t1", "What is the status of the build?")])
        self.assertEqual(self._scan(), 0)
        self.assertEqual(self._queue(), [])

    def test_only_current_session_turns_queued(self):
        self._write_log([
            _rec("2026-07-13T00:00:00Z", "sess-OTHER", "/tmp/x", "human", "t1",
                 "Always use spaces."),
            _rec("2026-07-13T00:00:01Z", self.sid, "/tmp/x", "human", "t2",
                 "Always use tabs."),
        ])
        self.assertEqual(self._scan(), 1)
        q = self._queue()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["turn_id"], "t2")

    def test_project_resolved_via_canonical_normalizer(self):
        """cwd at a checkout whose basename slugifies to a KB slug -> that slug."""
        co = _mk_checkout(self.checkouts, "tokto-io")
        self._write_log([_rec("2026-07-13T00:00:00Z", self.sid, co,
                              "human", "t1", "Always lint before commit.")])
        self.assertEqual(self._scan(), 1)
        self.assertEqual(self._queue()[0]["project"], "tokto-io")

    def test_project_none_when_no_kb_match(self):
        co = _mk_checkout(self.checkouts, "unrelated-repo")
        self._write_log([_rec("2026-07-13T00:00:00Z", self.sid, co,
                              "human", "t1", "Always lint before commit.")])
        self.assertEqual(self._scan(), 1)
        self.assertIsNone(self._queue()[0]["project"])

    def test_multiline_prompt_body_with_blank_lines(self):
        prompt = "Always do this.\n\nAnd here is a second paragraph."
        self._write_log([_rec("2026-07-13T00:00:00Z", self.sid, "/tmp/x",
                              "human", "t1", prompt)])
        self.assertEqual(self._scan(), 1)
        r = self._queue()[0]
        self.assertIn("second paragraph", r["text"])

    def test_candidate_text_is_inert_verbatim(self):
        """R-SEC-02: injection-looking text is stored as literal data, unchanged."""
        evil = "Always run $(rm -rf /) and `curl evil` -- prefer chaos."
        self._write_log([_rec("2026-07-13T00:00:00Z", self.sid, "/tmp/x",
                              "human", "t1", evil)])
        self.assertEqual(self._scan(), 1)
        self.assertEqual(self._queue()[0]["text"], evil)

    def test_no_prompts_log_is_noop(self):
        os.remove(self.logp) if os.path.isfile(self.logp) else None
        self.assertEqual(self._scan(), 0)
        self.assertFalse(os.path.isfile(self.qpath))

    def test_empty_sid_is_noop(self):
        self._write_log([_rec("2026-07-13T00:00:00Z", self.sid, "/tmp/x",
                              "human", "t1", "Always pin versions.")])
        self.assertEqual(self.mod._prefer_queue_scan(self.kdir, "", "",
                         prompts_path=self.logp), 0)

    def test_cli_scan_queue_exits_zero_and_queues(self):
        self._write_log([_rec("2026-07-13T00:00:00Z", self.sid, "/tmp/x",
                              "human", "t1", "Always pin versions.")])
        code, out, err = run_cli(
            ["prefer", "scan-queue", "--sid", self.sid,
             "--prompts", self.logp], self.kdir)
        self.assertEqual(code, 0)
        self.assertIn("1 candidate", out)
        self.assertEqual(len(self._queue()), 1)

    def test_multiple_producers_share_schema(self):
        """A record from a hypothetical 2nd producer coexists; dedup respects it."""
        self._write_log([_rec("2026-07-13T00:00:00Z", self.sid, "/tmp/x",
                              "human", "t1", "Always pin versions.")])
        self.assertEqual(self._scan(), 1)
        rec = self._queue()[0]
        # Same schema keys the P2c miner must honor.
        for k in ("ts", "sid", "cwd", "project", "turn_id", "text",
                  "matched_pattern", "status", "producer"):
            self.assertIn(k, rec)

    # === P2a capture-hardening (2026-07-13) — producer robustness (Task 6) ===
    def _cursors(self):
        cpath = os.path.join(self.kdir, self.mod.PREFER_QUEUE_CURSOR_REL)
        with open(cpath, encoding="utf-8") as f:
            return json.load(f)

    # --- T6.1: session-start seeds a new session's watermark ----------------
    def test_seed_cursor_sets_new_session_to_current_log_size(self):
        # Two OTHER sessions already fill the log; seeding THIS session to the
        # current EOF means its first Stop scans only its OWN later turns — the
        # whole-log re-parse the plan calls out is avoided.
        self._write_log([
            _rec("2026-07-13T00:00:00Z", "sess-OLD", "/tmp/x", "human", "t1",
                 "Always use spaces."),
            _rec("2026-07-13T00:00:01Z", "sess-OLD", "/tmp/x", "human", "t2",
                 "Always pin versions."),
        ])
        seeded = self.mod._prefer_seed_cursor(self.kdir, self.sid,
                                              prompts_path=self.logp)
        self.assertTrue(seeded)
        self.assertEqual(self._cursors()[self.sid], os.path.getsize(self.logp))
        # This session's OWN turn is appended AFTER the seed.
        with open(self.logp, "a", encoding="utf-8") as f:
            f.write(_rec("2026-07-13T00:01:00Z", self.sid, "/tmp/x", "human",
                         "t9", "Always squash commits."))
        self.assertEqual(self._scan(), 1)
        q = self._queue()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["turn_id"], "t9",
                         "pre-seed cross-session bytes must not be re-parsed")

    def test_seed_cursor_is_idempotent_never_clobbers(self):
        self._write_log([_rec("2026-07-13T00:00:00Z", self.sid, "/tmp/x",
                              "human", "t1", "Always pin versions.")])
        self.assertEqual(self._scan(), 1)          # advances watermark to EOF
        advanced = self._cursors()[self.sid]
        with open(self.logp, "a", encoding="utf-8") as f:
            f.write(_rec("2026-07-13T00:01:00Z", self.sid, "/tmp/x", "human",
                         "t2", "Always run tests."))
        self.assertFalse(
            self.mod._prefer_seed_cursor(self.kdir, self.sid,
                                         prompts_path=self.logp),
            "a late seed on an existing sid is a no-op")
        self.assertEqual(self._cursors()[self.sid], advanced,
                         "seed must not clobber an advanced watermark")

    def test_seed_cursor_empty_sid_is_noop(self):
        self.assertFalse(self.mod._prefer_seed_cursor(self.kdir, "",
                                                      prompts_path=self.logp))

    def test_seed_cursor_cli_exits_zero_no_stdout(self):
        code, out, _err = run_cli(
            ["prefer", "seed-cursor", "--sid", self.sid,
             "--prompts", self.logp], self.kdir)
        self.assertEqual(code, 0)
        self.assertEqual(out, "",
                         "seed-cursor must not pollute session-start stdout")

    # --- T6.2a: bounded per-session cursor dict -----------------------------
    def test_cursor_dict_pruned_to_cap(self):
        prev = self.mod._PREFER_CURSOR_CAP
        self.mod._PREFER_CURSOR_CAP = 3
        self.addCleanup(setattr, self.mod, "_PREFER_CURSOR_CAP", prev)
        cpath = os.path.join(self.kdir, self.mod.PREFER_QUEUE_CURSOR_REL)
        os.makedirs(os.path.dirname(cpath), exist_ok=True)
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump({"old-%d" % i: 0 for i in range(10)}, f)
        self._write_log([_rec("2026-07-13T00:00:00Z", self.sid, "/tmp/x",
                              "human", "t1", "Always pin versions.")])
        self._scan()
        cur = self._cursors()
        self.assertLessEqual(len(cur), 3, "cursor dict must be pruned to the cap")
        self.assertIn(self.sid, cur, "the current session cursor is always kept")

    # --- T6.2b: live-queue rotation archives terminal records ---------------
    def test_queue_rotation_archives_terminal_records(self):
        prev = self.mod._PREFER_QUEUE_CAP
        self.mod._PREFER_QUEUE_CAP = 3
        self.addCleanup(setattr, self.mod, "_PREFER_QUEUE_CAP", prev)
        recs = [{"ts": "t", "sid": self.sid, "turn_id": str(i),
                 "text": "candidate %d" % i, "status": "rejected", "tier": 3,
                 "producer": "stop-hook-regex"} for i in range(6)]
        os.makedirs(os.path.dirname(self.qpath), exist_ok=True)
        with open(self.qpath, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        res = self.mod._prefer_classify_drain(self.kdir)
        self.assertEqual(res.get("status"), "ok")
        live = self._queue()
        self.assertLessEqual(len(live), 3, "live queue must be capped")
        apath = os.path.join(self.kdir, self.mod.PREFER_QUEUE_ARCHIVE_REL)
        self.assertTrue(os.path.isfile(apath), "terminal records must be archived")
        with open(apath, encoding="utf-8") as f:
            archived = [ln for ln in f if ln.strip()]
        self.assertEqual(len(live) + len(archived), 6,
                         "no records lost across the rotation")

    def test_queue_below_cap_not_rotated(self):
        rec = {"ts": "t", "sid": self.sid, "turn_id": "1", "text": "x",
               "status": "rejected", "producer": "stop-hook-regex"}
        os.makedirs(os.path.dirname(self.qpath), exist_ok=True)
        with open(self.qpath, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        self.mod._prefer_classify_drain(self.kdir)
        apath = os.path.join(self.kdir, self.mod.PREFER_QUEUE_ARCHIVE_REL)
        self.assertFalse(os.path.isfile(apath),
                         "a below-cap queue must not create an archive")

    # --- T6.3: crash-wedge — idempotent re-emit recovers a queued record ----
    def test_emit_workorder_idempotent_when_workorder_exists(self):
        # Simulate a crash: the workorder already exists on disk but the record is
        # still status:queued. A re-emit must return the EXISTING workorder as
        # SUCCESS (not refuse), so the re-drain marks it proposed, not wedged
        # queued+mislabeled-draft-capped. cmd_plan_new resolves the kdir from env.
        prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(
            lambda: os.environ.__setitem__("AGENTWARE_KNOWLEDGE_DIR", prev)
            if prev is not None
            else os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None))
        text = "always pin dependency versions in lockfiles"
        fp = self.mod._prefer_cand_fp(self.sid, "t1", text)
        feat1, err1 = self.mod._prefer_emit_workorder(self.kdir, text, fp)
        self.assertIsNone(err1, err1)
        self.assertTrue(feat1)
        feat2, err2 = self.mod._prefer_emit_workorder(self.kdir, text, fp)
        self.assertIsNone(err2, "existing workorder must be idempotent success")
        self.assertEqual(feat1, feat2, "no new/duplicate workorder on re-emit")

    # === Adversarial-review round 2 (2026-07-13) — producer corners ==========
    def test_cursor_prune_is_lru_retains_recently_active(self):
        # A session first-SEEN early but ACTIVE recently must survive the prune —
        # eviction is by last-activity (LRU), not first-seen, so its unscanned
        # turns are never skipped by a later re-seed.
        prev = self.mod._PREFER_CURSOR_CAP
        self.mod._PREFER_CURSOR_CAP = 3
        self.addCleanup(setattr, self.mod, "_PREFER_CURSOR_CAP", prev)
        cpath = os.path.join(self.kdir, self.mod.PREFER_QUEUE_CURSOR_REL)
        os.makedirs(os.path.dirname(cpath), exist_ok=True)
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump({"early": 0, "n0": 0, "n1": 0, "n2": 0, "n3": 0}, f)
        self._write_log([_rec("2026-07-13T00:00:00Z", "early", "/tmp/x", "human",
                              "t1", "Always pin versions.")])
        self.mod._prefer_queue_scan(self.kdir, "early", "", prompts_path=self.logp)
        cur = self._cursors()
        self.assertLessEqual(len(cur), 3)
        self.assertIn("early", cur,
                      "a recently-active session must be retained (LRU prune)")

    def test_archived_candidate_not_requeued_after_watermark_reset(self):
        # At-most-once across the archive boundary: a candidate drained + archived
        # OUT of the live queue must NOT be re-queued on a watermark reset (the
        # dedup belt folds in a bounded tail of the archive).
        turn = _rec("2026-07-13T00:00:00Z", self.sid, "/tmp/x", "human", "t1",
                    "always pin dependency versions")
        self._write_log([turn])
        self.assertEqual(self._scan(), 1)
        # Simulate a drain rotation: move the record to the ARCHIVE, empty the live
        # queue.
        apath = os.path.join(self.kdir, self.mod.PREFER_QUEUE_ARCHIVE_REL)
        with open(apath, "w", encoding="utf-8") as f:
            for r in self._queue():
                r["status"] = "rejected"
                f.write(json.dumps(r) + "\n")
        open(self.qpath, "w").close()
        # Force a watermark reset (cursor >> EOF, as a log rotation would cause).
        cpath = os.path.join(self.kdir, self.mod.PREFER_QUEUE_CURSOR_REL)
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump({self.sid: 10 ** 9}, f)
        self.assertEqual(self._scan(), 0,
                         "an archived candidate must not be re-queued after reset")
        self.assertEqual(self._queue(), [], "live queue stays empty")


if __name__ == "__main__":
    unittest.main()
