"""Tests for the `relate` declare verb (feature 260717-p3-followup-declare-habitual,
Task 1).

Stdlib `unittest` only, synthetic temp KB. Asserts that `relate <subject>
--supersedes <target>` declares a `supersedes` edge on an EXISTING entry in ONE
call, routed through the index sole-writer:

  - The subject `.md` frontmatter gains `relates: [supersedes:<target>]` AND the
    derived index.json subject row gains the same token.
  - Re-declaring the same edge is a BYTE-IDENTICAL no-op (idempotent): neither the
    `.md` nor index.json changes on the second call.
  - A self-edge (X supersedes X) and a dangling target (supersedes:ghost) are
    REFUSED (nonzero exit, index.json byte-identical, nothing written).
  - An out-of-vocab relation type is refused at the helper level.
  - After the declare, `trust_signals` reports the target `trust:superseded`
    with `superseded_by:[subject]` (the read-time trust surface the payoff loop
    consumes).
  - EVERY other entry's `.md` bytes AND index row are byte-unchanged (INV-2).
"""

import json
import os

try:
    from tests._fixtures import (SyntheticKBTestCase, build_synthetic_kb,
                                 load_cli, load_lib)
except ImportError:  # allow `python3 -m unittest tests.unit.knowledge.test_relate`
    from _fixtures import (SyntheticKBTestCase, build_synthetic_kb, load_cli,
                           load_lib)

CLI = load_cli()
# `relate` is PORTED (feature 260813 task 38), so the oracle for its own helper is
# the OWNING module, not the facade: a ported function reads its own module
# globals, so patching `CLI.save_index` would rebind a name nothing consults and
# the write-failure probe below would pass while proving nothing.
REL = load_lib("agentware_lib.knowledge.relatecmd")
IDX = load_lib("agentware_lib.knowledge.index")


def _relate_node():
    """The assembled `relate` subparser, reached the way the CLI reaches it.

    Built through `assemble_parser` (the frozen-registration walk) rather than by
    calling `register_relate` directly, so this pins the node the CLI actually
    serves — a verb dropped from the registration tuple fails here too.
    """
    parser = load_lib("agentware_lib.cli").assemble_parser()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and "relate" in choices:
            return choices["relate"]
    raise AssertionError("`relate` is not registered on the assembled parser")

# Pinned so freshness never depends on the wall clock (INV-1). All entries are
# FRESH at this as_of, so the ONLY thing that flips trust is the declared edge.
AS_OF = "2026-06-25"

_BODY = ("The cache invalidation race served a stale value because the "
         "invalidation hook fired before the write committed.\n")


def _entry(eid, name, category="learnings", **extra):
    e = {
        "id": eid, "title": name, "category": category,
        "path": "%s/%s.md" % (category, name), "tags": ["cache", "race"],
        "created": AS_OF, "last_verified": AS_OF, "author": "testuser",
        "summary": "cache invalidation race note",
        "body": "# %s\n\n%s" % (name, _BODY),
    }
    e.update(extra)
    return e


# X (to-be-superseded) + Y (newer) + two untouched bystanders (INV-2 witnesses).
_ENTRIES = [
    _entry("learn-cache-old", "old"),
    _entry("learn-cache-new", "new"),
    _entry("learn-cache-bystander", "bystander"),
    _entry("ref-untouched", "untouched", category="references"),
]

SUBJECT = "learn-cache-new"
TARGET = "learn-cache-old"
TOKEN = "supersedes:learn-cache-old"


class RelateTest(SyntheticKBTestCase):

    def setUp(self):
        super().setUp()
        self.index_data = build_synthetic_kb(self.kdir, entries=_ENTRIES)

    # --- helpers -------------------------------------------------------------
    def _index_bytes(self):
        with open(os.path.join(self.kdir, "index.json"), "rb") as f:
            return f.read()

    def _rows_by_id(self):
        return {e["id"]: e for e in self.read_index()["entries"]}

    def _entry_path(self, eid):
        return os.path.join(self.kdir, "%s/%s.md" % (
            _ENTRIES_BY_ID[eid]["category"], _ENTRIES_BY_ID[eid]["title"]))

    def _md_bytes(self, eid):
        with open(self._entry_path(eid), "rb") as f:
            return f.read()

    def _relate(self, subject=SUBJECT, target=TARGET, flag="--supersedes"):
        return self.run_cli(["relate", subject, flag, target, "--format", "json"])

    # --- happy path ----------------------------------------------------------
    def test_declare_supersedes_writes_md_and_index(self):
        code, out, err = self._relate()
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["changed"])
        self.assertFalse(payload["reconciled"],
                         "a fresh declare is 'changed', not a drift-heal 'reconciled'")
        self.assertEqual(payload["relates"], [TOKEN])
        # subject .md frontmatter carries the edge
        with open(self._entry_path(SUBJECT), encoding="utf-8") as f:
            md = f.read()
        fields, _ = CLI.split_frontmatter(md)
        self.assertEqual(fields.get("relates"), [TOKEN])
        # derived index.json subject row carries the edge
        self.assertEqual(self._rows_by_id()[SUBJECT].get("relates"), [TOKEN])

    def test_trust_surface_reports_superseded_after_declare(self):
        self.assertEqual(self._relate()[0], 0)
        data = self.read_index()
        sig = CLI.trust_signals(data, AS_OF)
        self.assertEqual(sig[TARGET]["trust"], "superseded")
        self.assertEqual(sig[TARGET]["superseded_by"], [SUBJECT])
        # the superseder itself stays current — direction is not inverted
        self.assertEqual(sig[SUBJECT]["trust"], "current")

    # --- idempotency (byte-identical no-op) ----------------------------------
    def test_redeclare_is_byte_identical_noop(self):
        self.assertEqual(self._relate()[0], 0)
        md_after1 = self._md_bytes(SUBJECT)
        idx_after1 = self._index_bytes()
        code, out, err = self._relate()
        self.assertEqual(code, 0, err)
        self.assertFalse(json.loads(out)["changed"])
        self.assertEqual(self._md_bytes(SUBJECT), md_after1,
                         "re-declare must not rewrite the subject .md")
        self.assertEqual(self._index_bytes(), idx_after1,
                         "re-declare must not rewrite index.json")

    # --- refusals (nonzero, index byte-identical, nothing written) -----------
    def test_self_edge_refused(self):
        before = self._index_bytes()
        code, out, err = self._relate(subject=TARGET, target=TARGET)
        self.assertNotEqual(code, 0)
        self.assertIn("self-edge", err)
        self.assertEqual(self._index_bytes(), before)

    def test_dangling_target_refused(self):
        before = self._index_bytes()
        code, out, err = self._relate(target="learn-ghost-does-not-exist")
        self.assertNotEqual(code, 0)
        self.assertIn("does not resolve", err)
        self.assertEqual(self._index_bytes(), before)

    def test_dangling_subject_refused(self):
        before = self._index_bytes()
        code, out, err = self._relate(subject="learn-ghost-subject")
        self.assertNotEqual(code, 0)
        self.assertEqual(self._index_bytes(), before)

    def test_out_of_vocab_relation_refused_at_helper(self):
        # The CLI flags are closed-vocab by construction, so exercise the helper's
        # defense-in-depth vocab guard directly.
        result, err = REL._declare_relation(
            self.kdir, SUBJECT, "bogus-relation", TARGET)
        self.assertIsNone(result)
        self.assertIn("unknown relation type", err)

    def test_relation_group_stays_required(self):
        # PROMOTION-GATE BLIND SPOT (feature 260813 task 38). The surface capture
        # records per-ACTION `required` (tests/_surface_capture.ACTION_FIELDS), but
        # argparse keeps a mutually-exclusive group's requiredness on the GROUP, not
        # on its member actions — every member reports required=False either way. So
        # flipping `required=True` -> `required=False` on this group is INVISIBLE to
        # the promotion gate: mutation-proven silent at the port. Deleting a member
        # flag or changing the positional's arity both redden it correctly; only the
        # group's own requiredness slips through. Locked here so the ported verb
        # cannot lose it. `query` carries the only other required group in the whole
        # 44-verb surface and is still legacy-served; task 54 promotes it and owns
        # closing this generically.
        import argparse
        node = _relate_node()
        groups = [g for g in node._mutually_exclusive_groups if g.required]
        self.assertEqual(
            len(groups), 1,
            "relate must keep exactly ONE required mutually-exclusive relation "
            "group; the promotion gate cannot see this")
        self.assertEqual(
            sorted(a.dest for a in groups[0]._group_actions),
            ["blocks", "depends_on", "relates_to", "supersedes"])
        # ...and the closed vocab is the whole vocab: no relation flag may live
        # OUTSIDE the required group, where it would bypass the mutex entirely.
        self.assertIsInstance(groups[0], argparse._MutuallyExclusiveGroup)

    def test_missing_relation_refused_even_if_parser_guard_is_lost(self):
        # Defense in depth, and the reason the blind spot above is a coverage gap
        # rather than a live defect: the HANDLER refuses a subject with no relation
        # regardless of what the parser enforced.
        import contextlib
        import io
        import types
        args = types.SimpleNamespace(subject=SUBJECT, supersedes=None,
                                     depends_on=None, blocks=None,
                                     relates_to=None, format="text")
        prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        err = io.StringIO()
        try:
            before = self._index_bytes()
            with contextlib.redirect_stderr(err):
                code = REL.cmd_relate(args)
        finally:
            if prev is None:
                os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
            else:
                os.environ["AGENTWARE_KNOWLEDGE_DIR"] = prev
        self.assertNotEqual(code, 0)
        self.assertEqual(self._index_bytes(), before)
        self.assertIn("requires one of", err.getvalue())

    # --- hardening (adversarial-review findings) -----------------------------
    def _write_subject_md(self, frontmatter_lines):
        with open(self._entry_path(SUBJECT), "w", encoding="utf-8") as f:
            f.write("---\n" + "\n".join(frontmatter_lines) + "\n---\n\n# new\n\n"
                    "cache invalidation race body\n")

    def test_duplicate_relates_lines_refused_zero_writes(self):
        # F1: two `relates:` lines — _fm_upsert (first-wins) and split_frontmatter
        # (last-wins) disagree, so the rewrite would not round-trip. The pre-flight
        # must REFUSE with zero writes rather than commit a corrupting partial write.
        self._write_subject_md([
            "id: %s" % SUBJECT, "title: new", "category: learnings",
            "tags: [cache, race]", "created: %s" % AS_OF, "author: testuser",
            "summary: note", "relates: [depends-on:learn-cache-bystander]",
            "relates: [blocks:ref-untouched]"])
        md_before, idx_before = self._md_bytes(SUBJECT), self._index_bytes()
        code, out, err = self._relate()
        self.assertNotEqual(code, 0)
        self.assertIn("round-trip", err)
        self.assertEqual(self._md_bytes(SUBJECT), md_before, "partial .md write")
        self.assertEqual(self._index_bytes(), idx_before, "partial index write")

    def test_no_real_frontmatter_refused_zero_writes(self):
        # F3: a file opening with `---` and a stray `---` rule but no real fields
        # yields split_frontmatter=={} (non-None); must be refused (else the edge
        # would be injected into the document BODY).
        with open(self._entry_path(SUBJECT), "w", encoding="utf-8") as f:
            f.write("---\nThis is a plain document opening with a rule.\n\n"
                    "some prose here\n\n---\n\nAfter the horizontal rule.\n")
        md_before, idx_before = self._md_bytes(SUBJECT), self._index_bytes()
        code, out, err = self._relate()
        self.assertNotEqual(code, 0)
        self.assertIn("frontmatter", err)
        self.assertEqual(self._md_bytes(SUBJECT), md_before)
        self.assertEqual(self._index_bytes(), idx_before)

    def test_id_mismatch_refused(self):
        # F3 sibling: real frontmatter but the id does not name this entry (an
        # index/file divergence — a row pointing at the wrong/overwritten file).
        self._write_subject_md([
            "id: some-other-entry", "title: x", "category: learnings",
            "tags: [x]", "created: %s" % AS_OF, "author: t", "summary: s"])
        md_before, idx_before = self._md_bytes(SUBJECT), self._index_bytes()
        code, out, err = self._relate()
        self.assertNotEqual(code, 0)
        self.assertEqual(self._md_bytes(SUBJECT), md_before)
        self.assertEqual(self._index_bytes(), idx_before)

    def test_path_traversal_refused_victim_untouched(self):
        # F6 [HIGH]: a poisoned index `path` that escapes the KB must be refused
        # BEFORE any read/write, so relate can never rewrite a file outside the KB
        # (e.g. a kernel .claude/** governance file).
        victim = os.path.join(os.path.dirname(self.kdir),
                              "RELATE-VICTIM-%s.md" % os.path.basename(self.kdir))
        with open(victim, "w", encoding="utf-8") as f:
            f.write("---\nid: evil-subject\ntitle: v\ncategory: learnings\n"
                    "tags: [x]\ncreated: %s\nauthor: t\nsummary: s\n---\n\nbody\n"
                    % AS_OF)
        self.addCleanup(lambda: os.path.exists(victim) and os.remove(victim))
        with open(victim, "rb") as f:
            victim_before = f.read()
        for rel in ("../%s" % os.path.basename(victim), victim):  # traversal + absolute
            data = self.read_index()
            data["entries"] = [e for e in data["entries"]
                               if e["id"] != "evil-subject"]
            data["entries"].append({
                "id": "evil-subject", "title": "v", "category": "learnings",
                "path": rel, "tags": ["x"], "created": AS_OF,
                "last_verified": AS_OF, "summary": "s"})
            with open(os.path.join(self.kdir, "index.json"), "w",
                      encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            code, out, err = self.run_cli(
                ["relate", "evil-subject", "--supersedes", TARGET,
                 "--format", "json"])
            self.assertNotEqual(code, 0, "path %r must be refused" % rel)
            self.assertIn("escapes the knowledge dir", err)
            with open(victim, "rb") as f:
                self.assertEqual(f.read(), victim_before,
                                 "victim OUTSIDE the KB was mutated (%r)" % rel)

    def test_divergence_heal_reconciles_not_silent(self):
        # F6b/F7: the edge is in the .md frontmatter but the index row lags it. The
        # re-declare must RECONCILE the index (reported, not a silent no-op) while
        # leaving the .md byte-identical and every other row unchanged.
        self._write_subject_md([
            "id: %s" % SUBJECT, "title: new", "category: learnings",
            "tags: [cache, race]", "created: %s" % AS_OF, "author: testuser",
            "summary: note", "relates: [%s]" % TOKEN])
        rows_before = self._rows_by_id()
        self.assertNotIn("relates", rows_before[SUBJECT])   # index lags the .md
        md_before = self._md_bytes(SUBJECT)
        code, out, err = self._relate()
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertFalse(payload["changed"])                # .md not rewritten
        self.assertTrue(payload["reconciled"])              # index healed, reported
        self.assertEqual(self._md_bytes(SUBJECT), md_before, ".md must be untouched")
        self.assertEqual(self._rows_by_id()[SUBJECT].get("relates"), [TOKEN])
        for eid in _ENTRIES_BY_ID:
            if eid != SUBJECT:
                self.assertEqual(self._rows_by_id()[eid], rows_before[eid])

    def test_crlf_line_endings_refused_zero_writes(self):
        # re-review #1: a non-LF entry would be silently LF-normalized by a
        # text-mode read+rewrite; refuse it (zero writes) to keep byte-preservation.
        p = self._entry_path(SUBJECT)
        with open(p, "rb") as f:
            lf = f.read()
        with open(p, "wb") as f:
            f.write(lf.replace(b"\n", b"\r\n"))
        md_before, idx_before = self._md_bytes(SUBJECT), self._index_bytes()
        code, out, err = self._relate()
        self.assertNotEqual(code, 0)
        self.assertIn("non-LF", err)
        self.assertEqual(self._md_bytes(SUBJECT), md_before)
        self.assertEqual(self._index_bytes(), idx_before)

    def test_index_write_failure_is_reported_not_a_traceback(self):
        # re-review #5: an index-write failure AFTER the atomic .md write must be
        # returned as an error (md-ahead; self-heals on rebuild), never escape raw.
        from unittest import mock
        with mock.patch.object(IDX, "save_index", side_effect=OSError("ENOSPC")):
            result, err = REL._declare_relation(
                self.kdir, SUBJECT, "supersedes", TARGET)
        self.assertIsNone(result)
        self.assertIn("index rebuild", err)

    def test_malformed_pre_existing_token_normalized_away(self):
        # F2 (documented): a structurally-broken (colon-less) pre-existing token —
        # already inert to every reader — is normalized out by the rewrite; the
        # well-formed edge survives and the declared edge is added.
        self._write_subject_md([
            "id: %s" % SUBJECT, "title: new", "category: learnings",
            "tags: [cache, race]", "created: %s" % AS_OF, "author: testuser",
            "summary: note",
            "relates: [depends-on:learn-cache-bystander, garbagenocolon]"])
        code, out, err = self._relate()
        self.assertEqual(code, 0, err)
        with open(self._entry_path(SUBJECT), encoding="utf-8") as f:
            fields, _ = CLI.split_frontmatter(f.read())
        self.assertIn(TOKEN, fields["relates"])
        self.assertIn("depends-on:learn-cache-bystander", fields["relates"])
        self.assertNotIn("garbagenocolon", fields["relates"])

    # --- INV-2: every OTHER entry is byte-unchanged --------------------------
    def test_other_entries_unchanged(self):
        rows_before = self._rows_by_id()
        md_before = {eid: self._md_bytes(eid) for eid in _ENTRIES_BY_ID}
        self.assertEqual(self._relate()[0], 0)
        rows_after = self._rows_by_id()
        for eid in _ENTRIES_BY_ID:
            if eid == SUBJECT:
                continue
            self.assertEqual(rows_after[eid], rows_before[eid],
                             "index row for %s changed (INV-2)" % eid)
            self.assertEqual(self._md_bytes(eid), md_before[eid],
                             ".md for %s changed (INV-2)" % eid)
        # the subject row differs ONLY by the added relates edge
        expected = dict(rows_before[SUBJECT])
        expected["relates"] = [TOKEN]
        self.assertEqual(rows_after[SUBJECT], expected)


_ENTRIES_BY_ID = {e["id"]: e for e in _ENTRIES}


if __name__ == "__main__":
    import unittest
    unittest.main()
