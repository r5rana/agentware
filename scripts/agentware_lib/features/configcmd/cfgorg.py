"""agentware_lib.features.configcmd.cfgorg — org-mode + workspace-KB-mode settings.

Owns: the `config` handlers for the ORG + KB-MODE family — --set-org-root /
      --set-team / --set-org-project (and its deprecated --set-org-package
      spelling) / --unset-knowledge-dir (applied as ONE grouped write, exactly as
      the pre-split branch did) plus --set-kb-mode / --kb-mode-only.
Does NOT own: the direct knowledge-dir POINTER write (--set-knowledge-dir) or the
      provenance handle -> the sibling agentware_lib.features.configcmd.cfgidentity;
      the RETRIEVAL --set-mode (a different `mode`) ->
      agentware_lib.features.configcmd.cfgretrieval; the org key CONSTANTS ->
      agentware_lib.core.orgkeys; the resolvers/writers -> agentware_lib.core.config.
Layer deps: features.configcmd.cfgorg -> core (config, orgkeys) -- DOWNWARD for
      the features layer.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

The org group is the ONE multi-flag branch of the old cmd_config: several flags
may be passed together and are applied in a fixed order into a single result
payload, so it keeps a GROUP predicate (_org_triggered) alongside its handler
rather than one row per flag in the cfgroutes table.
"""

import json
import os
import sys

import agentware_lib.core.config as config
import agentware_lib.core.orgkeys as orgkeys


def _resolve_org_project(args):
    """(value, flag-spelling) for the org-project anchor.

    --set-org-package is the DEPRECATED spelling of --set-org-project. Both write
    the CURRENT key (orgkeys.ORG_PROJECT_KEY): the deprecated spelling is a way in,
    never a way to persist the old one. The new flag wins if somebody passes both.
    """
    set_proj = getattr(args, "set_org_project", None)
    set_proj_flag = "--set-org-project"
    if set_proj is None:
        set_proj = getattr(args, "set_org_package", None)
        set_proj_flag = "--set-org-package"
    return set_proj, set_proj_flag


def _org_triggered(args):
    """True iff any org-mode flag was passed (the grouped branch's predicate).

    --set-org-root / --set-team / --unset-knowledge-dir engage ORG MODE. Setting
    ORG_ROOT+TEAM makes resolve_knowledge_dir compose <org>/teams/<team> AND makes
    `org lint` engage (it no-ops when ORG_ROOT is unset). The direct
    AGENTWARE_KNOWLEDGE_DIR pointer WINS in the resolver, so switching to org mode
    requires --unset-knowledge-dir too. All three are first-class, guard-free.
    """
    set_proj, _set_proj_flag = _resolve_org_project(args)
    return (getattr(args, "set_org_root", None) is not None
            or getattr(args, "set_team", None) is not None
            or set_proj is not None
            or bool(getattr(args, "unset_knowledge_dir", False)))


def _apply_org_writes(args, result):
    """Persist each passed org key into ``result``; return an exit code or None."""
    set_org = getattr(args, "set_org_root", None)
    set_team = getattr(args, "set_team", None)
    set_proj, set_proj_flag = _resolve_org_project(args)
    do_unset_kdir = getattr(args, "unset_knowledge_dir", False)
    if set_org is not None:
        norm = os.path.abspath(os.path.expanduser(set_org.strip())) if set_org.strip() else ""
        if not norm:
            sys.stderr.write("agentware: invalid --set-org-root %r (must be a path)\n" % set_org)
            return 2
        config._set_config_value(orgkeys.ORG_ROOT_KEY, norm)
        result["org_root"] = norm
    if set_team is not None:
        t = set_team.strip()
        if not t:
            sys.stderr.write("agentware: invalid --set-team %r (must be non-empty)\n" % set_team)
            return 2
        config._set_config_value(orgkeys.ORG_TEAM_KEY, t)
        result["team"] = t
    if set_proj is not None:
        pk = set_proj.strip()
        if not pk:
            sys.stderr.write("agentware: invalid %s %r (must be non-empty)\n"
                             % (set_proj_flag, set_proj))
            return 2
        config._set_config_value(orgkeys.ORG_PROJECT_KEY, pk)
        result["org_project"] = pk
        result["org_package"] = pk  # deprecated payload mirror
    if do_unset_kdir:
        config._unset_config_value(config.CONFIG_KEY)
        result["unset_knowledge_dir"] = True
    return None


def _render_org(result, as_json):
    """Echo the applied org writes + the RESOLVED knowledge dir."""
    if as_json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return
    if "org_root" in result:
        print("Set %s=%s" % (orgkeys.ORG_ROOT_KEY, result["org_root"]))
    if "team" in result:
        print("Set %s=%s" % (orgkeys.ORG_TEAM_KEY, result["team"]))
    if "org_project" in result:
        print("Set %s=%s" % (orgkeys.ORG_PROJECT_KEY, result["org_project"]))
    if result.get("unset_knowledge_dir"):
        print("Unset %s (org mode now resolves via ORG_ROOT+TEAM)" % config.CONFIG_KEY)
    print("Resolved knowledge dir: %s" % (result["resolved_knowledge_dir"] or "(none)"))


def _handle_org(args, as_json):
    """--set-org-root / --set-team / --set-org-project / --unset-knowledge-dir."""
    result = {"ok": True, "config": config.HOME_CONFIG}
    rc = _apply_org_writes(args, result)
    if rc is not None:
        return rc
    # Report the RESOLVED kdir after the change so the caller can confirm parity.
    result["resolved_knowledge_dir"] = config.resolve_knowledge_dir()
    _render_org(result, as_json)
    return 0


def _set_kb_mode(args, as_json):
    """--set-kb-mode power|team: persist the WORKSPACE mode.

    Distinct from the retrieval --set-mode; persisted to ~/.agentware/config.env.
    """
    kb_mode_val = getattr(args, "set_kb_mode", None)
    norm = config._parse_kb_mode_strict(kb_mode_val)
    if norm is None:
        sys.stderr.write(
            "agentware: invalid --set-kb-mode value %r (use power|team)\n"
            % kb_mode_val)
        return 2
    config._set_config_value(config.KB_MODE_KEY, norm)
    if as_json:
        json.dump({"ok": True, "kb_mode": norm, "config": config.HOME_CONFIG},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Set %s=%s in %s (workspace KB mode %s)"
              % (config.KB_MODE_KEY, norm, config.HOME_CONFIG, norm))
    return 0


def _kb_mode_only(args, as_json):
    """--kb-mode-only: bare resolved workspace mode for shell consumers."""
    print(config.resolve_kb_mode())
    return 0


_EXPORTS = []

__all__ = list(_EXPORTS)
