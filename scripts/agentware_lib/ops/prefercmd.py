"""agentware_lib.ops.prefercmd — the `prefer` verb and the DIGEST (layer: ops).

Owns: the verb surface and the read-only summary — the `prefer` dispatcher
(`cmd_prefer`), the `prefer mine` and `prefer review` handlers, the digest family
(`_pref_digest_entries` / `_render_pref_digest` / `_prefer_digest` and their
budgets) and the `register_prefer` registrar.

WHY THE DIGEST HAS A CHARACTER BUDGET
The digest is injected at session start, so it is a PERMANENT context cost paid on
every session forever. `_PREF_DIGEST_MAX` and `_PREF_DIGEST_CHAR_BUDGET` are what
stop an accumulating preference set from silently eating the context window it is
supposed to improve — the budget is enforced by TRUNCATING the digest, not by
refusing to capture, so the capture path stays honest and the injection stays
bounded.

`prefer review` is deliberately read-only across EVERY candidate state, including
queued and journaled ones. A review surface that showed only actionable items
would hide the queue's backlog, which is precisely the number an operator needs to
decide whether the miner is worth keeping on.

Does NOT own: the admission bar (ops.preferadmit); the queue (ops.preferqueue);
the capture write (ops.prefercapture); classification (ops.preferclassify);
approval and rejection (ops.preferdispose); the miner (ops.sessionparse.minerscan).
Layer deps: ops -> core + same-layer ops (the whole prefer family plus the miner).
Fan-in: 0 (regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith — byte-identical, comments and all;
`register_prefer` is the monolith's own argparse block lifted out of build_parser.
"""

import argparse
import json
import os
import sys

import agentware_lib.core.config as config
from agentware_lib.core.config import require_knowledge_dir, resolve_knowledge_dir
from agentware_lib.core.dates import _now_iso
from agentware_lib.core.frontmatter import (
    _entry_list_field,
    _operator_handle,
    read_entry_frontmatter,
)
from agentware_lib.core.projectcontext import _canonical_project_key, _resolve_context
from agentware_lib.knowledge.index import load_index
from agentware_lib.ops.preferadmit import _pref_entry_is_user_source
from agentware_lib.ops.prefercapture import (
    _prefer_capture,
    _prefer_forget,
    _prefer_list,
)
from agentware_lib.ops.preferclassify import _prefer_classify_drain
from agentware_lib.ops.preferdispose import cmd_prefer_approve, cmd_prefer_reject
from agentware_lib.ops.preferqueue import (
    PREFER_QUEUE_ARCHIVE_REL,
    PREFER_QUEUE_PRODUCER,
    PREFER_QUEUE_REL,
    _prefer_archive_tail_records,
    _prefer_cand_fp,
    _prefer_scan_queue,
    _prefer_seed_cursor_cli,
)
from agentware_lib.ops.prefergate import _pref_loosens_gate
from agentware_lib.ops.planauthor.plankernelpath import _pref_hits_kernel
from agentware_lib.ops.preferdigest import _prefer_digest, _render_pref_digest
from agentware_lib.ops.sessionparse.minerscan import _transcript_mine
from agentware_lib.ops.sessionparse.minerstate import (
    MINER_SESSION_CAP,
    MINER_TRANSCRIPT_MAXLEN,
)


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("prefer", 4000, "register_prefer"),
)


def register_prefer(sub, fmt_parent):
    """Declare the `prefer` verb. Lifted verbatim from the monolith's build_parser."""
    p_prefer = sub.add_parser(
        "prefer",
        help="capture a durable steering preference (say it once, it's in force "
             "every future session)")
    p_prefer.set_defaults(func=cmd_prefer)
    prefer_sub = p_prefer.add_subparsers(dest="prefer_command",
                                         metavar="<subcommand>")
    _register_prefer_capture(prefer_sub, fmt_parent)
    _register_prefer_manage(prefer_sub, fmt_parent)
    _register_prefer_producers(prefer_sub, fmt_parent)


def _register_prefer_capture(prefer_sub, fmt_parent):
    """The `capture` subverb — the one that WRITES a preference."""
    p_pref_cap = prefer_sub.add_parser(
        "capture", parents=[fmt_parent],
        help="capture a preference as a source=user, preference-tagged entry")
    p_pref_cap.add_argument("text", nargs="?", default=None,
                            help="the preference, one line (omit with "
                                 "--from-workorder)")
    p_pref_cap.add_argument(
        "--from-workorder", dest="from_workorder", default=None,
        metavar="AUTO/PREF-…",
        help="activate the APPROVED tier-2 candidate of this workorder: read "
             "its exact text + project scope from the prefer-queue record "
             "(the loop-closing step an emitted `auto/pref-…` workorder runs; "
             "refuses a candidate that is not yet `prefer approve`d)")
    pref_scope = p_pref_cap.add_mutually_exclusive_group()
    pref_scope.add_argument("--global", dest="scope_global", action="store_true",
                            help="capture as a GLOBAL preference (all projects)")
    pref_scope.add_argument("--project", default=None,
                            help="scope to a KB project slug (default: auto-"
                                 "resolve the checkout via the canonical "
                                 "normalizer, else global)")
    p_pref_cap.add_argument("--key", default=None,
                            help="explicit supersede key (default: derived from "
                                 "the text); a re-capture under the same key "
                                 "supersedes rather than stacks")
    p_pref_cap.add_argument("--tags", default=None,
                            help="extra comma-separated tags ('preference' "
                                 "auto-added)")
    p_pref_cap.add_argument("--force", action="store_true",
                            help="capture even if a near-duplicate exists")
    p_pref_cap.set_defaults(func=cmd_prefer, prefer_command="capture")

    p_pref_digest = prefer_sub.add_parser(
        "digest",
        help="render the scope-filtered active-preference digest that "
             "session-start injects (read-only; global + resolved-project prefs)")
    p_pref_digest.add_argument(
        "--project", default=None,
        help="the checkout signal (whereami project_name); normalized to the "
             "canonical KB slug (unknown -> GLOBAL-only, never mis-scoped)")
    p_pref_digest.set_defaults(func=cmd_prefer, prefer_command="digest")



def _register_prefer_manage(prefer_sub, fmt_parent):
    """Read and disposition subverbs: digest, list, forget, approve, reject."""
    p_pref_list = prefer_sub.add_parser(
        "list", parents=[fmt_parent],
        help="list captured preferences (the injection audit surface)")
    p_pref_list.set_defaults(func=cmd_prefer, prefer_command="list")

    p_pref_forget = prefer_sub.add_parser(
        "forget", parents=[fmt_parent],
        help="remove a captured preference by id (reversible: re-capture)")
    p_pref_forget.add_argument("id", help="the preference entry id (learn-pref-…)")
    p_pref_forget.set_defaults(func=cmd_prefer, prefer_command="forget")

    # Layer-3 consumer (Task 7): approve a dream-proposed steering candidate —
    # flip its queue record proposed -> approved (approval = operator-run the
    # emitted workorder through the loop, Q3). Its own dispatched func so the
    # schema-guard covers this state-mutating write.
    p_pref_approve = prefer_sub.add_parser(
        "approve",
        help="approve a dream-proposed steering candidate by its workorder id "
             "(operator-run the workorder to activate the preference)")
    p_pref_approve.add_argument(
        "id", help="the emitted workorder feature (auto/pref-…)")
    p_pref_approve.set_defaults(func=cmd_prefer_approve, prefer_command="approve")

    # reject: the governed counterpart to approve — mark a proposed candidate as
    # noise (proposed -> rejected) AND cancel its emitted workorder, keeping the
    # plan.md as the re-emit sentinel. Closes the proposed-noise dead end.
    p_pref_reject = prefer_sub.add_parser(
        "reject", parents=[fmt_parent],
        help="reject a dream-proposed steering candidate as noise by its "
             "workorder id (flips proposed->rejected + cancels the workorder)")
    p_pref_reject.add_argument(
        "id", help="the emitted workorder feature (auto/pref-…)")
    p_pref_reject.add_argument(
        "--reason", default=None,
        help="optional rejection reason recorded on the queue record + the "
             "workorder cancel")
    p_pref_reject.set_defaults(func=cmd_prefer_reject, prefer_command="reject")

    # Layer-3 OPERATOR SURFACE (Task 8, 260714): the read-only "what needs me?"
    # digest over the prefer-queue + archive. Its own dispatched func precisely
    # BECAUSE it is read-only — `cmd_prefer` is in `_kb_writer_funcs()`, so
    # riding it would make this read refuse on a newer-schema KB.


def _register_prefer_producers(prefer_sub, fmt_parent):
    """Producer subverbs: review, scan-queue, seed-cursor and mine — the paths
    that FEED the queue rather than dispose of it."""
    p_pref_review = prefer_sub.add_parser(
        "review", parents=[fmt_parent],
        help="read-only digest of mined steering candidates awaiting you "
             "(default: proposed-but-unapproved). NOTE: reviews CANDIDATES — "
             "the top-level `review` verb audits a DIFF instead.")
    p_pref_review.add_argument(
        "--rejected", action="store_true",
        help="also show tier-3 rejected candidates (kernel-path / gate-loosening)")
    p_pref_review.add_argument(
        "--approved", action="store_true",
        help="also show already-approved candidates (awaiting their workorder run)")
    p_pref_review.add_argument(
        "--all", action="store_true",
        help="show candidates in EVERY state (incl. queued / journaled)")
    p_pref_review.set_defaults(func=cmd_prefer_review, prefer_command="review")

    # Layer-2 producer (Task 5): the Stop hook invokes this to append inert
    # preference-shaped candidates from origin=human turns in logs/prompts.log to
    # logs/steering/prefer-queue.jsonl (watermark-idempotent, no auto-apply).
    p_pref_scanq = prefer_sub.add_parser(
        "scan-queue",
        help="Stop-hook producer: queue preference-shaped human turns from "
             "prompts.log (inert candidates; watermark-idempotent)")
    p_pref_scanq.add_argument("--sid", default="",
                              help="the current session id (queue is scoped/"
                                   "watermarked per session)")
    p_pref_scanq.add_argument("--cwd", default="",
                              help="fallback checkout dir when a record omits cwd")
    p_pref_scanq.add_argument("--prompts", default=None,
                              help="prompts.log path (default: <kdir>/logs/"
                                   "prompts.log; overridable for tests)")
    p_pref_scanq.set_defaults(func=cmd_prefer, prefer_command="scan-queue")

    # Producer robustness (Task 6, 260713): session-start seeds a NEW session's
    # watermark to the CURRENT prompts.log size so the first Stop does not re-parse
    # the whole cross-session log. Idempotent (never clobbers an advanced cursor).
    p_pref_seed = prefer_sub.add_parser(
        "seed-cursor",
        help="session-start producer seed: set a new session's queue watermark to "
             "the current prompts.log size (idempotent; bounds first-Stop cost)")
    p_pref_seed.add_argument("--sid", default="",
                             help="the current session id")
    p_pref_seed.add_argument("--prompts", default=None,
                             help="prompts.log path (default: <kdir>/logs/"
                                  "prompts.log; overridable for tests)")
    p_pref_seed.set_defaults(func=cmd_prefer, prefer_command="seed-cursor")

    # P2c transcript miner (260713): the OFFLINE second producer. Mines the
    # operator's own corpus (prompts.log/sessions/metrics) for recurrence +
    # error-recovery candidates into the SAME queue (producer=transcript-miner),
    # quarantined (status:queued) and governed by the EXISTING drain tiers. Its
    # own dispatched writer func so the schema-guard covers this state mutation.
    p_pref_mine = prefer_sub.add_parser(
        "mine", parents=[fmt_parent],
        help="OFFLINE transcript miner: queue recurrence + error-recovery "
             "candidates from the corpus (quarantined; never auto-activated)")
    p_pref_mine.add_argument(
        "--backfill", action="store_true",
        help="one-shot historical mining of seeded sessions (bounded by "
             "--max-sessions; run repeatedly to catch up). Default: incremental.")
    p_pref_mine.add_argument(
        "--max-sessions", dest="max_sessions", type=int, default=None,
        help="cap sessions walked this run (default: %d)" % MINER_SESSION_CAP)
    p_pref_mine.set_defaults(func=cmd_prefer_mine, prefer_command="mine")


def cmd_prefer_mine(args):
    """`prefer mine [--backfill] [--max-sessions N]` — the OFFLINE transcript
    miner: a SECOND prefer-queue producer that mines the operator's own corpus for
    recurrence + error-recovery candidates (QUARANTINED status:queued,
    producer='transcript-miner'). Never auto-activates a source=user pref — that
    stays an explicit `prefer approve` -> run (Q3). A dispatched STATE-MUTATING
    writer, so it is registered in _kb_writer_funcs() (the predispatch schema-guard
    keys on args.func)."""
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    backfill = bool(getattr(args, "backfill", False))
    cap = getattr(args, "max_sessions", None)
    try:
        cap = int(cap) if cap else None
    except (TypeError, ValueError):
        cap = None
    n = _transcript_mine(kdir, backfill=backfill, session_cap=cap)
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": True,
                   "mode": "backfill" if backfill else "incremental",
                   "candidates": n}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("prefer mine (%s): %d candidate(s) queued"
              % ("backfill" if backfill else "incremental", n))
    return 0


# Layer-3 OPERATOR SURFACE (Task 8, 260714-miner-operationalize): `prefer review`
# — a READ-ONLY digest of what is AWAITING the operator. The drain's own report
# (PREFER_CLASSIFY_REPORT_REL) is a PER-RUN snapshot: it shows what THAT run
# classified, so an item proposed two runs ago and still unapproved has already
# scrolled out of it. This verb instead derives the CURRENT state from the
# durable source of truth — the prefer-queue + its archive `status` field (plus
# the emitted `work/auto/pref-…` workorders) — so the open set is always right.
#
# NAMESPACE (P9): distinct from the TOP-LEVEL `review` verb (the adversarial code
# audit). `prefer review` reviews mined STEERING CANDIDATES; `review` audits a
# DIFF. Different namespaces, no CLI collision — disambiguated in the help text.
#
# Read-only by construction: it opens the queue/archive for READ, never locks,
# never writes, and is deliberately NOT registered in `_kb_writer_funcs()` (which
# is why it gets its own dispatched func rather than riding `cmd_prefer`, whose
# registration would make this read refuse on a newer-schema KB).
#
# R-SEC-02: candidate text is UNTRUSTED mined operator prose. It is rendered as
# DATA only — collapsed to one line, capped, and quoted — never as an executable
# line and never interpolated into the printed `prefer approve` command (that
# names the workorder ID, per decide-p2c-prefer-activation-by-workorder-id-not-
# inlined-text).
#
# INV-1 (determinism): no clock in the body. The only timestamps printed are the
# records' OWN `ts` fields (data, not a live read), and the total order is a
# deterministic sort with a full tiebreak — so re-running against an unchanged
# queue is byte-identical.
_PREFER_REVIEW_TEXT_PREVIEW = 240     # text-mode single-line candidate cap


_PREFER_REVIEW_DEFAULT_STATUSES = ("proposed",)


def _prefer_review_signal(rec):
    """The detector signal behind a candidate, normalized for display.

    Miner records carry an explicit `signal` (recurrence / error-recovery); the
    Stop-hook regex producer predates that field, so map it to 'regex' by its
    `producer`. Anything else is reported honestly as 'unknown' rather than
    guessed."""
    sig = rec.get("signal")
    if isinstance(sig, str) and sig.strip():
        return sig.strip()
    if rec.get("producer") == PREFER_QUEUE_PRODUCER:
        return "regex"
    return "unknown"


def _prefer_review_sessions(rec):
    """How many DISTINCT sessions a candidate was seen in.

    `recurrence_count` is the TRUE distinct-session count and wins; the
    `source_sessions` list is capped at MINER_SOURCE_SESSIONS_CAP, so its length
    is only a floor (used when no count was recorded). A single-turn regex
    candidate is 1 (its own session)."""
    rc = rec.get("recurrence_count")
    if isinstance(rc, int) and not isinstance(rc, bool) and rc > 0:
        return rc
    ss = rec.get("source_sessions")
    if isinstance(ss, list):
        n = len(set(s for s in ss if isinstance(s, str) and s))
        if n:
            return n
    return 1 if rec.get("sid") else 0


def _prefer_review_records(kdir):
    """Every prefer-queue candidate, deduped NEWEST-WINS, in a deterministic order.

    Reads the BOUNDED archive tail FIRST, then the live queue, keying both by the
    shared candidate fingerprint (`_prefer_cand_fp`) so a record present in both
    resolves to its LIVE copy. That is what makes an APPROVED item drop out of the
    default view even when `_prefer_rotate_split` left a stale `proposed` copy in
    the archive (`approved` is terminal, so it MAY be archived at any point).

    Bounded: the archive read is a tail window; the live queue is rotation-capped
    at _PREFER_QUEUE_CAP. Read-only — never locks, never writes."""
    recs = []
    recs.extend(_prefer_archive_tail_records(
        os.path.join(kdir, PREFER_QUEUE_ARCHIVE_REL)))
    qpath = os.path.join(kdir, PREFER_QUEUE_REL)
    if os.path.isfile(qpath):
        try:
            with open(qpath, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue          # tolerate a torn/partial queue line
                    if isinstance(rec, dict):
                        recs.append(rec)
        except OSError:
            pass                          # an unreadable queue reviews as empty
    merged = {}
    for rec in recs:
        merged[_prefer_cand_fp(rec.get("sid"), rec.get("turn_id"),
                               rec.get("text"))] = rec
    # Deterministic TOTAL order (INV-1): record ts, then the fp as a full
    # tiebreak — never dict/file order, never a live clock.
    return [r for _, r in sorted(
        merged.items(),
        key=lambda kv: (str(kv[1].get("ts") or ""), kv[0]))]


def _prefer_review_collapse(text):
    """UNTRUSTED candidate text as ONE bounded, printable line (R-SEC-02).

    Collapses all whitespace (so embedded newlines can never fake a new report
    line or a shell prompt) and caps the length — the queue holds multi-KB pasted
    turns. Data only; the caller quotes it."""
    txt = " ".join((text if isinstance(text, str) else "").split())
    if len(txt) > _PREFER_REVIEW_TEXT_PREVIEW:
        txt = txt[:_PREFER_REVIEW_TEXT_PREVIEW - 1].rstrip() + "…"
    return txt


def cmd_prefer_review(args):
    """`prefer review [--rejected|--approved|--all] [--format json]` — the
    operator's read-only "what needs me?" digest over the prefer-queue.

    DEFAULT = the OPEN set only: tier-2 candidates `proposed` but not yet
    approved (i.e. a `work/auto/pref-…` workorder was emitted and is still
    awaiting `prefer approve`). `--rejected` adds the tier-3 rejects, `--approved`
    adds the approved, `--all` shows every state (incl. `queued`/`journaled`).

    A dispatched READ-ONLY func, deliberately ABSENT from `_kb_writer_funcs()`
    (exempt by OMISSION, like recall/query — see that docstring)."""
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    show_all = bool(getattr(args, "all", False))
    want = set(_PREFER_REVIEW_DEFAULT_STATUSES)
    if getattr(args, "rejected", False):
        want.add("rejected")
    if getattr(args, "approved", False):
        want.add("approved")
    items = []
    for rec in _prefer_review_records(kdir):
        status = rec.get("status") or "unknown"
        if not show_all and status not in want:
            continue
        wo = rec.get("workorder") or None
        item = {
            "workorder": wo,
            "status": status,
            "signal": _prefer_review_signal(rec),
            "sessions": _prefer_review_sessions(rec),
            "project": rec.get("project"),
            "ts": rec.get("ts") or "",
            "text": rec.get("text") if isinstance(rec.get("text"), str) else "",
        }
        if wo:
            # Report the workorder's presence rather than FILTERING on it: a
            # proposed record whose workorder was removed is still an open item
            # the operator must resolve — hiding it would silently shrink the
            # open set (the exact failure `prefer review` exists to prevent).
            item["workorder_exists"] = os.path.isdir(
                os.path.join(kdir, "work", wo))
            if status == "proposed":
                item["approve"] = "scripts/agentware prefer approve %s" % wo
        items.append(item)
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": True,
                   "scope": "all" if show_all else sorted(want),
                   "count": len(items),
                   "items": items},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    if not items:
        scope = "any state" if show_all else " / ".join(sorted(want))
        print("prefer review: nothing awaiting you (no %s candidate(s) in the "
              "prefer-queue)." % scope)
        return 0
    label = "candidate(s)" if show_all or want != {"proposed"} else \
        "open item(s) awaiting `prefer approve`"
    print("prefer review: %d %s" % (len(items), label))
    print("")
    for item in items:
        head = item["workorder"] or "(no workorder — %s)" % item["status"]
        print("- %s  [%s · %s · %d session(s)]"
              % (head, item["status"], item["signal"], item["sessions"]))
        if item.get("project"):
            print("  project:   %s" % item["project"])
        # UNTRUSTED, inert, collapsed + capped, quoted as DATA (R-SEC-02).
        print("  candidate: \"%s\"" % _prefer_review_collapse(item["text"]))
        if item.get("workorder") and not item.get("workorder_exists"):
            print("  note:      workorder dir is MISSING under work/ "
                  "(emitted then removed?)")
        if item.get("approve"):
            print("  approve:   %s" % item["approve"])
    return 0














def cmd_prefer(args):
    """Steering-capture verb. `prefer capture <text>` writes ONE `source: user`,
    `preference`-tagged learning (layer 1); `prefer digest` renders the injected
    scope-filtered digest (layer-1 enforcement); `prefer list` / `prefer forget
    <id>` are the audit + reversible-revoke surface. Every governance boundary is
    a CODE check enforced before any write; the verb NEVER mutates a package/
    kernel path. Registered in `_kb_writer_funcs()` (the gate keys on the
    dispatched func)."""
    subcmd = getattr(args, "prefer_command", None)
    if subcmd == "capture":
        return _prefer_capture(args)
    if subcmd == "digest":
        return _prefer_digest(args)
    if subcmd == "list":
        return _prefer_list(args)
    if subcmd == "forget":
        return _prefer_forget(args)
    if subcmd == "scan-queue":
        return _prefer_scan_queue(args)
    if subcmd == "seed-cursor":
        return _prefer_seed_cursor_cli(args)
    if subcmd == "approve":
        return cmd_prefer_approve(args)
    if subcmd == "review":
        return cmd_prefer_review(args)
    sys.stderr.write(
        "agentware: usage: prefer capture <text> [--global|--project SLUG] | "
        "prefer digest [--project SIGNAL] | prefer list | prefer forget <id> | "
        "prefer review [--rejected|--approved|--all] | "
        "prefer approve <workorder> | prefer scan-queue --sid SID --cwd CWD | "
        "prefer seed-cursor --sid SID\n")
    return 2


_EXPORTS = [
    "cmd_prefer",
    "cmd_prefer_mine",
    "cmd_prefer_review",
    "register_prefer",
    "_prefer_digest",
    "_render_pref_digest",
]

__all__ = list(_EXPORTS)
