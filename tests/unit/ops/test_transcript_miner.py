"""Transcript miner — bounded reader + state, recurrence, error-recovery.

Runner: python3 -m unittest tests.unit.ops.test_transcript_miner -v

Feature 260713-p2c-transcript-miner (Tasks 1-3). The miner is a SECOND, FULLY
OFFLINE prefer-queue producer over the operator's OWN corpus. Proven here:
  * Task 1 — bounded incremental read + the miner's OWN watermark/state: two
    consecutive incremental mines scan each session/turn EXACTLY once (no
    re-walk), a newly-appended session is picked up next mine, `--backfill`
    respects the per-run session cap (a 3rd session past a cap of 2 defers), an
    un-settled session is re-mined without double-emit, and the persistent
    recurrence tally survives across runs (cross-window accumulation, F1);
  * Task 2 — recurrence: a body across >=N DISTINCT sessions emits ONE candidate
    (recurrence_count == distinct sessions), split-across-windows still emits,
    twice-in-one-session counts once (F4), whitespace/case near-dups collapse,
    once-asked yields none, an emitted key does not re-emit as it climbs;
  * Task 3 — error-recovery: an ERR->ok pair on the same tool+target emits ONE
    factual, byte-stable candidate stored as inert data; a bare ERR yields none.

Feature 260714-miner-operationalize (Task 1) adds TestEmitOnceLedger — review
F-A: emit-once must survive LRU tally eviction via the bounded persistent
`emitted_keys` ledger (round-trips across reloads, stays <= MINER_EMITTED_CAP,
and a legacy state file without the key loads clean).

Hermetic: a temp KB (synthetic index + seeded logs), never the live KB (R-LOC-03).
"""

from tests.unit.ops._transcript_miner_support import (  # noqa: F401
    _MinerBase, _rec, mock, os, setUpModule, tearDownModule)


class TestSeedAndWatermark(_MinerBase):
    def test_first_run_seeds_tail_and_mines_nothing(self):
        # A populated corpus present at first run is SEEDED (offsets=tail, all
        # sessions processed) so incremental mines only NEW material thereafter.
        self._write_prompts([_rec("t", "A", "/x", "human", "1",
                                  "Always pin versions.")])
        self._write_session("A", [self._call("Read", "ok", "/f")])
        self.assertEqual(self._mine(), 0, "first run seeds; mines nothing")
        st = self._state()
        self.assertEqual(st["offsets"]["prompts"], os.path.getsize(self.logp))
        self.assertIn("A", st["processed_sessions"])
        self.assertIn("A", st["backfill_pending"])
        self.assertEqual(self._queue(), [])

    def test_two_incremental_mines_scan_each_once(self):
        self._seed_zero()
        self._write_prompts([
            _rec("t", "A", "/x", "human", "1", "Always pin versions."),
            _rec("t", "B", "/x", "human", "1", "Always pin versions."),
        ])
        self._write_session("A", [self._call("Read", "ERR", "/f", "boom"),
                                  self._call("Read", "ok", "/f")])
        n1 = self._mine()
        q1 = len(self._queue())
        self.assertGreaterEqual(n1, 1)
        # Second mine over the SAME corpus: watermark advanced, session settled
        # and processed -> nothing re-walked, nothing re-emitted.
        self.assertEqual(self._mine(), 0, "no re-walk / no re-emit")
        self.assertEqual(len(self._queue()), q1)
        st = self._state()
        self.assertEqual(st["offsets"]["prompts"], os.path.getsize(self.logp))
        self.assertIn("A", st["processed_sessions"])

    def test_newly_appended_session_picked_up_next_mine(self):
        self._seed_zero()
        self._write_session("A", [self._call("Edit", "ERR", "/a", "x"),
                                  self._call("Edit", "ok", "/a")])
        self._mine()
        before = len(self._queue())
        # A genuinely NEW session appears after the first mine.
        self._write_session("B", [self._call("Edit", "ERR", "/b", "y"),
                                  self._call("Edit", "ok", "/b")])
        self.assertEqual(self._mine(), 1, "new session yields its pair")
        self.assertEqual(len(self._queue()), before + 1)

    def test_backfill_respects_session_cap_defers_third(self):
        # 3 settled sessions seeded as backfill-pending; a cap of 2 scans two now,
        # defers the third to the next backfill run.
        for s in ("s1", "s2", "s3"):
            self._write_session(s, [self._call("Bash", "ok", None,
                                               input_override='{"command":"ls"}')])
        self._seed_zero(backfill_pending=["s1", "s2", "s3"])
        self._mine(backfill=True, cap=2)
        st = self._state()
        self.assertEqual(sorted(st["processed_sessions"]), ["s1", "s2"])
        self.assertEqual(st["backfill_pending"], ["s3"], "3rd session deferred")
        self._mine(backfill=True, cap=2)
        st = self._state()
        self.assertEqual(sorted(st["processed_sessions"]), ["s1", "s2", "s3"])
        self.assertEqual(st["backfill_pending"], [])

    def test_unsettled_session_remined_without_double_emit(self):
        self._seed_zero()
        self._write_session("A", [self._call("Edit", "ERR", "/a", "x"),
                                  self._call("Edit", "ok", "/a")], settled=False)
        self.assertEqual(self._mine(), 1)
        self.assertEqual(len(self._queue()), 1)
        self.assertNotIn("A", self._state()["processed_sessions"],
                         "an un-settled session is not finalized")
        # Re-mine: the session is re-scanned (still not processed) but the pair
        # fp-dedups against the queue -> no second candidate.
        self.assertEqual(self._mine(), 0, "fp-dedup guards re-mine double-emit")
        self.assertEqual(len(self._queue()), 1)

    def test_tally_survives_across_runs_cross_window(self):
        self._seed_zero()
        self._append_prompts([_rec("t", "A", "/x", "human", "1",
                                   "Always squash commits before merge.")])
        self.assertEqual(self._mine(), 0, "one session -> below N, no emit")
        tally = self._state()["tally"]
        self.assertEqual(len(tally), 1, "tally persisted across the run")
        # A DIFFERENT session asks the same thing in a LATER window.
        self._append_prompts([_rec("t", "B", "/x", "human", "1",
                                   "Always squash commits before merge.")])
        self.assertEqual(self._mine(), 1, "distinct-session count reaches 2 -> emit")
        r = self._queue()[0]
        self.assertEqual(r["signal"], "recurrence")
        self.assertEqual(r["recurrence_count"], 2)

class TestRecurrence(_MinerBase):
    def test_three_distinct_sessions_one_candidate_count_three(self):
        self._seed_zero()
        body = "From now on, prefer tabs over spaces."
        self._write_prompts([_rec("t", s, "/x", "human", "1", body)
                             for s in ("A", "B", "C")])
        self.assertEqual(self._mine(), 1)
        r = self._queue()[0]
        self.assertEqual(r["signal"], "recurrence")
        self.assertEqual(r["recurrence_count"], 3)
        self.assertEqual(r["producer"], self.mod.PREFER_QUEUE_MINER_PRODUCER)
        self.assertEqual(r["status"], "queued")
        self.assertEqual(sorted(r["source_sessions"]), ["A", "B", "C"])
        # Stable fp: re-running the SAME window yields the same identity.
        fp = self.mod._prefer_cand_fp(r["sid"], r["turn_id"], r["text"])
        self.assertEqual(fp, self.mod._prefer_cand_fp(r["sid"], r["turn_id"],
                                                      r["text"]))

    def test_twice_in_one_session_counts_once(self):
        self._seed_zero()
        body = "Always run the linter."
        self._write_prompts([_rec("t", "A", "/x", "human", "1", body),
                             _rec("t", "A", "/x", "human", "2", body)])
        self.assertEqual(self._mine(), 0, "distinct-SESSION, not raw occurrence (F4)")
        self.assertEqual(self._queue(), [])

    def test_once_asked_yields_none(self):
        self._seed_zero()
        self._write_prompts([_rec("t", "A", "/x", "human", "1",
                                  "Always foo the bar.")])
        self.assertEqual(self._mine(), 0)

    def test_whitespace_case_nearups_collapse(self):
        self._seed_zero()
        self._write_prompts([
            _rec("t", "A", "/x", "human", "1", "Always Pin   Versions."),
            _rec("t", "B", "/x", "human", "1", "always pin versions"),
        ])
        self.assertEqual(self._mine(), 1, "near-dups collapse to one key")
        self.assertEqual(self._queue()[0]["recurrence_count"], 2)

    def test_emitted_key_does_not_reemit_as_count_climbs(self):
        self._seed_zero()
        body = "Never force-push to main."
        self._write_prompts([_rec("t", "A", "/x", "human", "1", body),
                             _rec("t", "B", "/x", "human", "1", body)])
        self.assertEqual(self._mine(), 1)
        # A THIRD distinct session asks it later; the key already emitted.
        self._append_prompts([_rec("t", "C", "/x", "human", "1", body)])
        self.assertEqual(self._mine(), 0, "emitted key never re-emits")
        self.assertEqual(len(self._queue()), 1)

class TestRecurrencePrecisionGate(_MinerBase):
    """Review F-C: _transcript_mine_recurrence emitted for ANY body reaching N
    distinct sessions, so high-frequency non-preference turns flooded the queue.
    Measured on the live corpus (2026-07-14) the ONLY key reaching N>=2 was the
    ack 'continue pls' — i.e. today recurrence emits noise and nothing else.
    The gate is EMISSION-side: the tally still counts, so it stays re-tunable."""

    def _mine_body_across(self, body, sids=("A", "B")):
        self._write_prompts([_rec("t", s, "/x", "human", "1", body)
                             for s in sids])
        return self._mine()

    # --- the measured live-corpus regression lock -------------------------
    def test_live_corpus_ack_continue_pls_is_gated(self):
        # THE regression lock: the one key that reached N>=2 on the real corpus.
        self._seed_zero()
        self.assertEqual(self._mine_body_across("continue pls"), 0,
                         "the measured live-corpus ack must never emit")
        self.assertEqual(self._queue(), [])

    def test_continue_pls_is_gated_by_BOTH_subtests_independently(self):
        # The plan requires each sub-test to gate it ALONE, so neither is
        # load-bearing by itself. Probe the pure predicate directly.
        key = self.mod._transcript_norm_key("continue pls")
        base = self.mod._transcript_recurrence_gate()
        # (a) length alone: disable the ack test (empty stopwords) -> still gated
        len_only = dict(base, stopwords=frozenset())
        self.assertFalse(self.mod._transcript_recurrence_relevant(key, len_only),
                         "length sub-test alone gates it")
        # (b) ack alone: disable the length test (min_len=0) -> still gated
        ack_only = dict(base, min_len=0)
        self.assertFalse(self.mod._transcript_recurrence_relevant(key, ack_only),
                         "ack sub-test alone gates it")

    def test_high_frequency_acks_across_n_sessions_emit_nothing(self):
        for ack in ("continue", "yes", "ok", "go ahead", "run it", "proceed",
                    "thanks", "continue please", "yes ok thanks"):
            with self.subTest(ack=ack):
                self.setUp()          # a clean KB per ack
                self._seed_zero()
                self.assertEqual(self._mine_body_across(ack), 0,
                                 "%r must not emit" % ack)

    def test_cue_bearing_preference_across_n_sessions_emits_exactly_one(self):
        self._seed_zero()
        self.assertEqual(
            self._mine_body_across("Always pin dependency versions."), 1)
        q = self._queue()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["signal"], "recurrence")

    def test_long_non_cue_all_ack_body_is_gated_by_the_blocklist(self):
        # >= min_len (so (a) passes) and carries NO cue, but every token is an
        # ack -> the blocklist is what gates it.
        body = "ok sure thanks that works great"
        key = self.mod._transcript_norm_key(body)
        gate = self.mod._transcript_recurrence_gate()
        self.assertGreaterEqual(len(key), gate["min_len"], "(a) passes")
        self.assertIsNone(self.mod._PREFER_QUEUE_RE.search(key), "no cue")
        self._seed_zero()
        self.assertEqual(self._mine_body_across(body), 0, "gated by blocklist")

    def test_long_non_cue_substantive_body_still_emits(self):
        # The ack test is TOKEN-level, not substring: a body with real content
        # must survive even without an explicit cue.
        self._seed_zero()
        self.assertEqual(
            self._mine_body_across("run the migrations against staging first"),
            1, "substantive non-cue body is not an ack")

    def test_ack_substring_inside_a_real_preference_does_not_gate_it(self):
        # "continue" appears, but the body is not ALL stopwords.
        key = self.mod._transcript_norm_key("do not continue on a failed build")
        gate = self.mod._transcript_recurrence_gate()
        self.assertFalse(self.mod._transcript_is_ack(key, gate["stopwords"]))
        self.assertTrue(self.mod._transcript_recurrence_relevant(key, gate))

    # --- the gate is re-tunable: the tally is unaffected -------------------
    def test_gated_key_is_still_counted_and_never_flagged_or_ledgered(self):
        # The core re-tunability contract (Task 6 tunes thresholds later): a
        # gated key must stay countable, unflagged, and OUT of the ledger.
        self._seed_zero()
        self.assertEqual(self._mine_body_across("continue pls", ("A", "B")), 0)
        st = self._state()
        key = self.mod._transcript_norm_key("continue pls")
        self.assertIn(key, st["tally"], "the tally COUNTS gated keys")
        self.assertEqual(len(st["tally"][key]["sessions"]), 2,
                         "distinct-session count is unaffected by the gate")
        self.assertFalse(st["tally"][key]["emitted"],
                         "a gated key is NOT flagged emitted")
        self.assertNotIn(key, st["emitted_keys"],
                         "a gated key never enters the emit-once ledger")

    def test_retuning_min_len_down_emits_a_previously_gated_key(self):
        # Proves re-tunability end-to-end: same corpus + a looser gate -> the
        # key that was gated now emits, because it was never flagged/ledgered.
        self._seed_zero()
        body = "use tabs"          # 8 chars, no cue, not an ack -> gated by (a)
        self.assertEqual(self._mine_body_across(body, ("A", "B")), 0)
        self._append_prompts([_rec("t", "C", "/x", "human", "1", body)])
        with mock.patch.dict(os.environ,
                             {self.mod.MINER_RECURRENCE_MIN_LEN_KEY: "4"}):
            self.assertEqual(self._mine(), 1, "a looser gate reconsiders it")
        self.assertEqual(self._queue()[0]["recurrence_count"], 3)

    # --- config plumbing ---------------------------------------------------
    def test_min_len_is_honored_from_env(self):
        self._seed_zero()
        with mock.patch.dict(os.environ,
                             {self.mod.MINER_RECURRENCE_MIN_LEN_KEY: "999"}):
            self.assertEqual(
                self._mine_body_across("Always pin dependency versions."), 0,
                "an absurd min_len gates even a cue-bearing preference")

    def test_cue_required_drops_the_not_an_ack_escape(self):
        self._seed_zero()
        body = "run the migrations against staging first"   # substantive, no cue
        with mock.patch.dict(
                os.environ, {self.mod.MINER_RECURRENCE_CUE_REQUIRED_KEY: "1"}):
            self.assertEqual(self._mine_body_across(body), 0,
                             "cue_required => a non-cue body never emits")

    def test_stopwords_are_honored_from_env(self):
        self._seed_zero()
        # "deploy on fridays" is not an ack by default, but becomes one when the
        # operator declares those tokens acks.
        body = "deploy on fridays"
        with mock.patch.dict(
                os.environ,
                {self.mod.MINER_RECURRENCE_STOPWORDS_KEY: "deploy,on,fridays"}):
            self.assertEqual(self._mine_body_across(body), 0,
                             "operator-supplied stopwords gate it")

    def test_empty_stopwords_config_disables_the_ack_subtest(self):
        gate = dict(self.mod._transcript_recurrence_gate(),
                    stopwords=self.mod._parse_miner_stopwords(""))
        self.assertEqual(gate["stopwords"], frozenset())
        key = self.mod._transcript_norm_key("yes ok thanks that works")
        self.assertTrue(self.mod._transcript_recurrence_relevant(key, gate),
                        "an explicitly empty stopword list disables (b)")

    def test_invalid_min_len_config_degrades_to_the_default(self):
        # A hand-edited typo must NOT silently disable the gate.
        for bad in ("", "abc", "-3", "  "):
            with self.subTest(bad=bad):
                self.assertIsNone(self.mod._parse_miner_min_len(bad))
        self.assertEqual(self.mod._parse_miner_min_len("0"), 0,
                         "0 is VALID and means 'no length gate'")
        with mock.patch.dict(os.environ,
                             {self.mod.MINER_RECURRENCE_MIN_LEN_KEY: "abc"}):
            self.assertEqual(self.mod._transcript_recurrence_gate()["min_len"],
                             self.mod.MINER_RECURRENCE_MIN_LEN)

    def test_gate_is_deterministic_across_calls(self):
        # INV-1: same key + same gate => same verdict, no clock/randomness.
        gate = self.mod._transcript_recurrence_gate()
        for key in ("continue pls", "always pin dependency versions", "yes"):
            k = self.mod._transcript_norm_key(key)
            verdicts = {self.mod._transcript_recurrence_relevant(k, gate)
                        for _ in range(5)}
            self.assertEqual(len(verdicts), 1, "byte-stable verdict for %r" % key)

    def test_error_recovery_emission_is_unaffected_by_the_gate(self):
        # The gate applies to RECURRENCE only: a short error-recovery candidate
        # (whose text would fail the length sub-test) must still emit.
        self._seed_zero()
        self._write_session("A", [self._call("Read", "ERR", "/f", "boom"),
                                  self._call("Read", "ok", "/f")])
        self.assertEqual(self._mine(), 1, "error-recovery is not gated")
        self.assertEqual(self._queue()[0]["signal"], "error-recovery")
