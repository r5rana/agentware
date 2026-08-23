"""Shared fixture header for the `workstamp` owning unit files — task 99 w16.

ONE obligation: the scratch-file plumbing both halves of the module's owning
corpus need (a captured-stdout context, an argparse stand-in, read/write helpers,
and the work tree whose three counters deliberately differ). It holds NO
assertions and never will — a test here would make this the grab-bag the split
exists to prevent, and the boundary is checkable by grep: this file contains no
`def test_`.

Why the split at all: `test_workstamp.py` crossed the 600-line `tests/**` cap in
the commit that pays five ceiling rows down, and R-MOD-04 says to split where the
obligation changes rather than declare a debt row to fit the code — declaring one
here would have WORSENED a ratchet in the commit whose purpose is to shrink five.
The seam follows the module's own: the per-ARTIFACT writers
(`_prose_stamp_insert_index` / `_stamp_work_artifact` / `_ensure_work_frontmatter`
/ `_is_derived_roster`) in `test_workstamp.py`, and the TREE walk plus the handler
(`_stamp_work_tree` / `cmd_work_stamp`) in `test_workstamp_tree.py`.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import os
import sys

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

TEAM = "platform"


@contextlib.contextmanager
def _captured():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


class Args(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _build_tree(root):
    """A tree where scanned / stamped / frontmatter_added are THREE numbers.

    The three must DIFFER, or swapping two of them in the report is invisible —
    the first draft of this fixture had stamped == frontmatter_added and would
    have passed with the two counters exchanged. `design.md` is what separates
    them: it already carries a correct machine block, so it is stamped WITHOUT
    being counted as a frontmatter addition.

    Resulting counts: scanned 5, stamped 4, frontmatter_added 3.
    """
    _write(os.path.join(root, "plan.md"), "# Plan\n\nbody\n")
    _write(os.path.join(root, "worklog.md"), "# Worklog\n> Team: other\n\nb\n")
    _write(os.path.join(root, "done.md"), "# Done\n> Team: %s\n\nb\n" % TEAM)
    _write(os.path.join(root, "design.md"),
           "---\nteam: %s\norigin: team\n---\n\n# Design\n\nb\n" % TEAM)
    _write(os.path.join(root, "index.md"),
           "# Roster\n\n> Auto-generated roster — DO NOT edit\n")
    _write(os.path.join(root, "nested", "index.md"),
           "---\nid: x\nteam: %s\norigin: team\n---\n\n# Authored\n" % TEAM)
    _write(os.path.join(root, "notes.txt"), "not markdown\n")
    return root
