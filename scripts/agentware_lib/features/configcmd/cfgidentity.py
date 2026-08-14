"""agentware_lib.features.configcmd.cfgidentity — workspace-identity settings.

Owns: the `config` handlers for the KNOWLEDGE-DIR POINTER + PROVENANCE family —
      --set-knowledge-dir (with the kblayout.kb_pointable target guard),
      --set-user-handle / --user-handle-only, --set-skill-propose-threshold /
      --skill-propose-threshold-only, and --record-onboarding.
Does NOT own: the org-mode keys + workspace kb-mode (--set-org-root/--set-team/
      --set-org-project/--unset-knowledge-dir/--set-kb-mode) -> the sibling
      agentware_lib.features.configcmd.cfgorg; the resolved-config VIEW ->
      agentware_lib.features.configcmd.cfgshow; the flag->handler ORDER ->
      agentware_lib.features.configcmd.cfgroutes; the pure resolvers/writers ->
      agentware_lib.core.config + agentware_lib.core.routing.
Layer deps: features.configcmd.cfgidentity -> core (config, kblayout, routing),
      ops (metrics) -- all DOWNWARD for the features layer.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Every handler here takes ``(args, as_json)`` and returns the process exit code,
byte-identical to the corresponding branch of the pre-split 622-line cmd_config.
The BRANCH ORDER is not encoded here — it is the cfgroutes table's single job.
"""

import json
import os
import sys

import agentware_lib.core.config as config
import agentware_lib.core.kblayout as kblayout
import agentware_lib.core.routing as routing
import agentware_lib.ops.metrics as _metrics


def _set_knowledge_dir(args, as_json):
    """--set-knowledge-dir <path>: persist the KB pointer to ~/.agentware/config.env.

    This is the sanctioned way to repoint an already-onboarded operator (cmd_init's
    clobber guard refuses to repoint an existing config without --force). The path
    is normalized to absolute (expanding a leading ~) so both resolvers
    (resolve_knowledge_dir + scripts/aw-knowledge-dir) agree.

    TARGET GUARD (kblayout.kb_pointable), applied on the SAME asymmetry the guard
    itself uses for the derived index: ABSENCE is not evidence, PRESENCE-but-broken
    is. A target that does not exist yet stays allowed with a warning (the checkout
    may arrive later; require_knowledge_dir validates existence at use time), but a
    target that EXISTS and is not a knowledge base — no .initialized sentinel, no
    MAIN.md, or an index.json that will not parse — is refused BY NAME rather than
    persisted, because pointing the machine at it breaks every later command with a
    far less legible error.
    """
    set_kdir_val = getattr(args, "set_knowledge_dir", None)
    norm = os.path.abspath(os.path.expanduser(set_kdir_val.strip())) \
        if set_kdir_val.strip() else ""
    if not norm:
        sys.stderr.write(
            "agentware: invalid --set-knowledge-dir value %r (must be a path)\n"
            % set_kdir_val)
        return 2
    if os.path.isdir(norm):
        pointable, reason = kblayout.kb_pointable(norm)
        if not pointable:
            sys.stderr.write(
                "agentware: refusing to point the knowledge dir at %s: %s\n"
                "The directory exists but is not a knowledge base. Run "
                "`agentware init --knowledge-dir %s` to scaffold one.\n"
                % (norm, reason, norm))
            if as_json:
                json.dump({"ok": False, "error": "kb-not-pointable",
                           "requested_dir": norm, "reason": reason,
                           "config": config.HOME_CONFIG},
                          sys.stdout, ensure_ascii=False, indent=2)
                sys.stdout.write("\n")
            return 3
    config._set_config_value(config.CONFIG_KEY, norm)
    exists = os.path.isdir(norm)
    if as_json:
        json.dump({"ok": True, "knowledge_dir": norm, "exists": exists,
                   "config": config.HOME_CONFIG},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Set %s=%s in %s (knowledge dir)" % (config.CONFIG_KEY, norm, config.HOME_CONFIG))
        if not exists:
            sys.stderr.write(
                "agentware: warning — %s does not exist yet; check it out before "
                "the next run or agentware commands will fail.\n" % norm)
    return 0


def _set_skill_propose_threshold(args, as_json):
    """--set-skill-propose-threshold <N>: persist the ripeness threshold.

    A proposal is "ripe" for human-gated promotion once its unique-ref count
    reaches N. Invalid/non-positive tokens are rejected (no config corruption).
    """
    threshold_val = getattr(args, "set_skill_propose_threshold", None)
    n = config._parse_skill_propose_threshold(threshold_val)
    if n is None:
        sys.stderr.write(
            "agentware: invalid --set-skill-propose-threshold value %r "
            "(use a positive integer, e.g. 5)\n" % threshold_val)
        return 2
    config._set_config_value(config.SKILL_PROPOSE_THRESHOLD_KEY, str(n))
    if as_json:
        json.dump({"ok": True, "skill_propose_threshold": n,
                   "config": config.HOME_CONFIG},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Set %s=%s in %s (skill-proposal ripeness threshold)"
              % (config.SKILL_PROPOSE_THRESHOLD_KEY, n, config.HOME_CONFIG))
    return 0


def _set_user_handle(args, as_json):
    """--set-user-handle <h>: persist the per-user provenance handle.

    Sanitized to a safe single token; an all-invalid handle is rejected (no
    config corruption).
    """
    handle_val = getattr(args, "set_user_handle", None)
    norm = routing._normalize_user_handle(handle_val)
    if not norm:
        sys.stderr.write(
            "agentware: invalid --set-user-handle value %r "
            "(must contain at least one of A-Z a-z 0-9 . _ -)\n" % handle_val)
        return 2
    config._set_config_value(config.USER_HANDLE_KEY, norm)
    if as_json:
        json.dump({"ok": True, "user_handle": norm, "config": config.HOME_CONFIG},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Set %s=%s in %s (per-user provenance handle)"
              % (config.USER_HANDLE_KEY, norm, config.HOME_CONFIG))
    return 0


def _skill_propose_threshold_only(args, as_json):
    """--skill-propose-threshold-only: bare resolved ripeness threshold (int)."""
    print(config.resolve_skill_propose_threshold())
    return 0


def _user_handle_only(args, as_json):
    """--user-handle-only: bare resolved per-user handle (empty if unset)."""
    h = routing.resolve_user_handle()
    if h:
        print(h)
    return 0


def _record_onboarding(args, as_json):
    """--record-onboarding: append one onboarding event to <kdir>/logs/metrics.jsonl.

    Rule-7 observability; gitignored, best-effort. Onboarding calls this on
    completion so the dashboard can chart mode adoption.
    """
    kdir = config.resolve_knowledge_dir()
    if not kdir:
        sys.stderr.write("agentware: no knowledge dir configured\n")
        return 1
    mode = config.resolve_kb_mode()
    handle = routing.resolve_user_handle()
    wrote = _metrics.emit_onboarding_metric(kdir, mode, handle)
    if as_json:
        json.dump({"ok": wrote, "event": "onboarding", "mode": mode,
                   "user_handle": handle}, sys.stdout, ensure_ascii=False,
                  indent=2)
        sys.stdout.write("\n")
    else:
        print("Recorded onboarding event (mode=%s, user_handle=%s) -> "
              "%s/logs/metrics.jsonl" % (mode, handle or "<unset>", kdir))
    return 0


_EXPORTS = []

__all__ = list(_EXPORTS)
