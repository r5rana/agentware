"""agentware_lib.knowledge.relatecmd — the `relate` DECLARE verb (layer: knowledge).

Owns: the declared-edge WRITE path in full — `_declare_relation` (the guarded,
locked, pre-flighted `.md` + index.json edge write) and its four extracted stages
(_relate_resolve_subject / _relate_read_subject / _relate_write_md /
_relate_confirm), plus the `relate` command handler and registrar (cmd_relate /
register_relate).
Does NOT own: the `relates` token vocabulary or the byte-preserving frontmatter
line upsert (agentware_lib.core.frontmatter: RELATION_TYPES,
_fm_upsert_relates_line, split_frontmatter, _entry_list_field); the index lock,
loader and sole writer (agentware_lib.knowledge.index: _index_lock / load_index /
save_index); the READ side of the graph those edges form
(agentware_lib.knowledge.graph — build_adjacency, graph_traverse, the `query`
verb); or the read-time trust surface that CONSUMES a `supersedes` edge
(agentware_lib.retrieval).
Layer deps: knowledge -> core (config, frontmatter) + knowledge (index); nothing
above. Every refusal is fail-CLOSED with ZERO writes, and the whole
load -> mutate(.md + index row) -> save runs inside ONE `_index_lock` critical
section, so no concurrent writer can interleave a lost update.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

WHY THIS IS A PORT, NOT A REWRITE (feature 260813 task 38)
----------------------------------------------------------
`relate` is LOCAL-ONLY: the donor tree has no equivalent, so there is no surface
to union and nothing to adjudicate — the whole capability is at risk of being
lost, not diluted. The behaviour therefore lands VERBATIM from the monolith; the
only change is decomposition. The monolith's `_declare_relation` was a single
~150-line, 30-branch body, which is over BOTH per-function ceilings (120 lines /
12 branches) that `tests/_ceiling_gate.py` enforces with no debt row available
for a new location. It is split into the four stages it always had — resolve,
read, write-the-md, confirm — with the orchestration left in `_declare_relation`
so its NAME, signature and every returned key are unchanged for `test_relate` and
for `tests/unit/features/test_schemaguard.py`'s index-writer map.

THE SCHEMA GUARD FOLLOWS THE HANDLER
------------------------------------
`relate` is the FIRST ported verb that is a KB WRITER, so this is the port that
first pays `agentware_lib.features.migratecmd._KB_WRITER_TARGETS`: `cmd_relate`
is named there in the SAME commit that appends `relate` to
`strangler.PORTED_VERBS`. Without that row the handler dispatches past the
older-CLI refusal entirely — it would parse, run and write against a KB whose
`.schema` is ahead of this package, which is exactly the corruption the interlock
exists to prevent and which nothing else in the tree would notice.
"""

import json
import os
import sys

import agentware_lib.core.config as config
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.knowledge.index as index


def _relate_resolve_subject(kdir, data, subject_id, target_id, relation):
    """Resolve + CONTAIN the subject row. Returns (subject, abs_path, error).

    Both ids must resolve to real index entries (a dangling `supersedes:ghost` is
    refused here, before any file is opened), and the subject's `path` must stay
    strictly inside the knowledge dir.

    SECURITY (containment): the entry `path` comes from index.json, an explicitly-
    defended poisoned-input surface. A traversing (`..`) or absolute path would let
    `os.path.join` escape the KB and rewrite an arbitrary file — including a kernel
    `.claude/**` governance file that also opens with a `---` frontmatter block.
    Symlinks are resolved and anything not strictly inside the KB is REFUSED, so
    the check is fail-CLOSED for a category dir symlinked outside the tree too.
    """
    by_id = {e.get("id"): e for e in (data.get("entries") or []) if e.get("id")}
    subject = by_id.get(subject_id)
    if subject is None:
        return None, None, ("subject id does not resolve to an index entry: %s"
                            % subject_id)
    if target_id not in by_id:
        return None, None, ("target id does not resolve to an index entry "
                            "(dangling %s:%s)" % (relation, target_id))
    rel_path = subject.get("path")
    if not rel_path:
        return None, None, "subject entry has no file path: %s" % subject_id
    kroot = os.path.realpath(kdir)
    abs_path = os.path.realpath(os.path.join(kdir, rel_path))
    if os.path.isabs(rel_path) or not (
            abs_path == kroot or abs_path.startswith(kroot + os.sep)):
        return None, None, ("refusing: subject entry path escapes the knowledge "
                            "dir (%r) — relate only ever touches KB entry files"
                            % rel_path)
    return subject, abs_path, None


def _relate_read_subject(abs_path, rel_path, subject_id):
    """Read + parse the subject `.md`. Returns (text, fields, body, error).

    Read as BYTES: a text-mode read universal-newline-translates CR/CRLF to LF, so
    the pre-flight round-trip (which compares already-normalized text) could not
    detect a line-ending change and would silently LF-normalize a CRLF/bare-CR
    entry on write. The KB is LF-native (every toolkit writer emits '\\n'); refuse
    a non-LF entry with ZERO writes rather than restructure it — preserving the
    byte-preservation invariant the pre-flight promises.

    A REAL frontmatter block naming THIS entry is required. A bare `fields is not
    None` is insufficient: `split_frontmatter` returns an EMPTY dict for a file
    that merely opens with `---` and has a stray `---` rule in its body (no
    recognized fields) — an upsert there would land in the document BODY. Pinning
    `id == subject_id` also catches an index/file divergence (a row pointing at the
    wrong or overwritten file).
    """
    try:
        with open(abs_path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        return None, None, None, ("cannot read subject entry file %s: %s"
                                  % (rel_path, exc))
    if b"\r" in raw:
        return None, None, None, ("refusing: subject entry has non-LF (CR/CRLF) "
                                  "line endings — normalize it first: %s"
                                  % rel_path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, None, None, ("cannot decode subject entry file %s: %s"
                                  % (rel_path, exc))
    fields, body = frontmatter.split_frontmatter(text)
    if not fields or fields.get("id") != subject_id:
        return None, None, None, ("subject entry file carries no frontmatter block "
                                  "naming %s (run 'index migrate-frontmatter' "
                                  "first): %s" % (subject_id, rel_path))
    return text, fields, body, None


def _relate_write_md(abs_path, rel_path, text, fields, body, new_tokens):
    """PRE-FLIGHT then atomically rewrite the subject `.md`. Returns an error or None.

    The rewrite must re-parse to EXACTLY `new_tokens` with every OTHER field AND
    the body byte-preserved. If it does not (e.g. duplicate/ambiguous `relates:`
    lines that `_fm_upsert_relates_line` and `split_frontmatter` resolve
    differently), REFUSE with ZERO writes rather than commit a corrupting partial
    write that a post-hoc read-after-write would only detect AFTER the damage.
    """
    new_text = frontmatter._fm_upsert_relates_line(text, new_tokens)
    rt_fields, rt_body = frontmatter.split_frontmatter(new_text)
    if (not rt_fields
            or list(rt_fields.get("relates") or []) != new_tokens
            or rt_body != body
            or {k: v for k, v in rt_fields.items() if k != "relates"}
            != {k: v for k, v in fields.items() if k != "relates"}):
        return ("refusing: subject frontmatter does not round-trip cleanly "
                "(ambiguous/duplicate 'relates'?) — fix it with 'index "
                "migrate-frontmatter': %s" % rel_path)
    tmp = abs_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new_text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, abs_path)  # atomic .md write
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return "cannot write subject entry file %s: %s" % (rel_path, exc)
    return None


def _relate_confirm(kdir, abs_path, subject_id, token):
    """Read-after-write (R-VERIFY-04): re-load the derived index AND re-read the
    file; the edge must be present in BOTH. Returns (ok_index, ok_file)."""
    rb_data, rb_err = index.load_index(kdir)
    ok_index = False
    if rb_err is None:
        rb = {e.get("id"): e for e in (rb_data.get("entries") or [])}
        ok_index = token in frontmatter._entry_list_field(
            rb.get(subject_id) or {}, "relates")
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            confirm_fields, _ = frontmatter.split_frontmatter(f.read())
        ok_file = token in list((confirm_fields or {}).get("relates") or [])
    except OSError:
        ok_file = False
    return ok_index, ok_file


def _declare_relation(kdir, subject_id, relation, target_id):
    """Declare ONE closed-vocab `relates` edge (`<relation>:<target_id>`) on the
    EXISTING entry `subject_id`, routed through the sole index writer.

    OPERATOR/agent-invoked (like `learn`/`decide`) — NEVER autonomous self-mutation
    and NEVER LLM-inferred: a human/agent ASSERTS the edge, which is exactly the
    declared-only moat the read-time trust surface (`trust_signals`) consumes. The
    whole load -> mutate(.md + index row) -> save runs under the re-entrant
    `_index_lock` (B2 lost-update guard). The entry's `.md` frontmatter is the
    source of truth (the edge is upserted there, byte-preserving every other field
    via `_fm_upsert_relates_line`); the derived `index.json` row is reconciled in
    the SAME critical section through `save_index` (the sanctioned atomic writer,
    NEVER a raw hand-edit, R-KB-01), so the two can never drift and a later
    `index rebuild` is a no-op. Mirrors `cmd_index_add`'s targeted lock+write
    pattern, so every OTHER index row stays byte-identical (INV-2 by construction) —
    no full rebuild, no surprise churn on rows the caller never touched.

    Fail-fast with ZERO writes on: an out-of-vocab relation type; an unresolvable
    subject or target id (a dangling `supersedes:ghost`); a self-edge; a subject
    entry whose index `path` escapes the knowledge dir; non-LF (CR/CRLF) line
    endings; a file that carries no real frontmatter block naming the subject; or
    frontmatter that does not round-trip cleanly. Each refusal is owned by one of
    the four stages above; the write is PRE-FLIGHTED before it is committed, so a
    refusal never leaves a partial write. The index row is reconciled to the
    frontmatter edge set (exactly what `index rebuild` derives), so an index-only
    phantom edge from index-ahead drift is dropped identically to a rebuild.

    Idempotent: when the edge is already in BOTH the frontmatter AND the index row,
    nothing is written (`changed=False, reconciled=False`, byte-identical). When the
    edge is in the frontmatter but the derived index row lagged it (a pre-existing
    drift), the index is reconciled — reported as `reconciled=True`, never a silent
    index mutation behind a "no-op". A structurally-malformed pre-existing token
    (no colon — already inert to every reader) is normalized away by the rewrite.
    `last_verified` is deliberately left untouched — the edge itself IS the
    assertion. Returns `(result_dict, None)` on success or `(None, error)` on any
    refusal.
    """
    if relation not in frontmatter.RELATION_TYPES:
        return None, ("unknown relation type %r (closed vocab: %s)"
                      % (relation, ", ".join(frontmatter.RELATION_TYPES)))
    if subject_id == target_id:
        return None, ("self-edge refused: %s cannot '%s' itself"
                      % (subject_id, relation))
    with index._index_lock(kdir):  # serialize load->mutate(.md+index)->save (B2)
        data, err = index.load_index(kdir)
        if err is not None:
            return None, err
        subject, abs_path, err = _relate_resolve_subject(
            kdir, data, subject_id, target_id, relation)
        if err is not None:
            return None, err
        rel_path = subject.get("path")
        text, fields, body, err = _relate_read_subject(
            abs_path, rel_path, subject_id)
        if err is not None:
            return None, err
        # The `.md` frontmatter is the source of truth for the edge set.
        existing = list(fields.get("relates") or [])
        token = "%s:%s" % (relation, target_id)
        idx_has = token in frontmatter._entry_list_field(subject, "relates")
        if token in existing and idx_has:
            # Truly idempotent: the edge is already in BOTH the source-of-truth
            # frontmatter AND the derived index row -> ZERO writes, byte-identical.
            return ({"subject": subject_id, "relation": relation,
                     "target": target_id, "changed": False, "reconciled": False,
                     "relates": existing}, None)
        wrote_md = False
        if token not in existing:
            new_tokens = existing + [token]
            err = _relate_write_md(abs_path, rel_path, text, fields, body,
                                   new_tokens)
            if err is not None:
                return None, err
            wrote_md = True
            existing = new_tokens
        return _relate_commit_index(kdir, data, subject, subject_id, relation,
                                    target_id, abs_path, rel_path, existing,
                                    token, wrote_md)


def _relate_commit_index(kdir, data, subject, subject_id, relation, target_id,
                         abs_path, rel_path, existing, token, wrote_md):
    """Reconcile the derived index row to the frontmatter edge set, then confirm.

    `existing` (the frontmatter tokens) is authoritative — exactly what `index
    rebuild` derives — so an index-ONLY phantom edge from index-ahead drift is
    dropped identically to a rebuild. `save_index` (the R-KB-01 sanctioned writer)
    is wrapped so an index-write failure AFTER the atomic `.md` write is reported
    (md-ahead, self-heals on `index rebuild`) rather than escaping as a bare
    traceback. `reconciled` is True ONLY for a genuine drift-heal (the index lagged
    the frontmatter WITHOUT us just writing it) — a fresh declare's expected index
    update is `changed`, not `reconciled`. Called INSIDE the caller's `_index_lock`
    critical section (the lock is re-entrant), never on its own.
    """
    index_changed = frontmatter._entry_list_field(subject, "relates") != existing
    reconciled = False
    if index_changed:
        subject["relates"] = existing
        try:
            index.save_index(kdir, data)
        except OSError as exc:
            return None, ("subject frontmatter updated but the index reconcile "
                          "failed (%s) — run 'index rebuild': %s"
                          % (exc, rel_path))
        reconciled = not wrote_md
    if wrote_md or index_changed:
        ok_index, ok_file = _relate_confirm(kdir, abs_path, subject_id, token)
        if not (ok_index and ok_file):
            return None, ("read-after-write verification failed for %s "
                          "(index=%s file=%s)" % (token, ok_index, ok_file))
    return ({"subject": subject_id, "relation": relation, "target": target_id,
             "changed": wrote_md, "reconciled": reconciled,
             "relates": existing}, None)


def cmd_relate(args):
    """`relate <subject> --supersedes <target>` (and the other closed-vocab edges):
    declare ONE `relates` graph edge on an EXISTING entry in a single call, routed
    through the index sole-writer.

    The low-friction, human/agent-driven DECLARE step that lets a trust decision
    migrate from the computed staleness heuristic toward declared structure — a
    `supersedes` edge is consumed immediately by the read-time trust surface, so
    recall reports the target `trust:superseded` and downweights it under bm25+acr
    (plain bm25 byte-identical; no ranking code touched). NEVER auto-declares."""
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    # Exactly one closed-vocab relation flag is set (argparse: mutually-exclusive,
    # required). Map it to (relation_type, target_id).
    flag_map = (("supersedes", getattr(args, "supersedes", None)),
                ("depends-on", getattr(args, "depends_on", None)),
                ("blocks", getattr(args, "blocks", None)),
                ("relates-to", getattr(args, "relates_to", None)))
    relation, target = next(((r, t) for r, t in flag_map if t), (None, None))
    if not relation:
        sys.stderr.write("agentware: relate requires one of --supersedes / "
                         "--depends-on / --blocks / --relates-to\n")
        return 2
    result, err = _declare_relation(kdir, args.subject, relation, target)
    if err is not None:
        sys.stderr.write("agentware: relate: %s\n" % err)
        return 1
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": True, **result}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        if result["changed"]:
            verb = "declared"
        elif result.get("reconciled"):
            verb = "reconciled index (edge already in frontmatter)"
        else:
            verb = "already declared (no-op)"
        print("RELATE %s: %s %s %s  relates=%s"
              % (verb, result["subject"], result["relation"], result["target"],
                 result["relates"]))
    return 0


def register_relate(sub, fmt_parent):
    # relate -----------------------------------------------------------------
    # Declare a closed-vocab graph edge on an EXISTING entry in ONE call, routed
    # through the index sole-writer (never a hand-edit). Human/agent-asserted —
    # NEVER auto-declared — so the declared-only trust moat holds.
    p_relate = sub.add_parser(
        "relate", parents=[fmt_parent],
        help="declare a graph edge (e.g. --supersedes) on an EXISTING entry, "
             "routed through the index writer (human/agent-asserted, never auto)")
    p_relate.add_argument("subject", help="id of the entry that GAINS the edge")
    rel_grp = p_relate.add_mutually_exclusive_group(required=True)
    rel_grp.add_argument("--supersedes", metavar="TARGET_ID",
                         help="declare SUBJECT supersedes TARGET — the high-"
                              "precision declared trust signal recall consumes")
    rel_grp.add_argument("--depends-on", dest="depends_on", metavar="TARGET_ID",
                         help="declare SUBJECT depends-on TARGET")
    rel_grp.add_argument("--blocks", metavar="TARGET_ID",
                         help="declare SUBJECT blocks TARGET")
    rel_grp.add_argument("--relates-to", dest="relates_to", metavar="TARGET_ID",
                         help="declare SUBJECT relates-to TARGET")
    p_relate.set_defaults(func=cmd_relate)


# Explicit facade export list (public AND underscore-private): the entrypoint binds
# exactly these names, so `CLI.<name>` — the suite's oracle and the preserved-symbol
# freeze — still resolves for every symbol the monolith used to define here.
_EXPORTS = [
    "_declare_relation",
    "cmd_relate",
]

__all__ = list(_EXPORTS) + ["register_relate"]
