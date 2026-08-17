"""agentware_lib.features.curationcmd -- the learn/decide command surface (layer: features).

Owns: the `learn` and `decide` CLI command handlers (cmd_learn / cmd_decide) and
their argparse registrars (register_learn / register_decide) -- the orchestrating
write-commands that validate input, guard shared-writes, render the entry body,
write the file, and register it in the index.

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


def cmd_learn(args):
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    # Shared CR-gate: a default team-loop write into shared/ is
    # refused unless --to-shared by a curator. No-op for team/flat writes.
    ok, gerr = _fleet.guard_shared_write(kdir, to_shared=getattr(args, "to_shared", False))
    if not ok:
        sys.stderr.write("agentware: %s\n" % gerr)
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
            src = frontmatter.DEFAULT_ENTRY_SOURCE
        author = getattr(args, "author", None)
        if author is not None:
            author = routing._normalize_user_handle(author)
            if not author:
                sys.stderr.write(
                    "agentware: invalid --author (must contain at least one of "
                    "A-Z a-z 0-9 . _ -)\n")
                return 1
        else:
            author = routing.resolve_user_handle()  # None => build_entry_frontmatter uses _operator_handle

        eid = "learn-%s" % topic
        rel_path = "%s/%s.md" % (index.LEARNINGS_REL, topic)
        abs_path = os.path.join(kdir, rel_path)
        tag_list = index.parse_tags(args.tags)
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
                "agentware: learning file already exists (refusing to overwrite): %s\n"
                % abs_path)
            return 1

        fm = frontmatter.build_entry_frontmatter(
            {"id": eid, "title": title, "category": "learnings", "tags": tag_list,
             "created": created, "summary": args.summary},
            kdir, author=author, source=src)
        os.makedirs(index.learnings_dir(kdir), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(curation._render_learning(fm, body))

        entry, add_err = index._do_add(
            kdir, data, eid, title, "learnings", rel_path, tag_list, args.summary, created)
        if add_err is not None:
            try:
                os.remove(abs_path)
            except OSError:
                pass
            sys.stderr.write("agentware: %s\n" % add_err)
            return 1

        index.save_index(kdir, data)

        # Advisory capture-quality hint (non-blocking): if body references an artifact
        # keyword but contains no fenced code block, nudge the caller to embed verbatim.
        _ARTIFACT_KEYWORDS = re.compile(
            r'\b(config|command|yaml|error|endpoint|run )', re.IGNORECASE)
        if _ARTIFACT_KEYWORDS.search(body) and '```' not in body:
            sys.stderr.write(
                "agentware: hint: this learning mentions an artifact but embeds no "
                "code block — consider re-running with the artifact pasted inside a "
                "fenced ``` block so future agents can embed the artifact verbatim.\n")

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
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    ok, gerr = _fleet.guard_shared_write(kdir, to_shared=getattr(args, "to_shared", False))
    if not ok:
        sys.stderr.write("agentware: %s\n" % gerr)
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
