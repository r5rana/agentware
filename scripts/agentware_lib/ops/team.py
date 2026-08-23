"""agentware_lib.ops.team -- the org `team` domain: registry.json access + onboarding.

Owns: (1) the org team-membership registry primitives -- the registry path resolver
(registry_path), the tolerant read-only parser (load_registry), the O5 membership
validation (registry_membership_errors: well-formed + UNIQUE (user, team) rows +
known-team refs), and the idempotent member-add mutation (_registry_add_member); and
(2) the team onboarding surface (moved in wave-6 seam 17, once NON_INDEXED_DIRS/
_ensure_kb_gitignore landed in core.kblayout): the canonical-structure scaffolder
_scaffold_team_dir, its `team list|create|register` handler cmd_team, and the `team`
parser registrar register_team.

Does NOT own: the org-invariant lint (lint_org / cmd_org_lint, still RESIDENT in
scripts/agentware -- a later seam); the fleet telemetry/reporter + org/template lint +
the fleet org-root resolver _fleet_org_root (ops.fleet); and fleet provenance emit
(emit_onboarding_metric -> ops.metrics).

Layer deps: ops.team -> core (config/dates/kblayout/orgkeys/routing) DOWNWARD +
knowledge.index (index_path/save_index/KNOWLEDGE_SUBDIRS) DOWNWARD + the SAME-LAYER
ops.fleet (imported as _fleet, for _fleet._fleet_org_root -- acyclic: fleet does NOT
import team). Nothing above ops imports team.

Determinism: read-only parse (INV-2 tolerant) for the readers; _registry_add_member is
an idempotent JSON mutation of teams/registry.json (deterministic, sort_keys writes);
_scaffold_team_dir is idempotent (makedirs exist_ok, never clobbers).

_EXPORTS is imported BY NAME onto the scripts/agentware facade so the resident org-lint
consumer + the moved cmd_team/_scaffold_team_dir resolve the registry names as bare names
and load_cli tests reach them as CLI.<name>. register_team is NOT exported (it is declared
in this module's `__verbs__`).
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import os
import sys
import time

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.core.kblayout as kblayout
import agentware_lib.core.orgkeys as orgkeys
import agentware_lib.core.routing as routing
import agentware_lib.knowledge.index as index
import agentware_lib.ops.fleet as _fleet


def registry_path(org_root):
    """Absolute path to teams/registry.json under org_root."""
    return os.path.join(org_root, orgkeys.ORG_REGISTRY_REL)


def load_registry(org_root):
    """Parse teams/registry.json -> dict, or a fresh empty registry. Tolerant.

    Returns {"version":1, "teams":[...], "members":[...]}. A missing/invalid file
    yields the empty shape so callers can create the first team. Read-only.
    """
    p = registry_path(org_root)
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("version", 1)
            d.setdefault("teams", [])
            d.setdefault("members", [])
            return d
    except (OSError, ValueError):
        pass
    return {"version": 1, "teams": [], "members": []}


def registry_membership_errors(reg):
    """O5 validation: well-formed + UNIQUE (user, team) rows. Returns [errors]."""
    errs = []
    seen = set()
    for m in reg.get("members") or []:
        if not isinstance(m, dict) or "user" not in m or "team" not in m:
            errs.append("malformed member row: %r" % (m,))
            continue
        key = (m["user"], m["team"])
        if key in seen:
            errs.append("duplicate (user, team) row: %s" % (key,))
        seen.add(key)
    team_names = {t.get("name") for t in (reg.get("teams") or []) if isinstance(t, dict)}
    for m in reg.get("members") or []:
        if isinstance(m, dict) and m.get("team") and m["team"] not in team_names:
            errs.append("member %s references unknown team %s"
                        % (m.get("user"), m.get("team")))
    return errs


def _registry_add_member(org_root, user, team, max_retries=8):
    """Append a UNIQUE (user, team) row to teams/registry.json (idempotent).

    Concurrent-safe: retries with exponential backoff + jitter on a write race so
    two engineers registering the same new team both land without conflict markers
    (R5-style). Returns (ok, note). Deterministic jitter (index-derived, no RNG).
    """
    for attempt in range(max_retries):
        reg = load_registry(org_root)
        # Ensure the team is listed.
        if not any(t.get("name") == team for t in reg["teams"]):
            reg["teams"].append({"name": team,
                                 "path": "%s/%s" % (orgkeys.ORG_TEAMS_DIR, team),
                                 "created": dates._today()})
        # Idempotent membership.
        if any(m.get("user") == user and m.get("team") == team
               for m in reg["members"]):
            return True, "already a member"
        reg["members"].append({"user": user, "team": team})
        errs = registry_membership_errors(reg)
        if errs:
            return False, "; ".join(errs)
        p = registry_path(org_root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            # Write atomically (tmp + rename) so a concurrent reader never sees a
            # half-written file.
            tmp = p + ".tmp.%d" % attempt
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(reg, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp, p)
            return True, "registered"
        except OSError as e:
            # Backoff + deterministic jitter (attempt-derived), then retry.
            time.sleep(min(0.05 * (2 ** attempt) + 0.01 * attempt, 1.0))
            last = str(e)
    return False, "registry write failed after %d attempts: %s" % (max_retries, last)


def _scaffold_team_dir(org_root, team):
    """Create teams/<team>/ with the canonical KB structure (idempotent).

    Reuses the init subdir set (KNOWLEDGE_SUBDIRS + work/logs/profiles) + prompts/
    references (attach-conformance dirs) + fleet/. NEVER clobbers existing entries.
    Returns the absolute team dir path.
    """
    tdir = os.path.join(org_root, orgkeys.ORG_TEAMS_DIR, team)
    for sub in ("",) + index.KNOWLEDGE_SUBDIRS + kblayout.NON_INDEXED_DIRS + (orgkeys.ORG_FLEET_DIR, "benchmarks"):
        d = os.path.join(tdir, sub) if sub else tdir
        os.makedirs(d, exist_ok=True)
    # Seed a MAIN.md stub if absent (team profile — injected at session start).
    main = os.path.join(tdir, "MAIN.md")
    if not os.path.exists(main):
        with open(main, "w", encoding="utf-8") as f:
            f.write("# %s — Team Knowledge Base (MAIN)\n\n"
                    "> Team profile + active-work table. Injected at session start.\n\n"
                    "## Team members\n\n| Handle | Role |\n|--------|------|\n\n"
                    "## Active work\n\n| Feature | Status | Notes |\n|---------|--------|-------|\n"
                    % team)
    kblayout._ensure_kb_gitignore(tdir)
    # Seed an empty index if absent (rebuild will regenerate).
    if not os.path.exists(index.index_path(tdir)):
        index.save_index(tdir, {"entries": [], "tags": {}})
    return tdir


def _team_org_root():
    """The org root a `team` subcommand acts in, or None when there is none.

    Two sources in one fixed order: the explicit org key first, then the
    fleet resolver walking UP from the caller's knowledge dir — so an operator
    already standing inside a shard does not have to re-export the root.
    """
    return config.resolve_org_root() or _fleet._fleet_org_root(
        config.require_knowledge_dir() or "")


def _team_caller_handle(args):
    """The caller handle a `create`/`register` row is written for, or None.

    Three sources in one fixed order (`--handle`, the resolver, `$USER`), then
    the SAME normalisation the registry's own reader applies — so a handle typed
    on the command line and one resolved from the environment can never produce
    two different member rows for one person.
    """
    handle = (getattr(args, "handle", None) or routing.resolve_user_handle()
              or os.environ.get("USER"))
    return routing._normalize_user_handle(handle) if handle else None


def _team_list(reg, as_json):
    """Render `team list` from an ALREADY-PARSED registry. Returns the exit code.

    Takes the parsed registry rather than the org root because rendering is not
    entitled to decide how the registry is read: `load_registry` is tolerant by
    contract (INV-2), and a renderer that re-read it could disagree with the
    caller about what "the registry" is.
    """
    if as_json:
        json.dump(reg, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    print("teams (%d):" % len(reg.get("teams") or []))
    for t in reg.get("teams") or []:
        members = [m["user"] for m in reg.get("members") or []
                   if m.get("team") == t.get("name")]
        print("  %-20s %s" % (t.get("name"), ", ".join(sorted(members)) or "(no members)"))
    return 0


def _team_join(args, sub, org_root, handle, as_json):
    """The shared `create`/`register` arm: guards, the registry mutation, the
    config persist and the two-mode emit. Returns the exit code.

    `create` and `register` are ONE body because they differ in exactly one
    decision — whether a team that does not exist yet is scaffolded or refused —
    and every other step (the member add, the config persist, the emit) is
    identical. Splitting them would duplicate four steps to avoid one branch.
    """
    team = getattr(args, "name", None)
    if not team:
        sys.stderr.write("agentware team %s: --name required\n" % sub)
        return 1
    if not handle:
        sys.stderr.write("agentware team %s: no handle (pass --handle or set "
                         "AGENTWARE_USER_HANDLE)\n" % sub)
        return 1
    reg = load_registry(org_root)
    exists = any(t.get("name") == team for t in reg["teams"])
    if sub == "register" and not exists:
        sys.stderr.write("agentware team register: team %r does not exist "
                         "(use `team create`)\n" % team)
        return 1
    if sub == "create" and not exists:
        _scaffold_team_dir(org_root, team)
    ok, note = _registry_add_member(org_root, handle, team)
    if not ok:
        sys.stderr.write("agentware team %s: %s\n" % (sub, note))
        return 1
    # Persist AGENTWARE_TEAM to machine-local config so the resolver points here.
    if getattr(args, "set_default", True):
        config._set_config_value(orgkeys.ORG_TEAM_KEY, team)
        config._set_config_value(orgkeys.ORG_ROOT_KEY, org_root)
    if as_json:
        json.dump({"team": team, "handle": handle, "action": sub, "note": note},
                  sys.stdout)
        sys.stdout.write("\n")
    else:
        print("team %s: %s -> %s (%s)" % (sub, handle, team, note))
    return 0


def cmd_team(args):
    """`agentware team list|create|register` — org team onboarding (Task 6).

    list     — print the teams/registry.json membership (a plain co-resident read).
    create   — scaffold teams/<name>/ + append a registry row + persist AGENTWARE_TEAM.
    register — add the caller (handle) to an EXISTING team (JOIN); never re-scaffolds.

    A DISPATCH and nothing else: it resolves the org root every subcommand needs,
    picks the arm, and returns what the arm returns. The handle is resolved on the
    way to the mutating arms and NOT before the `list` arm, because reading the
    registry is not entitled to an identity.
    """
    sub = getattr(args, "team_cmd", None)
    org_root = _team_org_root()
    if not org_root:
        sys.stderr.write("agentware team: no org root (set AGENTWARE_ORG_ROOT)\n")
        return 1
    as_json = getattr(args, "format", "text") == "json"

    if sub == "list":
        return _team_list(load_registry(org_root), as_json)

    handle = _team_caller_handle(args)

    if sub in ("create", "register"):
        return _team_join(args, sub, org_root, handle, as_json)

    sys.stderr.write("agentware team: unknown subcommand (list|create|register)\n")
    return 1


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("team", 2500, "register_team"),
)


def register_team(sub, fmt_parent):
    # team -------------------------------------------------------------------
    # Org team onboarding: list/create/register against the
    # co-resident teams/registry.json.
    p_team = sub.add_parser(
        "team", parents=[fmt_parent],
        help="org team onboarding: list | create | register")
    team_sub = p_team.add_subparsers(dest="team_cmd")
    p_team_list = team_sub.add_parser("list", parents=[fmt_parent],
                                      help="list teams + members from teams/registry.json")
    p_team_list.set_defaults(func=cmd_team)
    p_team_create = team_sub.add_parser("create", parents=[fmt_parent],
                                        help="scaffold a NEW team folder + register the caller")
    p_team_create.add_argument("--name", required=True, help="team folder name")
    p_team_create.add_argument("--handle", default=None, help="caller handle (default: resolved)")
    p_team_create.add_argument("--no-set-default", dest="set_default",
                               action="store_false", default=True,
                               help="do NOT persist AGENTWARE_TEAM/ORG_ROOT to config")
    p_team_create.set_defaults(func=cmd_team)
    p_team_register = team_sub.add_parser("register", parents=[fmt_parent],
                                          help="JOIN an existing team (adds the caller)")
    p_team_register.add_argument("--name", required=True, help="existing team name")
    p_team_register.add_argument("--handle", default=None, help="caller handle")
    p_team_register.add_argument("--no-set-default", dest="set_default",
                                 action="store_false", default=True,
                                 help="do NOT persist AGENTWARE_TEAM/ORG_ROOT to config")
    p_team_register.set_defaults(func=cmd_team)


_EXPORTS = [
    'registry_path',
    'load_registry',
    'registry_membership_errors',
    '_registry_add_member',
    '_scaffold_team_dir',
    'cmd_team',
]
