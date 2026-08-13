"""agentware_lib.features.setupcmd — the `init` + `profile` command surface.

Owns: cmd_init (scaffold the external knowledge dir + seed index + write config)
      and cmd_profile (resolve a per-user profile overlay path), plus their parser
      registrars register_init / register_profile.
Does NOT own: the config WRITERS (_write_config, *_KEY) -> agentware_lib.core.config;
      the KB-layout primitives (NON_INDEXED_DIRS, _ensure_kb_gitignore) ->
      agentware_lib.core.kblayout; the `config` handler -> sibling
      agentware_lib.features.configcmd.
Layer deps: features.setupcmd -> core (config, paths, kblayout), knowledge (index)
      -- all DOWNWARD for the features layer. Nothing imports setupcmd except
      cli/the entrypoint facade.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

`init` scaffolds the operator's external knowledge dir (dirs, seed index, category
rosters, installed templates, KB .gitignore) and persists the config pointer;
`profile` resolves the path to a per-user profile overlay. Both are workspace-
setup/operator-identity commands, distinct from the `config` settings surface
(configcmd). The registrars travel with their handlers (set_defaults(func=cmd_*)
resolves cmd_* in this module's globals).

Changes to the moved lines vs the monolith (all mechanical, behavior-preserving),
all in cmd_init: bare REPO_ROOT -> paths.REPO_ROOT (1x) -- the entrypoint's own
REPO_ROOT and paths.REPO_ROOT resolve the SAME package root byte-identically
(dirname vs the AGENTS.md marker-walk) and cmd_init's REPO_ROOT is never
monkeypatched (the only REPO_ROOT patch sites are regressiongate's A/B and
staging's kb-git C-3 check) -- the skills.py precedent; the cross-module function
_ensure_kb_gitignore( -> kblayout._ensure_kb_gitignore( (1x) and the constant
NON_INDEXED_DIRS -> kblayout.NON_INDEXED_DIRS (2x), the module-alias form the
global-resolve gate requires for a function whose global closure lives elsewhere.
"""

import json
import os
import shutil
import sys

import agentware_lib.core.config as config
import agentware_lib.core.kblayout as kblayout
import agentware_lib.core.paths as paths
import agentware_lib.knowledge.index as index


def cmd_init(args):
    """Scaffold the external knowledge dir + seed an empty index + write config.

    Idempotent: re-running against an existing dir leaves entries intact and
    only fills in any missing structure. Does NOT write the .initialized
    sentinel — onboarding writes that after the interview.
    """
    kdir = os.path.abspath(os.path.expanduser(args.knowledge_dir))
    as_json = getattr(args, "format", "text") == "json"

    # KNOWLEDGE_SUBDIRS are indexed knowledge categories; kblayout.NON_INDEXED_DIRS are
    # the operator's work artifacts (plans/worklogs) and append-only logs that
    # the orchestrator writes here INSTEAD of into the package.
    created_dirs = []
    for sub in ("",) + index.KNOWLEDGE_SUBDIRS + kblayout.NON_INDEXED_DIRS:
        d = os.path.join(kdir, sub) if sub else kdir
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            created_dirs.append(d)

    # Seed an empty valid index only if absent (never clobber real data).
    ipath = index.index_path(kdir)
    seeded = False
    if not os.path.exists(ipath):
        index.save_index(kdir, {"entries": [], "tags": {}})
        seeded = True

    # Drop a category roster placeholder so each subdir is browsable.
    for sub in index.KNOWLEDGE_SUBDIRS:
        roster = os.path.join(kdir, sub, "index.md")
        if not os.path.exists(roster):
            with open(roster, "w", encoding="utf-8") as f:
                f.write("# %s\n\n_Index of %s. Entries are registered in "
                        "`../index.json` via `scripts/agentware`._\n"
                        % (sub.capitalize(), sub))

    # Install the entry templates INTO the operator's dir so their working
    # instance is self-contained and they never have to reach back into the
    # package to scaffold a learning / project / skill entry.
    installed_templates = []
    src_templates = os.path.join(paths.REPO_ROOT, "templates")
    dst_templates = os.path.join(kdir, "templates")
    if os.path.isdir(src_templates):
        os.makedirs(dst_templates, exist_ok=True)
        for fn in sorted(os.listdir(src_templates)):
            if not fn.endswith(".md"):
                continue
            dst = os.path.join(dst_templates, fn)
            if not os.path.exists(dst):
                shutil.copyfile(os.path.join(src_templates, fn), dst)
                installed_templates.append(fn)

    # Scaffold the KB .gitignore so transcripts (logs/) are never committed/pushed
    # once autocommit is ON by default.
    gitignore_action = kblayout._ensure_kb_gitignore(kdir)

    # Guard: refuse to overwrite an existing HOME_CONFIG pointing elsewhere unless
    # --force is passed. Idempotent no-op (same dir) is always allowed; first-run
    # (no config) is always allowed. This prevents accidental config-clobber when
    # test harnesses or concurrent sessions run `init --knowledge-dir <scratch>`.
    #
    # The TRIGGER is "the config already points somewhere else", NOT "and that
    # somewhere else carries the .initialized sentinel": a config written by a
    # half-finished onboarding still names the operator's chosen dir, and
    # silently repointing it is the same clobber. The guard REPORTS whether the
    # configured dir is a conformant knowledge base (kblayout.kb_pointable) so a
    # caller can tell "you are about to abandon a live KB" from "you are about to
    # abandon a scaffold", but it refuses either way without --force.
    force = getattr(args, "force", False)
    if os.path.isfile(config.HOME_CONFIG) and not force:
        existing = config._persisted_knowledge_dir()
        if existing and os.path.abspath(existing) != os.path.abspath(kdir):
            pointable, reason = kblayout.kb_pointable(existing)
            sys.stderr.write(
                "agentware init: refusing to repoint an already-configured "
                "knowledge base.\n"
                "  currently configured: %s (%s)\n"
                "  requested:            %s\n"
                "Your %s was left unchanged. Re-run with --force to repoint it.\n"
                % (existing, "conformant knowledge base" if pointable else reason,
                   kdir, config.HOME_CONFIG))
            if as_json:
                json.dump({"ok": False, "error": "kb-already-configured",
                           "configured_dir": existing, "requested_dir": kdir,
                           "configured_dir_pointable": pointable,
                           "configured_dir_reason": reason,
                           "config": config.HOME_CONFIG,
                           "hint": "pass --force to repoint"},
                          sys.stdout, ensure_ascii=False, indent=2)
                sys.stdout.write("\n")
            return 3

    config._write_config(kdir)

    if as_json:
        json.dump({"ok": True, "knowledge_dir": kdir, "seeded_index": seeded,
                   "created_dirs": created_dirs, "installed_templates": installed_templates,
                   "gitignore": gitignore_action, "config": config.HOME_CONFIG},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("INITIALIZED knowledge dir: %s" % kdir)
        print("  index:  %s%s" % (ipath, " (seeded)" if seeded else " (kept)"))
        print("  config: %s" % config.HOME_CONFIG)
        print("  work:   %s/work/<feature>/   logs: %s/logs/" % (kdir, kdir))
        print("  .gitignore: %s (logs/ + .loop/ excluded — transcripts & loop "
              "state never committed)" % gitignore_action)
        if installed_templates:
            print("  templates installed: %s" % ", ".join(installed_templates))
        print("Next: run onboarding (.claude/skills/onboarding/SKILL.md) to "
              "personalize MAIN.md and write the .initialized sentinel.")
    return 0


def cmd_profile(args):
    """Resolve the path to a per-user profile overlay in the knowledge dir.

    Usage: scripts/agentware profile --path <handle>
    Prints the absolute path to <kdir>/profiles/<handle>.md (the file may or may
    not exist yet — this is a resolver, not a validator). Requires the knowledge
    dir to be configured.
    """
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    handle = args.handle
    if not handle or not handle.strip():
        sys.stderr.write("agentware: profile --path requires a non-empty handle\n")
        return 2
    handle = handle.strip()
    profile_path = os.path.join(kdir, "profiles", "%s.md" % handle)
    as_json = getattr(args, "format", "text") == "json"
    if as_json:
        json.dump({"handle": handle, "path": profile_path,
                   "exists": os.path.isfile(profile_path)},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(profile_path)
    return 0


#: Verb declarations — the ONE place these verbs are registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("init", 100, "register_init"),
    ("profile", 300, "register_profile"),
)


def register_init(sub, fmt_parent):
    # init -------------------------------------------------------------------
    p_init = sub.add_parser(
        "init", parents=[fmt_parent],
        help="scaffold the external knowledge dir + seed index + write config")
    p_init.add_argument("--knowledge-dir", required=True, dest="knowledge_dir",
                        help="absolute or ~ path to store this operator's knowledge")
    p_init.add_argument("--force", action="store_true", default=False,
                        help="overwrite an existing HOME config pointing elsewhere")
    p_init.set_defaults(func=cmd_init)


def register_profile(sub, fmt_parent):
    # profile ----------------------------------------------------------------
    p_profile = sub.add_parser(
        "profile", parents=[fmt_parent],
        help="resolve the path to a per-user profile overlay")
    p_profile.add_argument("--path", required=True, dest="handle",
                           help="handle of the user whose profile path to resolve")
    p_profile.set_defaults(func=cmd_profile)


_EXPORTS = ["cmd_init", "cmd_profile"]

__all__ = list(_EXPORTS)
