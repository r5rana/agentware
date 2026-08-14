"""agentware_lib.core.config.orgresolve — org-mode identity + path resolvers (layer: core).

Owns: WHICH org identity the caller declared and where it lands on disk —
resolve_org_root / resolve_team (env->config.env precedence for the org keys),
_resolve_org_project / resolve_org_project (current key, then the deprecated
spelling), org_project_identity_error (the source-of-truth guard that REFUSES a
mis-pointed org root), resolve_org_team_dir (the <org-root>/teams/<team>
composition resolve_knowledge_dir falls back to), and the path-inference
siblings _kdir_is_shared_root / _infer_team_from_kdir / _entry_origin_team
(given a resolved knowledge dir, infer its (origin, team) facet or shared-root
status from the org folder layout).
Does NOT own: the org KEY NAMES and layout constants (agentware_lib.core.orgkeys);
the project MARKER table and the pure directory-declares-project probe
(agentware_lib.core.orgidentity — this module owns only WHICH name to ask about);
the env->config precedence primitive it calls (agentware_lib.core.config.
configresolve._resolve_key_env_then_config); the knowledge-dir resolution that
composes with it (…config.configresolve); the org lint/team commands
(agentware_lib.ops.orglint, agentware_lib.ops.team).
Layer deps: core -> core only (imports agentware_lib.core.orgkeys + .orgidentity + the aggregator package; nothing above).
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Inert on a flat single-operator install: with the org keys unset every resolver
returns None and _entry_origin_team yields (None, None), so resolution is
byte-identical to a flat install (INV-1 / R-ORG-04). Sibling config symbols are
reached late-bound through the aggregator package (`config.<name>`) so the suite's
monkeypatches on agentware_lib.core.config.<name> keep propagating exactly as
they did before the split — and so this submodule carries no module-level edge to
configresolve, which calls back into resolve_org_team_dir.
"""

import os

import agentware_lib.core.config as config
import agentware_lib.core.orgidentity as orgidentity
import agentware_lib.core.orgkeys as orgkeys


def resolve_org_root():
    """Absolute AGENTWARE_ORG_ROOT (env→config), or None if unset. Additive."""
    v = config._resolve_key_env_then_config(orgkeys.ORG_ROOT_KEY)
    if v:
        return os.path.abspath(os.path.expanduser(v))
    return None


def resolve_team():
    """AGENTWARE_TEAM name (env→config), or None if unset. Additive."""
    v = config._resolve_key_env_then_config(orgkeys.ORG_TEAM_KEY)
    return v or None


# The marker table and the pure "does this dir declare <name>?" probe live in
# agentware_lib.core.orgidentity (a zero-dependency core leaf). WHICH name to ask
# about is a config question, so the two resolvers and the identity error stay
# here, where the env->config precedence lives.


def _resolve_org_project():
    """`(key, name)` for whichever org-project key is set, else `(None, None)`.

    The current key wins; the deprecated spelling is still READ so an operator
    whose config.env predates the rename keeps the guard instead of silently
    losing it.

    Written as two DIRECT calls rather than a loop over a key tuple on purpose:
    the env/config surface capture (tests/_env_config_scan.py) attributes a read
    site by resolving the literal constant passed to this resolver, and a loop
    variable resolves to nothing — so a loop here would erase BOTH keys from the
    env parity fixture while the code kept working. An invisible env read is the
    exact silent-loss class this migration exists to prevent."""
    value = config._resolve_key_env_then_config(orgkeys.ORG_PROJECT_KEY)
    if value and value.strip():
        return orgkeys.ORG_PROJECT_KEY, value.strip()
    value = config._resolve_key_env_then_config(orgkeys.ORG_PROJECT_LEGACY_KEY)
    if value and value.strip():
        return orgkeys.ORG_PROJECT_LEGACY_KEY, value.strip()
    return None, None


def resolve_org_project():
    """The declared org project name (env→config, current key then deprecated
    spelling), or None when neither is set. Additive."""
    return config._resolve_org_project()[1]


def org_project_identity_error(org_root):
    """Return an error string iff an org-project key is set AND `org_root` does
    NOT declare that project; else None. This is the source-of-truth guard: the
    project NAME (not a raw path) is authoritative, so a stray/scratch org_root
    that doesn't carry the expected identity is REJECTED loudly instead of
    silently used. No-op (None) when unset — flat installs are unaffected."""
    key, name = config._resolve_org_project()
    if not name:
        return None
    if not org_root:
        return "%s=%s is set but no org root resolves" % (key, name)
    if not orgidentity._dir_declares_project(org_root, name):
        return ("org root %s is NOT the expected project '%s' (none of its "
                "project markers declare it: %s) — refusing to use a mis-pointed "
                "org root. Fix AGENTWARE_ORG_ROOT to your %s checkout, or declare "
                "the name in %s."
                % (org_root, name, ", ".join(m for m, _ in orgidentity.PROJECT_MARKERS),
                   name, orgidentity.PROJECT_NAME_FILE))
    return None


def resolve_org_team_dir():
    """<org-root>/teams/<team> when BOTH org keys are set, else None.

    Pure path composition — does NOT require the dir to exist (the caller / hooks
    decide how to handle a not-yet-cloned team). Used ONLY as a fallback in
    resolve_knowledge_dir when AGENTWARE_KNOWLEDGE_DIR is unset (INV-1: the direct
    knowledge-dir pointer always wins, so existing installs are byte-identical).
    """
    org = config.resolve_org_root()
    team = config.resolve_team()
    if org and team:
        return os.path.abspath(os.path.join(org, orgkeys.ORG_TEAMS_DIR, team))
    return None


# --- org-identity path resolvers ---------------------------------------------
# Path-inference siblings of resolve_org_root/resolve_team above: given a resolved
# knowledge dir, infer its (origin, team) facet or shared-root status from the org
# folder layout. orgkeys owns the LAYOUT CONSTANTS; the RESOLVERS live here (per the
# orgkeys docstring's "Does NOT own"). Moved from the entrypoint in wave 2 seam 2 so
# core.frontmatter.build_entry_frontmatter can stamp provenance without an upward import.
def _kdir_is_shared_root(kdir):
    """True iff kdir IS the org shared/ root (…/shared)."""
    norm = os.path.normpath(kdir or "").replace(os.sep, "/")
    return norm.endswith("/" + orgkeys.ORG_SHARED_DIR) or norm == orgkeys.ORG_SHARED_DIR


def _infer_team_from_kdir(kdir):
    """The team name when `kdir` IS (or is under) an org …/teams/<team> dir, else None.

    Pure path inspection — the folder path IS the team facet (org model). Used as
    the C-O1 path-inference fallback so LEGACY entries with no `team:` frontmatter
    still resolve a team without a file rewrite, and so writes/lint know the caller's
    team. Returns None for a flat (non-org) knowledge dir → single-team behavior is
    byte-identical (INV-1).
    """
    if not kdir:
        return None
    norm = os.path.normpath(kdir).replace(os.sep, "/")
    parts = norm.split("/")
    # Find the LAST '<...>/teams/<team>' segment (kdir points AT the team dir).
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == orgkeys.ORG_TEAMS_DIR and i + 1 < len(parts) and parts[i + 1]:
            # Guard: teams/<team> must be the tail (kdir == the team dir), not an
            # ancestor of a deeper path, so we don't mis-read teams/<t>/work/... .
            if i + 2 == len(parts):
                return parts[i + 1]
    return None


def _entry_origin_team(fm, kdir):
    """Resolve (origin, team) for an entry from frontmatter, else path-inference.

    C-O1 fix: O1 must be satisfiable on LEGACY entries carrying no
    origin/team line. Preference order:
      - explicit `origin:`/`team:` frontmatter (the org-frontmatter backfill migration sets these)
      - ELSE infer origin=team + team=<folder> from the kdir path (shared/ → origin
        'shared'); a flat non-org KB yields (None, None) → byte-identical to today.
    Pure; no file rewrite. Verified doc_text-neutral (org fields ∉ _entry_doc_text).
    """
    origin = fm.get("origin")
    team = fm.get("team")
    if origin and team:
        return origin, team
    inferred_team = config._infer_team_from_kdir(kdir)
    # is_shared: kdir IS the org shared/ root — a whole-COMPONENT tail match, NOT a
    # substring check (F3 fix: a flat KB path like /home/u/shared-notes must NOT be
    # treated as the org shared root, else it wrongly gets origin:shared stamped and
    # its recall breaks — INV-1 violation for flat KBs whose path contains 'shared').
    # An org shared root also sits directly under an org root (…/<org>/shared), so
    # require BOTH: the tail component is exactly 'shared' AND a sibling teams/ exists.
    is_shared = False
    if config._kdir_is_shared_root(kdir):
        parent = os.path.dirname(os.path.normpath(kdir or ""))
        is_shared = os.path.isdir(os.path.join(parent, orgkeys.ORG_TEAMS_DIR))
    if not origin:
        origin = "shared" if (is_shared and not inferred_team) else ("team" if inferred_team else None)
    if not team and inferred_team:
        team = inferred_team
    return origin, team


# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "resolve_org_root",
    "resolve_team",
    "_resolve_org_project",
    "resolve_org_project",
    "org_project_identity_error",
    "resolve_org_team_dir",
    "_kdir_is_shared_root",
    "_infer_team_from_kdir",
    "_entry_origin_team",
]
