"""Sibling of `test_setupcmd.py` — the `profile` verb — task 99 wave 17.

Split off from the owning file, which crossed the 600-line `tests/**` cap in the
commit paying four ceiling rows down; growing it would have WORSENED one ratchet
in the change whose purpose is to shrink another (R-MOD-04: split where the
obligation changes, never raise a cap to fit).

The seam is not arbitrary and is not "the file got long". `setupcmd` declares TWO
verbs, and they share a module for one reason — both are workspace-setup surfaces
— while sharing nothing else. `init` WRITES: it scaffolds a tree, seeds an index,
installs templates and repoints a config, and every assertion about it is about
idempotence and step order. `profile` READS: it resolves a path and reports
whether it exists, touching nothing. The owning file keeps the writer; this one
keeps the resolver.

What is asserted here is `profile`'s promise:

  * IT IS A RESOLVER, NOT A VALIDATOR. An absent overlay is reported as absent
    and is NOT an error — the caller is asking where the file would go.
  * A HANDLE IS A FILENAME, SO IT IS STRIPPED FIRST. A padded handle must not
    produce `  casey  .md`, and blankness is tested for AFTER stripping.
  * A BLANK HANDLE EXITS 2, NOT 1. The two codes mean different things (2 is a
    usage error, 1 is an unconfigured workspace) and both arms are asserted.

Hermetic: reuses the owning file's `_InitCase` fixture, so the knowledge dir is a
fresh mkdtemp and `HOME_CONFIG` is redirected into the scratch tree.
"""

from tests._paths import TESTS_DIR

import io
import json
import os
import sys
import unittest

from tests.unit.features.test_setupcmd import (      # noqa: E402
    Args, _InitCase, _write,
)

HERE = TESTS_DIR
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "scripts"))

import agentware_lib.core.config as config              # noqa: E402
import agentware_lib.features.setupcmd as setupcmd      # noqa: E402


class CmdProfileTest(_InitCase):

    def _profile(self, handle, fmt="text", kdir="/kb"):
        self._patch(config, "require_knowledge_dir", lambda: kdir)
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = setupcmd.cmd_profile(Args(handle=handle, format=fmt))
        finally:
            sys.stdout, sys.stderr = old
        return rc, out.getvalue(), err.getvalue()

    def test_it_resolves_a_path_under_profiles(self):
        rc, out, _err = self._profile("casey")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), os.path.join("/kb", "profiles", "casey.md"))

    def test_it_is_a_resolver_and_not_a_validator(self):
        # The file need not exist; `exists` REPORTS that rather than failing.
        rc, out, _err = self._profile("ghost", fmt="json")
        self.assertEqual(rc, 0)
        self.assertFalse(json.loads(out)["exists"])

    def test_an_existing_profile_is_reported_as_present(self):
        _write(os.path.join(self.kdir, "profiles", "casey.md"), "# me\n")
        _rc, out, _err = self._profile("casey", fmt="json", kdir=self.kdir)
        self.assertTrue(json.loads(out)["exists"])

    def test_a_blank_handle_is_refused_with_exit_two(self):
        for handle in ("", "   ", None):
            rc, out, err = self._profile(handle)
            self.assertEqual((rc, out), (2, ""))
            self.assertIn("non-empty handle", err)

    def test_a_padded_handle_is_stripped_before_it_becomes_a_filename(self):
        _rc, out, _err = self._profile("  casey  ", fmt="json")
        self.assertEqual(json.loads(out)["handle"], "casey")
        self.assertTrue(json.loads(out)["path"].endswith("casey.md"))

    def test_an_unconfigured_workspace_exits_one_silently(self):
        # 1, not 2: an unconfigured workspace is a different failure from a
        # malformed invocation, and a caller scripting `profile` needs to tell
        # "run init first" from "you passed nothing".
        rc, out, err = self._profile("casey", kdir=None)
        self.assertEqual((rc, out, err), (1, "", ""))


if __name__ == "__main__":
    unittest.main()
