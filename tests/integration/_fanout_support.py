"""Shared header for the `test_fanout` suite, split out by task 58.

Holds every import, helper, constant and base class the split
parts share, so no part duplicates a definition and none can
drift from another.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
try:
    from tests._fixtures import load_cli, run_cli
except ImportError:  # pragma: no cover - direct `pytest tests/integration/test_fanout.py`
    from _fixtures import load_cli, run_cli
def _have_git():
    try:
        subprocess.run(["git", "--version"], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, check=True)
        return True
    except Exception:  # pragma: no cover
        return False
HAVE_GIT = _have_git()
def _run(cwd, *args):
    """Run a git command in `cwd`, raising on failure (test setup must succeed)."""
    return subprocess.run(["git", "-C", cwd] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=True)
def _entry(eid):
    """A frontmatter-ready entry dict (the rebuild_kb source of truth)."""
    return {
        "id": "learn-%s" % eid, "title": "Seed %s" % eid,
        "category": "learnings", "path": "learnings/%s.md" % eid,
        "tags": ["seed", eid], "created": "2026-01-01",
        "summary": "Seed entry %s." % eid,
        "body": "# Seed %s\n\nSeed knowledge body for %s.\n" % (eid, eid),
    }
def _write_fm_entry(cli, repo, eid):
    """Materialize one entry .md WITH frontmatter under `repo` (rebuild source)."""
    entry = _entry(eid)
    abs_path = os.path.join(repo, entry["path"])
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    fm = cli.build_entry_frontmatter(entry, repo, author="test")
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(cli.render_frontmatter(fm) + entry["body"])
@unittest.skipUnless(HAVE_GIT, "git not available")
class FanoutTestBase(unittest.TestCase):
    """Stands up two throwaway git repos (a toy package + a synthetic KB) and
    pins fanout state to a tempdir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-test-fanout-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # Pin fanout state to a tempdir — never touch ~/.agentware.
        self.fanout_dir = os.path.join(self.tmp, "fanout-home")
        self._prev_fanout = os.environ.get("AGENTWARE_FANOUT_DIR")
        os.environ["AGENTWARE_FANOUT_DIR"] = self.fanout_dir
        self.addCleanup(self._restore_fanout)
        self.cli = load_cli()
        self.repo = self._seed_pkg_repo("pkg")
        self.kb_repo = self._seed_kb_repo("kb")

    def _restore_fanout(self):
        if self._prev_fanout is None:
            os.environ.pop("AGENTWARE_FANOUT_DIR", None)
        else:
            os.environ["AGENTWARE_FANOUT_DIR"] = self._prev_fanout

    # -- helpers --------------------------------------------------------------
    def _mk(self, name):
        return os.path.join(self.tmp, name)

    def _seed_pkg_repo(self, name):
        """A toy 'package' repo whose `scripts/agentware` monolith carries widely
        separated anchor lines, so disjoint lane edits rebase cleanly while
        same-line edits conflict (the merge-queue's monolith chokepoint)."""
        path = self._mk(name)
        os.makedirs(os.path.join(path, "scripts"), exist_ok=True)
        lines = ["# toy agentware monolith (test stand-in)", ""]
        lines += ["line_%02d = %d" % (i, i) for i in range(40)]
        with open(os.path.join(path, "scripts", "agentware"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        _run(path, "init", "-q", "-b", "main")
        _run(path, "config", "user.email", "test@example.com")
        _run(path, "config", "user.name", "Test")
        _run(path, "add", "-A")
        _run(path, "commit", "-q", "-m", "seed pkg")
        return path

    def _seed_kb_repo(self, name):
        """A valid frontmatter KB committed as a git repo (so kb/<f> worktrees
        branch off it AND rebuild_kb stays valid through merges)."""
        path = self._mk(name)
        for sub in ("learnings", "projects", "configurations", "prompts",
                    "references", "skills"):
            os.makedirs(os.path.join(path, sub), exist_ok=True)
        _write_fm_entry(self.cli, path, "seed-one")
        data, _rosters, errors = self.cli.rebuild_kb(path)
        self.assertEqual(errors, [], "seed rebuild must be clean")
        # Mirror the real KB: loop state AND derived caches are gitignored, so a
        # finished lane's KB worktree reads CLEAN (the .loop/.done sentinel is not
        # "dirty", and the derived `.cache/` postings/vector caches that rebuild_kb
        # writes are never tracked/committed — machine-local, regenerable).
        with open(os.path.join(path, ".gitignore"), "w", encoding="utf-8") as f:
            f.write(".loop/\nlogs/\n.cache/\n")
        # An append-only ledger (like benchmarks/history.jsonl) so concurrent-lane
        # EOF-append conflicts can be exercised.
        os.makedirs(os.path.join(path, "benchmarks"), exist_ok=True)
        with open(os.path.join(path, "benchmarks", "history.jsonl"),
                  "w", encoding="utf-8") as f:
            f.write('{"seed": true}\n')
        _run(path, "init", "-q", "-b", "main")
        _run(path, "config", "user.email", "test@example.com")
        _run(path, "config", "user.name", "Test")
        _run(path, "add", "-A")
        _run(path, "commit", "-q", "-m", "seed kb")
        return path

    def _fanout(self, *argv):
        """Run the fanout CLI in-process; returns (rc, out, err)."""
        return run_cli(list(argv), self.kb_repo)

    def _spin_up(self, *features, base=None):
        argv = ["fanout", "spin-up", "--repo", self.repo,
                "--kb-repo", self.kb_repo]
        if base:
            argv += ["--base", base]
        argv += list(features)
        return self._fanout(*argv)

    def _lanes(self):
        path = os.path.join(self.fanout_dir, "lanes.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _has_branch(self, repo, branch):
        return subprocess.run(
            ["git", "-C", repo, "rev-parse", "--verify", "--quiet",
             "refs/heads/%s" % branch],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0

    # -- lane-finishing helpers (the TEST stands in for the loop/agent) --------
    def _pkg_wt(self, feature):
        return os.path.join(self.fanout_dir, "worktrees", feature, "pkg")

    def _kb_wt(self, feature):
        return os.path.join(self.fanout_dir, "worktrees", feature, "kb")

    def _insert_after(self, worktree, anchor, *newlines):
        """Insert lines after the first occurrence of `anchor` in the monolith."""
        p = os.path.join(worktree, "scripts", "agentware")
        with open(p, encoding="utf-8") as f:
            lines = f.read().splitlines()
        out = []
        for ln in lines:
            out.append(ln)
            if ln == anchor:
                out.extend(newlines)
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")

    def _finish_lane(self, feature, anchor, *subcmd_lines, kb_entry=None):
        """Stage a lane's FINISHED end-state deterministically (no loop/LLM):
        a disjoint subcommand committed on feat/<f>, an optional KB entry committed
        on kb/<f>, and the `.loop/.done` sentinel so the outcome reads completed."""
        pkg = self._pkg_wt(feature)
        self._insert_after(pkg, anchor, *subcmd_lines)
        _run(pkg, "add", "-A")
        _run(pkg, "commit", "-q", "-m", "feat %s" % feature)
        kb = self._kb_wt(feature)
        if kb_entry:
            _write_fm_entry(self.cli, kb, kb_entry)
            _data, _r, errs = self.cli.rebuild_kb(kb)
            self.assertEqual(errs, [], "lane kb rebuild must be clean")
            _run(kb, "add", "-A")
            _run(kb, "commit", "-q", "-m", "kb %s" % feature)
        loop = os.path.join(kb, "work", feature, ".loop")
        os.makedirs(loop, exist_ok=True)
        open(os.path.join(loop, ".done"), "w").close()

    def _read_integration_monolith(self, branch="integration"):
        cp = subprocess.run(
            ["git", "-C", self.repo, "show", "%s:scripts/agentware" % branch],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return cp.stdout

    def _integration_kb_ids(self, branch="integration"):
        cp = subprocess.run(
            ["git", "-C", self.kb_repo, "show", "%s:index.json" % branch],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        data = json.loads(cp.stdout)
        return {e["id"] for e in data["entries"]}

    def _porcelain(self, repo):
        return subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                              stdout=subprocess.PIPE, text=True).stdout.strip()

    def _append_ledger(self, feature, line):
        """Append a row to the lane KB worktree's append-only ledger + commit."""
        kb = self._kb_wt(feature)
        with open(os.path.join(kb, "benchmarks", "history.jsonl"),
                  "a", encoding="utf-8") as f:
            f.write(line + "\n")
        _run(kb, "add", "-A")
        _run(kb, "commit", "-q", "-m", "ledger %s" % feature)

    def _show(self, repo, ref_path):
        return subprocess.run(["git", "-C", repo, "show", ref_path],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True).stdout
if __name__ == "__main__":  # pragma: no cover
    unittest.main()
