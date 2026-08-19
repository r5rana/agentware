"""agentware_lib.features.curationcmd -- the learn/decide command surface (layer: features).

Owns: the `learn` and `decide` CLI command handlers (cmd_learn / cmd_decide), the
ONE write prologue they share (_kdir_for_curation_write -- resolve the knowledge dir
THEN apply the shared/ CR-gate, so neither handler can acquire a write target
without passing the gate), and their argparse registrars (register_learn /
register_decide) -- the orchestrating write-commands that validate input, guard
shared-writes, render the entry body, write the file, and register it in the index.

Does NOT own: the pure entry WRITERS/renderers (_render_learning /
_render_decision_body / _topic_to_title / _slugify) -> agentware_lib.knowledge.curation;
the shared-write governance guard (guard_shared_write) -> agentware_lib.ops.fleet;
the ACR source-weight prior (ACR_SOURCE_WEIGHTS) -> agentware_lib.retrieval.acr; the
index CRUD (_do_add / save_index / load_index) -> agentware_lib.knowledge.index.

Layer deps: features.curationcmd -> core (config, dates, frontmatter, routing) +
knowledge (index, curation) + ops (fleet as _fleet, for guard_shared_write) +
retrieval (acr.ACR_SOURCE_WEIGHTS) -- ALL DOWNWARD for the features layer. Those two
cross-layer symbols are exactly why the handlers CANNOT live in knowledge.curation
with the writers: knowledge->ops and knowledge->retrieval are UPWARD edges M12
forbids, so the orchestrating HANDLERS belong in features (mirrors the
init/config/profile handler split) while the pure writers stay in knowledge.

A LEAF: nothing imports it at module level (fan-in 0); the scripts/agentware
entrypoint re-exports cmd_learn / cmd_decide BY NAME so the legacy load_cli oracle
(CLI.cmd_learn / CLI.cmd_decide) and the global-resolve gate resolve them, and the
registrars are resolved by agentware_lib.cli.assemble_parser via _PARSER_REGISTRATION.

Determinism: read-mostly w.r.t. the index EXCEPT the explicit entry writes, which go
through index.save_index / index._do_add (the sole index writer, R-KB-01). A default
team-loop write into shared/ is refused unless a curator --to-shared
(guard_shared_write). INV-1 deterministic.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os
import re
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.core.routing as routing
import agentware_lib.knowledge.curation as curation
import agentware_lib.knowledge.index as index
import agentware_lib.ops.fleet as _fleet
from agentware_lib.retrieval.acr import ACR_SOURCE_WEIGHTS
from agentware_lib.core.frontmatter import DEFAULT_ENTRY_SOURCE
# Imported BY NAME because the ported `cmd_learn` body is the monolith's verbatim
# and calls these bare. Rewriting the body to alias form would have made a
# hardened, security-relevant function a transcription instead of a move — and
# this migration's whole verification story is byte-identical bodies.
from agentware_lib.core.config import require_knowledge_dir
from agentware_lib.core.dates import _today, utc_now_iso
from agentware_lib.core.frontmatter import build_entry_frontmatter, _operator_handle
from agentware_lib.core.routing import resolve_user_handle
from agentware_lib.knowledge.curation import _slugify, _topic_to_title
from agentware_lib.knowledge.index import (
    insert_entry_sorted, learnings_dir, load_index, save_index, add_tags_to_map,
    LEARNINGS_REL, parse_tags, _do_add, _index_lock)
from agentware_lib.knowledge.curation import _render_learning
from agentware_lib.knowledge.worklog import all_entry_ids
from agentware_lib.core.routing import _normalize_user_handle
from agentware_lib.retrieval.bm25 import bm25_scores
from agentware_lib.retrieval.corpus import build_corpus, tokenize


# A slug-safe learning topic stem: starts alnum, then alnum-or-hyphen.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


# Self-normalized BM25 overlap at/above which `learn` treats new content as a
# near-duplicate of an existing learning and refuses without --force. Tuned so a
# near-verbatim restatement (~1.0) blocks while distinct entries sharing only
# boilerplate stay well under (empirically <= ~0.5 on the synthetic KB).
LEARN_NEAR_DUP_THRESHOLD = 0.75


def _kdir_for_curation_write(args):
    """The knowledge dir a `learn`/`decide` write may target, or None if refused.

    ONE prologue for both handlers, because the CR-gate is only a gate if EVERY
    entry-writing verb passes through it. `learn` reached this module without the
    guard call its own docstring promises: the flag was still declared, so
    `--to-shared` parsed, was ignored, and a non-curator team loop wrote org-wide
    while the help text said it could not. A gate that one of two sibling handlers
    skips is indistinguishable from no gate for whichever handler skips it, and
    the skip is invisible at review time precisely because the flag is still there.
    Sharing the prologue makes the guard structural: a third curation verb gets it
    by resolving its kdir, and cannot get a kdir any other way.

    Returns the resolved kdir on success. On refusal it writes the reason to
    stderr (both branches already own their message) and returns None, so the
    caller's existing `if not kdir: return 1` covers both without gaining a
    branch. Read-only; the refusal happens BEFORE any file or index write.
    """
    kdir = require_knowledge_dir()
    if not kdir:
        return None
    ok, gerr = _fleet.guard_shared_write(
        kdir, to_shared=getattr(args, "to_shared", False))
    if not ok:
        sys.stderr.write("agentware: %s\n" % gerr)
        return None
    return kdir


def _near_duplicate_learning(kdir, data, query_text):
    """Closest existing-learning near-duplicate of `query_text`: (entry, ratio).

    Retrieval reuses the SAME lexical machinery recall uses (`build_corpus` +
    `bm25_scores`) scoped to the `learnings` sub-corpus. The new content is
    appended as an ephemeral pseudo-doc so its BM25 SELF-score normalizes scale:
    `ratio = bm25(query, best_existing) / bm25(query, new_content)`, a corpus-
    independent [0,1] self-similarity (~1.0 == near-verbatim; distinct entries
    sharing only boilerplate score low because BM25 discounts common terms via
    IDF). On ties the reported entry follows recall's canonical order (score desc
    -> created desc -> id asc). Read-only; no network/LLM/index write."""
    q_tokens = tokenize(query_text)
    if not q_tokens:
        return None, 0.0
    corpus = [(e, t) for (e, t) in build_corpus(kdir, data)
              if e.get("category") == "learnings"]
    if not corpus:
        return None, 0.0
    pseudo_id = "\x00new-learning"  # sentinel absent from any real corpus id
    corpus.append(({"id": pseudo_id, "category": "learnings", "created": ""},
                   query_text))
    scored = bm25_scores(corpus, q_tokens)
    self_score = 0.0
    others = []
    for entry, score in scored:
        if entry.get("id") == pseudo_id:
            self_score = score
        elif score > 0.0:
            others.append((entry, score))
    if self_score <= 0.0 or not others:
        return None, 0.0
    others.sort(key=lambda es: es[0].get("id") or "")
    others.sort(key=lambda es: es[0].get("created") or "", reverse=True)
    others.sort(key=lambda es: es[1], reverse=True)
    best_entry, best_score = others[0]
    return best_entry, best_score / self_score


def cmd_learn(args):
    kdir = _kdir_for_curation_write(args)
    if not kdir:
        return 1

    raw_topic = (args.topic or "").strip()
    if not raw_topic:
        sys.stderr.write("agentware: --topic must not be empty\n")
        return 1
    # Normalize --topic to a slug-safe stem (^[a-z0-9][a-z0-9-]*$). A non-slug
    # topic (spaces, punctuation, uppercase) silently produced broken ids AND
    # filenames, and breaks worklog-scan promotion (which slugifies markers
    # before matching learning stems). Auto-slugify + notice instead of writing a
    # malformed entry (the exact bug that left 4 unpromotable legacy entries).
    topic = raw_topic if SLUG_RE.match(raw_topic) else _slugify(raw_topic)
    if not topic:
        sys.stderr.write(
            "agentware: --topic %r has no slug-safe characters\n" % raw_topic)
        return 1
    if topic != raw_topic:
        sys.stderr.write(
            "agentware: normalized --topic %r -> %r\n" % (raw_topic, topic))

    body = sys.stdin.read() if args.content == "-" else args.content
    if not (body or "").strip():
        sys.stderr.write("agentware: --content must not be empty\n")
        return 1

    # Optional provenance passthrough (feature 260625-team-mode-onboarding-fork).
    # Backward-compat invariant: when BOTH are omitted the frontmatter is
    # byte-identical to today — `source` stays DEFAULT_ENTRY_SOURCE and `author`
    # defaults to the per-user handle if configured, else _operator_handle(kdir)
    # (which is exactly today's behavior when no per-user handle is set).
    src = getattr(args, "source", None)
    if src is not None:
        src_norm = src.strip().lower()
        if src_norm not in ACR_SOURCE_WEIGHTS:
            sys.stderr.write(
                "agentware: invalid --source %r (use %s)\n"
                % (src, "|".join(sorted(ACR_SOURCE_WEIGHTS))))
            return 1
        src = src_norm
    else:
        src = DEFAULT_ENTRY_SOURCE
    author = getattr(args, "author", None)
    if author is not None:
        author = _normalize_user_handle(author)
        if not author:
            sys.stderr.write(
                "agentware: invalid --author (must contain at least one of "
                "A-Z a-z 0-9 . _ -)\n")
            return 1
    else:
        author = resolve_user_handle()  # None => build_entry_frontmatter uses _operator_handle

    eid = "learn-%s" % topic
    rel_path = "%s/%s.md" % (LEARNINGS_REL, topic)
    abs_path = os.path.join(kdir, rel_path)
    tag_list = parse_tags(args.tags)
    # --title overrides the derived display title; the default derives from the
    # ORIGINAL (pre-slug) topic so readability survives auto-slugify. When topic
    # is already a slug and --title is omitted this is byte-identical to the prior
    # `_topic_to_title(topic)` (raw_topic == topic).
    title = (getattr(args, "title", None) or "").strip() or _topic_to_title(raw_topic)
    created = _today()

    # Serialize the index read-modify-write against concurrent writers (B2). Load
    # INSIDE the lock so the dup/near-dup checks and _do_add all see the freshest
    # committed index — a lock that opened after the load could not prevent a lost
    # update. Arg-parse/validation + stdin read stayed OUTSIDE (index-independent).
    with _index_lock(kdir):
        data, err = load_index(kdir)
        if err is not None:
            sys.stderr.write("agentware: %s\n" % err)
            return 1

        entries = data.get("entries") or []
        if any(e.get("id") == eid for e in entries):
            sys.stderr.write("agentware: duplicate id '%s'\n" % eid)
            return 1
        if any(e.get("path") == rel_path for e in entries):
            sys.stderr.write("agentware: path already indexed: %s\n" % rel_path)
            return 1
        if os.path.exists(abs_path):
            sys.stderr.write(
                "agentware: learning file already exists (refusing to overwrite): %s\n"
                % abs_path)
            return 1

        # BM25 near-duplicate probe: self-query the new content against EXISTING
        # learnings via the SAME lexical retrieval recall uses. A high self-
        # normalized overlap means this likely restates an entry that already
        # exists — point at the match and refuse unless --force.
        if not getattr(args, "force", False):
            probe_text = "\n".join(
                [title, args.summary or "", " ".join(tag_list), body])
            dup_entry, ratio = _near_duplicate_learning(kdir, data, probe_text)
            if dup_entry is not None and ratio >= LEARN_NEAR_DUP_THRESHOLD:
                sys.stderr.write(
                    "agentware: near-duplicate of existing learning '%s' "
                    "(%.0f%% overlap): %s\n"
                    "  amend that entry, pick a more specific --topic, or pass "
                    "--force to record anyway.\n"
                    % (dup_entry.get("id"), ratio * 100.0, dup_entry.get("path")))
                return 1

        fm = build_entry_frontmatter(
            {"id": eid, "title": title, "category": "learnings", "tags": tag_list,
             "created": created, "summary": args.summary},
            kdir, author=author, source=src)
        os.makedirs(learnings_dir(kdir), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(_render_learning(fm, body))

        entry, add_err = _do_add(
            kdir, data, eid, title, "learnings", rel_path, tag_list, args.summary, created)
        if add_err is not None:
            try:
                os.remove(abs_path)
            except OSError:
                pass
            sys.stderr.write("agentware: %s\n" % add_err)
            return 1

        save_index(kdir, data)
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": True, "learning": entry}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("LEARNED entry '%s' -> %s (registered in index)" % (eid, abs_path))
    return 0


def cmd_decide(args):
    """Write + register a `decisions/`-category entry (auto-tagged `decision`).

    A near-clone of `cmd_learn` that reuses the same index plumbing (NEVER hand-
    edits index.json) and the same renderer; the only differences are the
    category, the auto `decision` tag, and the structured decision body.
    """
    kdir = _kdir_for_curation_write(args)
    if not kdir:
        return 1
    with index._index_lock(kdir):  # serialize load->mutate->save (B2 lost-update guard)
        data, err = index.load_index(kdir)
        if err is not None:
            sys.stderr.write("agentware: %s\n" % err)
            return 1

        topic = (args.topic or "").strip()
        if not topic:
            sys.stderr.write("agentware: --topic must not be empty\n")
            return 1

        eid = "decide-%s" % topic
        rel_path = "%s/%s.md" % (index.DECISIONS_REL, topic)
        abs_path = os.path.join(kdir, rel_path)
        tag_list = index.parse_tags(args.tags) if getattr(args, "tags", None) else []
        if "decision" not in tag_list:
            tag_list = ["decision"] + tag_list
        title = curation._topic_to_title(topic)
        created = dates._today()

        entries = data.get("entries") or []
        if any(e.get("id") == eid for e in entries):
            sys.stderr.write("agentware: duplicate id '%s'\n" % eid)
            return 1
        if any(e.get("path") == rel_path for e in entries):
            sys.stderr.write("agentware: path already indexed: %s\n" % rel_path)
            return 1
        if os.path.exists(abs_path):
            sys.stderr.write(
                "agentware: decision file already exists (refusing to overwrite): "
                "%s\n" % abs_path)
            return 1

        body = curation._render_decision_body(args.summary, args.options, args.choice,
                                     args.rationale, args.reversible)
        # Resolve author: explicit --author override, else the per-user handle, else
        # _operator_handle (power-user fallback, byte-identical to pre-team behavior).
        author = getattr(args, "author", None)
        if author is not None:
            author = routing._normalize_user_handle(author) or None
        if not author:
            author = routing.resolve_user_handle()  # None => build_entry_frontmatter uses _operator_handle
        fm = frontmatter.build_entry_frontmatter(
            {"id": eid, "title": title, "category": "decisions", "tags": tag_list,
             "created": created, "summary": args.summary},
            kdir, author=author, source=frontmatter.DEFAULT_ENTRY_SOURCE)
        os.makedirs(index.decisions_dir(kdir), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(curation._render_learning(fm, body, back_label="Decisions"))

        entry, add_err = index._do_add(
            kdir, data, eid, title, "decisions", rel_path, tag_list,
            args.summary, created)
        if add_err is not None:
            try:
                os.remove(abs_path)
            except OSError:
                pass
            sys.stderr.write("agentware: %s\n" % add_err)
            return 1

        index.save_index(kdir, data)
        if getattr(args, "format", "text") == "json":
            json.dump({"ok": True, "decision": entry}, sys.stdout,
                      ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            print("DECISION entry '%s' -> %s (registered in index)" % (eid, abs_path))
        return 0


def register_learn(sub, fmt_parent):
    # learn ------------------------------------------------------------------
    p_learn = sub.add_parser(
        "learn", parents=[fmt_parent], help="create + register a learning file")
    p_learn.add_argument("--topic", required=True, help="filename stem under learnings/")
    p_learn.add_argument("--summary", required=True, help="one-line summary")
    p_learn.add_argument("--tags", required=True, help="comma-separated tags")
    p_learn.add_argument("--content", required=True,
                         help="learning body, or '-' to read from stdin")
    p_learn.add_argument("--author", default=None,
                         help="optional provenance author handle (default: the "
                              "per-user handle if set, else the operator handle). "
                              "For shared/team KBs.")
    p_learn.add_argument("--source", default=None,
                         help="optional provenance source: user|agent|imported "
                              "(default: agent). Feeds the ACR source_weight prior.")
    p_learn.add_argument("--to-shared", dest="to_shared", action="store_true",
                         help="write org-wide into shared/ (curator only, CR-gated); "
                              "default writes go to the caller's team.")
    p_learn.add_argument("--force", action="store_true",
                         help="record even if a near-duplicate existing learning "
                              "is detected")
    p_learn.add_argument("--title", default=None,
                         help="optional display title (default: derived from "
                              "--topic via title-casing)")
    p_learn.set_defaults(func=cmd_learn)


def register_decide(sub, fmt_parent):
    # decide -----------------------------------------------------------------
    p_decide = sub.add_parser(
        "decide", parents=[fmt_parent],
        help="record + register an autonomous decision (R-AUTO-03)")
    p_decide.add_argument("--topic", required=True,
                          help="hyphenated slug; filename stem under decisions/")
    p_decide.add_argument("--summary", required=True, help="one-line summary")
    p_decide.add_argument("--options", required=True,
                          help="options that were weighed")
    p_decide.add_argument("--choice", required=True, help="the option chosen")
    p_decide.add_argument("--rationale", required=True,
                          help="why this choice, in-scope per R-AUTO-01")
    p_decide.add_argument("--reversible", required=True,
                          help="how reversible the decision is")
    p_decide.add_argument("--tags", default=None,
                          help="comma-separated extra tags ('decision' auto-added)")
    p_decide.add_argument("--author", default=None,
                          help="provenance author handle (default: the per-user "
                               "handle if set, else the operator handle)")
    p_decide.add_argument("--to-shared", dest="to_shared", action="store_true",
                          help="write org-wide into shared/ (curator only, CR-gated)")
    p_decide.set_defaults(func=cmd_decide)


_EXPORTS = [
    "cmd_learn",
    "cmd_decide",
]
