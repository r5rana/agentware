"""TestClaudeTranscriptReconciliation, TestTruncatedInputTargetGuard, TestSnapshotBackfillHarness -- split from `test_transcript_miner` (task 58).

See `tests.unit.ops._transcript_miner_support` for the
shared header.
"""

from tests.unit.ops._transcript_miner_support import (  # noqa: F401
    _MinerBase, _rec, build_synthetic_kb, json, load_cli, mock, os,
    seed_miner_state_at_zero, setUpModule, shutil, snapshot_corpus_kb,
    tearDownModule, tempfile, unittest)


class TestClaudeTranscriptReconciliation(_MinerBase):
    """Feature 260714-miner-operationalize Task 3 / review F-B.

    The PostToolUse hook does NOT fire on a FAILED Claude tool call (spike,
    verified 2026-07-14: `ls <missing>`, `false` and `exit 7` each produced ZERO
    live.jsonl records while every success logged), so error-recovery was blind
    on the majority harness. These lock the reconstruction of the missing ERR
    record from the durable transcript's tool_result `is_error`."""

    TS_ERR = "2026-07-14T10:00:00.123Z"
    TS_FIX = "2026-07-14T10:05:00.456Z"

    def test_failed_claude_bash_is_captured_as_ERR_and_pairs_with_a_later_fix(self):
        # THE headline case: the hook logged only the later SUCCESS; the failure
        # exists solely in the transcript. Pre-fix this mined nothing at all.
        self._seed_zero()
        cmd = "pytest tests/test_api.py"
        self._write_session("A", [
            {"ts": "2026-07-14T10:05:00Z", "tool": "Bash", "status": "ok",
             "input": json.dumps({"command": cmd}), "response": "2 passed"},
        ])
        self._write_transcript("A", [
            ("Bash", {"command": cmd}, True,
             "Exit code 1\nModuleNotFoundError: No module named 'api'",
             self.TS_ERR),
            ("Bash", {"command": cmd}, False, "2 passed", self.TS_FIX),
        ])
        self.assertEqual(self._mine(), 1)
        r = self._queue()[0]
        self.assertEqual(r["signal"], "error-recovery")
        self.assertEqual(r["source_sessions"], ["A"])
        self.assertIn("Bash", r["text"])
        self.assertIn(cmd, r["text"])                     # target, from tool_use
        # _transcript_err_signature STRIPS the constant `Exit code N` preamble
        # (adversarial-review: it is identical for every genuinely-distinct Bash
        # failure, so keying on it collapses them all onto one permanent F-E
        # ledger key) and keeps the informative detail line instead. The
        # signature must be the ModuleNotFoundError, NOT the content-free
        # `Exit code 1` preamble.
        self.assertIn("ModuleNotFoundError: No module named 'api'", r["text"])
        self.assertNotIn("Exit code 1", r["text"])

    def test_reconciler_emits_ERR_records_only_for_is_error_true(self):
        self._write_transcript("A", [
            ("Bash", {"command": "ok-one"}, False, "fine", self.TS_ERR),
            ("Bash", {"command": "bad-one"}, True, "boom", self.TS_FIX),
        ])
        errs = self.mod._transcript_reconcile_errors("A", [])
        self.assertEqual(len(errs), 1, "only the is_error:true result is rebuilt")
        self.assertEqual(errs[0]["status"], "ERR")
        self.assertEqual(errs[0]["tool"], "Bash")
        self.assertEqual(self.mod._transcript_tool_target(errs[0]), "bad-one")

    def test_reconstructed_record_matches_the_hook_schema(self):
        # Byte-compatible with what log-tool.sh would have written, so every
        # downstream reader (detector, signature, target) works unchanged.
        self._write_transcript("A", [
            ("Bash", {"command": "x"}, True, "boom", self.TS_ERR)])
        rec = self.mod._transcript_reconcile_errors("A", [])[0]
        self.assertEqual(sorted(rec), ["agent", "input", "response", "status",
                                       "tool", "ts"])
        self.assertIsInstance(rec["input"], str)      # hook stores JSON strings
        self.assertIsInstance(rec["response"], str)

    def test_transcript_ts_normalized_to_hook_second_granularity(self):
        self._write_transcript("A", [
            ("Bash", {"command": "x"}, True, "boom", self.TS_ERR)])
        rec = self.mod._transcript_reconcile_errors("A", [])[0]
        self.assertEqual(rec["ts"], "2026-07-14T10:00:00Z")

    def test_records_merge_onto_one_ordered_timeline(self):
        self._seed_zero()
        self._write_session("A", [
            {"ts": "2026-07-14T10:05:00Z", "tool": "Bash", "status": "ok",
             "input": json.dumps({"command": "c"}), "response": "fixed"},
        ])
        self._write_transcript("A", [
            ("Bash", {"command": "c"}, True, "boom", self.TS_ERR)])
        sdir = os.path.join(self.kdir, "logs", "sessions", "A")
        recs = self.mod._transcript_read_session(sdir, "A")
        self.assertEqual([r["status"] for r in recs], ["ERR", "ok"],
                         "the 10:00 reconstructed ERR precedes the 10:05 fix")

    def test_error_after_the_fix_does_not_pair_backwards(self):
        # Ordering is load-bearing: a failure AFTER the success is not recovery.
        self._seed_zero()
        self._write_session("A", [
            {"ts": "2026-07-14T09:00:00Z", "tool": "Bash", "status": "ok",
             "input": json.dumps({"command": "c"}), "response": "fine"},
        ])
        self._write_transcript("A", [
            ("Bash", {"command": "c"}, True, "boom", "2026-07-14T11:00:00.0Z")])
        self.assertEqual(self._mine(), 0)

    def test_session_without_a_transcript_is_untouched(self):
        # The codex path (no Claude transcript) keeps its own exit-code ERRs.
        self._seed_zero()
        self._write_session("A", [
            self._call("command_execution", "ERR", None, "boom",
                       input_override="deploy.sh"),
            self._call("command_execution", "ok", None, "done",
                       input_override="deploy.sh"),
        ])
        self.assertEqual(self.mod._transcript_reconcile_errors("A", []), [])
        self.assertEqual(self._mine(), 1, "codex ERR->ok still pairs")

    def test_absent_or_unreadable_transcript_degrades_to_empty(self):
        self.assertEqual(self.mod._transcript_reconcile_errors("nope", []), [])
        self.assertEqual(self.mod._transcript_claude_path("nope"), "")

    def test_malformed_transcript_lines_are_skipped_not_fatal(self):
        d = os.path.join(self.home, ".claude", "projects", "p")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "A.jsonl"), "w", encoding="utf-8") as f:
            f.write("not json\n")
            f.write(json.dumps({"message": {"content": "not-a-list"}}) + "\n")
            f.write(json.dumps({"message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "x"}}]}, "timestamp": self.TS_ERR}) + "\n")
            f.write(json.dumps({"message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                 "content": "boom"}]}, "timestamp": self.TS_ERR}) + "\n")
        errs = self.mod._transcript_reconcile_errors("A", [])
        self.assertEqual(len(errs), 1)

    def test_unpaired_tool_result_is_ignored(self):
        d = os.path.join(self.home, ".claude", "projects", "p")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "A.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"message": {"content": [
                {"type": "tool_result", "tool_use_id": "orphan",
                 "is_error": True, "content": "boom"}]},
                "timestamp": self.TS_ERR}) + "\n")
        self.assertEqual(self.mod._transcript_reconcile_errors("A", []), [])

    def test_writer_recorded_err_is_not_duplicated(self):
        # A tool whose response DID carry success=false is already in live.jsonl;
        # the reconstruction must not double it.
        live = [{"ts": "2026-07-14T10:00:00Z", "tool": "Bash", "status": "ERR",
                 "input": json.dumps({"command": "x"}), "response": "boom"}]
        self._write_transcript("A", [
            ("Bash", {"command": "x"}, True, "boom", self.TS_ERR)])
        self.assertEqual(self.mod._transcript_reconcile_errors("A", live), [])

    def test_a_second_failure_at_a_different_time_is_still_rebuilt(self):
        # The dedup keys on (tool, target, ts) — never collapsing real repeats.
        live = [{"ts": "2026-07-14T10:00:00Z", "tool": "Bash", "status": "ERR",
                 "input": json.dumps({"command": "x"}), "response": "boom"}]
        self._write_transcript("A", [
            ("Bash", {"command": "x"}, True, "boom", self.TS_ERR),
            ("Bash", {"command": "x"}, True, "boom again", self.TS_FIX)])
        errs = self.mod._transcript_reconcile_errors("A", live)
        self.assertEqual([e["ts"] for e in errs], ["2026-07-14T10:05:00Z"])

    def test_sid_path_traversal_is_refused(self):
        for evil in ("../../etc/passwd", "a/b", "..", ".", "", None, 7):
            self.assertEqual(self.mod._transcript_claude_path(evil), "")

    def test_reconciler_is_read_only_and_deterministic(self):
        self._write_transcript("A", [
            ("Bash", {"command": "x"}, True, "boom", self.TS_ERR)])
        tpath = self.mod._transcript_claude_path("A")
        before = open(tpath, encoding="utf-8").read()
        a = self.mod._transcript_reconcile_errors("A", [])
        b = self.mod._transcript_reconcile_errors("A", [])
        self.assertEqual(a, b, "byte-stable across calls (INV-1)")
        self.assertEqual(open(tpath, encoding="utf-8").read(), before)

    def test_error_text_from_a_transcript_stays_inert(self):
        # R-SEC-02: transcript content is untrusted data, stored verbatim.
        self._seed_zero()
        evil = "$(rm -rf /) `curl evil` -- boom"
        self._write_session("A", [
            {"ts": "2026-07-14T10:05:00Z", "tool": "Bash", "status": "ok",
             "input": json.dumps({"command": "deploy.sh"}), "response": "done"}])
        self._write_transcript("A", [
            ("Bash", {"command": "deploy.sh"}, True, evil, self.TS_ERR),
            ("Bash", {"command": "deploy.sh"}, False, "done", self.TS_FIX)])
        self.assertEqual(self._mine(), 1)
        self.assertEqual(self._queue()[0]["status"], "queued")  # inert, not run

class TestTruncatedInputTargetGuard(_MinerBase):
    """Feature 260714-miner-operationalize Task 3 / review F-D.

    log-tool.sh bounds `input` at MAXLEN=1500 + a marker, so a long call's stored
    input is INVALID JSON. Pre-fix, json.loads raised and the WHOLE blob became
    the target via _transcript_norm_key — wrong, and collision-prone across two
    commands sharing a 1500-char prefix."""

    def _truncated(self, cmd):
        """Exactly what log-tool.sh writes for an over-long tool_input."""
        blob = json.dumps({"command": cmd, "description": "x" * 200})
        return self.mod._transcript_hook_trunc(blob)

    def test_truncated_input_yields_the_command_never_the_blob(self):
        cmd = "deploy --region us-east-1 " + ("--flag " * 400)
        raw = self._truncated(cmd)
        self.assertGreater(len(raw), self.mod.MINER_TRANSCRIPT_MAXLEN)
        target = self.mod._transcript_tool_target({"input": raw})
        self.assertTrue(target.startswith("deploy --region us-east-1"))
        self.assertNotIn("[truncated]", target, "the marker never leaks in")
        self.assertNotIn('"description"', target, "the blob is not the target")
        self.assertLess(len(target), len(raw))

    def test_two_commands_sharing_a_long_prefix_do_not_collide(self):
        # The pre-fix collision: an unrelated success 'recovering' an error.
        base = "run " + ("x" * 1600)
        a = self.mod._transcript_tool_target({"input": self._truncated(base + " --alpha")})
        b = self.mod._transcript_tool_target({"input": self._truncated(base + " --beta")})
        # Both truncate inside the shared prefix, so they legitimately agree;
        # what must hold is that neither is the raw blob.
        self.assertNotIn("[truncated]", a)
        self.assertEqual(a, b)
        short_a = self.mod._transcript_tool_target(
            {"input": json.dumps({"command": "run --alpha"})})
        short_b = self.mod._transcript_tool_target(
            {"input": json.dumps({"command": "run --beta"})})
        self.assertNotEqual(short_a, short_b, "distinct short commands stay distinct")

    def test_truncated_err_and_fix_derive_the_same_target_and_pair(self):
        # The reason parity with the writer's truncation matters at all.
        self._seed_zero()
        cmd = "pytest " + ("tests/test_%d.py " % 1) * 400
        raw = self._truncated(cmd)
        self._write_session("A", [
            {"ts": "2026-07-14T10:00:00Z", "tool": "Bash", "status": "ERR",
             "input": raw, "response": "boom"},
            {"ts": "2026-07-14T10:05:00Z", "tool": "Bash", "status": "ok",
             "input": raw, "response": "passed"},
        ])
        self.assertEqual(self._mine(), 1)

    def test_truncated_precise_target_is_preferred_over_command(self):
        blob = json.dumps({"file_path": "/a/b.py", "command": "z" * 2000})
        raw = self.mod._transcript_hook_trunc(blob)
        self.assertEqual(self.mod._transcript_tool_target({"input": raw}),
                         "/a/b.py")

    def test_valid_json_input_is_unaffected(self):
        self.assertEqual(self.mod._transcript_tool_target(
            {"input": json.dumps({"file_path": "/a/b.py"})}), "/a/b.py")
        self.assertEqual(self.mod._transcript_tool_target(
            {"input": json.dumps({"command": "  ls -la  "})}), "ls -la")

    def test_plain_non_json_string_input_keeps_the_normalized_path(self):
        # codex-stream.py writes the BARE command as `input` — not JSON.
        self.assertEqual(self.mod._transcript_tool_target({"input": "  ls -la "}),
                         "ls -la")

    def test_brace_shell_group_is_not_mistaken_for_truncated_json(self):
        # A codex command may literally start with '{' — it must not be salvaged
        # into '' and lose its target.
        # norm_key strips the surrounding brace/semicolon punctuation.
        self.assertEqual(
            self.mod._transcript_tool_target({"input": "{ echo hi; }"}),
            "echo hi")

    def test_unrecoverable_blob_yields_no_target_not_a_bogus_one(self):
        raw = '{"comm'          # truncation cut before any field value
        self.assertEqual(self.mod._transcript_tool_target({"input": raw}), "")

    def test_field_extractor_handles_escapes_and_severed_escapes(self):
        ex = self.mod._transcript_extract_json_field
        self.assertEqual(ex(r'{"command":"say \"hi\" now"}', "command"),
                         'say "hi" now')
        self.assertEqual(ex(r'{"command":"a\\b"}', "command"), r"a\b")
        self.assertEqual(ex('{"command":"tail-cut', "command"), "tail-cut")
        self.assertEqual(ex('{"command":"bad\\', "command"), "bad")
        self.assertEqual(ex('{"command":"x\\u00', "command"), "x")
        self.assertEqual(ex('{"other":"x"}', "command"), "")

    def test_target_derivation_is_byte_stable(self):
        raw = self._truncated("deploy " + "y" * 2000)
        self.assertEqual(self.mod._transcript_tool_target({"input": raw}),
                         self.mod._transcript_tool_target({"input": raw}))

class TestSnapshotBackfillHarness(unittest.TestCase):
    """Feature 260714-miner-operationalize, Task 5 — the SNAPSHOT-BACKFILL harness.

    Locks the harness the operator's real-corpus measurement runs on (D4: measure
    a COPY, never mutate the live KB). The source corpus here is synthetic and
    live-SHAPED — a preference repeated across 3 distinct sessions, ack noise, and
    an error->fix pair — so the harness contract is proven hermetically while the
    same `snapshot_corpus_kb` + `seed_miner_state_at_zero` code path carries the
    real measurement (one definition, no drift).

    THE CENTRAL LOCK is test_selfseeded_snapshot_mines_nothing_the_p1_trap: it
    proves the pre-seed is LOAD-BEARING. Without it a snapshot self-seeds
    `offsets.prompts` at EOF, `--backfill` does not rewind it, and the harness
    mines ZERO while every other assertion still passes — a vacuous green. That
    test fails loudly if anyone ever deletes the pre-seed as 'redundant'.
    """

    def setUp(self):
        self.mod = load_cli()
        self.src = tempfile.mkdtemp(prefix="aw_src_")
        self.addCleanup(shutil.rmtree, self.src, True)
        self.dst = tempfile.mkdtemp(prefix="aw_snap_")
        self.addCleanup(shutil.rmtree, self.dst, True)
        self.home = tempfile.mkdtemp(prefix="aw_home_")   # empty => no leak
        self.addCleanup(shutil.rmtree, self.home, True)
        p = mock.patch.dict(os.environ, {"HOME": self.home})
        p.start()
        self.addCleanup(p.stop)
        self._build_source_corpus()

    def _build_source_corpus(self):
        """A live-SHAPED source KB: one real preference across 3 distinct
        sessions, high-frequency acks, one-off chatter, and an ERR->ok pair."""
        build_synthetic_kb(self.src)
        logs = os.path.join(self.src, "logs")
        os.makedirs(os.path.join(logs, "sessions"), exist_ok=True)
        pref = "always pin dependency versions, never use latest"
        recs = []
        for i, sid in enumerate(("s-aaa", "s-bbb", "s-ccc")):
            recs.append(_rec("2026-07-1%d" % i, sid, "/w/proj", "human",
                             "t%d" % i, pref))
            recs.append(_rec("2026-07-1%d" % i, sid, "/w/proj", "human",
                             "t%da" % i, "continue"))        # ack noise
            recs.append(_rec("2026-07-1%d" % i, sid, "/w/proj", "machine",
                             "t%db" % i, pref))              # machine => ignored
        recs.append(_rec("2026-07-13", "s-ddd", "/w/proj", "human", "t9",
                         "what does this one-off question do"))
        with open(os.path.join(logs, "prompts.log"), "w", encoding="utf-8") as f:
            f.write("".join(recs))
        with open(os.path.join(logs, "metrics.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"event": "terminal", "feature": "x"}) + "\n")
        for sid in ("s-aaa", "s-bbb", "s-ccc", "s-ddd"):
            sdir = os.path.join(logs, "sessions", sid)
            os.makedirs(sdir, exist_ok=True)
            calls = []
            if sid == "s-aaa":                      # an ERR -> ok pair to drain
                calls = [{"ts": "2026-07-10T00:00:01Z", "tool": "Bash",
                          "status": "ERR", "input": '{"command":"pytest -q"}',
                          "response": "boom"},
                         {"ts": "2026-07-10T00:00:09Z", "tool": "Bash",
                          "status": "ok", "input": '{"command":"pytest -q"}',
                          "response": "passed"}]
            with open(os.path.join(sdir, "live.jsonl"), "w",
                      encoding="utf-8") as f:
                for c in calls:
                    f.write(json.dumps(c) + "\n")
            with open(os.path.join(sdir, "main.jsonl"), "w",
                      encoding="utf-8") as f:      # settled
                f.write("x" * 4096)                # bulk the snapshot must NOT copy

    # --- helpers ----------------------------------------------------------
    def _drain(self, kdir, cap=2):
        """Run `prefer mine --backfill` until backfill_pending drains (bounded,
        exactly as the real measurement does). Returns (total, passes)."""
        total, passes = 0, 0
        while True:
            total += self.mod._transcript_mine(kdir, backfill=True,
                                               session_cap=cap)
            passes += 1
            with open(os.path.join(kdir, self.mod.MINER_STATE_REL),
                      encoding="utf-8") as f:
                st = json.load(f)
            if not st.get("backfill_pending") or passes > 50:
                return total, passes

    def _queue(self, kdir):
        q = os.path.join(kdir, self.mod.PREFER_QUEUE_REL)
        if not os.path.isfile(q):
            return []
        with open(q, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def _state(self, kdir):
        with open(os.path.join(kdir, self.mod.MINER_STATE_REL),
                  encoding="utf-8") as f:
            return json.load(f)

    def _fps(self, kdir):
        """The dedup fingerprints of the queued records. `fp` is DERIVED, never
        a stored field: the queue record carries the producer-shared schema, and
        _transcript_mine recomputes the fp at mine time to dedup against the
        live queue + archive tail. Deriving it the same way here keeps the test
        honest about the on-disk schema."""
        return sorted(self.mod._prefer_cand_fp(r.get("sid"), r.get("turn_id"),
                                               r.get("text"))
                      for r in self._queue(kdir))

    # --- the P1 trap ------------------------------------------------------
    def test_selfseeded_snapshot_mines_no_recurrence_the_p1_trap(self):
        # THE REGRESSION LOCK. Same corpus, NO offset-0 pre-seed: the miner
        # self-seeds offsets.prompts at EOF and --backfill does not rewind it.
        #
        # The trap is RECURRENCE-SPECIFIC, and that is exactly what makes it
        # dangerous. Deleting the state also self-seeds backfill_pending with
        # every on-disk sid, so the SESSION-sourced error-recovery detector
        # still drains and still emits — a harness asserting only "total > 0"
        # goes green on a corpus whose prompts.log was never read at all. The
        # recurrence count is the only assertion that catches it.
        snapshot_corpus_kb(self.src, self.dst)
        os.remove(os.path.join(self.dst, self.mod.MINER_STATE_REL))  # self-seed
        total, _ = self._drain(self.dst)
        sigs = [r.get("signal") for r in self._queue(self.dst)]
        self.assertEqual(sigs.count("recurrence"), 0,
                         "P1 has been fixed upstream (--backfill now rewinds the "
                         "prompts offset?) — re-verify the harness contract")
        self.assertEqual(total, sigs.count("error-recovery"),
                         "the only candidates a self-seeded snapshot can mine "
                         "are session-sourced")
        st = self._state(self.dst)
        size = os.path.getsize(os.path.join(self.dst, "logs", "prompts.log"))
        self.assertEqual(st["offsets"]["prompts"], size)   # parked at the tail

    def test_preseeded_snapshot_mines_a_nonzero_set(self):
        snapshot_corpus_kb(self.src, self.dst)
        total, _ = self._drain(self.dst)
        self.assertGreater(total, 0, "0 candidates == the P1 trap bit again; a "
                                     "snapshot harness must never pass vacuously")
        sigs = [r.get("signal") for r in self._queue(self.dst)]
        self.assertIn("recurrence", sigs)
        self.assertIn("error-recovery", sigs)   # pending pre-seed => sessions drain

    def test_the_repeated_preference_is_the_only_recurrence_candidate(self):
        snapshot_corpus_kb(self.src, self.dst)
        self._drain(self.dst)
        rec = [r for r in self._queue(self.dst) if r.get("signal") == "recurrence"]
        self.assertEqual(len(rec), 1)
        self.assertIn("pin dependency versions", rec[0]["text"])
        self.assertEqual(rec[0]["recurrence_count"], 3)      # distinct sessions
        self.assertEqual(rec[0]["source_sessions"], ["s-aaa", "s-bbb", "s-ccc"])

    # --- integrity --------------------------------------------------------
    def test_a_second_full_drain_mines_nothing_new_and_fps_are_stable(self):
        snapshot_corpus_kb(self.src, self.dst)
        self._drain(self.dst)
        fps = self._fps(self.dst)
        self.assertTrue(fps)
        seed_miner_state_at_zero(self.dst)     # re-arm: re-read the WHOLE corpus
        again, _ = self._drain(self.dst)
        self.assertEqual(again, 0, "re-mining the same corpus double-emitted")
        self.assertEqual(self._fps(self.dst), fps)

    def test_queue_records_carry_the_shared_producer_schema(self):
        snapshot_corpus_kb(self.src, self.dst)
        self._drain(self.dst)
        q = self._queue(self.dst)
        self.assertTrue(q)
        for r in q:
            # the schema the Stop-hook producer writes, field-for-field ...
            for k in ("ts", "sid", "cwd", "project", "turn_id", "text",
                      "matched_pattern", "status", "producer"):
                self.assertIn(k, r, "record missing shared-schema field %r" % k)
            self.assertIn("signal", r)             # ... + the miner's additive
            self.assertNotIn("fp", r, "fp is a DERIVED dedup key, not a stored "
                                      "field — persisting it would fork the "
                                      "producer-shared schema")
            self.assertEqual(r["status"], "queued")        # QUARANTINED
            self.assertEqual(r["producer"], "transcript-miner")

    def test_state_stays_bounded_and_offsets_are_monotonic(self):
        snapshot_corpus_kb(self.src, self.dst)
        before = self._state(self.dst)["offsets"]
        self._drain(self.dst)
        st = self._state(self.dst)
        self.assertLessEqual(len(st["tally"]), self.mod.MINER_TALLY_CAP)
        self.assertLessEqual(len(st["emitted_keys"]), self.mod.MINER_EMITTED_CAP)
        for k in ("prompts", "metrics"):
            self.assertGreaterEqual(st["offsets"][k], before[k])
            self.assertLessEqual(
                st["offsets"][k],
                os.path.getsize(os.path.join(self.dst, "logs",
                                             "prompts.log" if k == "prompts"
                                             else "metrics.jsonl")))
        self.assertEqual(st["backfill_pending"], [])       # fully drained

    # --- snapshot fidelity + write isolation ------------------------------
    def test_snapshot_never_writes_to_the_source_corpus(self):
        def snap(root):
            out = {}
            for dirpath, _dirs, files in os.walk(root):
                for fn in files:
                    p = os.path.join(dirpath, fn)
                    out[os.path.relpath(p, root)] = (os.path.getsize(p),
                                                     os.stat(p).st_mtime_ns)
            return out
        before = snap(self.src)
        snapshot_corpus_kb(self.src, self.dst)
        self._drain(self.dst)
        self.assertEqual(snap(self.src), before, "the measurement mutated live")

    def test_snapshot_copies_the_read_surface_and_stubs_main_jsonl(self):
        def rb(*parts):
            with open(os.path.join(*parts), "rb") as f:
                return f.read()
        info = snapshot_corpus_kb(self.src, self.dst)
        self.assertEqual(info["sessions"], 4)
        self.assertEqual(info["settled_sessions"], 4)
        for name in ("prompts.log", "metrics.jsonl"):
            self.assertEqual(rb(self.dst, "logs", name),
                             rb(self.src, "logs", name))
        d = os.path.join(self.dst, "logs", "sessions", "s-aaa")
        self.assertEqual(rb(d, "live.jsonl"),
                         rb(self.src, "logs", "sessions", "s-aaa", "live.jsonl"))
        # main.jsonl: settled MARKER only — presence is the whole read surface.
        self.assertEqual(os.path.getsize(os.path.join(d, "main.jsonl")), 0)
        self.assertTrue(self.mod._transcript_session_settled(d))

    def test_preseed_state_rewinds_offsets_and_arms_the_backfill(self):
        st = seed_miner_state_at_zero(self.dst, ["s-aaa", "s-bbb"])
        self.assertEqual(st["offsets"], {"prompts": 0, "metrics": 0})
        # pending seeded (NOT empty): --backfill selects `pending & on_disk`, so
        # an empty pending set silently scans ZERO sessions.
        self.assertEqual(st["backfill_pending"], ["s-aaa", "s-bbb"])
        self.assertEqual(st["processed_sessions"], ["s-aaa", "s-bbb"])
        self.assertEqual(st["tally"], {})
        self.assertEqual(st["emitted_keys"], [])
        loaded, fresh = self.mod._transcript_load_state(self.dst)
        self.assertFalse(fresh, "the pre-seed must be read as an EXISTING state")
        self.assertEqual(loaded["offsets"]["prompts"], 0)
