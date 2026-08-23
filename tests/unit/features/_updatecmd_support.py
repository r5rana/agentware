"""Shared header for the `features/updatecmd` owning unit file — wave 15.

`test_updatecmd.py` hit the 600-line `tests/**` cap the commit it landed in, so
its HEADER moved here rather than the file declaring a debt row in the very
commit whose purpose is to pay four down (R-MOD-04: split where the obligation
changes, never raise a cap to fit the code).

The obligation this file holds is ISOLATION, and it is one obligation: the stub
table below is what makes every `update` test run without git, without a
network, without the operator's `~/.agentware` and without the real REPO_ROOT.
Keeping it in one place is what stops a later half of the corpus from quietly
stubbing `_pkg_run_update` differently and testing a different product.

Not a test module: it defines no TestCase with test methods of its own, and the
`_`-prefix keeps `unittest discover` from collecting it.
"""

from tests._paths import TESTS_DIR

import contextlib
import io
import os
import sys
import unittest

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.features.updatecmd as U                    # noqa: E402


class _Proc(object):
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


class _Args(object):
    def __init__(self, **kw):
        base = dict(check=False, catalog=False, self_heal=False,
                    staleness_check=False, status_line=False, format="text")
        base.update(kw)
        for k, v in base.items():
            setattr(self, k, v)


def _sparse(**kw):
    ns = type("Sparse", (object,), {})()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


@contextlib.contextmanager
def _captured():
    out, err = io.StringIO(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        yield out, err
    finally:
        sys.stdout, sys.stderr = real_out, real_err


def _git_counts(target, *a):
    if "--abbrev-ref" in a:
        return _Proc("origin/main\n", 0)
    if a[:2] == ("rev-list", "--count"):
        return _Proc("3\n" if a[2] == "HEAD..@{upstream}" else "1\n", 0)
    return _Proc("", 0)


#: Stubs that make every path terminate WITHOUT git, a network, or a real apply.
_DEFAULTS = {
    "_pkg_state_read": lambda: {},
    "_pkg_staleness_prehook": lambda kdir, target: 0,
    "_pkg_self_heal": lambda kdir, target: {"status": "clean",
                                            "detail": "nothing to heal"},
    "git_is_work_tree": lambda target: True,
    "git_has_upstream": lambda target: False,
    "git_fetch": lambda target: _Proc("", 0),
    "_git": lambda target, *a: _Proc("", 0),
    "_pkg_resolve_verified_tag": lambda target: {"status": "up-to-date",
                                                 "detail": "on v1.0.0",
                                                 "warnings": []},
    "_pkg_run_update": lambda target: {"status": "up-to-date",
                                       "detail": "already current",
                                       "warnings": []},
    "is_session_active": lambda kdir: None,
    "_pkg_version": lambda target: "1.0.0",
    "_pinned_ref": lambda: "v1.0.0",
    "_catalog_skill_names_at_ref": lambda ref: None,
    "require_knowledge_dir": lambda: "/scratch/kb",
}


class UpdateCase(unittest.TestCase):
    def setUp(self):
        self._saved = {n: getattr(U, n) for n in _DEFAULTS}
        for name, stub in _DEFAULTS.items():
            setattr(U, name, stub)
        self.addCleanup(self._restore)

    def _restore(self):
        for name, real in self._saved.items():
            setattr(U, name, real)

    def stub(self, **over):
        for name, value in over.items():
            setattr(U, name, value)

    def run_update(self, args, **over):
        self.stub(**over)
        with _captured() as (out, err):
            rc = U.cmd_update(args)
        return rc, out.getvalue(), err.getvalue()


