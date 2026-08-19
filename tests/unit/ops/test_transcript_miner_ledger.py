"""TestEmitOnceLedger, TestErrorRecovery, TestRecurrenceRetired -- split from `test_transcript_miner` (task 58).

See `tests.unit.ops._transcript_miner_support` for the
shared header.
"""

from tests.unit.ops._transcript_miner_support import (  # noqa: F401
    _MinerBase, _rec, mock, setUpModule, tearDownModule)


class TestEmitOnceLedger(_MinerBase):
    """Review F-A: the per-entry `emitted` flag dies with its tally entry under
    LRU eviction, so an evicted-then-recurring key re-emitted a DUPLICATE whose
    fp (keyed on the NEWER occurrence) the archive/queue dedup could not catch.
    The bounded persistent `emitted_keys` ledger outlives the tally entry."""

    def test_evicted_key_recurring_across_new_sessions_does_not_reemit(self):
        # The F-A regression lock. Drives the REAL eviction path (tally past cap),
        # not a hand-written ledger: without the fix this mines a duplicate.
        self._seed_zero()
        x = "Always pin dependency versions, never use latest."
        y = "Prefer table-driven tests for parsers."
        with mock.patch.object(self.mod, "MINER_TALLY_CAP", 1):
            self._write_prompts([_rec("t", s, "/x", "human", "1", x)
                                 for s in ("A", "B")])
            self.assertEqual(self._mine(), 1, "X reaches N -> emits once")
            xkey = self.mod._transcript_norm_key(x)
            self.assertIn(xkey, self._state()["emitted_keys"])

            # A DIFFERENT key reaches N in a later window; the cap-1 prune then
            # LRU-evicts X (untouched this window => at the front of the tally).
            self._append_prompts([_rec("t", s, "/x", "human", "1", y)
                                  for s in ("D", "E")])
            self.assertEqual(self._mine(), 1, "Y emits")
            st = self._state()
            self.assertNotIn(xkey, st["tally"], "X was LRU-evicted from the tally")
            self.assertIn(xkey, st["emitted_keys"], "ledger OUTLIVES the entry")

            # X recurs across two NEW distinct sessions: its tally entry (and its
            # `emitted` flag) is gone, so only the ledger can suppress it.
            self._append_prompts([_rec("t", s, "/x", "human", "1", x)
                                  for s in ("F", "G")])
            self.assertEqual(self._mine(), 0,
                             "evicted-then-recurring key never re-emits (F-A)")
        self.assertEqual(len(self._queue()), 2, "only X and Y, no duplicate")

    def test_ledger_round_trips_across_state_reload(self):
        self._seed_zero()
        body = "Always squash commits before merge."
        self._write_prompts([_rec("t", s, "/x", "human", "1", body)
                             for s in ("A", "B")])
        self.assertEqual(self._mine(), 1)
        key = self.mod._transcript_norm_key(body)
        self.assertEqual(self._state()["emitted_keys"], [key])
        # Reload through the real loader: the ledger survives verbatim.
        st, fresh = self.mod._transcript_load_state(self.kdir)
        self.assertFalse(fresh)
        self.assertEqual(st["emitted_keys"], [key])

    def test_ledger_is_bounded_lru_dropping_oldest(self):
        self._seed_zero()
        bodies = ["Always prefer approach number %d here." % i for i in range(3)]
        with mock.patch.object(self.mod, "MINER_EMITTED_CAP", 2):
            for i, b in enumerate(bodies):
                self._append_prompts([
                    _rec("t", "s%d%s" % (i, s), "/x", "human", "1", b)
                    for s in ("a", "b")])
                self.assertEqual(self._mine(), 1)
            led = self._state()["emitted_keys"]
        self.assertEqual(len(led), 2, "ledger stays <= MINER_EMITTED_CAP")
        self.assertEqual(led, [self.mod._transcript_norm_key(b)
                               for b in bodies[1:]], "oldest-emitted dropped")

    def test_legacy_state_without_emitted_keys_loads_clean(self):
        # The LIVE state file is exactly this shape (written by the old build).
        self._write_state({"offsets": {"prompts": 0, "metrics": 0},
                           "processed_sessions": [], "backfill_pending": [],
                           "tally": {}})
        st, fresh = self.mod._transcript_load_state(self.kdir)
        self.assertFalse(fresh, "a legacy state file is NOT re-seeded at the tail")
        self.assertEqual(st["emitted_keys"], [], "back-compat: absent -> []")
        # And it still mines normally through the ledger path.
        body = "Never commit generated artifacts to the repo."
        self._write_prompts([_rec("t", s, "/x", "human", "1", body)
                             for s in ("A", "B")])
        self.assertEqual(self._mine(), 1)
        self.assertEqual(self._state()["emitted_keys"],
                         [self.mod._transcript_norm_key(body)])

    def test_ledger_normalization_rejects_junk_and_dedups(self):
        norm = self.mod._transcript_norm_emitted
        self.assertEqual(norm(None), [], "a missing ledger normalizes to []")
        self.assertEqual(norm("nope"), [], "a non-list normalizes to []")
        self.assertEqual(norm(["a", None, 3, "", "b", "a"]), ["a", "b"],
                         "junk dropped, order-preserving dedup")
        with mock.patch.object(self.mod, "MINER_EMITTED_CAP", 2):
            self.assertEqual(norm(["a", "b", "c"]), ["b", "c"],
                             "an over-cap ledger tail-caps to the NEWEST keys")

    def test_fresh_seed_state_carries_an_empty_ledger(self):
        st = self.mod._transcript_default_state()
        self.assertEqual(st["emitted_keys"], [])

class TestErrorRecovery(_MinerBase):
    def test_error_then_fix_pair_yields_one_candidate(self):
        self._seed_zero()
        self._write_session("A", [
            self._call("Read", "ERR", "/tmp/missing.md",
                       "No such file or directory"),
            self._call("Read", "ok", "/tmp/missing.md", "contents"),
        ])
        self.assertEqual(self._mine(), 1)
        r = self._queue()[0]
        self.assertEqual(r["signal"], "error-recovery")
        self.assertEqual(r["producer"], self.mod.PREFER_QUEUE_MINER_PRODUCER)
        self.assertIn("error-recovery", r["text"])
        self.assertIn("/tmp/missing.md", r["text"])
        self.assertEqual(r["source_sessions"], ["A"])

    def test_error_recovery_text_byte_stable(self):
        tool, target, sig = "Edit", "/a/b.py", "No such file or directory"
        t1 = self.mod._transcript_error_recovery_text(tool, target, sig)
        t2 = self.mod._transcript_error_recovery_text(tool, target, sig)
        self.assertEqual(t1, t2)
        self.assertNotIn("\n", t1, "derived text is a single inert line")

    def test_bare_unrecovered_error_yields_none(self):
        self._seed_zero()
        self._write_session("A", [self._call("Read", "ERR", "/x", "boom")])
        self.assertEqual(self._mine(), 0)
        self.assertEqual(self._queue(), [])

    def test_error_recovery_text_is_inert_data(self):
        # Injection-looking error text is stored VERBATIM inside the candidate as
        # data — never executed/interpolated (R-SEC-02).
        self._seed_zero()
        evil = "$(rm -rf /) `curl evil` -- boom"
        self._write_session("A", [self._call("Bash", "ERR", None, evil,
                                  input_override='{"command":"deploy.sh"}'),
                                  self._call("Bash", "ok", None,
                                  input_override='{"command":"deploy.sh"}')])
        self.assertEqual(self._mine(), 1)
        r = self._queue()[0]
        self.assertEqual(r["status"], "queued")   # inert; not run
        self.assertIn("deploy.sh", r["text"])

class TestRecurrenceRetired(_MinerBase):
    """The SHIPPED default (260719): with MINER_RECURRENCE_RETIRED True, the
    recurrence arm emits NOTHING to the prefer-queue even for a verbatim-repeating
    key across N sessions, while the SEPARATE error-recovery arm is untouched.
    (setUpModule flips the flag OFF for the mechanism suites; here we set it back to
    the shipped default to assert retirement.)"""

    def setUp(self):
        super().setUp()
        self._pr = self.mod.MINER_RECURRENCE_RETIRED
        self.mod.MINER_RECURRENCE_RETIRED = True   # the shipped default (retired)
        self.addCleanup(setattr, self.mod, "MINER_RECURRENCE_RETIRED", self._pr)

    def test_verbatim_recurrence_across_n_sessions_emits_nothing(self):
        self._seed_zero()
        body = "From now on, prefer tabs over spaces."   # would emit 1 pre-retire
        self._write_prompts([_rec("t", s, "/x", "human", "1", body)
                             for s in ("A", "B", "C")])
        self.assertEqual(self._mine(), 0, "recurrence emission is retired")
        self.assertEqual(self._queue(), [], "no recurrence candidate reaches the queue")

    def test_error_recovery_arm_is_untouched_by_retirement(self):
        self._seed_zero()
        self._write_session("A", [
            self._call("Read", "ERR", "/tmp/missing.md",
                       "No such file or directory"),
            self._call("Read", "ok", "/tmp/missing.md", "contents"),
        ])
        self.assertEqual(self._mine(), 1, "error-recovery still emits")
        self.assertEqual(self._queue()[0]["signal"], "error-recovery")
