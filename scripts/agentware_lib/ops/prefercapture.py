"""agentware_lib.ops.prefercapture — the NOISE BAR and the capture write (layer: ops).

Owns: the capture WRITE and the entry lifecycle around it — `_prefer_capture`,
entry deletion, the migration gate, the workorder candidate builder, and
`prefer list` / `prefer forget`.

CAPTURE SUPERSEDES, IT DOES NOT APPEND
A contradicting preference REPLACES its predecessor by key rather than sitting
beside it. Two captures that disagree would both inject into the same context
window, and the model has no way to know which the operator meant — so the write
path resolves the conflict at capture time rather than deferring it to every
future session.

Every capture is refused if it names a kernel path (R-CAP-03) or reads as
weakening a governance gate: a preference that could quietly disable a safety
property for every later run is not a taste, and the refusal routes it to a human
instead.

Does NOT own: the admission bar (ops.preferadmit); the queue candidates land in
(ops.preferqueue); classification,
approval and rejection (ops.preferclassify); the gate-loosening refusal
(ops.prefergate); the kernel-path refusal (ops.planauthor.plankernelpath); the
digest and CLI (ops.prefercmd).
Layer deps: ops -> core (config, dates, frontmatter, projectcontext) + knowledge
(the entry writers) + same-layer ops guards.
Fan-in: 3 at landing (regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith — byte-identical, comments and all.
"""

import json
import os
import re

import agentware_lib.core.config as config
from agentware_lib.core.config import require_knowledge_dir, resolve_knowledge_dir
from agentware_lib.core.dates import _now_iso
from agentware_lib.core.frontmatter import _operator_handle
from agentware_lib.core.projectcontext import (
    _canonical_project_key,
    _current_project_signal,
)
from agentware_lib.core.kblayout import SENTINEL_REL
from agentware_lib.knowledge.curation import _slugify
from agentware_lib.knowledge.index import load_index
from agentware_lib.core.frontmatter import _entry_list_field
from agentware_lib.knowledge.index import parse_tags
from agentware_lib.ops.preferadmit import _pref_entry_is_user_source, _pref_is_capturable
from agentware_lib.features.curationcmd import cmd_learn
from agentware_lib.features.indexcmd import cmd_index_remove
from agentware_lib.knowledge.graph import cmd_query
from agentware_lib.ops.prefergate import _pref_loosens_gate
from agentware_lib.ops.preferqueue import (
    PREFER_QUEUE_ARCHIVE_REL,
    PREFER_QUEUE_REL,
    _prefer_archive_tail_records,
    _prefer_queue_records,
)
from agentware_lib.ops.planauthor.plankernelpath import _KERNEL_PATH_TOKENS, _pref_hits_kernel
import agentware_lib.features.migratecmd as _kbmigrate
import argparse
import contextlib
import hashlib
import shutil
import sys


def _prefer_forgettable(kdir, match, eid):
    """May `prefer forget` delete this index entry? (audit MEDIUM)

    A genuine capture is BOTH `preference`-tagged AND `source=user`. Without both
    halves, `forget <any-id>` would remove any KB entry by id and unlink its `.md`
    under a false "reversible" promise, so this is an authorization predicate and
    not a convenience filter.

    ORPHAN TOLERANCE is the one relaxation, and it is bounded: a genuine capture
    whose `.md` is missing or unreadable cannot prove `source=user` from
    frontmatter, so the capture-id prefix (`learn-pref-…`) stands in — the verb
    must stay able to revoke its OWN orphaned capture. The strong check still
    applies whenever the file exists, so a real non-capture never becomes
    forgettable by deleting a file.
    """
    is_pref = "preference" in _entry_list_field(match, "tags")
    is_user = _pref_entry_is_user_source(kdir, match)
    if is_pref and not is_user:
        mpath = match.get("path")
        if not mpath or not os.path.isfile(os.path.join(kdir, mpath)):
            is_user = str(eid).startswith("learn-pref-")
    return is_pref and is_user


def _prefer_archive_md(kdir, path, archive_dir):
    """Copy a capture's `.md` to `archive_dir` BEFORE it is removed and unlinked.

    Best-effort on purpose: the archive is what makes `prefer forget` genuinely
    reversible, but a failure to write it must not block the revocation the
    operator asked for. The caller reports what actually happened by checking for
    the archived file rather than by assuming this succeeded, so the ack stays
    honest either way.
    """
    if not (archive_dir and path):
        return
    try:
        os.makedirs(archive_dir, exist_ok=True)
        shutil.copy2(os.path.join(kdir, path),
                     os.path.join(archive_dir, os.path.basename(path)))
    except OSError:
        pass


def _prefer_delete_entry(kdir, eid, require_preference=False, archive_dir=None):
    """Drop a captured preference: remove it from the index via the guarded
    `cmd_index_remove` AND unlink its orphaned `.md`. Returns
    (removed_entry_or_None, err_or_None); a MISSING id is (None, None) — not an
    error — so the exact-key supersede is a no-op on the first capture.

    `require_preference` (used by `prefer forget`, audit MEDIUM): REFUSE to delete
    an entry that is not a GENUINE capture — it MUST be `preference`-tagged AND
    `source=user`. Without this, `forget <any-id>` would remove ANY KB entry by id
    (and unlink its `.md`) with a false "reversible" promise. The `_prefer_capture`
    supersede path passes its OWN capture id with the guard OFF, so it is
    unaffected (that id is always a preference/source=user entry anyway).

    `archive_dir` (used by `prefer forget`): copy the `.md` there BEFORE the
    index-remove + unlink, so a forgotten capture is genuinely recoverable — the
    honest reversibility the old unconditional message merely claimed."""
    data, err = load_index(kdir)
    if err is not None:
        return None, err
    match = next((e for e in (data.get("entries") or [])
                  if isinstance(e, dict) and e.get("id") == eid), None)
    if match is None:
        return None, None
    if require_preference and not _prefer_forgettable(kdir, match, eid):
        return None, (
            "refusing to forget '%s' — not a captured preference (source=user "
            "AND tag=preference required). `prefer forget` only revokes "
            "captures; use `index remove` for other entries." % eid)
    path = match.get("path")
    _prefer_archive_md(kdir, path, archive_dir)
    ns = argparse.Namespace(id=eid, format="json")
    with open(os.devnull, "w", encoding="utf-8") as _dn, \
            contextlib.redirect_stdout(_dn):
        rc = cmd_index_remove(ns)
    if rc != 0:
        return None, "failed to remove index entry '%s'" % eid
    if path:
        try:
            os.remove(os.path.join(kdir, path))
        except OSError:
            pass
    return match, None


def _prefer_write_migration_gate(kdir):
    """Migration contract for the prefer WRITE subcommands (Task 7, 260713).

    `prefer capture`/`forget` dispatch as `cmd_prefer`, which is EXCLUDED from the
    migration auto-apply set (`_kb_autoapply_funcs`) so its READ-ONLY subcommands
    (digest/list/scan-queue/seed-cursor) never trigger a schema WRITE inside the
    session-start / Stop hooks. That exclusion must NOT let a WRITE proceed while a
    RESTRUCTURING migration is pending — the same case `_kb_predispatch_gate`
    refuses for `learn`/`decide` — so a preference never lands against a schema
    about to be restructured. Refuse it here for the write paths (version-refusal
    for a NEWER KB already fired at the cmd_prefer predispatch, so this only sees
    cur <= KB_SCHEMA_VERSION). A pending ADDITIVE migration is intentionally NOT
    auto-applied here: that schema WRITE belongs to a real writer verb, not a
    hook-adjacent dual-mode func, and additive fields are forward-compatible — a
    capture written before them stays valid and is migrated by the next writer or
    an explicit `migrate --apply`. No-op for an uninitialized KB (onboarding path).
    Returns an exit code to short-circuit, else None."""
    if not kdir or not os.path.isdir(kdir):
        return None
    if not os.path.isfile(os.path.join(kdir, SENTINEL_REL)):
        return None
    restructuring = next(
        (m for m in _kbmigrate._kb_pending_migrations(kdir)
         if m[1] != "additive"), None)
    if restructuring is None:
        return None
    ver, _tier, name, _fn = restructuring
    sys.stderr.write(
        "agentware: refusing to capture — a restructuring migration (%d %s) is "
        "pending; run `scripts/agentware migrate --apply` first (a preference "
        "write must never land against a schema about to be restructured).\n"
        % (ver, name))
    return 1


def _workorder_queue_hits(kdir, feature):
    """Every queue record carrying `workorder == feature`. Returns (hits, err).

    Two sources, in this order and for one reason: `approved` is a TERMINAL
    status, so the rotation MAY archive the record between `prefer approve` and
    the operator running the workorder. Reading the live queue alone would make
    an approved candidate un-activatable purely because the queue got busy, so an
    empty live result falls back to a BOUNDED tail of the archive.

    An unreadable live queue is an ERROR rather than an empty result: silently
    falling through to the archive would turn "I could not read your queue" into
    "you never approved this", which is the wrong thing to tell an operator.
    """
    qpath = os.path.join(kdir, PREFER_QUEUE_REL)
    recs = []
    if os.path.isfile(qpath):
        try:
            recs = _prefer_queue_records(qpath)
        except OSError:
            return [], "cannot read the prefer-queue"
    hits = [r for r in recs if r.get("workorder") == feature]
    if not hits:
        hits = [r for r in _prefer_archive_tail_records(
            os.path.join(kdir, PREFER_QUEUE_ARCHIVE_REL))
            if r.get("workorder") == feature]
    return hits, None


def _prefer_workorder_candidate(kdir, feature):
    """Resolve the APPROVED prefer-queue record whose `workorder` == `feature`.

    The loop-closing seam (Task 4, 260714-miner-operationalize): a tier-2
    workorder's activation task names its candidate by WORKORDER ID, never by
    inlining the mined text — so untrusted candidate text never lands on an
    executable line (R-SEC-02) and never has to survive shell quoting. The verb
    reads the EXACT text (and the record's resolved `project` scope) straight
    from the queue record the operator reviewed.

    Reads the LIVE queue first, then falls back to a BOUNDED tail of the archive:
    `approved` is a TERMINAL status, so `_prefer_rotate_split` MAY archive the
    record between `prefer approve` and the operator running the workorder.

    APPROVAL IS A CODE CHECK (decide-self-correction-governance-tiers): only an
    `approved` record resolves. A still-`proposed` record is REFUSED with the
    approve instruction, so running the workorder WITHOUT approving can never
    activate a source=user pref. Read-only — never mutates the queue.

    Returns (rec, None) or (None, reason)."""
    feature = (feature or "").strip()
    if not feature:
        return None, "no workorder id given"
    hits, err = _workorder_queue_hits(kdir, feature)
    if err is not None:
        return None, err
    if not hits:
        return None, ("no queue candidate with workorder '%s' — see `prefer "
                      "list` or the classify report" % feature)
    # LAST wins: the drain appends, so the newest record for a workorder is the
    # current one (an archived+re-emitted fp resolves to the live record).
    approved = [r for r in hits if r.get("status") == "approved"]
    if not approved:
        return None, ("candidate for workorder '%s' is not approved (status: %s) "
                      "— run `scripts/agentware prefer approve %s` first"
                      % (feature, hits[-1].get("status") or "unknown", feature))
    rec = approved[-1]
    text = rec.get("text")
    if not isinstance(text, str) or not text.strip():
        return None, ("candidate for workorder '%s' has no text" % feature)
    return rec, None


def _capture_resolve_text(args, kdir):
    """The preference text this capture will write. Returns `(text, rc)`, where a
    non-None `rc` is the exit code to short-circuit on.

    Two routes, and the whole point is that they MERGE here rather than later.
    `--from-workorder auto/pref-…` reads the exact text and resolved project
    scope from the APPROVED queue record (the loop-closing activation the emitted
    workorder runs) instead of the command line; the resolved text then flows
    through the SAME refusals, noise bar and supersede path as a hand-typed
    capture, so mined text gets NO privileged route to a `source=user` pref.

    Passing both is refused rather than silently preferring one: the operator
    means two different captures and only one of them would happen.
    """
    text = (getattr(args, "text", None) or "").strip()
    from_wo = (getattr(args, "from_workorder", None) or "").strip()
    if not from_wo:
        if text:
            return text, None
        sys.stderr.write("agentware: prefer capture requires <text> or "
                         "--from-workorder <auto/pref-…>\n")
        return None, 1
    if text:
        sys.stderr.write("agentware: pass either <text> or --from-workorder, "
                         "not both\n")
        return None, 1
    rec, err = _prefer_workorder_candidate(kdir, from_wo)
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return None, 1
    # Scope from the record's resolved `project` (None -> global), unless the
    # caller overrode it explicitly.
    if not getattr(args, "scope_global", False) and not getattr(
            args, "project", None):
        proj_rec = rec.get("project")
        if isinstance(proj_rec, str) and proj_rec.strip():
            args.project = proj_rec.strip()
        else:
            args.scope_global = True
    return (rec.get("text") or "").strip(), None


def _capture_refusals(text):
    """The three gates every capture passes. Returns an exit code, or None to
    proceed.

    ORDER IS THE CONTRACT. The two REFUSALS run BEFORE the noise bar, so a
    capture naming a kernel/package path or weakening a governance gate exits
    NONZERO even when it carries no preference cue — routed to a human rather
    than silently swallowed as "noise". The noise bar is the opposite shape: not
    a preference is exit 0 with zero writes, because failing on it would make
    every casual sentence an error.
    """
    if _pref_hits_kernel(text):
        sys.stderr.write(
            "agentware: refusing to capture — this names a kernel/package path "
            "(%s). Package changes go through the human-authored self-extension "
            "plan path, never a captured preference.\n"
            % ", ".join(_KERNEL_PATH_TOKENS))
        return 1
    if _pref_loosens_gate(text):
        sys.stderr.write(
            "agentware: refusing to capture — a preference may TIGHTEN but never "
            "LOOSEN a governance gate. Route a gate change through the human-"
            "authored self-extension path.\n")
        return 1
    if not _pref_is_capturable(text):
        print("nothing captured: not a durable preference — say what a future "
              "session should always/never do (e.g. \"always pin dependency "
              "versions\").")
        return 0
    return None


def _capture_scope(kdir, args):
    """The canonical project slug this capture is scoped to, or None for global.

    `--global` forces global; `--project <slug>` scopes explicitly; otherwise the
    checkout is auto-resolved through the canonical normalizer and falls back to
    GLOBAL (Q4) rather than inventing a scope.

    An explicit `--project` naming no KB project WARNS and still captures. The
    session-start injection filter resolves a scope only to an existing
    `projects/<slug>/`, so such a preference would be captured yet never injected
    — a silently dead pref. Warning beats refusing because an operator may
    legitimately pre-scope a project they are about to register.
    """
    if getattr(args, "scope_global", False):
        return None
    if not getattr(args, "project", None):
        return _canonical_project_key(kdir, _current_project_signal())
    proj = _slugify(args.project) or None
    if proj and _canonical_project_key(kdir, proj) is None:
        sys.stderr.write(
            "agentware: warning — no KB project 'projects/%s/' exists; this "
            "preference is captured but will NOT inject until that project is "
            "registered (or re-capture with --global).\n" % proj)
    return proj


def _capture_entry_id(args, text, proj):
    """The supersede key: `(topic, entry_id)` for this capture.

    Explicit `--key`, else the slugified preference text — that is what makes a
    contradicting re-capture REPLACE its predecessor instead of stacking beside
    it.

    TRUNCATION IS DISAMBIGUATED, and the reason is data loss: a bare 60-char cap
    lets two DISTINCT long preferences that merely share a 60-char slug prefix
    collapse onto the same id, so the exact-key supersede would delete the
    unrelated prior capture. A stable short digest of the FULL key is appended
    only when the slug was actually truncated, so short prefs keep their clean,
    human-readable id and identical text (or an identical `--key`) still
    supersedes.
    """
    raw_key = getattr(args, "key", None) or text
    full_slug = _slugify(raw_key)
    keyslug = (full_slug[:60]).strip("-") or "pref"
    if len(full_slug) > 60:
        kh = hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:8]
        keyslug = ("%s-%s" % (keyslug, kh)).strip("-")
    topic = "pref-%s-%s" % (proj or "global", keyslug)
    return topic, "learn-%s" % topic


def _capture_tags(args, proj):
    """The entry's tags: `preference` first, the project tag when scoped, then
    the caller's extras, de-duplicated with first-wins order.

    `preference` is not decoration — `prefer list` and the session-start digest
    both select on it, so a capture that lost the tag would be written, injected
    nowhere and invisible to the audit surface that exists to find it.
    """
    tags = ["preference"]
    if proj:
        tags.append("project-%s" % proj)
    for t in (parse_tags(getattr(args, "tags", None)) or []):
        if t not in tags:
            tags.append(t)
    return tags


def _prefer_capture(args):
    """`prefer capture` — the steering-capture WRITE path (R-CAP-01/02/03).

    Five phases, each its own function: resolve the text (typed or activated from
    an approved workorder), run the refusals, resolve the scope, derive the
    supersede key, then write through `cmd_learn` under a suppressed stdout so
    the ack contract is exactly one `captured: <what> -> <where>` line.
    """
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    _mgate = _prefer_write_migration_gate(kdir)
    if _mgate is not None:
        return _mgate
    text, rc = _capture_resolve_text(args, kdir)
    if rc is not None:
        return rc
    rc = _capture_refusals(text)
    if rc is not None:
        return rc
    proj = _capture_scope(kdir, args)
    scope_slug = proj or "global"
    topic, eid = _capture_entry_id(args, text, proj)

    # One exact-key supersede: drop a prior capture under the SAME key so a
    # contradicting re-capture REPLACES rather than stacks (offline-first).
    _removed, err = _prefer_delete_entry(kdir, eid)
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1

    tags = _capture_tags(args, proj)
    body = ("%s\n\nOperator steering preference (source: user, scope: %s). "
            "Captured once via the steering-capture verb; in force every future "
            "session.\n" % (text, scope_slug))
    ns = argparse.Namespace(
        topic=topic, summary=text, tags=",".join(tags), content=body,
        source="user", author=None, title=None, force=True, format="json")
    with open(os.devnull, "w", encoding="utf-8") as _dn, \
            contextlib.redirect_stdout(_dn):
        rc = cmd_learn(ns)
    if rc != 0:
        return rc
    # Ack contract: exactly `captured: <what> -> <where>` (the resolved KB id).
    print("captured: %s -> %s" % (text, eid))
    return 0


def _prefer_list(args):
    """Audit surface: the captured preferences (wraps `query --tag preference`)."""
    ns = argparse.Namespace(
        id=None, path=None, tag="preference", category=None,
        depends_on=None, impact=None, relates=None, depth=1,
        format=getattr(args, "format", "text"))
    return cmd_query(ns)


def _prefer_forget(args):
    """Revoke a captured preference by id. Refuses anything that is not a genuine
    capture (source=user AND tag=preference — see `_prefer_delete_entry`), ARCHIVES
    the `.md` for genuine reversibility, then removes the index entry + unlinks the
    live file. Honest ack: the entry is REMOVED (not silently 'reversible')."""
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    _mgate = _prefer_write_migration_gate(kdir)
    if _mgate is not None:
        return _mgate
    eid = args.id
    archive_rel = os.path.join("logs", "steering", "forgotten")
    removed, err = _prefer_delete_entry(
        kdir, eid, require_preference=True,
        archive_dir=os.path.join(kdir, archive_rel))
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    if removed is None:
        sys.stderr.write(
            "agentware: no captured preference with id '%s'\n" % eid)
        return 1
    path = removed.get("path")
    if path:
        archived_rel_file = os.path.join(archive_rel, os.path.basename(path))
        # HONEST ack (audit round-2): only claim unlinked+archived when it actually
        # happened — an ORPHANED capture (its .md was already gone) archives nothing.
        if os.path.isfile(os.path.join(kdir, archived_rel_file)):
            print("forgot: %s — removed from the index and unlinked %s; archived "
                  "to %s (re-capture, or re-index the archived file, to restore)."
                  % (eid, path, archived_rel_file))
        else:
            print("forgot: %s — removed from the index; its .md (%s) was already "
                  "missing, so nothing was archived. Re-capture to restore."
                  % (eid, path))
    else:
        print("forgot: %s — removed from the index (no .md file); re-capture to "
              "restore." % eid)
    return 0


_EXPORTS = [
    "_prefer_forgettable",
    "_prefer_archive_md",
    "_prefer_delete_entry",
    "_prefer_write_migration_gate",
    "_workorder_queue_hits",
    "_prefer_workorder_candidate",
    "_capture_resolve_text",
    "_capture_refusals",
    "_capture_scope",
    "_capture_entry_id",
    "_capture_tags",
    "_prefer_capture",
    "_prefer_list",
    "_prefer_forget",
]

__all__ = list(_EXPORTS)
