"""g1/g2 emitter-guard hardening tests (feature 260711-emitter-gate-hardening, T1).

BEHAVIORAL, not grep-shaped: these import the guard functions directly from the
extensionless `scripts/agentware` and exercise the ACTUAL code paths the fix
hardens. Crucially, the case/symlink/traversal classes use TOKEN-FREE paths
(derived from REPO_ROOT, carrying NO kernel token like 'scripts/agentware') so it
is the PATH-RESOLUTION branch (_resolves_under_repo), not the substring-token
loop, that is under test — a token-bearing path would green via the substring
loop and mask a broken path compare.

g1 (_target_hits_kernel / _resolves_under_repo / _text_hits_kernel_path):
  - case-variant of REPO_ROOT is refused via .casefold() (NOT os.path.normcase,
    a posix no-op) — the load-bearing correction for case-insensitive APFS;
  - a symlink INTO the package is refused (realpath follows it);
  - a `../` traversal that normalizes into the package is refused;
  - a ~-rooted package path is refused (expanduser);
  - a PATH-SHAPED kernel token in free text is refused, but a bare basename in
    prose still emits (pitfall b);
  - the sibling `agentware-knowledge` and unrelated abs paths still PASS.
g2 (_sanitize_provenance):
  - EVERY str.splitlines() separator is collapsed so a crafted provenance value
    can never materialize a second `> Status:`/`> Claimed-by:` header line.

The `test_g1harden_` prefix is unique against every existing test-module filename
AND method (pitfall g).

Runner: python3 -m unittest tests.test_g1harden -v
"""

import importlib.machinery
import importlib.util
import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import REPO_ROOT, attach_lib_resolver
except ImportError:  # discover -s tests puts tests/ on sys.path
    from _fixtures import REPO_ROOT, attach_lib_resolver


def _load_cli():
    ld = importlib.machinery.SourceFileLoader("aw_g1harden",
                                              os.path.join(REPO_ROOT, "scripts",
                                                           "agentware"))
    m = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("aw_g1harden", ld))
    ld.exec_module(m)
    # Task 56: with the monolith deleted this namespace has no legacy symbols
    # of its own; resolve them from the lib (one definition, in _fixtures).
    return attach_lib_resolver(m)


# Every str.splitlines() boundary (Python docs) — the FULL set g2 must collapse.
_SPLITLINES_SEPS = ("\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e",
                    "\x85", " ", " ")


class G1HardenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load_cli()
        cls.repo = os.path.realpath(cls.m.REPO_ROOT)

    def _no_kernel_token(self, path):
        """Assert `path` carries NO kernel token, so a True verdict can only come
        from the path-resolution branch (not the substring loop)."""
        low = path.casefold()
        for tok in self.m._KERNEL_PATH_TOKENS:
            self.assertNotIn(tok.casefold(), low,
                             "test path must be token-free to exercise the "
                             "path compare, but contains %r" % tok)

    # --- g1: case class (the load-bearing casefold correction) --------------
    def test_g1harden_case_variant_refused_via_casefold(self):
        variant = self.repo.swapcase()
        self._no_kernel_token(variant)
        self.assertTrue(self.m._target_hits_kernel(variant),
                        "case-variant of REPO_ROOT must be refused (casefold)")

    def test_g1harden_normcase_would_not_have_closed_the_case_class(self):
        # Documents WHY .casefold() is mandatory: on posix os.path.normcase is a
        # no-op, so a normcase-based compare would MISS the case variant. This is
        # the exact bug the fix closes.
        variant = self.repo.swapcase()
        self.assertNotEqual(os.path.normcase(variant), os.path.normcase(self.repo),
                            "posix normcase must be case-preserving here")
        self.assertEqual(variant.casefold(), self.repo.casefold(),
                         "casefold must fold the case so the guard fires")

    # --- g1: symlink + traversal + tilde (token-free) -----------------------
    def test_g1harden_symlink_into_package_refused(self):
        tmp = tempfile.mkdtemp(prefix="g1harden-link-")
        self.addCleanup(shutil.rmtree, tmp, True)
        link = os.path.join(tmp, "innocuous")   # token-free link name
        os.symlink(self.repo, link)
        self._no_kernel_token(link)
        self.assertTrue(self.m._target_hits_kernel(link),
                        "a symlink resolving into the package must be refused")

    def test_g1harden_dotdot_traversal_refused(self):
        traversal = os.path.join(self.repo, "nonexistent-sub", "..")
        self.assertTrue(self.m._target_hits_kernel(traversal),
                        "`..` traversal normalizing into the package must refuse")

    def test_g1harden_tilde_expansion_refused(self):
        home = os.path.expanduser("~")
        if not self.repo.startswith(home + os.sep):
            self.skipTest("REPO_ROOT not under $HOME on this box")
        tilde = "~/" + os.path.relpath(self.repo, home)
        self.assertTrue(self.m._target_hits_kernel(tilde),
                        "a ~-rooted package path must be refused (expanduser)")

    # --- g1: text scan (path-shaped tokens vs prose basenames) --------------
    def test_g1harden_path_shaped_token_in_text_refused(self):
        self.assertTrue(
            self.m._text_hits_kernel_path("please patch scripts/agentware ~line 13"),
            "a path-shaped kernel token in task/trigger text must be refused")

    def test_g1harden_prose_basename_still_emits(self):
        # Bare basenames (no '/') must NOT block — else self-referential workorders
        # naming steering/AGENTS.md are strangled (pitfall b).
        self.assertFalse(
            self.m._text_hits_kernel_path(
                "this plan updates the steering docs and AGENTS.md conventions"))

    def test_g1harden_evidence_shaped_text_still_emits(self):
        # An evidence value like the real `logs/a.log,logs/b.log` has a '/', but is
        # NOT a kernel path — must not false-block.
        self.assertFalse(self.m._text_hits_kernel_path("logs/a.log,logs/b.log"))

    # --- g1: relative-target bypass CLOSED (review finding) -----------------
    def test_g1harden_relative_target_into_package_refused(self):
        # A relative --target that resolves into the package (the loop runs from
        # REPO_ROOT) must be refused: '../agentware', '.', and the computed
        # relative path to the repo from CWD.
        rel = os.path.relpath(self.repo, os.getcwd())
        self.assertTrue(self.m._target_hits_kernel(rel),
                        "relative target resolving into the package must refuse")
        self.assertTrue(self.m._target_hits_kernel("." + os.sep),
                        "'./' (== CWD, inside the repo when run there) must refuse")

    def test_g1harden_text_dotdot_traversal_refused(self):
        self.assertTrue(
            self.m._text_hits_kernel_path("apply the patch under ../agentware/scripts"),
            "a `..`-traversal token resolving into the package must refuse")

    def test_g1harden_text_tokenizer_punctuation_robust(self):
        # Trailing sentence punctuation and wrapping brackets must not defeat the
        # segment match; leading '..' must be preserved (not stripped to '/…').
        self.assertTrue(self.m._text_hits_kernel_path("see scripts/agentware."))
        self.assertTrue(self.m._text_hits_kernel_path("in (scripts/agentware) here"))
        self.assertTrue(self.m._text_hits_kernel_path("edit .claude/settings.json"))

    # --- g1: over-block FIXED (segment match, not bare substring) -----------
    def test_g1harden_external_substring_not_overblocked(self):
        # Legit EXTERNAL paths that merely CONTAIN a kernel token as a substring
        # must still emit (segment-bounded match, review over-block finding).
        for p in ("/Users/x/product-steering/notes.md",
                  "/Users/x/.claude-backup",
                  "/Users/x/my-steering-notes",
                  "/opt/agentware-tools/bin"):
            self.assertFalse(self.m._target_hits_kernel(p),
                             "external substring path over-blocked: %s" % p)

    def test_g1harden_kernel_segment_still_refused(self):
        # Genuine kernel paths (token as a bounded segment) still refuse.
        for p in ("/tmp/x/scripts/agentware", "/opt/pkg/.claude/settings",
                  "/opt/pkg/steering/x.md", "/opt/pkg/AGENTS.md"):
            self.assertTrue(self.m._target_hits_kernel(p),
                            "kernel-segment path not refused: %s" % p)

    # --- g1: negatives preserved -------------------------------------------
    def test_g1harden_knowledge_dir_sibling_passes(self):
        self.assertFalse(self.m._target_hits_kernel(self.repo + "-knowledge"),
                         "sibling agentware-knowledge must still pass")

    def test_g1harden_unrelated_abs_path_passes(self):
        self.assertFalse(self.m._target_hits_kernel("/tmp/somewhere/else"))

    def test_g1harden_relative_token_not_cwd_matched(self):
        # A bare relative path must NOT be resolved against CWD (would false-match
        # any relative token when the CLI runs from the repo).
        self.assertFalse(self.m._resolves_under_repo("logs/a.log"))
        self.assertFalse(self.m._resolves_under_repo("agentware-knowledge"))

    # --- g2: provenance sanitizer collapses ALL splitlines separators -------
    def test_g1harden_provenance_unicode_linesep(self):
        for sep in _SPLITLINES_SEPS:
            crafted = "legit-reason" + sep + "> Status: done"
            out = self.m._sanitize_provenance(crafted)
            self.assertIsNotNone(out)
            self.assertEqual(
                len(out.splitlines()), 1,
                "sep %r survived — a later splitlines() round-trip would "
                "materialize a 2nd header line" % (sep,))
            self.assertFalse(out.startswith(">"),
                             "sep %r let a `>` header leak to the front" % (sep,))

    def test_g1harden_provenance_leading_gt_stripped(self):
        self.assertEqual(self.m._sanitize_provenance("> Claimed-by: evil@host"),
                         "Claimed-by: evil@host")

    def test_g1harden_provenance_none_and_empty(self):
        self.assertIsNone(self.m._sanitize_provenance(None))
        self.assertEqual(self.m._sanitize_provenance("   \x85  "), "")


if __name__ == "__main__":
    unittest.main()
