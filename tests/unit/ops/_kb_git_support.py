"""Shared header for the `test_kb_git` suite, split out by task 58.

Holds every import, helper, constant and base class the split
parts share, so no part duplicates a definition and none can
drift from another.
"""

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import types
import unittest
try:  # works both as `tests.unit.ops.test_kb_git` and under `discover -s tests`
    from tests._fixtures import load_cli, run_cli, build_synthetic_kb
except ImportError:
    from _fixtures import load_cli, run_cli, build_synthetic_kb
def _have_git():
    try:
        subprocess.run(["git", "--version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
        return True
    except FileNotFoundError:
        return False
HAVE_GIT = _have_git()
def _run(cwd, *args):
    """Run a git command in `cwd`, raising on failure (test setup must succeed)."""
    return subprocess.run(["git", "-C", cwd] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=True)
def _init_repo(path, with_commit=True):
    """Initialize a git repo at `path` with deterministic identity + branch."""
    os.makedirs(path, exist_ok=True)
    _run(path, "init", "-q", "-b", "main")
    _run(path, "config", "user.email", "test@example.com")
    _run(path, "config", "user.name", "Test")
    if with_commit:
        with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as f:
            f.write("seed\n")
        _run(path, "add", "-A")
        _run(path, "commit", "-q", "-m", "seed")
    return path
def _commit_count(repo):
    cp = _run(repo, "rev-list", "--count", "HEAD")
    return int(cp.stdout.strip())
def _head_subject(repo):
    return _run(repo, "log", "-1", "--pretty=%s").stdout.strip()
def _write_entry(cli, repo, entry):
    """Materialize one entry .md WITH frontmatter under `repo` (the rebuild source)."""
    abs_path = os.path.join(repo, entry["path"])
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    fm = cli.build_entry_frontmatter(entry, repo, author="test")
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(cli.render_frontmatter(fm) + entry["body"])
def _seed_entry(eid):
    return {
        "id": "learn-%s" % eid, "title": "Seed %s" % eid,
        "category": "learnings", "path": "learnings/%s.md" % eid,
        "tags": ["seed", eid], "created": "2026-01-01",
        "summary": "Seed entry %s." % eid,
        "body": "# Seed %s\n\nSeed knowledge body for %s.\n" % (eid, eid),
    }
if __name__ == "__main__":
    unittest.main()
