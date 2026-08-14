"""agentware_lib.features.configcmd.cfgshow — the resolved-config VIEW.

Owns: the TERMINAL view of the `config` verb — the read-only report every
      non-setter invocation falls through to: the full --format json dump, the
      --knowledge-dir-only bare path for shell consumers (hooks), and the
      human-readable key/value report. It also owns that view's EXIT-CODE
      contract: 0 when a knowledge dir is configured, 1 when it is not.
Does NOT own: any SETTER — each settings family lives in its own sibling
      (cfgidentity, cfgorg, cfgkbgit, cfgretrieval, cfgdream, cfgphases); the
      RESOLVERS it reads -> agentware_lib.core.config + agentware_lib.core.routing;
      the effective-mode + embedder-availability probes ->
      agentware_lib.retrieval.vectorcache; the sentinel path constant ->
      agentware_lib.core.kblayout; the flag->handler ORDER ->
      agentware_lib.features.configcmd.cfgroutes.
Layer deps: features.configcmd.cfgshow -> core (config, kblayout, routing),
      retrieval (vectorcache) -- all DOWNWARD for the features layer.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

_show_config takes ``(args, as_json)`` and returns the process exit code, exactly
like every other handler — but it is the FALLTHROUGH, not a table row: cfgroutes
deliberately excludes it so the table stays a pure flag->handler map and the
"nothing matched -> show the config" default lives in ONE place (cfgmain).

Byte-shape notes (this view is a consumed contract, not cosmetics): the JSON keys
and the human-readable label column are read by the SessionStart hook, onboarding
and the loop, so the resolution ORDER, the key set and the two-space label
padding are preserved verbatim from the pre-split cmd_config tail. The kdir and
no-kdir reports share an IDENTICAL 11-line tail — the pre-split code duplicated
it inline; _view_lines computes it once and both arms print it, which is a
mechanical de-duplication with byte-identical output.
"""

import json
import os
import sys

import agentware_lib.core.config as config
import agentware_lib.core.routing as routing
import agentware_lib.retrieval.vectorcache as _vectorcache
from agentware_lib.core.kblayout import SENTINEL_REL


def _resolve_view():
    """Resolve every value the config view reports, in the pre-split ORDER."""
    kdir = config.resolve_knowledge_dir()
    autocommit = config.resolve_kb_autocommit()
    kb_push = config.resolve_kb_push()
    retrieval_mode = config.resolve_retrieval_mode()
    eff_mode, mode_fell_back, _mode_notice = _vectorcache.resolve_effective_retrieval_mode()
    kb_mode = config.resolve_kb_mode()
    cli = config.resolve_cli()
    user_handle = routing.resolve_user_handle()
    dream = routing.resolve_dream()
    dream_schedule = routing.resolve_dream_schedule()
    dream_max_runtime = routing.resolve_dream_max_runtime()
    initialized = bool(kdir) and os.path.isfile(os.path.join(kdir, SENTINEL_REL))
    return {"kdir": kdir, "autocommit": autocommit, "kb_push": kb_push,
            "retrieval_mode": retrieval_mode, "eff_mode": eff_mode,
            "mode_fell_back": mode_fell_back, "kb_mode": kb_mode, "cli": cli,
            "user_handle": user_handle, "dream": dream,
            "dream_schedule": dream_schedule,
            "dream_max_runtime": dream_max_runtime, "initialized": initialized}


def _render_json(v):
    """The full --format json dump of the resolved config."""
    json.dump({"knowledge_dir": v["kdir"], "configured": bool(v["kdir"]),
               "initialized": v["initialized"], "kb_autocommit": v["autocommit"],
               "kb_push": v["kb_push"], "retrieval_mode": v["retrieval_mode"],
               "effective_retrieval_mode": v["eff_mode"],
               "kb_mode": v["kb_mode"], "cli": v["cli"],
               "user_handle": v["user_handle"],
               "dream": v["dream"], "dream_schedule": v["dream_schedule"],
               "dream_max_runtime": v["dream_max_runtime"],
               "embedder_backend": routing.resolve_embedder_backend(),
               "embed_model": routing.resolve_embed_model(),
               "semantic_embedder_available": _vectorcache.semantic_embedder_available(),
               "phase_routing": {
                   p: {"cli": routing.resolve_phase_cli(p),
                       "model": routing.resolve_phase_model(p),
                       "local": routing.resolve_phase_local(p)}
                   for p in config.PHASES}},
              sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _view_lines(v):
    """The 11 human-readable lines BOTH report arms print, in print order.

    Computed in the pre-split computation order (mode/cli/handle/dream lines
    first, then the SETTINGS_AW embedder pair) and returned in the pre-split
    PRINT order, which differs — the embedder pair prints right after the mode.
    """
    mode_line = "retrieval_mode: %s" % v["retrieval_mode"]
    if v["mode_fell_back"]:
        mode_line += " (effective: %s — no local model)" % v["eff_mode"]
    cli_line = "cli:           %s" % v["cli"]
    handle_line = "user_handle:   %s" % (v["user_handle"] or "<unset>")
    dream_line = "dream:         %s" % ("on" if v["dream"] == "1" else "off")
    dream_sched_line = "dream_sched:   %s" % (v["dream_schedule"] or "<unset>")
    dream_max_line = ("dream_max_rt:  %s"
                      % (("%ss" % v["dream_max_runtime"]) if v["dream_max_runtime"]
                         else "disabled"))
    # SETTINGS_AW surfacing (semantic backend choice): only meaningful in Mode B.
    embedder_line = "embedder:      %s" % (routing.resolve_embedder_backend() or "<unset>")
    embed_model_line = "embed_model:   %s" % routing.resolve_embed_model()
    return (
        "kb_autocommit: %s" % ("on" if v["autocommit"] == "1" else "off"),
        "kb_push:       %s" % ("on" if v["kb_push"] == "1" else "off"),
        mode_line,
        embedder_line,
        embed_model_line,
        "kb_mode:       %s" % v["kb_mode"],
        cli_line,
        handle_line,
        dream_line,
        dream_sched_line,
        dream_max_line,
    )


def _render_text(v):
    """The human-readable report; 0 with a knowledge dir configured, else 1."""
    lines = _view_lines(v)
    if v["kdir"]:
        print("knowledge_dir: %s" % v["kdir"])
        print("initialized:   %s" % ("yes" if v["initialized"] else "no"))
        for line in lines:
            print(line)
        return 0
    print("knowledge_dir: <unconfigured>")
    for line in lines:
        print(line)
    return 1


def _show_config(args, as_json):
    """Show the resolved config — the TERMINAL fallthrough of the `config` verb.

    Exit 1 if the knowledge dir is unconfigured (for the default/dir views).
    """
    v = _resolve_view()
    if as_json:
        _render_json(v)
        return 0 if v["kdir"] else 1
    if getattr(args, "knowledge_dir_only", False):
        # Bare path for shell consumers (hooks). Empty output if unconfigured.
        if v["kdir"]:
            print(v["kdir"])
        return 0 if v["kdir"] else 1
    return _render_text(v)


_EXPORTS = []

__all__ = list(_EXPORTS)
