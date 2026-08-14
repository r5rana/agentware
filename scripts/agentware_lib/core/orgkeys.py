"""agentware_lib.core.orgkeys — org-level KB key + fixed-layout constants (layer: core).

Owns: the org-mode env-var key names (AGENTWARE_ORG_ROOT / _TEAM / _PROJECT plus the
deprecated-but-still-read spelling of the last one), the
fixed org-repo subdir names (teams/ shared/ fleet/ + registry/denylist rels), and
the optional org frontmatter field tuple.
Does NOT own: the resolvers that READ these keys (resolve_org_root / resolve_team /
resolve_knowledge_dir) — those live in agentware_lib.core.config; nor the org lint /
fleet / staging behaviour that consumes them — see agentware_lib.ops.fleet.
Layer deps: core -> stdlib only (imports nothing else from agentware_lib).
Fan-in: 9 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

These are small, STABLE, feature-independent constants with high fan-in (config
resolvers, index frontmatter, fleet telemetry, org lint and the staging engine all
read them). Keeping them as a zero-dependency core leaf is what lets every consumer
share ONE definition without inverting the layer graph.

The org model: ONE repo, mainline, /teams/<team> folders co-resident + a shared/
root. Two OPTIONAL keys let a resolved knowledge dir be composed from an org root
+ a team name, so the whole fleet lives in one clonable tree. STRICTLY additive:
when BOTH are unset, resolve_knowledge_dir behaves EXACTLY as before (INV-1).
  AGENTWARE_ORG_ROOT = absolute path to the org repo root (holds teams/ + shared/)
  AGENTWARE_TEAM     = the caller's team folder name under teams/
Precedence in resolve_knowledge_dir: AGENTWARE_KNOWLEDGE_DIR (env->config) WINS
(byte-identical to today) -> ELSE ORG_ROOT+TEAM resolve to <org-root>/teams/<team>
-> ELSE None. The bash twin scripts/aw-knowledge-dir mirrors this precedence.
"""

ORG_ROOT_KEY = "AGENTWARE_ORG_ROOT"
ORG_TEAM_KEY = "AGENTWARE_TEAM"
# OPTIONAL org-project identity anchor (source-of-truth hardening). When set, it
# names the PROJECT the org root MUST be — the resolver reads the standard project
# marker files in that directory (see config.PROJECT_MARKERS), compares the declared
# project name as a WHOLE TOKEN, and REFUSES a mismatched/absent path (e.g. a stray
# /tmp scratch left in config) instead of silently using it. Unset ⇒ no-op
# (flat/other-org installs unaffected; INV-1). NEVER hardcoded in the framework —
# set per-operator in config.env (kept out of the package per R-LOC-03).
ORG_PROJECT_KEY = "AGENTWARE_ORG_PROJECT"
# DEPRECATED SPELLING of ORG_PROJECT_KEY, still READ (never written) at LOWER
# precedence. The key was renamed when the identity probe was genericised off one
# vendor's build system onto standard project markers. Dropping it outright would
# silently DISARM the guard for any operator whose config.env still carries the old
# spelling — the resolver would find no name, return None, and a mis-pointed org
# root would become the live KB with nothing printed. So it stays readable, and the
# behavioural proof that it does lives in tests/_orgidentity_gate.py (check 4).
ORG_PROJECT_LEGACY_KEY = "AGENTWARE_ORG_PACKAGE"
# The org repo's fixed subdir names (canonical layout — see docs/REPO-LAYOUT.md).
ORG_TEAMS_DIR = "teams"
ORG_SHARED_DIR = "shared"
ORG_REGISTRY_REL = "teams/registry.json"
# Per-team telemetry sink dir (committed, per-USER shards) + the org-wide DENYLIST.
ORG_FLEET_DIR = "fleet"
ORG_DENYLIST_REL = "shared/DENYLIST"
# Optional frontmatter fields the org model adds. These are IGNORED by the scorer
# (not in FRONTMATTER_FIELDS / _entry_doc_text) so ranking stays byte-identical
# (INV-1). Single source of truth consumed by build_index_from_frontmatter AND the
# gate-release AST guard (which asserts _entry_doc_text references none of them).
_ORG_FRONTMATTER_FIELDS = ("origin", "team", "project", "status", "supersedes")

# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "ORG_ROOT_KEY",
    "ORG_TEAM_KEY",
    "ORG_PROJECT_KEY",
    "ORG_PROJECT_LEGACY_KEY",
    "ORG_TEAMS_DIR",
    "ORG_SHARED_DIR",
    "ORG_REGISTRY_REL",
    "ORG_FLEET_DIR",
    "ORG_DENYLIST_REL",
    "_ORG_FRONTMATTER_FIELDS",
]
