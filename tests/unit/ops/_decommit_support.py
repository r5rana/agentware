"""Shared header for the `test_decommit` suite, split out by task 58.

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
    from tests._fixtures import load_cli, run_cli, build_synthetic_kb
except ImportError:  # discover -s tests
    from _fixtures import load_cli, run_cli, build_synthetic_kb
CLI = load_cli()
def _have_git():
    try:
        subprocess.run(["git", "--version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
        return True
    except FileNotFoundError:
        return False
HAVE_GIT = _have_git()
def _git(cwd, *args, check=True):
    return subprocess.run(["git", "-C", cwd] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=check)
def _init_repo(path):
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    return path
def _tracked(repo):
    return set(_git(repo, "ls-files").stdout.split("\n")) - {""}
def _is_ignored(repo, rel):
    """True iff git would ignore `rel` in `repo` (check-ignore exit 0)."""
    cp = _git(repo, "check-ignore", "-q", rel, check=False)
    return cp.returncode == 0
if __name__ == "__main__":
    unittest.main()
