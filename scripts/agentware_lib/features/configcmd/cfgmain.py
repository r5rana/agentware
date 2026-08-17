"""agentware_lib.features.configcmd.cfgmain — the `config` verb entry + registrar.

Owns: cmd_config (the SLIM route walker: resolve --format, take the FIRST
      cfgroutes row whose predicate matches, else fall through to the resolved-
      config view) and register_config (the `config` argparse registrar), split
      into per-group helper registrars whose CALL ORDER is the --help order.
Does NOT own: any handler BODY — each settings family lives in its own sibling
      (cfgidentity, cfgorg, cfgkbgit, cfgretrieval, cfgdream, cfgphases); the
      branch ORDER -> agentware_lib.features.configcmd.cfgroutes; the terminal
      resolved-config VIEW -> agentware_lib.features.configcmd.cfgshow; the pure
      config RESOLVERS/WRITERS -> agentware_lib.core.config; the `init`/`profile`
      handlers -> the sibling agentware_lib.features.setupcmd.
Layer deps: features.configcmd.cfgmain -> its own package siblings ONLY
      (cfgroutes, cfgshow); every core/retrieval/ops edge belongs to the handler
      modules, so the verb entry itself has no outbound layer edge.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

register_config travels with cmd_config (its set_defaults(func=cmd_config)
resolves cmd_config in this module's globals) and keeps the frozen registrar
signature ``register_<verb>(sub, fmt_parent)``.

⚠️ The ORDER of the _register_*_flags calls in register_config IS the rendered
`config --help` order, and that rendering is pinned BYTE-FOR-BYTE by the
parser golden master (tests/test_parser_golden.py + tests/fixtures/help_golden/).
Each helper is one CONTIGUOUS run of the pre-split registrar — which is why the
org and identity families each appear as two runs rather than one tidy call.
Re-grouping or reordering them, or rewording a single help string, is a
behaviour change that FAILS the golden test.
"""

from .cfgports import register_ported_setting_flags
from .cfgroutes import CONFIG_ROUTES
from .cfgshow import _show_config


def cmd_config(args):
    """Show resolved config; optionally set/print one of its settings.

    Walks CONFIG_ROUTES in the pre-split guard-clause ORDER and returns the exit
    code of the FIRST handler whose predicate matches; with nothing matched it
    falls through to the resolved-config view (_show_config), which exits 1 if
    the knowledge dir is unconfigured. The setters that never require a knowledge
    dir (e.g. --set-autocommit, --kb-autocommit-only) return before that view is
    ever reached, exactly as they did pre-split.
    """
    as_json = getattr(args, "format", "text") == "json"
    for predicate, handler in CONFIG_ROUTES:
        if predicate(args):
            return handler(args, as_json)
    return _show_config(args, as_json)


def _register_pointer_flags(p_cfg):
    """The knowledge-dir pointer: the bare-path reader + the repoint writer."""
    p_cfg.add_argument("--knowledge-dir-only", action="store_true",
                       dest="knowledge_dir_only",
                       help="print only the bare path (for shell consumers)")
    p_cfg.add_argument("--set-knowledge-dir", dest="set_knowledge_dir",
                       metavar="PATH",
                       help="persist the external knowledge-dir pointer to "
                            "~/.agentware/config.env (normalized to an absolute "
                            "path; the sanctioned way to repoint an already-"
                            "onboarded operator without cmd_init's --force guard; "
                            "env still overrides per-run)")


def _register_org_flags(p_cfg):
    """The org-mode keys (org root / team / project) + the org-mode switch."""
    p_cfg.add_argument("--set-org-root", dest="set_org_root", metavar="PATH",
                       help="persist AGENTWARE_ORG_ROOT (the org repo root holding "
                            "teams/ + shared/) — engages org mode + org lint")
    p_cfg.add_argument("--set-team", dest="set_team", metavar="TEAM",
                       help="persist AGENTWARE_TEAM (resolves to <org-root>/teams/<team>)")
    p_cfg.add_argument("--set-org-project", dest="set_org_project", metavar="NAME",
                       help="persist AGENTWARE_ORG_PROJECT — the project the org "
                            "root MUST be (matched against its standard project "
                            "marker files); the resolver refuses a mis-pointed org "
                            "root (source-of-truth anchor)")
    p_cfg.add_argument("--set-org-package", dest="set_org_package", metavar="NAME",
                       help="deprecated spelling of --set-org-project (persists "
                            "AGENTWARE_ORG_PROJECT)")
    p_cfg.add_argument("--unset-knowledge-dir", dest="unset_knowledge_dir",
                       action="store_true",
                       help="remove the direct AGENTWARE_KNOWLEDGE_DIR pointer so "
                            "ORG_ROOT+TEAM takes precedence (org-mode switch)")


def _register_kbgit_flags(p_cfg):
    """The KB git write-back settings: autocommit + push."""
    p_cfg.add_argument("--kb-autocommit-only", action="store_true",
                       dest="kb_autocommit_only",
                       help="print only the resolved KB-autocommit setting (1/0)")
    p_cfg.add_argument("--set-autocommit", dest="set_autocommit",
                       metavar="on|off",
                       help="persist the KB-autocommit setting to "
                            "~/.agentware/config.env (env still overrides per-run)")
    p_cfg.add_argument("--kb-push-only", action="store_true",
                       dest="kb_push_only",
                       help="print only the resolved KB-push setting (1/0)")
    p_cfg.add_argument("--set-push", dest="set_push",
                       metavar="on|off",
                       help="persist the KB-push setting to "
                            "~/.agentware/config.env (env still overrides per-run)")


def _register_retrieval_flags(p_cfg):
    """The retrieval mode + the SETTINGS_AW embedder/model settings."""
    p_cfg.add_argument("--retrieval-mode-only", action="store_true",
                       dest="retrieval_mode_only",
                       help="print only the resolved EFFECTIVE retrieval mode "
                            "(deterministic|semantic) for shell consumers")
    p_cfg.add_argument("--set-mode", dest="set_mode",
                       metavar="deterministic|semantic",
                       help="persist the retrieval mode to ~/.agentware/config.env "
                            "(Mode A deterministic / Mode B semantic; env still "
                            "overrides per-run)")
    # --- SETTINGS_AW (260627-semantic-embedding-settings) ----------------
    p_cfg.add_argument("--set-retrieval", dest="set_retrieval",
                       metavar="bm25|semantic",
                       help="SETTINGS_AW alias for --set-mode "
                            "(bm25=Mode A deterministic / semantic=Mode B); "
                            "persisted to ~/.agentware/config.env")
    p_cfg.add_argument("--set-embedder", dest="set_embedder",
                       metavar="DOTTED_NAME|PATH",
                       help="persist the LOCAL embedder backend spec (dotted module "
                            "name e.g. agentware_embedder_fastembed, or a path to a "
                            ".py backend) to ~/.agentware/config.env")
    p_cfg.add_argument("--embedder-only", action="store_true",
                       dest="embedder_only",
                       help="print only the resolved embedder backend spec "
                            "(empty if unset) for shell consumers")
    p_cfg.add_argument("--set-embed-model", dest="set_embed_model",
                       metavar="MODEL_ID",
                       help="persist the embedding model id (e.g. "
                            "BAAI/bge-small-en-v1.5) to ~/.agentware/config.env")
    p_cfg.add_argument("--embed-model-only", action="store_true",
                       dest="embed_model_only",
                       help="print only the resolved embedding model id for "
                            "shell consumers")


def _register_kb_mode_flags(p_cfg):
    """The WORKSPACE KB mode (power|team) — distinct from the retrieval mode."""
    p_cfg.add_argument("--kb-mode-only", action="store_true",
                       dest="kb_mode_only",
                       help="print only the resolved workspace KB mode "
                            "(power|team) for shell consumers (onboarding)")
    p_cfg.add_argument("--set-kb-mode", dest="set_kb_mode",
                       metavar="power|team",
                       help="persist the workspace KB mode to ~/.agentware/config.env "
                            "(power=solo local KB / team=shared git KB; distinct "
                            "from the retrieval --set-mode; env overrides per-run)")


def _register_cli_flags(p_cfg):
    """The global runtime CLI + the per-phase routing grid."""
    # --- runtime CLI (260627-codex-runtime-adapter) ----------------------
    p_cfg.add_argument("--cli-only", action="store_true", dest="cli_only",
                       help="print only the resolved runtime CLI (claude|codex) "
                            "for shell consumers (the loop's run_agent adapter "
                            "+ onboarding alias generation)")
    p_cfg.add_argument("--set-cli", dest="set_cli", metavar="claude|codex",
                       help="persist the runtime CLI to ~/.agentware/config.env "
                            "(claude=Claude Code / codex=OpenAI Codex; env still "
                            "overrides per-run; default claude)")
    # --- per-phase routing (260628-hybrid-local-model) -------------------
    # 3 phases (pre/main/post) x 3 axes (cli/model/local): a --set-<phase>-<axis>
    # writer + a --<phase>-<axis>-only reader each. Registered in a loop to keep
    # the surface declarative; resolution precedence is phase env -> phase config
    # -> global -> default (see resolve_phase_*).
    for _phase in ("pre", "main", "post"):
        for _axis, _meta in (("cli", "claude|codex"), ("model", "MODEL_ID"),
                             ("local", "lmstudio|ollama")):
            p_cfg.add_argument(
                "--set-%s-%s" % (_phase, _axis),
                dest="set_%s_%s" % (_phase, _axis), metavar=_meta,
                help="persist the %s-phase %s to ~/.agentware/config.env "
                     "(per-phase override of the global value; env still "
                     "overrides per-run)" % (_phase, _axis))
            p_cfg.add_argument(
                "--%s-%s-only" % (_phase, _axis),
                dest="%s_%s_only" % (_phase, _axis), action="store_true",
                help="print only the resolved %s-phase %s for shell consumers "
                     "(the loop's per-phase run_agent adapter)" % (_phase, _axis))


def _register_provenance_flags(p_cfg):
    """The per-user handle, the onboarding metric, and the ripeness threshold."""
    p_cfg.add_argument("--user-handle-only", action="store_true",
                       dest="user_handle_only",
                       help="print only the resolved per-user provenance handle "
                            "(empty if unset)")
    p_cfg.add_argument("--set-user-handle", dest="set_user_handle",
                       metavar="HANDLE",
                       help="persist the per-user provenance handle to "
                            "~/.agentware/config.env (stamped as `author` on "
                            "entries this user creates in a shared team KB)")
    p_cfg.add_argument("--record-onboarding", action="store_true",
                       dest="record_onboarding",
                       help="append one onboarding event {mode,user_handle} to "
                            "<kdir>/logs/metrics.jsonl (gitignored observability)")
    # --- skill-proposal ripeness threshold (260722-roster-field-mapping-lint) ---
    p_cfg.add_argument("--skill-propose-threshold-only", action="store_true",
                       dest="skill_propose_threshold_only",
                       help="print only the resolved skill-proposal ripeness "
                            "threshold (positive int, default 5) for shell consumers")
    p_cfg.add_argument("--set-skill-propose-threshold",
                       dest="set_skill_propose_threshold", metavar="N",
                       help="persist the skill-proposal ripeness threshold (a "
                            "positive integer, default 5) to ~/.agentware/config.env; "
                            "a proposal is 'ripe' for human-gated promotion once its "
                            "unique-ref count reaches N (env still overrides per-run)")


def _register_dream_flags(p_cfg):
    """The dream-mode opt-in, its schedule, and its wall-clock cap."""
    # --- dream mode (260627-dream-mode) ----------------------------------
    p_cfg.add_argument("--dream-only", action="store_true", dest="dream_only",
                       help="print only the resolved dream setting (on|off) for "
                            "shell consumers (the scheduler installer + idle-gate)")
    p_cfg.add_argument("--set-dream", dest="set_dream", metavar="on|off",
                       help="persist the dream-mode opt-in to "
                            "~/.agentware/config.env (default OFF; env still "
                            "overrides per-run)")
    p_cfg.add_argument("--dream-schedule-only", action="store_true",
                       dest="dream_schedule_only",
                       help="print only the resolved dream schedule token "
                            "(empty if unset) for shell consumers")
    p_cfg.add_argument("--set-dream-schedule", dest="set_dream_schedule",
                       metavar="HH:MM|CRON",
                       help="persist the nightly dream schedule (a 24h HH:MM token "
                            "or a 5-field cron expression) to ~/.agentware/config.env")
    p_cfg.add_argument("--dream-max-runtime-only", action="store_true",
                       dest="dream_max_runtime_only",
                       help="print only the resolved dream max-runtime cap in "
                            "seconds (0 = disabled) for shell consumers")
    p_cfg.add_argument("--set-dream-max-runtime", dest="set_dream_max_runtime",
                       metavar="N|Ns|Nm|Nh|off",
                       help="persist the best-effort dream wall-clock cap (seconds, "
                            "or N[s|m|h]; off/0 disables) to ~/.agentware/config.env; "
                            "fully offline, no network")


def register_config(sub, fmt_parent):
    # config -----------------------------------------------------------------
    # The helper CALL ORDER below IS the rendered --help order (golden-pinned).
    p_cfg = sub.add_parser(
        "config", parents=[fmt_parent], help="show the resolved knowledge dir")
    _register_pointer_flags(p_cfg)
    _register_org_flags(p_cfg)
    _register_kbgit_flags(p_cfg)
    _register_retrieval_flags(p_cfg)
    _register_kb_mode_flags(p_cfg)
    _register_cli_flags(p_cfg)
    _register_provenance_flags(p_cfg)
    _register_dream_flags(p_cfg)
    # The settings this migration PORTED, whose resolvers landed without their
    # accessors — see cfgports for why that is worse than a missing feature.
    register_ported_setting_flags(p_cfg)
    p_cfg.set_defaults(func=cmd_config)


_EXPORTS = ["cmd_config"]

__all__ = list(_EXPORTS)
