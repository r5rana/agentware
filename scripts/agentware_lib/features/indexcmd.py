"""agentware_lib.features.indexcmd -- the `index` verb command family (layer: features).

Owns: the six `index` sub-command handlers (cmd_index_add / cmd_index_remove /
cmd_index_validate / cmd_index_sync / cmd_index_migrate / cmd_index_rebuild) and
their argparse registrar (register_index) -- the orchestrating CLI commands that
load/validate/mutate index.json, backfill entry frontmatter, report/fix unindexed
files, and rebuild the derived KB (index.json + rosters + FEATURES.md + vector cache).

Does NOT own: the index CRUD/validate/rebuild primitives (_do_add / save_index /
load_index / validate_index / roster_injection_errors / scan_unindexed / rebuild_kb /
_infer_category / _suggest_id / _suggest_title / parse_tags / remove_tags_from_map /
features_path + CATEGORIES) -> agentware_lib.knowledge.index; the derived vector-cache
rebuild (rebuild_vector_cache) -> agentware_lib.retrieval.vectorcache; the loop-run
proposal-integrity guard (proposal_integrity_errors) -> agentware_lib.knowledge.proposals;
the frontmatter backfill (ensure_entry_frontmatter / DEFAULT_ENTRY_SOURCE /
_operator_handle) -> agentware_lib.core.frontmatter; per-user provenance
(resolve_user_handle) -> agentware_lib.core.routing.

Layer deps: features.indexcmd -> core (config, frontmatter, routing) + knowledge
(index, proposals) + retrieval (vectorcache) -- ALL DOWNWARD for the features layer.
The retrieval read (cmd_index_rebuild -> vectorcache.rebuild_vector_cache) is exactly
why these HANDLERS cannot live in knowledge.index with the CRUD primitives:
knowledge->retrieval is an UPWARD edge M12 forbids (ast.walk records it even lazily),
so the orchestrating handlers belong in features (mirrors curationcmd / configcmd)
while knowledge.index stays a clean knowledge-layer module.

A LEAF: nothing imports it at module level (fan-in 0); the scripts/agentware
entrypoint re-exports the 6 cmd_index_* handlers BY NAME so the legacy load_cli oracle
(CLI.cmd_index_*) and the global-resolve gate resolve them, and register_index is
declared in this module's `__verbs__` and resolved from there by
agentware_lib.cli.assemble_parser.
The proposal_integrity_errors / rebuild_vector_cache cross-module functions are reached
via the proposals / vectorcache MODULE aliases (never by-name imports, whose foreign
__code__ the global-resolve gate would walk against this namespace and FAIL).

Determinism: read-mostly EXCEPT the explicit index writes, which go through
index.save_index / index._do_add (the sole index writer, R-KB-01) and the derived-KB
rebuild through index.rebuild_kb (the sole fingerprint + BM25-cache writer, INV-2).
INV-1 deterministic.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os
import sys

import agentware_lib.core.config as config
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.core.routing as routing
import agentware_lib.knowledge.index as index
import agentware_lib.knowledge.proposals as proposals
import agentware_lib.retrieval.vectorcache as vectorcache


def cmd_index_add(args):
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    with index._index_lock(kdir):  # serialize load->mutate->save (B2 lost-update guard)
        data, err = index.load_index(kdir)
        if err is not None:
            sys.stderr.write("agentware: %s\n" % err)
            return 1

        entry, add_err = index._do_add(
            kdir, data, args.id, args.title, args.category, args.path,
            index.parse_tags(args.tags), args.summary, args.created,
            getattr(args, "last_verified", None))
        if add_err is not None:
            sys.stderr.write("agentware: %s\n" % add_err)
            return 1

        # Ensure the registered (pre-existing) file is frontmatter-complete: if it
        # lacks a frontmatter block, prepend one built from the entry + defaults so
        # the file remains the rebuildable source of truth (C-1). Never overwrite an
        # existing block (idempotent); never edit the body.
        # Resolve per-user author for the frontmatter (team-mode provenance).
        _add_author = routing.resolve_user_handle()  # None => ensure_entry_frontmatter uses _operator_handle
        backfilled = frontmatter.ensure_entry_frontmatter(kdir, entry, author=_add_author)

        index.save_index(kdir, data)
        if getattr(args, "format", "text") == "json":
            json.dump({"ok": True, "added": entry, "frontmatter_backfilled": backfilled},
                      sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            print("ADDED entry '%s' (category=%s, path=%s)%s"
                  % (args.id, args.category, args.path,
                     " [frontmatter backfilled]" if backfilled else ""))
        return 0


def cmd_index_remove(args):
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    with index._index_lock(kdir):  # serialize load->mutate->save (B2 lost-update guard)
        data, err = index.load_index(kdir)
        if err is not None:
            sys.stderr.write("agentware: %s\n" % err)
            return 1

        entries = data.setdefault("entries", [])
        tags_map = data.setdefault("tags", {})
        match = next((e for e in entries if e.get("id") == args.id), None)
        if match is None:
            sys.stderr.write("agentware: id not found: %s\n" % args.id)
            return 1

        entries[:] = [e for e in entries if e.get("id") != args.id]
        index.remove_tags_from_map(tags_map, args.id)
        index.save_index(kdir, data)
        if getattr(args, "format", "text") == "json":
            json.dump({"ok": True, "removed": match}, sys.stdout,
                      ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            print("REMOVED entry '%s' (file on disk left untouched)" % args.id)
        return 0


def cmd_index_validate(args):
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    data, err = index.load_index(kdir)
    if err is not None:
        errors = [err]
    else:
        errors = index.validate_index(kdir, data)
        # Durable roster/injection lint (feature 260722, Task 4): fold into the
        # gate `run_pre_hooks` already runs so the Part-1 injection bugs can
        # never silently regress. Only meaningful when the base index is valid.
        if not errors:
            errors = index.roster_injection_errors(kdir, data)
        # Loop-run proposal-integrity guard (feature 260722, Task 10 / M2):
        # FAIL on a `skills-proposed/` leak into the skills roster, a malformed
        # `.jsonl` ledger, or a stored count that disagrees with unique(refs).
        # Only meaningful when the base index is valid.
        if not errors:
            errors = proposals.proposal_integrity_errors(kdir, data)
    ok = len(errors) == 0
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": ok, "errors": errors}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        if ok:
            print("VALIDATION PASSED: index.json is consistent.")
        else:
            print("VALIDATION FAILED (%d error(s)):" % len(errors))
            for e in errors:
                print(" -", e)
    return 0 if ok else 1


def cmd_index_sync(args):
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    with index._index_lock(kdir):  # serialize load->mutate->save (B2 lost-update guard)
        data, err = index.load_index(kdir)
        if err is not None:
            sys.stderr.write("agentware: %s\n" % err)
            return 1

        candidates = index.scan_unindexed(kdir, data)
        as_json = getattr(args, "format", "text") == "json"

        if not args.fix:
            report = [{"path": p, "suggested_id": index._suggest_id(p),
                       "suggested_category": index._infer_category(p)} for p in candidates]
            if as_json:
                json.dump({"unindexed": report, "count": len(report)},
                          sys.stdout, ensure_ascii=False, indent=2)
                sys.stdout.write("\n")
            else:
                if not report:
                    print("SYNC: no unindexed *.md files in the knowledge dir.")
                else:
                    print("SYNC: %d unindexed *.md file(s) (dry-run; pass --fix):"
                          % len(report))
                    for r in report:
                        print("  %s  ->  id=%s category=%s"
                              % (r["path"], r["suggested_id"], r["suggested_category"]))
            return 0

        added, failures = [], []
        interactive = sys.stdin.isatty()
        for path in candidates:
            category = index._infer_category(path)
            eid = index._suggest_id(path)
            title = index._suggest_title(path)
            if interactive:
                tags_in = input("tags for %s (comma-sep) [%s]: " % (path, category)).strip() \
                    or category
                summary_in = input("summary for %s [%s]: " % (path, title)).strip() \
                    or title
            else:
                tags_in = category
                summary_in = title
            entry, add_err = index._do_add(
                kdir, data, eid, title, category, path, index.parse_tags(tags_in), summary_in)
            if add_err is not None:
                failures.append({"path": path, "error": add_err})
            else:
                added.append(entry)

        if added:
            index.save_index(kdir, data)
        if as_json:
            json.dump({"ok": not failures, "added": added, "failures": failures},
                      sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            for e in added:
                print("ADDED entry '%s' (category=%s, path=%s)"
                      % (e["id"], e["category"], e["path"]))
            for f in failures:
                sys.stderr.write("agentware: could not add %s: %s\n"
                                 % (f["path"], f["error"]))
            if not added and not failures:
                print("SYNC: nothing to fix; all *.md files already indexed.")
        return 1 if failures else 0


def cmd_index_migrate(args):
    """One-time backfill: prepend frontmatter to every indexed entry file.

    Bootstraps the rebuildable source of truth (C-1): each entry's `.md` gets a
    frontmatter block derived from its `index.json` row + defaults
    (author=operator handle, source=agent, last_verified=created). PREPEND only —
    bodies are never edited. Idempotent: a file that already carries frontmatter
    is skipped, so re-running is a no-op (reports 0 changed).
    """
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    data, err = index.load_index(kdir)
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1

    author = frontmatter._operator_handle(kdir)
    migrated, skipped, missing = [], [], []
    for entry in data.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        rel = entry.get("path")
        if not rel or not os.path.exists(os.path.join(kdir, rel)):
            missing.append(rel)
            continue
        if frontmatter.ensure_entry_frontmatter(kdir, entry, author=author,
                                    source=frontmatter.DEFAULT_ENTRY_SOURCE):
            migrated.append(rel)
        else:
            skipped.append(rel)

    as_json = getattr(args, "format", "text") == "json"
    if as_json:
        json.dump({"ok": not missing, "migrated": migrated, "skipped": skipped,
                   "missing": missing, "migrated_count": len(migrated),
                   "skipped_count": len(skipped)},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("MIGRATE: %d file(s) gained frontmatter, %d already had it."
              % (len(migrated), len(skipped)))
        for p in migrated:
            print("  + %s" % p)
        for p in missing:
            sys.stderr.write("agentware: indexed path missing on disk: %s\n" % p)
    return 1 if missing else 0


def cmd_index_rebuild(args):
    """Regenerate index.json + `<sub>/index.md` rosters + FEATURES.md from frontmatter.

    The index is a DERIVED cache: this reconstructs it from the per-entry markdown
    frontmatter alone (C-1), deterministically and idempotently (C-2). It is the
    conflict-safe resolver for the derived files — never hand-edit them (C-3).
    """
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    data, rosters, errors = index.rebuild_kb(kdir)
    if errors:
        for e in errors:
            sys.stderr.write("agentware: rebuild: %s\n" % e)
        return 1

    # Mode-B half: rebuild the derived vector cache when a LOCAL embedder is
    # configured (sole writer, INV-2). A no-op when none is installed, so the
    # zero-install Mode-A default is unaffected. `index rebuild` here is the ONLY
    # caller — the git-sync throwaway-clone resolver uses rebuild_kb() directly
    # and never embeds/networks.
    vc = vectorcache.rebuild_vector_cache(kdir, data)

    if getattr(args, "format", "text") == "json":
        json.dump({"ok": True, "entries": len(data["entries"]),
                   "rosters": rosters, "features": index.features_path(kdir),
                   "vector_cache": vc},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("REBUILT index.json (%d entries) + %d roster(s) + FEATURES.md "
              "from frontmatter." % (len(data["entries"]), len(rosters)))
        if vc.get("status") == "built":
            print("REBUILT vector cache (model %s): %d embedded, %d reused, "
                  "%d total, dim %d." % (vc.get("model"), vc.get("embedded", 0),
                  vc.get("reused", 0), vc.get("total", 0), vc.get("dim", 0)))
        elif vc.get("status") == "error":
            sys.stderr.write("agentware: vector cache not rebuilt (%s); Mode A "
                             "unaffected.\n" % vc.get("reason"))
    return 0


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("index", 500, "register_index"),
)


def register_index(sub, fmt_parent):
    # index ------------------------------------------------------------------
    p_index = sub.add_parser("index", help="manage the knowledge index.json")
    index_sub = p_index.add_subparsers(dest="index_command", metavar="<subcommand>")
    index_sub.required = True

    p_add = index_sub.add_parser(
        "add", parents=[fmt_parent], help="register a new entry in index.json")
    p_add.add_argument("--id", required=True, help="unique entry id")
    p_add.add_argument("--title", required=True, help="human-readable title")
    p_add.add_argument("--category", required=True,
                       help="one of: %s" % ", ".join(index.CATEGORIES))
    p_add.add_argument("--path", required=True,
                       help="knowledge-dir-relative path (must exist)")
    p_add.add_argument("--tags", required=True, help="comma-separated tags")
    p_add.add_argument("--summary", required=True, help="one-line summary")
    p_add.add_argument("--created", default=None, help="YYYY-MM-DD (default: today)")
    p_add.add_argument("--last-verified", default=None,
                       help="YYYY-MM-DD freshness date (default: == created)")
    p_add.set_defaults(func=cmd_index_add)

    p_rm = index_sub.add_parser(
        "remove", parents=[fmt_parent], help="unregister an entry from index.json")
    p_rm.add_argument("--id", required=True, help="id of the entry to remove")
    p_rm.set_defaults(func=cmd_index_remove)

    p_val = index_sub.add_parser(
        "validate", parents=[fmt_parent], help="validate index.json integrity")
    p_val.set_defaults(func=cmd_index_validate)

    p_sync = index_sub.add_parser(
        "sync", parents=[fmt_parent], help="report (or --fix) unindexed *.md files")
    p_sync.add_argument("--fix", action="store_true",
                        help="add missing entries instead of just reporting")
    p_sync.set_defaults(func=cmd_index_sync)

    p_migrate = index_sub.add_parser(
        "migrate-frontmatter", parents=[fmt_parent],
        help="one-time: backfill YAML frontmatter into every indexed entry file")
    p_migrate.set_defaults(func=cmd_index_migrate)

    p_rebuild = index_sub.add_parser(
        "rebuild", parents=[fmt_parent],
        help="regenerate index.json + rosters + FEATURES.md from entry frontmatter")
    p_rebuild.set_defaults(func=cmd_index_rebuild)


_EXPORTS = [
    "cmd_index_add",
    "cmd_index_remove",
    "cmd_index_validate",
    "cmd_index_sync",
    "cmd_index_migrate",
    "cmd_index_rebuild",
]
