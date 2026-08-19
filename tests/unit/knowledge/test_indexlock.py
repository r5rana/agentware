"""Task 3 (P1.7) — flock the save-index critical section (B2 lost-update guard).

Proves:
  (a) two concurrent load->add->save writers serialize and BOTH adds survive —
      across THREADS (the process-wide RLock) and across PROCESSES (fcntl.flock);
  (b) the fcntl-unavailable path degrades WITHOUT crashing;
  (c) save_index is still atomic + byte-canonical (no leftover temp files);
  (d) the lock is RE-ENTRANT — a lock holder whose load_index auto-rebuilds (which
      re-enters via rebuild_kb) does NOT self-deadlock.

Stdlib-only unittest; throwaway temp KB (R-LOC-03).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

try:
    from tests._fixtures import load_cli, run_cli, build_synthetic_kb, CLI_PATH
except ImportError:
    from _fixtures import load_cli, run_cli, build_synthetic_kb, CLI_PATH


CLI = load_cli()


class IndexLockTestCase(unittest.TestCase):
    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw-indexlock-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)

    def _read_index(self):
        data, err = CLI.load_index(self.kdir)
        self.assertIsNone(err, err)
        return data


class MutualExclusionTest(IndexLockTestCase):
    def test_indexlock_serializes_thread_holders(self):
        # Two threads' critical sections must NOT interleave.
        order = []
        barrier = threading.Barrier(2)

        def worker(tag):
            barrier.wait()
            with CLI._index_lock(self.kdir):
                order.append((tag, "enter"))
                time.sleep(0.05)
                order.append((tag, "exit"))

        threads = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(order), 4)
        # enter/exit strictly paired per holder — no interleave.
        self.assertEqual(order[0][1], "enter")
        self.assertEqual(order[1][1], "exit")
        self.assertEqual(order[0][0], order[1][0])
        self.assertEqual(order[2][1], "enter")
        self.assertEqual(order[3][1], "exit")
        self.assertEqual(order[2][0], order[3][0])

    def test_indexlock_concurrent_thread_adds_all_survive(self):
        # N threads each do a full load->_do_add->save under the lock; a lost
        # update (unserialized) would drop some. All must survive.
        n = 12

        def add(i):
            eid = "learn-thread-%02d" % i
            rel = "learnings/thread-%02d.md" % i
            abs_path = os.path.join(self.kdir, rel)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write("---\nid: %s\ncategory: learnings\n---\nbody %d\n"
                        % (eid, i))
            with CLI._index_lock(self.kdir):
                data, err = CLI.load_index(self.kdir)
                assert err is None, err
                _e, aerr = CLI._do_add(self.kdir, data, eid, "T%d" % i,
                                       "learnings", rel, ["t"], "s")
                assert aerr is None, aerr
                CLI.save_index(self.kdir, data)

        threads = [threading.Thread(target=add, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ids = {e["id"] for e in self._read_index()["entries"]}
        for i in range(n):
            self.assertIn("learn-thread-%02d" % i, ids,
                          "a concurrent add was lost (no serialization)")


class CrossProcessTest(IndexLockTestCase):
    def test_indexlock_concurrent_process_adds_all_survive(self):
        # Real-world scenario: N `learn` PROCESSES on one host concurrently. Each
        # is a separate interpreter (its own RLock), so cross-process
        # serialization rests SOLELY on the fcntl.flock. All adds must survive.
        n = 6
        env = dict(os.environ, AGENTWARE_KNOWLEDGE_DIR=self.kdir)
        env.pop("AGENTWARE_DREAM", None)
        procs = []
        for i in range(n):
            procs.append(subprocess.Popen(
                [sys.executable, CLI_PATH, "learn",
                 "--topic", "proc-%02d" % i,
                 "--summary", "concurrent add %d" % i,
                 "--tags", "proc",
                 "--content", "distinct process body number %d qzx%d" % (i, i),
                 "--force"],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True))
        errs = []
        for p in procs:
            _o, e = p.communicate()
            if p.returncode != 0:
                errs.append(e)
        self.assertEqual(errs, [], "a concurrent `learn` process failed: %s" % errs)
        ids = {e["id"] for e in self._read_index()["entries"]}
        for i in range(n):
            self.assertIn("learn-proc-%02d" % i, ids,
                          "a concurrent process add was lost (no flock)")


class DegradeTest(IndexLockTestCase):
    def test_indexlock_degrades_without_fcntl(self):
        # Where fcntl is unavailable the RLock still serializes threads and the
        # lock never crashes.
        # Patch the OWNING module, not the CLI facade. `_index_lock` reads
        # `_fcntl` out of `indexlock`'s own globals, so rebinding the facade's
        # copy never reached it -- the harness now refuses that rebind outright
        # rather than letting the test pass while proving nothing.
        import agentware_lib.knowledge.index.indexlock as _indexlock
        real = _indexlock._fcntl
        _indexlock._fcntl = None
        try:
            with CLI._index_lock(self.kdir):
                data, err = CLI.load_index(self.kdir)
                self.assertIsNone(err, err)
            # A full mutating verb still works with fcntl stubbed out.
            code, _o, err = run_cli(
                ["learn", "--topic", "nofcntl", "--summary", "s",
                 "--tags", "t", "--content", "no fcntl body zzz", "--force"],
                self.kdir)
            self.assertEqual(code, 0, err)
        finally:
            _indexlock._fcntl = real
        self.assertTrue(any(e["id"] == "learn-nofcntl"
                            for e in self._read_index()["entries"]))


class AtomicityTest(IndexLockTestCase):
    def test_indexlock_save_index_still_atomic_and_canonical(self):
        data = self._read_index()
        data["entries"][0]["summary"] = "touched for atomicity check"
        CLI.save_index(self.kdir, data)
        with open(os.path.join(self.kdir, "index.json"), encoding="utf-8") as f:
            raw = f.read()
        # Canonical: 2-space indent + trailing newline, round-trips.
        self.assertTrue(raw.endswith("\n"))
        self.assertEqual(json.loads(raw)["entries"][0]["summary"],
                         "touched for atomicity check")
        # No leftover temp files from the atomic tempfile+rename.
        leftover = [f for f in os.listdir(self.kdir)
                    if f.startswith(".index.") and f.endswith(".tmp")]
        self.assertEqual(leftover, [])


class ReentrancyTest(IndexLockTestCase):
    def test_indexlock_nested_acquire_no_deadlock(self):
        # Nested acquisition in one thread must not self-deadlock (depth counter).
        with CLI._index_lock(self.kdir):
            with CLI._index_lock(self.kdir):
                data, err = CLI.load_index(self.kdir)
                self.assertIsNone(err, err)

    def test_indexlock_reentrant_rebuild_inside_writer_no_deadlock(self):
        # A `learn` whose load_index auto-rebuilds (missing index.json) re-enters
        # the lock via rebuild_kb WHILE the learn already holds it. Must complete.
        os.remove(os.path.join(self.kdir, "index.json"))
        code, _o, err = run_cli(
            ["learn", "--topic", "reentrant", "--summary", "s", "--tags", "t",
             "--content", "reentrant rebuild body zzz", "--force"], self.kdir)
        self.assertEqual(code, 0, err)
        ids = {e["id"] for e in self._read_index()["entries"]}
        self.assertIn("learn-reentrant", ids)
        # The pre-existing entries were recovered by the reentrant rebuild.
        self.assertIn("learn-geofence-reminders", ids)


if __name__ == "__main__":
    unittest.main()
