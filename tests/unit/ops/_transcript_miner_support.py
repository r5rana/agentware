"""Shared header for the `test_transcript_miner` suite, split out by task 58.

Holds every import, helper, constant and base class the split
parts share, so no part duplicates a definition and none can
drift from another.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock
try:
    from tests._fixtures import (load_cli, build_synthetic_kb,
                                 snapshot_corpus_kb, seed_miner_state_at_zero)
except ImportError:
    from _fixtures import (load_cli, build_synthetic_kb,
                           snapshot_corpus_kb, seed_miner_state_at_zero)
def _rec(ts, sid, cwd, origin, turn, prompt):
    """One prompts.log record in the exact log-prompt.sh format."""
    return ("[%s] [session %s] [cwd %s] [origin=%s] [turn=%s]\n%s\n\n"
            % (ts, sid, cwd, origin, turn, prompt))
_PREV_RECURRENCE_RETIRED = True
def setUpModule():
    # 260719: recurrence EMISSION is RETIRED by default (MINER_RECURRENCE_RETIRED).
    # These suites validate the dormant-but-REVERSIBLE mechanism (fold / emit-once /
    # LRU ledger / precision gate / wiring / governance), so re-enable it for the
    # module; the shipped default-off behavior is asserted in TestRecurrenceRetired.
    global _PREV_RECURRENCE_RETIRED
    _m = load_cli()
    _PREV_RECURRENCE_RETIRED = _m.MINER_RECURRENCE_RETIRED
    _m.MINER_RECURRENCE_RETIRED = False
def tearDownModule():
    load_cli().MINER_RECURRENCE_RETIRED = _PREV_RECURRENCE_RETIRED
class _MinerBase(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_miner_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = load_cli()
        os.makedirs(os.path.join(self.kdir, "logs", "sessions"), exist_ok=True)
        self.logp = os.path.join(self.kdir, "logs", "prompts.log")
        # HOME is the transcript root (_transcript_claude_path). Patch it to an
        # EMPTY temp home for every miner test so the reconciler can never read
        # the operator's real ~/.claude/projects (R-LOC-03) — without this, a
        # synthetic sid that happened to exist there would leak into a fixture.
        self.home = tempfile.mkdtemp(prefix="aw_home_")
        self.addCleanup(shutil.rmtree, self.home, True)
        p = mock.patch.dict(os.environ, {"HOME": self.home})
        p.start()
        self.addCleanup(p.stop)
        # HERMETICITY: the miner gates' MIDDLE resolver rung is
        # _read_config_key(...), which walks module-level CONFIG_PATHS — bound
        # at import time to the OPERATOR's real ~/.agentware/config.env. A knob
        # set there (e.g. AGENTWARE_MINER_ERRREC_DEDUP) would silently flip
        # these tests, so point it at a path that does not exist.
        cfgp = mock.patch.object(
            self.mod, "CONFIG_PATHS",
            [os.path.join(self.kdir, "_no_such_config.env")])
        cfgp.start()
        self.addCleanup(cfgp.stop)
        # HERMETICITY (highest resolver rung): the miner gates read env vars
        # FIRST, ABOVE CONFIG_PATHS. An operator who exports a knob
        # (AGENTWARE_MINER_ERRREC_DEDUP, or a recurrence knob) would silently
        # flip these tests, so neutralize the ambient values. The HOME patch.dict
        # above snapshots os.environ and restores it wholesale on stop, so these
        # pops are undone at teardown; a test that sets a knob explicitly (nested
        # patch.dict) still overrides.
        for _k in (self.mod.MINER_ERRREC_DEDUP_KEY,
                   self.mod.MINER_RECURRENCE_MIN_LEN_KEY,
                   self.mod.MINER_RECURRENCE_CUE_REQUIRED_KEY,
                   self.mod.MINER_RECURRENCE_STOPWORDS_KEY):
            os.environ.pop(_k, None)

    # --- corpus writers ---------------------------------------------------
    def _write_prompts(self, records):
        with open(self.logp, "w", encoding="utf-8") as f:
            f.write("".join(records))

    def _append_prompts(self, records):
        with open(self.logp, "a", encoding="utf-8") as f:
            f.write("".join(records))

    def _write_session(self, sid, calls, settled=True):
        sdir = os.path.join(self.kdir, "logs", "sessions", sid)
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "live.jsonl"), "w", encoding="utf-8") as f:
            for c in calls:
                f.write(json.dumps(c) + "\n")
        if settled:                       # main.jsonl present => Stop completed
            with open(os.path.join(sdir, "main.jsonl"), "w", encoding="utf-8") as f:
                f.write('{"type":"user"}\n')

    def _write_transcript(self, sid, calls, proj="-Users-x-proj"):
        """Write a durable Claude session transcript for <sid> under the patched
        HOME, in the REAL shape verified live 2026-07-14: an assistant message
        carrying a tool_use block (id/name/input), then a user message carrying
        the matching tool_result (tool_use_id/is_error/content).
        calls: [(tool_name, input_obj, is_error, content, ts)]"""
        d = os.path.join(self.home, ".claude", "projects", proj)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, sid + ".jsonl"), "w", encoding="utf-8") as f:
            for i, (name, inp, is_err, content, ts) in enumerate(calls):
                tuid = "toolu_%s_%d" % (sid, i)
                f.write(json.dumps({
                    "type": "assistant", "sessionId": sid, "timestamp": ts,
                    "message": {"content": [{"type": "tool_use", "id": tuid,
                                             "name": name, "input": inp}]}}) + "\n")
                f.write(json.dumps({
                    "type": "user", "sessionId": sid, "timestamp": ts,
                    "message": {"content": [{"type": "tool_result",
                                             "tool_use_id": tuid,
                                             "is_error": is_err,
                                             "content": content}]}}) + "\n")

    def _call(self, tool, status, target=None, response="", input_override=None):
        inp = input_override if input_override is not None else (
            json.dumps({"file_path": target}) if target else "{}")
        return {"ts": "2026-07-13T00:00:00Z", "tool": tool, "status": status,
                "input": inp, "response": response}

    # --- state control ----------------------------------------------------
    def _seed_zero(self, **over):
        """Write a state that reads prompts from offset 0 and scans all sessions
        incrementally (empty processed/pending) — the primary detector setup."""
        st = {"offsets": {"prompts": 0, "metrics": 0},
              "processed_sessions": [], "backfill_pending": [], "tally": {}}
        st.update(over)
        self._write_state(st)

    def _write_state(self, st):
        p = os.path.join(self.kdir, self.mod.MINER_STATE_REL)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(st, f)

    def _state(self):
        p = os.path.join(self.kdir, self.mod.MINER_STATE_REL)
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def _mine(self, backfill=False, cap=None):
        return self.mod._transcript_mine(self.kdir, backfill=backfill,
                                         session_cap=cap)

    def _queue(self):
        q = os.path.join(self.kdir, self.mod.PREFER_QUEUE_REL)
        if not os.path.isfile(q):
            return []
        out = []
        with open(q, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out
if __name__ == "__main__":
    unittest.main()
